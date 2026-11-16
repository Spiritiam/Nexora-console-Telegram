"""SpiritFX Trading Program certificates: exam, issue, certificate image with QR, public verify page.

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
from datetime import datetime, timezone

import requests
from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "sfx_cert_assets")
PROGRAMME = "SpiritFX Trading Program"
SIGNER_NAME = "Henry Onyefunuwe Smith"
SIGNER_TITLE = "CEO, SpiritFX Trading Academy"
EXAM_BASIC = 8
EXAM_ADVANCED = 12
PASS_PERCENT = 70
RETRY_WAIT_SECONDS = 6 * 3600
EXAM_MAX_AGE_SECONDS = 3 * 3600
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
    base_name, base_uname, since, cert = "", "", "", ""
    try:
        r = requests.get(_deps["user_url"], headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=10)
        body = r.json()
        base_name = str(_find(body, "funame") or _find(body, "name") or "").strip()
        base_uname = str(_find(body, "uname") or "").strip()
        since = str(_find(body, "datetime") or "").strip()
        c = _find(body, "certificate")
        cert = str(c) if c is not None else ""
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
            "eligible": cert == "1"}


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
            "image_url": f"{PUBLIC_BASE}/cert/{c['cert_no']}.png"}


def _status_sync(me):
    c = _existing_cert(me["key"])
    if c:
        return {"eligible": True, "issued": _cert_public(c), "can_start": False, "wait_seconds": 0, "name": c["holder_name"]}
    wait = 0
    last = _last_attempt(me["key"])
    if last and last.get("submitted_at"):
        wait = max(0, int(_ts(last["submitted_at"]) + RETRY_WAIT_SECONDS - time.time()))
    return {"eligible": bool(me["eligible"]), "issued": None, "name": me["name"],
            "can_start": bool(me["eligible"]) and wait == 0, "wait_seconds": wait,
            "pass_percent": PASS_PERCENT, "questions": EXAM_BASIC + EXAM_ADVANCED}


async def status_handler(request):
    me, err = await _auth(request)
    if err:
        return err
    return _j(await asyncio.to_thread(_status_sync, me))


def _start_sync(me, confirmed):
    st = _status_sync(me)
    if st["issued"]:
        return {"error": "done", "message": "You already have your certificate."}, 409
    if not st["eligible"]:
        return {"error": "not_eligible", "message": "Finish the Beginner and Advanced courses first."}, 403
    if not confirmed:
        return {"error": "confirm", "message": "Please confirm the name that will be printed on your certificate."}, 400
    if not me["name"]:
        return {"error": "no_name", "message": "Add your full name in Edit profile first."}, 400
    if st["wait_seconds"] > 0:
        return {"error": "wait", "message": "Please wait before trying the exam again.", "wait_seconds": st["wait_seconds"]}, 429
    bank = _load_bank()
    basic = [q for q in bank.values() if q["level"] == "basic"]
    adv = [q for q in bank.values() if q["level"] == "advanced"]
    picked = random.sample(basic, EXAM_BASIC) + random.sample(adv, EXAM_ADVANCED)
    random.shuffle(picked)
    ids = [q["id"] for q in picked]
    code, rows = _sb("sfx_cert_attempts", "POST", {"user_key": me["key"], "question_ids": ids}, prefer="return=representation")
    if code not in (200, 201) or not rows:
        return {"error": "server", "message": "Could not start the exam. Try again."}, 503
    return {"attempt_id": rows[0]["id"], "pass_percent": PASS_PERCENT,
            "questions": [{"id": q["id"], "question": q["question"], "options": q["options"]} for q in picked]}, 200


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
    if not me["eligible"] and not _existing_cert(me["key"]):
        return {"error": "not_eligible", "message": "Finish the Beginner and Advanced courses first."}, 403
    bank = _load_bank()
    ids = at["question_ids"]
    if not isinstance(answers, list):
        answers = []
    right = 0
    for i, qid in enumerate(ids):
        try:
            if i < len(answers) and int(answers[i]) == int(bank[qid]["answer_index"]):
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
        if not c:
            name = me["name"] or "SpiritFX Student"
            code, rows = _sb("sfx_certificates", "POST",
                             {"user_key": me["key"], "holder_name": name, "score": percent}, prefer="return=representation")
            c = rows[0] if code in (200, 201) and rows else None
        if not c:
            return {"error": "server", "message": "Passed, but the certificate could not be saved. Open Certificate again."}, 503
        out["issued"] = _cert_public(c)
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
    return web.Response(body=png, content_type="image/png", headers={"Cache-Control": "public, max-age=3600"})


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
        "image_url": f"{PUBLIC_BASE}/cert/{c['cert_no']}.png"}, headers=cors)


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


def register(app, sb_headers, supabase_url, validate_token, user_url):
    _deps.update(sb_headers=sb_headers, supabase_url=supabase_url, validate_token=validate_token, user_url=user_url)
    app.router.add_get("/api/nexora/cert/status", status_handler)
    app.router.add_post("/api/nexora/cert/exam/start", start_handler)
    app.router.add_post("/api/nexora/cert/exam/submit", submit_handler)
    app.router.add_get("/api/nexora/profile", profile_get_handler)
    app.router.add_post("/api/nexora/profile", profile_save_handler)
    app.router.add_get("/cert/{no}", image_handler)
    app.router.add_get("/verify/{no}", verify_handler)
    app.router.add_get("/api/public/cert/{no}", verify_json_handler)
