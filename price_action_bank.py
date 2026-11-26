"""Price-action strategy bank (multi-timeframe).

How a signal is decided:
  1-hour chart  -> context: market structure bias (higher highs / higher lows, or lower highs / lower lows)
  15-minute     -> every strategy below runs on the last CLOSED 15-minute candle
  5-minute      -> entry trigger: the last closed 5-minute candle must move in the signal direction
At least MIN_AGREE strategies from at least 2 different families must point the same way. A signal against the
1-hour structure needs one more (MIN_AGREE + 1). Mixed BUY / SELL with no clear majority = no signal.
Only the direction and the strategy names are used; stop loss / take profit stay the normal fixed distances.

evaluate_pa(c1h, c15, c5, daily=None, now=None) -> {"best": idea|None, "ideas": [...], "agree": n, "bias": str, "why": str}
idea = {"name", "label", "family", "direction", "agree_labels": [...]}
"""
import math
import os
import time
from datetime import datetime, timezone

import ema_trend
import strategy_bank as sb

MIN_AGREE = int(os.environ.get("BANK_MIN_AGREE", "2"))
# A signal against the 1-hour trend is skipped when price has already run this many 15-minute ranges (ATR) away from
# the low (BUY) or high (SELL) of the last 8 hours: it would be buying the top of a bounce / selling the bottom of a drop.
OVEREXT_ATR = float(os.environ.get("BANK_OVEREXT_ATR", "5"))
K = 3  # swing strength (bars each side)


# ----------------------------------------------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------------------------------------------
def _norm(candles):
    out = []
    for x in candles or []:
        try:
            t = ema_trend._ts(x.get("time"))
            if t is None:
                t = ema_trend._ts(x.get("datetime"))
            out.append({"t": t, "open": float(x["open"]), "high": float(x["high"]), "low": float(x["low"]),
                        "close": float(x["close"]), "v": float(x.get("volume") or 0), "time": x.get("time")})
        except Exception:
            continue
    return out


def _dt(t):
    return datetime.fromtimestamp(t, tz=timezone.utc)


def swing_points(c, k=K, upto=None):
    """Confirmed swing highs / lows as (index, price)."""
    n = len(c) if upto is None else upto
    highs, lows = [], []
    for j in range(k, n - k):
        # strictly higher than the bars before it, not lower than the bars after it (an equal high counts once)
        if all(c[j]["high"] > c[j - q]["high"] for q in range(1, k + 1)) and all(c[j]["high"] >= c[j + q]["high"] for q in range(1, k + 1)):
            highs.append((j, c[j]["high"]))
        if all(c[j]["low"] < c[j - q]["low"] for q in range(1, k + 1)) and all(c[j]["low"] <= c[j + q]["low"] for q in range(1, k + 1)):
            lows.append((j, c[j]["low"]))
    return highs, lows


def structure(highs, lows):
    """'up' (HH + HL), 'down' (LH + LL) or 'range' from the last two swings."""
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    if hh and hl:
        return "up"
    if not hh and not hl:
        return "down"
    return "range"


def _body(x):
    return abs(x["close"] - x["open"])


def _rng(x):
    return x["high"] - x["low"]


def _up(x):
    return x["close"] > x["open"]


def _dn(x):
    return x["close"] < x["open"]


class Ctx:
    pass


def build_ctx(c15, c1h, c5, daily, now):
    X = Ctx()
    X.c = c15
    X.n = len(c15)
    X.i = X.n - 1
    X.px = c15[-1]["close"]
    cl = [x["close"] for x in c15]
    X.cl = cl
    X.P = sb.indicators([{"open": x["open"], "high": x["high"], "low": x["low"], "close": x["close"]} for x in c15])
    X.a = X.P["atr"][X.i] or 0.0
    X.swH, X.swL = swing_points(c15)
    X.h1 = c1h
    X.c5 = c5
    X.daily = daily or []
    X.now = now
    X.vol_ok = sum(1 for x in c15[-40:] if x["v"] > 0) >= 30
    h1H, h1L = swing_points(c1h, 2)
    X.bias = structure(h1H, h1L)
    X.step = 10 ** (math.floor(math.log10(max(X.px, 1e-9))) - 1) / (10 if X.px < 10 else 1)
    X.lv = _levels(X)
    return X


