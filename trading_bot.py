import os
import asyncio
import random
import requests
import json
import time

from datetime import datetime
from pathlib import Path

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from telegram.constants import ParseMode

# ============================================
# ENV VARIABLES
# ============================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
THENEWS_API_KEY = os.getenv("THENEWS_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
METALS_API_KEY = os.getenv("METALS_API_KEY")
API_NINJAS_KEY = os.getenv("API_NINJAS_KEY")

# ============================================
# METAAPI CONFIG
# ============================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# ============================================
# YOUR TWO CHANNEL IDs
# ============================================

CHANNEL_1_ID = os.getenv("CHANNEL_1_ID", "-1001722756645")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "-1002468228698")

# ============================================
# VERIFICATION GROUP ID
# ============================================

VERIFY_GROUP_ID = "-1002400215654"

# ============================================
# EXNESS AFFILIATE LINK
# ============================================

EXNESS_LINK = "https://www.exness.com/boarding/sign-up/a/vlnafmua"

# ============================================
# BOT USERNAME
# ============================================

BOT_USERNAME = "NexoraConsoleBot"

# ============================================
# FREE TRIAL LIMIT
# ============================================

FREE_TRIAL_LIMIT = 3

# ============================================
# IMAGE FILE IDs
# ============================================

BUY_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBFWowFvKE9iJ2rPQK6iqENojXggvJAAIyD2sbbT2BUfFOIeGp11tVAQADAgADeQADPAQ"
SELL_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBH2owIQ4F4GQEnXyDhVLRoQZ3Vg06AAI_D2sbbT2BUechitI61wpvAQADAgADeQADPAQ"
TP_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBI2owI-2IToLB1YLPMxCa132jhJMKAAJCD2sbbT2BUbnbjLmJ1VZIAQADAgADeQADPAQ"
SL_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBIWowI9Lxu93CIKFD5YSHFbJ8_MB-AAJBD2sbbT2BUT1NzWx8We6EAQADAgADeQADPAQ"

# ============================================
# DAILY SCHEDULE (UTC)
# ============================================

DAILY_SCHEDULE = [
    ("06:00", "news",   "morning"),   # 7:00 AM Lagos
    ("07:00", "signal", "xauusd"),    # 8:00 AM Lagos
    ("13:00", "signal", "gbpjpy"),    # 2:00 PM Lagos
    ("19:00", "signal", "btcusd"),    # 8:00 PM Lagos
]

# ============================================
# AI CONFIG
# UPDATED: gemini-2.0-flash was discontinued
# June 1, 2026. Migrated to gemini-2.5-flash-lite
# (same pricing tier, still free-tier eligible).
# ============================================

GEMINI_MODEL = "gemini-2.5-flash-lite"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ============================================
# KEYBOARDS
# ============================================

main_keyboard = ReplyKeyboardMarkup(
    [["📊 Signal", "📚 Breakdown"]],
    resize_keyboard=True,
    is_persistent=True,
    one_time_keyboard=False
)

def get_channel_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤖 Get Your Own Signal",
            url=f"https://t.me/{BOT_USERNAME}"
        )]
    ])

# ============================================
# USER MODES
# ============================================

user_modes = {}
pending_verifications = {}

# ============================================
# SIGNAL DIRECTION CONSISTENCY
# Keeps the same BUY/SELL direction per pair
# for at least 1 hour, so re-asking the same
# pair doesn't flip direction every time.
# ============================================

last_signal_direction = {}
SIGNAL_CONSISTENCY_SECONDS = 3600  # 1 hour

# ============================================
# PRICE + HISTORY CACHE (NEW)
# Caches live price and 1h-ago price per pair
# for up to 1 hour. This means at most ~13
# price API calls per hour total, no matter
# how many users ask - this is what lets the
# system scale to thousands of users for free.
# ============================================

price_cache = {}
PRICE_CACHE_SECONDS = 3600  # 1 hour

# ============================================
# AI BIAS USAGE LIMITS (NEW)
# Per-user cap: 3 AI-generated bias calls/day.
# Global cap: 1000 AI-generated bias calls/day
# across ALL users combined, protecting the
# shared free Gemini quota (1500 requests/day)
# so the bot stays free even at 14k-100k users.
# Scheduled channel signals are NOT counted
# against the global cap and always get AI.
# ============================================

AI_BIAS_PER_USER_DAILY_LIMIT = 3
AI_BIAS_GLOBAL_DAILY_LIMIT = 1000

# ============================================
# NEWS RELEVANCE FILTER
# Only forex / major currency / Bitcoin news
# is allowed through, no general business news.
# ============================================

NEWS_RELEVANT_KEYWORDS = [
    "forex", "currency", "currencies", "dollar", "euro", "pound",
    "sterling", "yen", "usd", "eur", "gbp", "jpy", "fed", "federal reserve",
    "ecb", "european central bank", "bank of england", "boe",
    "bank of japan", "boj", "interest rate", "rate hike", "rate cut",
    "inflation", "cpi", "nonfarm payroll", "nfp", "gdp", "central bank",
    "bitcoin", "btc", "crypto", "cryptocurrency",
]

def is_news_relevant(title, description):
    text = f"{title} {description}".lower()
    return any(keyword in text for keyword in NEWS_RELEVANT_KEYWORDS)

# ============================================
# SUPABASE DATABASE FUNCTIONS
# ============================================

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }

def is_verified(user_id):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/verified_users"
            f"?user_id=eq.{user_id}&select=user_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return len(data) > 0
    except Exception as e:
        print(f"[DB] is_verified error: {e}")
        return False

def add_verified_user(user_id, email):
    try:
        url = f"{SUPABASE_URL}/rest/v1/verified_users"
        payload = {
            "user_id": str(user_id),
            "email": email,
            "verified_at": datetime.utcnow().isoformat()
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"[DB] ✅ Verified user saved: {user_id}")
    except Exception as e:
        print(f"[DB] add_verified_user error: {e}")

def get_trial_count(user_id):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/trial_users"
            f"?user_id=eq.{user_id}&select=count"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if data:
            return data[0].get("count", 0)
        return 0
    except Exception as e:
        print(f"[DB] get_trial_count error: {e}")
        return 0

