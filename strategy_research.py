"""
Offline strategy research on the MT5 (Exness via MetaAPI) price history.
Runs as its own low-priority process (started by the bot when RESEARCH_RUN=1 is set).
It never places a trade and never touches the live signal path.

What it does
  1. Pulls ~3 years of H1 candles for XAUUSDm, GBPUSDm, BTCUSDm from the MetaAPI market-data API
     (pages backwards 1000 bars at a time, gentle on rate limits, cached in /tmp).
  2. Backtests four rule sets on closed bars, entry on the NEXT bar open, stop checked before target
     inside a bar (pessimistic), costs charged on every trade:
        ema_stack      the production 25/50/100 EMA stack bounce rules, on H1
        linreg_trend   strong straight-line trend only (slope and R^2 filter), entry on pullback
        linreg_revert  stretched from the regression line (z-score) in a NON-trending market only
        nn_pattern     small neural net on the last bars' shape, retrained walk-forward
  3. Reports each one per market with: trades, win rate, expectancy in R, profit factor, max drawdown (R),
     the result in 6 sequential windows, and a first-70% / last-30% split. Nothing is refit or tuned on
     the test windows.
Results print as "[RESEARCH] ..." lines and are stored in Supabase table ml1m_store (key research_report,
readable from meta).

Run with --selftest to check the maths on made-up data without any network.
"""
import os
import sys
import json
import time
import math
import base64
import zlib
import pickle
from datetime import datetime, timezone, timedelta

import numpy as np
import requests

CACHE_DIR = "/tmp/research_cache"
SYMBOLS = [s.strip() for s in os.getenv("RESEARCH_SYMBOLS", "XAUUSDm,GBPUSDm,BTCUSDm").split(",") if s.strip()]
DAYS = int(os.getenv("RESEARCH_DAYS", "1095"))
MD_HOST = "https://mt-market-data-client-api-v1.new-york.agiliumtrade.ai"
COST_ATR = 0.10          # round-trip spread + slippage, in ATR (about 0.1R on a 1 ATR stop)
HORIZON = 48             # bars a trade may stay open before it is closed at market


def log(msg):
    print(f"[RESEARCH] {msg}", flush=True)


# ----------------------------------------------------------------------------------------------
# DATA
# ----------------------------------------------------------------------------------------------
def _ts(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


def fetch_h1(symbol, days):
    path = os.path.join(CACHE_DIR, f"{symbol}_h1.json")
    os.makedirs(CACHE_DIR, exist_ok=True)
    rows = {}
    try:
        with open(path) as f:
            for r in json.load(f):
                rows[int(r[0])] = r
    except Exception:
        pass
    target = time.time() - days * 86400
    if rows and min(rows) <= target + 7 * 86400:
        return [rows[k] for k in sorted(rows)]
    token, acct = os.getenv("METAAPI_TOKEN"), os.getenv("METAAPI_ACCOUNT_ID")
    if not token or not acct:
        raise RuntimeError("MetaAPI credentials not set")
    headers = {"auth-token": token, "Accept": "application/json"}
    cur = datetime.fromtimestamp(min(rows) - 1, tz=timezone.utc) if rows else datetime.now(timezone.utc)
    fails = 0
    pages = 0
    while cur.timestamp() > target and pages < 60:
        url = (f"{MD_HOST}/users/current/accounts/{acct}/historical-market-data/symbols/{symbol}"
               f"/timeframes/1h/candles?startTime={cur.strftime('%Y-%m-%dT%H:%M:%S.000Z')}&limit=1000")
        try:
            r = requests.get(url, headers=headers, timeout=60)
        except Exception as e:
            fails += 1
            log(f"{symbol} request error {type(e).__name__}")
            if fails > 5:
                break
            time.sleep(5 * fails)
            continue
        if r.status_code in (429, 502, 503, 504):
            fails += 1
            log(f"{symbol} HTTP {r.status_code}, backing off")
            if fails > 8:
                break
            time.sleep(8 * fails)
            continue
        if r.status_code != 200:
            log(f"{symbol} HTTP {r.status_code} {r.text[:120]}")
            break
        fails = 0
        raw = r.json()
        if not raw:
            break
        new = 0
        for c in raw:
            t = int(_ts(c["time"]))
            if t not in rows:
                rows[t] = [t, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]),
                           float(c.get("tickVolume") or 0)]
                new += 1
        pages += 1
        oldest = min(int(_ts(c["time"])) for c in raw)
        if new == 0 or oldest >= cur.timestamp():
            break
        cur = datetime.fromtimestamp(oldest - 1, tz=timezone.utc)
        time.sleep(0.6)
    out = [rows[k] for k in sorted(rows)]
    try:
        with open(path, "w") as f:
            json.dump(out, f)
    except Exception:
        pass
    return out


