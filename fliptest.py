"""Flip test: for past closed signals, did the OPPOSITE side (same distances, mirrored) hit its target first?

Admin-triggered background job (/fliptest). Pulls the price path after each sampled signal
(MetaAPI M5 for forex-group pairs, Deriv public socket M1 for Volatility indices), simulates the
opposite trade with mirrored stop/target, and stores one row per signal in public.flip_test so the
result can be analysed and, if it holds up, added to the ML model as one extra input.
"""
import asyncio
import json
import random
import time
from datetime import datetime, timedelta, timezone

import requests

WINDOW_H = 24


def _ts(s):
    s = str(s).replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def first_hit(candles, side, entry, sl, tp):
    """candles oldest->newest, each {t,h,l}. Same-candle double touch counts as the stop (conservative)."""
    for c in candles:
        if side == "BUY":
            hs, ht = c["l"] <= sl, c["h"] >= tp
        else:
            hs, ht = c["h"] >= sl, c["l"] <= tp
        if hs:
            return "SL"
        if ht:
            return "TP"
    return "NONE"


def simulate(candles, direction, entry, sl, tp):
    d_sl, d_tp = abs(entry - sl), abs(tp - entry)
    orig = first_hit(candles, direction, entry, sl, tp)
    opp_side = "SELL" if direction == "BUY" else "BUY"
    if opp_side == "BUY":
        o_sl, o_tp = entry - d_sl, entry + d_tp
    else:
        o_sl, o_tp = entry + d_sl, entry - d_tp
    opp = first_hit(candles, opp_side, entry, o_sl, o_tp)
    return orig, opp


def _metaapi_path(token, acct, symbol, posted, retries=3):
    end = min(posted + timedelta(hours=WINDOW_H), datetime.now(timezone.utc))
    url = (f"https://mt-market-data-client-api-v1.new-york.agiliumtrade.ai/users/current/accounts/{acct}"
           f"/historical-market-data/symbols/{symbol}/timeframes/5m/candles"
           f"?startTime={end.strftime('%Y-%m-%dT%H:%M:%S.000Z')}&limit=300")
    for i in range(retries):
        r = requests.get(url, headers={"auth-token": token, "Accept": "application/json"}, timeout=60)
        if r.status_code == 429:
            time.sleep(5 * (i + 1))
            continue
        if r.status_code != 200:
            return None
        out = []
        for c in r.json() or []:
            t = _ts(c["time"])
            if t >= posted:
                out.append({"t": t, "h": float(c["high"]), "l": float(c["low"])})
        out.sort(key=lambda x: x["t"])
        return out
    return None


async def _deriv_paths(requests_list):
    """requests_list: [(key, symbol, posted)] -> {key: candles}. One socket, sequential calls."""
    import websockets
    from ml1m_live import DERIV_PUBLIC_WS
    res = {}
    n = 0
    async with websockets.connect(DERIV_PUBLIC_WS, open_timeout=15, close_timeout=3) as ws:
        for key, sym, posted in requests_list:
            end = min(posted + timedelta(hours=WINDOW_H), datetime.now(timezone.utc))
            try:
                await ws.send(json.dumps({"ticks_history": sym, "style": "candles", "granularity": 60,
                                          "start": int(posted.timestamp()), "end": int(end.timestamp())}))
                r = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                res[key] = [{"t": datetime.fromtimestamp(int(c["epoch"]), timezone.utc),
                             "h": float(c["high"]), "l": float(c["low"])} for c in (r.get("candles") or [])]
            except Exception as e:
                print(f"[FLIPTEST] deriv path failed {sym}: {type(e).__name__}: {str(e)[:80]}", flush=True)
            n += 1
            if n % 100 == 0:
                print(f"[FLIPTEST] deriv paths {n}/{len(requests_list)}", flush=True)
            await asyncio.sleep(0.15)
    return res


def _fetch(url, sb_headers):
    r = requests.get(url, headers=sb_headers(), timeout=30)
    return r.json() if r.status_code == 200 else []


def _save(rows, supabase_url, sb_headers):
    if not rows:
        return
    h = dict(sb_headers())
    h["Prefer"] = "resolution=merge-duplicates,return=minimal"
    for i in range(0, len(rows), 200):
        requests.post(f"{supabase_url}/rest/v1/flip_test?on_conflict=src,sig_id", headers=h,
                      data=json.dumps(rows[i:i + 200]), timeout=60)


