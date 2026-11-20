"""Translation for SpiritFX Academy: exam questions, AI Tutor lessons and quizzes.

One generic primitive: translate_texts(texts, language_name). Every translated text is cached forever in the
Supabase table sfx_translations (keyed by a hash of language + text), so each sentence is translated once and
every later student gets it instantly. Answer keys are never touched: callers translate the words only and keep
the same order of questions and options.

Languages: the list below is the only place a language lives. Adding a language to LANGUAGES makes it appear in
the app pickers (the app reads /api/nexora/languages) without an app update.
"""
import os
import re
import json
import hashlib
import logging

import requests

log = logging.getLogger("sfx_translate")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("SFX_TRANSLATE_MODEL", os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite"))
MAX_TEXTS_PER_CALL = 80
MAX_CHARS_PER_CALL = 60000
CHUNK_ITEMS = 24
CHUNK_CHARS = 9000

# (English name, native name). English first; the app shows both names.
LANGUAGES = [
    ("English", "English"), ("Spanish", "Español"), ("French", "Français"), ("Portuguese", "Português"),
    ("Arabic", "العربية"), ("Hindi", "हिन्दी"), ("Bengali", "বাংলা"), ("Urdu", "اردو"),
    ("Indonesian", "Bahasa Indonesia"), ("Malay", "Bahasa Melayu"), ("Swahili", "Kiswahili"),
    ("Hausa", "Hausa"), ("Yoruba", "Yorùbá"), ("Igbo", "Igbo"), ("Nigerian Pidgin", "Naijá"),
    ("Amharic", "አማርኛ"), ("Zulu", "isiZulu"), ("Xhosa", "isiXhosa"), ("Afrikaans", "Afrikaans"),
    ("Somali", "Soomaali"), ("Shona", "chiShona"), ("Twi (Akan)", "Twi"), ("Wolof", "Wolof"),
    ("Kinyarwanda", "Kinyarwanda"), ("Malagasy", "Malagasy"), ("Oromo", "Afaan Oromoo"),
    ("Chinese (Simplified)", "简体中文"), ("Chinese (Traditional)", "繁體中文"), ("Japanese", "日本語"),
    ("Korean", "한국어"), ("Vietnamese", "Tiếng Việt"), ("Thai", "ไทย"), ("Filipino (Tagalog)", "Filipino"),
    ("Burmese", "မြန်မာ"), ("Khmer", "ខ្មែរ"), ("Lao", "ລາວ"), ("Mongolian", "Монгол"),
    ("Russian", "Русский"), ("Ukrainian", "Українська"), ("Polish", "Polski"), ("Czech", "Čeština"),
    ("Slovak", "Slovenčina"), ("Hungarian", "Magyar"), ("Romanian", "Română"), ("Bulgarian", "Български"),
    ("Serbian", "Српски"), ("Croatian", "Hrvatski"), ("Bosnian", "Bosanski"), ("Slovenian", "Slovenščina"),
    ("Macedonian", "Македонски"), ("Albanian", "Shqip"), ("Greek", "Ελληνικά"), ("Turkish", "Türkçe"),
    ("German", "Deutsch"), ("Dutch", "Nederlands"), ("Italian", "Italiano"), ("Swedish", "Svenska"),
    ("Norwegian", "Norsk"), ("Danish", "Dansk"), ("Finnish", "Suomi"), ("Icelandic", "Íslenska"),
    ("Estonian", "Eesti"), ("Latvian", "Latviešu"), ("Lithuanian", "Lietuvių"), ("Irish", "Gaeilge"),
    ("Welsh", "Cymraeg"), ("Catalan", "Català"), ("Basque", "Euskara"), ("Galician", "Galego"),
    ("Maltese", "Malti"), ("Luxembourgish", "Lëtzebuergesch"), ("Persian (Farsi)", "فارسی"),
    ("Hebrew", "עברית"), ("Pashto", "پښتو"), ("Kurdish", "Kurdî"), ("Azerbaijani", "Azərbaycanca"),
    ("Georgian", "ქართული"), ("Armenian", "Հայերեն"), ("Kazakh", "Қазақша"), ("Uzbek", "Oʻzbekcha"),
    ("Kyrgyz", "Кыргызча"), ("Tajik", "Тоҷикӣ"), ("Turkmen", "Türkmençe"), ("Tamil", "தமிழ்"),
    ("Telugu", "తెలుగు"), ("Kannada", "ಕನ್ನಡ"), ("Malayalam", "മലയാളം"), ("Marathi", "मराठी"),
    ("Gujarati", "ગુજરાતી"), ("Punjabi", "ਪੰਜਾਬੀ"), ("Odia", "ଓଡ଼ିଆ"), ("Assamese", "অসমীয়া"),
    ("Nepali", "नेपाली"), ("Sinhala", "සිංහල"), ("Sindhi", "سنڌي"), ("Dhivehi", "ދިވެހި"),
    ("Haitian Creole", "Kreyòl ayisyen"), ("Quechua", "Runasimi"), ("Guarani", "Avañeʼẽ"),
    ("Maori", "Te Reo Māori"), ("Samoan", "Gagana Samoa"), ("Hawaiian", "ʻŌlelo Hawaiʻi"),
    ("Javanese", "Basa Jawa"), ("Sundanese", "Basa Sunda"), ("Cebuano", "Cebuano"),
    ("Esperanto", "Esperanto"), ("Latin", "Latina"),
]
_BY_KEY = {}
for _n, _nat in LANGUAGES:
    _BY_KEY[_n.lower()] = _n
    _BY_KEY.setdefault(_nat.lower(), _n)
    _BY_KEY.setdefault(re.sub(r"\s*\(.*\)", "", _n).lower(), _n)
    _m = re.search(r"\((.*)\)", _n)
    if _m and not _m.group(1).lower().startswith(("simplified", "traditional")):
        _BY_KEY.setdefault(_m.group(1).lower(), _n)


def resolve_language(value):
    """Returns the canonical English language name, or None when unknown / English / empty."""
    v = str(value or "").strip().lower()
    if not v:
        return None
    name = _BY_KEY.get(v)
    if not name or name == "English":
        return None
    return name


def languages_out():
    return [{"name": n, "native": nat} for n, nat in LANGUAGES]


def _h(lang, text):
    return hashlib.sha256((lang + "\n" + text).encode("utf-8")).hexdigest()


def _cache_get(sb, hashes):
    out = {}
    for i in range(0, len(hashes), 60):
        part = hashes[i:i + 60]
        code, rows = sb("sfx_translations?h=in.(" + ",".join(part) + ")&select=h,out")
        if code == 200 and isinstance(rows, list):
            for r in rows:
                out[r["h"]] = r["out"]
    return out


def _cache_put(sb, rows):
    if not rows:
        return
    for i in range(0, len(rows), 50):
        sb("sfx_translations", "POST", rows[i:i + 50], prefer="resolution=ignore-duplicates,return=minimal")


def _gemini(items, lang):
    """items: list of strings. Returns a list of translated strings (same length) or None."""
    if not GEMINI_API_KEY:
        return None
    prompt = (
        f"Translate every string in the JSON array below from English into {lang}.\n"
        "Rules: keep the meaning exact and the tone simple and clear, for beginner forex and trading students.\n"
        "Keep numbers, prices, percentages, pip values, currency pairs (EURUSD, XAUUSD), tickers and brand names "
        "(SpiritFX, MetaTrader, Exness, Deriv) unchanged. Keep trading terms that traders commonly use in English "
        "(pip, lot, spread, leverage, stop loss, take profit, bullish, bearish) in English or in the form traders "
        "in that language use; when you do translate one, add the English term in brackets the first time.\n"
        "Keep any HTML tags, Markdown symbols, line breaks and emoji exactly where they are; translate only the "
        "words. Do not add or remove items and do not merge items.\n"
        "Return ONLY a JSON array of strings with exactly the same number of items in the same order.\n\n"
        + json.dumps(items, ensure_ascii=False)
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}}
    for _ in range(2):
        try:
            r = requests.post(url, json=body, timeout=90)
            if r.status_code != 200:
                log.warning("translate gemini %s %s", r.status_code, r.text[:200])
                continue
            txt = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt)
            arr = json.loads(txt)
            if isinstance(arr, list) and len(arr) == len(items) and all(isinstance(x, str) for x in arr):
                return arr
            log.warning("translate gemini bad shape: %s items for %s", len(arr) if isinstance(arr, list) else "?", len(items))
        except Exception as e:
            log.warning("translate gemini error: %s", e)
    return None