# ----------------------------------------------------------------------------------------------
# INDICATORS (numpy, no look-ahead: value at bar i uses bars <= i)
# ----------------------------------------------------------------------------------------------
def ema(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    k = 2.0 / (n + 1)
    prev = x[:n].mean()
    out[n - 1] = prev
    for i in range(n, len(x)):
        prev = x[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def atr(h, l, c, n=14):
    pc = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    a = tr[1:n + 1].mean()
    out[n] = a
    for i in range(n + 1, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def linreg(c, n):
    """slope per bar, R^2, fitted value at the last bar, residual std, over a rolling window of n bars."""
    N = len(c)
    slope = np.full(N, np.nan)
    r2 = np.full(N, np.nan)
    fit = np.full(N, np.nan)
    sd = np.full(N, np.nan)
    if N < n:
        return slope, r2, fit, sd
    from numpy.lib.stride_tricks import sliding_window_view
    w = sliding_window_view(c, n)                       # (N-n+1, n)
    x = np.arange(n, dtype=float)
    xm = x.mean()
    xv = ((x - xm) ** 2).sum()
    ym = w.mean(axis=1)
    cov = ((w - ym[:, None]) * (x - xm)[None, :]).sum(axis=1)
    b = cov / xv
    a = ym - b * xm
    pred = a[:, None] + b[:, None] * x[None, :]
    ss_res = ((w - pred) ** 2).sum(axis=1)
    ss_tot = ((w - ym[:, None]) ** 2).sum(axis=1)
    slope[n - 1:] = b
    r2[n - 1:] = np.where(ss_tot > 0, 1 - ss_res / np.where(ss_tot > 0, ss_tot, 1), 0)
    fit[n - 1:] = a + b * (n - 1)
    sd[n - 1:] = np.sqrt(ss_res / n)
    return slope, r2, fit, sd


# ----------------------------------------------------------------------------------------------
# SIGNALS: each returns an int array: +1 long, -1 short, 0 none, decided on the CLOSE of bar i
# ----------------------------------------------------------------------------------------------
def sig_ema_stack(o, h, l, c, a):
    e25, e50, e100 = ema(c, 25), ema(c, 50), ema(c, 100)
    N = len(c)
    s = np.zeros(N, dtype=int)
    hold = 3
    for i in range(130, N):
        if np.isnan(a[i]) or a[i] <= 0:
            continue
        up = all(e25[j] > e50[j] > e100[j] for j in range(i - hold + 1, i + 1))
        dn = all(e25[j] < e50[j] < e100[j] for j in range(i - hold + 1, i + 1))
        slope = e100[i] - e100[i - 6]
        gap_slow = abs(e50[i] - e100[i]) / a[i]
        gap_fast = abs(e25[i] - e50[i]) / a[i]
        if gap_slow < 0.30 or gap_fast < 0.10:
            continue
        tol = 0.15 * a[i]
        if up and slope > 0 and c[i] > o[i]:
            if (l[i] <= e25[i] + tol and c[i] > e25[i]) or (l[i] <= e50[i] + tol and c[i] > e50[i]):
                s[i] = 1
        elif dn and slope < 0 and c[i] < o[i]:
            if (h[i] >= e25[i] - tol and c[i] < e25[i]) or (h[i] >= e50[i] - tol and c[i] < e50[i]):
                s[i] = -1
    return s


def sig_linreg_trend(o, h, l, c, a):
    slope, r2, fit, sd = linreg(c, 50)
    N = len(c)
    s = np.zeros(N, dtype=int)
    for i in range(60, N):
        if np.isnan(slope[i]) or np.isnan(a[i]) or a[i] <= 0:
            continue
        strength = slope[i] * 50 / a[i]        # total drift over the window, in ATR
        if r2[i] < 0.6 or abs(strength) < 3.0:
            continue                            # only a strong, straight trend
        if strength > 0 and c[i - 1] <= fit[i - 1] and c[i] > fit[i]:
            s[i] = 1
        elif strength < 0 and c[i - 1] >= fit[i - 1] and c[i] < fit[i]:
            s[i] = -1
    return s


def sig_linreg_revert(o, h, l, c, a):
    slope, r2, fit, sd = linreg(c, 50)
    N = len(c)
    s = np.zeros(N, dtype=int)
    for i in range(60, N):
        if np.isnan(slope[i]) or sd[i] <= 0:
            continue
        if r2[i] > 0.3:
            continue                            # only when the market is NOT trending
        z = (c[i] - fit[i]) / sd[i]
        if z <= -2.0:
            s[i] = 1
        elif z >= 2.0:
            s[i] = -1
    return s


# (stop, target) in ATR for each rule set. Fixed in advance, not tuned.
RULES = {
    "ema_stack": (sig_ema_stack, 1.5, 3.0),
    "linreg_trend": (sig_linreg_trend, 1.5, 3.0),
    "linreg_revert": (sig_linreg_revert, 1.5, 1.5),
}


# ----------------------------------------------------------------------------------------------
# TRADE SIMULATION
# ----------------------------------------------------------------------------------------------
def simulate(o, h, l, c, a, sig, sl_atr, tp_atr, start=0, end=None):
    """Enter on the open of the bar after the signal. One position at a time. Returns list of (bar, R)."""
    N = len(c)
    end = N if end is None else end
    trades = []
    i = max(start, 1)
    while i < end - 1:
        d = sig[i]
        if d == 0 or np.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue
        entry = o[i + 1]
        risk = sl_atr * a[i]
        sl = entry - d * risk
        tp = entry + d * tp_atr * a[i]
        r = None
        j = i + 1
        last = min(end - 1, i + 1 + HORIZON)
        while j <= last:
            hit_sl = (l[j] <= sl) if d == 1 else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if d == 1 else (l[j] <= tp)
            if hit_sl:                           # stop first when both touch in one bar
                r = -1.0
                break
            if hit_tp:
                r = tp_atr / sl_atr
                break
            j += 1
        if r is None:
            j = min(last, end - 1)
            r = d * (c[j] - entry) / risk
        r -= COST_ATR / sl_atr
        trades.append((i, r))
        i = j + 1
    return trades


def summarize(trades):
    rs = np.array([t[1] for t in trades], dtype=float)
    n = len(rs)
    if n == 0:
        return {"n": 0, "win": 0.0, "exp": 0.0, "pf": 0.0, "dd": 0.0, "net": 0.0}
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    eq = np.cumsum(rs)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf")
    return {"n": int(n), "win": round(float((rs > 0).mean() * 100), 1), "exp": round(float(rs.mean()), 3),
            "pf": round(min(pf, 99.0), 2), "dd": round(dd, 1), "net": round(float(rs.sum()), 1)}


def windows(trades, N, k=6):
    edges = np.linspace(0, N, k + 1)
    out = []
    for w in range(k):
        part = [t for t in trades if edges[w] <= t[0] < edges[w + 1]]
        out.append(round(float(sum(t[1] for t in part)), 1))
    return out


def report(name, sym, trades, N):
    cut = int(N * 0.7)
    tr_a = [t for t in trades if t[0] < cut]
    tr_b = [t for t in trades if t[0] >= cut]
    res = {"strategy": name, "symbol": sym, "all": summarize(trades), "first70": summarize(tr_a),
           "last30": summarize(tr_b), "windows_R": windows(trades, N)}
    s = res["all"]
    log(f"{sym:9s} {name:14s} trades {s['n']:4d} win {s['win']:5.1f}% exp {s['exp']:+.3f}R PF {s['pf']:.2f} "
        f"net {s['net']:+.1f}R maxDD {s['dd']:.1f}R | first70 {res['first70']['exp']:+.3f}R ({res['first70']['n']}) "
        f"last30 {res['last30']['exp']:+.3f}R ({res['last30']['n']}) | windows {res['windows_R']}")
    return res


# ----------------------------------------------------------------------------------------------
# NEURAL NET PATTERN RULE (walk-forward: trained only on bars BEFORE the window it trades)
# ----------------------------------------------------------------------------------------------
def nn_features(o, h, l, c, a, ts):
    N = len(c)
    slope, r2, fit, sd = linreg(c, 50)
    F = np.full((N, 20), np.nan)
    for i in range(60, N):
        if np.isnan(a[i]) or a[i] <= 0 or np.isnan(slope[i]):
            continue
        rets = [(c[i - k] - c[i - k - 1]) / a[i] for k in range(12)]
        hr = datetime.fromtimestamp(ts[i], tz=timezone.utc).hour
        F[i, :12] = rets
        F[i, 12] = slope[i] * 50 / a[i]
        F[i, 13] = r2[i]
        F[i, 14] = (c[i] - fit[i]) / sd[i] if sd[i] > 0 else 0
        F[i, 15] = (h[i] - l[i]) / a[i]
        F[i, 16] = (c[i] - o[i]) / a[i]
        F[i, 17] = math.sin(2 * math.pi * hr / 24)
        F[i, 18] = math.cos(2 * math.pi * hr / 24)
        F[i, 19] = (c[i] - c[i - 24]) / a[i]
    return F


def sig_nn(o, h, l, c, a, ts, windows_n=6):
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    N = len(c)
    F = nn_features(o, h, l, c, a, ts)
    fwd = np.full(N, np.nan)
    for i in range(N - 13):
        if not np.isnan(a[i]) and a[i] > 0:
            fwd[i] = (c[i + 12] - c[i]) / a[i]
    y = np.where(fwd > 0.5, 1, np.where(fwd < -0.5, 0, -1))
    sig = np.zeros(N, dtype=int)
    edges = np.linspace(0, N, windows_n + 1).astype(int)
    for w in range(2, windows_n):               # first two windows are training only
        tr_end = edges[w] - 13                  # leave a gap so labels never see the test window
        te0, te1 = edges[w], edges[w + 1]
        idx = np.where((np.arange(N) < tr_end) & ~np.isnan(F).any(axis=1) & (y >= 0))[0]
        if len(idx) < 500:
            continue
        sc = StandardScaler().fit(F[idx])
        clf = MLPClassifier(hidden_layer_sizes=(24, 12), alpha=1e-2, max_iter=300, early_stopping=True,
                            random_state=7).fit(sc.transform(F[idx]), y[idx])
        te = np.where((np.arange(N) >= te0) & (np.arange(N) < te1) & ~np.isnan(F).any(axis=1))[0]
        if not len(te):
            continue
        p = clf.predict_proba(sc.transform(F[te]))[:, 1]
        sig[te[p >= 0.60]] = 1
        sig[te[p <= 0.40]] = -1
    return sig, edges[2]


# ----------------------------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------------------------
def run_symbol(sym, rows):
    arr = np.array(rows, dtype=float)
    ts, o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    N = len(c)
    span = (ts[-1] - ts[0]) / 86400
    log(f"{sym}: {N} H1 bars, {span:.0f} days ({datetime.fromtimestamp(ts[0], tz=timezone.utc):%Y-%m-%d} to "
        f"{datetime.fromtimestamp(ts[-1], tz=timezone.utc):%Y-%m-%d})")
    a = atr(h, l, c)
    out = []
    for name, (fn, sl, tp) in RULES.items():
        sig = fn(o, h, l, c, a)
        out.append(report(name, sym, simulate(o, h, l, c, a, sig, sl, tp), N))
    try:
        sig, first = sig_nn(o, h, l, c, a, ts)
        tr = simulate(o, h, l, c, a, sig, 1.5, 3.0, start=first)
        out.append(report("nn_pattern", sym, [(t[0], t[1]) for t in tr], N))
    except Exception as e:
        log(f"{sym} nn_pattern failed: {type(e).__name__}: {e}")
    # buy and hold reference
    out.append({"strategy": "buy_hold", "symbol": sym, "pct": round(float((c[-1] / c[0] - 1) * 100), 1)})
    return out


def store_report(obj):
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        return
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
         "Prefer": "resolution=merge-duplicates,return=minimal"}
    blob = base64.b64encode(zlib.compress(pickle.dumps(obj), 6)).decode()
    r = requests.post(f"{url}/rest/v1/ml1m_store?on_conflict=key", headers=h, timeout=60,
                      data=json.dumps({"key": "research_report", "data": blob, "meta": obj,
                                       "updated_at": datetime.now(timezone.utc).isoformat()}))
    log(f"report stored (HTTP {r.status_code})")


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {SYMBOLS}, {DAYS} days")
    results, depth = [], {}
    for sym in SYMBOLS:
        try:
            rows = fetch_h1(sym, DAYS)
            depth[sym] = len(rows)
            if len(rows) < 2000:
                log(f"{sym}: only {len(rows)} bars, skipped")
                continue
            results += run_symbol(sym, rows)
        except Exception as e:
            log(f"{sym} failed: {type(e).__name__}: {e}")
    store_report({"when": datetime.now(timezone.utc).isoformat(), "bars": depth, "results": results,
                  "cost_atr": COST_ATR, "horizon_bars": HORIZON})
    log("done")


def selftest():
    rng = np.random.default_rng(1)
    n = 4000
    c = 100 + np.cumsum(rng.normal(0, 0.3, n))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.random(n) * 0.2
    l = np.minimum(o, c) - rng.random(n) * 0.2
    ts = 1_700_000_000 + np.arange(n) * 3600
    a = atr(h, l, c)
    assert not np.isnan(a[-1])
    sl_, r2_, fit_, sd_ = linreg(np.arange(100, dtype=float), 50)
    assert abs(sl_[-1] - 1.0) < 1e-9 and abs(r2_[-1] - 1.0) < 1e-9
    for name, (fn, sl, tp) in RULES.items():
        s = fn(o, h, l, c, a)
        tr = simulate(o, h, l, c, a, s, sl, tp)
        print(name, summarize(tr))
    sg, first = sig_nn(o, h, l, c, a, ts)
    print("nn signals", int((sg != 0).sum()), "first test bar", first)
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