def _levels(X):
    """Previous day / week / month highs and lows, previous-session range, opening range, daily pivot."""
    lv = {}
    src = X.daily if len(X.daily) >= 3 else None
    cur_day = _dt(X.c[-1]["t"]).date()
    if src:
        prev = [d for d in src if _dt(d["t"]).date() < cur_day]
        if prev:
            d = prev[-1]
            lv["pd"] = (d["high"], d["low"])
            P = (d["high"] + d["low"] + d["close"]) / 3
            lv["pivot"] = (P, 2 * P - d["low"], P - (d["high"] - d["low"]), P + (d["high"] - d["low"]), P - (d["high"] - d["low"]))
        wk = _dt(X.c[-1]["t"]).isocalendar()[:2]
        pw = [d for d in src if _dt(d["t"]).isocalendar()[:2] < wk]
        if pw:
            last_wk = _dt(pw[-1]["t"]).isocalendar()[:2]
            w = [d for d in pw if _dt(d["t"]).isocalendar()[:2] == last_wk]
            lv["pw"] = (max(d["high"] for d in w), min(d["low"] for d in w))
        cm = (_dt(X.c[-1]["t"]).year, _dt(X.c[-1]["t"]).month)
        pm = [d for d in src if (_dt(d["t"]).year, _dt(d["t"]).month) < cm]
        if pm:
            last_m = (_dt(pm[-1]["t"]).year, _dt(pm[-1]["t"]).month)
            m = [d for d in pm if (_dt(d["t"]).year, _dt(d["t"]).month) == last_m]
            if len(m) >= 10:
                lv["pm"] = (max(d["high"] for d in m), min(d["low"] for d in m))
    else:
        # no daily data: fall back to the 15-minute history
        by = {}
        for x in X.c:
            by.setdefault(_dt(x["t"]).date(), []).append(x)
        days = [d for d in sorted(by) if d < cur_day]
        if days:
            xs = by[days[-1]]
            lv["pd"] = (max(x["high"] for x in xs), min(x["low"] for x in xs))
    # sessions (UTC): Asia 00-07, London 07-12, New York 12-21
    def sess(h):
        return "A" if h < 7 else "L" if h < 12 else "N" if h < 21 else "-"
    by = []
    for x in X.c:
        by.append((_dt(x["t"]).date(), sess(_dt(x["t"]).hour), x))
    cur = (by[-1][0], by[-1][1])
    groups = {}
    for d, s, x in by:
        groups.setdefault((d, s), []).append(x)
    order = [g for g in groups if g != cur and g[1] != "-"]
    if order:
        g = order[-1]
        lv["sess"] = (max(x["high"] for x in groups[g]), min(x["low"] for x in groups[g]), g[1])
    # opening range: first hour after London open (07:00-08:00) or New York open (13:30-14:30)
    for tag, (h0, m0) in (("orbL", (7, 0)), ("orbN", (13, 30))):
        xs = [x for x in X.c if _dt(x["t"]).date() == cur_day and
              (_dt(x["t"]).hour * 60 + _dt(x["t"]).minute) >= h0 * 60 + m0 and
              (_dt(x["t"]).hour * 60 + _dt(x["t"]).minute) < h0 * 60 + m0 + 60]
        if len(xs) >= 3:
            lv[tag] = (max(x["high"] for x in xs), min(x["low"] for x in xs), xs[-1]["t"])
    return lv


def _brk(X, hi, lo, bars=2):
    """Close beyond hi / lo within the last `bars` candles, after being inside before that."""
    c = X.c
    for b in range(1, bars + 1):
        k = X.n - b
        if k < 1:
            break
        if hi is not None and c[k]["close"] > hi and c[k - 1]["close"] <= hi and X.px > hi:
            return "BUY"
        if lo is not None and c[k]["close"] < lo and c[k - 1]["close"] >= lo and X.px < lo:
            return "SELL"
    return None


def _reject(X, hi, lo, tol=0.15):
    """Wick through the level and close back inside on the last candle: reversal."""
    x = X.c[-1]
    a = X.a
    if hi is not None and x["high"] > hi and x["close"] < hi and x["high"] - hi < 1.5 * a:
        return "SELL"
    if lo is not None and x["low"] < lo and x["close"] > lo and lo - x["low"] < 1.5 * a:
        return "BUY"
    return None


def _prior_move(X, bars=5):
    """Direction of the move into the last candle (+1 up, -1 down, 0 none), needs at least 1 ATR."""
    if X.n < bars + 2:
        return 0
    d = X.c[-2]["close"] - X.c[-2 - bars]["close"]
    if d > X.a:
        return 1
    if d < -X.a:
        return -1
    return 0


# ----------------------------------------------------------------------------------------------------------------
# STRUCTURE
# ----------------------------------------------------------------------------------------------------------------
def s_hhhl(X):
    if len(X.swH) < 2 or len(X.swL) < 2:
        return None
    s = structure(X.swH, X.swL)
    fresh = max(X.swH[-1][0], X.swL[-1][0]) >= X.n - 12   # the latest swing just formed, so the structure is live
    x = X.c[-1]
    if s == "up" and fresh and X.px > X.swL[-1][1] and _up(x):
        return "BUY"
    if s == "down" and fresh and X.px < X.swH[-1][1] and _dn(x):
        return "SELL"
    return None


def s_bos(X):
    if not X.swH or not X.swL:
        return None
    return _brk(X, X.swH[-1][1], X.swL[-1][1], 3)


def s_choch(X):
    if len(X.swH) < 3 or len(X.swL) < 3:
        return None
    # earlier structure was down (LH + LL) and price now closes above the last lower high -> BUY; mirror for SELL
    was_down = X.swH[-1][1] < X.swH[-2][1] and X.swL[-1][1] < X.swL[-2][1]
    was_up = X.swH[-1][1] > X.swH[-2][1] and X.swL[-1][1] > X.swL[-2][1]
    if was_down:
        b = _brk(X, X.swH[-1][1], None, 3)
        if b == "BUY":
            return "BUY"
    if was_up:
        b = _brk(X, None, X.swL[-1][1], 3)
        if b == "SELL":
            return "SELL"
    return None


