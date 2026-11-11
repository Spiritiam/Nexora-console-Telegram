"""
Live side of the 1-minute ML entry for the Aggressive Scalper (Exness and Deriv).

Every minute, for each market, it reads the latest closed 1-minute candles, builds the price features,
asks the stored models what a BUY and a SELL would return under the scalper's real exit rules, and
returns the better side if it clears the model's threshold. The caller then enters at market, at once.

Models live in Supabase (table ml1m_store), trained by ml1m_research.py. Until a model exists the caller
gets ready=False and keeps using its previous entry.
"""
import os
import json
import time
import zlib
import base64
import pickle
import asyncio
import math
from datetime import datetime, timezone

import numpy as np
import requests

import ml1m_research as R

DERIV_PUBLIC_WS = R.DERIV_PUBLIC_WS
MODELS = {}
_STATE = {"loaded_at": 0.0, "loading": False}
_BARS = {}          # market key -> {"bars": [...], "fetched": ts, "fail_until": ts}
_DECISION = {}      # (model key, last bar t) -> decision
_LOCKS = {}
_WS = {"ws": None, "lock": None, "block_until": 0.0}


def _log(msg):
    print(f"[ML1M] {msg}", flush=True)


# ---------------------------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------------------------
def _load_models_sync():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        return
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    out = {}
    try:
        r = requests.get(f"{url}/rest/v1/ml1m_store?key=like.model:*&select=key,data,updated_at", headers=h, timeout=60)
        if r.status_code != 200:
            _log(f"live: model load HTTP {r.status_code}")
            return
        for row in r.json():
            try:
                m = pickle.loads(zlib.decompress(base64.b64decode(row["data"])))
                if m.get("ver") != R.MODEL_VERSION:
                    continue          # older model trained with a less cautious exit simulation
                out[row["key"]] = m
            except Exception as e:
                _log(f"live: could not read {row.get('key')}: {type(e).__name__}")
    except Exception as e:
        _log(f"live: model load failed {type(e).__name__}")
        return
    if out:
        new = sorted(set(out) - set(MODELS))
        MODELS.clear()
        MODELS.update(out)
        if new:
            _log(f"live: {len(MODELS)} entry model(s) loaded: {', '.join(sorted(MODELS))}")
    _STATE["loaded_at"] = time.time()


async def _ensure_models():
    age = time.time() - _STATE["loaded_at"]
    if (not MODELS and age < 60) or (MODELS and age < 1200) or _STATE["loading"]:
        return
    _STATE["loading"] = True
    try:
        await asyncio.to_thread(_load_models_sync)
        _STATE["loaded_at"] = time.time()
    finally:
        _STATE["loading"] = False


# Markets that use the ML entry. Everything else keeps its old entry, whatever models exist in the store.
ALLOWED = {"R_75", "R_100", "USOILm", "BTCUSDm"}


def _pick_model(symbol, frac=None):
    if symbol not in ALLOWED:
        return None, None
    if frac is None:
        return MODELS.get(f"model:{symbol}:x20"), f"model:{symbol}:x20"
    best, bk, bd = None, None, 1e9
    for k, m in MODELS.items():
        parts = k.split(":")
        if len(parts) == 3 and parts[1] == symbol and parts[2] != "x20":
            d = abs(math.log(max(frac, 1e-6) / float(parts[2])))
            if d < bd:
                best, bk, bd = m, k, d
    return best, bk


# ---------------------------------------------------------------------------------------------
# decision
# ---------------------------------------------------------------------------------------------
def _decide(model, mkey, bars):
    """bars: closed 1-minute candles [[epoch,o,h,l,c], ...] oldest first."""
    last_t = int(bars[-1][0])
    ck = (mkey, last_t)
    hit = _DECISION.get(ck)
    if hit is not None:
        return hit
    arr = np.array(bars[-220:], dtype=float)
    t, o, h, l, c = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4]
    # one extra bar stands for the minute we are about to trade in; its features come from the last closed bar
    t = np.append(t, t[-1] + 60)
    o, h, l, c = (np.append(x, c[-1]) for x in (o, h, l, c))
    X, names = R.build_features(t, o, h, l, c, pure=False)
    x = X[-1]
    # Default: no minimum edge - always take the better side, so a trade follows the last one within a minute.
    # ML1M_MIN_EDGE (in units of the stop, e.g. 0.03) puts a floor back without a code change.
    try:
        theta = float(os.getenv("ML1M_MIN_EDGE", "-9"))
    except ValueError:
        theta = -9.0
    dec = {"ready": True, "dir": None, "bar_t": last_t, "sb": None, "ss": None, "theta": theta}
    if not np.isnan(x).any() and list(names) == list(model["names"]):
        sb = float(model["mb"].predict(x.reshape(1, -1))[0])
        ss = float(model["ms"].predict(x.reshape(1, -1))[0])
        dec.update({"sb": round(sb, 3), "ss": round(ss, 3)})
        if max(sb, ss) > theta:
            dec["dir"] = "BUY" if sb >= ss else "SELL"
    if len(_DECISION) > 400:
        for k in list(_DECISION)[:200]:
            _DECISION.pop(k, None)
    _DECISION[ck] = dec
    _STATE["n_dec"] = _STATE.get("n_dec", 0) + 1
    if dec["dir"]:
        _log(f"live: {mkey} -> {dec['dir']} (buy {dec['sb']}, sell {dec['ss']}, threshold {theta})")
    return dec