def _summ(rows, label):
    n = len(rows)
    if not n:
        return f"{label}: no rows"
    cnt = lambda f: sum(1 for r in rows if f(r))
    wins_after_loss = [r for r in rows if r["orig_status"] == "SL_HIT"]
    wins_after_win = [r for r in rows if r["orig_status"] == "TP_HIT"]
    f = lambda xs: (f"{100.0 * sum(1 for r in xs if r['opp_outcome'] == 'TP') / len(xs):.1f}% (n={len(xs)})" if xs else "n/a")
    agree = cnt(lambda r: (r["orig_sim"] == "TP") == (r["orig_status"] == "TP_HIT") and r["orig_sim"] != "NONE")
    ok = cnt(lambda r: r["orig_sim"] != "NONE")
    return (f"{label}: n={n}. Opposite side wins overall {f(rows)}; after an original LOSS {f(wins_after_loss)}; "
            f"after an original WIN {f(wins_after_win)}. Path check: simulated original matches recorded result "
            f"{agree}/{ok}.")


def run(supabase_url, sb_headers, path_fn, pair_symbols, fx_n=400):
    """Blocking: call via asyncio.to_thread. pair_symbols: {PAIR_NAME: mt5_symbol};
    path_fn(mt5_symbol, posted) -> [{t,h,l}] after `posted` or None. Returns summary text."""
    random.seed(7)
    since = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S")
    out = []

    # ---- forex group ---------------------------------------------------
    fx = _fetch(f"{supabase_url}/rest/v1/signal_log?status=in.(TP_HIT,SL_HIT)&posted_at=gte.{since}"
                f"&select=id,pair_name,direction,entry_price,stop_loss,take_profit,posted_at,status,ml_features"
                f"&order=id.desc&limit=6000", sb_headers)
    seen, pool = set(), []
    for r in fx:
        if r["pair_name"] not in pair_symbols or not (r.get("entry_price") and r.get("stop_loss") and r.get("take_profit")):
            continue
        k = (r["pair_name"], r["direction"], str(r["posted_at"])[:13])
        if k in seen:
            continue
        seen.add(k)
        pool.append(r)
    random.shuffle(pool)
    rows = []
    for r in pool[:fx_n]:
        posted = _ts(r["posted_at"])
        if datetime.now(timezone.utc) - posted < timedelta(hours=WINDOW_H):
            continue
        path = path_fn(pair_symbols[r["pair_name"]], posted)
        time.sleep(0.5)
        if not path or len(path) < 20:
            continue
        o, p = simulate(path, r["direction"], float(r["entry_price"]), float(r["stop_loss"]), float(r["take_profit"]))
        rows.append({"src": "fx", "sig_id": r["id"], "pair": r["pair_name"], "direction": r["direction"],
                     "entry": float(r["entry_price"]), "sl": float(r["stop_loss"]), "tp": float(r["take_profit"]),
                     "posted_at": posted.isoformat(), "orig_status": r["status"], "orig_sim": o, "opp_outcome": p,
                     "features": r.get("ml_features")})
    _save(rows, supabase_url, sb_headers)
    out.append(_summ(rows, "Forex group"))
    print("[FLIPTEST] " + out[-1], flush=True)

    return "\n".join(out)


def run_deriv(supabase_url, sb_headers, index_symbols, dv_n=600):
    random.seed(11)
    since = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S")
    dv = _fetch(f"{supabase_url}/rest/v1/deriv_live_signals?status=in.(TP_HIT,SL_HIT)&posted_at=gte.{since}"
                f"&select=id,index_key,direction,entry_price,stop_loss,take_profit,posted_at,status,ml_features"
                f"&order=id.desc&limit=8000", sb_headers)
    seen, pool = set(), []
    for r in dv:
        if r["index_key"] not in index_symbols or not (r.get("entry_price") and r.get("stop_loss") and r.get("take_profit")):
            continue
        k = (r["index_key"], r["direction"], str(r["posted_at"])[:15])
        if k in seen:
            continue
        seen.add(k)
        pool.append(r)
    random.shuffle(pool)
    pool = [r for r in pool if datetime.now(timezone.utc) - _ts(r["posted_at"]) >= timedelta(hours=WINDOW_H)][:dv_n]
    reqs = [(r["id"], index_symbols[r["index_key"]], _ts(r["posted_at"])) for r in pool]
    paths = asyncio.run(_deriv_paths(reqs))
    rows = []
    for r in pool:
        path = paths.get(r["id"])
        if not path or len(path) < 20:
            continue
        o, p = simulate(path, r["direction"], float(r["entry_price"]), float(r["stop_loss"]), float(r["take_profit"]))
        rows.append({"src": "dv", "sig_id": r["id"], "pair": r["index_key"], "direction": r["direction"],
                     "entry": float(r["entry_price"]), "sl": float(r["stop_loss"]), "tp": float(r["take_profit"]),
                     "posted_at": _ts(r["posted_at"]).isoformat(), "orig_status": r["status"], "orig_sim": o,
                     "opp_outcome": p, "features": r.get("ml_features")})
    _save(rows, supabase_url, sb_headers)
    s = _summ(rows, "Deriv")
    print("[FLIPTEST] " + s, flush=True)
    return s
