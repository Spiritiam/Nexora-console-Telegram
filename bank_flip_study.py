"""
Read-only study: can the price-action strategy bank give Account Flip its entries (gold and silver)?

Never places a trade and never touches the live path. Started by the bot only while BANK_FLIP_STUDY=1 is set.
Prints "[BFS] ..." lines.

Bank as designed: the 1-hour chart gives the direction, the strategies run on the last CLOSED 15-minute candle, 2+ strategies
from 2+ families must agree (one more against the 1-hour direction). Checked every 15 minutes while no trade is open.

Trade rules (Account Flip's own management, first trade only, no extra layers): entry at the NEXT 1-minute candle's open;
stop 100 pips on gold (10.0 price units), 50 pips on silver (0.50 units); once price is 20 pips in
profit the stop moves to break-even and then follows 10 pips behind the best price; no take profit; a trade still open after
24 hours is closed at market. Spread + slippage is charged on every trade (gold 0.30, silver 0.04 price units). One trade at
a time, stop checked first inside a candle. Data: Exness 1-minute bid candles.

Compared with two simple benchmarks that use the SAME 15-minute checks and the SAME trade rules, so any edge is visible:
  random side (fixed seed) and "follow the 1-hour direction" (trade only when the 1-hour chart is clearly up or down).
The ML + simulation entry Account Flip uses today cannot be replayed here (it needs the live model), so it is not in the table.
"""
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst_all as ba  # noqa: E402
import price_action_bank as pab  # noqa: E402
import bank_scalper_study as bss  # noqa: E402

DAYS = int(os.getenv("BFS_DAYS", "60"))
SIG_WIN, BIAS_WIN = 300, 100
LOOK = 32          # "recent low / high" for the counter-trend rule = 8 hours of 15-minute candles
HORIZON = 1440     # 1-minute candles = 24 hours
# stop, break-even trigger, trail distance, cost, all in price units
SYMS = {"XAUUSDm": dict(stop=10.0, act=2.0, trail=1.0, cost=0.30),
        "XAGUSDm": dict(stop=0.50, act=0.20, trail=0.10, cost=0.04)}


def log(msg):
    print(f"[BFS] {msg}", flush=True)


def agg(a, sec):
    t = a[:, 0].astype(np.int64)
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


def run_trades(t, o, h, l, c, cfg, decide):
    n = len(c)
    stop_d, act, trail, cost = cfg["stop"], cfg["act"], cfg["trail"], cfg["cost"]
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
            if (best - e) * d >= act:
                new = max(e, best - trail) if d > 0 else min(e, best + trail)
                stop = max(stop, new) if d > 0 else min(stop, new)
            j += 1
        if exit_p is None:
            exit_p = c[min(j, n - 1) - 1] if j >= end else c[j]
        trades.append((t[i], ((exit_p - e) * d - cost) / stop_d, (exit_p - e) * d - cost))
        i = max(j, i + 1) + 1
    return trades


def stats(tr):
    if not tr:
        return None
    r = np.array([x[1] for x in tr])
    return len(r), float((r > 0).mean() * 100), float(r.mean())


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {list(SYMS)}, {DAYS} days of 1-minute candles")
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
        m15, h1, d1 = agg(a, 900), agg(a, 3600), agg(a, 86400)
        sig_end = np.array([x["time"] + 900 for x in m15])
        bias_end = np.array([x["time"] + 3600 for x in h1])
        d1_start = np.array([x["time"] for x in d1])
        log(f"{sym}: {len(rows)} candles over {days:.0f} trading days, stop {cfg['stop']:g} price units")
        cache = {}
        t0 = time.time()
        cnt = [0]

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
                cache[i] = (x, ideas, [{"low": min(q["low"] for q in look), "high": max(q["high"] for q in look)}])
            cnt[0] += 1
            if cnt[0] % 1000 == 0:
                log(f"{sym}: {cnt[0]} charts checked, {time.time() - t0:.0f}s")
            return cache[i]

        def bank(min_agree):
            def decide(i):
                s = scan(i)
                if s is None:
                    return 0
                x, ideas, tail = s
                b = pab.pick_pa(x, ideas, tail, min_agree=min_agree, look=1, why_tf="1h")["best"]
                return 0 if not b else (1 if b["direction"] == "BUY" else -1)
            return decide

        def trend(i):
            s = scan(i)
            if s is None:
                return 0
            return {"up": 1, "down": -1}.get(s[0].bias, 0)

        rnd = random.Random(7)

        def rand_side(i):
            s = scan(i)
            return 0 if s is None else rnd.choice((1, -1))

        for name, fn in (("bank, 2+ agree (2+ groups)", bank(2)), ("bank, 3+ agree (2+ groups)", bank(3)),
                         ("benchmark: random side", rand_side), ("benchmark: follow the 1-hour direction", trend)):
            tr = run_trades(t, o, h, l, c, cfg, fn)
            s = stats(tr)
            if not s:
                log(f"{sym} | {name}: no trades")
                continue
            cut = len(tr) // 2
            s1, s2 = stats(tr[:cut]), stats(tr[cut:])
            extra = ""
            if sym.upper().startswith("XAU"):
                usd = float(np.mean([x[2] for x in tr]))   # price units = $1 per unit on 0.01 lot of gold
                extra = f" = ${usd:+.2f} per trade on 0.01 lot"
            log(f"{sym} | {name}: trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% avg={s[2] * 100:+.0f}% of stop per trade{extra} | "
                f"older half {(s1[2] * 100 if s1 else 0):+.0f}% newer half {(s2[2] * 100 if s2 else 0):+.0f}%")
        log(f"{sym}: done in {time.time() - t0:.0f}s")
    log("done")


if __name__ == "__main__":
    main()