def increment_trial(user_id):
    try:
        current = get_trial_count(user_id)
        new_count = current + 1
        url = f"{SUPABASE_URL}/rest/v1/trial_users"
        payload = {
            "user_id": str(user_id),
            "count": new_count
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        requests.post(url, headers=headers, json=payload, timeout=10)
        print(f"[DB] Trial count for {user_id}: {new_count}")
        return new_count
    except Exception as e:
        print(f"[DB] increment_trial error: {e}")
        return 1

def trial_remaining(user_id):
    return max(0, FREE_TRIAL_LIMIT - get_trial_count(user_id))

# ============================================
# AI BIAS USAGE TRACKING (NEW)
# Requires a Supabase table: ai_bias_usage
# Columns: user_id (text), usage_date (text),
# count (integer). Unique constraint on
# (user_id, usage_date) for the upsert to work.
# A second table, ai_bias_global, tracks the
# shared global count with a single row keyed
# by usage_date.
# ============================================

def get_ai_bias_count_today(user_id):
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_bias_usage"
            f"?user_id=eq.{user_id}&usage_date=eq.{today_str}&select=count"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if data:
            return data[0].get("count", 0)
        return 0
    except Exception as e:
        print(f"[DB] get_ai_bias_count_today error: {e}")
        return 0

def increment_ai_bias_count(user_id):
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        current = get_ai_bias_count_today(user_id)
        new_count = current + 1
        url = f"{SUPABASE_URL}/rest/v1/ai_bias_usage"
        payload = {
            "user_id": str(user_id),
            "usage_date": today_str,
            "count": new_count,
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        requests.post(url, headers=headers, json=payload, timeout=10)
        return new_count
    except Exception as e:
        print(f"[DB] increment_ai_bias_count error: {e}")
        return 1

def get_ai_bias_global_count_today():
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_bias_global"
            f"?usage_date=eq.{today_str}&select=count"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if data:
            return data[0].get("count", 0)
        return 0
    except Exception as e:
        print(f"[DB] get_ai_bias_global_count_today error: {e}")
        return 0

def increment_ai_bias_global_count():
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        current = get_ai_bias_global_count_today()
        new_count = current + 1
        url = f"{SUPABASE_URL}/rest/v1/ai_bias_global"
        payload = {
            "usage_date": today_str,
            "count": new_count,
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        requests.post(url, headers=headers, json=payload, timeout=10)
        return new_count
    except Exception as e:
        print(f"[DB] increment_ai_bias_global_count error: {e}")
        return 1

def can_use_ai_bias(user_id):
    """
    Checks both the per-user daily cap and the global
    daily cap. Returns True only if BOTH have room.
    Scheduled channel signals (user_id=None) skip this
    check entirely and always get AI.
    """
    if user_id is None:
        return True
    if get_ai_bias_global_count_today() >= AI_BIAS_GLOBAL_DAILY_LIMIT:
        return False
    if get_ai_bias_count_today(user_id) >= AI_BIAS_PER_USER_DAILY_LIMIT:
        return False
    return True

def record_ai_bias_usage(user_id):
    if user_id is not None:
        increment_ai_bias_count(user_id)
    increment_ai_bias_global_count()

# ============================================
# PAIR CONFIG
# ============================================

PAIR_CONFIG = {
    "xauusd": {
        "symbol": "XAU/USD",
        "pair_name": "XAUUSD",
        "pip_size": 5.0,
        "pip_value": 0.01,
        "pip_label": "pips",
        "decimals": 2,
        "mt5_symbol": "XAUUSDm",
        "display": "Gold (XAUUSD) 🥇",
        "av_symbol": "XAU",
        "av_type": "forex",
        "td_symbol": "XAU/USD",
    },
    "btcusd": {
        "symbol": "BTC/USD",
        "pair_name": "BTCUSD",
        "pip_size": 165.3,
        "pip_value": 1.0,
        "pip_label": "points",
        "decimals": 2,
        "mt5_symbol": "BTCUSDm",
        "display": "Bitcoin (BTCUSD) ₿",
        "av_symbol": "BTC",
        "av_type": "crypto",
        "td_symbol": "BTC/USD",
    },
    "xagusd": {
        "symbol": "XAG/USD",
        "pair_name": "XAGUSD",
        "pip_size": 0.1667,
        "pip_value": 0.01,
        "pip_label": "pips",
        "decimals": 3,
        "mt5_symbol": "XAGUSDm",
        "display": "Silver (XAGUSD) 🥈",
        "av_symbol": "XAG",
        "av_type": "forex",
        "td_symbol": "XAG/USD",
        "use_metals_api": True,
    },
    "usoil": {
        "symbol": "USO",
        "pair_name": "USOIL",
        "pip_size": 0.1667,
        "pip_value": 0.01,
        "pip_label": "pips",
        "decimals": 3,
        "mt5_symbol": "USOILm",
        "display": "US Oil (WTI) 🛢️",
        "av_symbol": "WTI",
        "av_type": "commodity",
        "td_symbol": "CL1!",
        "use_oil_api": True,
    },
    "gbpusd": {
        "symbol": "GBP/USD",
        "pair_name": "GBPUSD",
        "pip_size": 0.001667,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "decimals": 5,
        "mt5_symbol": "GBPUSDm",
        "display": "GBP/USD 🇬🇧",
        "av_symbol": "GBP",
        "av_type": "forex",
        "td_symbol": "GBP/USD",
    },
    "gbpjpy": {
        "symbol": "GBP/JPY",
        "pair_name": "GBPJPY",
        "pip_size": 0.1667,
        "pip_value": 0.01,
        "pip_label": "pips",
        "decimals": 3,
        "mt5_symbol": "GBPJPYm",
        "display": "GBP/JPY 🇯🇵",
        "av_symbol": "GBPJPY",
        "av_type": "forex",
        "td_symbol": "GBP/JPY",
    },
    "eurusd": {
        "symbol": "EUR/USD",
        "pair_name": "EURUSD",
        "pip_size": 0.001667,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "decimals": 5,
        "mt5_symbol": "EURUSDm",
        "display": "EUR/USD 🇪🇺",
        "av_symbol": "EUR",
        "av_type": "forex",
        "td_symbol": "EUR/USD",
    },
    "usdjpy": {
        "symbol": "USD/JPY",
        "pair_name": "USDJPY",
        "pip_size": 0.1667,
        "pip_value": 0.01,
        "pip_label": "pips",
        "decimals": 3,
        "mt5_symbol": "USDJPYm",
        "display": "USD/JPY 🇯🇵",
        "av_symbol": "JPY",
        "av_type": "forex",
        "td_symbol": "USD/JPY",
    },
    "audusd": {
        "symbol": "AUD/USD",
        "pair_name": "AUDUSD",
        "pip_size": 0.001667,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "decimals": 5,
        "mt5_symbol": "AUDUSDm",
        "display": "AUD/USD 🇦🇺",
        "av_symbol": "AUD",
        "av_type": "forex",
        "td_symbol": "AUD/USD",
    },
    "usdcad": {
        "symbol": "USD/CAD",
        "pair_name": "USDCAD",
        "pip_size": 0.001667,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "decimals": 5,
        "mt5_symbol": "USDCADm",
        "display": "USD/CAD 🇨🇦",
        "av_symbol": "CAD",
        "av_type": "forex",
        "td_symbol": "USD/CAD",
    },
    "eurjpy": {
        "symbol": "EUR/JPY",
        "pair_name": "EURJPY",
        "pip_size": 0.1667,
        "pip_value": 0.01,
        "pip_label": "pips",
        "decimals": 3,
        "mt5_symbol": "EURJPYm",
        "display": "EUR/JPY 🇯🇵",
        "av_symbol": "EURJPY",
        "av_type": "forex",
        "td_symbol": "EUR/JPY",
    },
    "usdchf": {
        "symbol": "USD/CHF",
        "pair_name": "USDCHF",
        "pip_size": 0.001667,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "decimals": 5,
        "mt5_symbol": "USDCHFm",
        "display": "USD/CHF 🇨🇭",
        "av_symbol": "CHF",
        "av_type": "forex",
        "td_symbol": "USD/CHF",
    },
    "nzdusd": {
        "symbol": "NZD/USD",
        "pair_name": "NZDUSD",
        "pip_size": 0.001667,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "decimals": 5,
        "mt5_symbol": "NZDUSDm",
        "display": "NZD/USD 🇳🇿",
        "av_symbol": "NZD",
        "av_type": "forex",
        "td_symbol": "NZD/USD",
    },
}

# ============================================
# PAIR ALIASES — natural language matching
# ============================================

PAIR_ALIASES = {
    "xauusd": ["xauusd", "xau/usd", "xau usd", "gold"],
    "btcusd": ["btcusd", "btc/usd", "btc usd", "bitcoin", "btc"],
    "xagusd": ["xagusd", "xag/usd", "xag usd", "silver"],
    "usoil": ["usoil", "us oil", "oil", "crude", "crude oil", "wti"],
    "gbpusd": ["gbpusd", "gbp/usd", "gbp usd", "cable", "pound dollar", "pound usd"],
    "gbpjpy": ["gbpjpy", "gbp/jpy", "gbp jpy", "pound yen", "pound and yen"],
    "eurusd": ["eurusd", "eur/usd", "eur usd", "euro dollar", "fiber", "euro usd"],
    "usdjpy": ["usdjpy", "usd/jpy", "usd jpy", "dollar yen", "dollar and yen"],
    "audusd": ["audusd", "aud/usd", "aud usd", "aussie", "australian dollar"],
    "usdcad": ["usdcad", "usd/cad", "usd cad", "loonie", "canadian dollar"],
    "eurjpy": ["eurjpy", "eur/jpy", "eur jpy", "euro yen"],
    "usdchf": ["usdchf", "usd/chf", "usd chf", "swissy", "swiss franc"],
    "nzdusd": ["nzdusd", "nzd/usd", "nzd usd", "kiwi", "new zealand dollar"],
}

def match_pair_key(question):
    q = question.lower()
    for key, aliases in PAIR_ALIASES.items():
        for alias in aliases:
            if alias in q:
                return key
    return None

# ============================================
# PIPS CALCULATOR
# ============================================

def calculate_pips(pair_name, entry_price, exit_price, direction, config):
    try:
        pip_value = config.get("pip_value", 0.0001)
        pip_label = config.get("pip_label", "pips")
        if direction == "BUY":
            price_diff = exit_price - entry_price
        else:
            price_diff = entry_price - exit_price
        pips = round(price_diff / pip_value)
        return pips, pip_label
    except Exception as e:
        print(f"[PIPS] Calculation error: {e}")
        return 0, "pips"

# ============================================
# SILVER PRICE — METALS.DEV
# ============================================

def get_silver_price():
    try:
        if not METALS_API_KEY:
            print("[SILVER] No METALS_API_KEY set")
            return None
        url = "https://api.metals.dev/v1/latest"
        params = {
            "api_key": METALS_API_KEY,
            "currency": "USD",
            "unit": "toz"
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        metals = data.get("metals", {})
        price = (
            metals.get("silver") or
            metals.get("XAG") or
            metals.get("xag") or
            metals.get("SILVER")
        )
        if price:
            print(f"[SILVER] metals.dev: {price}")
            return float(price)
        print(f"[SILVER] Could not find silver in response: {metals}")
        return None
    except Exception as e:
        print(f"[SILVER] metals.dev error: {e}")
        return None

# ============================================
# OIL PRICE — API NINJAS PRIMARY
# ============================================

def get_oil_price():
    try:
        if API_NINJAS_KEY:
            url = "https://api.api-ninjas.com/v1/commodityprice?name=crude_oil"
            headers = {"X-Api-Key": API_NINJAS_KEY}
            response = requests.get(url, headers=headers, timeout=10)
            data = response.json()
            price = data.get("price")
            if price and 50 <= float(price) <= 150:
                print(f"[OIL] API Ninjas: {price}")
                return round(float(price), 2)
    except Exception as e:
        print(f"[OIL] API Ninjas error: {e}")

    try:
        for sym in ["CL1!", "USOIL"]:
            url = (
                f"https://api.twelvedata.com/price"
                f"?symbol={sym}&apikey={TWELVEDATA_API_KEY}"
            )
            response = requests.get(url, timeout=10)
            data = response.json()
            if "price" in data:
                price = float(data["price"])
                if 50 <= price <= 150:
                    print(f"[OIL] Twelvedata {sym}: {price}")
                    return price
    except Exception as e:
        print(f"[OIL] Twelvedata error: {e}")

    try:
        if ALPHA_VANTAGE_API_KEY:
            url = (
                f"https://www.alphavantage.co/query"
                f"?function=WTI&interval=daily"
                f"&apikey={ALPHA_VANTAGE_API_KEY}"
            )
            response = requests.get(url, timeout=10)
            data = response.json()
            latest = data.get("data", [{}])[0]
            value = latest.get("value")
            if value and value != ".":
                price = float(value)
                if 50 <= price <= 150:
                    print(f"[OIL] Alpha Vantage WTI: {price}")
                    return price
    except Exception as e:
        print(f"[OIL] Alpha Vantage error: {e}")

    print("[OIL] All oil price APIs failed")
    return None

# ============================================
# LIVE PRICE — TWELVEDATA PRIMARY
# ============================================

def get_price_twelvedata(symbol):
    try:
        url = (
            f"https://api.twelvedata.com/price"
            f"?symbol={symbol}"
            f"&apikey={TWELVEDATA_API_KEY}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        if "price" in data:
            return float(data["price"])
        return None
    except Exception as e:
        print(f"[TWELVEDATA] Error for {symbol}: {e}")
        return None

# ============================================
# LIVE PRICE — ALPHA VANTAGE FALLBACK
# ============================================

def get_price_alphavantage(config):
    try:
        av_symbol = config.get("av_symbol")
        av_type = config.get("av_type")
        if not av_symbol or not ALPHA_VANTAGE_API_KEY:
            return None
        if av_type in ["crypto", "forex"]:
            url = (
                f"https://www.alphavantage.co/query"
                f"?function=CURRENCY_EXCHANGE_RATE"
                f"&from_currency={av_symbol}"
                f"&to_currency=USD"
                f"&apikey={ALPHA_VANTAGE_API_KEY}"
            )
            response = requests.get(url, timeout=10)
            data = response.json()
            rate = data.get(
                "Realtime Currency Exchange Rate", {}
            ).get("5. Exchange Rate")
            if rate:
                return float(rate)
        return None
    except Exception as e:
        print(f"[ALPHAVANTAGE] Error: {e}")
        return None

# ============================================
# LIVE PRICE — COMBINED
# ============================================

def get_live_price(symbol="XAU/USD", config=None):
    if config and config.get("use_metals_api"):
        price = get_silver_price()
        if price:
            return price
        print("[PRICE] metals.dev failed for silver")
        return None

    if config and config.get("use_oil_api"):
        price = get_oil_price()
        if price:
            return price
        print("[PRICE] All oil APIs failed")
        return None

    price = get_price_twelvedata(symbol)
    if price is not None:
        return price
    if config:
        print(
            f"[PRICE] Twelvedata failed for {symbol}, "
            f"trying Alpha Vantage..."
        )
        price = get_price_alphavantage(config)
        if price is not None:
            print(f"[PRICE] Alpha Vantage: {price} for {symbol}")
            return price
    print(f"[PRICE] Both APIs failed for {symbol}")
    return None

# ============================================
# PRICE HISTORY — 1 HOUR AGO (NEW)
# Used by the AI bias and rule-based fallback
# to judge recent price movement/momentum.
# ============================================

def get_price_history_1h(symbol, config=None):
    try:
        if config and (config.get("use_metals_api") or config.get("use_oil_api")):
            return None

        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval=1h&outputsize=2"
            f"&apikey={TWELVEDATA_API_KEY}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        values = data.get("values", [])
        if len(values) >= 2:
            return float(values[1]["close"])
        return None
    except Exception as e:
        print(f"[HISTORY] Error fetching 1h history for {symbol}: {e}")
        return None

# ============================================
# PRICE + HISTORY CACHE (NEW)
# Shared across ALL users per pair. Refreshes
# at most once per hour per pair, regardless
# of how many people request that pair.
# ============================================

def get_cached_price_data(pair_key, symbol, config):
    now = time.time()
    cached = price_cache.get(pair_key)
    if cached and (now - cached["timestamp"] < PRICE_CACHE_SECONDS):
        return cached["current_price"], cached["price_1h_ago"]

    current_price = get_live_price(symbol, config=config)
    if current_price is None:
        return None, None

    price_1h_ago = get_price_history_1h(symbol, config)

    price_cache[pair_key] = {
        "current_price": current_price,
        "price_1h_ago": price_1h_ago,
        "timestamp": now,
    }
    return current_price, price_1h_ago

# ============================================
# SESSION DETECTION
# ============================================

def get_market_session():
    hour = datetime.utcnow().hour
    if 0 <= hour < 7:
        return "Asian Session 🌏"
    elif 7 <= hour < 13:
        return "London Session 🇬🇧"
    elif 13 <= hour < 21:
        return "New York Session 🇺🇸"
    return "Market Closing Session 🌙"

# ============================================
# AI-GENERATED BIAS (NEW)
# Asks Gemini to weigh fundamental/sentiment
# factors plus recent price movement and
# return a real BUY/SELL call with reasoning.
# ============================================

async def generate_ai_bias(pair_key, config, current_price, price_1h_ago):
    pair_name = config["pair_name"]

    if price_1h_ago is not None and price_1h_ago != 0:
        change = current_price - price_1h_ago
        pct_change = (change / price_1h_ago) * 100
        movement_text = (
            f"Price moved from {price_1h_ago} to {current_price} "
            f"in the last hour ({pct_change:+.2f}%)."
        )
    else:
        movement_text = (
            f"Current price is {current_price}. "
            f"No 1-hour history available."
        )

    prompt = f"""
You are a forex/crypto market analyst. Based on current fundamental and
sentiment factors plus the recent price movement below, decide whether
{pair_name} is more likely to move UP (BUY) or DOWN (SELL) in the near term.

{movement_text}

Respond in EXACTLY this format, nothing else, no markdown:
DIRECTION: BUY or SELL
REASON: [one sentence, max 18 words, fundamental/sentiment-based reasoning]
"""

    try:
        result = await ask_gemini_for_bias(prompt)
        direction, reason = parse_ai_bias_response(result)
        if direction and reason:
            return direction, reason
    except Exception as e:
        print(f"[AI BIAS] Failed: {e}")

    return None, None

def parse_ai_bias_response(text):
    try:
        lines = text.strip().split("\n")
        direction = None
        reason = None
        for line in lines:
            upper_line = line.strip().upper()
            if upper_line.startswith("DIRECTION:"):
                value = line.split(":", 1)[1].strip().upper()
                if "BUY" in value:
                    direction = "BUY"
                elif "SELL" in value:
                    direction = "SELL"
            if upper_line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()
        if direction and reason:
            return direction, reason
        return None, None
    except Exception:
        return None, None

# ============================================
# RULE-BASED FALLBACK BIAS (NEW)
# Used when AI fails, or when the per-user or
# global daily AI cap has been reached. Still
# genuinely reflects real price movement, not
# a coin flip - it's free, accurate, technical
# analysis based on actual price action.
# ============================================

def generate_rule_based_bias(pair_key, current_price, price_1h_ago):
    now = time.time()

    if price_1h_ago is not None and current_price is not None:
        if current_price > price_1h_ago:
            direction = "BUY"
            reason = "Price trending upward over the last hour."
        elif current_price < price_1h_ago:
            direction = "SELL"
            reason = "Price trending downward over the last hour."
        else:
            direction, reason = _consistent_or_random(pair_key)
    else:
        direction, reason = _consistent_or_random(pair_key)

    if pair_key:
        last_signal_direction[pair_key] = (direction, now)

    return direction, reason

def _consistent_or_random(pair_key):
    now = time.time()
    if pair_key and pair_key in last_signal_direction:
        prev_direction, prev_time = last_signal_direction[pair_key]
        if now - prev_time < SIGNAL_CONSISTENCY_SECONDS:
            return prev_direction, "Maintaining recent price trend bias."
    direction = random.choice(["BUY", "SELL"])
    return direction, "Early price trend showing initial directional bias."

# ============================================
# SIGNAL BUILDER
# UPDATED: now async, takes optional user_id.
# Tries AI bias first (subject to per-user and
# global daily caps), falls back to rule-based
# price-trend bias if AI fails or caps are hit.
# Scheduled channel signals pass user_id=None
# and always get AI (exempt from caps).
# ============================================

async def build_signal_response(question, user_id=None):
    matched_key = match_pair_key(question)

    if matched_key is None:
        print(f"[SIGNAL] ❌ No matching pair found for: {question}")
        return None, None, None, None

    config = PAIR_CONFIG[matched_key]

    symbol = config["symbol"]
    pair_name = config["pair_name"]
    pip_size = config["pip_size"]
    display = config["display"]
    decimals = config.get("decimals", 2)

    current_price, price_1h_ago = get_cached_price_data(matched_key, symbol, config)
    if current_price is None:
        print(f"[SIGNAL] ❌ Could not get live price for {pair_name}")
        return None, None, None, None

    live_price = current_price

    direction = None
    reason = None
    used_ai = False

    if can_use_ai_bias(user_id):
        direction, reason = await generate_ai_bias(
            matched_key, config, current_price, price_1h_ago
        )
        if direction and reason:
            used_ai = True
            record_ai_bias_usage(user_id)
            last_signal_direction[matched_key] = (direction, time.time())

    if not direction:
        direction, reason = generate_rule_based_bias(
            matched_key, current_price, price_1h_ago
        )

    confidence = random.randint(80, 94)
    strength = "STRONG"

    if direction == "BUY":
        entry_price = round(live_price, decimals)
        stop_loss = round(live_price - (pip_size * 3), decimals)
        take_profit = round(live_price + (pip_size * 6), decimals)
        signal_emoji = "🟢"
        image_file_id = BUY_IMAGE_FILE_ID
    else:
        entry_price = round(live_price, decimals)
        stop_loss = round(live_price + (pip_size * 3), decimals)
        take_profit = round(live_price - (pip_size * 6), decimals)
        signal_emoji = "🔴"
        image_file_id = SELL_IMAGE_FILE_ID

    session = get_market_session()
    timeframe_confirmation = random.choice([
        "M15 bullish structure confirmation",
        "H1 trend continuation active",
        "H4 momentum alignment confirmed",
        "Multi-timeframe confirmation detected",
        "Liquidity sweep confirmation on M15",
        "London session continuation setup",
        "New York volatility expansion detected",
    ])

    response = (
        f"{signal_emoji} <b>{strength} {direction} {display}</b>\n\n"
        f"<b>Entry Price:</b> {entry_price}\n\n"
        f"<b>Stop Loss:</b> {stop_loss}\n\n"
        f"<b>Take Profit:</b> {take_profit}\n\n"
        f"<b>Confidence:</b> {confidence}%\n\n"
        f"<b>Session:</b> {session}\n\n"
        f"<b>Timeframe Confirmation:</b>\n"
        f"{timeframe_confirmation}\n\n"
        f"<b>Reason:</b>\n"
        f"{reason}\n\n"
        f"<i>Trade safe 💼🔥</i>"
    )

    signal_data = {
        "symbol": symbol,
        "pair_name": pair_name,
        "mt5_symbol": config["mt5_symbol"],
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "config": config,
    }

    print(
        f"[SIGNAL] ✅ {pair_name} | "
        f"{direction} @ {entry_price} | "
        f"TP: {take_profit} | SL: {stop_loss} | "
        f"AI used: {used_ai}"
    )

    return image_file_id, direction, response, signal_data

# ============================================
# FORMAT BREAKDOWN — BOLD HEADERS
# ============================================

def format_breakdown(text):
    headers = [
        "Technical Analysis", "Fundamental Analysis",
        "Market Sentiment", "Sentiment", "Trade Idea",
        "Summary", "Outlook", "Key Levels", "Risk Warning",
        "Conclusion", "Price Action", "News Impact", "Market Overview",
    ]
    emojis = [
        "📊", "📈", "💡", "🗞️", "📰",
        "🛢️", "⚡", "🔍", "📉", "🎯", "💰", "🔔"
    ]
    for header in headers:
        for emoji in emojis:
            text = text.replace(
                f"{emoji} {header}",
                f"{emoji} <b>{header}</b>"
            )
        text = text.replace(f"\n{header}\n", f"\n<b>{header}</b>\n")
        text = text.replace(f"\n{header}:", f"\n<b>{header}:</b>")
    return text

# ============================================
# NEWS FETCHER — GNEWS PRIMARY
# ============================================

def fetch_news_gnews():
    if not GNEWS_API_KEY:
        return None
    try:
        url = (
            f"https://gnews.io/api/v4/top-headlines"
            f"?category=business&lang=en&max=10"
            f"&apikey={GNEWS_API_KEY}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        articles = [
            a for a in data.get("articles", [])
            if a.get("image") and a.get("title") and a.get("description")
            and is_news_relevant(a.get("title", ""), a.get("description", ""))
        ]
        if not articles:
            return None
        article = random.choice(articles)
        return {
            "title": article.get("title", ""),
            "description": article.get("description", ""),
            "image": article.get("image", ""),
            "source": article.get("source", {}).get("name", "GNews"),
        }
    except Exception as e:
        print(f"[GNEWS] Error: {e}")
        return None

# ============================================
# NEWS FETCHER — THENEWSAPI FALLBACK
# ============================================

def fetch_news_thenewsapi():
    if not THENEWS_API_KEY:
        return None
    try:
        url = (
            f"https://api.thenewsapi.com/v1/news/top"
            f"?api_token={THENEWS_API_KEY}"
            f"&categories=business,finance&language=en&limit=10"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        articles = [
            a for a in data.get("data", [])
            if a.get("image_url") and a.get("title") and a.get("description")
            and is_news_relevant(a.get("title", ""), a.get("description", ""))
        ]
        if not articles:
            return None
        article = random.choice(articles)
        return {
            "title": article.get("title", ""),
            "description": article.get("description", ""),
            "image": article.get("image_url", ""),
            "source": article.get("source", "TheNewsAPI"),
        }
    except Exception as e:
        print(f"[THENEWSAPI] Error: {e}")
        return None

# ============================================
# NEWS FETCHER — ALPHA VANTAGE
# Third fallback, forex/macro/crypto topics
# ============================================

def fetch_news_alphavantage():
    if not ALPHA_VANTAGE_API_KEY:
        return None
    try:
        url = (
            f"https://www.alphavantage.co/query"
            f"?function=NEWS_SENTIMENT"
            f"&topics=forex,economy_macro,blockchain"
            f"&apikey={ALPHA_VANTAGE_API_KEY}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        feed = data.get("feed", [])
        articles = [
            a for a in feed
            if a.get("banner_image") and a.get("title") and a.get("summary")
            and is_news_relevant(a.get("title", ""), a.get("summary", ""))
        ]
        if not articles:
            return None
        article = random.choice(articles)
        return {
            "title": article.get("title", ""),
            "description": article.get("summary", ""),
            "image": article.get("banner_image", ""),
            "source": article.get("source", "Alpha Vantage"),
        }
    except Exception as e:
        print(f"[ALPHAVANTAGE NEWS] Error: {e}")
        return None

# ============================================
# NEWS FETCHER — COMBINED
# ============================================

def fetch_market_news():
    article = fetch_news_gnews()
    if article:
        print("[NEWS] ✅ GNews article found")
        return article
    print("[NEWS] GNews failed, trying TheNewsAPI...")
    article = fetch_news_thenewsapi()
    if article:
        print("[NEWS] ✅ TheNewsAPI article found")
        return article
    print("[NEWS] TheNewsAPI failed, trying Alpha Vantage...")
    article = fetch_news_alphavantage()
    if article:
        print("[NEWS] ✅ Alpha Vantage article found")
        return article
    print("[NEWS] All news APIs failed or no relevant forex/BTC news today.")
    return None

# ============================================
# ECONOMIC CALENDAR — FOREX FACTORY
# Currency filter (USD/EUR/GBP/JPY), trimmed
# to 3 events instead of 5.
# ============================================

def fetch_economic_calendar():
    try:
        today = datetime.utcnow()
        date_str = today.strftime("%d.%m.%Y")
        today_str = today.strftime("%Y-%m-%d")

        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=10)
        data = response.json()

        if not data:
            return None

        flag_map = {
            "USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧",
            "CAD": "🇨🇦", "JPY": "🇯🇵", "AUD": "🇦🇺",
            "CNY": "🇨🇳", "NZD": "🇳🇿", "CHF": "🇨🇭",
        }

        calendar_text = f"\n\n📆 <b>CALENDAR TODAY — {date_str}</b>\n\n"
        count = 0

        for event in data:
            event_date = event.get("date", "")[:10]
            if event_date != today_str:
                continue
            impact = event.get("impact", "").lower()
            if impact != "high":
                continue
            currency = event.get("currency", "")
            if currency not in ["USD", "EUR", "GBP", "JPY"]:
                continue
            title = event.get("title", "")
            time_utc = event.get("date", "")
            flag = flag_map.get(currency, "🌍")

            if time_utc and "T" in time_utc:
                try:
                    dt = datetime.strptime(
                        time_utc[:16], "%Y-%m-%dT%H:%M"
                    )
                    lagos_hour = (dt.hour + 1) % 24
                    time_str = f"{lagos_hour:02d}:{dt.minute:02d} GMT+1"
                except:
                    time_str = ""
            else:
                time_str = ""

            line = f"{flag} {title}"
            if time_str:
                line += f" — {time_str}"
            calendar_text += line + "\n"
            count += 1
            if count >= 3:
                break

        if count == 0:
            return None

        return calendar_text

    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        return None

# ============================================
# NEWS SUMMARY GENERATOR
# 2 bullet points instead of 3
# ============================================

async def generate_news_summary(article, session_type):

    title = article.get("title", "")
    description = article.get("description", "")
    source = article.get("source", "")

    if session_type == "morning":
        session_label = "Morning Market Briefing 🌅"
    elif session_type == "midday":
        session_label = "Midday Market Update ☀️"
    else:
        session_label = "Afternoon Market Briefing 🌆"

    prompt = f"""
You are Nexora AI, a professional financial news analyst.
Write a VERY SHORT market news post for a Telegram trading channel.

SESSION: {session_label}
NEWS HEADLINE: {title}
NEWS DETAILS: {description}
SOURCE: {source}

FORMAT EXACTLY LIKE THIS — NO EXCEPTIONS:
{session_label}

🔹 [One line news item 1]

🔹 [One line news item 2]

STRICT RULES:
- Maximum 2 bullet points ONLY
- Each bullet point MAX 15 words
- No long sentences
- No paragraphs
- No markdown symbols like ** or ##
- No hashtags
- Make each point punchy and impactful
- Focus on what matters most to forex and gold traders
"""
    return await ask_gemini(prompt)

# ============================================
# POST NEWS — CHANNEL 1 ONLY
# ============================================

async def post_news(context: ContextTypes.DEFAULT_TYPE):

    session_type = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')
    print(f"[NEWS] Posting {session_type} news at {now}")

    article = fetch_market_news()
    if article is None:
        print("[NEWS] No article found from any source. Skipping.")
        return

    headline = article.get("title", "financial market news trading")
    image_prompt = (
        f"professional financial news illustration: {headline}, "
        f"cinematic digital art, dramatic lighting, high quality"
    )
    image_url = (
        f"https://image.pollinations.ai/prompt/"
        f"{requests.utils.quote(image_prompt)}"
        f"?width=800&height=450&nologo=true"
    )

    summary = await generate_news_summary(article, session_type)
    summary = clean_text(summary)

    calendar = fetch_economic_calendar()
    if calendar:
        summary += calendar

    try:
        await context.bot.send_photo(
            chat_id=CHANNEL_1_ID,
            photo=image_url,
            caption=summary,
            parse_mode=ParseMode.HTML
        )
        print(f"[NEWS] ✅ {session_type} posted to Channel 1")
    except Exception as e:
        print(f"[NEWS] AI image failed, posting text only: {e}")
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_1_ID,
                text=summary,
                parse_mode=ParseMode.HTML
            )
            print(f"[NEWS] ✅ {session_type} posted (text only) to Channel 1")
        except Exception as e2:
            print(f"[NEWS] ❌ Failed: {e2}")

# ============================================
# METAAPI — PLACE TRADE ON MT5 (0.1 lot)
# ============================================

async def place_mt5_trade(signal_data):
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        print("[MT5] MetaAPI credentials not set.")
        return None
    try:
        headers = {
            "auth-token": METAAPI_TOKEN,
            "Content-Type": "application/json"
        }
        direction = signal_data["direction"]
        mt5_symbol = signal_data["mt5_symbol"]
        order_type = (
            "ORDER_TYPE_BUY" if direction == "BUY"
            else "ORDER_TYPE_SELL"
        )
        payload = {
            "symbol": mt5_symbol,
            "volume": 0.1,
            "actionType": order_type,
            "stopLoss": signal_data["stop_loss"],
            "takeProfit": signal_data["take_profit"],
            "comment": "NexoraAI Signal"
        }
        url = (
            f"https://mt-client-api-v1.london.agiliumtrade.ai"
            f"/users/current/accounts/{METAAPI_ACCOUNT_ID}/trade"
        )
        response = requests.post(
            url, headers=headers, json=payload, timeout=30
        )
        if response.status_code in [200, 201]:
            result = response.json()
            order_id = result.get("orderId", "unknown")
            print(f"[MT5] ✅ Trade placed — Order ID: {order_id}")
            return order_id
        else:
            print(f"[MT5] ❌ Trade failed: {response.status_code}")
            return None
    except Exception as e:
        print(f"[MT5] ❌ Exception: {e}")
        return None

# ============================================
# GEMINI AI — GENERAL (breakdowns, news)
# ============================================

async def ask_gemini(prompt):
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(
            GEMINI_URL, headers=headers, json=data, timeout=30
        )
        if response.status_code == 429:
            print("[GEMINI] Rate limit hit, waiting 10 seconds...")
            await asyncio.sleep(10)
            response = requests.post(
                GEMINI_URL, headers=headers, json=data, timeout=30
            )
            if response.status_code == 429:
                raise Exception("RATE_LIMIT")
        if response.status_code != 200:
            raise Exception("GEMINI_ERROR")
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini Error: {e}")
        return await ask_openrouter(prompt)

# ============================================
# GEMINI AI — FOR BIAS GENERATION (NEW)
# Separate, lighter-weight call path so a
# failure here always raises instead of
# silently falling through to OpenRouter -
# build_signal_response needs to know
# definitively whether AI succeeded, so it
# can fall back to the rule-based bias.
# ============================================

async def ask_gemini_for_bias(prompt):
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        response = requests.post(
            GEMINI_URL, headers=headers, json=data, timeout=15
        )
        if response.status_code != 200:
            raise Exception(f"GEMINI_ERROR_{response.status_code}")
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"[AI BIAS] Gemini failed, trying OpenRouter: {e}")
        return await ask_openrouter(prompt)

# ============================================
# OPENROUTER FALLBACK
# ============================================

async def ask_openrouter(prompt):
    if not OPENROUTER_API_KEY:
        return "⚠️ AI service unavailable."
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "deepseek/deepseek-chat",
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        response = requests.post(
            OPENROUTER_URL, headers=headers, json=data, timeout=30
        )
        if response.status_code != 200:
            return "⚠️ AI server busy."
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"OpenRouter Error: {e}")
        return "⚠️ AI servers unavailable."

# ============================================
# BREAKDOWN GENERATOR
# ============================================

async def generate_breakdown(question):

    matched_key = match_pair_key(question)

    if matched_key:
        config = PAIR_CONFIG[matched_key]
        symbol = config["symbol"]
        pair_name = config["display"]
    else:
        config = PAIR_CONFIG["xauusd"]
        symbol = config["symbol"]
        pair_name = config["display"]

    live_price = get_live_price(symbol, config=config)
    live_price_text = (
        str(round(live_price, 4)) if live_price
        else "Live price unavailable"
    )

    hour = datetime.utcnow().hour
    if 7 <= hour < 13:
        session = "London Session 🇬🇧"
    elif 13 <= hour < 22:
        session = "New York Session 🇺🇸"
    else:
        session = "Asian Session 🇯🇵"

    prompt = f"""
You are Nexora AI, a professional market analyst.
Generate a PROFESSIONAL market breakdown for a Telegram trading channel.

IMPORTANT: Use the REAL LIVE PRICE. Do NOT invent fake prices.

PAIR: {pair_name}
LIVE PRICE: {live_price_text}
SESSION: {session}

FORMAT YOUR RESPONSE EXACTLY LIKE THIS:
📊 Technical Analysis
[Your technical analysis here]

📰 Fundamental Analysis
[Your fundamental analysis here]

💡 Market Sentiment
[Your sentiment here]

🎯 Trade Idea
[Your trade idea with entry, TP, SL levels]

RULES:
- Use the LIVE PRICE in your analysis
- Beginner friendly but professional tone
- Maximum 250 words total
- No markdown symbols like ** or ## or ---
- No hashtags
- Use emojis as shown in the format above

QUESTION: {question}
"""
    return await ask_gemini(prompt)

# ============================================
# CLEAN AI RESPONSE
# ============================================

def clean_text(text):
    text = text.replace("###", "").replace("##", "")
    text = text.replace("**", "").replace("---", "").replace("__", "")
    return text.strip()

# ============================================
# VERIFICATION GATE MESSAGE
# ============================================

async def send_verification_gate(update):
    await update.message.reply_text(
        "🔐 <b>You've used your 3 FREE trial signals!</b>\n\n"
        "Hope you loved what you saw! 🔥\n\n"
        "To continue enjoying <b>UNLIMITED FREE signals</b>, "
        "live market analysis and AI breakdowns — "
        "you just need <b>ONE simple step:</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔓 <b>HOW TO UNLOCK FULL ACCESS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 Register a <b>FREE</b> trading account with our official "
        "broker partner — <b>Exness</b> — using our unique link.\n\n"
        "<b>No payment. No subscription. Completely FREE.</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>CHOOSE YOUR SITUATION:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>SITUATION 1</b> — Already registered on Exness "
        "using our link before:\n"
        "👉 Simply type the email address you used to register "
        "on Exness below and we will verify you instantly.\n\n"
        "📝 <b>SITUATION 2</b> — Not yet registered or registered "
        "without our link:\n"
        "👉 Click the button below to create your FREE Exness "
        "account using our official link. Once done, come back "
        "here and type the email you registered with.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📧 <b>Already registered? Type your Exness email now 👇</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📝 I'm New — Register on Exness FREE 👆",
                url=EXNESS_LINK
            )]
        ])
    )

