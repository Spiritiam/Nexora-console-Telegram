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
from decimal import Decimal, ROUND_DOWN
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
        return str(Decimal(value).quantize(q, rounding=ROUND_DOWN))

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
