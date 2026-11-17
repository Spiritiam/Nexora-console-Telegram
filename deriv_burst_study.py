"""Read-only study on Deriv Volatility indices (no trades): 1-minute bursts, same rules as burst_research.py.
Synthetic indices have no tick-count variation, so only the range burst is used. Prints "[DBURST] ..." lines."""
import json
import time

import numpy as np
import websockets

from burst_research import simulate, stats

INDICES = {"R_10": "Volatility 10", "R_25": "Volatility 25", "R_50": "Volatility 50", "R_75": "Volatility 75", "R_100": "Volatility 100"}
DAYS = 20
STOP_PCTS = (0.003, 0.01)  # stop as a share of price (0.3% and 1%)


def log(m):
    print(f"[DBURST] {m}", flush=True)


async def _fetch(ws_url, symbol, days):
    rows, end = {}, "latest"
    need = days * 1440
    async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
        for _ in range(12):
            await ws.send(json.dumps({"ticks_history": symbol, "style": "candles", "granularity": 60, "count": 5000, "end": end}))
            r = json.loads(await ws.recv())
            if "error" in r:
                log(f"{symbol} error: {r['error'].get('message')}")
                break
            cs = r.get("candles") or []
            if not cs:
                break
            for c in cs:
                rows[int(c["epoch"])] = (int(c["epoch"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), 0.0)
            first = min(rows)
            if len(rows) >= need or len(cs) < 100:
                break
            end = first - 60
    return [rows[k] for k in sorted(rows)]


async def run(get_ws_url):
    log(f"start: {list(INDICES)}, up to {DAYS} days of 1-minute bars")
    for sym, name in INDICES.items():
        try:
            url = await get_ws_url()
            rows = await _fetch(url, sym, DAYS)
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {str(e)[:100]}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} bars, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c, v = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
        days = max(1.0, (t[-1] - t[0]) / 86400.0)
        px = float(np.median(c))
        log(f"{name}: {len(rows)} bars over {days:.1f} days")
        for sp in STOP_PCTS:
            stop_d = px * sp
            for kr in (3, 4):
                for label, side, trend in (("follow", "follow", False), ("fade", "fade", False), ("follow + trend", "follow", True), ("fade + trend", "fade", True)):
                    tr = simulate(t, o, h, l, c, v, 0, kr, stop_d, False, side, trend, 0.0)
                    s = stats(tr)
                    if not s or s[0] < 15:
                        log(f"{name} | stop {sp * 100:.1f}% | range>={kr}x | {label}: too few trades ({0 if not s else s[0]})")
                        continue
                    cut = int(len(tr) * 0.7)
                    a1, a2 = stats(tr[:cut]), stats(tr[cut:])
                    log(f"{name} | stop {sp * 100:.1f}% | range>={kr}x | {label}: trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% "
                        f"net={s[2] * 100:+.0f}% of stop per trade | first70% net={(a1[2] * 100) if a1 else float('nan'):+.0f}% "
                        f"last30% net={(a2[2] * 100) if a2 else float('nan'):+.0f}%")
    log("done")
