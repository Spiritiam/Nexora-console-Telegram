"""
Exness Trader API client (signing + read-only calls).

Signing follows the public docs at https://www.exness-api.com/authentication/request-signing:
- EXN-DATA = base64url(JSON payload) with api_key, idempotency_key, timestamp,
  sign_version, method, path (incl. exact query string), body_hash
- EXN-SIGN = base64url(Ed25519 signature over the payload bytes)
- body_hash = base64url(SHA-256(body)), no '=' padding
Secrets are never logged or returned.
"""
import base64
import binascii
import hashlib
import json
import time
import uuid

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def load_private_key(secret: str) -> Ed25519PrivateKey:
    """Accepts the secret as base64 / base64url / hex text (32-byte seed, or
    a 64-byte seed+public key). Raises ValueError if it can't be understood."""
    s = (secret or "").strip()
    candidates = []
    try:
        candidates.append(bytes.fromhex(s))
    except ValueError:
        pass
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            candidates.append(decoder(s + "=" * (-len(s) % 4)))
        except (binascii.Error, ValueError):
            pass
    for raw in candidates:
        if len(raw) == 32:
            return Ed25519PrivateKey.from_private_bytes(raw)
        if len(raw) == 64:
            return Ed25519PrivateKey.from_private_bytes(raw[:32])
        if len(raw) > 32:
            # PKCS#8 DER: the 32-byte seed is the last 32 bytes
            try:
                return Ed25519PrivateKey.from_private_bytes(raw[-32:])
            except Exception:
                pass
    raise ValueError("secret key format not recognised")


