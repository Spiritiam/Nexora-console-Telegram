"""
Signal odds: for every signal that is logged (scheduled and manual), simulate 10,000 possible futures
by re-drawing the pair's own recent 15-minute candles, and record how often the signal's target is hit
before its stop (sim_tp_odds), how often the stop comes first (sim_sl_odds), and the rest is "still open".

RECORD-ONLY. It runs in one background thread, after the signal is already logged and posted, and it
never blocks, delays, changes or skips a signal. Any failure is swallowed. Switch off with SIGNAL_ODDS=0.
The numbers are stored on the signal_log row so they can be compared with real outcomes later.
"""
import os
import time
import queue
import threading
from datetime import datetime, timezone

import numpy as np
import requests

SIMS = 10000
HORIZON = 192        # 15-minute bars = 48 hours
LOOKBACK = 960       # bars drawn from (~10 trading days)
MIN_BARS = 300
BAR_SECONDS = 900

_Q = queue.Queue(maxsize=300)
_BARS = {}           # symbol -> (fetched_at, array of [t, o, h, l, c])
_RES = {}            # (pair, dir, sl_f, tp_f, last_bar_t) -> (p_tp, p_sl)
_STATE = {"started": False, "done": 0, "skipped": 0, "failed": 0}
_LOCK = threading.Lock()


def _log(msg):
    print(f"[ODDS] {msg}", flush=True)


def simulate(hist, sl_f, tp_f, direction, sims=SIMS, horizon=HORIZON, seed=None):
    """hist: array of [t, o, h, l, c], oldest first. Returns (p_target_first, p_stop_first).
    Stop is assumed first when one candle touches both (cautious)."""
    rng = np.random.default_rng(seed)
    o, h, l, c = hist[:, 1], hist[:, 2], hist[:, 3], hist[:, 4]
    rh, rl, rc = h / o - 1, l / o - 1, c / o - 1
    if direction == "SELL":              # mirror, so that "up" is always the winning side
        rh, rl, rc = -rl, -rh, -rc
    idx = rng.integers(0, len(o), size=(horizon, sims))
    p = np.ones(sims)
    alive = np.ones(sims, bool)
    tp_hit = np.zeros(sims, bool)
    sl_hit = np.zeros(sims, bool)
    up, dn = 1 + tp_f, 1 - sl_f
    for k in range(horizon):
        j = idx[k]
        lo = p * (1 + rl[j])
        hi = p * (1 + rh[j])
        s_hit = alive & (lo <= dn)
        t_hit = alive & ~s_hit & (hi >= up)
        sl_hit |= s_hit
        tp_hit |= t_hit
        alive &= ~(s_hit | t_hit)
        p = p * (1 + rc[j])
        if not alive.any():
            break
    return float(tp_hit.mean()), float(sl_hit.mean())


def _parse_time(v):
    try:
        if isinstance(v, (int, float)):
            return float(v) / 1000.0 if v > 1e11 else float(v)
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(
            tzinfo=timezone.utc).timestamp() if "+" not in str(v) and "Z" not in str(v) else \
            datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _bars(symbol, candle_fn):
    now = time.time()
    hit = _BARS.get(symbol)
    if hit and now - hit[0] < BAR_SECONDS:
        return hit[1]
    rows = candle_fn(symbol, "15min", LOOKBACK + 10)
    out = []
    for r in rows or []:
        try:
            t = _parse_time(r["time"])
            if t is None:
                continue
            out.append([t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])])
        except Exception:
            continue
    if not out:
        return hit[1] if hit else None
    out.sort(key=lambda x: x[0])
    arr = np.array([b for b in out if b[0] + BAR_SECONDS <= now], dtype=float)   # closed bars only
    if len(arr) < MIN_BARS or now - arr[-1][0] > 3 * 3600:
        return None
    _BARS[symbol] = (now, arr)
    return arr


def _patch(sb_url, headers, signal_id, body):
    h = dict(headers)
    h["Content-Type"] = "application/json"
    r = requests.patch(f"{sb_url}/rest/v1/signal_log?id=eq.{signal_id}", headers=h, json=body, timeout=10)
    return r.status_code in (200, 204)


def _work(item):
    sid, pair, direction, entry, sl, tp, symbol, candle_fn, sb_url, sb_headers, source = item
    sl_f, tp_f = abs(entry - sl) / entry, abs(tp - entry) / entry
    if sl_f <= 0 or tp_f <= 0 or sl_f > 0.05 or tp_f > 0.1:
        _STATE["skipped"] += 1
        return
    arr = _bars(symbol, candle_fn)
    if arr is None:
        _STATE["skipped"] += 1
        return
    key = (pair, direction, round(sl_f, 4), round(tp_f, 4), int(arr[-1][0]))
    res = _RES.get(key)
    if res is None:
        res = simulate(arr[-LOOKBACK:], sl_f, tp_f, direction)
        if len(_RES) > 500:
            _RES.clear()
        _RES[key] = res
    ok = _patch(sb_url, sb_headers, sid, {"sim_tp_odds": round(res[0], 4), "sim_sl_odds": round(res[1], 4)})
    _STATE["done"] += 1
    if source == "scheduled" or _STATE["done"] % 200 == 0:
        _log(f"signal {sid} {pair} {direction}: target first {res[0]*100:.1f}%, stop first {res[1]*100:.1f}% "
             f"(saved={ok}; {_STATE['done']} done, {_STATE['skipped']} skipped, {_STATE['failed']} failed)")