def s_swing_trade(X):
    if len(X.swH) < 2 or len(X.swL) < 2:
        return None
    s = structure(X.swH, X.swL)
    x = X.c[-1]
    if s == "up" and abs(x["low"] - X.swL[-1][1]) <= 0.5 * X.a and _up(x) and x["close"] > X.swL[-1][1]:
        return "BUY"
    if s == "down" and abs(x["high"] - X.swH[-1][1]) <= 0.5 * X.a and _dn(x) and x["close"] < X.swH[-1][1]:
        return "SELL"
    return None


# ----------------------------------------------------------------------------------------------------------------
# KEY LEVELS
# ----------------------------------------------------------------------------------------------------------------
def _lvl_pair(X, key):
    v = X.lv.get(key)
    return (v[0], v[1]) if v else (None, None)


def s_pd_break(X):
    hi, lo = _lvl_pair(X, "pd")
    return _brk(X, hi, lo, 2) if hi else None


def s_pd_reject(X):
    hi, lo = _lvl_pair(X, "pd")
    return _reject(X, hi, lo) if hi else None


def s_pw_break(X):
    out = []
    for k in ("pw", "pm"):
        hi, lo = _lvl_pair(X, k)
        if hi:
            out.append(_brk(X, hi, lo, 2) or _reject(X, hi, lo))
    out = [o for o in out if o]
    return out[0] if out else None


def s_sr_zone(X):
    x = X.c[-1]
    a = X.a
    for _, lv in reversed(X.swL[-8:]):
        if abs(x["low"] - lv) <= 0.4 * a and x["close"] > lv and (x["close"] - x["low"]) > 0.5 * _rng(x):
            return "BUY"
    for _, lv in reversed(X.swH[-8:]):
        if abs(x["high"] - lv) <= 0.4 * a and x["close"] < lv and (x["high"] - x["close"]) > 0.5 * _rng(x):
            return "SELL"
    return None


def s_flip_zone(X):
    """Old resistance that was broken now acts as support (and the mirror)."""
    x = X.c[-1]
    a = X.a
    for j, lv in X.swH[-8:]:
        broke = any(X.c[k]["close"] > lv + 0.3 * a for k in range(j + 1, X.n - 3))
        if broke and abs(x["low"] - lv) <= 0.5 * a and _up(x) and x["close"] > lv:
            return "BUY"
    for j, lv in X.swL[-8:]:
        broke = any(X.c[k]["close"] < lv - 0.3 * a for k in range(j + 1, X.n - 3))
        if broke and abs(x["high"] - lv) <= 0.5 * a and _dn(x) and x["close"] < lv:
            return "SELL"
    return None


def s_round_number(X):
    x = X.c[-1]
    st = X.step
    if st <= 0:
        return None
    lv = round(x["close"] / st) * st
    if abs(x["high"] - lv) <= 0.3 * X.a or abs(x["low"] - lv) <= 0.3 * X.a:
        r = _reject(X, lv, lv)
        if r:
            return r
    return None


def s_pivot_points(X):
    p = X.lv.get("pivot")
    if not p:
        return None
    P, R1, S1, R2, S2 = p
    x = X.c[-1]
    a = X.a
    for s in (S1, S2):
        if abs(x["low"] - s) <= 0.3 * a and _up(x) and x["close"] > s:
            return "BUY"
    for r in (R1, R2):
        if abs(x["high"] - r) <= 0.3 * a and _dn(x) and x["close"] < r:
            return "SELL"
    return None


def s_session(X):
    s = X.lv.get("sess")
    if not s:
        return None
    return _brk(X, s[0], s[1], 2) or _reject(X, s[0], s[1])


def s_orb(X):
    for tag in ("orbL", "orbN"):
        o = X.lv.get(tag)
        if o and X.c[-1]["t"] > o[2] and X.c[-1]["t"] - o[2] < 6 * 3600:
            b = _brk(X, o[0], o[1], 2)
            if b:
                return b
    return None


# ----------------------------------------------------------------------------------------------------------------
# REVERSAL
# ----------------------------------------------------------------------------------------------------------------
def s_exhaustion(X):
    x = X.c[-1]
    m20 = X.P["m20"][X.i]
    if m20 is None or X.a <= 0:
        return None
    wick_lo = min(x["open"], x["close"]) - x["low"]
    wick_hi = x["high"] - max(x["open"], x["close"])
    if (m20 - x["low"]) > 2.2 * X.a and wick_lo > max(1.5 * _body(x), 0.5 * X.a):
        return "BUY"
    if (x["high"] - m20) > 2.2 * X.a and wick_hi > max(1.5 * _body(x), 0.5 * X.a):
        return "SELL"
    return None


def s_engulfing(X):
    if X.n < 8:
        return None
    a, b = X.c[-2], X.c[-1]
    pm = _prior_move(X)
    if pm < 0 and _dn(a) and _up(b) and b["close"] >= a["open"] and b["open"] <= a["close"] and _body(b) > _body(a):
        return "BUY"
    if pm > 0 and _up(a) and _dn(b) and b["close"] <= a["open"] and b["open"] >= a["close"] and _body(b) > _body(a):
        return "SELL"
    return None


