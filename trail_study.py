"""
Read-only study: when should the scalper start trailing? Reverse the 1-minute candle, every candle, no filter (the live rule
for BTC, gold and oil). Never places a trade. Started by the bot only while TRAIL_STUDY=1 is set. Prints "[TRAIL] ..." lines.

Each variant = (start trailing once profit reaches `act` x the stop, then keep the stop `trail` x the stop behind the best price,
never worse than break-even). Baseline is the live rule: act 1.0, trail 0.5. Same costs, horizon and stops as burst_all.py.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst_all as ba  # noqa: E402
import burst_research as br  # noqa: E402

VARIANTS = [("live: start at 1.0 stop, trail 0.5 behind", 1.0, 0.5),
            ("start at 0.75 stop, trail 0.5 behind", 0.75, 0.5),
            ("start at 0.5 stop, break-even then trail 0.5 behind", 0.5, 0.5),
            ("start at 0.5 stop, trail 0.25 behind", 0.5, 0.25),
            ("start at 0.33 stop, break-even then trail 0.33 behind", 0.33, 0.33)]


def log(msg):
    print(f"[TRAIL] {msg}", flush=True)


def simulate(t, o, h, l, c, stop_d, side, act, trail):
    rng = h - l
    med_r = br.rolling_median_prior(rng, br.LOOKBACK)
    trades = []
    i = br.LOOKBACK
    n = len(c)
    while i < n - 2:
        r = rng[i]
        if not (r > 0 and c[i] != o[i]):
            i += 1
            continue
        d = 1 if c[i] > o[i] else -1
        if side == "fade":
            d = -d
        e = o[i + 1]
        stop = e - d * stop_d
        best = e
        exit_p = None
        j = i + 1
        end = min(i + 1 + br.HORIZON, n)
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
            if (best - e) * d >= act * stop_d:
                tr = best - d * trail * stop_d
                new = max(e, tr) if d > 0 else min(e, tr)
                stop = max(stop, new) if d > 0 else min(stop, new)
            j += 1
        if exit_p is None:
            exit_p = c[min(j, n - 1) - 1] if j >= end else c[j]
        trades.append((t[i], (exit_p - e) * d / stop_d - br.COST_R))
        i = max(j, i + 1) + 1
    return trades


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    for sym, stop_d in ba.SYMBOLS.items():
        try:
            rows = ba.fetch(sym, int(os.getenv("BURSTALL_DAYS", "60")))
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} candles, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        days = max(1.0, len(np.unique((t // 86400).astype(int))))
        log(f"{sym}: {len(rows)} candles over {days:.0f} days, reverse the candle, every candle")
        for name, act, trail in VARIANTS:
            tr = simulate(t, o, h, l, c, stop_d, "fade", act, trail)
            s = br.stats(tr)
            if not s:
                continue
            cut = len(tr) // 2
            s1, s2 = br.stats(tr[:cut]), br.stats(tr[cut:])
            log(f"{sym} | {name}: trades={s[0]} ({s[0] / days:.0f}/day) win={s[1]:.0f}% net={s[2] * 100:+.0f}% of stop per trade | "
                f"older half {s1[2] * 100:+.0f}% newer half {s2[2] * 100:+.0f}%")
        if sym == "XAUUSDm" and os.getenv("TRAIL_GOLD_STOPS") == "1":
            for sd in (2.5, 3.0, 5.0):
                tr = simulate(t, o, h, l, c, sd, "fade", 1.0, 0.5)
                s = br.stats(tr)
                if not s:
                    continue
                cut = len(tr) // 2
                s1, s2 = br.stats(tr[:cut]), br.stats(tr[cut:])
                per = s[2] * sd  # 0.01 lot of gold = $1 per price unit
                log(f"XAUUSDm STOP {sd:g} price units (= ${sd:g} on 0.01 lot): trades={s[0]} ({s[0] / days:.0f}/day) win={s[1]:.0f}% "
                    f"net={s[2] * 100:+.0f}% of stop = ${per:+.2f} per trade, ${per * s[0] / days:+.1f} per day | "
                    f"older half {s1[2] * 100:+.0f}% newer half {s2[2] * 100:+.0f}%")
    log("done")


if __name__ == "__main__":
    main()
