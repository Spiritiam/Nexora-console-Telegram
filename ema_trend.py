"""5-minute EMA trend-bounce entry (25 / 50 / 100 EMA).

Trade only when the three EMAs are cleanly stacked and fanned out (no intertwining) for a
while: 25 > 50 > 100 for buys, 25 < 50 < 100 for sells. The entry is a bounce off the 25 or
the 50 EMA. While the lines are tangled (consolidation) nothing is traded.

evaluate(candles) -> {"state", "direction", "trigger", "bar_t", "why"}
  state:   "up" | "down" | "range" | "nodata"
  trigger: True when the last CLOSED candle is a bounce in the trend direction
"""
import time
from datetime import datetime

FAST, MID, SLOW = 25, 50, 100
import os
HOLD_BARS = int(os.getenv("EMA_HOLD_BARS", "3"))  # lines must stay in order for this many closed bars (3 = 15 min on M5); no wait for a fresh flip to age
SLOPE_BARS = 6        # the 100 EMA must be sloping the trend's way over this many bars
MIN_GAP_SLOW_ATR = 0.40   # distance 50 <-> 100, in ATR(14)
MIN_GAP_FAST_ATR = 0.10   # distance 25 <-> 50, in ATR(14)
TOUCH_ATR = 0.15          # how close to the EMA counts as a touch
MIN_CANDLES = 220


def ema(values, period):
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    out = [None] * (period - 1)
    prev = sum(values[:period]) / period
    out.append(prev)
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def atr(candles, n=14):
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < n:
        return None
    a = sum(trs[:n]) / n
    for t in trs[n:]:
        a = (a * (n - 1) + t) / n
    return a


def _ts(t):
    try:
        if isinstance(t, (int, float)):
            return float(t)
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def closed_only(candles, secs=300, now=None):
    """Drop the candle that is still forming."""
    if not candles:
        return []
    now = now or time.time()
    ts = _ts(candles[-1].get("time"))
    if ts is None:
        return candles[:-1]
    return candles[:-1] if ts + secs > now else candles


def evaluate(candles, secs=300, now=None):
    c = closed_only([
        {"time": x.get("time"), "open": float(x["open"]), "high": float(x["high"]),
         "low": float(x["low"]), "close": float(x["close"])} for x in (candles or [])
    ], secs, now)
    if len(c) < MIN_CANDLES:
        return {"state": "nodata", "direction": None, "trigger": False, "bar_t": None, "why": f"only {len(c)} candles"}
    closes = [x["close"] for x in c]
    e25, e50, e100 = ema(closes, FAST), ema(closes, MID), ema(closes, SLOW)
    a = atr(c, 14)
    bar_t = c[-1].get("time")
    if not a or a <= 0:
        return {"state": "nodata", "direction": None, "trigger": False, "bar_t": bar_t, "why": "no ATR"}
    n = len(c)
    up_ok = all(e25[i] > e50[i] > e100[i] for i in range(n - HOLD_BARS, n))
    dn_ok = all(e25[i] < e50[i] < e100[i] for i in range(n - HOLD_BARS, n))
    side = "up" if up_ok else ("down" if dn_ok else None)
    if side is None:
        return {"state": "range", "direction": None, "trigger": False, "bar_t": bar_t, "why": "EMAs intertwined"}
    gap_slow = abs(e50[-1] - e100[-1]) / a
    gap_fast = abs(e25[-1] - e50[-1]) / a
    slope = e100[-1] - e100[-1 - SLOPE_BARS]
    slope_ok = slope > 0 if side == "up" else slope < 0
    if gap_slow < MIN_GAP_SLOW_ATR or gap_fast < MIN_GAP_FAST_ATR or not slope_ok:
        return {"state": "range", "direction": None, "trigger": False, "bar_t": bar_t,
                "why": f"stacked but not fanned out (gaps {gap_fast:.2f}/{gap_slow:.2f} ATR)"}
    last = c[-1]
    tol = TOUCH_ATR * a
    if side == "up":
        bounce = last["close"] > last["open"] and last["low"] > e100[-1] and (
            (last["low"] <= e25[-1] + tol and last["close"] > e25[-1]) or
            (last["low"] <= e50[-1] + tol and last["close"] > e50[-1]))
        direction = "BUY"
    else:
        bounce = last["close"] < last["open"] and last["high"] < e100[-1] and (
            (last["high"] >= e25[-1] - tol and last["close"] < e25[-1]) or
            (last["high"] >= e50[-1] - tol and last["close"] < e50[-1]))
        direction = "SELL"
    return {"state": side, "direction": direction if bounce else None, "trigger": bool(bounce),
            "bar_t": bar_t, "why": "bounce" if bounce else "trending, waiting for a bounce"}