def _lock(key):
    lk = _LOCKS.get(key)
    if lk is None:
        lk = _LOCKS[key] = asyncio.Lock()
    return lk


def _closed(bars, now):
    return [b for b in bars if b[0] + 60 <= now]


# ---------------------------------------------------------------------------------------------
# Deriv: one shared connection to Deriv's public data socket (no login)
# ---------------------------------------------------------------------------------------------
async def _deriv_fetch(symbol):
    import websockets
    if _WS["lock"] is None:
        _WS["lock"] = asyncio.Lock()
    async with _WS["lock"]:
        for attempt in (1, 2):
            try:
                if _WS["ws"] is None:
                    _WS["ws"] = await websockets.connect(DERIV_PUBLIC_WS, open_timeout=10, close_timeout=3)
                ws = _WS["ws"]
                await ws.send(json.dumps({"ticks_history": symbol, "style": "candles", "granularity": 60,
                                          "count": 230, "end": "latest"}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if resp.get("error"):
                    _log(f"live: Deriv data error for {symbol}: {str(resp['error'].get('code'))} {str(resp['error'].get('message'))[:100]}")
                    return None
                cs = resp.get("candles") or []
                return [[int(c["epoch"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])] for c in cs]
            except Exception as e:
                try:
                    if _WS["ws"] is not None:
                        await _WS["ws"].close()
                except Exception:
                    pass
                _WS["ws"] = None
                if attempt == 2:
                    _log(f"live: Deriv data connection failed ({type(e).__name__})")
                    return None
    return None


async def deriv_decision(symbol, sl_frac, blocked=None):
    """Returns {"ready", "dir", "bar_t", ...}. ready=False -> no model yet, caller keeps its old entry."""
    await _ensure_models()
    model, mkey = _pick_model(symbol, sl_frac)
    if not model:
        return {"ready": False}
    async with _lock("d:" + symbol):
        now = time.time()
        b = _BARS.setdefault("d:" + symbol, {"bars": [], "fetched": 0.0, "fail_until": 0.0})
        last_t = b["bars"][-1][0] if b["bars"] else 0
        if now >= b["fail_until"] and (not b["bars"] or now >= last_t + 122) and now - b["fetched"] > 8:
            if blocked and blocked():
                b["fail_until"] = now + 30
            else:
                got = await _deriv_fetch(symbol)
                b["fetched"] = time.time()
                if got:
                    b["bars"] = _closed(got, time.time())
                else:
                    b["fail_until"] = time.time() + 45
        bars = b["bars"]
        if len(bars) < 120 or now - bars[-1][0] > 240:
            return {"ready": False}     # no fresh candles: caller keeps trading with its previous entry
        return _decide(model, mkey, bars)


# ---------------------------------------------------------------------------------------------
# Exness: candles come from the bot's own Exness data helper
# ---------------------------------------------------------------------------------------------
def _exness_bars_sync(candle_fn, mt5_symbol):
    rows = candle_fn(mt5_symbol, "1min", 230)
    out = []
    for r in rows or []:
        try:
            ts = datetime.fromisoformat(str(r["time"]).replace("Z", "+00:00")).timestamp()
            out.append([int(ts), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])])
        except Exception:
            continue
    return out


async def exness_decision(pair_key, mt5_symbol, candle_fn):
    await _ensure_models()
    model, mkey = _pick_model(mt5_symbol)
    if not model:
        return {"ready": False}
    async with _lock("x:" + pair_key):
        now = time.time()
        b = _BARS.setdefault("x:" + pair_key, {"bars": [], "fetched": 0.0, "fail_until": 0.0})
        last_t = b["bars"][-1][0] if b["bars"] else 0
        if now >= b["fail_until"] and (not b["bars"] or now >= last_t + 122) and now - b["fetched"] > 10:
            got = await asyncio.to_thread(_exness_bars_sync, candle_fn, mt5_symbol)
            b["fetched"] = time.time()
            if got:
                b["bars"] = _closed(got, time.time())
            else:
                b["fail_until"] = time.time() + 10
        bars = b["bars"]
        if len(bars) < 120 or now - bars[-1][0] > 200:
            return {"ready": False}     # no fresh candles: caller keeps trading with its previous entry
        return _decide(model, mkey, bars)
