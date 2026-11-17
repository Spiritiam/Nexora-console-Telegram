"""
Read-only study: do sudden bursts of activity on 1-minute candles (tick count and/or range far above
the last 30 minutes) continue far enough to beat the Aggressive Scalper stop?

Runs as its own low-priority process (started by the bot only while BURST_RESEARCH=1 is set). It never
places a trade and never touches the live path. Prints "[BURST] ..." lines.

Rules of the test (no look-ahead, pessimistic):
  - A burst is judged on a CLOSED 1-minute bar. Entry is the NEXT bar's open, in the direction of the burst bar.
  - One trade at a time per market.
  - Stop = the Aggressive Scalper stop (gold 50 pips, oil 25, BTC 25). Once price is a full stop-distance in
    profit the stop moves to break-even, then trails half a stop-distance behind. No take profit.
  - Inside a bar the stop is checked first. A trade open for 60 bars is closed at market.
  - Every trade is charged 0.1R for spread and slippage.
"""
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from strategy_research import fetch_h1  # noqa: E402  (1000-bar paging, cache, tickVolume kept)

SYMBOLS = {  # symbol -> stop distance in PRICE units (the bot's own pips: gold 0.1, oil 0.01, BTC 9.918)
    "XAUUSDm": 5.0,
    "USOILm": 0.25,
    "BTCUSDm": 25 * 9.918,
    # BTC priced in gold: stop given as a share of price (0.1%, 0.2%, 0.3%) since its pip size is not known
    "BTCXAUm@0.001": 0, "BTCXAUm@0.002": 0, "BTCXAUm@0.003": 0,
}
ONLY = os.getenv("BURST_ONLY", "").strip()  # e.g. "BTCXAUm": run just that symbol
DAYS = int(os.getenv("BURST_DAYS", "20"))
LOOKBACK = 30
HORIZON = 60
COST_R = 0.10
BODY_FRAC = 0.6
# (tick-count multiple, range multiple); 0 = not used
GRID = [(0, 0), (2, 0), (3, 0), (0, 2), (0, 3), (2, 2), (3, 3), (4, 4)]


def log(msg):
    print(f"[BURST] {msg}", flush=True)


def rolling_median_prior(x, n):
    out = np.full(len(x), np.nan)
    for i in range(n, len(x)):
        out[i] = np.median(x[i - n:i])
    return out


def ema_arr(x, n):
    out = np.empty(len(x))
    k = 2.0 / (n + 1)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def simulate(t, o, h, l, c, v, kv, kr, stop_d, have_v, side="follow", trend=False, env=0.0):
    rng = h - l
    if trend:
        e25, e50, e100 = ema_arr(c, 25), ema_arr(c, 50), ema_arr(c, 100)
    if env:
        cs = np.concatenate([[0.0], np.cumsum(c)])
    med_r = rolling_median_prior(rng, LOOKBACK)
    med_v = rolling_median_prior(v, LOOKBACK) if have_v else None
    trades = []
    i = LOOKBACK
    n = len(c)
    while i < n - 2:
        r = rng[i]
        body = abs(c[i] - o[i])
        ok = r > 0 and body >= BODY_FRAC * r
        if ok and kr:
            ok = med_r[i] > 0 and r >= kr * med_r[i]
        if ok and kv:
            ok = have_v and med_v[i] > 0 and v[i] >= kv * med_v[i]
        if not ok:
            i += 1
            continue
        d = 1 if c[i] > o[i] else -1
        if trend:
            fan_up = e25[i] > e50[i] > e100[i]
            fan_dn = e25[i] < e50[i] < e100[i]
            if (d > 0 and not fan_up) or (d < 0 and not fan_dn):
                i += 1
                continue
        if env:
            sma = (cs[i] - cs[i - 20]) / 20.0  # the 20 closes BEFORE the burst bar
            if not ((d > 0 and c[i] > sma * (1 + env)) or (d < 0 and c[i] < sma * (1 - env))):
                i += 1
                continue
        if side == "fade":
            d = -d
        e = o[i + 1]
        stop = e - d * stop_d
        best = e
        exit_p = None
        j = i + 1
        end = min(i + 1 + HORIZON, n)
        while j < end:
            if d > 0:
                if o[j] <= stop:
                    exit_p = o[j]; break
                if l[j] <= stop:
                    exit_p = stop; break
                best = max(best, h[j])
            else:
                if o[j] >= stop:
                    exit_p = o[j]; break
                if h[j] >= stop:
                    exit_p = stop; break
                best = min(best, l[j])
            if (best - e) * d >= stop_d:
                be = e
                tr = best - d * 0.5 * stop_d
                new = max(be, tr) if d > 0 else min(be, tr)
                stop = max(stop, new) if d > 0 else min(stop, new)
            j += 1
        if exit_p is None:
            exit_p = c[min(j, n - 1) - 1] if j >= end else c[j]
        r_mult = (exit_p - e) * d / stop_d - COST_R
        trades.append((t[i], r_mult))
        i = max(j, i + 1) + 1
    return trades


