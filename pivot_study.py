"""
Read-only study: the "pivot / support-resistance" signal on 15-minute candles. Never places a trade.
Started by the bot only while PIVOT_STUDY=1. Prints "[PIVOT] ..." lines.

Rule (mirrors the research-card style): trend from MA20 / MA50 on the 15-minute chart.
  Down trend (close < MA50 and MA20 < MA50): SELL while price is below the pivot = nearest recent swing high above price.
  Up trend: BUY while price is above the pivot = nearest recent swing low below price.
  Stop = the pivot (plus 0.1 ATR). Target 1 / 2 = the next support (or resistance) levels, at least 1.0x / 1.8x the stop away.
Trade: entry at the next candle's open, half closes at target 1 and the stop moves to entry, the rest runs to target 2.
Closed at market after 96 candles (24 h). 0.1 of a stop is charged per trade for spread and slippage. One trade at a time per market.
Variants: "any time" (every closed candle that qualifies) and "pullback" (price within 1 ATR of the pivot).
"""
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SYMBOLS = [s.strip() for s in os.getenv("PIVOT_SYMBOLS", "XAUUSDm,USOILm,BTCUSDm,XAGUSDm,EURUSDm,GBPUSDm,USDJPYm,AUDUSDm").split(",") if s.strip()]
DAYS = int(os.getenv("PIVOT_DAYS", "90"))
BAR = 900
HORIZON = 96
COST = 0.10
K = 3          # swing = highest/lowest of 3 candles each side
LEVEL_BARS = 160


def log(msg):
    print(f"[PIVOT] {msg}", flush=True)


