"""SpiritFX Trading Academy Program certificates: exam, issue, certificate image with QR, public verify page.

Wired into the web server by register(app, deps). Eligibility comes from spiritfx.org's user_data
(`certificate` == "1" means the student finished Beginner and Advanced).
"""
import io
import os
import json
import html
import time
import random
import hashlib
import asyncio
import re
import threading
from datetime import datetime, timezone

import requests
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "sfx_cert_assets")
PROGRAMME = "SpiritFX Trading Academy Program"
SIGNER_NAME = "Henry Onyefunuwe Smith"
SIGNER_TITLE = "CEO, SpiritFX Trading Academy"
EXAM_BASIC = 8
EXAM_ADVANCED = 12
PASS_PERCENT = 70
RETRY_WAIT_SECONDS = 6 * 3600
EXAM_MAX_AGE_SECONDS = 3 * 3600
EXAM_TIME_LIMIT_SECONDS = 20 * 60   # 20 questions, 20 minutes
EXAM_TIME_GRACE_SECONDS = 30        # covers network delay at the start and the end
PUBLIC_BASE = os.getenv("PUBLIC_BASE_URL", "https://nexora-console-telegram-production.up.railway.app").rstrip("/")

_deps = {}
_bank = None
_png_cache = {}


def _load_bank():
    global _bank
    if _bank is None:
        with open(os.path.join(ASSETS, "exam_bank.json"), encoding="utf-8") as f:
            data = json.load(f)
        _bank = {q["id"]: q for q in data["questions"]}
    return _bank


def _j(data, status=200):
    return web.json_response(data, status=status)


def _sb(path, method="GET", body=None, prefer=None):
    h = dict(_deps["sb_headers"]())
    if prefer:
        h["Prefer"] = prefer
    url = f"{_deps['supabase_url']}/rest/v1/{path}"
    r = requests.request(method, url, headers=h, data=json.dumps(body) if body is not None else None, timeout=15)
    try:
        return r.status_code, r.json() if r.text else None
    except Exception:
        return r.status_code, None


def _find(obj, key, depth=0):
    if not isinstance(obj, dict):
        return None
    if key in obj and not isinstance(obj[key], (dict, list)):
        return obj[key]
    if depth < 2:
        for wrap in ("data", "user", "result", "profile"):
            inner = obj.get(wrap)
            if isinstance(inner, dict):
                v = _find(inner, key, depth + 1)
                if v is not None:
                    return v
    return None


AVATARS = ("fox", "panda", "lion", "tiger", "frog", "monkey", "owl", "wolf",
           "robot", "alien", "rocket", "diamond", "unicorn", "dragon", "shark", "whale")


def _q(v):
    return requests.utils.quote(str(v), safe="")


