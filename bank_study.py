"""
Read-only study: a BANK of chart-card style strategies on 15- and 30-minute candles. Never places a trade.
Started by the bot only while BANK_STUDY=1. Prints "[BANK] ..." lines.

Strategies (indicators: MA20 + Bollinger Bands(20,2), MA50, RSI14, MACD 12/26/9, swing highs/lows as levels):
  trend      price below (above) MA20 and MA50, RSI under (over) 50, MACD under (over) its signal; stop = nearest swing level against
  pullback   same trend, price pulled back to the MA20 and turns back with the trend; stop = the pullback extreme
  range      MA20 and MA50 flat, price at a swing support (resistance) with RSI under 40 (over 60): bounce
  breakout   close breaks the 20-candle high (low) with RSI and MACD agreeing; stop 1 ATR behind
  bbtrend    close outside the Bollinger Band in the trend direction, RSI under 35 (over 65), trend down (up): continue
  bbrevert   close outside the Bollinger Band, RSI under 30 (over 70): come back to the middle band
Targets 1 and 2 = the next swing levels at least 1.0x / 1.8x the stop away (else exactly that distance).
Trade: entry at next open, half closes at target 1 and the stop moves to entry, rest runs to target 2; closed at market after
the time limit (24 h); 0.1 of a stop charged per trade. One trade at a time per market and strategy.
Each result line shows the first two thirds of the data ("train") and the last third ("test") separately.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pivot_study as ps  # noqa: E402

SYMBOLS = [s.strip() for s in os.getenv("BANK_SYMBOLS", "XAUUSDm,USOILm,BTCUSDm,XAGUSDm,EURUSDm,GBPUSDm,USDJPYm,AUDUSDm,USDCADm,USDCHFm").split(",") if s.strip()]
K = 3
LEVEL_BARS = 160


def log(msg):
    print(f"[BANK] {msg}", flush=True)


def to_30m(c):
    out, cur = [], None
    for t, o, h, l, cl in c:
        b = t // 1800 * 1800
        if cur is None or cur[0] != b:
            if cur is not None:
                out.append(tuple(cur))
            cur = [b, o, h, l, cl]
        else:
            cur[2] = max(cur[2], h)
            cur[3] = min(cur[3], l)
            cur[4] = cl
    if cur is not None:
        out.append(tuple(cur))
    return out[:-1]


def ema(v, n):
    k = 2.0 / (n + 1)
    out, p = [], v[0]
    for x in v:
        p = x * k + p * (1 - k)
        out.append(p)
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    g = l = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        up, dn = max(d, 0), max(-d, 0)
        if i <= n:
            g += up / n
            l += dn / n
        else:
            g = (g * (n - 1) + up) / n
            l = (l * (n - 1) + dn) / n
        if i >= n:
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def prep(c):
    cl = [x[4] for x in c]
    m20, m50 = ps.sma(cl, 20), ps.sma(cl, 50)
    sd = [None] * len(cl)
    for i in range(19, len(cl)):
        w = cl[i - 19:i + 1]
        mu = m20[i]
        sd[i] = (sum((x - mu) ** 2 for x in w) / 20) ** 0.5
    e12, e26 = ema(cl, 12), ema(cl, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema(macd, 9)
    return {"cl": cl, "m20": m20, "m50": m50, "sd": sd, "macd": macd, "sig": sig, "rsi": rsi(cl), "atr": ps.atr_series(c)}


def swings(c, i):
    lo = max(K, i - LEVEL_BARS)
    highs, lows = [], []
    n = len(c)
    for j in range(lo, i - K + 1):
        if all(c[j][2] >= c[j + q][2] for q in range(-K, K + 1) if q and 0 <= j + q < n):
            highs.append(c[j][2])
        if all(c[j][3] <= c[j + q][3] for q in range(-K, K + 1) if q and 0 <= j + q < n):
            lows.append(c[j][3])
    return highs, lows


def targets(px, d, sd, highs, lows):
    pool = sorted((x for x in lows if x < px), reverse=True) if d < 0 else sorted(x for x in highs if x > px)
    t1 = next((x for x in pool if abs(px - x) >= 1.0 * sd), px - d * 1.0 * sd)
    t2 = next((x for x in pool if abs(px - x) >= 1.8 * sd and abs(x - t1) > 0.2 * sd), px - d * 1.8 * sd)
    return t1, t2


def signal(name, c, P, i, highs, lows):
    px, a = P["cl"][i], P["atr"][i]
    m20, m50, s, r, mc, sg = P["m20"][i], P["m50"][i], P["sd"][i], P["rsi"][i], P["macd"][i], P["sig"][i]
    if None in (a, m20, m50, s, r):
        return None
    dn = px < m20 and px < m50 and m20 < m50 and r < 50 and mc < sg
    up = px > m20 and px > m50 and m20 > m50 and r > 50 and mc > sg
    d = sl = None
    if name == "trend":
        if dn:
            above = sorted(x for x in highs if x > px + 0.3 * a)
            if above:
                d, sl = -1, above[0] + 0.1 * a
        elif up:
            under = sorted((x for x in lows if x < px - 0.3 * a), reverse=True)
            if under:
                d, sl = 1, under[0] - 0.1 * a
    elif name == "pullback":
        w = c[i - 5:i + 1]
        if dn and max(x[2] for x in w) >= m20 - 0.2 * a and c[i][4] < c[i][1]:
            d, sl = -1, max(x[2] for x in w) + 0.1 * a
        elif up and min(x[3] for x in w) <= m20 + 0.2 * a and c[i][4] > c[i][1]:
            d, sl = 1, min(x[3] for x in w) - 0.1 * a
    elif name == "range":
        if abs(m20 - m50) < 0.5 * a:
            sup = [x for x in lows if abs(px - x) < 0.5 * a]
            res = [x for x in highs if abs(px - x) < 0.5 * a]
            if sup and r < 40 and c[i][4] > c[i][1]:
                d, sl = 1, min(sup) - 0.3 * a
            elif res and r > 60 and c[i][4] < c[i][1]:
                d, sl = -1, max(res) + 0.3 * a
    elif name == "breakout":
        hh = max(x[2] for x in c[i - 20:i])
        ll = min(x[3] for x in c[i - 20:i])
        if px > hh and r > 55 and mc > sg:
            d, sl = 1, px - 1.0 * a
        elif px < ll and r < 45 and mc < sg:
            d, sl = -1, px + 1.0 * a
    elif name == "bbtrend":
        if px < m20 - 2 * s and r < 35 and m20 < m50:
            d, sl = -1, m20
        elif px > m20 + 2 * s and r > 65 and m20 > m50:
            d, sl = 1, m20
    elif name == "bbrevert":
        if px < m20 - 2 * s and r < 30:
            d, sl = 1, px - 0.8 * a
        elif px > m20 + 2 * s and r > 70:
            d, sl = -1, px + 0.8 * a
    if d is None:
        return None
    sdist = abs(px - sl)
    if not (0.4 * a <= sdist <= 2.5 * a):
        return None
    if name == "bbrevert":
        t1 = m20
        t2 = P["m50"][i] if (P["m50"][i] - px) * d > abs(t1 - px) else px + d * 1.8 * sdist
        if (t1 - px) * d < 0.5 * sdist:
            return None
    else:
        t1, t2 = targets(px, d, sdist, highs, lows)
    return d, sl, t1, t2


def run(c, P, name, lo, hi, horizon):
    n = len(c)
    res = []
    i = max(60, lo)
    while i < min(hi, n - 2):
        highs, lows = swings(c, i)
        sg = signal(name, c, P, i, highs, lows)
        if sg is None:
            i += 1
            continue
        d, sl, t1, t2 = sg
        e = c[i + 1][1]
        sdist = abs(P["cl"][i] - sl)
        if sdist <= 0:
            i += 1
            continue
        stop = e - d * sdist
        tp1 = e + d * abs(t1 - P["cl"][i])
        tp2 = e + d * abs(t2 - P["cl"][i])
        r1, r2 = abs(tp1 - e) / sdist, abs(tp2 - e) / sdist
        hit1, out = False, None
        j, end = i + 1, min(i + 1 + horizon, n)
        while j < end:
            h, l = c[j][2], c[j][3]
            if d > 0:
                if not hit1:
                    if l <= stop:
                        out = ("stop", -1.0); break
                    if h >= tp1:
                        hit1, stop = True, e
                elif l <= stop:
                    out = ("tp1be", 0.5 * r1); break
                if hit1 and h >= tp2:
                    out = ("tp2", 0.5 * r1 + 0.5 * r2); break
            else:
                if not hit1:
                    if h >= stop:
                        out = ("stop", -1.0); break
                    if l <= tp1:
                        hit1, stop = True, e
                elif h >= stop:
                    out = ("tp1be", 0.5 * r1); break
                if hit1 and l <= tp2:
                    out = ("tp2", 0.5 * r1 + 0.5 * r2); break
            j += 1
        if out is None:
            last = c[min(end, n) - 1][4]
            mv = (last - e) * d / sdist
            out = ("timeout", (0.5 * r1 + 0.5 * mv) if hit1 else mv)
            j = end - 1
        res.append((out[0], out[1] - ps.COST))
        i = j + 1
    return res


def summ(r):
    if not r:
        return "no trades"
    nn = len(r)
    w1 = sum(1 for k, _ in r if k in ("tp1be", "tp2")) / nn * 100
    st = sum(1 for k, _ in r if k == "stop") / nn * 100
    net = sum(x for _, x in r) / nn * 100
    return f"{nn} trades, T1 {w1:.0f}%, stopped {st:.0f}%, {net:+.0f}% of stop"


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {SYMBOLS}")
    for sym in SYMBOLS:
        try:
            base = ps.fetch(sym, int(os.getenv("BANK_DAYS", "90")))
        except Exception as e:
            log(f"{sym}: failed {type(e).__name__}: {e}")
            continue
        if len(base) < 600:
            log(f"{sym}: not enough candles")
            continue
        for tf, c, horizon in (("15m", base, 96), ("30m", to_30m(base), 48)):
            P = prep(c)
            cut = int(len(c) * 2 / 3)
            for name in ("trend", "pullback", "range", "breakout", "bbtrend", "bbrevert"):
                a = run(c, P, name, 0, cut, horizon)
                b = run(c, P, name, cut, len(c), horizon)
                log(f"{sym} {tf} {name} | train: {summ(a)} | test: {summ(b)}")
    log("done")


if __name__ == "__main__":
    main()
