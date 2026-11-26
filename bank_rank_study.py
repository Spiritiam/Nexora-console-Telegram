"""
Read-only study: which single strategy of the price-action bank has the highest win % on each market?

Never places a trade and never touches the live path. Started by the bot only while RANK_STUDY=1 is set. Prints "[RANK] ..." lines.

Each of the 42 strategies is run ALONE (no agreement needed) and trades every time it fires while no trade is open:
  scalper (Aggressive Scalper): strategies on the 1-minute chart, 5-minute chart for the direction. Gold, oil, Bitcoin.
    Trade rules as in bank_scalper_study.py (scalper stop, trailing from 0.75 of the stop, 60 candles, 0.1 of a stop cost).
  flip (Account Flip): strategies on the 15-minute chart, 1-hour chart for the direction. Gold, silver.
    Trade rules as in bank_flip_study.py (flip stop, break-even at +20 pips then trail 10, 24 hours, spread + slippage).
Win % = share of trades that finish above zero after costs. Each row also shows the win % of the older and newer half of the
60 days, so a strategy that only worked in one half is visible.
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

DAYS = int(os.getenv("RANK_DAYS", "60"))
ONLY = os.getenv("RANK_ONLY", "")   # "scalper" or "flip" to run just one set


def log(msg):
    print(f"[RANK] {msg}", flush=True)


def make_scan(t, sig_frames, bias_frames, d1, sig_s, bias_s, boundary):
    sig_end = np.array([x["time"] + sig_s for x in sig_frames])
    bias_end = np.array([x["time"] + bias_s for x in bias_frames])
    d1_start = np.array([x["time"] for x in d1])
    cache = {}

    def scan(i):
        now = float(t[i]) + 60.0
        if boundary and now % boundary != 0:
            return None
        if i in cache:
            return cache[i]
        ks = int(np.searchsorted(sig_end, now, side="right"))
        kb = int(np.searchsorted(bias_end, now, side="right"))
        kd = int(np.searchsorted(d1_start, (now // 86400) * 86400, side="left"))
        sig = sig_frames[max(0, ks - 300): ks]
        bias = bias_frames[max(0, kb - 100): kb]
        X, ideas, n_sig, why = pab.scan_pa(bias, sig, sig, d1[max(0, kd - 45): kd], now,
                                           sig_s=sig_s, bias_s=bias_s, c5_s=sig_s, min_sig=120, min_bias=40)
        cache[i] = None if X is None else {x["name"]: (1 if x["direction"] == "BUY" else -1) for x in ideas}
        return cache[i]
    return scan


def rank(sym, tag, t, run, scan, days, min_trades):
    t0 = time.time()
    n = len(t)
    for i in range(40, n - 2):      # fill the cache once; every strategy below reuses it
        scan(i)
    log(f"{tag} {sym}: all charts scanned in {time.time() - t0:.0f}s")
    out = []
    for name, label, fam, fn in pab.STRATEGIES:
        def decide(i, name=name):
            s = scan(i)
            return 0 if not s else s.get(name, 0)
        tr = run(decide)
        if len(tr) < min_trades:
            continue
        r = np.array([x[1] for x in tr])
        cut = len(r) // 2
        w1, w2 = float((r[:cut] > 0).mean() * 100), float((r[cut:] > 0).mean() * 100)
        out.append((float((r > 0).mean() * 100), name, label, len(r), float(r.mean()), w1, w2))
    out.sort(reverse=True)
    for win, name, label, cnt, avg, w1, w2 in out[:10]:
        log(f"{tag} {sym} | {name} ({label}): trades={cnt} ({cnt / days:.1f}/day) win={win:.0f}% avg={avg * 100:+.0f}% of stop | "
            f"older half win {w1:.0f}% newer half win {w2:.0f}%")
    log(f"{tag} {sym}: {len(out)} strategies with enough trades; done in {time.time() - t0:.0f}s")


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {DAYS} days of 1-minute candles")
    jobs = []
    if ONLY in ("", "scalper"):
        jobs += [("scalper", s, ba.SYMBOLS[s]) for s in ("XAUUSDm", "USOILm", "BTCUSDm")]
    if ONLY in ("", "flip"):
        jobs += [("flip", s, bfs.SYMS[s]) for s in ("XAUUSDm", "XAGUSDm")]
    cache_rows = {}
    for tag, sym, par in jobs:
        try:
            if sym not in cache_rows:
                cache_rows[sym] = ba.fetch(sym, DAYS)
            rows = cache_rows[sym]
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} candles, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        days = max(1.0, len(np.unique((t // 86400).astype(int))))
        m1, m5, d1, m15 = bss.build_frames(a)
        if tag == "scalper":
            scan = make_scan(t, m1, m5, d1, 60, 300, 0)
            rank(sym, tag, t, lambda dec: bss.run_trades(t, o, h, l, c, par, dec), scan, days, 150)
        else:
            h1 = bfs.agg(a, 3600)
            scan = make_scan(t, m15, h1, d1, 900, 3600, 900)
            rank(sym, tag, t, lambda dec: bfs.run_trades(t, o, h, l, c, par, dec), scan, days, 60)
    log("done")


if __name__ == "__main__":
    main()
