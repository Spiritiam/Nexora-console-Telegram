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


def fetch_exness_5m(symbol, days):
    """60 days of 5-minute bid candles from the Exness market-data API (pages of 990 bars, newest to oldest)."""
    from datetime import timedelta
    from urllib.parse import urlencode
    import exness_api
    key, secret = os.getenv("EXNESS_API_KEY"), os.getenv("EXNESS_SECRET_KEY")
    acct = os.getenv("EXNESS_ACCOUNT_ID", "10000776165")
    if not key or not secret:
        raise RuntimeError("Exness credentials not set")
    cl = exness_api.ExnessClient(os.getenv("EXNESS_GATEWAY_URL", "api.exness.com"), key, secret, acct)
    names = {str(n).upper(): str(n) for n in cl.instruments()}
    want = symbol.upper()
    pick = names.get(want) or next((n for u, n in names.items() if u.startswith(want) or want.startswith(u)), None)
    if not pick:
        raise RuntimeError(f"{symbol} not found in Exness instruments")
    log(f"{symbol}: using Exness instrument {pick}")
    rows = {}
    target = time.time() - days * 86400
    end = datetime.now(timezone.utc) + timedelta(seconds=BAR)
    fails = pages = 0
    while end.timestamp() > target and pages < 60:
        start = end - timedelta(seconds=990 * BAR)
        q = urlencode({"instrument": pick, "price_type": "bid", "timeframe": "M5",
                       "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        try:
            code, d = cl._call("GET", f"/v1/market-data/accounts/{cl.account_id}/candles?{q}")
        except Exception as e:
            code, d = -1, str(e)
        if code != 200:
            fails += 1
            log(f"{symbol} Exness HTTP {code} {str(d)[:100]} (retry {fails})")
            if fails > 8:
                break
            time.sleep(min(20, 2 * fails))
            continue
        fails = 0
        got = d.get("candles") or []
        if not got:
            break
        for r in got:
            t = int(_ts(r["time"]))
            rows.setdefault(t, [t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), 0.0])
        pages += 1
        end = start
        time.sleep(0.4)
    return [rows[k] for k in sorted(rows)]


def fetch_5m(symbol, days):
    try:
        rows = fetch_exness_5m(symbol, days)
        if len(rows) >= 3000:
            return rows
        log(f"{symbol}: Exness gave only {len(rows)} bars, trying MetaAPI")
    except Exception as e:
        log(f"{symbol}: Exness feed failed: {type(e).__name__}: {e}")
    return fetch_5m_metaapi(symbol, days)


def fetch_5m_metaapi(symbol, days):
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


# Fixed stop / take-profit version (HOUR_RR=1): 20-pip stop with a 20-pip target (1 to 1) and a 40-pip target (1 to 2).
# Pips are the bot's own: gold 0.1, oil 0.01, BTC 9.918 price units. No trailing, no break-even.
# Same spread and slippage cost per trade as the first study (0.1 of the old Aggressive Scalper stop, in price units).
RR_STOP = {"XAUUSDm": 20 * 0.1, "USOILm": 20 * 0.01, "BTCUSDm": 20 * 9.918}
RR_COST = {"XAUUSDm": 0.5, "USOILm": 0.025, "BTCUSDm": 0.1 * 25 * 9.918}


def run_fixed(o, h, l, c, i, d, stop_d, tp_d, n, cost):
    """Enter at the open of bar i. Stop checked before target inside a bar (pessimistic). Closed at market after 1 hour.
    Returns (result in stops after cost, 'tp' | 'sl' | 'time')."""
    e = o[i]
    sl = e - d * stop_d
    tp = e + d * tp_d
    end = min(i + HORIZON, n)
    for j in range(i, end):
        if d > 0:
            if o[j] <= sl: return (o[j] - e) / stop_d - cost / stop_d, "sl"
            if l[j] <= sl: return -1.0 - cost / stop_d, "sl"
            if h[j] >= tp: return tp_d / stop_d - cost / stop_d, "tp"
        else:
            if o[j] >= sl: return (e - o[j]) / stop_d - cost / stop_d, "sl"
            if h[j] >= sl: return -1.0 - cost / stop_d, "sl"
            if l[j] <= tp: return tp_d / stop_d - cost / stop_d, "tp"
    return (c[end - 1] - e) * d / stop_d - cost / stop_d, "time"


def rr_study():
    log(f"RR study start: {list(RR_STOP)}, {DAYS} days, 20-pip stop, target 20 pips (1 to 1) and 40 pips (1 to 2)")
    for sym, stop_d in RR_STOP.items():
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
        cost = RR_COST[sym]
        log(f"{sym}: {n} bars over {days:.1f} days; stop {stop_d:g} price units, spread+slippage per trade {cost:g}")
        for mode, label in (("A", "first candle of the hour"), ("B", "last candle of the hour"),
                            ("C", "1-hour candle just closed")):
            for strong in (False, True):
                sigs = signals(t, o, h, l, c, mode, strong)
                if len(sigs) < 30:
                    continue
                for side, sgn in (("follow", 1), ("fade", -1)):
                    for rr, tp_d in (("1:1", stop_d), ("1:2", 2 * stop_d)):
                        res = [run_fixed(o, h, l, c, k, d * sgn, stop_d, tp_d, n, cost) for k, d in sigs]
                        r = np.array([x[0] for x in res])
                        tp = sum(1 for x in res if x[1] == "tp") / len(res) * 100
                        sl = sum(1 for x in res if x[1] == "sl") / len(res) * 100
                        cut = int(len(r) * 0.7)
                        log(f"{sym} | {label} | {'strong' if strong else 'any'} | {side} | {rr}: trades={len(r)} "
                            f"({len(r) / days:.1f}/day) target hit={tp:.0f}% stop hit={sl:.0f}% timed out={100 - tp - sl:.0f}% "
                            f"net={r.mean() * 100:+.0f}% of stop per trade (before spread {(r.mean() + cost / stop_d) * 100:+.0f}%) | first70% {r[:cut].mean() * 100:+.0f}% "
                            f"last30% {r[cut:].mean() * 100:+.0f}%")
    log("RR done")


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
    rr_study() if os.getenv("HOUR_RR") == "1" else main()
