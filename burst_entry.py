"""1-minute burst entry for Aggressive Scalper and Account Flip (gold and BTC).

Judged on the last CLOSED 1-minute candle, exactly as in the study (burst_research.py):
  - burst  : tick count >= kv x the median of the 30 bars before it, and range >= kr x their median range
  - strong : the candle body is at least 60% of its range
  - side   : "follow" trades with the burst candle, "fade" trades against it
  - trend  : (optional) the 25/50/100 EMAs on 1-minute closes must be fanned the same way as the burst candle
evaluate(candles, rule) -> {"direction": "BUY"/"SELL"/None, "bar_t", "bar_ts", "why"}
"""
import statistics
import time

from ema_trend import closed_only, ema, _ts

LOOKBACK = 30
BODY_FRAC = 0.6
MIN_BARS = 150


def evaluate(candles, rule, now=None):
    raw = []
    for x in candles or []:
        try:
            raw.append({"time": x.get("time"), "open": float(x["open"]), "high": float(x["high"]),
                        "low": float(x["low"]), "close": float(x["close"]), "volume": float(x.get("volume") or 0)})
        except Exception:
            continue
    c = closed_only(raw, 60, now)
    if len(c) < MIN_BARS:
        return {"direction": None, "bar_t": None, "bar_ts": None, "why": f"only {len(c)} bars"}
    last = c[-1]
    base = {"direction": None, "bar_t": last.get("time"), "bar_ts": _ts(last.get("time"))}
    rng = last["high"] - last["low"]
    body = abs(last["close"] - last["open"])
    if rng <= 0 or body < BODY_FRAC * rng:
        return dict(base, why="no strong candle")
    prior = c[-1 - LOOKBACK:-1]
    med_r = statistics.median(x["high"] - x["low"] for x in prior)
    med_v = statistics.median(x["volume"] for x in prior)
    kr, kv = rule.get("kr", 0), rule.get("kv", 0)
    if kr and not (med_r > 0 and rng >= kr * med_r):
        return dict(base, why="range normal")
    if kv and not (med_v > 0 and last["volume"] >= kv * med_v):
        return dict(base, why="tick count normal")
    d = 1 if last["close"] > last["open"] else -1
    if rule.get("trend"):
        closes = [x["close"] for x in c]
        e25, e50, e100 = ema(closes, 25)[-1], ema(closes, 50)[-1], ema(closes, 100)[-1]
        fan_up = e25 > e50 > e100
        fan_dn = e25 < e50 < e100
        if (d > 0 and not fan_up) or (d < 0 and not fan_dn):
            return dict(base, why="burst against the trend")
    if rule.get("side") == "fade":
        d = -d
    return dict(base, direction="BUY" if d > 0 else "SELL",
                why=f"burst {rule.get('side')}: range {rng / med_r:.1f}x, ticks {last['volume'] / med_v:.1f}x" if med_r and med_v else "burst")