def s_pin_bar(X):
    x = X.c[-1]
    r = _rng(x)
    if r <= 0 or X.a <= 0 or r < 0.8 * X.a:
        return None
    wick_lo = min(x["open"], x["close"]) - x["low"]
    wick_hi = x["high"] - max(x["open"], x["close"])
    pm = _prior_move(X)
    if pm <= 0 and wick_lo > 0.6 * r and _body(x) < 0.3 * r and pm < 0:
        return "BUY"
    if pm >= 0 and wick_hi > 0.6 * r and _body(x) < 0.3 * r and pm > 0:
        return "SELL"
    return None


def s_star(X):
    if X.n < 10:
        return None
    a, b, c3 = X.c[-3], X.c[-2], X.c[-1]
    small = _body(b) < 0.35 * max(_body(a), 1e-9)
    if X.a <= 0:
        return None
    if _dn(a) and _body(a) > 0.6 * X.a and small and _up(c3) and c3["close"] > (a["open"] + a["close"]) / 2:
        return "BUY"
    if _up(a) and _body(a) > 0.6 * X.a and small and _dn(c3) and c3["close"] < (a["open"] + a["close"]) / 2:
        return "SELL"
    return None


def s_doji_level(X):
    x = X.c[-1]
    if _rng(x) <= 0 or _body(x) > 0.12 * _rng(x) or X.a <= 0:
        return None
    pm = _prior_move(X)
    near_lo = any(abs(x["low"] - lv) <= 0.5 * X.a for _, lv in X.swL[-8:])
    near_hi = any(abs(x["high"] - lv) <= 0.5 * X.a for _, lv in X.swH[-8:])
    if pm < 0 and near_lo:
        return "BUY"
    if pm > 0 and near_hi:
        return "SELL"
    return None


def _tops(X, pts, n, tol):
    """Last n swing points whose prices are within tol of each other."""
    if len(pts) < n:
        return None
    last = pts[-n:]
    p = [v for _, v in last]
    if max(p) - min(p) <= tol and last[-1][0] - last[0][0] >= 5 * (n - 1):
        return last
    return None


def _neck(X, i0, i1, kind):
    seg = X.c[i0:i1 + 1]
    return min(x["low"] for x in seg) if kind == "top" else max(x["high"] for x in seg)


def s_double(X, n=2):
    tol = 0.25 * X.a if n == 2 else 0.3 * X.a
    t = _tops(X, X.swH, n, tol)
    if t and X.n - 1 - t[-1][0] <= 25:
        neck = _neck(X, t[0][0], t[-1][0], "top")
        if _brk(X, None, neck, 3) == "SELL":
            return "SELL"
    b = _tops(X, X.swL, n, tol)
    if b and X.n - 1 - b[-1][0] <= 25:
        neck = _neck(X, b[0][0], b[-1][0], "bottom")
        if _brk(X, neck, None, 3) == "BUY":
            return "BUY"
    return None


def s_triple(X):
    return s_double(X, 3)


def s_hns(X):
    if len(X.swH) >= 3 and X.a > 0:
        (i1, l), (i2, h), (i3, r) = X.swH[-3:]
        if h > l + 0.5 * X.a and h > r + 0.5 * X.a and abs(l - r) <= 0.5 * X.a and X.n - 1 - i3 <= 25:
            neck = _neck(X, i1, i3, "top")
            if _brk(X, None, neck, 3) == "SELL":
                return "SELL"
    if len(X.swL) >= 3 and X.a > 0:
        (i1, l), (i2, h), (i3, r) = X.swL[-3:]
        if h < l - 0.5 * X.a and h < r - 0.5 * X.a and abs(l - r) <= 0.5 * X.a and X.n - 1 - i3 <= 25:
            neck = _neck(X, i1, i3, "bottom")
            if _brk(X, neck, None, 3) == "BUY":
                return "BUY"
    return None


def s_liq_sweep(X):
    x = X.c[-1]
    for k in (1, 2, 3):
        j = X.n - k
        if j < 1:
            break
        y = X.c[j]
        for hi_i, hi in X.swH[-6:]:
            if hi_i < j - 1 and y["high"] > hi and y["close"] < hi and X.px < hi and X.px < y["close"]:
                return "SELL"
        for lo_i, lo in X.swL[-6:]:
            if lo_i < j - 1 and y["low"] < lo and y["close"] > lo and X.px > lo and X.px > y["close"]:
                return "BUY"
    return None


def _diverge(X, series, bull_cap, bear_floor):
    if len(X.swL) >= 2:
        (i1, p1), (i2, p2) = X.swL[-2:]
        if X.n - 1 - i2 <= 15 and p2 < p1 and series[i1] is not None and series[i2] is not None \
                and series[i2] > series[i1] and series[i2] < bull_cap and X.px > X.c[i2]["close"]:
            return "BUY"
    if len(X.swH) >= 2:
        (i1, p1), (i2, p2) = X.swH[-2:]
        if X.n - 1 - i2 <= 15 and p2 > p1 and series[i1] is not None and series[i2] is not None \
                and series[i2] < series[i1] and series[i2] > bear_floor and X.px < X.c[i2]["close"]:
            return "SELL"
    return None


def s_rsi_div(X):
    return _diverge(X, X.P["rsi"], 45, 55)


def s_macd_div(X):
    return _diverge(X, X.P["macd"], 1e18, -1e18)


