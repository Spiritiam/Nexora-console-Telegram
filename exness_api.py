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
