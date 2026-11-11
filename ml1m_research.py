"""
One-off research run (read-only, nothing here places a trade).

Question: on a 1-minute chart, does a machine-learning entry beat the current Aggressive Scalper
entry (buy-stop + sell-stop around price) when the stop, break-even and trailing rules are the same?

Steps: pull 1-minute history (Exness: USOIL, BTCUSD; Deriv: R_75, R_100) slowly, build features from
past bars only, label every bar with what a BUY and a SELL would have returned under the real exit
rules, train on the earlier part of the data, test on later parts it never saw, and compare with
(a) the current straddle entry and (b) a random-direction entry with the same exits.

Results are printed as "[ML1M] ..." lines. Started by the bot when ML1M_RUN=1 (see trading_bot.py);
run with --selftest to check the code on made-up data without any network.
"""
import os
import sys
import json
import time
import math
import random
import asyncio
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import numpy as np

CACHE_DIR = "/tmp/ml1m_cache"
DERIV_PUBLIC_WS = "wss://api.derivws.com/trading/v1/options/ws/public"


def log(msg):
    print(f"[ML1M] {msg}", flush=True)


# ----------------------------------------------------------------------------------------------
# DATA
# ----------------------------------------------------------------------------------------------
def _cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, key + ".json")


def _load_cache(key, max_age_s=6 * 3600):
    p = _cache_path(key)
    try:
        if time.time() - os.path.getmtime(p) < max_age_s:
            with open(p) as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_cache(key, data):
    try:
        with open(_cache_path(key), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


async def fetch_deriv_1m(symbol, days):
    """1-minute candles from Deriv's public data socket (no login). ~2s between requests."""
    key = f"deriv_{symbol}_{days}"
    hit = _load_cache(key)
    if hit:
        log(f"{symbol}: using cached {len(hit)} candles")
        return hit
    import websockets
    rows = {}
    target = int(time.time()) - days * 86400
    cur_end = int(time.time())
    fails = 0
    async with websockets.connect(DERIV_PUBLIC_WS, open_timeout=20, close_timeout=5) as ws:
        use_underlying = False
        while cur_end > target:
            req = {"ticks_history": symbol, "style": "candles", "granularity": 60, "count": 5000, "end": cur_end}
            if use_underlying:
                req["underlying_symbol"] = symbol
            await ws.send(json.dumps(req))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=40))
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
    log(f"{symbol}: pulled {len(out)} candles")
    if out:
        _save_cache(key, out)
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


