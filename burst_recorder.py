"""
Read-only price recorder for the burst study. Samples gold and oil bid/ask from MetaApi every 2 seconds
and stores them in the Supabase table burst_quotes (sym, t, bid, ask). Never places a trade.
Started by the bot only while BURST_RECORD=1 is set; stops itself after BURST_RECORD_HOURS (default 72).
"""
import os
import sys
import time
import json
from datetime import datetime

import requests

SYMBOLS = [s.strip() for s in os.getenv("BURST_RECORD_SYMBOLS", "XAUUSDm,USOILm").split(",") if s.strip()]
HOURS = float(os.getenv("BURST_RECORD_HOURS", "72"))
STEP = 2.0
HOST = "https://mt-client-api-v1.london.agiliumtrade.ai"


def log(msg):
    print(f"[BURST REC] {msg}", flush=True)


def flush(rows):
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not rows or not url or not key:
        return True
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json",
         "Prefer": "resolution=ignore-duplicates,return=minimal"}
    try:
        r = requests.post(f"{url}/rest/v1/burst_quotes?on_conflict=sym,t", headers=h, data=json.dumps(rows), timeout=30)
        if r.status_code not in (200, 201, 204):
            log(f"save HTTP {r.status_code} {r.text[:120]}")
            return False
        return True
    except Exception as e:
        log(f"save error {type(e).__name__}")
        return False


def main():
    try:
        os.nice(10)
    except Exception:
        pass
    token, acct = os.getenv("METAAPI_TOKEN"), os.getenv("METAAPI_ACCOUNT_ID")
    if not token or not acct:
        log("MetaApi credentials missing")
        return
    log(f"start: {SYMBOLS} every {STEP:g}s for {HOURS:g}h")
    headers = {"auth-token": token, "Accept": "application/json"}
    end = time.time() + HOURS * 3600
    buf, last_flush, saved, errs = [], time.time(), 0, 0
    while time.time() < end:
        t0 = time.time()
        for sym in SYMBOLS:
            try:
                r = requests.get(f"{HOST}/users/current/accounts/{acct}/symbols/{sym}/current-price",
                                 headers=headers, timeout=6)
                if r.status_code == 200:
                    d = r.json()
                    bid, ask = d.get("bid"), d.get("ask")
                    if bid and ask:
                        buf.append({"sym": sym, "t": int(time.time()), "bid": float(bid), "ask": float(ask)})
                else:
                    errs += 1
            except Exception:
                errs += 1
        if time.time() - last_flush >= 60 and buf:
            if flush(buf):
                saved += len(buf)
                buf = []
            elif len(buf) > 5000:
                buf = buf[-2000:]
            last_flush = time.time()
            if saved and saved % 3000 < len(SYMBOLS) * 31:
                log(f"saved {saved} rows so far, {errs} request errors")
        time.sleep(max(0.0, STEP - (time.time() - t0)))
    flush(buf)
    log("done")


if __name__ == "__main__":
    main()