def _overlay(user_key):
    code, rows = _sb(f"sfx_profiles?user_key=eq.{_q(user_key)}&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


def _profile_sync(token):
    """Dict (key, name, uname, since, avatar, eligible) for a spiritfx.org token, or None.
    name/uname are the student's own edits when they made any, else the spiritfx.org values."""
    user_key = _deps["validate_token"](token)
    if not user_key:
        return None
    base_name, base_uname, since, cert, base_email = "", "", "", "", ""
    try:
        r = requests.get(_deps["user_url"], headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=10)
        body = r.json()
        base_name = str(_find(body, "funame") or _find(body, "name") or "").strip()
        base_uname = str(_find(body, "uname") or "").strip()
        since = str(_find(body, "datetime") or "").strip()
        c = _find(body, "certificate")
        cert = str(c) if c is not None else ""
        base_email = str(_find(body, "email") or "").strip()
    except Exception:
        pass
    ov = _overlay(user_key)
    if ov is None and base_uname:
        _sb("sfx_profiles", "POST", {"user_key": user_key, "orig_uname": base_uname,
                                     "orig_uname_lower": base_uname.lower()}, prefer="resolution=ignore-duplicates")
    ov = ov or {}
    return {"key": user_key,
            "name": (ov.get("display_name") or base_name or "").strip(),
            "uname": (ov.get("username") or base_uname or "").strip(),
            "since": since, "avatar": ov.get("avatar") or "",
            "eligible": cert == "1", "token": token, "email": base_email}


async def _auth(request):
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    me = await asyncio.to_thread(_profile_sync, token)
    if not me:
        return None, _j({"error": "auth", "message": "Please log in again."}, 401)
    return me, None


def _existing_cert(user_key):
    code, rows = _sb(f"sfx_certificates?user_key=eq.{requests.utils.quote(user_key, safe='')}&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


def _last_attempt(user_key):
    code, rows = _sb(f"sfx_cert_attempts?user_key=eq.{requests.utils.quote(user_key, safe='')}&order=created_at.desc&limit=1&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


def _ts(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def _cert_public(c):
    return {"cert_no": c["cert_no"], "name": c["holder_name"], "programme": c["programme"],
            "issued_at": c["issued_at"], "verify_url": f"{PUBLIC_BASE}/verify/{c['cert_no']}",
            "image_url": f"{PUBLIC_BASE}/cert/{c['cert_no']}.png?v=2"}


BEGINNER_TOTAL = 12
ADVANCED_TOTAL = 13


def _to_int(v):
    try:
        return int(str(v).strip())
    except Exception:
        return 0


def _class_counts(me):
    """Lessons finished per level, read from the student's own Forex class progress."""
    try:
        url = _deps["user_url"].replace("user_data", "forex_class_data")
        r = requests.get(url, headers={"Authorization": f"Bearer {me['token']}", "Accept": "application/json"}, timeout=10)
        b = r.json()
        return {"beginner": _to_int(_find(b, "beginner_completed")),
                "intermediate": _to_int(_find(b, "intermediate_completed")),
                "advanced": _to_int(_find(b, "advanced_completed"))}
    except Exception:
        return None


WATCH_PERCENT = 0.8          # a video counts once 80% of it has really been played
MIN_VIDEO_SECONDS = 30       # ignore clips shorter than this
MIN_FULL_COURSE = 8          # a tutor course with at least this many videos can complete a level on its own


def _level_key(lvl):
    l = str(lvl or "").strip().lower()
    return l if l in ("beginner", "advanced") else ""


def _video_progress(user_key):
    """Watched tutor videos for this student, from any tutor or language.
    Returns {"beginner": n, "advanced": n, "full_course": {"beginner": bool, "advanced": bool}}."""
    out = {"beginner": 0, "advanced": 0, "full_course": {"beginner": False, "advanced": False}}
    try:
        code, rows = _sb(f"sfx_video_events?user_key=eq.{requests.utils.quote(user_key, safe='')}"
                         "&watched=eq.true&select=course_id,video_id,lvl,course_total&limit=5000")
        if code != 200 or not isinstance(rows, list):
            return out
        per_course = {}
        for r in rows:
            lv = _level_key(r.get("lvl"))
            if not lv:
                continue
            out[lv] += 1
            c = per_course.setdefault(r.get("course_id"), {"lvl": lv, "n": 0, "total": 0})
            c["n"] += 1
            c["total"] = max(c["total"], _to_int(r.get("course_total")))
        for c in per_course.values():
            if c["total"] >= MIN_FULL_COURSE and c["n"] >= c["total"]:
                out["full_course"][c["lvl"]] = True
    except Exception:
        pass
    return out


def _eligibility(me):
    """(eligible, source, counts). Eligible when spiritfx.org marks the student eligible,
    or when the Beginner and Advanced lessons are all finished."""
    counts = _class_counts(me)
    vids = _video_progress(me["key"])
    # Two things are both required: the AI Tutor lessons (Forex Class) and tutor videos
    # (any tutor, any language, 80% watched). A full course of 8+ videos can also
    # complete a level on the video side.
    lesson_b = (counts or {}).get("beginner", 0)
    lesson_a = (counts or {}).get("advanced", 0)
    lessons_done = bool(counts) and lesson_b >= BEGINNER_TOTAL and lesson_a >= ADVANCED_TOTAL
    vid_b_done = vids["beginner"] >= BEGINNER_TOTAL or vids["full_course"]["beginner"]
    vid_a_done = vids["advanced"] >= ADVANCED_TOTAL or vids["full_course"]["advanced"]
    ok = lessons_done and vid_b_done and vid_a_done
    source = "progress" if ok else None
    try:
        row = {"user_key": me["key"], "name": me["name"], "uname": me["uname"], "eligible": ok,
               "eligible_source": source, "updated_at": datetime.now(timezone.utc).isoformat()}
        if counts:
            row.update(counts)
        _sb("sfx_progress", "POST", row, prefer="resolution=merge-duplicates")
    except Exception:
        pass
    shown = dict(counts or {})
    shown["lessons_beginner"] = min(lesson_b, BEGINNER_TOTAL)
    shown["lessons_advanced"] = min(lesson_a, ADVANCED_TOTAL)
    shown["videos_beginner"] = BEGINNER_TOTAL if vid_b_done else min(vids["beginner"], BEGINNER_TOTAL)
    shown["videos_advanced"] = ADVANCED_TOTAL if vid_a_done else min(vids["advanced"], ADVANCED_TOTAL)
    return ok, source, shown


def _status_sync(me):
    price = _price()
    c = _existing_cert(me["key"])
    if c:
        pay = _payment_summary(me["key"], issued=True)
        out = {"eligible": True, "issued": _cert_public(c), "can_start": False, "wait_seconds": 0, "name": c["holder_name"],
               "passed": True, "paid": True, "price": {"ngn": price}, "payment": pay}
        out.update(_notice("certificate_issued", price))
        return out
    pas = _get_pass(me["key"])
    if pas:
        # Self-heal: the payment was confirmed but the certificate could not be saved at that moment.
        if _paid_row(me["key"]):
            c = _issue_after_payment(me["key"])
            if c:
                pay = _payment_summary(me["key"], issued=True)
                out = {"eligible": True, "issued": _cert_public(c), "can_start": False, "wait_seconds": 0, "name": c["holder_name"],
                       "passed": True, "paid": True, "price": {"ngn": price}, "payment": pay}
                out.update(_notice("certificate_issued", price))
                return out
        # Backup for a missed webhook: ask Korapay about the newest pending checkout.
        pend = _latest_row(me["key"], "pending")
        if pend:
            _recheck_pending(pend)
            c = _existing_cert(me["key"])
            if c:
                pay = _payment_summary(me["key"], issued=True)
                out = {"eligible": True, "issued": _cert_public(c), "can_start": False, "wait_seconds": 0, "name": c["holder_name"],
                       "passed": True, "paid": True, "price": {"ngn": price}, "payment": pay}
                out.update(_notice("certificate_issued", price))
                return out
        pay = _payment_summary(me["key"])
        out = {"eligible": True, "issued": None, "name": pas["holder_name"], "can_start": False, "wait_seconds": 0,
               "pass_percent": PASS_PERCENT, "questions": EXAM_BASIC + EXAM_ADVANCED,
               "time_limit_seconds": EXAM_TIME_LIMIT_SECONDS,
               "passed": True, "paid": False, "price": {"ngn": price}, "payment": pay,
               "score_percent": pas.get("score")}
        out.update(_notice({"pending": "payment_pending", "failed": "payment_failed"}.get(pay["state"], "passed_payment_required"), price))
        out["ok"] = True  # a status reply is data, not an error
        return out
    wait = 0
    last = _last_attempt(me["key"])
    if last and last.get("submitted_at"):
        wait = max(0, int(_ts(last["submitted_at"]) + RETRY_WAIT_SECONDS - time.time()))
    ok, _src, counts = _eligibility(me)
    return {"eligible": ok, "issued": None, "name": me["name"],
            "can_start": ok and wait == 0, "wait_seconds": wait,
            "pass_percent": PASS_PERCENT, "questions": EXAM_BASIC + EXAM_ADVANCED,
            "time_limit_seconds": EXAM_TIME_LIMIT_SECONDS,
            "passed": False, "paid": False, "price": {"ngn": price}, "payment": {"state": "none", "reference": None},
            "progress": {"lessons_beginner": (counts or {}).get("lessons_beginner", 0),
                         "lessons_advanced": (counts or {}).get("lessons_advanced", 0),
                         "videos_beginner": (counts or {}).get("videos_beginner", 0),
                         "videos_advanced": (counts or {}).get("videos_advanced", 0),
                         "beginner_total": BEGINNER_TOTAL, "advanced_total": ADVANCED_TOTAL}}


async def status_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    return _j(await asyncio.to_thread(_status_sync, me))


def _perm(attempt_id, qid, n):
    """Display order of a question's options for one attempt (same on start and submit)."""
    order = list(range(n))
    random.Random(f"{attempt_id}:{qid}").shuffle(order)
    return order


def _seen_ids(user_key):
    code, rows = _sb(f"sfx_cert_attempts?user_key=eq.{requests.utils.quote(str(user_key), safe='')}&select=question_ids&order=created_at.desc&limit=3")
    seen = set()
    if code == 200 and isinstance(rows, list):
        for r in rows:
            for i in (r.get("question_ids") or []):
                seen.add(i)
    return seen


def _pick(pool, n, seen):
    fresh = [q for q in pool if q["id"] not in seen]
    random.shuffle(fresh)
    out = fresh[:n]
    if len(out) < n:
        rest = [q for q in pool if q not in out]
        random.shuffle(rest)
        out += rest[:n - len(out)]
    return out


def _start_sync(me, confirmed):
    st = _status_sync(me)
    if st["issued"]:
        return {"error": "done", "message": "You already have your certificate."}, 409
    if st.get("passed"):
        return _reply("already_passed", _price())
    if not st["eligible"]:
        return {"error": "not_eligible", "message": "Finish the AI Tutor lessons and the Beginner and Advanced videos first."}, 403
    if not confirmed:
        return {"error": "confirm", "message": "Please confirm the name that will be printed on your certificate."}, 400
    if not me["name"]:
        return {"error": "no_name", "message": "Add your full name in Edit profile first."}, 400
    if st["wait_seconds"] > 0:
        return {"error": "wait", "message": "Please wait before trying the exam again.", "wait_seconds": st["wait_seconds"]}, 429
    bank = _load_bank()
    basic = [q for q in bank.values() if q["level"] == "basic"]
    adv = [q for q in bank.values() if q["level"] == "advanced"]
    seen = _seen_ids(me["key"])
    picked = _pick(basic, EXAM_BASIC, seen) + _pick(adv, EXAM_ADVANCED, seen)
    random.shuffle(picked)
    ids = [q["id"] for q in picked]
    code, rows = _sb("sfx_cert_attempts", "POST", {"user_key": me["key"], "question_ids": ids}, prefer="return=representation")
    if code not in (200, 201) or not rows:
        return {"error": "server", "message": "Could not start the exam. Try again."}, 503
    aid = rows[0]["id"]
    qs = []
    for q in picked:
        opts = list(q["options"])
        order = _perm(aid, q["id"], len(opts))
        qs.append({"id": q["id"], "question": q["question"], "options": [opts[k] for k in order]})
    return {"attempt_id": aid, "pass_percent": PASS_PERCENT,
            "time_limit_seconds": EXAM_TIME_LIMIT_SECONDS, "questions": qs}, 200


async def start_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    try:
        data = await request.json()
    except Exception:
        data = {}
    body, status = await asyncio.to_thread(_start_sync, me, bool(data.get("confirmed_name")))
    return _j(body, status)


def _submit_sync(me, attempt_id, answers):
    code, rows = _sb(f"sfx_cert_attempts?id=eq.{requests.utils.quote(str(attempt_id), safe='')}&select=*")
    if code != 200 or not rows:
        return {"error": "bad_attempt", "message": "Exam not found."}, 404
    at = rows[0]
    if at["user_key"] != me["key"]:
        return {"error": "bad_attempt", "message": "Exam not found."}, 404
    if at.get("submitted_at"):
        return {"error": "bad_attempt", "message": "This exam was already submitted."}, 409
    if time.time() - _ts(at["created_at"]) > EXAM_MAX_AGE_SECONDS:
        return {"error": "expired", "message": "This exam expired. Start a new one."}, 410
    if not _existing_cert(me["key"]) and not _eligibility(me)[0]:
        return {"error": "not_eligible", "message": "Finish the AI Tutor lessons and the Beginner and Advanced videos first."}, 403
    bank = _load_bank()
    ids = at["question_ids"]
    if not isinstance(answers, list):
        answers = []
    if time.time() - _ts(at["created_at"]) > EXAM_TIME_LIMIT_SECONDS + EXAM_TIME_GRACE_SECONDS:
        # Time ran out: the attempt is used up and nothing is scored.
        _sb(f"sfx_cert_attempts?id=eq.{at['id']}", "PATCH",
            {"submitted_at": datetime.now(timezone.utc).isoformat(), "score": 0})
        return {"score": 0, "total": len(ids), "percent": 0, "passed": False,
                "pass_percent": PASS_PERCENT, "timed_out": True}, 200
    right = 0
    for i, qid in enumerate(ids):
        try:
            if i < len(answers):
                order = _perm(at["id"], qid, len(bank[qid]["options"]))
                if order[int(answers[i])] == int(bank[qid]["answer_index"]):
                    right += 1
        except Exception:
            pass
    total = len(ids)
    percent = round(right * 100 / total) if total else 0
    passed = percent >= PASS_PERCENT
    _sb(f"sfx_cert_attempts?id=eq.{at['id']}", "PATCH",
        {"submitted_at": datetime.now(timezone.utc).isoformat(), "score": percent})
    out = {"score": right, "total": total, "percent": percent, "passed": passed, "pass_percent": PASS_PERCENT}
    if passed:
        c = _existing_cert(me["key"])
        if c:
            out["issued"] = _cert_public(c)
            return out, 200
        # Not issued yet: the name and score are locked in now; the certificate comes after the fee is paid.
        name = me["name"] or "SpiritFX Student"
        code, _rows = _sb("sfx_cert_passes", "POST",
                          {"user_key": me["key"], "holder_name": name, "score": percent, "attempt_id": at["id"]},
                          prefer="resolution=ignore-duplicates,return=representation")
        if code not in (200, 201, 204):
            return {"error": "server", "message": "You passed, but we could not save your result. Open Certificate again."}, 503
        price = _price()
        out.update(_notice("passed_payment_required", price))
        out.update({"passed": True, "paid": False, "price": {"ngn": price}})
    return out, 200


async def submit_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    try:
        data = await request.json()
    except Exception:
        data = {}
    body, status = await asyncio.to_thread(_submit_sync, me, data.get("attempt_id"), data.get("answers"))
    return _j(body, status)


# --------------------------------------------------------------------------- certificate fee (Korapay)

KORAPAY_BASE = "https://api.korapay.com/merchant/api/v1"
CERT_CURRENCY = "NGN"
PRICE_FALLBACK = 20000
CHECKOUT_REUSE_SECONDS = 30 * 60
VERIFY_THROTTLE_SECONDS = 10
_price_cache = [0.0, PRICE_FALLBACK]
_verify_seen = {}
_user_locks = {}
_user_locks_guard = threading.Lock()


def _korapay_key():
    return os.getenv("KORAPAY_SECRET_KEY") or ""


def _price():
    """Certificate fee in NGN. Kept in the Supabase table sfx_cert_config (key price_ngn) so it can change
    without an app update or a deploy. Cached for a minute."""
    now = time.time()
    if now - _price_cache[0] < 60:
        return _price_cache[1]
    val = None
    try:
        code, rows = _sb("sfx_cert_config?key=eq.price_ngn&select=value")
        if code == 200 and isinstance(rows, list) and rows:
            val = int(float(str(rows[0]["value"]).strip()))
    except Exception:
        val = None
    if not val or val <= 0:
        try:
            val = int(os.getenv("CERT_PRICE_NGN") or PRICE_FALLBACK)
        except Exception:
            val = PRICE_FALLBACK
    _price_cache[0], _price_cache[1] = now, val
    return val


def _naira(n):
    return "\u20a6" + format(int(n), ",")


_CODES = {
    "passed_payment_required": (True, 200, "You passed!", "Pay the {price} certificate fee to receive your SpiritFX certificate.", "pay"),
    "payment_pending": (True, 200, "Complete your payment", "Your payment is not confirmed yet. Finish paying on the checkout page. Your certificate is issued as soon as the payment is confirmed.", "pay"),
    "payment_failed": (False, 502, "Payment failed", "The payment did not go through. Please try again.", "pay"),
    "already_paid": (False, 409, "Already paid", "Your payment is confirmed. Open Certificate to see your certificate.", "open_certificate"),
    "not_passed": (False, 403, "Pass the exam first", "Pass the certificate exam before you pay the certificate fee.", "start_exam"),
    "already_passed": (False, 409, "You already passed", "You passed the exam. Pay the {price} certificate fee to receive your certificate.", "pay"),
    "certificate_issued": (True, 200, "Certificate issued", "Your payment is confirmed and your SpiritFX certificate is ready.", "open_certificate"),
}


def _notice(code, price, message=None):
    ok, _http, title, msg, action = _CODES[code]
    return {"ok": ok, "code": code, "title": title,
            "message": message or msg.format(price=_naira(price)), "action": action}


def _reply(code, price, extra=None, message=None):
    body = _notice(code, price, message)
    body.update(extra or {})
    if not body["ok"]:
        body["error"] = code  # older app builds read "error"
    return body, _CODES[code][1]


def _lock_for(user_key):
    with _user_locks_guard:
        return _user_locks.setdefault(user_key, threading.Lock())


def _get_pass(user_key):
    code, rows = _sb(f"sfx_cert_passes?user_key=eq.{_q(user_key)}&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


def _paid_row(user_key):
    code, rows = _sb(f"sfx_cert_payments?user_key=eq.{_q(user_key)}&state=eq.paid&order=confirmed_at.asc&limit=1&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


def _latest_row(user_key, state=None):
    flt = f"&state=eq.{state}" if state else ""
    code, rows = _sb(f"sfx_cert_payments?user_key=eq.{_q(user_key)}{flt}&order=created_at.desc&limit=1&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


def _payment_summary(user_key, issued=False):
    paid = _paid_row(user_key)
    if paid:
        return {"state": "paid", "reference": paid["reference"]}
    if issued:
        return {"state": "paid", "reference": None}
    last = _latest_row(user_key)
    if not last:
        return {"state": "none", "reference": None}
    state = last["state"] if last["state"] in ("pending", "failed") else "none"
    return {"state": state, "reference": last["reference"]}


def _issue_after_payment(user_key):
    """Creates the certificate from the locked pass record. Only runs when a confirmed payment exists."""
    c = _existing_cert(user_key)
    if c:
        return c
    if not _paid_row(user_key):
        print(f"[CERT PAY] refused to issue for {user_key}: no confirmed payment", flush=True)
        return None
    pas = _get_pass(user_key)
    if not pas:
        print(f"[CERT PAY] paid but no pass record for {user_key}", flush=True)
        return None
    code, rows = _sb("sfx_certificates", "POST",
                     {"user_key": user_key, "holder_name": pas["holder_name"], "score": pas["score"]},
                     prefer="return=representation")
    c = rows[0] if code in (200, 201) and isinstance(rows, list) and rows else _existing_cert(user_key)
    if c:
        print(f"[CERT PAY] certificate {c['cert_no']} issued to {user_key}", flush=True)
    else:
        print(f"[CERT PAY] certificate save FAILED for {user_key} (HTTP {code})", flush=True)
    return c


def _korapay_verify(reference):
    """Korapay's own record of a charge (GET /charges/{reference}), or None."""
    key = _korapay_key()
    if not key:
        return None
    try:
        r = requests.get(f"{KORAPAY_BASE}/charges/{_q(reference)}", headers={"Authorization": f"Bearer {key}"}, timeout=15)
        j = r.json()
        d = j.get("data") if isinstance(j, dict) else None
        return d if isinstance(d, dict) else None
    except Exception as e:
        print(f"[CERT PAY] verify error {type(e).__name__}", flush=True)
        return None


def _amount_matches(got, expected):
    try:
        return abs(float(got) - float(expected)) < 0.005
    except Exception:
        return False


def _settle_success(row, data, source):
    """A success report (verified webhook or Korapay verify call) for one payment row.
    Checks status, currency and the exact amount, processes each reference once, then issues the certificate."""
    ref, uk = row["reference"], row["user_key"]
    if str(data.get("status") or "").lower() != "success":
        print(f"[CERT PAY] {source} {ref}: status {data.get('status')!r}, not paid", flush=True)
        return False
    cur = str(data.get("currency") or "").upper()
    if cur != CERT_CURRENCY:
        _sb(f"sfx_cert_payments?reference=eq.{_q(ref)}&state=neq.paid", "PATCH", {"note": f"rejected {source}: currency {cur}"})
        print(f"[CERT PAY] {source} {ref} user={uk}: REJECTED currency {cur}", flush=True)
        return False
    if not _amount_matches(data.get("amount"), row["amount"]):
        _sb(f"sfx_cert_payments?reference=eq.{_q(ref)}&state=neq.paid", "PATCH",
            {"note": f"rejected {source}: amount {data.get('amount')} expected {row['amount']}"})
        print(f"[CERT PAY] {source} {ref} user={uk}: REJECTED amount {data.get('amount')} expected {row['amount']}", flush=True)
        return False
    already = _paid_row(uk)
    note = f"confirmed via {source}"
    if already and already["reference"] != ref:
        note = f"DUPLICATE payment (already paid under {already['reference']}) via {source} - refund needed"
    code, rows = _sb(f"sfx_cert_payments?reference=eq.{_q(ref)}&state=in.(pending,failed)", "PATCH",
                     {"state": "paid", "korapay_status": "success", "source": source, "note": note,
                      "confirmed_at": datetime.now(timezone.utc).isoformat()}, prefer="return=representation")
    if code not in (200, 201, 204) or not rows:
        print(f"[CERT PAY] {source} {ref}: already processed", flush=True)
        return True
    print(f"[CERT PAY] PAID user={uk} ref={ref} amount={row['amount']} {cur} via {source}: {note}", flush=True)
    _issue_after_payment(uk)
    return True


def _recheck_pending(row):
    ref = row["reference"]
    now = time.time()
    if now - _verify_seen.get(ref, 0) < VERIFY_THROTTLE_SECONDS:
        return
    _verify_seen[ref] = now
    data = _korapay_verify(ref)
    if not data:
        return
    st = str(data.get("status") or "").lower()
    if st == "success":
        _settle_success(row, data, "verify")
    elif st == "failed":
        _sb(f"sfx_cert_payments?reference=eq.{_q(ref)}&state=eq.pending", "PATCH", {"state": "failed", "korapay_status": "failed", "source": "verify"})
        print(f"[CERT PAY] FAILED user={row['user_key']} ref={ref} (verify)", flush=True)


def handle_korapay_event(payload):
    """Called by the Korapay webhook (signature already verified) for references starting with CERT-.
    Never raises; the caller always answers 200."""
    try:
        event = str((payload or {}).get("event") or "")
        data = (payload or {}).get("data") or {}
        ref = str(data.get("reference") or data.get("payment_reference") or "")
        code, rows = _sb(f"sfx_cert_payments?reference=eq.{_q(ref)}&select=*")
        row = rows[0] if code == 200 and isinstance(rows, list) and rows else None
        if not row:
            print(f"[CERT PAY] webhook {event}: unknown reference {ref}, ignored", flush=True)
            return
        if not _get_pass(row["user_key"]) and event == "charge.success":
            print(f"[CERT PAY] webhook {event} {ref}: user {row['user_key']} has no pass record, ignored", flush=True)
            return
        if event == "charge.success":
            _settle_success(row, data, "webhook")
        elif event == "charge.failed":
            _sb(f"sfx_cert_payments?reference=eq.{_q(ref)}&state=eq.pending", "PATCH",
                {"state": "failed", "korapay_status": "failed", "source": "webhook"})
            print(f"[CERT PAY] FAILED user={row['user_key']} ref={ref} (webhook)", flush=True)
        else:
            print(f"[CERT PAY] webhook {event} {ref}: ignored", flush=True)
    except Exception as e:
        print(f"[CERT PAY] webhook error {type(e).__name__}: {e}", flush=True)


def _pay_sync(me):
    price = _price()
    uk = me["key"]
    with _lock_for(uk):
        if _existing_cert(uk) or _paid_row(uk):
            _issue_after_payment(uk)
            return _reply("already_paid", price)
        if not _get_pass(uk):
            return _reply("not_passed", price)
        last = _latest_row(uk, "pending")
        if last:
            _recheck_pending(last)
            if _paid_row(uk):
                return _reply("already_paid", price)
            last = _latest_row(uk, "pending")
        if last and last.get("checkout_url") and _amount_matches(last["amount"], price) \
                and time.time() - _ts(last["created_at"]) < CHECKOUT_REUSE_SECONDS:
            return _reply("payment_pending", price, {"url": last["checkout_url"], "reference": last["reference"]})
        key = _korapay_key()
        if not key:
            print("[CERT PAY] KORAPAY_SECRET_KEY missing", flush=True)
            return _reply("payment_failed", price, message="Payments are not available right now. Please try again later.")
        safe = re.sub(r"[^A-Za-z0-9]", "", str(uk))[:40] or "user"
        reference = f"CERT-{safe}-{int(time.time())}"
        email = (me.get("email") or "").strip()
        if "@" not in email:
            email = os.getenv("CERT_FALLBACK_EMAIL") or "spiritfxtradingacademy@gmail.com"
        body = {"amount": price, "currency": CERT_CURRENCY, "reference": reference,
                "redirect_url": f"{PUBLIC_BASE}/cert-payment/done",
                "customer": {"name": me.get("name") or "SpiritFX Student", "email": email},
                "narration": "SpiritFX Trading Academy certificate fee"}
        hook = os.getenv("KORAPAY_WEBHOOK_URL")
        if hook:
            body["notification_url"] = hook
        try:
            r = requests.post(f"{KORAPAY_BASE}/charges/initialize", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=20)
            j = r.json()
        except Exception as e:
            print(f"[CERT PAY] init error {type(e).__name__}", flush=True)
            return _reply("payment_failed", price, message="We could not reach the payment provider. Please try again.")
        d = j.get("data") if isinstance(j, dict) else None
        url = (d or {}).get("checkout_url") if isinstance(d, dict) else None
        if not (isinstance(j, dict) and j.get("status")) or not url:
            print(f"[CERT PAY] init failed for {uk}: {str(j)[:200]}", flush=True)
            return _reply("payment_failed", price, message="We could not start the payment. Please try again.")
        ref = str((d or {}).get("reference") or reference)
        _sb("sfx_cert_payments", "POST", {"reference": ref, "user_key": uk, "amount": price, "currency": CERT_CURRENCY,
                                          "state": "pending", "checkout_url": url})
        print(f"[CERT PAY] checkout created user={uk} ref={ref} amount={price} {CERT_CURRENCY}", flush=True)
        return _reply("payment_pending", price, {"url": url, "reference": ref})


async def pay_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    body, status = await asyncio.to_thread(_pay_sync, me)
    return _j(body, status)


async def pay_done_handler(request):
    page = ("<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Payment received</title></head><body style=\"font-family:system-ui,sans-serif;text-align:center;padding:48px 20px;"
            "background:#0f1422;color:#fff\"><h2>Payment completed</h2><p>You can return to the SpiritFX app now. "
            "Your certificate appears there as soon as the payment is confirmed.</p></body></html>")
    return web.Response(text=page, content_type="text/html")


# --------------------------------------------------------------------------- image

def _font(size, serif=False, bold=False):
    from PIL import ImageFont
    import matplotlib
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    name = ("DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf") if serif else ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(os.path.join(base, name), size)
    except Exception:
        return ImageFont.load_default()


def render_certificate_png(cert):
    """Matches the approved SFXACADEMY mock-up: cream paper, double gold border, faint emblem watermark,
    logo on top, signature left, emblem seal centre, QR right."""
    from PIL import Image, ImageDraw
    import qrcode
    W, H = 2000, 1414
    cream, gold, gold_l, ink, grey, gold_d = (251, 247, 236), (184, 146, 58), (217, 196, 138), (27, 34, 48), (91, 100, 117), (138, 109, 36)
    img = Image.new("RGB", (W, H), cream)

    emblem = Image.open(os.path.join(ASSETS, "spiritfx_emblem.png")).convert("RGBA")
    wm = emblem.copy()
    wm.thumbnail((860, 860))
    alpha = wm.split()[3].point(lambda v: int(v * 0.07))
    wm.putalpha(alpha)
    img.paste(wm, ((W - wm.width) // 2, (H - wm.height) // 2 - 20), wm)

    d = ImageDraw.Draw(img)
    d.rectangle([56, 56, W - 56, H - 56], outline=gold, width=6)
    d.rectangle([80, 80, W - 80, H - 80], outline=gold_l, width=2)

    def center(text, y, font, fill=ink, spacing=0):
        if spacing:
            total = sum(d.textlength(c, font=font) for c in text) + spacing * (len(text) - 1)
            x = (W - total) / 2
            for c in text:
                d.text((x, y), c, font=font, fill=fill)
                x += d.textlength(c, font=font) + spacing
        else:
            w = d.textlength(text, font=font)
            d.text(((W - w) / 2, y), text, font=font, fill=fill)

    logo = Image.open(os.path.join(ASSETS, "spiritfx_logo.png")).convert("RGBA")
    logo.thumbnail((300, 190))
    img.paste(logo, ((W - logo.width) // 2, 110), logo)
    center("TRADING ACADEMY", 312, _font(24, bold=True), gold_d, spacing=9)
    center("Certificate of Completion", 372, _font(112, serif=True, bold=True), ink)
    center("This certifies that", 520, _font(30), grey)
    name = cert["holder_name"]
    size = 100
    f = _font(size, serif=True, bold=True)
    while d.textlength(name, font=f) > W - 420 and size > 40:
        size -= 4
        f = _font(size, serif=True, bold=True)
    center(name, 575, f, ink)
    nw = d.textlength(name, font=f)
    lw = max(nw + 100, 700)
    d.line([((W - lw) / 2, 700), ((W + lw) / 2, 700)], fill=gold, width=3)
    center("has successfully completed the Beginner and Advanced courses and passed the final exam of the", 742, _font(30), grey)
    center(PROGRAMME, 796, _font(52, bold=True), ink)

    # bottom row: signature | seal | QR
    base = H - 300
    sig = Image.open(os.path.join(ASSETS, "ceo_signature.png")).convert("RGBA")
    sig.thumbnail((340, 150))
    img.paste(sig, (260 + (300 - sig.width) // 2, base - 150 + 40), sig)
    d.line([(240, base + 50), (580, base + 50)], fill=ink, width=2)
    sw = d.textlength(SIGNER_NAME, font=_font(26, bold=True))
    d.text((410 - sw / 2, base + 62), SIGNER_NAME, font=_font(26, bold=True), fill=ink)
    tw = d.textlength(SIGNER_TITLE, font=_font(23))
    d.text((410 - tw / 2, base + 100), SIGNER_TITLE, font=_font(23), fill=grey)

    cx, cy, r = W // 2, base - 10, 92
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255), outline=gold, width=6)
    em = emblem.copy()
    em.thumbnail((140, 140))
    img.paste(em, (cx - em.width // 2, cy - em.height // 2), em)
    try:
        issued = datetime.fromisoformat(str(cert["issued_at"]).replace("Z", "+00:00")).strftime("%d %B %Y")
    except Exception:
        issued = ""
    center(f"Issued {issued}", cy + r + 22, _font(26), grey)

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(f"{PUBLIC_BASE}/verify/{cert['cert_no']}")
    qr.make(fit=True)
    qimg = qr.make_image(fill_color=(27, 34, 48), back_color="white").convert("RGB").resize((200, 200))
    qx = W - 240 - 200
    img.paste(qimg, (qx, base - 120))
    cn = cert["cert_no"]
    cw = d.textlength(cn, font=_font(28, bold=True))
    d.text((qx + 100 - cw / 2, base + 92), cn, font=_font(28, bold=True), fill=ink)
    sc = d.textlength("Scan to verify", font=_font(22))
    d.text((qx + 100 - sc / 2, base + 132), "Scan to verify", font=_font(22), fill=grey)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _public_cert_sync(cert_no):
    code, rows = _sb(f"sfx_certificates?cert_no=eq.{requests.utils.quote(cert_no, safe='')}&select=*")
    return rows[0] if code == 200 and isinstance(rows, list) and rows else None


async def image_handler(request):
    cert_no = request.match_info["no"].upper()
    if cert_no.endswith(".PNG"):
        cert_no = cert_no[:-4]
    c = await asyncio.to_thread(_public_cert_sync, cert_no)
    if not c:
        return web.Response(status=404, text="Certificate not found")
    png = _png_cache.get(cert_no)
    if png is None:
        png = await asyncio.to_thread(render_certificate_png, c)
        if len(_png_cache) > 200:
            _png_cache.clear()
        _png_cache[cert_no] = png
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "public, max-age=300"})


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>
body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f3f5f7;color:#1c2026}}
.wrap{{max-width:760px;margin:0 auto;padding:28px 18px}}
.badge{{display:inline-block;padding:6px 14px;border-radius:99px;font-weight:700;font-size:14px;background:{bg};color:{fg}}}
h1{{font-size:26px;margin:14px 0 4px}} p{{margin:4px 0;color:#5b6470}}
.card{{background:#fff;border:1px solid #dfe3e8;border-radius:16px;padding:20px;margin-top:18px}}
.row{{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #eef0f3;gap:12px}}
.row:last-child{{border:0}} .row b{{text-align:right}} img{{width:100%;border-radius:10px;margin-top:16px;border:1px solid #dfe3e8}}
</style></head><body><div class="wrap">{body}</div></body></html>"""


async def verify_handler(request):
    cert_no = request.match_info["no"].upper()
    c = await asyncio.to_thread(_public_cert_sync, cert_no)
    if not c:
        body = ('<span class="badge">Not found</span><h1>Certificate not found</h1>'
                f'<p>No certificate with number {html.escape(cert_no)} exists. Check the number and try again.</p>')
        return web.Response(text=PAGE.format(title="Certificate not found", bg="#fde8e8", fg="#b42318", body=body),
                            content_type="text/html", status=404)
    try:
        issued = datetime.fromisoformat(str(c["issued_at"]).replace("Z", "+00:00")).strftime("%d %B %Y")
    except Exception:
        issued = ""
    n = html.escape(c["cert_no"])
    body = (f'<span class="badge">&#10003; Valid certificate</span><h1>{html.escape(c["holder_name"])}</h1>'
            f'<p>has completed the {html.escape(c["programme"])}.</p>'
            '<div class="card">'
            f'<div class="row"><span>Certificate number</span><b>{n}</b></div>'
            f'<div class="row"><span>Programme</span><b>{html.escape(c["programme"])}</b></div>'
            f'<div class="row"><span>Issued</span><b>{issued}</b></div>'
            f'<div class="row"><span>Issued by</span><b>SpiritFX Trading Academy</b></div>'
            f'<img src="/cert/{n}.png" alt="Certificate {n}"></div>')
    return web.Response(text=PAGE.format(title=f"Certificate {n}", bg="#e3f6ec", fg="#0e6b45", body=body),
                        content_type="text/html")


async def verify_json_handler(request):
    """Public JSON lookup used by the SpiritFX website's verify page."""
    cors = {"Access-Control-Allow-Origin": "*"}
    cert_no = request.match_info["no"].upper()
    c = await asyncio.to_thread(_public_cert_sync, cert_no)
    if not c:
        return web.json_response({"valid": False, "cert_no": cert_no}, status=404, headers=cors)
    return web.json_response({
        "valid": True, "cert_no": c["cert_no"], "holder_name": c["holder_name"],
        "programme": c["programme"], "issued_at": c["issued_at"],
        "issued_by": "SpiritFX Trading Academy",
        "image_url": f"{PUBLIC_BASE}/cert/{c['cert_no']}.png?v=2"}, headers=cors)


def _profile_public(me):
    return {"name": me["name"], "username": me["uname"], "avatar": me["avatar"], "since": me["since"]}


async def profile_get_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    return _j(_profile_public(me))


def _clean_name(raw):
    name = " ".join(str(raw or "").split())
    if len(name) < 2 or len(name) > 60:
        return None
    if not all(ch.isalpha() or ch in " .'-" for ch in name) or not any(ch.isalpha() for ch in name):
        return None
    return name


def _profile_save_sync(me, data):
    import re
    name = _clean_name(data.get("name")) if data.get("name") is not None else me["name"]
    if not name:
        return {"error": "name", "message": "Enter your full name (letters only, 2 to 60 characters)."}, 400
    uname = str(data.get("username") if data.get("username") is not None else me["uname"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", uname):
        return {"error": "username", "message": "Username must be 3 to 20 letters, numbers or _."}, 400
    avatar = data.get("avatar") if data.get("avatar") is not None else me["avatar"]
    if avatar and avatar not in AVATARS:
        return {"error": "avatar", "message": "Pick one of the icons."}, 400
    if _existing_cert(me["key"]) and name != me["name"]:
        return {"error": "locked", "message": "Your certificate is already issued, so your name can no longer be changed."}, 409
    low = uname.lower()
    code, rows = _sb(f"sfx_profiles?or=(username_lower.eq.{_q(low)},orig_uname_lower.eq.{_q(low)})&user_key=neq.{_q(me['key'])}&select=user_key&limit=1")
    if code == 200 and isinstance(rows, list) and rows:
        return {"error": "taken", "message": "That username is already taken."}, 409
    row = {"user_key": me["key"], "display_name": name, "username": uname, "username_lower": low,
           "avatar": avatar or None, "updated_at": datetime.now(timezone.utc).isoformat()}
    code, _r = _sb("sfx_profiles?on_conflict=user_key", "POST", row, prefer="resolution=merge-duplicates,return=minimal")
    if code == 409:
        return {"error": "taken", "message": "That username is already taken."}, 409
    if code not in (200, 201, 204):
        return {"error": "server", "message": "Could not save. Try again."}, 503
    return {"name": name, "username": uname, "avatar": avatar or "", "since": me["since"]}, 200


async def profile_save_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    try:
        data = await request.json()
    except Exception:
        data = {}
    body, status = await asyncio.to_thread(_profile_save_sync, me, data if isinstance(data, dict) else {})
    return _j(body, status)


def _video_event_sync(me, data):
    cid = str(data.get("course_id") or "").strip()[:80]
    vid = str(data.get("video_id") or "").strip()[:80]
    if not cid or not vid:
        return {"error": "bad_request", "message": "Missing course or video."}, 400
    lvl = str(data.get("lvl") or "").strip()[:20]
    total = _to_int(data.get("course_total"))
    row = {"user_key": me["key"], "course_id": cid, "video_id": vid}
    if lvl:
        row["lvl"] = lvl
    if 0 < total < 1000:
        row["course_total"] = total
    try:
        duration = float(data.get("duration") or 0)
        played = float(data.get("played") or 0)
    except Exception:
        duration, played = 0.0, 0.0
    # Counts as watched only when the student really played 80% of the video.
    if duration >= MIN_VIDEO_SECONDS and played >= WATCH_PERCENT * duration and played <= duration * 4:
        row["watched"] = True
        row["watched_at"] = datetime.now(timezone.utc).isoformat()
        _sb("sfx_video_events?on_conflict=user_key,course_id,video_id", "POST", row,
            prefer="resolution=merge-duplicates")
        return {"ok": True, "watched": True}, 200
    _sb("sfx_video_events", "POST", row, prefer="resolution=ignore-duplicates")
    return {"ok": True, "watched": False}, 200


def _watched_sync(me):
    code, rows = _sb(f"sfx_video_events?user_key=eq.{requests.utils.quote(me['key'], safe='')}"
                     "&watched=eq.true&select=course_id,video_id&limit=5000")
    out = {}
    if code == 200 and isinstance(rows, list):
        for r in rows:
            out.setdefault(str(r.get("course_id")), []).append(str(r.get("video_id")))
    return {"watched": out}, 200


async def watched_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    body, status = await asyncio.to_thread(_watched_sync, me)
    return _j(body, status)


async def progress_event_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    try:
        data = await request.json()
    except Exception:
        data = {}
    body, status = await asyncio.to_thread(_video_event_sync, me, data if isinstance(data, dict) else {})
    return _j(body, status)


def register(app, sb_headers, supabase_url, validate_token, user_url):
    _deps.update(sb_headers=sb_headers, supabase_url=supabase_url, validate_token=validate_token, user_url=user_url)
    app.router.add_get("/api/nexora/cert/status", status_handler)
    app.router.add_post("/api/nexora/cert/exam/start", start_handler)
    app.router.add_post("/api/nexora/cert/exam/submit", submit_handler)
    app.router.add_post("/api/nexora/cert/pay", pay_handler)
    app.router.add_get("/cert-payment/done", pay_done_handler)
    app.router.add_post("/api/nexora/progress", progress_event_handler)
    app.router.add_get("/api/nexora/progress/watched", watched_handler)
    app.router.add_get("/api/nexora/profile", profile_get_handler)
    app.router.add_post("/api/nexora/profile", profile_save_handler)
    app.router.add_get("/cert/{no}", image_handler)
    app.router.add_get("/verify/{no}", verify_handler)
    app.router.add_get("/api/public/cert/{no}", verify_json_handler)
