"""
Read-only study of the "hour candle" idea: on a 5-minute chart, take the candle at each 1-hour mark; the claim
is that the next candle most likely has the same colour. Never places a trade, never touches the live path.
Runs as its own low-priority process (started by the bot only while HOUR_RESEARCH=1 is set). Prints "[HOUR] ..." lines.

Readings of "the candle at the hour" that are tested (UTC hour marks):
  A  first 5-minute candle of the hour (opens HH:00)  -> trade the next candle (opens HH:05)
  B  last 5-minute candle of the hour (opens HH:55)   -> trade the next candle (opens HH:00)
  C  the 1-hour candle that just closed               -> trade the next 5-minute candle (opens HH:00)
Each is tested with any candle colour, and with a strong candle only (body at least 60% of its range),
and both with the colour (follow) and against it (fade).

Two numbers per row:
  same-colour = how often the NEXT 5-minute candle closes in the direction of the signal candle (a coin flip is 50%)
  trade       = a real trade entered at the next candle's open, with the Aggressive Scalper stop (gold 50 pips, oil 25,
                BTC 25), break-even after a full stop in profit then a trail of half a stop, closed at market after
                1 hour, 0.1 of a stop charged per trade for spread and slippage.
"""
import os
import sys

import time
from datetime import datetime, timezone

import numpy as np
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_research import fetch_h1 as fetch_1m  # noqa: E402  (the 1-minute downloader the burst study used)

SYMBOLS = {"XAUUSDm": 5.0, "USOILm": 0.25, "BTCUSDm": 25 * 9.918}
DAYS = int(os.getenv("HOUR_DAYS", "60"))
HORIZON = 12  # 5-minute bars = 1 hour
COST_R = 0.10
BAR = 300


def log(msg):
    print(f"[HOUR] {msg}", flush=True)


HOSTS = [f"https://mt-market-data-client-api-v1.{r}.agiliumtrade.ai" for r in ("new-york", "london", "singapore")]