# ----------------------------------------------------------------------------------------------------------------
# RETRACEMENT / CONTINUATION
# ----------------------------------------------------------------------------------------------------------------
def s_fib(X):
    if not X.swH or not X.swL:
        return None
    (ih, h), (il, l) = X.swH[-1], X.swL[-1]
    rg = h - l
    if rg < 2 * X.a:
        return None
    x = X.c[-1]
    if il < ih:  # up-leg low -> high, retracing down
        r = (h - x["close"]) / rg
        if 0.382 <= r <= 0.618 and _up(x) and x["close"] > X.c[-2]["close"]:
            return "BUY"
    elif ih < il:  # down-leg high -> low, retracing up
        r = (x["close"] - l) / rg
        if 0.382 <= r <= 0.618 and _dn(x) and x["close"] < X.c[-2]["close"]:
            return "SELL"
    return None


def s_retest(X):
    x = X.c[-1]
    a = X.a
    for j, lv in X.swH[-6:]:
        crossed = [k for k in range(j + 1, X.n - 2) if X.c[k]["close"] > lv + 0.2 * a]
        if crossed and 3 <= X.n - 1 - crossed[0] <= 20 and abs(x["low"] - lv) <= 0.4 * a and _up(x) and x["close"] > lv:
            return "BUY"
    for j, lv in X.swL[-6:]:
        crossed = [k for k in range(j + 1, X.n - 2) if X.c[k]["close"] < lv - 0.2 * a]
        if crossed and 3 <= X.n - 1 - crossed[0] <= 20 and abs(x["high"] - lv) <= 0.4 * a and _dn(x) and x["close"] < lv:
            return "SELL"
    return None


def s_flag(X):
    c, a = X.c, X.a
    if X.n < 30 or a <= 0:
        return None
    for cons in range(5, 13):
        seg = c[X.n - 1 - cons:X.n - 1]
        imp = c[X.n - 1 - cons - 8:X.n - 1 - cons]
        if len(imp) < 8:
            continue
        move = imp[-1]["close"] - imp[0]["open"]
        hi = max(x["high"] for x in seg)
        lo = min(x["low"] for x in seg)
        if abs(move) >= 3 * a and (hi - lo) <= 1.6 * a:
            if move > 0 and X.px > hi and lo > imp[-1]["close"] - 0.6 * abs(move):
                return "BUY"
            if move < 0 and X.px < lo and hi < imp[-1]["close"] + 0.6 * abs(move):
                return "SELL"
    return None


def _line(pts):
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    if x2 == x1:
        return 0.0, y2
    m = (y2 - y1) / (x2 - x1)
    return m, y2 - m * x2


def _converge(X):
    if len(X.swH) < 3 or len(X.swL) < 3:
        return None
    H, L = X.swH[-3:], X.swL[-3:]
    if min(H[0][0], L[0][0]) < X.n - 80:
        return None
    mh, bh = _line(H)
    ml, bl = _line(L)
    i = X.n - 1
    up_line, lo_line = mh * i + bh, ml * i + bl
    if up_line - lo_line <= 0 or (up_line - lo_line) > (mh * H[0][0] + bh) - (ml * H[0][0] + bl):
        return None
    return mh, ml, up_line, lo_line


def s_triangle(X):
    r = _converge(X)
    if not r:
        return None
    mh, ml, up, lo = r
    # symmetrical / ascending / descending: highs flat or falling and lows flat or rising
    if mh <= 0.02 * X.a and ml >= -0.02 * X.a:
        if X.px > up + 0.1 * X.a:
            return "BUY"
        if X.px < lo - 0.1 * X.a:
            return "SELL"
    return None


def s_wedge(X):
    r = _converge(X)
    if not r:
        return None
    mh, ml, up, lo = r
    if mh > 0 and ml > 0 and X.px < lo - 0.1 * X.a:   # rising wedge breaks down
        return "SELL"
    if mh < 0 and ml < 0 and X.px > up + 0.1 * X.a:   # falling wedge breaks up
        return "BUY"
    return None


def s_channel(X):
    if len(X.swH) < 3 or len(X.swL) < 3:
        return None
    mh, bh = _line(X.swH[-3:])
    ml, bl = _line(X.swL[-3:])
    if abs(mh - ml) > 0.35 * max(abs(mh), abs(ml), 1e-9) or mh * ml <= 0:
        return None
    i = X.n - 1
    up, lo = mh * i + bh, ml * i + bl
    if up - lo < 1.5 * X.a:
        return None
    x = X.c[-1]
    if mh > 0 and abs(x["low"] - lo) <= 0.4 * X.a and _up(x):
        return "BUY"
    if mh < 0 and abs(x["high"] - up) <= 0.4 * X.a and _dn(x):
        return "SELL"
    return None


def _zones(X, strong, base):
    """Demand / supply zones: the base before a strong impulse. Returns (demand, supply) lists of (lo, hi, idx)."""
    dem, sup = [], []
    c, a = X.c, X.a
    for j in range(max(base, X.n - 120), X.n - 4):
        imp_up = c[j + 4]["close"] - c[j]["close"] if j + 4 < X.n else 0
        seg = c[j - base + 1:j + 1]
        lo, hi = min(x["low"] for x in seg), max(x["high"] for x in seg)
        if imp_up > strong * a and _dn(c[j]) or (imp_up > strong * a and base > 1):
            dem.append((lo, hi, j))
        if imp_up < -strong * a and _up(c[j]) or (imp_up < -strong * a and base > 1):
            sup.append((lo, hi, j))
    return dem, sup


