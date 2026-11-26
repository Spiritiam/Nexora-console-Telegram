"""
Read-only study: can the price-action strategy bank give the Aggressive Scalper its entries on the 1-minute chart?

Never places a trade and never touches the live path. Started by the bot only while BANK_SCALPER_STUDY=1 is set.
Prints "[BSS] ..." lines.

Bank on the scalper's charts: the 5-minute chart gives the direction (HH/HL up, LH/LL down, else range), the strategies run
on the last CLOSED 1-minute candle, 2+ strategies from 2+ families must agree (one more against the 5-minute direction).
Compared with the entry the scalper uses today: reverse every closed 1-minute candle.

Trade rules are the same for every row (same as burst_all.py / trail_study.py): entry at the NEXT candle's open, the
Aggressive Scalper stop (gold 50 pips, oil 25, BTC 25), once price is 0.75 of a stop in profit the stop moves to
break-even then trails half a stop behind, closed at market after 60 candles, 0.1 of a stop charged per trade for spread
and slippage, one trade at a time, stop checked first inside a candle. Data: Exness 1-minute bid candles.
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst_all as ba  # noqa: E402
import burst_research as br  # noqa: E402
import price_action_bank as pab  # noqa: E402

DAYS = int(os.getenv("BSS_DAYS", "30"))
MODE = os.getenv("BSS_MODE", "1m")   # "1m": strategies on 1-minute, direction on 5-minute. "5m": strategies on 5-minute, direction on 15-minute
SIG_WIN = 300      # 1-minute candles the strategies see
BIAS_WIN = 100     # 5-minute candles that give the direction
ACT, TRAIL = 0.75, 0.5   # live scalper trailing: starts at 0.75 of the stop, then half a stop behind
HORIZON = br.HORIZON
COST_R = br.COST_R
LOOK = 60 if MODE == "1m" else 24   # "recent low / high" for the counter-trend rule = the last hour (1m) or two hours (5m)
SIG_S, BIAS_S = (60, 300) if MODE == "1m" else (300, 900)


def log(msg):
    print(f"[BSS] {msg}", flush=True)


class _X:
    pass


def run_trades(t, o, h, l, c, stop_d, decide):
    """decide(i) -> +1 / -1 / 0 on the closed candle i. Entry at open of i+1. One trade at a time."""
    n = len(c)
    trades = []
    i = 40
    while i < n - 2:
        d = decide(i)
        if not d:
            i += 1
            continue
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
            if (best - e) * d >= ACT * stop_d:
                tr = best - d * TRAIL * stop_d
                new = max(e, tr) if d > 0 else min(e, tr)
                stop = max(stop, new) if d > 0 else min(stop, new)
            j += 1
        if exit_p is None:
            exit_p = c[min(j, n - 1) - 1] if j >= end else c[j]
        trades.append((t[i], (exit_p - e) * d / stop_d - COST_R))
        i = max(j, i + 1) + 1
    return trades


def build_frames(a):
    """1-minute rows -> candle dict lists for the 1-minute chart, the 5-minute chart and the daily chart."""
    t = a[:, 0].astype(np.int64)
    m1 = [{"time": int(a[k, 0]), "open": a[k, 1], "high": a[k, 2], "low": a[k, 3], "close": a[k, 4], "volume": a[k, 5]}
          for k in range(len(a))]

    def agg(sec):
        out, key, cur = [], None, None
        for k in range(len(a)):
            g = int(t[k] // sec)
            if g != key:
                if cur:
                    out.append(cur)
                key = g
                cur = {"time": g * sec, "open": a[k, 1], "high": a[k, 2], "low": a[k, 3], "close": a[k, 4], "volume": a[k, 5]}
            else:
                cur["high"] = max(cur["high"], a[k, 2])
                cur["low"] = min(cur["low"], a[k, 3])
                cur["close"] = a[k, 4]
                cur["volume"] += a[k, 5]
        if cur:
            out.append(cur)
        return out

    return m1, agg(300), agg(86400), agg(900)


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start ({MODE} mode): {list(ba.SYMBOLS)}, {DAYS} days of 1-minute candles")
    for sym, stop_d in ba.SYMBOLS.items():
        try:
            rows = ba.fetch(sym, DAYS)
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} candles, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        days = max(1.0, len(np.unique((t // 86400).astype(int))))
        m1, m5, d1, m15 = build_frames(a)
        sig_frames, bias_frames = (m1, m5) if MODE == "1m" else (m5, m15)
        bias_end = np.array([x["time"] + BIAS_S for x in bias_frames])
        sig_end = np.array([x["time"] + SIG_S for x in sig_frames])
        d1_start = np.array([x["time"] for x in d1])
        log(f"{sym}: {len(rows)} candles over {days:.0f} trading days, stop {stop_d:g} price units")

        cache = {}
        t0 = time.time()
        counter = [0]

        def scan(i):
            """All strategies on the signal chart as of closed 1-minute candle i (cached: every row of the table reuses it).
            In 5m mode this only runs when candle i is the last minute of a 5-minute candle."""
            if i in cache:
                return cache[i]
            now = float(t[i]) + 60.0
            if MODE == "5m" and now % 300 != 0:
                return None
            kb = int(np.searchsorted(bias_end, now, side="right"))     # bias-chart candles closed by now
            ks = int(np.searchsorted(sig_end, now, side="right"))      # signal-chart candles closed by now
            kd = int(np.searchsorted(d1_start, (now // 86400) * 86400, side="left"))  # completed days only
            sig = sig_frames[max(0, ks - SIG_WIN): ks]
            bias = bias_frames[max(0, kb - BIAS_WIN): kb]
            X, ideas, n_sig, why = pab.scan_pa(bias, sig, sig, d1[max(0, kd - 45): kd], now,
                                               sig_s=SIG_S, bias_s=BIAS_S, c5_s=SIG_S, min_sig=120, min_bias=40)
            if X is None:
                cache[i] = None
            else:
                x = _X()
                x.bias, x.px, x.a = X.bias, X.px, X.a
                look = n_sig[-LOOK:]
                tail = [{"low": min(q["low"] for q in look), "high": max(q["high"] for q in look)}]
                cache[i] = (x, ideas, tail)
            counter[0] += 1
            if counter[0] % 4000 == 0:
                log(f"{sym}: {counter[0]} charts checked, {time.time() - t0:.0f}s")
            return cache[i]

        def bank(min_agree):
            def decide(i):
                s = scan(i)
                if s is None:
                    return 0
                x, ideas, tail = s
                r = pab.pick_pa(x, ideas, tail, min_agree=min_agree, look=len(tail), why_tf="5m" if MODE == "1m" else "15m")
                b = r["best"]
                return 0 if not b else (1 if b["direction"] == "BUY" else -1)
            return decide

        def fade(i):
            if h[i] > l[i] and c[i] != o[i]:
                return -1 if c[i] > o[i] else 1
            return 0

        tfn = "1-minute" if MODE == "1m" else "5-minute"
        rows_out = [("today's entry: reverse every 1-minute candle", fade),
                    (f"bank on {tfn} chart, 2+ agree (2+ groups)", bank(2)),
                    (f"bank on {tfn} chart, 3+ agree (2+ groups)", bank(3))]
        for name, fn in rows_out:
            tr = run_trades(t, o, h, l, c, stop_d, fn)
            s = br.stats(tr)
            if not s:
                log(f"{sym} | {name}: no trades")
                continue
            cut = len(tr) // 2
            s1, s2 = br.stats(tr[:cut]), br.stats(tr[cut:])
            extra = f" = ${s[2] * stop_d:+.2f} per trade on 0.01 lot" if sym.upper().startswith("XAU") else ""
            log(f"{sym} | {name}: trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% "
                f"avg={s[2] * 100:+.0f}% of stop per trade{extra} | older half {(s1[2] * 100 if s1 else 0):+.0f}% "
                f"newer half {(s2[2] * 100 if s2 else 0):+.0f}%")
        log(f"{sym}: done in {time.time() - t0:.0f}s")
    log("done")


if __name__ == "__main__":
    main()