def signed_headers(api_key: str, private_key: Ed25519PrivateKey, method: str,
                   path: str, body: bytes = b"", idempotency_key: str = "") -> dict:
    payload = {
        "api_key": api_key,
        "idempotency_key": idempotency_key,
        "timestamp": int(time.time() * 1000),
        "sign_version": 1,
        "method": method.upper(),
        "path": path,
        "body_hash": _b64url(hashlib.sha256(body).digest()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return {
        "EXN-API-KEY": api_key,
        "EXN-IDEMPOTENCY-KEY": idempotency_key,
        "EXN-TIMESTAMP": str(payload["timestamp"]),
        "EXN-SIGN-VERSION": "1",
        "EXN-DATA": _b64url(payload_bytes),
        "EXN-SIGN": _b64url(private_key.sign(payload_bytes)),
    }


def new_idempotency_key(prefix: str = "nx") -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def signed_request(base_url: str, api_key: str, private_key, method: str, path: str,
                   json_body: dict = None, idempotency_key: str = "", timeout: int = 20):
    body = json.dumps(json_body, separators=(",", ":")).encode("utf-8") if json_body is not None else b""
    headers = signed_headers(api_key, private_key, method, path, body, idempotency_key)
    if body:
        headers["Content-Type"] = "application/json"
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return requests.request(method.upper(), base_url + path,
                            headers=headers, data=body if body else None, timeout=timeout)


def discover_access_point(gateway_url: str, api_key: str, private_key, account_id: str) -> str:
    """Gateway -> the account's Access Point base URL."""
    r = signed_request(gateway_url, api_key, private_key, "GET",
                       f"/v1/trading/access-point?account_id={account_id}")
    if r.status_code != 200:
        raise RuntimeError(f"access-point lookup failed: HTTP {r.status_code} {r.text[:300]}")
    ap = r.json().get("access_point")
    if not ap:
        raise RuntimeError(f"access-point lookup returned no access_point: {r.text[:300]}")
    return ap


def get_account_details(access_point: str, api_key: str, private_key, account_id: str) -> dict:
    r = signed_request(access_point, api_key, private_key, "GET",
                       f"/v1/configuration/accounts/{account_id}/account")
    if r.status_code != 200:
        raise RuntimeError(f"account details failed: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


# ---------------------------------------------------------------------------
# Trading helpers (all signed). Opening/closing return an ACK only; the real
# result is read from the operation status endpoint.
# ---------------------------------------------------------------------------
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from urllib.parse import urlencode


class ExnessClient:
    def __init__(self, gateway_url, api_key, secret, account_id):
        self.api_key = api_key
        self.pk = load_private_key(secret)
        self.account_id = str(account_id)
        self.gateway_url = gateway_url
        self._ap = None

    @property
    def access_point(self):
        if not self._ap:
            self._ap = discover_access_point(self.gateway_url, self.api_key, self.pk, self.account_id)
        return self._ap

    def _call(self, method, path, json_body=None, idem=""):
        r = signed_request(self.access_point, self.api_key, self.pk, method, path,
                           json_body=json_body, idempotency_key=idem)
        try:
            data = r.json() if r.text else {}
        except ValueError:
            data = {"raw": r.text[:300]}
        return r.status_code, data

    def instruments(self):
        code, d = self._call("GET", f"/v1/configuration/accounts/{self.account_id}/instruments")
        if code != 200:
            raise RuntimeError(f"instruments: HTTP {code} {str(d)[:200]}")
        items = d.get("instruments") or d.get("items") or d
        return items if isinstance(items, list) else []

    def conditions(self, instrument):
        code, d = self._call("GET", f"/v1/configuration/accounts/{self.account_id}/instruments/{instrument}/conditions")
        if code != 200:
            raise RuntimeError(f"conditions {instrument}: HTTP {code} {str(d)[:200]}")
        return d

    def last_price(self, instrument, side="bid"):
        now = datetime.now(timezone.utc)
        q = urlencode({
            "instrument": instrument, "price_type": side, "timeframe": "M1",
            "from": (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        code, d = self._call("GET", f"/v1/market-data/accounts/{self.account_id}/candles?{q}")
        if code != 200:
            raise RuntimeError(f"candles {instrument}: HTTP {code} {str(d)[:200]}")
        candles = d.get("candles") or []
        if not candles:
            raise RuntimeError(f"no recent {side} candles for {instrument} (market closed?)")
        return Decimal(str(candles[-1]["close"]))

    @staticmethod
    def fmt_price(value, point_digits):
        q = Decimal(1).scaleb(-int(point_digits))
        return str(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))

    def open_position(self, instrument, side, volume, stop_loss=None, take_profit=None,
                      price=None, comment="Nexora AI", idem=None):
        body = {"instrument": instrument, "side": side, "volume": str(volume), "comment": comment[:30]}
        if stop_loss is not None:
            body["stop_loss_price"] = str(stop_loss)
        if take_profit is not None:
            body["take_profit_price"] = str(take_profit)
        if price is not None:
            body["price"] = str(price)
        idem = idem or new_idempotency_key("open")
        code, d = self._call("POST", f"/v1/trading/accounts/{self.account_id}/positions", json_body=body, idem=idem)
        return code, d, idem

    def close_position(self, position_id, idem=None):
        idem = idem or new_idempotency_key("close")
        code, d = self._call("DELETE", f"/v1/trading/accounts/{self.account_id}/positions/{position_id}", idem=idem)
        return code, d, idem

    def operation(self, operation_id):
        code, d = self._call("GET", f"/v1/trading/accounts/{self.account_id}/operations/{operation_id}")
        return code, d

    def wait_operation(self, operation_id, timeout=20):
        end = time.time() + timeout
        last = {}
        while time.time() < end:
            code, d = self.operation(operation_id)
            last = d
            if code == 200 and d.get("status") in ("confirmed", "rejected", "failed"):
                return d
            time.sleep(1)
        return last or {"status": "pending"}

    def snapshot(self):
        code, d = self._call("GET", f"/v1/trading/accounts/{self.account_id}/snapshot")
        if code != 200:
            raise RuntimeError(f"snapshot: HTTP {code} {str(d)[:200]}")
        return d


# ---------------------------------------------------------------------------
# MetaAPI-shaped adapter. The bot's engines (full coverage, follow platform
# signals, Account Flip, aggressive) were written against MetaAPI position
# dicts and a handful of helper calls. ExnessBroker answers the same questions
# from the Exness Trader API so those engines work unchanged.
# ---------------------------------------------------------------------------
import threading


class ExnessBroker(ExnessClient):
    TRANSIENT = (429, 500, 502, 503, 504)

    def __init__(self, gateway_url, api_key, secret, account_id):
        super().__init__(gateway_url, api_key, secret, account_id)
        self._cond = {}
        self._names = None
        self._px = {}
        self._lock = threading.Lock()

    # -- reference data ----------------------------------------------------
    def instrument_names(self):
        if self._names is None:
            self._names = [str(n) for n in self.instruments()]
        return self._names

    def cond(self, instrument):
        if instrument not in self._cond:
            self._cond[instrument] = self.conditions(instrument)
        return self._cond[instrument]

    def quantize_volume(self, instrument, volume):
        c = self.cond(instrument)
        vmin = Decimal(str(c.get("volume_min") or "0.01"))
        vmax = Decimal(str(c.get("volume_max") or "100"))
        step = Decimal(str(c.get("volume_step") or "0.01"))
        v = Decimal(str(volume))
        v = max(vmin, min(vmax, v))
        n = ((v - vmin) / step).to_integral_value(rounding=ROUND_DOWN)
        v = vmin + n * step
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)

    def price(self, instrument, side="bid", max_age=4):
        key = (instrument, side)
        hit = self._px.get(key)
        if hit and time.time() - hit[1] < max_age:
            return hit[0]
        p = self.last_price(instrument, side)
        self._px[key] = (p, time.time())
        return p

    # -- account -----------------------------------------------------------
    def account_info(self):
        st = (self.snapshot().get("account_state") or {})
        def f(k):
            try:
                return float(st.get(k))
            except (TypeError, ValueError):
                return None
        return {"balance": f("balance"), "equity": f("equity"), "margin": f("used_margin")}

    # -- positions (MetaAPI-shaped) ----------------------------------------
    def _usd_per_quote(self, quote_ccy, instrument_price):
        q = (quote_ccy or "").upper()
        if q in ("", "USD"):
            return Decimal(1)
        try:
            rate = self.price("USD" + q, "bid")  # units of q per 1 USD
            return Decimal(1) / rate
        except Exception:
            return None

    def positions_metaapi(self):
        snap = self.snapshot()
        sltp = {}
        for o in snap.get("orders") or []:
            pid = o.get("position_id")
            if pid and str(o.get("state", "")).lower() in ("placed", "active", "open", "") :
                sltp.setdefault(str(pid), []).append(o)
        out = []
        for p in snap.get("positions") or []:
            if str(p.get("state", "open")).lower() != "open":
                continue
            inst = p.get("instrument")
            buy = str(p.get("direction")).lower() == "buy"
            try:
                vol = Decimal(str(p.get("volume")))
                opn = Decimal(str(p.get("open_price")))
            except Exception:
                continue
            cur = profit = None
            try:
                cur = self.price(inst, "bid" if buy else "ask")
                diff = (cur - opn) if buy else (opn - cur)
                cs = Decimal(str(self.cond(inst).get("contract_size") or "100000"))
                conv = self._usd_per_quote(self.cond(inst).get("quote_currency"), cur)
                profit = float(diff * vol * cs * conv) if conv is not None else 0.0
            except Exception:
                pass
            sl = tp = None
            for o in sltp.get(str(p.get("position_id")), []):
                if o.get("stop_loss_price"):
                    sl = float(o["stop_loss_price"])
                if o.get("take_profit_price"):
                    tp = float(o["take_profit_price"])
            out.append({
                "id": str(p.get("position_id")),
                "symbol": inst,
                "type": "POSITION_TYPE_BUY" if buy else "POSITION_TYPE_SELL",
                "volume": float(vol),
                "openPrice": float(opn),
                "currentPrice": float(cur) if cur is not None else float(opn),
                "profit": profit if profit is not None else 0.0,
                "stopLoss": sl,
                "takeProfit": tp,
                "time": p.get("create_time"),
                "comment": "",
            })
        return out

    # -- trading -----------------------------------------------------------
    def place_trade(self, symbol, direction, volume, stop_loss, take_profit, comment="Nexora AI"):
        """Returns (position_id_or_None, detail). One idempotency key is reused
        across retries so a retried request can never open a second position."""
        cond = self.cond(symbol)
        digits = cond.get("point_digits", 5)
        vol = self.quantize_volume(symbol, volume)
        buy = str(direction).upper() == "BUY"
        sl = self.fmt_price(stop_loss, digits) if stop_loss else None
        tp = self.fmt_price(take_profit, digits) if take_profit else None
        price = None
        if "instant" in str(cond.get("exec_mode", "")).lower():
            price = self.fmt_price(self.price(symbol, "ask" if buy else "bid", max_age=0), digits)
        idem = new_idempotency_key("open")
        started = datetime.now(timezone.utc) - timedelta(seconds=5)
        ack = {}
        for attempt in range(3):
            code, ack, _ = self.open_position(symbol, "buy" if buy else "sell", vol, sl, tp,
                                              price=price, comment=comment, idem=idem)
            if code in (200, 202) and ack.get("operation_id"):
                break
            if code in self.TRANSIENT and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            return None, f"Exness rejected the request (HTTP {code}): {str(ack)[:200]}"
        op = self.wait_operation(ack["operation_id"], timeout=25)
        status = op.get("status")
        if status != "confirmed":
            return None, f"Exness {status or 'pending'}: {op.get('error_code', '')} {op.get('error_message', '')}".strip()
        try:
            time.sleep(0.5)
            mine = [p for p in self.positions_metaapi() if p["symbol"] == symbol and p["time"]
                    and datetime.fromisoformat(str(p["time"]).replace("Z", "+00:00")) >= started]
            if mine:
                return mine[-1]["id"], "ok"
        except Exception:
            pass
        return str(ack["operation_id"]), "ok"

    def close_one(self, position_id):
        code, ack, _ = self.close_position(position_id)
        if code not in (200, 202) or not ack.get("operation_id"):
            return False, f"HTTP {code} {str(ack)[:200]}"
        op = self.wait_operation(ack["operation_id"], timeout=25)
        return op.get("status") == "confirmed", f"{op.get('status')} {op.get('error_code', '')}".strip()

    def close_symbol(self, symbol):
        ok_all, detail = True, "no positions"
        for p in self.positions_metaapi():
            if p["symbol"].upper() == str(symbol).upper():
                ok, d = self.close_one(p["id"])
                ok_all = ok_all and ok
                detail = d
        return ok_all, detail

    def set_sltp(self, position_id, stop_loss=None, take_profit=None):
        snap = self.snapshot()
        pos = next((p for p in snap.get("positions") or [] if str(p.get("position_id")) == str(position_id)), None)
        if not pos:
            return False, "position not found"
        digits = self.cond(pos["instrument"]).get("point_digits", 5)
        body = {}
        if stop_loss is not None:
            body["stop_loss_price"] = self.fmt_price(stop_loss, digits)
        if take_profit is not None:
            body["take_profit_price"] = self.fmt_price(take_profit, digits)
        if not body:
            return True, "nothing to change"
        existing = next((o for o in snap.get("orders") or [] if str(o.get("position_id")) == str(position_id)
                         and str(o.get("order_type", "")).lower() == "sltp"), None)
        idem = new_idempotency_key("sltp")
        if existing:
            code, ack = self._call("PATCH", f"/v1/trading/accounts/{self.account_id}/orders/{existing['order_id']}",
                                   json_body=body, idem=idem)
        else:
            body.update({"type": "sltp", "position_id": str(position_id)})
            code, ack = self._call("POST", f"/v1/trading/accounts/{self.account_id}/orders",
                                   json_body=body, idem=idem)
        if code not in (200, 202) or not ack.get("operation_id"):
            return False, f"HTTP {code} {str(ack)[:200]}"
        op = self.wait_operation(ack["operation_id"], timeout=20)
        return op.get("status") == "confirmed", f"{op.get('status')} {op.get('error_code', '')}".strip()

    def trade_action(self, payload):
        """Translate the MetaAPI trade payloads the engines send."""
        t = payload.get("actionType")
        if t == "POSITION_MODIFY":
            return self.set_sltp(payload.get("positionId"), payload.get("stopLoss"), payload.get("takeProfit"))
        if t == "POSITION_CLOSE_ID":
            return self.close_one(payload.get("positionId"))
        if t == "POSITIONS_CLOSE_SYMBOL":
            return self.close_symbol(payload.get("symbol"))
        return False, f"unsupported action {t}"

    def resolve_symbol_map(self, pair_names, known_suffixes=()):
        """{canonical pair name: this account's real instrument name}."""
        names = self.instrument_names()
        upper = {n.upper(): n for n in names}
        out = {}
        for base in pair_names:
            b = base.upper()
            if b in upper:
                out[base] = upper[b]
                continue
            hit = next((upper[b + s.upper()] for s in known_suffixes if (b + s.upper()) in upper), None)
            if not hit:
                cands = [n for n in names if n.upper().startswith(b) and len(n) - len(b) <= 5]
                hit = min(cands, key=len) if cands else None
            if hit:
                out[base] = hit
        return out


def _closed_position_profit(self, position_id):
    """Total profit (incl. commission and swap) of a CLOSED position from deal
    history, or None while it is still open / unknown."""
    now = datetime.now(timezone.utc)
    q = urlencode({"from": (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "to": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 200})
    code, d = self._call("GET", f"/v1/history/accounts/{self.account_id}/deals?{q}")
    if code != 200:
        return None
    deals = [x for x in (d.get("items") or d.get("deals") or [])
             if str(x.get("position_id")) == str(position_id)]
    if not any(str(x.get("type")).lower() == "close" for x in deals):
        return None
    total = 0.0
    for x in deals:
        for k in ("profit", "commission", "swap"):
            try:
                total += float(x.get(k) or 0)
            except (TypeError, ValueError):
                pass
    return total


ExnessBroker.closed_position_profit = _closed_position_profit


# ---------------------------------------------------------------------------
# Market data (used with the master account's own key for live signals)
# ---------------------------------------------------------------------------
_TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}


def _client_candles(self, instrument, timeframe, count):
    """Last `count` bid candles, oldest first, as dicts with ISO time or None."""
    tf = _TF_SECONDS[timeframe]
    now = datetime.now(timezone.utc)
    for factor in (1.3, 3.0, 8.0):
        span = int(count * tf * factor)
        span = min(span, 990 * tf)
        q = urlencode({
            "instrument": instrument, "price_type": "bid", "timeframe": timeframe,
            "from": (now - timedelta(seconds=span)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (now + timedelta(seconds=tf)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        code, d = self._call("GET", f"/v1/market-data/accounts/{self.account_id}/candles?{q}")
        if code != 200:
            raise RuntimeError(f"candles {instrument} {timeframe}: HTTP {code} {str(d)[:160]}")
        rows = d.get("candles") or []
        if len(rows) >= count * 0.9 or span >= 990 * tf:
            return rows[-count:]
    return rows[-count:]


def _client_quote(self, instrument, max_age_seconds=240):
    """(bid, ask) from the latest M1 candle, or None if the feed looks stale."""
    now = datetime.now(timezone.utc)
    q = urlencode({
        "instrument": instrument, "price_type": "both", "timeframe": "M1",
        "from": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    code, d = self._call("GET", f"/v1/market-data/accounts/{self.account_id}/candles?{q}")
    if code != 200:
        raise RuntimeError(f"quote {instrument}: HTTP {code}")
    rows = d.get("candles") or []
    if not rows:
        return None
    last = rows[-1]
    try:
        opened = datetime.fromisoformat(str(last["time"]).replace("Z", "+00:00"))
        if (now - opened).total_seconds() > max_age_seconds:
            return None
        return float(last["bid"]["close"]), float(last["ask"]["close"])
    except Exception:
        return None


ExnessClient.candles = _client_candles
ExnessClient.quote = _client_quote