def stats(trades):
    if not trades:
        return None
    r = np.array([x[1] for x in trades])
    wins, losses = r[r > 0], r[r <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return len(r), float((r > 0).mean() * 100), float(r.mean()), float(pf)



# ----------------------------------------------------------------------------------------------
# LIVE-TICK STUDY (gold and oil): speed of price inside the minute, from MetaApi historical ticks
# ----------------------------------------------------------------------------------------------
TICK_HOURS = float(os.getenv("BURST_TICK_HOURS", "36"))
TICK_MAX_PAGES = int(os.getenv("BURST_TICK_PAGES", "300"))
TICK_STOPS = {"XAUUSDm": 5.0, "USOILm": 0.25}
MD_HOST = "https://mt-market-data-client-api-v1.new-york.agiliumtrade.ai"


def fetch_ticks(sym, hours):
    import requests
    token, acct = os.getenv("METAAPI_TOKEN"), os.getenv("METAAPI_ACCOUNT_ID")
    if not token or not acct:
        log("tick study: MetaApi credentials missing")
        return None
    headers = {"auth-token": token, "Accept": "application/json"}
    cur = datetime.fromtimestamp(time.time() - hours * 3600, tz=timezone.utc)
    tt, bb, aa = [], [], []
    pages = 0
    fails = 0
    while pages < TICK_MAX_PAGES:
        url = (f"{MD_HOST}/users/current/accounts/{acct}/historical-market-data/symbols/{sym}/ticks"
               f"?startTime={cur.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]}Z&offset=0&limit=1000")
        try:
            r = requests.get(url, headers=headers, timeout=60)
        except Exception as e:
            fails += 1
            if fails > 4:
                break
            time.sleep(4 * fails)
            continue
        if r.status_code in (429, 502, 503, 504):
            fails += 1
            if fails > 6:
                break
            time.sleep(6 * fails)
            continue
        if r.status_code != 200:
            log(f"{sym} ticks HTTP {r.status_code}: {r.text[:160]}")
            return None if not tt else (np.array(tt), np.array(bb), np.array(aa))
        fails = 0
        raw = r.json()
        if not raw:
            break
        for k in raw:
            try:
                tt.append(datetime.fromisoformat(str(k["time"]).replace("Z", "+00:00")).timestamp())
                bb.append(float(k["bid"]))
                aa.append(float(k["ask"]))
            except Exception:
                pass
        pages += 1
        last = datetime.fromisoformat(str(raw[-1]["time"]).replace("Z", "+00:00"))
        if last.timestamp() <= cur.timestamp() and len(raw) < 1000:
            break
        cur = datetime.fromtimestamp(last.timestamp() + 0.001, tz=timezone.utc)
        if cur.timestamp() > time.time() - 60:
            break
        time.sleep(0.5)
    if not tt:
        return None
    return np.array(tt), np.array(bb), np.array(aa)


def tick_study(sym, stop_d):
    got = fetch_ticks(sym, TICK_HOURS)
    if not got:
        log(f"{sym}: no tick data from MetaApi")
        return
    tt, bb, aa = got
    span_h = (tt[-1] - tt[0]) / 3600.0
    log(f"{sym}: {len(tt)} ticks over {span_h:.1f} hours ({len(tt) / max(1.0, span_h * 3600):.1f} ticks/second)")
    t0 = int(tt[0])
    n = int(tt[-1]) - t0 + 1
    idx = np.searchsorted(tt, np.arange(t0, t0 + n) + 1.0) - 1  # last tick at or before each second
    idx = np.clip(idx, 0, len(tt) - 1)
    bid, ask = bb[idx], aa[idx]
    mid = (bid + ask) / 2
    cnt = np.bincount((tt - t0).astype(int), minlength=n).astype(float)
    csum = np.concatenate([[0.0], np.cumsum(cnt)])
    W = 10
    speed = np.zeros(n)
    speed[W:] = mid[W:] - mid[:-W]
    rate = np.zeros(n)
    rate[W:] = csum[W + 1:] - csum[1:n - W + 1]
    base_s = np.full(n, np.nan)
    base_r = np.full(n, np.nan)
    for i in range(1800, n, 1):
        base_s[i] = np.median(np.abs(speed[i - 1800:i:10]))
        base_r[i] = np.median(rate[i - 1800:i:10])
    spread_med = float(np.median(ask - bid))
    log(f"{sym}: median spread {spread_med:g} price units ({spread_med / stop_d * 100:.0f}% of the stop)")
    cfgs = [(4, 0), (6, 0), (4, 3), (6, 3)]  # (speed multiple, tick-rate multiple)
    for ks, kt in cfgs:
        for side in ("follow", "fade"):
            trades = []
            i = 1800
            while i < n - 400:
                sp = abs(speed[i])
                ok = base_s[i] > 0 and sp >= ks * base_s[i] and sp >= 0.2 * stop_d
                if ok and kt:
                    ok = base_r[i] > 0 and rate[i] >= kt * base_r[i]
                if not ok:
                    i += 1
                    continue
                d = 1 if speed[i] > 0 else -1
                if side == "fade":
                    d = -d
                j = i + 1
                e = ask[j] if d > 0 else bid[j]
                stop = e - d * stop_d
                best = e
                exit_p = None
                end = min(j + 300, n)
                k = j + 1
                while k < end:
                    px = bid[k] if d > 0 else ask[k]
                    if (d > 0 and px <= stop) or (d < 0 and px >= stop):
                        exit_p = px
                        break
                    best = max(best, px) if d > 0 else min(best, px)
                    if (best - e) * d >= stop_d:
                        new = max(e, best - 0.5 * stop_d) if d > 0 else min(e, best + 0.5 * stop_d)
                        stop = max(stop, new) if d > 0 else min(stop, new)
                    k += 1
                if exit_p is None:
                    exit_p = bid[min(k, n - 1)] if d > 0 else ask[min(k, n - 1)]
                trades.append((t0 + i, (exit_p - e) * d / stop_d - 0.05))  # spread is already in entry/exit
                i = k + 30
            st = stats(trades)
            lab = f"price speed>={ks}x" + (f" + tick rate>={kt}x" if kt else "")
            if not st or st[0] < 10:
                log(f"{sym} TICKS | {lab} | {side}: too few trades ({0 if not st else st[0]})")
                continue
            log(f"{sym} TICKS | {lab} | {side}: trades={st[0]} win={st[1]:.0f}% net={st[2] * 100:+.0f}% of stop per trade")


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    log(f"start: {list(SYMBOLS)}, {DAYS} days of 1-minute bars")
    for sym_key, stop_d in SYMBOLS.items():
        sym, _, frac = sym_key.partition("@")
        if ONLY and sym != ONLY:
            continue
        if not ONLY and frac:
            continue
        try:
            rows = fetch_h1(sym, DAYS, "1m")
        except Exception as e:
            log(f"{sym} fetch failed: {type(e).__name__}: {e}")
            continue
        if len(rows) < 3000:
            log(f"{sym}: only {len(rows)} bars, skipped")
            continue
        a = np.array(rows, dtype=float)
        t, o, h, l, c, v = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]
        if frac:
            stop_d = float(frac) * float(np.median(c))
            sym = f"{sym} (stop {float(frac) * 100:.1f}% of price)"
        have_v = float(v.sum()) > 0 and float(np.median(v)) > 0
        days = max(1.0, (t[-1] - t[0]) / 86400.0)
        log(f"{sym}: {len(rows)} bars over {days:.1f} days, tick counts {'available' if have_v else 'MISSING'}, "
            f"stop {stop_d:g} price units")
        VARIANTS = [  # (name, side, trend, envelope %)
            ("follow the burst", "follow", False, 0.0),
            ("fade the burst", "fade", False, 0.0),
            ("follow + trend filter", "follow", True, 0.0),
            ("follow + outside envelope 0.1%", "follow", False, 0.001),
            ("fade + outside envelope 0.1%", "fade", False, 0.001),
            ("follow + outside envelope 0.2%", "follow", False, 0.002),
            ("fade + outside envelope 0.2%", "fade", False, 0.002),
        ]
        for kv, kr in [(3, 0), (0, 3), (3, 3)]:
            if kv and not have_v:
                continue
            for name, side, trend, env in VARIANTS:
                tr = simulate(t, o, h, l, c, v, kv, kr, stop_d, have_v, side, trend, env)
                s = stats(tr)
                bl = f"ticks>={kv}x range>={kr}x"
                if not s or s[0] < 15:
                    log(f"{sym} | {bl} | {name}: too few trades ({0 if not s else s[0]})")
                    continue
                cut = int(len(tr) * 0.7)
                a1, a2 = stats(tr[:cut]), stats(tr[cut:])
                log(f"{sym} | {bl} | {name}: trades={s[0]} ({s[0] / days:.1f}/day) win={s[1]:.0f}% "
                    f"net={s[2] * 100:+.0f}% of stop per trade | first70% net={(a1[2] * 100) if a1 else float('nan'):+.0f}% "
                    f"last30% net={(a2[2] * 100) if a2 else float('nan'):+.0f}%")
    if os.getenv("BURST_TICKS", "1") == "1" and not ONLY:
        for sym, stop_d in TICK_STOPS.items():
            try:
                tick_study(sym, stop_d)
            except Exception as e:
                log(f"{sym} tick study failed: {type(e).__name__}: {e}")
    log("done")


def selftest():
    rng = np.random.default_rng(3)
    n = 6000
    c = 100 + np.cumsum(rng.normal(0, 0.05, n))
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + rng.random(n) * 0.02
    l = np.minimum(o, c) - rng.random(n) * 0.02
    v = rng.integers(50, 150, n).astype(float)
    t = 1_700_000_000 + np.arange(n) * 60.0
    for kv, kr in GRID:
        print(kv, kr, stats(simulate(t, o, h, l, c, v, kv, kr, 0.5, True)))
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