def _loop():
    while True:
        item = _Q.get()
        try:
            _work(item)
        except Exception as e:
            _STATE["failed"] += 1
            if _STATE["failed"] <= 5 or _STATE["failed"] % 100 == 0:
                _log(f"failed: {type(e).__name__}: {str(e)[:120]}")


def submit(signal_id, payload, symbol, candle_fn, sb_url, sb_headers, source="scheduled"):
    """Queue one signal. Returns immediately; never raises."""
    try:
        if os.getenv("SIGNAL_ODDS", "1") != "1" or not signal_id or not symbol:
            return
        item = (signal_id, payload["pair_name"], payload["direction"], float(payload["entry_price"]),
                float(payload["stop_loss"]), float(payload["take_profit"]), symbol, candle_fn,
                sb_url, sb_headers(), source)
        with _LOCK:
            if not _STATE["started"]:
                threading.Thread(target=_loop, name="signal-odds", daemon=True).start()
                _STATE["started"] = True
        _Q.put_nowait(item)
    except Exception:
        pass


def decide_direction(symbol, candle_fn, sl_f, tp_f, pair="", fallback="BUY", margin=None):
    """Run the simulation for BUY and SELL with the signal's own stop/target distances and return
    (direction, p_buy, p_sell), where p is the chance the target is hit before the stop.
    The simulation's side is used when it leads by at least `margin` (default 1.5 percentage points,
    about 2.5x the sampling noise of 10,000 paths); otherwise `fallback` (the caller's own direction) stands.
    Both sides use the same random draws, so the comparison is not affected by sampling noise.
    Returns None if the simulation can't run (no data etc.). Never raises."""
    try:
        if os.getenv("SIGNAL_ODDS_DECIDE", "1") != "1" or not symbol or sl_f <= 0 or tp_f <= 0 \
                or sl_f > 0.05 or tp_f > 0.1:
            return None
        if margin is None:
            margin = float(os.getenv("SIGNAL_ODDS_MARGIN", "0.015"))
        arr = _bars(symbol, candle_fn)
        if arr is None:
            return None
        seed = int(arr[-1][0]) % (2 ** 32)
        res = {}
        for d in ("BUY", "SELL"):
            key = (pair, d, round(sl_f, 4), round(tp_f, 4), int(arr[-1][0]), "d")
            r = _RES.get(key)
            if r is None:
                r = simulate(arr[-LOOKBACK:], sl_f, tp_f, d, seed=seed)
                if len(_RES) > 500:
                    _RES.clear()
                _RES[key] = r
            res[d] = r[0] / (r[0] + r[1]) if (r[0] + r[1]) > 0 else 0.0
        lead = "BUY" if res["BUY"] > res["SELL"] else "SELL"
        if abs(res["BUY"] - res["SELL"]) < margin:
            return fallback, res["BUY"], res["SELL"]
        return lead, res["BUY"], res["SELL"]
    except Exception as e:
        _log(f"decide failed: {type(e).__name__}: {str(e)[:100]}")
        return None


def deriv_candles(symbol, interval, n):
    """15-minute candles for a Deriv synthetic index from Deriv's public data socket (no login).
    Same row shape as the Exness helper. Returns [] on any failure."""
    try:
        import asyncio
        import json
        import websockets
        from ml1m_live import DERIV_PUBLIC_WS

        async def go():
            async with websockets.connect(DERIV_PUBLIC_WS, open_timeout=10, close_timeout=3) as ws:
                await ws.send(json.dumps({"ticks_history": symbol, "style": "candles", "granularity": 900,
                                          "count": int(n), "end": "latest"}))
                r = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                return [{"time": int(c["epoch"]), "open": float(c["open"]), "high": float(c["high"]),
                         "low": float(c["low"]), "close": float(c["close"])} for c in (r.get("candles") or [])]
        return asyncio.run(go())
    except Exception as e:
        _log(f"deriv candles failed {symbol}: {type(e).__name__}: {str(e)[:80]}")
        return []


if __name__ == "__main__":
    # self-test on synthetic data: a driftless random walk with a 1:3 stop/target should hit the target ~25%
    rng = np.random.default_rng(1)
    n = 960
    steps = rng.normal(0, 0.0008, n)
    c = 100 * np.cumprod(1 + steps)
    o = np.roll(c, 1); o[0] = 100
    hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0003, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0003, n)))
    hist = np.column_stack([np.arange(n) * 900.0, o, hi, lo, c])
    t0 = time.time()
    a, b = simulate(hist, 0.005, 0.015, "BUY", seed=3)
    s, u = simulate(hist, 0.005, 0.015, "SELL", seed=3)
    print(f"BUY target first {a:.3f}, stop first {b:.3f} | SELL target first {s:.3f}, stop first {u:.3f} "
          f"({time.time() - t0:.2f}s for two 10,000-path runs)")
