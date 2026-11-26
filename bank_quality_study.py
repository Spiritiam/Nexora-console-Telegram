"""
Read-only study: does waiting for the pullback to stop make the price-action bank's signals better?

Never places a trade. Started by the bot only while QUALITY_STUDY=1. Prints "[BQS] ..." lines.

The bank as it runs for scheduled signals: 1-hour chart for direction, strategies on the last CLOSED 15-minute candle, 2+ strategies
from 2+ families must agree (one more against the 1-hour direction). Checked every 15 minutes while no trade is open.
Trade = the real signal: entry at the next 1-minute open, fixed stop, TP1 (half closes, stop to break-even), TP2 for the rest,
24 hour limit, spread charged. Markets and levels as the signals use them (stop / TP1 / TP2 in price units):
  gold 10 / 15 / 30, oil 0.5 / 0.75 / 1.5, BTC 496 / 496 / 992, silver 0.5 / 0.75 / 1.5.
Entry rules compared (all on the same bank signals):
  today          take the bank signal as it is
  candle agrees  the last closed 15-minute candle closed in the signal direction (buy: green)
  pullback over  the last closed candle did not make a new low (buy) / new high (sell) against the signal
  both           candle agrees + pullback over
  trend of 3     the close of the last 15-minute candle is not against the signal compared with 3 candles ago
  strong A       pullback over + the 1-hour direction agrees with the signal
  strong B       pullback over + candle agrees + 1-hour direction agrees
  strong C       pullback over + price has not fallen (buy) / risen (sell) more than 1.5 ranges from the last 8 candles' extreme
  strong D       pullback over + candle agrees + the spike rule of C
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst_all as ba  # noqa: E402
import price_action_bank as pab  # noqa: E402
import bank_scalper_study as bss  # noqa: E402
import bank_flip_study as bfs  # noqa: E402

DAYS = int(os.getenv("BQS_DAYS", "60"))
SIG_WIN, BIAS_WIN, LOOK, HORIZON = 300, 100, 32, 1440
SYMS = {"XAUUSDm": dict(stop=10.0, tp1=15.0, tp2=30.0, cost=0.30),
        "USOILm": dict(stop=0.5, tp1=0.75, tp2=1.5, cost=0.03),
        "BTCUSDm": dict(stop=496.0, tp1=496.0, tp2=992.0, cost=25.0),
        "XAGUSDm": dict(stop=0.5, tp1=0.75, tp2=1.5, cost=0.04)}


def log(msg):
    print(f"[BQS] {msg}", flush=True)


def run_trades(t, o, h, l, c, cfg, decide):
    n = len(c)
    S, T1, T2, cost = cfg["stop"], cfg["tp1"], cfg["tp2"], cfg["cost"]
    out = []
    i = 40
    while i < n - 2:
        d = decide(i)
        if not d:
            i += 1
            continue
        e = o[i + 1]
        stop, stage = e - d * S, 0
        res = None
        j = i + 1
        end = min(i + 1 + HORIZON, n)
        while j < end:
            lo_hit = (l[j] <= stop) if d > 0 else (h[j] >= stop)
            if lo_hit:
                res = ("SL" if stage == 0 else "BE", stop)
                break
            if stage == 0 and ((h[j] - e) * d if d > 0 else (e - l[j])) >= T1:
                stage, stop = 1, e
            if stage == 1 and (((h[j] - e) >= T2) if d > 0 else ((e - l[j]) >= T2)):
                res = ("TP2", e + d * T2)
                break
            j += 1
        if res is None:
            px = c[min(j, n - 1) - 1]
            res = ("TIME", px)
        kind, px = res
        if stage == 0:
            r = (px - e) * d - cost
        else:
            r = 0.5 * T1 + 0.5 * ((px - e) * d) - cost
        out.append((t[i], kind, stage, r / S))
        i = max(j, i + 1) + 1
    return out


def summ(tr, days):
    if not tr:
        return None
    n = len(tr)
    tp1 = sum(1 for x in tr if x[2] == 1) / n * 100
    tp2 = sum(1 for x in tr if x[1] == "TP2") / n * 100
    sl = sum(1 for x in tr if x[1] == "SL") / n * 100
    avg = float(np.mean([x[3] for x in tr])) * 100
    return n, n / days, tp1, tp2, sl, avg


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {list(SYMS)}, {DAYS} days")
    for sym, cfg in SYMS.items():
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
        m15, h1, d1 = bfs.agg(a, 900), bfs.agg(a, 3600), bfs.agg(a, 86400)
        sig_end = np.array([x["time"] + 900 for x in m15])
        bias_end = np.array([x["time"] + 3600 for x in h1])
        d1_start = np.array([x["time"] for x in d1])
        cache, t0, cnt = {}, time.time(), [0]

        def scan(i):
            now = float(t[i]) + 60.0
            if now % 900 != 0:
                return None
            if i in cache:
                return cache[i]
            ks = int(np.searchsorted(sig_end, now, side="right"))
            kb = int(np.searchsorted(bias_end, now, side="right"))
            kd = int(np.searchsorted(d1_start, (now // 86400) * 86400, side="left"))
            sig = m15[max(0, ks - SIG_WIN): ks]
            bias = h1[max(0, kb - BIAS_WIN): kb]
            X, ideas, n_sig, why = pab.scan_pa(bias, sig, sig, d1[max(0, kd - 45): kd], now,
                                               sig_s=900, bias_s=3600, c5_s=900, min_sig=120, min_bias=40)
            if X is None:
                cache[i] = None
            else:
                x = bss._X()
                x.bias, x.px, x.a = X.bias, X.px, X.a
                look = n_sig[-LOOK:]
                cache[i] = (x, ideas, [{"low": min(q["low"] for q in look), "high": max(q["high"] for q in look)}], n_sig[-8:])
            cnt[0] += 1
            if cnt[0] % 1000 == 0:
                log(f"{sym}: {cnt[0]} charts checked, {time.time() - t0:.0f}s")
            return cache[i]

        def mk(rule):
            def decide(i):
                s = scan(i)
                if s is None:
                    return 0
                x, ideas, tail, last = s
                b = pab.pick_pa(x, ideas, tail, min_agree=2, look=1, why_tf="1h")["best"]
                if not b:
                    return 0
                d = 1 if b["direction"] == "BUY" else -1
                if len(last) < 8:
                    return 0
                q, p = last[-1], last[-2]
                green_ok = (q["close"] > q["open"]) if d > 0 else (q["close"] < q["open"])
                over_ok = (q["low"] >= p["low"]) if d > 0 else (q["high"] <= p["high"])
                trend_ok = (q["close"] >= last[-4]["close"]) if d > 0 else (q["close"] <= last[-4]["close"])
                atr = x.a or 0
                hi8 = max(z["high"] for z in last) if last else 0
                spike_ok = True
                if atr > 0:
                    spike_ok = ((hi8 - q["close"]) <= 1.5 * atr) if d > 0 else ((q["close"] - min(z["low"] for z in last)) <= 1.5 * atr)
                bias_ok = (x.bias == "up") if d > 0 else (x.bias == "down")
                ok = {"strong A": over_ok and bias_ok, "strong B": over_ok and green_ok and bias_ok,
                      "strong C": over_ok and spike_ok, "strong D": over_ok and green_ok and spike_ok, "today": True, "candle agrees": green_ok, "pullback over": over_ok,
                      "both": green_ok and over_ok, "trend of 3": trend_ok}[rule]
                return d if ok else 0
            return decide

        log(f"{sym}: {len(rows)} candles, {days:.0f} trading days")
        for rule in ("today", "pullback over", "strong A", "strong B", "strong C", "strong D"):
            tr = run_trades(t, o, h, l, c, cfg, mk(rule))
            s = summ(tr, days)
            if not s:
                log(f"{sym} | {rule}: no trades")
                continue
            cut = len(tr) // 2
            a1, a2 = summ(tr[:cut], days), summ(tr[cut:], days)
            log(f"{sym} | {rule}: trades={s[0]} ({s[1]:.1f}/day) TP1={s[2]:.0f}% TP2={s[3]:.0f}% stopped={s[4]:.0f}% "
                f"avg={s[5]:+.0f}%ofstop | older {(a1[5] if a1 else 0):+.0f}% newer {(a2[5] if a2 else 0):+.0f}%")
    log("done")


if __name__ == "__main__":
    main()