def _ts(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


def fetch_h1(symbol, days, tf="5m"):
    """5-minute candles from the MetaAPI market-data API. Smaller pages and three regions, because the large
    1000-bar request returns HTTP 504 for 5-minute data."""
    token, acct = os.getenv("METAAPI_TOKEN"), os.getenv("METAAPI_ACCOUNT_ID")
    if not token or not acct:
        raise RuntimeError("MetaAPI credentials not set")
    headers = {"auth-token": token, "Accept": "application/json"}
    rows = {}
    target = time.time() - days * 86400
    cur = time.time()
    limit, hi, fails, pages = 300, 0, 0, 0
    while cur > target and pages < 600:
        start = datetime.fromtimestamp(cur, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        url = (f"{HOSTS[hi % len(HOSTS)]}/users/current/accounts/{acct}/historical-market-data/symbols/{symbol}"
               f"/timeframes/{tf}/candles?startTime={start}&limit={limit}")
        try:
            r = requests.get(url, headers=headers, timeout=90)
            code = r.status_code
        except Exception as e:
            code = -1
            log(f"{symbol} request error {type(e).__name__}")
        if code != 200:
            fails += 1
            hi += 1
            if code in (504, 502, 503, 429) and limit > 100 and fails % 3 == 0:
                limit = max(100, limit // 2)
            log(f"{symbol} HTTP {code}, retry {fails} (limit {limit})")
            if fails > 12:
                break
            time.sleep(min(30, 3 * fails))
            continue
        fails = 0
        raw = r.json()
        if not raw:
            break
        for c in raw:
            t = int(_ts(c["time"]))
            rows.setdefault(t, [t, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), 0.0])
        oldest = min(int(_ts(c["time"])) for c in raw)
        if oldest >= cur:
            break
        cur = oldest - 1
        pages += 1
        time.sleep(0.5)
    return [rows[k] for k in sorted(rows)]


def fetch_5m(symbol, days):
    """5-minute candles built from 1-minute ones (the 5-minute download itself returned HTTP 504)."""
    rows = fetch_1m(symbol, days, "1m")
    buckets = {}
    for r in rows:
        k = int(r[0]) // 300 * 300
        b = buckets.get(k)
        if b is None:
            buckets[k] = [k, r[1], r[2], r[3], r[4], 1]
        else:
            b[2] = max(b[2], r[2]); b[3] = min(b[3], r[3]); b[4] = r[4]; b[5] += 1
    return [b[:5] + [0.0] for k, b in sorted(buckets.items()) if b[5] >= 4]


def run_trade(o, h, l, c, i, d, stop_d, n):
    """Enter at the open of bar i in direction d."""
    e = o[i]
    stop = e - d * stop_d
    best = e
    exit_p = None
    j = i
    end = min(i + HORIZON, n)
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
        exit_p = c[min(j, n) - 1]
    return (exit_p - e) * d / stop_d - COST_R


def signals(t, o, h, l, c, mode, strong):
    """Return list of (bar index to enter, direction of the signal candle)."""
    n = len(t)
    idx = {int(x): k for k, x in enumerate(t)}
    out = []
    for k in range(n):
        ts = int(t[k])
        if mode == "A" and ts % 3600 == 0:
            sig = k
        elif mode == "B" and ts % 3600 == 3300:
            sig = k
        elif mode == "C" and ts % 3600 == 0:
            # the hour that just closed: 12 five-minute bars from ts-3600
            a = idx.get(ts - 3600)
            if a is None or k - a != 12:
                continue
            ho, hc = o[a], c[k - 1]
            hh, hl = h[a:k].max(), l[a:k].min()
            rng = hh - hl
            if hc == ho or (strong and (rng <= 0 or abs(hc - ho) < 0.6 * rng)):
                continue
            nxt = k
            out.append((nxt, 1 if hc > ho else -1))
            continue
        else:
            continue
        nxt = sig + 1
        if nxt >= n or int(t[nxt]) != int(t[sig]) + BAR:
            continue
        rng = h[sig] - l[sig]
        if c[sig] == o[sig]:
            continue
        if strong and (rng <= 0 or abs(c[sig] - o[sig]) < 0.6 * rng):
            continue
        out.append((nxt, 1 if c[sig] > o[sig] else -1))
    return out


def summarize(rows):
    r = np.array(rows, dtype=float)
    return len(r), float((r > 0).mean() * 100), float(r.mean() * 100)


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {list(SYMBOLS)}, {DAYS} days of 5-minute bars")
    for sym, stop_d in SYMBOLS.items():
        try:
            rows = fetch_5m(sym, DAYS)
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} bars, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4]
        n = len(t)
        days = (t[-1] - t[0]) / 86400.0
        # baseline: how often any 5-minute candle has the same colour as the one before it
        col = np.sign(c - o)
        m = (col[:-1] != 0) & (col[1:] != 0)
        base = float((col[:-1][m] == col[1:][m]).mean() * 100)
        log(f"{sym}: {n} bars over {days:.1f} days; baseline next-candle same colour (every candle) = {base:.1f}%")
        for mode, label in (("A", "first candle of the hour"), ("B", "last candle of the hour"),
                            ("C", "1-hour candle just closed")):
            for strong in (False, True):
                sigs = signals(t, o, h, l, c, mode, strong)
                if len(sigs) < 30:
                    log(f"{sym} | {label} | {'strong' if strong else 'any'}: too few signals ({len(sigs)})")
                    continue
                same = float(np.mean([1.0 if np.sign(c[k] - o[k]) == d else 0.0 for k, d in sigs]) * 100)
                for side, sgn in (("follow", 1), ("fade", -1)):
                    tr = [run_trade(o, h, l, c, k, d * sgn, stop_d, n) for k, d in sigs]
                    cut = int(len(tr) * 0.7)
                    s = summarize(tr)
                    s1 = summarize(tr[:cut])
                    s2 = summarize(tr[cut:])
                    log(f"{sym} | {label} | {'strong' if strong else 'any'} | {side}: signals={s[0]} ({s[0] / days:.1f}/day) "
                        f"same-colour={same:.0f}% win={s[1]:.0f}% net={s[2]:+.0f}% of stop per trade | "
                        f"first70% net={s1[2]:+.0f}% last30% net={s2[2]:+.0f}%")
    log("done")


if __name__ == "__main__":
    main()