def _ts(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


def fetch(symbol, days):
    import exness_api
    key, secret = os.getenv("EXNESS_API_KEY"), os.getenv("EXNESS_SECRET_KEY")
    acct = os.getenv("EXNESS_ACCOUNT_ID", "10000776165")
    cl = None
    for attempt in range(6):
        try:
            cl = exness_api.ExnessClient(os.getenv("EXNESS_GATEWAY_URL", "api.exness.com"), key, secret, acct)
            names = {str(n).upper(): str(n) for n in cl.instruments()}
            break
        except Exception as e:
            log(f"Exness client setup failed ({type(e).__name__}), retry {attempt + 1}")
            time.sleep(10 * (attempt + 1))
    if cl is None:
        raise RuntimeError("Exness client setup failed")
    want = symbol.upper()
    pick = names.get(want) or next((n for u, n in names.items() if u.startswith(want) or want.startswith(u)), None)
    if not pick:
        raise RuntimeError(f"{symbol} not found")
    rows = {}
    target = time.time() - days * 86400
    end = datetime.now(timezone.utc) + timedelta(seconds=BAR)
    fails = empties = pages = 0
    while end.timestamp() > target and pages < 80:
        start = end - timedelta(seconds=990 * BAR)
        q = urlencode({"instrument": pick, "price_type": "bid", "timeframe": "M15",
                       "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        try:
            code, d = cl._call("GET", f"/v1/market-data/accounts/{cl.account_id}/candles?{q}")
        except Exception as e:
            code, d = -1, str(e)
        if code != 200:
            fails += 1
            log(f"{symbol} Exness HTTP {code} {str(d)[:80]} (retry {fails})")
            if fails > 10:
                break
            time.sleep(min(20, 2 * fails))
            continue
        fails = 0
        pages += 1
        got = d.get("candles") or []
        if not got:
            empties += 1
            if empties > 4:
                break
        else:
            empties = 0
            for r in got:
                t = int(_ts(r["time"]))
                rows.setdefault(t, (t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
        end = start
        time.sleep(0.6)
    out = [rows[k] for k in sorted(rows)]
    log(f"{symbol}: {len(out)} fifteen-minute candles")
    return out


def sma(v, n):
    out = [None] * len(v)
    s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr_series(c, n=14):
    out = [None] * len(c)
    a = None
    for i in range(1, len(c)):
        tr = max(c[i][2] - c[i][3], abs(c[i][2] - c[i - 1][4]), abs(c[i][3] - c[i - 1][4]))
        if i < n:
            a = tr if a is None else a + tr
            if i == n - 1:
                a /= n
        elif a is not None:
            a = (a * (n - 1) + tr) / n
        if i >= n - 1:
            out[i] = a
    return out


def run(c, pullback):
    closes = [x[4] for x in c]
    m20, m50, at = sma(closes, 20), sma(closes, 50), atr_series(c)
    n = len(c)
    res = []   # (kind, result in stops)  kind: tp2 / tp1be / stop / timeout
    i = 60
    while i < n - 2:
        if m50[i] is None or at[i] is None:
            i += 1
            continue
        px, a = closes[i], at[i]
        if px < m50[i] and m20[i] < m50[i]:
            d = -1
        elif px > m50[i] and m20[i] > m50[i]:
            d = 1
        else:
            i += 1
            continue
        lo = max(K, i - LEVEL_BARS)
        highs, lows = [], []
        for j in range(lo, i - K + 1):
            if all(c[j][2] >= c[j + q][2] for q in range(-K, K + 1) if q and 0 <= j + q < n):
                highs.append(c[j][2])
            if all(c[j][3] <= c[j + q][3] for q in range(-K, K + 1) if q and 0 <= j + q < n):
                lows.append(c[j][3])
        if d < 0:
            above = sorted(x for x in highs if x > px + 0.3 * a)
            if not above:
                i += 1
                continue
            pivot = above[0]
            sd = pivot + 0.1 * a - px
            below = sorted((x for x in lows if x < px), reverse=True)
        else:
            underneath = sorted((x for x in lows if x < px - 0.3 * a), reverse=True)
            if not underneath:
                i += 1
                continue
            pivot = underneath[0]
            sd = px - (pivot - 0.1 * a)
            below = sorted(x for x in highs if x > px)
        if not (0.5 * a <= sd <= 2.5 * a):
            i += 1
            continue
        if pullback and abs(pivot - px) > 1.0 * a:
            i += 1
            continue
        t1 = next((x for x in below if abs(px - x) >= 1.0 * sd), None)
        t2 = next((x for x in below if t1 is not None and abs(px - x) >= 1.8 * sd and abs(x - t1) > 0.2 * sd), None)
        if t1 is None or t2 is None:
            i += 1
            continue
        e = c[i + 1][1]
        stop = e + (-d) * sd * -1 if False else (e - d * sd)
        r1, r2 = abs(e - t1) / sd, abs(e - t2) / sd
        hit1 = False
        out = None
        j = i + 1
        end = min(i + 1 + HORIZON, n)
        while j < end:
            o, h, l, cl = c[j][1], c[j][2], c[j][3], c[j][4]
            if d > 0:
                if not hit1:
                    if l <= stop:
                        out = ("stop", -1.0); break
                    if h >= t1:
                        hit1 = True; stop = e
                else:
                    if l <= stop:
                        out = ("tp1be", 0.5 * r1); break
                if hit1 and h >= t2:
                    out = ("tp2", 0.5 * r1 + 0.5 * r2); break
            else:
                if not hit1:
                    if h >= stop:
                        out = ("stop", -1.0); break
                    if l <= t1:
                        hit1 = True; stop = e
                else:
                    if h >= stop:
                        out = ("tp1be", 0.5 * r1); break
                if hit1 and l <= t2:
                    out = ("tp2", 0.5 * r1 + 0.5 * r2); break
            j += 1
        if out is None:
            last = c[min(end, n) - 1][4]
            mv = (last - e) * d / sd
            out = ("timeout", (0.5 * r1 + 0.5 * mv) if hit1 else mv)
            j = end - 1
        res.append((out[0], out[1] - COST))
        i = j + 1
    return res


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {SYMBOLS}, {DAYS} days of 15-minute candles")
    for sym in SYMBOLS:
        try:
            c = fetch(sym, DAYS)
        except Exception as e:
            log(f"{sym}: failed {type(e).__name__}: {e}")
            continue
        if len(c) < 300:
            log(f"{sym}: not enough candles ({len(c)})")
            continue
        days = max(1.0, (c[-1][0] - c[0][0]) / 86400)
        for name, pb in (("any time", False), ("pullback to pivot", True)):
            r = run(c, pb)
            if not r:
                log(f"{sym} | {name} | no trades")
                continue
            nn = len(r)
            win1 = sum(1 for k, _ in r if k in ("tp1be", "tp2")) / nn * 100
            win2 = sum(1 for k, _ in r if k == "tp2") / nn * 100
            stp = sum(1 for k, _ in r if k == "stop") / nn * 100
            net = sum(x for _, x in r) / nn * 100
            log(f"{sym} | {name} | trades {nn} ({nn / days:.1f}/day) | reached T1 {win1:.0f}% | reached T2 {win2:.0f}% | stopped {stp:.0f}% | per trade {net:+.0f}% of the stop")
    log("done")


if __name__ == "__main__":
    main()
