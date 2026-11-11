"""
1-minute ML entry for the Aggressive Scalper: data, training and research. Runs as its own
low-priority process (started by the bot when ML1M_RUN is set). It never places a trade.

ML1M_RUN=train : for R_75, R_100, USOIL, BTCUSD pull 1-minute history, train the entry models, store them
                 in Supabase (table ml1m_store) where the live scalpers pick them up; then extend the history
                 and run the PRICE-ONLY research (no indicators) next to the indicator version.
Run with --selftest to check the code on made-up data without any network.

Entry model: for every minute, predict what a BUY and a SELL would return (in units of the stop, after costs)
under the real exit rules (stop, break-even at +0.5 stop, trailing). Trade the better side if it clears a threshold.
Results are printed as "[ML1M] ..." lines.
"""
import os
import sys
import json
import time
import math
import zlib
import base64
import pickle
import random
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import numpy as np
import requests

CACHE_DIR = "/tmp/ml1m_cache"
DERIV_PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public"
DERIV_FRACS = [0.001, 0.002, 0.004, 0.008, 0.016, 0.032]   # stop as a fraction of price (risk / (stake x multiplier))
DERIV_COST_R = 0.055                                        # commission in units of the stop
MODEL_VERSION = 1


def log(msg):
    print(f"[ML1M] {msg}", flush=True)


# ----------------------------------------------------------------------------------------------
# STORAGE (Supabase table ml1m_store)
# ----------------------------------------------------------------------------------------------
def _sb():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("no Supabase credentials")
    return url, {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"}


def store_put(key, obj, meta=None):
    url, h = _sb()
    blob = base64.b64encode(zlib.compress(pickle.dumps(obj), 6)).decode()
    r = requests.post(f"{url}/rest/v1/ml1m_store?on_conflict=key", headers=h, timeout=60,
                      data=json.dumps({"key": key, "data": blob, "meta": meta or {},
                                       "updated_at": datetime.now(timezone.utc).isoformat()}))
    if r.status_code >= 300:
        raise RuntimeError(f"store_put {key}: HTTP {r.status_code} {r.text[:160]}")
    return len(blob)


# ----------------------------------------------------------------------------------------------
# DATA (cache grows backwards in time: asking for more days only pulls the missing older part)
# ----------------------------------------------------------------------------------------------
def _hist_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, key + ".json")


def _hist_load(key):
    try:
        with open(_hist_path(key)) as f:
            d = json.load(f)
        return d.get("c", []), d.get("spread")
    except Exception:
        return [], None


def _hist_save(key, rows, spread=None):
    try:
        with open(_hist_path(key), "w") as f:
            json.dump({"c": rows, "spread": spread}, f)
    except Exception:
        pass


