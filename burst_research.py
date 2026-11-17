"""
Read-only study: do sudden bursts of activity on 1-minute candles (tick count and/or range far above
the last 30 minutes) continue far enough to beat the Aggressive Scalper stop?

Runs as its own low-priority process (started by the bot only while BURST_RESEARCH=1 is set). It never
places a trade and never touches the live path. Prints "[BURST] ..." lines.

Rules of the test (no look-ahead, pessimistic):
  - A burst is judged on a CLOSED 1-minute bar. Entry is the NEXT bar's open, in the direction of the burst bar.
  - One trade at a time per market.
  - Stop = the Aggressive Scalper stop (gold 50 pips, oil 25, BTC 25). Once price is a full stop-distance in
    profit the stop moves to break-even, then trails half a stop-distance behind. No take profit.
  - Inside a bar the stop is checked first. A trade open for 60 bars is closed at market.
  - Every trade is charged 0.1R for spread and slippage.
"""
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_research import fetch_h1  # noqa: E402  (1000-bar paging, cache, tickVolume kept)

SYMBOLS = {  # symbol -> stop distance in PRICE units (the bot's own pips: gold 0.1, oil 0.01, BTC 9.918)
    "XAUUSDm": 5.0,
    "USOILm": 0.25,
    "BTCUSDm": 25 * 9.918,
}
DAYS = int(os.getenv("BURST_DAYS", "20"))
LOOKBACK = 30
HORIZON = 60
COST_R = 0.10
BODY_FRAC = 0.6
# (tick-count multiple, range multiple); 0 = not used
GRID = [(0, 0), (2, 0), (3, 0), (0, 2), (0, 3), (2, 2), (3, 3), (4, 4)]


def log(msg):
    print(f"[BURST] {msg}", flush=True)


def rolling_median_prior(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        out[i] = np.median(x[i - n:i])
    return out


def simulate(t, o, h, l, c, v, kv, kr, stop_d, have_v):
    rng = h - l
    med_r = rolling_median_prior(rng, LOOKBACK)
    med_v = rolling_median_prior(v, LOOKBACK) if have_v else None
    trades = []
    i = LOOKBACK
    n = len(c)
    while i < n - 2:
        r = rng[i]
        body = abs(c[i] - o[i])
        ok = r > 0 and body >= BODY_FRAC * r
        if ok and kr:
            ok = med_r[i] > 0 and r >= kr * med_r[i]
        if ok and kv:
            ok = have_v and med_v[i] > 0 and v[i] >= kv * med_v[i]
        if not ok:
            i += 1
            continue
        d = 1 if c[i] > o[i] else -1
        e = o[i + 1]
        stop = e - d * stop_d
        best = e
        exit_p = None
        j = i + 1
        end = min(i + 1 + HORIZON, n)
        while j < end:
            if d > 0:
                if o[j] <= stop:
                    exit_p = o[j]; break
                if l[j] <= stop:
                    exit_p = stop; break
                best = max(best, h[j])
            else:
                if o[j] >= stop:
                    exit_p = o[j]; break
                if h[j] >= stop:
                    exit_p = stop; break
                best = min(best, l[j])
            if (best - e) * d >= stop_d:
                be = e
                tr = best - d * 0.5 * stop_d
                new = max(be, tr) if d > 0 else min(be, tr)
                stop = max(stop, new) if d > 0 else min(stop, new)
            j += 1
        if exit_p is None:
            exit_p = c[min(j, n - 1) - 1] if j >= end else c[j]
        r_mult = (exit_p - e) * d / stop_d - COST_R
        trades.append((t[i], r_mult))
        i = max(j, i + 1) + 1
    return trades


def stats(trades):
    if not trades:
        return None
    r = np.array([x[1] for x in trades])
    wins, losses = r[r > 0], r[r <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return len(r), float((r > 0).mean() * 100), float(r.mean()), float(pf)


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {list(SYMBOLS)}, {DAYS} days of 1-minute bars")
    for sym, stop_d in SYMBOLS.items():
        try:
            rows = fetch_h1(sym, DAYS, "1m")
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} bars, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c, v = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
        have_v = float(v.sum()) > 0 and float(np.median(v)) > 0
        days = max(1.0, (t[-1] - t[0]) / 86400.0)
        log(f"{sym}: {len(rows)} bars over {days:.1f} days, tick counts {'available' if have_v else 'MISSING'}, "
            f"stop {stop_d:g} price units")
        for kv, kr in GRID:
            if kv and not have_v:
                continue
            tr = simulate(t, o, h, l, c, v, kv, kr, stop_d, have_v)
            s = stats(tr)
            if not s:
                log(f"{sym} ticks>={kv}x range>={kr}x : no trades")
                continue
            cut = int(len(tr) * 0.7)
            a1, a2 = stats(tr[:cut]), stats(tr[cut:])
            label = "any bar in candle direction" if (kv, kr) == (0, 0) else f"ticks>={kv}x range>={kr}x"
            log(f"{sym} {label}: trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% "
                f"avg={s[2]:+.2f}R PF={s[3]:.2f} | first70%={a1[2] if a1 else float('nan'):+.2f}R "
                f"last30%={a2[2] if a2 else float('nan'):+.2f}R")
    log("done")


def selftest():
    rng = np.random.default_rng(3)
    n = 6000
    c = 100 + np.cumsum(rng.normal(0, 0.05, n))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.random(n) * 0.02
    l = np.minimum(o, c) - rng.random(n) * 0.02
    v = rng.integers(50, 150, n).astype(float)
    t = 1_700_000_000 + np.arange(n) * 60.0
    for kv, kr in GRID:
        print(kv, kr, stats(simulate(t, o, h, l, c, v, kv, kr, 0.5, True)))
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