def _zone_signal(X, strong, base):
    dem, sup = _zones(X, strong, base)
    x = X.c[-1]
    for lo, hi, j in reversed(dem[-6:]):
        if X.n - 1 - j > 6 and x["low"] <= hi and x["close"] > hi and x["close"] > x["open"] and x["low"] >= lo - 0.3 * X.a:
            return "BUY"
    for lo, hi, j in reversed(sup[-6:]):
        if X.n - 1 - j > 6 and x["high"] >= lo and x["close"] < lo and x["close"] < x["open"] and x["high"] <= hi + 0.3 * X.a:
            return "SELL"
    return None


def s_supply_demand(X):
    return _zone_signal(X, 2.5, 3)


def s_order_block(X):
    return _zone_signal(X, 2.0, 1)


def s_fvg(X):
    c = X.c
    x = c[-1]
    for j in range(X.n - 3, max(X.n - 35, 2), -1):
        a1, a3 = c[j - 1], c[j + 1]
        if a3["low"] > a1["high"]:  # bullish gap
            lo, hi = a1["high"], a3["low"]
            if all(c[k]["low"] > lo for k in range(j + 2, X.n - 1)) and x["low"] <= hi and x["close"] > hi and _up(x):
                return "BUY"
        if a3["high"] < a1["low"]:  # bearish gap
            lo, hi = a3["high"], a1["low"]
            if all(c[k]["high"] < hi for k in range(j + 2, X.n - 1)) and x["high"] >= lo and x["close"] < lo and _dn(x):
                return "SELL"
    return None


# ----------------------------------------------------------------------------------------------------------------
# RANGE / VOLATILITY
# ----------------------------------------------------------------------------------------------------------------
def s_range_bounce(X):
    seg = X.c[-31:-1]
    if len(seg) < 30 or X.a <= 0:
        return None
    hi, lo = max(x["high"] for x in seg), min(x["low"] for x in seg)
    if (hi - lo) > 5 * X.a:
        return None
    x = X.c[-1]
    if x["low"] <= lo + 0.4 * X.a and _up(x) and (x["close"] - x["low"]) > 0.5 * _rng(x):
        return "BUY"
    if x["high"] >= hi - 0.4 * X.a and _dn(x) and (x["high"] - x["close"]) > 0.5 * _rng(x):
        return "SELL"
    return None


def s_squeeze(X):
    P = X.P
    bw = []
    for k in range(X.n):
        u, l, m = P["bb_up"][k], P["bb_lo"][k], P["m20"][k]
        bw.append(None if None in (u, l, m) or m == 0 else (u - l) / m)
    recent = [b for b in bw[-100:] if b is not None]
    if len(recent) < 60:
        return None
    prev = bw[-2]
    if prev is None or prev > sorted(recent)[int(len(recent) * 0.2)]:
        return None
    if X.px > P["bb_up"][X.i]:
        return "BUY"
    if X.px < P["bb_lo"][X.i]:
        return "SELL"
    return None


def s_atr_break(X):
    x = X.c[-1]
    if X.a <= 0 or X.n < 25:
        return None
    prev_atr = X.P["atr"][X.i - 1] or X.a
    tr = max(x["high"] - x["low"], abs(x["high"] - X.c[-2]["close"]), abs(x["low"] - X.c[-2]["close"]))
    if tr < 1.6 * prev_atr:
        return None
    hi = max(k["high"] for k in X.c[-11:-1])
    lo = min(k["low"] for k in X.c[-11:-1])
    top = (x["close"] - x["low"]) / max(_rng(x), 1e-9)
    if x["close"] > hi and top > 0.75:
        return "BUY"
    if x["close"] < lo and top < 0.25:
        return "SELL"
    return None


def s_inside_bar(X):
    if X.n < 4:
        return None
    m, i1, x = X.c[-3], X.c[-2], X.c[-1]
    if i1["high"] <= m["high"] and i1["low"] >= m["low"]:
        if x["close"] > m["high"]:
            return "BUY"
        if x["close"] < m["low"]:
            return "SELL"
    return None


# ----------------------------------------------------------------------------------------------------------------
# VOLUME / MOMENTUM
# ----------------------------------------------------------------------------------------------------------------
def s_volume_spike(X):
    if not X.vol_ok:
        return None
    x = X.c[-1]
    avg = sum(k["v"] for k in X.c[-21:-1]) / 20
    if avg <= 0 or x["v"] < 2.0 * avg or X.a <= 0:
        return None
    pm = _prior_move(X)
    top = (x["close"] - x["low"]) / max(_rng(x), 1e-9)
    if pm < 0 and top > 0.6:
        return "BUY"
    if pm > 0 and top < 0.4:
        return "SELL"
    return None


def s_vwap(X):
    day = _dt(X.c[-1]["t"]).date()
    xs = [(k, i) for i, k in enumerate(X.c) if _dt(k["t"]).date() == day]
    if len(xs) < 12:
        return None
    num = den = 0.0
    vw = []
    for k, _ in xs:
        tp = (k["high"] + k["low"] + k["close"]) / 3
        w = k["v"] if X.vol_ok else 1.0
        num += tp * w
        den += w
        vw.append(num / den if den else tp)
    c = [k["close"] for k, _ in xs]
    if len(c) < 8:
        return None
    if all(c[j] < vw[j] for j in range(-7, -1)) and c[-1] > vw[-1]:
        return "BUY"
    if all(c[j] > vw[j] for j in range(-7, -1)) and c[-1] < vw[-1]:
        return "SELL"
    return None


