"""
Read-only study: what happens if the bot takes EVERY burst, with no trend filter and a much lower bar for what
counts as a burst? Never places a trade, never touches the live path. Started by the bot only while BURST_ALL=1.
Prints "[BURSTALL] ..." lines.

Uses 1-minute candles from the Exness market-data feed (tick counts when the feed has them). Same trade rules as
the earlier burst study: entry at the NEXT candle's open, Aggressive Scalper stop (gold 50 pips, oil 25, BTC 25),
break-even after a full stop in profit then a trail of half a stop, closed at market after 60 candles, 0.1 of a
stop charged per trade for spread and slippage, one trade at a time per market.

Per row:  bursts/day = how many 1-minute candles qualify as a burst (before the one-trade-at-a-time rule)
          trades/day = trades actually taken
"""
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import burst_research as br  # noqa: E402

SYMBOLS = {"XAUUSDm": 5.0, "USOILm": 0.25, "BTCUSDm": 25 * 9.918}
_only = [x.strip().upper() for x in os.getenv("BURSTALL_SYMBOLS", "").split(",") if x.strip()]
if _only:
    SYMBOLS = {k: v for k, v in SYMBOLS.items() if k.upper() in _only}
DAYS = int(os.getenv("BURSTALL_DAYS", "60"))
BAR = 60


def log(msg):
    print(f"[BURSTALL] {msg}", flush=True)


def _ts(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()


def fetch(symbol, days):
    """1-minute bid candles from Exness, newest to oldest in pages of 990. Empty pages (weekends) are skipped."""
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
    saw_volume = False
    while end.timestamp() > target and pages < 200:
        start = end - timedelta(seconds=990 * BAR)
        q = urlencode({"instrument": pick, "price_type": "bid", "timeframe": "M1",
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
            if empties > 6:  # a weekend is about 3 empty pages; more than 6 in a row means the history ends
                break
        else:
            empties = 0
            for r in got:
                v = r.get("volume") or r.get("tick_volume") or r.get("tickVolume") or 0
                saw_volume = saw_volume or bool(v)
                t = int(_ts(r["time"]))
                rows.setdefault(t, [t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(v)])
        end = start
        time.sleep(0.6)
    out = [rows[k] for k in sorted(rows)]
    log(f"{symbol}: {len(out)} one-minute candles, tick counts {'available' if saw_volume else 'MISSING'}")
    return out


def bursts_per_day(t, o, h, l, c, v, kv, kr, body, have_v, days):
    rng = h - l
    med_r = br.rolling_median_prior(rng, br.LOOKBACK)
    ok = (rng > 0) & (np.abs(c - o) >= body * rng)
    if kr:
        ok &= (med_r > 0) & (rng >= kr * med_r)
    if kv and have_v:
        med_v = br.rolling_median_prior(v, br.LOOKBACK)
        ok &= (med_v > 0) & (v >= kv * med_v)
    ok[: br.LOOKBACK] = False
    return float(ok.sum()) / days


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {list(SYMBOLS)}, up to {DAYS} days of 1-minute candles, no trend filter")
    for sym, stop_d in SYMBOLS.items():
        try:
            rows = fetch(sym, DAYS)
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} candles, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c, v = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
        have_v = float(v.sum()) > 0 and float(np.median(v)) > 0
        # trading days only (weekends have no candles)
        days = max(1.0, len(np.unique((t // 86400).astype(int))))
        log(f"{sym}: {len(rows)} candles over {days:.0f} trading days; stop {stop_d:g} price units")
        # (ticks multiple, range multiple, minimum body share of the candle's range)
        grid = [(0, 0, 0.0), (0, 1.5, 0.0), (0, 2, 0.0), (0, 3, 0.0), (1.5, 0, 0.0), (2, 0, 0.0), (3, 0, 0.0),
                (0, 2, 0.6), (2, 0, 0.6), (3, 0, 0.6), (3, 3, 0.6)]
        for kv, kr, body in grid:
            if kv and not have_v:
                continue
            br.BODY_FRAC = body
            nb = bursts_per_day(t, o, h, l, c, v, kv, kr, body, have_v, days)
            label = f"body>={body:g} ticks>={kv:g}x range>={kr:g}x"
            for side in ("follow", "fade"):
                tr = br.simulate(t, o, h, l, c, v, kv, kr, stop_d, have_v, side, False, 0.0)
                s = br.stats(tr)
                if not s or s[0] < 15:
                    log(f"{sym} | {label} | {side}: bursts/day={nb:.1f} too few trades ({0 if not s else s[0]})")
                    continue
                cut = int(len(tr) * 0.7)
                s1, s2 = br.stats(tr[:cut]), br.stats(tr[cut:])
                log(f"{sym} | {label} | {side}: bursts/day={nb:.1f} trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% "
                    f"net={s[2] * 100:+.0f}% of stop per trade | first70% {(s1[2] * 100) if s1 else float('nan'):+.0f}% "
                    f"last30% {(s2[2] * 100) if s2 else float('nan'):+.0f}%")
    log("done")


if __name__ == "__main__":
    main()