def fetch_exness_1m(mt5_symbol, days):
    """1-minute bid candles, 1000 per request, ~3s between requests. Also returns median spread."""
    key = f"exness_{mt5_symbol}_{days}"
    hit = _load_cache(key)
    if hit:
        log(f"{mt5_symbol}: using cached {len(hit['c'])} candles")
        return hit["c"], hit["spread"]
    client = _exness_client()
    inst = _exness_pick(client, mt5_symbol)
    if not inst:
        raise RuntimeError(f"Exness has no instrument like {mt5_symbol}")
    log(f"{mt5_symbol}: using instrument {inst}")
    now = datetime.now(timezone.utc)

    # median spread from the latest 1000 one-minute bars (bid and ask)
    spread = None
    try:
        q = urlencode({"instrument": inst, "price_type": "both", "timeframe": "M1",
                       "from": (now - timedelta(minutes=1000)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "to": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        code, d = client._call("GET", f"/v1/market-data/accounts/{client.account_id}/candles?{q}")
        if code == 200:
            sp = [float(r["ask"]["close"]) - float(r["bid"]["close"]) for r in (d.get("candles") or [])
                  if isinstance(r.get("bid"), dict) and isinstance(r.get("ask"), dict)]
            sp = [s for s in sp if s > 0]
            if sp:
                spread = float(np.median(sp))
        log(f"{mt5_symbol}: median spread {spread}")
    except Exception as e:
        log(f"{mt5_symbol}: spread lookup failed {type(e).__name__}")
    time.sleep(3)

    rows = {}
    start = now - timedelta(days=days)
    to = now + timedelta(minutes=1)
    fails = 0
    while to > start:
        frm = to - timedelta(minutes=1000)
        q = urlencode({"instrument": inst, "price_type": "bid", "timeframe": "M1",
                       "from": frm.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": to.strftime("%Y-%m-%dT%H:%M:%SZ")})
        code, d = client._call("GET", f"/v1/market-data/accounts/{client.account_id}/candles?{q}")
        if code == 429:
            fails += 1
            log(f"{mt5_symbol}: rate limited, resting 45s ({fails})")
            if fails >= 6:
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
        for r in d.get("candles") or []:
            try:
                t = datetime.fromisoformat(str(r["time"]).replace("Z", "+00:00")).timestamp()
                rows[int(t)] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
            except Exception:
                continue
        to = frm
        time.sleep(3)
    out = [[k, *rows[k]] for k in sorted(rows)]
    log(f"{mt5_symbol}: pulled {len(out)} candles")
    if out:
        _save_cache(key, {"c": out, "spread": spread})
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


def build_features(t, o, h, l, c):
    n = len(c)
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr14 = _roll_mean(tr, 14)
    atr5 = _roll_mean(tr, 5)
    atr60 = _roll_mean(tr, 60)
    atr = np.where(atr14 > 0, atr14, np.nan)
    f = {}
    f["atrp"] = atr14 / c
    f["vol_ratio"] = atr5 / np.where(atr60 > 0, atr60, np.nan)
    for k in (1, 3, 5, 10, 20, 60):
        r = np.full(n, np.nan)
        r[k:] = (c[k:] - c[:-k])
        f[f"ret{k}"] = r / atr
    f["range_last"] = (h - l) / atr
    f["body"] = (c - o) / atr
    f["upper_wick"] = (h - np.maximum(o, c)) / atr
    f["lower_wick"] = (np.minimum(o, c) - l) / atr
    for k in (20, 60):
        hi, lo = _roll_max(h, k), _roll_min(l, k)
        f[f"pos{k}"] = (c - lo) / np.where(hi - lo > 0, hi - lo, np.nan)
    e20, e50 = _ema(c, 20), _ema(c, 50)
    f["ema20_dist"] = (c - e20) / atr
    f["ema50_dist"] = (c - e50) / atr
    f["ema_gap"] = (e20 - e50) / atr
    d = np.diff(c, prepend=c[0])
    g = _roll_mean(np.where(d > 0, d, 0.0), 14)
    ls = _roll_mean(np.where(d < 0, -d, 0.0), 14)
    f["rsi14"] = g / np.where(g + ls > 0, g + ls, np.nan)
    # consecutive up/down bars
    run = np.zeros(n)
    for i in range(1, n):
        if c[i] > c[i - 1]:
            run[i] = max(run[i - 1], 0) + 1
        elif c[i] < c[i - 1]:
            run[i] = min(run[i - 1], 0) - 1
    f["run"] = np.clip(run, -10, 10)
    hh, ll = _roll_max(np.concatenate([[np.nan], h[:-1]]), 20) if False else None, None
    prev_hi = np.full(n, np.nan)
    prev_lo = np.full(n, np.nan)
    mx, mn = _roll_max(h, 20), _roll_min(l, 20)
    prev_hi[1:] = mx[:-1]
    prev_lo[1:] = mn[:-1]
    f["brk20"] = (c > prev_hi).astype(float) - (c < prev_lo).astype(float)
    rets = np.diff(np.log(c), prepend=np.log(c[0]))
    rv = np.sqrt(_roll_mean(rets * rets, 30))
    f["rv30_ratio"] = rv / np.where(f["atrp"] > 0, f["atrp"], np.nan)
    dt = [datetime.fromtimestamp(x, timezone.utc) for x in t]
    hr = np.array([x.hour + x.minute / 60 for x in dt])
    f["hr_s"] = np.sin(2 * np.pi * hr / 24)
    f["hr_c"] = np.cos(2 * np.pi * hr / 24)
    f["dow"] = np.array([x.weekday() for x in dt], dtype=float)
    names = list(f.keys())
    M = np.column_stack([f[k] for k in names])
    # row i must only use bars <= i-1: shift down by one
    X = np.full_like(M, np.nan)
    X[1:] = M[:-1]
    return X, names


# ----------------------------------------------------------------------------------------------
# TRADE SIMULATION (same exit rules as the live bots, worst case inside each bar)
# ----------------------------------------------------------------------------------------------
def sim_all(o, h, l, c, t, d, sl, act, trail, floor_min, maxbars=120, gapmax=600):
    """Vectorised: a trade entered at the OPEN of every bar i, direction d (+1/-1).
    sl is an array (price distance of the stop). Returns (R, exit_bar) arrays; NaN if not resolvable.
    Inside a bar the stop is checked before the high/low can lift it (pessimistic)."""
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
    """One trade, entry price e, first bar to process is i0. Returns (R, exit_bar)."""
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
    """The current entry: levels at +/- pend * stop around price; re-centred every 5 minutes if unfilled."""
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
        # the trade starts from the next bar (what happens later inside the trigger bar is unknown)
        if i + 1 >= hi:
            break
        r, ex = sim_one(o, h, l, c, t, i + 1, e, d, s, act, trail, floor_min)
        out.append(r - cost_arr[i])
        i = ex + 1
        ref, armed_at = c[min(i - 1, len(c) - 1)], i
    return out


# ----------------------------------------------------------------------------------------------
# ONE MARKET
# ----------------------------------------------------------------------------------------------
def analyse(name, candles, cfg):
    """cfg: dict(sl_mode 'price'|'frac', sl, pend, act, trail, floor_min, cost_R or spread)"""
    from sklearn.ensemble import HistGradientBoostingRegressor
    arr = np.array(candles, dtype=float)
    t, o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    n = len(c)
    if n < 20000:
        log(f"{name}: only {n} bars - too little to test")
        return None
    days = (t[-1] - t[0]) / 86400
    if cfg["sl_mode"] == "price":
        sl = np.full(n, cfg["sl"])
        cost = np.full(n, cfg["spread"] / cfg["sl"])
    else:
        sl = o * cfg["sl"]
        cost = np.full(n, cfg["cost_R"])
    X, fnames = build_features(t, o, h, l, c)
    log(f"{name}: {n} bars over {days:.0f} days, {len(fnames)} features, stop={'%.4g' % sl[n // 2]} cost_R={cost[n // 2]:.3f}")
    Rb, EXb = sim_all(o, h, l, c, t, +1, sl, cfg["act"], cfg["trail"], cfg["floor_min"])
    Rs, EXs = sim_all(o, h, l, c, t, -1, sl, cfg["act"], cfg["trail"], cfg["floor_min"])
    Rb, Rs = Rb - cost, Rs - cost
    log(f"{name}: raw outcome of ALWAYS buying / ALWAYS selling every minute (net R): "
        f"buy avg {np.nanmean(Rb):+.3f}, sell avg {np.nanmean(Rs):+.3f}")
    valid = ~np.isnan(X).any(axis=1) & ~np.isnan(Rb) & ~np.isnan(Rs)
    embargo = 130
    folds = [(0.0, 0.50, 0.65, 0.80), (0.0, 0.65, 0.80, 1.00)]
    summary = {"ml": [], "ml0": [], "base": [], "rand": []}
    for fi, (a0, a1, b1, c1) in enumerate(folds, 1):
        tr_lo, tr_hi = int(n * a0), int(n * a1) - embargo
        va_lo, va_hi = int(n * a1), int(n * b1) - embargo
        te_lo, te_hi = int(n * b1), int(n * c1)
        tr = np.where(valid[tr_lo:tr_hi])[0] + tr_lo
        if tr.size < 5000:
            continue
        mk = lambda: HistGradientBoostingRegressor(max_iter=120, max_depth=4, learning_rate=0.05,
                                                   min_samples_leaf=300, l2_regularization=1.0, random_state=1)
        mb, ms = mk().fit(X[tr], Rb[tr]), mk().fit(X[tr], Rs[tr])
        score_b = np.full(n, np.nan)
        score_s = np.full(n, np.nan)
        rows = np.where(~np.isnan(X).any(axis=1))[0]
        score_b[rows] = mb.predict(X[rows])
        score_s[rows] = ms.predict(X[rows])
        # pick the confidence threshold on the validation window only
        best_th, best_tot = None, -1e9
        for th in (-0.05, 0.0, 0.03, 0.06, 0.1, 0.15, 0.2, 0.3):
            rs = run_ml_window(score_b, score_s, Rb, Rs, EXb, EXs, va_lo, va_hi, th)
            if len(rs) >= 30 and float(np.sum(rs)) > best_tot:
                best_th, best_tot = th, float(np.sum(rs))
        res_ml = run_ml_window(score_b, score_s, Rb, Rs, EXb, EXs, te_lo, te_hi, best_th if best_th is not None else 0.1)
        res_ml0 = run_ml_window(score_b, score_s, Rb, Rs, EXb, EXs, te_lo, te_hi, 0.0)
        res_base = run_straddle_window(o, h, l, c, t, sl, cfg["pend"], cfg["act"], cfg["trail"], cfg["floor_min"], cost, te_lo, te_hi)
        res_rand = []
        for sd in range(8):
            res_rand += run_random_window(Rb, Rs, EXb, EXs, te_lo, te_hi, sd)
        res_rand = list(np.array(res_rand)[: max(1, len(res_rand) // 8) * 8])
        test_days = (t[te_hi - 1] - t[te_lo]) / 86400
        rr = stats(res_rand)
        rr["n"] = int(rr["n"] / 8)
        rr["totR"] = round(rr["totR"] / 8, 1)
        log(f"{name} fold{fi} test {test_days:.0f}d (threshold {best_th}): "
            f"ML={json.dumps(stats(res_ml))} | ML@0={json.dumps(stats(res_ml0))} | "
            f"CURRENT={json.dumps(stats(res_base))} | RANDOM(avg of 8)={json.dumps(rr)}")
        top = sorted(zip(fnames, [0] * len(fnames)))
        summary["ml"] += res_ml
        summary["ml0"] += res_ml0
        summary["base"] += res_base
        summary["rand"] += res_rand
    log(f"{name} ALL TEST WINDOWS: ML={json.dumps(stats(summary['ml']))} | ML@0={json.dumps(stats(summary['ml0']))} | "
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
DERIV_SETUP = {"R_75": None, "R_100": None}


def synthetic(n=60000, seed=3):
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 0.001, n)
    for i in range(1, n):                       # a little momentum so a model has something to find
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
        analyse("SELFTEST", cs, {"sl_mode": "frac", "sl": 0.008, "cost_R": 0.055, "pend": 0.75, "act": 0.5,
                                "trail": 0.15, "floor_min": 0.1})
        return
    log("research run started (read-only, no trades)")
    # Deriv first (public data, no login): the stop is 0.8% of price (stake 5, stop 2, x50), cost 0.11 commission.
    for sym in DERIV_SETUP:
        try:
            cs = await fetch_deriv_1m(sym, 120)
            if cs:
                for sl_frac, cost_r, tag in ((0.008, 0.055, "stop 0.8%"), (0.004, 0.11, "stop 0.4%")):
                    analyse(f"DERIV {sym} ({tag})", cs, {"sl_mode": "frac", "sl": sl_frac, "cost_R": cost_r, "pend": 0.75,
                                                        "act": 0.5, "trail": 0.15, "floor_min": 0.1})
        except Exception as e:
            log(f"DERIV {sym}: failed {type(e).__name__}: {str(e)[:150]}")
        await asyncio.sleep(5)
    for sym, st in EXNESS_SETUP.items():
        try:
            cs, spread = await asyncio.to_thread(fetch_exness_1m, sym, 60)
            if not cs:
                continue
            pip = st["pip"]
            if not spread:
                spread = st["fallback_spread_pips"] * pip
                log(f"{sym}: using assumed spread {spread}")
            sl = 20 * pip
            for mult, tag in ((1.0, "spread as measured"), (2.0, "spread doubled")):
                analyse(f"EXNESS {sym} ({tag})", cs, {"sl_mode": "price", "sl": sl, "spread": spread * mult, "pend": 0.75,
                                                    "act": 0.5, "trail": 0.5, "floor_min": 0.0})
        except Exception as e:
            log(f"EXNESS {sym}: failed {type(e).__name__}: {str(e)[:150]}")
    log("research run finished")


if __name__ == "__main__":
    try:
        os.nice(19)
    except Exception:
        pass
    asyncio.run(main())