def s_rsi_extreme(X):
    r = X.P["rsi"]
    if r[-1] is None or r[-2] is None:
        return None
    if r[-2] < 30 <= r[-1]:
        return "BUY"
    if r[-2] > 70 >= r[-1]:
        return "SELL"
    return None


def s_macd_cross(X):
    m, s = X.P["macd"], X.P["sig"]
    if X.n < 40:
        return None
    for b in (1, 2):
        k = X.n - b
        if m[k] > s[k] and m[k - 1] <= s[k - 1] and m[k] < 0:
            return "BUY"
        if m[k] < s[k] and m[k - 1] >= s[k - 1] and m[k] > 0:
            return "SELL"
    return None


def s_stoch(X):
    c = X.c
    if X.n < 20:
        return None
    k = []
    for i in range(X.n - 6, X.n):
        w = c[i - 13:i + 1]
        lo, hi = min(x["low"] for x in w), max(x["high"] for x in w)
        k.append(100 * (c[i]["close"] - lo) / (hi - lo) if hi > lo else 50.0)
    d = [sum(k[j - 2:j + 1]) / 3 for j in range(2, len(k))]
    kk = k[2:]
    if kk[-1] > d[-1] and kk[-2] <= d[-2] and min(kk[-3:]) < 25:
        return "BUY"
    if kk[-1] < d[-1] and kk[-2] >= d[-2] and max(kk[-3:]) > 75:
        return "SELL"
    return None


# name, label, family, function.  Order = priority inside a family when labels are shown.
STRATEGIES = [
    ("hhhl", "Higher highs / lower lows", "structure", s_hhhl),
    ("bos", "Break of structure", "structure", s_bos),
    ("choch", "Change of character", "structure", s_choch),
    ("swing", "Swing high / low", "structure", s_swing_trade),
    ("pd_break", "Previous day high / low break", "levels", s_pd_break),
    ("pd_reject", "Previous day high / low rejection", "levels", s_pd_reject),
    ("pw_levels", "Previous week / month high / low", "levels", s_pw_break),
    ("sr", "Support / resistance", "levels", s_sr_zone),
    ("flip", "Flip zone", "levels", s_flip_zone),
    ("round", "Round number", "levels", s_round_number),
    ("pivots", "Pivot points", "levels", s_pivot_points),
    ("session", "Session high / low", "levels", s_session),
    ("orb", "Opening range breakout", "levels", s_orb),
    ("exhaustion", "Exhaustion", "reversal", s_exhaustion),
    ("engulf", "Engulfing", "reversal", s_engulfing),
    ("pin", "Pin bar", "reversal", s_pin_bar),
    ("star", "Morning / evening star", "reversal", s_star),
    ("doji", "Doji at a level", "reversal", s_doji_level),
    ("double", "Double top / bottom", "reversal", s_double),
    ("triple", "Triple top / bottom", "reversal", s_triple),
    ("hns", "Head and shoulders", "reversal", s_hns),
    ("sweep", "Liquidity sweep", "reversal", s_liq_sweep),
    ("rsi_div", "RSI divergence", "reversal", s_rsi_div),
    ("macd_div", "MACD divergence", "reversal", s_macd_div),
    ("fib", "Fibonacci retracement", "retrace", s_fib),
    ("retest", "Breakout and retest", "retrace", s_retest),
    ("flag", "Flag / pennant", "retrace", s_flag),
    ("triangle", "Triangle", "retrace", s_triangle),
    ("wedge", "Wedge", "retrace", s_wedge),
    ("channel", "Channel", "retrace", s_channel),
    ("zones", "Supply / demand zone", "retrace", s_supply_demand),
    ("ob", "Order block", "retrace", s_order_block),
    ("fvg", "Fair value gap", "retrace", s_fvg),
    ("range", "Range bounce", "range", s_range_bounce),
    ("squeeze", "Bollinger squeeze breakout", "range", s_squeeze),
    ("atr_break", "ATR volatility breakout", "range", s_atr_break),
    ("inside", "Inside bar breakout", "range", s_inside_bar),
    ("volume", "Volume spike", "momentum", s_volume_spike),
    ("vwap", "VWAP", "momentum", s_vwap),
    ("rsi_x", "RSI overbought / oversold", "momentum", s_rsi_extreme),
    ("macd_x", "MACD cross", "momentum", s_macd_cross),
    ("stoch", "Stochastic cross", "momentum", s_stoch),
]
LABELS = {n: l for n, l, _, _ in STRATEGIES}


def trigger_5m(c5, direction):
    """Entry trigger on the 5-minute chart: the last closed 5-minute candle moves with the signal."""
    if len(c5) < 3:
        return True  # no 5-minute data: do not block
    x, y = c5[-1], c5[-2]
    if direction == "BUY":
        return x["close"] > x["open"] and x["close"] > y["close"]
    return x["close"] < x["open"] and x["close"] < y["close"]


