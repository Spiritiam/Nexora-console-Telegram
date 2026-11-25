"""
Read-only study: which market session does the BTCUSD scalper (reverse the 1-minute candle, every candle, no filter)
make or lose money in? Never places a trade. Started by the bot only while SESSION_STUDY=1 is set.
Prints "[SESSIONS] ..." lines. Same trade rules as burst_all.py. Times are UTC (Lagos is UTC+1).
"""
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst_all as ba  # noqa: E402
import burst_research as br  # noqa: E402

SESSIONS = [("Asian 00-07 UTC (01-08 Lagos)", 0, 7), ("London 07-13 UTC (08-14 Lagos)", 7, 13),
            ("New York 13-21 UTC (14-22 Lagos)", 13, 21), ("Late 21-24 UTC (22-01 Lagos)", 21, 24)]


def log(msg):
    print(f"[SESSIONS] {msg}", flush=True)


def line(label, tr, days):
    s = br.stats(tr)
    if not s or s[0] < 15:
        return f"{label}: too few trades ({0 if not s else s[0]})"
    cut = len(tr) // 2
    s1, s2 = br.stats(tr[:cut]), br.stats(tr[cut:])
    return (f"{label}: trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% net={s[2] * 100:+.0f}% of stop per trade | "
            f"older half {(s1[2] * 100) if s1 else float('nan'):+.0f}% newer half {(s2[2] * 100) if s2 else float('nan'):+.0f}%")


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    sym, stop_d = "BTCUSDm", 25 * 9.918
    rows = ba.fetch(sym, int(os.getenv("BURSTALL_DAYS", "60")))
    if len(rows) < 3000:
        log(f"only {len(rows)} candles, stopped")
        return
    a = np.array(rows, dtype=float)
    t, o, h, l, c, v = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
    have_v = float(v.sum()) > 0 and float(np.median(v)) > 0
    days = max(1.0, len(np.unique((t // 86400).astype(int))))
    br.BODY_FRAC = 0.0
    tr = br.simulate(t, o, h, l, c, v, 0, 0, stop_d, have_v, "fade", False, 0.0)
    log(f"{sym}: {len(rows)} candles over {days:.0f} days, reverse the candle, every candle")
    log(line("ALL DAY", tr, days))
    hour = lambda x: datetime.fromtimestamp(x[0], tz=timezone.utc).hour
    for name, a0, a1 in SESSIONS:
        log(line(name, [x for x in tr if a0 <= hour(x) < a1], days))
    for hh in range(24):
        log(line(f"hour {hh:02d} UTC", [x for x in tr if hour(x) == hh], days))
    log("done")


if __name__ == "__main__":
    main()