def translate_texts(texts, language, sb):
    """Translate a list of strings into `language` (canonical name). Returns (list, all_ok).
    Texts that could not be translated come back unchanged (English) and all_ok is False."""
    texts = [t if isinstance(t, str) else "" for t in texts]
    lang = resolve_language(language)
    if not lang:
        return texts, True
    hashes = [_h(lang, t) for t in texts]
    cached = _cache_get(sb, list({h for h, t in zip(hashes, texts) if t.strip()}))
    result = list(texts)
    todo = []
    for i, (h, t) in enumerate(zip(hashes, texts)):
        if not t.strip():
            continue
        if h in cached:
            result[i] = cached[h]
        else:
            todo.append(i)
    ok = True
    # one translation per distinct text
    distinct = {}
    for i in todo:
        distinct.setdefault(texts[i], []).append(i)
    pending = list(distinct.keys())
    chunk, size, chunks = [], 0, []
    for t in pending:
        if chunk and (len(chunk) >= CHUNK_ITEMS or size + len(t) > CHUNK_CHARS):
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.append(t)
        size += len(t)
    if chunk:
        chunks.append(chunk)
    new_rows = []
    for ch in chunks:
        out = _gemini(ch, lang)
        if out is None:
            ok = False
            continue
        for src, dst in zip(ch, out):
            if not dst.strip():
                ok = False
                continue
            for i in distinct[src]:
                result[i] = dst
            new_rows.append({"h": _h(lang, src), "lang": lang, "out": dst})
    _cache_put(sb, new_rows)
    return result, ok