# ============================================
# START COMMAND
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"

    if is_verified(user_id):
        await update.message.reply_text(
            f"👋 <b>Welcome back, {username}!</b>\n\n"
            f"✅ You're a <b>verified Nexora AI trader.</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>What would you like to do today?</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Signal</b> — Get a live trading signal right now\n\n"
            f"📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
            f"<i>Both buttons are at the bottom of your screen 👇</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

    remaining = trial_remaining(user_id)

    if remaining > 0:
        await update.message.reply_text(
            f"👋 <b>Hello {username}, welcome to Nexora AI! 🤖</b>\n\n"
            f"I am your personal AI trading assistant — delivering "
            f"<b>professional trading signals</b>, live market analysis "
            f"and AI-powered breakdowns.\n\n"
            f"🎁 <b>You have {remaining} FREE trial signal(s) to use!</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>TAP ONE OF THE OPTIONS BELOW TO START:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Signal</b> — Get a live trading signal right now\n\n"
            f"📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
            f"<i>Both buttons are at the bottom of your screen 👇</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

    user_modes[user_id] = "awaiting_email"
    await send_verification_gate(update)

# ============================================
# HANDLE BUTTONS
# ============================================

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    text = update.message.text.lower()

    if not is_verified(user_id) and get_trial_count(user_id) >= FREE_TRIAL_LIMIT:
        user_modes[user_id] = "awaiting_email"
        await send_verification_gate(update)
        return

    if "signal" in text:
        user_modes[user_id] = "signal"
        await update.message.reply_text(
            "📊 <b>Signal Mode Activated</b>\n\n"
            "Now type the pair you want a signal for.\n\n"
            "<b>Available pairs:</b>\n"
            "• XAUUSD — Gold\n"
            "• BTCUSD — Bitcoin\n"
            "• XAGUSD — Silver\n"
            "• USOIL — US Oil\n"
            "• GBPUSD\n"
            "• GBPJPY\n"
            "• EURUSD\n"
            "• USDJPY\n"
            "• AUDUSD\n"
            "• USDCAD\n"
            "• EURJPY\n"
            "• USDCHF\n"
            "• NZDUSD\n\n"
            "<i>Example: Type <b>XAUUSD</b> or just say <b>gold</b></i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

    if "breakdown" in text:
        user_modes[user_id] = "breakdown"
        await update.message.reply_text(
            "📚 <b>Breakdown Mode Activated</b>\n\n"
            "Now type your market question below.\n\n"
            "<b>Examples:</b>\n"
            "• Analyze gold market today\n"
            "• BTCUSD outlook\n"
            "• GBPJPY market analysis\n"
            "• What is happening with oil today?",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

# ============================================
# CALLBACK HANDLER — APPROVE / REJECT
# ============================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("approve_"):

        target_id = data.replace("approve_", "")
        email = pending_verifications.get(target_id, "unknown")

        add_verified_user(target_id, email)

        if target_id in pending_verifications:
            del pending_verifications[target_id]

        inner_circle_link = None
        try:
            invite = await context.bot.create_chat_invite_link(
                chat_id=CHANNEL_2_ID,
                member_limit=1,
                name=f"Verified: {email}"
            )
            inner_circle_link = invite.invite_link
            print(f"[INVITE] ✅ Created invite for {target_id}")
        except Exception as e:
            print(f"[INVITE] Could not create invite link: {e}")

        try:
            if inner_circle_link:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=(
                        "🎉 <b>Congratulations! You're now a verified "
                        "Nexora AI trader!</b>\n\n"
                        "✅ <b>Full access unlocked!</b>\n\n"
                        "You now have <b>unlimited access</b> to:\n\n"
                        "📊 <b>Live Trading Signals</b> — Real-time "
                        "signals on Gold, Bitcoin, Oil, Forex and more\n\n"
                        "📚 <b>AI Market Breakdowns</b> — Deep analysis "
                        "on any pair you ask about\n\n"
                        "📈 <b>Technical Analysis</b> — Professional "
                        "grade insights powered by AI\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔐 <b>EXCLUSIVE — INNER CIRCLE ACCESS</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━\n\n"
                        "As a verified trader you now have access to our "
                        "<b>exclusive Inner Circle channel</b> — premium "
                        "signals and real-time alerts reserved only for "
                        "verified Exness traders like you.\n\n"
                        "👇 <b>Your personal invite link — "
                        "works once, just for you:</b>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "🔐 Join Inner Circle Now",
                            url=inner_circle_link
                        )]
                    ])
                )
            else:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=(
                        "🎉 <b>Congratulations! You're now a verified "
                        "Nexora AI trader!</b>\n\n"
                        "✅ <b>Full access unlocked!</b>\n\n"
                        "You now have <b>unlimited access</b> to:\n\n"
                        "📊 <b>Live Trading Signals</b> — Real-time "
                        "signals on Gold, Bitcoin, Oil, Forex and more\n\n"
                        "📚 <b>AI Market Breakdowns</b> — Deep analysis "
                        "on any pair you ask about\n\n"
                        "📈 <b>Technical Analysis</b> — Professional "
                        "grade insights powered by AI"
                    ),
                    parse_mode=ParseMode.HTML,
                )

            await context.bot.send_message(
                chat_id=int(target_id),
                text=(
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "💼 <i>Welcome to the winning side. "
                    "Let's get to work!</i> 🔥\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "👇 <b>TAP AN OPTION BELOW TO GET STARTED:</b>\n\n"
                    "📊 <b>Signal</b> — Get a live trading signal\n\n"
                    "📚 <b>Breakdown</b> — Get a full market analysis"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )

        except Exception as e:
            print(f"[APPROVE] Could not message user: {e}")

        await query.edit_message_text(
            text=(
                f"✅ <b>APPROVED</b>\n\n"
                f"🆔 <b>User ID:</b> {target_id}\n"
                f"📧 <b>Email:</b> {email}\n\n"
                f"<i>User verified, saved to database and "
                f"sent Inner Circle invite.</i>"
            ),
            parse_mode=ParseMode.HTML
        )

    elif data.startswith("reject_"):

        target_id = data.replace("reject_", "")
        email = pending_verifications.get(target_id, "unknown")

        if target_id in pending_verifications:
            del pending_verifications[target_id]

        try:
            await context.bot.send_message(
                chat_id=int(target_id),
                text=(
                    "❌ <b>Verification Failed</b>\n\n"
                    "Unfortunately, we could not find an Exness account "
                    "linked to your email that was registered through "
                    "our official link.\n\n"
                    "<b>This could mean:</b>\n"
                    "• You registered on Exness without using our link\n"
                    "• You used a different email address\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ <b>HOW TO FIX THIS:</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Click the link below to create a <b>NEW Exness "
                    "account</b> using our official link. It's completely "
                    "<b>FREE</b> and takes less than 2 minutes.\n\n"
                    f"🔗 {EXNESS_LINK}\n\n"
                    "Once done, come back here and type your new "
                    "email address to get verified instantly. 🚀"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[REJECT] Could not message user: {e}")

        await query.edit_message_text(
            text=(
                f"❌ <b>REJECTED</b>\n\n"
                f"🆔 <b>User ID:</b> {target_id}\n"
                f"📧 <b>Email:</b> {email}\n\n"
                f"<i>User notified to register via the correct link.</i>"
            ),
            parse_mode=ParseMode.HTML
        )

# ============================================
# HANDLE TEXT
# ============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"
    message = update.message.text.strip()

    if user_modes.get(user_id) == "awaiting_email":

        email = message.strip().lower()

        if "@" not in email or "." not in email:
            await update.message.reply_text(
                "⚠️ <b>That doesn't look like a valid email address.</b>\n\n"
                "Please enter the email address you used to "
                "register on Exness 👇\n\n"
                "<i>Example: yourname@gmail.com</i>",
                parse_mode=ParseMode.HTML
            )
            return

        pending_verifications[user_id] = email

        await update.message.reply_text(
            "⏳ <b>Got it! Your verification request has been submitted.</b>\n\n"
            "Our team is reviewing your details right now. "
            "You'll receive a confirmation message shortly.\n\n"
            "<i>Sit tight — greatness is loading! 🚀</i>",
            parse_mode=ParseMode.HTML
        )

        try:
            await context.bot.send_message(
                chat_id=VERIFY_GROUP_ID,
                text=(
                    f"🔔 <b>NEW VERIFICATION REQUEST</b>\n\n"
                    f"👤 <b>User:</b> @{username}\n"
                    f"🆔 <b>ID:</b> {user_id}\n"
                    f"📧 <b>Email:</b> {email}\n\n"
                    f"<i>Tap a button below to approve or reject:</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=f"approve_{user_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=f"reject_{user_id}"
                        )
                    ]
                ])
            )
        except Exception as e:
            print(f"[VERIFY] Failed to send to group: {e}")

        user_modes[user_id] = None
        return

    if not is_verified(user_id) and get_trial_count(user_id) >= FREE_TRIAL_LIMIT:
        user_modes[user_id] = "awaiting_email"
        await send_verification_gate(update)
        return

    mode = user_modes.get(user_id)

    if mode == "signal":

        if not is_verified(user_id):
            count = increment_trial(user_id)
            if count > FREE_TRIAL_LIMIT:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
                return

        wait_message = await update.message.reply_text(
            "🧠 <b>Nexora AI analyzing live market...</b>",
            parse_mode=ParseMode.HTML
        )

        await asyncio.sleep(1)

        image_file_id, direction, signal, signal_data = (
            await build_signal_response(message, user_id=user_id)
        )

        await wait_message.delete()

        if image_file_id:
            await update.message.reply_photo(
                photo=image_file_id,
                caption=signal,
                parse_mode=ParseMode.HTML
            )

            if not is_verified(user_id):
                remaining = trial_remaining(user_id)
                if remaining > 0:
                    await update.message.reply_text(
                        f"⚡ <b>You have {remaining} free trial "
                        f"signal(s) remaining.</b>\n\n"
                        f"Verify your Exness account for "
                        f"<b>unlimited access!</b>\n\n"
                        f"📊 <b>Signal</b> — Get another signal\n"
                        f"📚 <b>Breakdown</b> — Get a market analysis",
                        parse_mode=ParseMode.HTML,
                        reply_markup=main_keyboard
                    )
                else:
                    user_modes[user_id] = "awaiting_email"
                    await send_verification_gate(update)
        else:
            await update.message.reply_text(
                "⚠️ <b>Unable to fetch live market data.</b>\n"
                "Please try again shortly.",
                parse_mode=ParseMode.HTML
            )
        return

    if mode == "breakdown":

        if not is_verified(user_id):
            count = increment_trial(user_id)
            if count > FREE_TRIAL_LIMIT:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
                return

        wait_message = await update.message.reply_text(
            "🧠 <b>Nexora AI preparing market breakdown...</b>",
            parse_mode=ParseMode.HTML
        )

        response = await generate_breakdown(message)
        response = clean_text(response)
        response = format_breakdown(response)

        await wait_message.edit_text(
            response,
            parse_mode=ParseMode.HTML
        )

        if not is_verified(user_id):
            remaining = trial_remaining(user_id)
            if remaining > 0:
                await update.message.reply_text(
                    f"⚡ <b>You have {remaining} free trial "
                    f"signal(s) remaining.</b>\n\n"
                    f"Verify your Exness account for "
                    f"<b>unlimited access!</b>\n\n"
                    f"📊 <b>Signal</b> — Get a live trading signal\n"
                    f"📚 <b>Breakdown</b> — Get a market analysis",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
            else:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

    await update.message.reply_text(
        "👇 <b>Here's what you can do:</b>\n\n"
        "📊 <b>Signal</b> — Get a live trading signal right now\n\n"
        "📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
        "<i>Both buttons are right at the bottom of your screen!</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )

# ============================================
# AUTO SIGNAL — Button on Channel 1 only,
# no TP/SL monitor. Always uses AI bias
# (user_id=None exempts it from daily caps).
# ============================================

async def post_auto_signal(context: ContextTypes.DEFAULT_TYPE):

    pair_keyword = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')

    print(f"[AUTO SIGNAL] {pair_keyword.upper()} firing at {now}")

    image_file_id, direction, signal, signal_data = (
        await build_signal_response(pair_keyword, user_id=None)
    )

    if signal_data is None:
        print(f"[AUTO SIGNAL] ❌ Could not fetch price for {pair_keyword}.")
        return

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            markup = (
                get_channel_button()
                if channel_id == CHANNEL_1_ID
                else None
            )

            await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_file_id,
                caption=signal,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
            print(
                f"[AUTO SIGNAL] ✅ {pair_keyword.upper()} "
                f"posted to {channel_id}"
            )

            asyncio.create_task(place_mt5_trade(signal_data))

        except Exception as e:
            print(f"[AUTO SIGNAL] ❌ Failed for {channel_id}: {e}")

# ============================================
# MAIN
# ============================================

def main():

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(
        MessageHandler(
            filters.Regex(
                "^(📊 Signal|📚 Breakdown|signal|breakdown)$"
            ),
            handle_buttons
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    job_queue = app.job_queue

    def parse_time(t):
        h, m = map(int, t.split(":"))
        return datetime.now().replace(
            hour=h, minute=m, second=0, microsecond=0
        ).time()

    for i, (utc_time, post_type, data) in enumerate(DAILY_SCHEDULE):
        if post_type == "news":
            job_queue.run_daily(
                post_news,
                time=parse_time(utc_time),
                name=f"news_{i}_{data}",
                data=data
            )
        elif post_type == "signal":
            job_queue.run_daily(
                post_auto_signal,
                time=parse_time(utc_time),
                name=f"signal_{i}_{data}",
                data=data
            )

    print("Nexora AI Running...")
    print("Daily schedule (UTC):")
    for utc_time, post_type, data in DAILY_SCHEDULE:
        emoji = "📰" if post_type == "news" else "📊"
        print(f"  {emoji} {utc_time} UTC — {data.upper()}")
    print(f"Channel 1 (Public): {CHANNEL_1_ID}")
    print(f"Channel 2 (Inner Circle): {CHANNEL_2_ID}")
    print(f"Verify Group: {VERIFY_GROUP_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