def scan_pa(c1h, c15, c5, daily=None, now=None, sig_s=900, bias_s=3600, c5_s=300, min_sig=120, min_bias=40):
    """Runs every strategy once. sig = the chart the strategies run on (15m live), bias = the higher chart that gives the
    direction (1h live). Returns (X, ideas, n_sig, why); X is None when there are not enough candles."""
    now = now or time.time()
    n15 = ema_trend.closed_only(_norm(c15), sig_s, now)
    n1h = ema_trend.closed_only(_norm(c1h), bias_s, now)
    n5 = ema_trend.closed_only(_norm(c5), c5_s, now)
    nd = _norm(daily)
    if len(n15) < min_sig or len(n1h) < min_bias or any(x["t"] is None for x in n15[-3:]):
        return None, [], n15, f"not enough candles (signal chart {len(n15)}, bias chart {len(n1h)})"
    X = build_ctx(n15, n1h, n5, nd, now)
    ideas = []
    for name, label, fam, fn in STRATEGIES:
        try:
            d = fn(X)
        except Exception:
            d = None
        if d in ("BUY", "SELL"):
            ideas.append({"name": name, "label": label, "family": fam, "direction": d})
    return X, ideas, n15, ""


def pick_pa(X, ideas, n15, min_agree=None, look=32, why_tf="1h"):
    """Chooses the signal from the strategies that fired. Same rules as always: majority side, min_agree from 2+ families,
    one more against the higher-chart direction, ORB + VWAP count once, counter-trend skipped after a big run."""
    min_agree = MIN_AGREE if min_agree is None else min_agree
    res = {"best": None, "ideas": ideas, "agree": 0, "bias": X.bias, "why": ""}
    if not ideas:
        res["why"] = "no strategy fired"
        return res
    buys = [i for i in ideas if i["direction"] == "BUY"]
    sells = [i for i in ideas if i["direction"] == "SELL"]
    if len(buys) == len(sells):
        res["why"] = f"{len(buys)} BUY vs {len(sells)} SELL, no clear side"
        return res
    group = buys if len(buys) > len(sells) else sells
    d = group[0]["direction"]
    # Opening range breakout and VWAP both just follow a breakout: count them as ONE confirmation
    names = {i["name"] for i in group}
    if "orb" in names and "vwap" in names:
        group = [i for i in group if i["name"] != "vwap"]
    need = min_agree + (1 if (X.bias == "up" and d == "SELL") or (X.bias == "down" and d == "BUY") else 0)
    fams = {i["family"] for i in group}
    res["agree"] = len(group)
    if need > min_agree and X.a > 0:
        lk = n15[-look:]
        run = (X.px - min(x["low"] for x in lk)) if d == "BUY" else (max(x["high"] for x in lk) - X.px)
        if run > OVEREXT_ATR * X.a:
            res["why"] = (f"counter-trend {d} skipped: price already ran {run / X.a:.1f} ranges from the recent "
                          f"{'low' if d == 'BUY' else 'high'} ({why_tf} bias {X.bias})")
            return res
    if len(group) < need or len(fams) < 2:
        res["why"] = f"{len(group)} {d} ({', '.join(sorted(fams))}), need {need} from 2+ families ({why_tf} bias {X.bias})"
        return res
    order = {f: k for k, f in enumerate(["structure", "levels", "reversal", "retrace", "range", "momentum"])}
    group.sort(key=lambda i: (order[i["family"]], i["name"]))
    labels = [i["label"] for i in group][:3]
    res["best"] = {"name": group[0]["name"], "family": group[0]["family"], "direction": d,
                   "label": " + ".join(labels), "agree_labels": [i["label"] for i in group]}
    return res


def evaluate_pa(c1h, c15, c5, daily=None, now=None):
    X, ideas, n15, why = scan_pa(c1h, c15, c5, daily, now)
    if X is None:
        return {"best": None, "ideas": [], "agree": 0, "bias": "range", "why": why}
    return pick_pa(X, ideas, n15)


def evaluate_single(name, c_bias, c_sig, daily=None, now=None, sig_s=900, bias_s=3600):
    """One strategy of the bank on its own. Returns {"direction": "BUY"/"SELL"/None, "why": str, "bias": str}."""
    X, ideas, n_sig, why = scan_pa(c_bias, c_sig, c_sig, daily, now, sig_s=sig_s, bias_s=bias_s, c5_s=sig_s)
    if X is None:
        return {"direction": None, "why": why, "bias": "range"}
    for i in ideas:
        if i["name"] == name:
            return {"direction": i["direction"], "why": f"{i['label']} fired", "bias": X.bias}
    return {"direction": None, "why": f"{name} not firing", "bias": X.bias}


def evaluate_first(names, c_bias, c_sig, daily=None, now=None, sig_s=900, bias_s=3600):
    """Several strategies in order of preference (best win % first): the first one firing decides. A single name also works."""
    if isinstance(names, str):
        names = [names]
    X, ideas, n_sig, why = scan_pa(c_bias, c_sig, c_sig, daily, now, sig_s=sig_s, bias_s=bias_s, c5_s=sig_s)
    if X is None:
        return {"direction": None, "why": why, "bias": "range"}
    by = {}
    for i in ideas:
        by.setdefault(i["name"], i)
    for n in names:
        if n in by:
            return {"direction": by[n]["direction"], "why": f"{by[n]['label']} fired", "bias": X.bias, "name": n}
    return {"direction": None, "why": "none firing", "bias": X.bias}
