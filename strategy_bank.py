"""Strategy bank for scheduled and manual signals (15-minute candles).

Each strategy looks at the latest CLOSED candle and either returns a trade idea (direction, stop, target 1, target 2)
or nothing. evaluate() runs them all and picks the best idea. Indicators: MA20 + Bollinger Bands (20, 2), MA50, RSI 14,
MACD 12/26/9, swing highs and lows as support / resistance levels.

evaluate(candles) -> {"best": idea or None, "ideas": [idea, ...], "agree": n}
idea = {"name", "label", "direction": "BUY"|"SELL", "sl", "tp1", "tp2", "ref": close of the signal candle,
        "levels": [(price, "support"|"resistance")], "pivot": price}
"""
import time

import ema_trend

K = 3
LEVEL_BARS = 160
# Highest priority first. "bbrevert" (return to the middle band) tested clearly negative and is not in the bank.
PRIORITY = ["trend", "pullback", "breakout", "bbtrend", "range"]
LABELS = {"trend": "Trend + pivot", "pullback": "Pullback to MA20", "breakout": "Breakout",
          "bbtrend": "Bollinger trend", "range": "Range bounce"}


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


def _ema(v, n):
    k = 2.0 / (n + 1)
    out, p = [], v[0]
    for x in v:
        p = x * k + p * (1 - k)
        out.append(p)
    return out


def rsi_series(closes, n=14):
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


def atr_series(c, n=14):
    out = [None] * len(c)
    a = None
    for i in range(1, len(c)):
        tr = max(c[i]["high"] - c[i]["low"], abs(c[i]["high"] - c[i - 1]["close"]), abs(c[i]["low"] - c[i - 1]["close"]))
        if i < n:
            a = tr if a is None else a + tr
            if i == n - 1:
                a /= n
        elif a is not None:
            a = (a * (n - 1) + tr) / n
        if i >= n - 1:
            out[i] = a
    return out


def indicators(c):
    """All chart / strategy series, aligned with c (None where not defined yet)."""
    cl = [x["close"] for x in c]
    m20, m50 = sma(cl, 20), sma(cl, 50)
    sd = [None] * len(cl)
    for i in range(19, len(cl)):
        w = cl[i - 19:i + 1]
        mu = m20[i]
        sd[i] = (sum((x - mu) ** 2 for x in w) / 20) ** 0.5
    macd = [a - b for a, b in zip(_ema(cl, 12), _ema(cl, 26))]
    sig = _ema(macd, 9)
    return {"cl": cl, "m20": m20, "m50": m50, "sd": sd,
            "bb_up": [None if (m is None or s is None) else m + 2 * s for m, s in zip(m20, sd)],
            "bb_lo": [None if (m is None or s is None) else m - 2 * s for m, s in zip(m20, sd)],
            "macd": macd, "sig": sig, "rsi": rsi_series(cl), "atr": atr_series(c)}


def swings(c, i):
    lo = max(K, i - LEVEL_BARS)
    highs, lows = [], []
    n = len(c)
    for j in range(lo, i - K + 1):
        if all(c[j]["high"] >= c[j + q]["high"] for q in range(-K, K + 1) if q and 0 <= j + q < n):
            highs.append(c[j]["high"])
        if all(c[j]["low"] <= c[j + q]["low"] for q in range(-K, K + 1) if q and 0 <= j + q < n):
            lows.append(c[j]["low"])
    return highs, lows


def _targets(px, d, sd, highs, lows):
    pool = sorted((x for x in lows if x < px), reverse=True) if d < 0 else sorted(x for x in highs if x > px)
    t1 = next((x for x in pool if abs(px - x) >= 1.0 * sd), px - d * 1.0 * sd)
    t2 = next((x for x in pool if abs(px - x) >= 1.8 * sd and abs(x - t1) > 0.2 * sd), px - d * 1.8 * sd)
    return t1, t2