async def fetch_deriv_1m(symbol, days):
    key = f"deriv_{symbol}"
    hist, _ = _hist_load(key)
    rows = {int(r[0]): tuple(r[1:]) for r in hist}
    target = int(time.time()) - days * 86400
    if rows and min(rows) <= target + 3600:
        return [[k, *rows[k]] for k in sorted(rows)]
    import websockets
    cur_end = min(rows) if rows else int(time.time())
    fails = 0
    async with websockets.connect(DERIV_PUBLIC_WS, open_timeout=20, close_timeout=5) as ws:
        use_underlying = False
        while cur_end > target:
            req = {"ticks_history": symbol, "style": "candles", "granularity": 60, "count": 5000, "end": cur_end}
            if use_underlying:
                req["underlying_symbol"] = symbol
            await ws.send(json.dumps(req))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if resp.get("error"):
                msg = str(resp["error"].get("message", ""))
                code = str(resp["error"].get("code", ""))
                log(f"{symbol}: Deriv answered with an error: {code} {msg[:120]}")
                if not use_underlying and ("underlying" in msg.lower() or "validation" in code.lower()):
                    use_underlying = True
                    continue
                fails += 1
                if fails >= 3:
                    break
                await asyncio.sleep(60)
                continue
            cs = resp.get("candles") or []
            if not cs:
                break
            for c in cs:
                rows[int(c["epoch"])] = (float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
            first = min(int(c["epoch"]) for c in cs)
            if first >= cur_end:
                break
            cur_end = first
            await asyncio.sleep(2.0)
    out = [[k, *rows[k]] for k in sorted(rows)]
    log(f"{symbol}: history now {len(out)} candles ({(out[-1][0] - out[0][0]) / 86400:.0f} days)" if out else f"{symbol}: no candles")
    if out:
        _hist_save(key, out)
    return out


def _exness_client():
    import exness_api
    key, secret = os.getenv("EXNESS_API_KEY"), os.getenv("EXNESS_SECRET_KEY")
    acct = os.getenv("EXNESS_ACCOUNT_ID", "10000776165")
    if not key or not secret:
        raise RuntimeError("no Exness data key")
    return exness_api.ExnessClient(os.getenv("EXNESS_GATEWAY_URL", "api.exness.com"), key, secret, acct)


def _exness_pick(client, want):
    names = [str(n) for n in client.instruments()]
    up = {n.upper(): n for n in names}
    w = want.upper()
    if w in up:
        return up[w]
    best = [n for u, n in up.items() if w.startswith(u) or u.startswith(w)]
    return max(best, key=len) if best else None


def _measure_spread(client, inst):
    now = datetime.now(timezone.utc)
    try:
        q = urlencode({"instrument": inst, "price_type": "both", "timeframe": "M1",
                       "from": (now - timedelta(minutes=200)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "to": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        code, d = client._call("GET", f"/v1/market-data/accounts/{client.account_id}/candles?{q}")
        if code != 200:
            log(f"{inst}: spread lookup HTTP {code}")
            return None
        sp = []
        for r in d.get("candles") or []:
            try:
                s = float(r["ask"]["close"]) - float(r["bid"]["close"])
                if s > 0:
                    sp.append(s)
            except Exception:
                continue
        return float(np.median(sp)) if sp else None
    except Exception as e:
        log(f"{inst}: spread lookup failed {type(e).__name__}")
        return None


def fetch_exness_1m(mt5_symbol, days):
    """1-minute bid candles, 1000 per request, ~3s apart, 45s rests on a rate limit."""
    key = f"exness_{mt5_symbol}"
    hist, spread = _hist_load(key)
    rows = {int(r[0]): tuple(r[1:]) for r in hist}
    target = datetime.now(timezone.utc) - timedelta(days=days)
    if rows and min(rows) <= target.timestamp() + 3600:
        return [[k, *rows[k]] for k in sorted(rows)], spread
    client = _exness_client()
    inst = _exness_pick(client, mt5_symbol)
    if not inst:
        raise RuntimeError(f"Exness has no instrument like {mt5_symbol}")
    if spread is None:
        spread = _measure_spread(client, inst)
        log(f"{mt5_symbol}: instrument {inst}, median spread {spread}")
        time.sleep(3)
    to = datetime.fromtimestamp(min(rows), timezone.utc) if rows else datetime.now(timezone.utc) + timedelta(minutes=1)
    fails = 0
    empty = 0
    while to > target:
        frm = to - timedelta(minutes=1000)
        q = urlencode({"instrument": inst, "price_type": "bid", "timeframe": "M1",
                       "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": to.strftime("%Y-%m-%dT%H:%M:%SZ")})
        code, d = client._call("GET", f"/v1/market-data/accounts/{client.account_id}/candles?{q}")
        if code == 429:
            fails += 1
            if fails >= 8:
                log(f"{mt5_symbol}: giving up after repeated rate limits")
                break
            time.sleep(45)
            continue
        if code != 200:
            fails += 1
            log(f"{mt5_symbol}: HTTP {code} {str(d)[:100]}")
            if fails >= 4:
                break
            time.sleep(10)
            continue
        fails = 0
        got = 0
        for r in d.get("candles") or []:
            try:
                t = datetime.fromisoformat(str(r["time"]).replace("Z", "+00:00")).timestamp()
                rows[int(t)] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
                got += 1
            except Exception:
                continue
        empty = empty + 1 if got == 0 else 0
        if empty >= 40:      # a long run of empty windows = we are past the start of the available history
            log(f"{mt5_symbol}: no older data available")
            break
        to = frm
        time.sleep(3)
    out = [[k, *rows[k]] for k in sorted(rows)]
    log(f"{mt5_symbol}: history now {len(out)} candles ({(out[-1][0] - out[0][0]) / 86400:.0f} days)" if out else f"{mt5_symbol}: no candles")
    if out:
        _hist_save(key, out, spread)
    return out, spread


# ----------------------------------------------------------------------------------------------
# FEATURES (row i uses bars up to i-1 only; the trade would start at the open of bar i)
# ----------------------------------------------------------------------------------------------
def _roll_mean(x, n):
    c = np.cumsum(np.insert(x, 0, 0.0))
    out = np.full(len(x), np.nan)
    out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _roll_max(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    out[n - 1:] = sliding_window_view(x, n).max(axis=1)
    return out


def _roll_min(x, n):
    from numpy.lib.stride_tricks import sliding_window_view
    out = np.full(len(x), np.nan)
    out[n - 1:] = sliding_window_view(x, n).min(axis=1)
    return out


def _ema(x, n):
    a = 2.0 / (n + 1)
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def build_features(t, o, h, l, c, pure=False):
    """pure=True: only what price itself did (moves, candle shapes, where price sits in its recent range,
    breakouts, spikes and what happened after them, time of day). No RSI, no moving averages, no ATR/volatility ratios."""
    n = len(c)
    f = {}
    if pure:
        rng = h - l
        scale = _roll_mean(rng, 20)
        scale = np.where(scale > 0, scale, np.nan)
        base = scale
    else:
        pc = np.concatenate([[c[0]], c[:-1]])
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
        atr14 = _roll_mean(tr, 14)
        atr5 = _roll_mean(tr, 5)
        atr60 = _roll_mean(tr, 60)
        base = np.where(atr14 > 0, atr14, np.nan)
        f["atrp"] = atr14 / c
        f["vol_ratio"] = atr5 / np.where(atr60 > 0, atr60, np.nan)
    for k in (1, 3, 5, 10, 20, 60):
        r = np.full(n, np.nan)
        r[k:] = (c[k:] - c[:-k])
        f[f"ret{k}"] = r / base
    f["range_last"] = (h - l) / base
    f["body"] = (c - o) / base
    f["upper_wick"] = (h - np.maximum(o, c)) / base
    f["lower_wick"] = (np.minimum(o, c) - l) / base
    for k in (20, 60):
        hi, lo = _roll_max(h, k), _roll_min(l, k)
        f[f"pos{k}"] = (c - lo) / np.where(hi - lo > 0, hi - lo, np.nan)
    if not pure:
        e20, e50 = _ema(c, 20), _ema(c, 50)
        f["ema20_dist"] = (c - e20) / base
        f["ema50_dist"] = (c - e50) / base
        f["ema_gap"] = (e20 - e50) / base
        d = np.diff(c, prepend=c[0])
        g = _roll_mean(np.where(d > 0, d, 0.0), 14)
        ls = _roll_mean(np.where(d < 0, -d, 0.0), 14)
        f["rsi14"] = g / np.where(g + ls > 0, g + ls, np.nan)
    run = np.zeros(n)
    for i in range(1, n):
        if c[i] > c[i - 1]:
            run[i] = max(run[i - 1], 0) + 1
        elif c[i] < c[i - 1]:
            run[i] = min(run[i - 1], 0) - 1
    f["run"] = np.clip(run, -10, 10)
    prev_hi = np.full(n, np.nan)
    prev_lo = np.full(n, np.nan)
    mx, mn = _roll_max(h, 20), _roll_min(l, 20)
    prev_hi[1:] = mx[:-1]
    prev_lo[1:] = mn[:-1]
    f["brk20"] = (c > prev_hi).astype(float) - (c < prev_lo).astype(float)
    if pure:
        # spikes: a bar much bigger than the recent average, and what price did since
        prev_scale = np.full(n, np.nan)
        prev_scale[1:] = scale[:-1]
        spike = (h - l) > 2.5 * prev_scale
        spike = np.where(np.isnan(prev_scale), False, spike)
        cs = np.concatenate([[0], np.cumsum(spike)])
        cnt10 = np.zeros(n)
        cnt10[9:] = cs[10:] - cs[:-10]
        f["spike_cnt10"] = cnt10
        since = np.full(n, 60.0)
        dhi = np.zeros(n)
        dlo = np.zeros(n)
        sdir = np.zeros(n)
        last, lhi, llo, ldir = -10 ** 9, np.nan, np.nan, 0.0
        for i in range(n):
            if spike[i]:
                last, lhi, llo, ldir = i, h[i], l[i], 1.0 if c[i] >= o[i] else -1.0
            since[i] = min(60.0, i - last) if last > -10 ** 8 else 60.0
            if last > -10 ** 8 and i - last <= 60 and scale[i] and not math.isnan(scale[i]):
                dhi[i] = (c[i] - lhi) / scale[i]
                dlo[i] = (c[i] - llo) / scale[i]
                sdir[i] = ldir
        f["bars_since_spike"] = since
        f["dist_spike_hi"] = dhi
        f["dist_spike_lo"] = dlo
        f["spike_dir"] = sdir
    else:
        rets = np.diff(np.log(c), prepend=np.log(c[0]))
        rv = np.sqrt(_roll_mean(rets * rets, 30))
        f["rv30_ratio"] = rv / np.where(f["atrp"] > 0, f["atrp"], np.nan)
    hr = np.array([(x // 60) % 24 + (x % 3600) / 3600 for x in (np.asarray(t) // 1).astype(np.int64)])
    f["hr_s"] = np.sin(2 * np.pi * hr / 24)
    f["hr_c"] = np.cos(2 * np.pi * hr / 24)
    f["dow"] = ((np.asarray(t).astype(np.int64) // 86400 + 3) % 7).astype(float)   # Mon=0 (epoch day 0 was a Thursday)
    names = list(f.keys())
    M = np.column_stack([f[k] for k in names])
    X = np.full_like(M, np.nan)
    X[1:] = M[:-1]       # row i must only use bars <= i-1
    return X, names


# ----------------------------------------------------------------------------------------------
# TRADE SIMULATION (same exit rules as the live bots, worst case inside each bar)
# ----------------------------------------------------------------------------------------------
def sim_all(o, h, l, c, t, d, sl, act, trail, floor_min, maxbars=120, gapmax=600):
    """A trade entered at the OPEN of every bar i in direction d (+1/-1). sl = price distance of the stop.
    Returns (R, exit_bar). Inside a bar the stop is checked before the high/low can lift it (pessimistic)."""
    n = len(o)
    R = np.full(n, np.nan)
    EX = np.full(n, -1, dtype=np.int64)
    a = np.arange(n - 1)
    e = o[a]
    s_ = sl[a]
    st = np.full(a.size, -1.0)
    pk = np.zeros(a.size)
    prevR = np.zeros(a.size)
    for step in range(maxbars):
        k = a + step
        ok = k < n
        if not ok.all():
            a, e, s_, st, pk, prevR, k = a[ok], e[ok], s_[ok], st[ok], pk[ok], prevR[ok], k[ok]
        if a.size == 0:
            break
        if step > 0:
            gap = (t[k] - t[k - 1]) > gapmax
            if gap.any():
                R[a[gap]] = prevR[gap]
                EX[a[gap]] = k[gap] - 1
                keep = ~gap
                a, e, s_, st, pk, prevR, k = a[keep], e[keep], s_[keep], st[keep], pk[keep], prevR[keep], k[keep]
                if a.size == 0:
                    break
        hk, lk, ck = h[k], l[k], c[k]
        if d > 0:
            best, worst, cl = (hk - e) / s_, (lk - e) / s_, (ck - e) / s_
        else:
            best, worst, cl = (e - lk) / s_, (e - hk) / s_, (e - ck) / s_
        hit = worst <= st
        if hit.any():
            R[a[hit]] = st[hit]
            EX[a[hit]] = k[hit]
        alive = ~hit
        a, e, s_, st, pk, best, cl, k = a[alive], e[alive], s_[alive], st[alive], pk[alive], best[alive], cl[alive], k[alive]
        if a.size == 0:
            break
        pk = np.maximum(pk, best)
        newst = np.where(pk >= act, np.maximum(floor_min, pk - trail), st)
        st = np.maximum(st, newst)
        hit2 = cl <= st
        if hit2.any():
            R[a[hit2]] = st[hit2]
            EX[a[hit2]] = k[hit2]
        alive = ~hit2
        a, e, s_, st, pk, cl, k = a[alive], e[alive], s_[alive], st[alive], pk[alive], cl[alive], k[alive]
        prevR = cl[:]
        if step == maxbars - 1 and a.size:
            R[a] = cl
            EX[a] = k
    return R, EX


def sim_one(o, h, l, c, t, i0, e, d, s, act, trail, floor_min, maxbars=120, gapmax=600):
    n = len(o)
    st, pk, prev = -1.0, 0.0, 0.0
    last = min(n - 1, i0 + maxbars - 1)
    for k in range(i0, last + 1):
        if k > i0 and t[k] - t[k - 1] > gapmax:
            return prev, k - 1
        if d > 0:
            best, worst, cl = (h[k] - e) / s, (l[k] - e) / s, (c[k] - e) / s
        else:
            best, worst, cl = (e - l[k]) / s, (e - h[k]) / s, (e - c[k]) / s
        if worst <= st:
            return st, k
        if best > pk:
            pk = best
        if pk >= act:
            st = max(st, max(floor_min, pk - trail))
        if cl <= st:
            return st, k
        prev = cl
    return prev, last


# ----------------------------------------------------------------------------------------------
# STRATEGIES ON A WINDOW  [lo, hi)
# ----------------------------------------------------------------------------------------------
def stats(rs):
    rs = np.asarray(rs, dtype=float)
    if rs.size == 0:
        return {"n": 0, "win": 0.0, "avgR": 0.0, "totR": 0.0, "pf": 0.0, "maxdd": 0.0}
    wins, losses = rs[rs > 0].sum(), -rs[rs < 0].sum()
    eq = np.cumsum(rs)
    dd = float(np.max(np.maximum.accumulate(eq) - eq))
    return {"n": int(rs.size), "win": round(float((rs > 0).mean()) * 100, 1), "avgR": round(float(rs.mean()), 3),
            "totR": round(float(rs.sum()), 1), "pf": round(float(wins / losses), 2) if losses > 0 else 99.0,
            "maxdd": round(dd, 1)}


def run_ml_window(score_b, score_s, Rb, Rs, EXb, EXs, lo, hi, theta):
    """Sequential: when flat take the better side if its predicted R > theta; wait until it closes."""
    out = []
    i = lo
    while i < hi:
        sb, ss = score_b[i], score_s[i]
        if np.isnan(sb) or np.isnan(ss):
            i += 1
            continue
        if max(sb, ss) > theta:
            if sb >= ss:
                r, ex = Rb[i], EXb[i]
            else:
                r, ex = Rs[i], EXs[i]
            if np.isnan(r):
                i += 1
                continue
            out.append(r)
            i = max(i + 1, int(ex) + 1)
        else:
            i += 1
    return out


def run_random_window(Rb, Rs, EXb, EXs, lo, hi, seed):
    rng = random.Random(seed)
    out = []
    i = lo
    while i < hi:
        if rng.random() < 0.5:
            r, ex = Rb[i], EXb[i]
        else:
            r, ex = Rs[i], EXs[i]
        if np.isnan(r):
            i += 1
            continue
        out.append(r)
        i = max(i + 1, int(ex) + 1)
    return out


def run_straddle_window(o, h, l, c, t, sl_arr, pend, act, trail, floor_min, cost_arr, lo, hi, expiry_bars=5):
    """The old entry: levels at +/- pend * stop around price; re-centred every 5 minutes if unfilled."""
    out = []
    i = max(lo, 1)
    ref, armed_at = c[i - 1], i
    while i < hi:
        s = sl_arr[i]
        if i - armed_at >= expiry_bars:
            ref, armed_at = c[i - 1], i
        buy, sell = ref + pend * s, ref - pend * s
        up, dn = h[i] >= buy, l[i] <= sell
        if up and dn:
            d = 1 if abs(buy - o[i]) <= abs(o[i] - sell) else -1
        elif up:
            d = 1
        elif dn:
            d = -1
        else:
            i += 1
            continue
        e = buy if d > 0 else sell
        if i + 1 >= hi:
            break
        r, ex = sim_one(o, h, l, c, t, i + 1, e, d, s, act, trail, floor_min)
        out.append(r - cost_arr[i])
        i = ex + 1
        ref, armed_at = c[min(i - 1, len(c) - 1)], i
    return out


# ----------------------------------------------------------------------------------------------
# PREPARE ONE MARKET
# ----------------------------------------------------------------------------------------------
def _hgb():
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=120, max_depth=4, learning_rate=0.05,
                                         min_samples_leaf=300, l2_regularization=1.0, random_state=1)


def prepare(candles, cfg, pure=False):
    """cfg: sl_mode 'price'|'frac', sl, spread|cost_R, act, trail, floor_min."""
    arr = np.array(candles, dtype=float)
    t, o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    n = len(c)
    if cfg["sl_mode"] == "price":
        sl = np.full(n, cfg["sl"])
        cost = np.full(n, cfg["spread"] / cfg["sl"])
    else:
        sl = o * cfg["sl"]
        cost = np.full(n, cfg["cost_R"])
    X, fnames = build_features(t, o, h, l, c, pure=pure)
    Rb, EXb = sim_all(o, h, l, c, t, +1, sl, cfg["act"], cfg["trail"], cfg["floor_min"])
    Rs, EXs = sim_all(o, h, l, c, t, -1, sl, cfg["act"], cfg["trail"], cfg["floor_min"])
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "n": n, "sl": sl, "cost": cost, "X": X, "names": fnames,
            "Rb": Rb - cost, "Rs": Rs - cost, "EXb": EXb, "EXs": EXs}


def pick_theta(P, score_b, score_s, lo, hi):
    best_th, best_tot = None, -1e9
    for th in (-0.05, 0.0, 0.03, 0.06, 0.1, 0.15, 0.2, 0.3):
        rs = run_ml_window(score_b, score_s, P["Rb"], P["Rs"], P["EXb"], P["EXs"], lo, hi, th)
        if len(rs) >= 30 and float(np.sum(rs)) > best_tot:
            best_th, best_tot = th, float(np.sum(rs))
    return best_th


def train_final(P):
    """Fit on everything but the last 15% to choose the threshold, then refit on all rows."""
    n, X = P["n"], P["X"]
    valid = ~np.isnan(X).any(axis=1) & ~np.isnan(P["Rb"]) & ~np.isnan(P["Rs"])
    cut = int(n * 0.85)
    tr = np.where(valid[:cut - 130])[0]
    if tr.size < 5000:
        return None
    mb, ms = _hgb().fit(X[tr], P["Rb"][tr]), _hgb().fit(X[tr], P["Rs"][tr])
    rows = np.where(~np.isnan(X).any(axis=1))[0]
    sb = np.full(n, np.nan)
    ss = np.full(n, np.nan)
    sb[rows], ss[rows] = mb.predict(X[rows]), ms.predict(X[rows])
    th = pick_theta(P, sb, ss, cut, n)
    full = np.where(valid)[0]
    mb, ms = _hgb().fit(X[full], P["Rb"][full]), _hgb().fit(X[full], P["Rs"][full])
    return {"mb": mb, "ms": ms, "theta": th if th is not None else 0.1, "theta_found": th is not None, "names": P["names"],
            "ver": MODEL_VERSION, "bars": int(full.size)}


def analyse(name, candles, cfg, pure=False):
    """Walk-forward test: three later windows, each tested by a model trained only on data before it."""
    P = prepare(candles, cfg, pure=pure)
    n, t = P["n"], P["t"]
    if n < 20000:
        log(f"{name}: only {n} bars - too little to test")
        return None
    days = (t[-1] - t[0]) / 86400
    X, Rb, Rs, EXb, EXs = P["X"], P["Rb"], P["Rs"], P["EXb"], P["EXs"]
    label = "PRICE-ONLY" if pure else "with indicators"
    log(f"{name} [{label}]: {n} bars over {days:.0f} days, {len(P['names'])} features, "
        f"always-buy avg {np.nanmean(Rb):+.3f}R, always-sell avg {np.nanmean(Rs):+.3f}R")
    valid = ~np.isnan(X).any(axis=1) & ~np.isnan(Rb) & ~np.isnan(Rs)
    embargo = 130
    summary = {"ml": [], "ml0": [], "base": [], "rand": []}
    for fi, (te0, te1) in enumerate(((0.55, 0.70), (0.70, 0.85), (0.85, 1.0)), 1):
        te_lo, te_hi = int(n * te0), int(n * te1)
        va_lo = te_lo - int(n * 0.12)
        tr_hi = va_lo - embargo
        tr = np.where(valid[:tr_hi])[0]
        if tr.size < 5000:
            continue
        mb, ms = _hgb().fit(X[tr], Rb[tr]), _hgb().fit(X[tr], Rs[tr])
        rows = np.where(~np.isnan(X).any(axis=1))[0]
        sb = np.full(n, np.nan)
        ss = np.full(n, np.nan)
        sb[rows], ss[rows] = mb.predict(X[rows]), ms.predict(X[rows])
        th = pick_theta(P, sb, ss, va_lo, te_lo - embargo)
        res_ml = run_ml_window(sb, ss, Rb, Rs, EXb, EXs, te_lo, te_hi, th if th is not None else 0.1)
        res_ml0 = run_ml_window(sb, ss, Rb, Rs, EXb, EXs, te_lo, te_hi, 0.0)
        res_base = run_straddle_window(P["o"], P["h"], P["l"], P["c"], t, P["sl"], 0.75, cfg["act"], cfg["trail"],
                                       cfg["floor_min"], P["cost"], te_lo, te_hi)
        res_rand = []
        for sd in range(4):
            res_rand += run_random_window(Rb, Rs, EXb, EXs, te_lo, te_hi, sd)
        rr = stats(res_rand)
        rr["n"] = int(rr["n"] / 4)
        rr["totR"] = round(rr["totR"] / 4, 1)
        tdays = (t[te_hi - 1] - t[te_lo]) / 86400
        log(f"{name} [{label}] window{fi} ({tdays:.0f}d, threshold {th}): ML={json.dumps(stats(res_ml))} | "
            f"CURRENT={json.dumps(stats(res_base))} | RANDOM={json.dumps(rr)}")
        summary["ml"] += res_ml
        summary["ml0"] += res_ml0
        summary["base"] += res_base
    log(f"{name} [{label}] ALL TEST WINDOWS: ML={json.dumps(stats(summary['ml']))} | ML@0={json.dumps(stats(summary['ml0']))} | "
        f"CURRENT={json.dumps(stats(summary['base']))}")
    return summary


# ----------------------------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------------------------
EXNESS_SETUP = {
    # stop 20 pips; break-even at +10, then 10 pips behind (same as the live Aggressive Scalper). R = stop.
    "USOILm": {"pip": 0.01, "fallback_spread_pips": 3},
    "BTCUSDm": {"pip": 9.918, "fallback_spread_pips": 5},
}
DERIV_SYMBOLS = ["R_75", "R_100"]
EXN_CFG = lambda sl, spread: {"sl_mode": "price", "sl": sl, "spread": spread, "act": 0.5, "trail": 0.5, "floor_min": 0.0}
DER_CFG = lambda frac: {"sl_mode": "frac", "sl": frac, "cost_R": DERIV_COST_R, "act": 0.5, "trail": 0.15, "floor_min": 0.1}


def train_and_store_deriv(sym, candles):
    ok = 0
    for frac in DERIV_FRACS:
        try:
            P = prepare(candles, DER_CFG(frac), pure=False)
            m = train_final(P)
            if not m:
                log(f"{sym} stop {frac * 100:.1f}%: not enough data to train")
                continue
            size = store_put(f"model:{sym}:{frac:.4f}", m, {"symbol": sym, "frac": frac, "theta": m["theta"], "bars": m["bars"]})
            ok += 1
            log(f"{sym} stop {frac * 100:.1f}%: model saved (threshold {m['theta']}{'' if m['theta_found'] else ' default'}, {size // 1024} KB)")
        except Exception as e:
            log(f"{sym} stop {frac * 100:.1f}%: save failed {type(e).__name__}: {str(e)[:150]}")
    return ok


def train_and_store_exness(sym, candles, spread, pip):
    try:
        P = prepare(candles, EXN_CFG(20 * pip, spread), pure=False)
        m = train_final(P)
        if not m:
            log(f"{sym}: not enough data to train")
            return 0
        m["spread"] = spread
        size = store_put(f"model:{sym}:x20", m, {"symbol": sym, "theta": m["theta"], "bars": m["bars"], "spread": spread})
        log(f"{sym}: model saved (threshold {m['theta']}{'' if m['theta_found'] else ' default'}, {size // 1024} KB)")
        return 1
    except Exception as e:
        log(f"{sym}: save failed {type(e).__name__}: {str(e)[:150]}")
        return 0


def synthetic(n=60000, seed=3):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.001, n)
    for i in range(1, n):
        r[i] += 0.04 * r[i - 1]
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate([[c[0]], c[:-1]])
    spread = np.abs(rng.normal(0, 0.0005, n)) * c
    h = np.maximum(o, c) + spread
    l = np.minimum(o, c) - spread
    t = 1_700_000_000 + 60 * np.arange(n)
    return [[float(t[i]), float(o[i]), float(h[i]), float(l[i]), float(c[i])] for i in range(n)]


async def main():
    if "--selftest" in sys.argv:
        cs = synthetic()
        for pure in (False, True):
            analyse("SELFTEST", cs, DER_CFG(0.008), pure=pure)
        P = prepare(cs, DER_CFG(0.008))
        m = train_final(P)
        blob = pickle.loads(zlib.decompress(base64.b64decode(base64.b64encode(zlib.compress(pickle.dumps(m))))))
        log(f"SELFTEST model round-trip ok, theta {blob['theta']}")
        return
    log("run started (read-only, no trades)")
    # ---- phase 1: quick history, train and store the live entry models --------------------------
    data = {}
    for sym in DERIV_SYMBOLS:
        try:
            cs = await fetch_deriv_1m(sym, 120)
            if cs:
                data[("deriv", sym)] = (cs, None)
                train_and_store_deriv(sym, cs)
        except Exception as e:
            log(f"DERIV {sym}: failed {type(e).__name__}: {str(e)[:150]}")
        await asyncio.sleep(5)
    for sym, st in EXNESS_SETUP.items():
        try:
            cs, spread = await asyncio.to_thread(fetch_exness_1m, sym, 60)
            if not spread:
                spread = st["fallback_spread_pips"] * st["pip"]
                log(f"{sym}: using assumed spread {spread}")
            if cs:
                data[("exness", sym)] = (cs, spread)
                train_and_store_exness(sym, cs, spread, st["pip"])
        except Exception as e:
            log(f"EXNESS {sym}: failed {type(e).__name__}: {str(e)[:150]}")
    log("phase 1 done: live entry models stored")
    # ---- phase 2: all the history we can get, price-only research next to the indicator version ---
    for sym in DERIV_SYMBOLS:
        try:
            cs = await fetch_deriv_1m(sym, 365)
            for frac in (0.008, 0.002):
                for pure in (True, False):
                    analyse(f"DERIV {sym} (stop {frac * 100:.1f}%)", cs, DER_CFG(frac), pure=pure)
        except Exception as e:
            log(f"DERIV {sym} research: failed {type(e).__name__}: {str(e)[:150]}")
        await asyncio.sleep(5)
    for sym, st in EXNESS_SETUP.items():
        try:
            cs, spread = await asyncio.to_thread(fetch_exness_1m, sym, 180)
            spread = spread or st["fallback_spread_pips"] * st["pip"]
            for pure in (True, False):
                analyse(f"EXNESS {sym}", cs, EXN_CFG(20 * st["pip"], spread), pure=pure)
        except Exception as e:
            log(f"EXNESS {sym} research: failed {type(e).__name__}: {str(e)[:150]}")
    log("research run finished")


if __name__ == "__main__":
    try:
        os.nice(19)
    except Exception:
        pass
    asyncio.run(main())