def _idea(name, c, P, i, highs, lows):
    px, a = P["cl"][i], P["atr"][i]
    m20, m50, s, r = P["m20"][i], P["m50"][i], P["sd"][i], P["rsi"][i]
    mc, sg = P["macd"][i], P["sig"][i]
    if None in (a, m20, m50, s, r):
        return None
    dn = px < m20 and px < m50 and m20 < m50 and r < 50 and mc < sg
    up = px > m20 and px > m50 and m20 > m50 and r > 50 and mc > sg
    d = sl = pivot = None
    if name == "trend":
        if dn:
            above = sorted(x for x in highs if x > px + 0.3 * a)
            if above:
                d, pivot = -1, above[0]
                sl = pivot + 0.1 * a
        elif up:
            under = sorted((x for x in lows if x < px - 0.3 * a), reverse=True)
            if under:
                d, pivot = 1, under[0]
                sl = pivot - 0.1 * a
    elif name == "pullback":
        w = c[i - 5:i + 1]
        if dn and max(x["high"] for x in w) >= m20 - 0.2 * a and c[i]["close"] < c[i]["open"]:
            d = -1
            pivot = max(x["high"] for x in w)
            sl = pivot + 0.1 * a
        elif up and min(x["low"] for x in w) <= m20 + 0.2 * a and c[i]["close"] > c[i]["open"]:
            d = 1
            pivot = min(x["low"] for x in w)
            sl = pivot - 0.1 * a
    elif name == "range":
        if abs(m20 - m50) < 0.5 * a:
            sup = [x for x in lows if abs(px - x) < 0.5 * a]
            res = [x for x in highs if abs(px - x) < 0.5 * a]
            if sup and r < 40 and c[i]["close"] > c[i]["open"]:
                d, pivot = 1, min(sup)
                sl = pivot - 0.3 * a
            elif res and r > 60 and c[i]["close"] < c[i]["open"]:
                d, pivot = -1, max(res)
                sl = pivot + 0.3 * a
    elif name == "breakout":
        hh = max(x["high"] for x in c[i - 20:i])
        ll = min(x["low"] for x in c[i - 20:i])
        if px > hh and r > 55 and mc > sg:
            d, pivot = 1, hh
            sl = px - 1.0 * a
        elif px < ll and r < 45 and mc < sg:
            d, pivot = -1, ll
            sl = px + 1.0 * a
    elif name == "bbtrend":
        if px < m20 - 2 * s and r < 35 and m20 < m50:
            d, sl, pivot = -1, m20, m20
        elif px > m20 + 2 * s and r > 65 and m20 > m50:
            d, sl, pivot = 1, m20, m20
    if d is None:
        return None
    sdist = abs(px - sl)
    if not (0.4 * a <= sdist <= 2.5 * a):
        return None
    t1, t2 = _targets(px, d, sdist, highs, lows)
    levels = [(x, "resistance") for x in sorted(x for x in highs if x > px)[:3]] + \
             [(x, "support") for x in sorted((x for x in lows if x < px), reverse=True)[:3]]
    return {"name": name, "label": LABELS[name], "direction": "BUY" if d > 0 else "SELL",
            "sl": sl, "tp1": t1, "tp2": t2, "ref": px, "pivot": pivot, "levels": levels}


def evaluate(candles, tf_secs=900, now=None, only_closed=True):
    """Run every strategy on the latest closed candle."""
    c = [{"time": x.get("time"), "open": float(x["open"]), "high": float(x["high"]),
          "low": float(x["low"]), "close": float(x["close"])} for x in (candles or [])]
    if only_closed:
        c = ema_trend.closed_only(c, tf_secs, now)
    if len(c) < 120:
        return {"best": None, "ideas": [], "agree": 0}
    P = indicators(c)
    i = len(c) - 1
    highs, lows = swings(c, i)
    ideas = []
    for name in PRIORITY:
        try:
            x = _idea(name, c, P, i, highs, lows)
        except Exception:
            x = None
        if x:
            ideas.append(x)
    if not ideas:
        return {"best": None, "ideas": [], "agree": 0}
    buys = [x for x in ideas if x["direction"] == "BUY"]
    sells = [x for x in ideas if x["direction"] == "SELL"]
    group = buys if len(buys) > len(sells) else sells if len(sells) > len(buys) else (buys if PRIORITY.index(buys[0]["name"]) < PRIORITY.index(sells[0]["name"]) else sells)
    group = sorted(group, key=lambda x: PRIORITY.index(x["name"]))
    return {"best": group[0], "ideas": ideas, "agree": len(group)}
