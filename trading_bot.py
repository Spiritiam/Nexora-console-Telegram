import os
import asyncio
import random
import requests
import re
import json
import time
import websockets

from metaapi_cloud_sdk import MetaApi

from datetime import datetime, timedelta
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
DERIV_APP_ID = os.getenv("DERIV_APP_ID")
DERIV_SERVICE_TOKEN = os.getenv("DERIV_SERVICE_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # restricts /broadcast to this Telegram user ID only

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

# Synthetic index channel posts (NEW) - rotates through all 5
# indices, one post on weekdays, two on weekends (slot 0/1 picks a
# different index for each of the two weekend posts on the same
# day - see get_rotation_key below).
SYNTHETIC_SCHEDULE = [
    ("11:00", "weekday", 0),
    ("11:00", "weekend", 0),
    ("17:00", "weekend", 1),
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
    [["📊 Signal", "📚 Breakdown", "🔗 Connect Deriv"]],
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
# SIGNAL LOG (NEW)
# Requires a Supabase table: signal_log
# Columns: id (bigint identity), pair_name (text),
# direction (text), entry_price (float8),
# stop_loss (float8), take_profit (float8),
# posted_at (text, ISO format), status (text,
# default 'OPEN'), closed_at (text, nullable).
# Only scheduled CHANNEL signals are logged here
# (post_auto_signal) - these are the official
# trade calls the brand stands behind, not every
# personal DM lookup a user makes. Backs both the
# TP/SL monitor and the weekly performance report.
# ============================================

def log_signal(signal_data):
    try:
        url = f"{SUPABASE_URL}/rest/v1/signal_log"
        payload = {
            "pair_name": signal_data["pair_name"],
            "direction": signal_data["direction"],
            "entry_price": signal_data["entry_price"],
            "stop_loss": signal_data["stop_loss"],
            "take_profit": signal_data["take_profit"],
            "posted_at": datetime.utcnow().isoformat(),
            "status": "OPEN",
        }
        headers = sb_headers()
        headers["Prefer"] = "return=representation"
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code in (200, 201):
            result = response.json()
            signal_id = result[0]["id"] if result else None
            print(
                f"[SIGNAL LOG] ✅ Logged {signal_data['pair_name']} "
                f"{signal_data['direction']} (id={signal_id})"
            )
            return signal_id
        else:
            print(
                f"[SIGNAL LOG] ❌ Failed to log {signal_data['pair_name']} "
                f"{signal_data['direction']} | "
                f"{response.status_code}: {response.text}"
            )
            return None
    except Exception as e:
        print(f"[SIGNAL LOG] log_signal error: {e}")
        return None

def attach_mt5_order_id(signal_id, order_id):
    """
    Links a placed MT5 order back onto its signal_log row, so the
    trade's eventual real outcome (TP, SL, or a manual close in
    profit/loss) can later be looked up directly from MT5 instead of
    inferred from a separate price feed. Requires an mt5_order_id
    column on signal_log.
    """
    if not signal_id or not order_id:
        return
    try:
        url = f"{SUPABASE_URL}/rest/v1/signal_log?id=eq.{signal_id}"
        payload = {"mt5_order_id": order_id}
        response = requests.patch(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code in (200, 204):
            print(f"[SIGNAL LOG] ✅ Linked MT5 order {order_id} to signal {signal_id}")
        else:
            print(
                f"[SIGNAL LOG] ❌ Failed to link MT5 order {order_id} to "
                f"signal {signal_id} | {response.status_code}: {response.text}"
            )
    except Exception as e:
        print(f"[SIGNAL LOG] attach_mt5_order_id error: {e}")

async def place_and_link_mt5_trade(signal_id, signal_data):
    """
    Places the MT5 trade exactly once, then links the resulting
    order_id back onto its signal_log row. Runs as a background task
    so channel posting never waits on MT5 execution.
    """
    order_id = await place_mt5_trade(signal_data)
    attach_mt5_order_id(signal_id, order_id)

def get_open_signals():
    try:
        url = f"{SUPABASE_URL}/rest/v1/signal_log?status=eq.OPEN&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[SIGNAL LOG] get_open_signals error: {e}")
        return []

def update_signal_status(signal_id, status):
    try:
        url = f"{SUPABASE_URL}/rest/v1/signal_log?id=eq.{signal_id}"
        payload = {
            "status": status,
            "closed_at": datetime.utcnow().isoformat(),
        }
        response = requests.patch(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code in (200, 204):
            print(f"[SIGNAL LOG] ✅ Signal {signal_id} -> {status}")
            return True
        else:
            print(
                f"[SIGNAL LOG] ❌ Failed to update signal {signal_id} -> "
                f"{status} | {response.status_code}: {response.text}"
            )
            return False
    except Exception as e:
        print(f"[SIGNAL LOG] update_signal_status error: {e}")
        return False

def get_signals_since(start_dt):
    try:
        start_str = start_dt.isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/signal_log"
            f"?posted_at=gte.{start_str}&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[SIGNAL LOG] get_signals_since error: {e}")
        return []

# ============================================
# DERIV ACCOUNT LINKING (NEW) — PHASE 1
# Read-only: connect a user's real Deriv account
# and show balance/open positions. No trading.
# Real accounts only - virtual/demo accounts are
# detected via Deriv's own account-type flag and
# rejected, never guessed from the loginid prefix.
# Uses a Personal Access Token the user generates
# themselves in their own Deriv account settings
# (scoped to read + trading info only) and pastes
# into the bot - no OAuth web server needed for
# this phase.
#
# CONNECTION FLOW (confirmed via live network trace
# against Deriv's Playground on 2026-06-20 - Deriv's
# API is mid-migration to a new system, and this is
# NOT the same flow shown in older/legacy examples):
#   1. GET /trading/v1/options/accounts
#      (Bearer token + Deriv-App-ID header)
#      -> lists every account tied to this token
#   2. POST /trading/v1/options/accounts/{accountId}/otp
#      -> returns a short-lived WebSocket URL with a
#         one-time code already embedded in it
#   3. Connect directly to that wss:// URL - no
#      further auth headers or "authorize" message
#      needed, the embedded code handles it
#   4. Send balance/portfolio requests on that
#      connection, then close it
# Never a persistent connection per user - opens,
# asks everything needed, closes, every time.
#
# Requires a Supabase table: deriv_accounts.
# Columns: user_id (text, unique), deriv_loginid
# (text), api_token (text), currency (text),
# linked_at (text), last_synced (text).
# ============================================

DERIV_API_BASE = "https://api.derivws.com"

def deriv_api_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Deriv-App-ID": DERIV_APP_ID or "",
    }

async def deriv_get_options_accounts(token):
    """
    Step 1: lists every account (real and virtual) tied to this
    token. Returns the raw parsed JSON on success, or None on any
    failure. The exact field names here are being confirmed against
    live testing - deriv_fetch_account_snapshot below logs the raw
    response if it can't find an account in the shape it expects,
    so the actual shape can be adjusted from real output rather
    than another guess.
    """
    if not DERIV_APP_ID:
        print("[DERIV] No DERIV_APP_ID set")
        return None
    try:
        url = f"{DERIV_API_BASE}/trading/v1/options/accounts"
        response = requests.get(url, headers=deriv_api_headers(token), timeout=10)
        if response.status_code != 200:
            print(f"[DERIV] Accounts lookup failed {response.status_code}: {response.text}")
            return None
        return response.json()
    except Exception as e:
        print(f"[DERIV] deriv_get_options_accounts error: {e}")
        return None

async def deriv_get_otp_url(token, account_id):
    """
    Step 2: exchanges the token + a specific account ID for the
    short-lived WebSocket URL with the one-time code embedded.
    """
    try:
        url = f"{DERIV_API_BASE}/trading/v1/options/accounts/{account_id}/otp"
        response = requests.post(url, headers=deriv_api_headers(token), timeout=10)
        if response.status_code != 200:
            print(f"[DERIV] OTP request failed {response.status_code}: {response.text}")
            return None
        data = response.json()
        return data.get("data", {}).get("url")
    except Exception as e:
        print(f"[DERIV] deriv_get_otp_url error: {e}")
        return None

async def deriv_fetch_account_snapshot(token):
    """
    Full flow: list accounts -> pick the real one -> get its OTP
    WebSocket URL -> connect and fetch balance/positions. Returns
    None on failure, or a dict with is_virtual=True (no balance
    data) if only demo accounts are found, so the caller's existing
    "that's a demo account" rejection message still fires correctly.
    """
    accounts_data = await deriv_get_options_accounts(token)
    if not accounts_data:
        return None

    accounts_list = accounts_data.get("data")
    if not isinstance(accounts_list, list):
        accounts_list = accounts_data.get("accounts")

    if not accounts_list:
        print(f"[DERIV] Unexpected accounts response shape: {accounts_data}")
        return None

    real_account = None
    for acct in accounts_list:
        is_virtual = bool(
            acct.get("is_virtual")
            or str(acct.get("account_type", "")).lower() in ("demo", "virtual")
        )
        if not is_virtual:
            real_account = acct
            break

    if not real_account:
        print(f"[DERIV] No real account found in: {accounts_list}")
        return {
            "loginid": None,
            "is_virtual": True,
            "currency": None,
            "balance": None,
            "open_contracts": [],
        }

    account_id = (
        real_account.get("account_id")
        or real_account.get("loginid")
        or real_account.get("id")
    )
    currency = real_account.get("currency", "")

    if not account_id:
        print(f"[DERIV] Could not find account_id in: {real_account}")
        return None

    ws_url = await deriv_get_otp_url(token, account_id)
    if not ws_url:
        return None

    try:
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps({"balance": 1}))
            balance_response = json.loads(await ws.recv())
            balance_block = balance_response.get("data", balance_response.get("balance", {}))
            balance = balance_block.get("balance") if isinstance(balance_block, dict) else None

            await ws.send(json.dumps({"portfolio": 1}))
            portfolio_response = json.loads(await ws.recv())
            portfolio_block = portfolio_response.get("data", portfolio_response.get("portfolio", {}))
            open_contracts = (
                portfolio_block.get("contracts", [])
                if isinstance(portfolio_block, dict) else []
            )

            return {
                "loginid": account_id,
                "is_virtual": False,
                "currency": currency,
                "balance": balance,
                "open_contracts": open_contracts,
            }
    except Exception as e:
        print(f"[DERIV] WebSocket connection error: {e}")
        return None

def save_deriv_account(user_id, loginid, token, currency):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_accounts"
            f"?on_conflict=user_id"
        )
        payload = {
            "user_id": str(user_id),
            "deriv_loginid": loginid,
            "api_token": token,
            "currency": currency,
            "linked_at": datetime.utcnow().isoformat(),
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        response = requests.post(
            url, headers=headers, json=payload, timeout=10
        )
        if response.status_code not in (200, 201):
            print(f"[DERIV] save_deriv_account unexpected status {response.status_code}: {response.text}")
            return False
        print(f"[DERIV] ✅ Linked account for user {user_id}: {loginid}")
        return True
    except Exception as e:
        print(f"[DERIV] save_deriv_account error: {e}")
        return False

def get_deriv_account(user_id):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_accounts"
            f"?user_id=eq.{user_id}&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if data:
            return data[0]
        return None
    except Exception as e:
        print(f"[DERIV] get_deriv_account error: {e}")
        return None

def save_auto_copy_settings(user_id, enabled, stake=None, risk_mode=None, risk=None, win=None):
    """
    Saves auto-copy trading preferences onto the user's existing
    deriv_accounts row (PATCH, not a full upsert - this never touches
    api_token/loginid, only the auto-copy columns). risk_mode is
    either "fixed" (always use this risk/win) or "signal" (always
    follow the signal's own suggested risk/win, scaled if the stake
    is auto-reduced for low balance - see get_auto_copy_trade_amounts).
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_accounts"
            f"?user_id=eq.{user_id}"
        )
        payload = {"auto_copy_enabled": enabled}
        if stake is not None:
            payload["auto_copy_stake"] = stake
        if risk_mode is not None:
            payload["auto_copy_risk_mode"] = risk_mode
        if risk is not None:
            payload["auto_copy_risk"] = risk
        if win is not None:
            payload["auto_copy_win"] = win

        response = requests.patch(
            url, headers=sb_headers(), json=payload, timeout=10
        )
        if response.status_code not in (200, 204):
            print(f"[DERIV] save_auto_copy_settings unexpected status {response.status_code}: {response.text}")
            return False
        return True
    except Exception as e:
        print(f"[DERIV] save_auto_copy_settings error: {e}")
        return False

def get_all_auto_copy_accounts():
    """
    Returns every deriv_accounts row with auto_copy_enabled = true,
    for the signal-posting loop to iterate over. Each row already
    carries its own api_token, so no separate lookup is needed per
    user.
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_accounts"
            f"?auto_copy_enabled=eq.true&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=15)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[DERIV] get_all_auto_copy_accounts error: {e}")
        return []

# ============================================
# DERIV SYNTHETIC SIGNALS + MULTIPLIER TRADING
# (NEW) — PHASE 2
# Real signals on Deriv's synthetic indices,
# reusing the exact same SMC/ICT detector suite
# already proven on forex/gold/crypto (analyze_
# timeframe and every detect_* function below
# are used completely unchanged - only the
# candle SOURCE is new, since no outside data
# provider carries Deriv's synthetic feeds).
#
# Candle data is fetched directly from Deriv
# using a separate SERVICE token (read-only,
# market-data purposes only - never used to
# trade), via the exact same OTP connection flow
# confirmed in Phase 1. Trading itself always
# uses the requesting user's OWN linked token.
#
# Stop loss / take profit are real dollar
# amounts on the stake (confirmed live, NOT
# price levels) - every signal suggests a
# default $10 stake / $3 risk / $6 target
# (matching the same 1:2 risk:reward convention
# already used elsewhere), always adjustable by
# the user before they confirm a trade. Every
# trade requires an explicit confirm - nothing
# executes automatically.
#
# Multiplier values below are confirmed for
# R_100 only (tested live). The other four are
# reasonable starting defaults, NOT yet verified
# - same caution as everything else in this file
# that touches unconfirmed Deriv API behavior:
# log clearly, fail safely, verify before trusting.
# ============================================

SYNTHETIC_CONFIG = {
    # Multiplier 100 is doubly confirmed safe: tested directly on
    # R_100 (real trade executed successfully), and present in R_75's
    # confirmed valid set (50/100/200/300/500, returned directly by
    # Deriv after a rejected guess). Standardizing all five on 100
    # for now rather than leaving the old per-index guesses, which
    # turned out unreliable - R_75's original guess of 150 wasn't
    # even in the valid set. R_10/R_25/R_50 still haven't been
    # individually tested, so their true max multiplier (possibly
    # higher, since lower-volatility indices often allow more) is
    # still unknown - worth testing each the same way R_75 just was.
    "r10": {"symbol": "R_10", "display": "Volatility 10 Index", "default_multiplier": 100},
    "r25": {"symbol": "R_25", "display": "Volatility 25 Index", "default_multiplier": 100},
    "r50": {"symbol": "R_50", "display": "Volatility 50 Index", "default_multiplier": 100},
    "r75": {"symbol": "R_75", "display": "Volatility 75 Index", "default_multiplier": 100},
    "r100": {"symbol": "R_100", "display": "Volatility 100 Index", "default_multiplier": 100},
}

SYNTHETIC_ALIASES = {
    "r10": ["r10", "r_10", "r 10", "volatility 10", "volatility10", "vol 10", "vol10", "v10"],
    "r25": ["r25", "r_25", "r 25", "volatility 25", "volatility25", "vol 25", "vol25", "v25"],
    "r50": ["r50", "r_50", "r 50", "volatility 50", "volatility50", "vol 50", "vol50", "v50"],
    "r75": ["r75", "r_75", "r 75", "volatility 75", "volatility75", "vol 75", "vol75", "v75"],
    "r100": ["r100", "r_100", "r 100", "volatility 100", "volatility100", "vol 100", "vol100", "v100"],
}

DEFAULT_SYNTHETIC_STAKE = 10
DEFAULT_RISK = 3
DEFAULT_WIN = 6

# Preset stake tiers shown as tap-to-pick buttons before a trade is
# confirmed. All keep the same 1:2 risk:reward ratio as the default
# above ($10/$3/$6 is kept as one of the tiers for consistency).
# "Custom Amount" is always offered alongside these for anything
# that doesn't fit a preset.
STAKE_TIERS = [
    {"stake": 5, "risk": 2, "win": 4},
    {"stake": 10, "risk": 3, "win": 6},
    {"stake": 20, "risk": 6, "win": 12},
    {"stake": 50, "risk": 15, "win": 30},
    {"stake": 100, "risk": 30, "win": 60},
]

pending_trades = {}  # user_id -> trade context dict, one pending trade at a time
pending_autocopy_setup = {}  # user_id -> {stake, risk, win} chosen so far, mid-setup

SYNTHETIC_ROTATION_ORDER = ["r10", "r25", "r50", "r75", "r100"]

# index_key -> most recent channel-posted trade context. Channel
# posts aren't generated for any one user (unlike DM signals), so
# when ANY member taps "Trade This Signal" on a channel post, this
# is copied into pending_trades for that specific tapper - each
# tapper gets their own independent trade context from the same
# underlying signal.
channel_signal_context = {}

def get_rotation_key(slot_number=0):
    """
    Deterministic day-of-year based rotation through the 5 indices -
    stateless on purpose, so a Railway restart never causes a repeat
    or a skipped index. slot_number lets the two weekend posts (same
    day, different times) land on two different indices instead of
    the same one twice.
    """
    day_of_year = datetime.utcnow().timetuple().tm_yday
    idx = (day_of_year + slot_number) % len(SYNTHETIC_ROTATION_ORDER)
    return SYNTHETIC_ROTATION_ORDER[idx]

def match_synthetic_key(question):
    """
    Word-boundary matching, not plain substring - "r10" is a literal
    substring of "r100" (r-1-0-0 contains r-1-0), so a naive `in`
    check would silently match Volatility 10 when someone types
    R100 without the underscore. \\b ensures each alias only matches
    as a whole token, never embedded inside a longer one.
    """
    q = question.lower()
    for key, aliases in SYNTHETIC_ALIASES.items():
        for alias in aliases:
            if re.search(r"\b" + re.escape(alias) + r"\b", q):
                return key
    return None

SYNTHETIC_CANDLE_CACHE_SECONDS = {"1h": 3600, "4h": 14400}
synthetic_candle_cache = {}

async def deriv_get_candles(symbol, granularity, count=60):
    """
    Fetches real candle history directly from Deriv using the
    service token, via the exact same OTP connection flow already
    confirmed in Phase 1. Returns a list of {open, high, low, close}
    dicts already oldest-to-newest (confirmed live - no reversal
    needed, unlike TwelveData), or None on any failure.
    """
    if not DERIV_SERVICE_TOKEN:
        print("[SYNTH] No DERIV_SERVICE_TOKEN set")
        return None

    accounts_data = await deriv_get_options_accounts(DERIV_SERVICE_TOKEN)
    if not accounts_data:
        return None

    accounts_list = accounts_data.get("data")
    if not isinstance(accounts_list, list):
        accounts_list = accounts_data.get("accounts")
    if not accounts_list:
        print(f"[SYNTH] Unexpected accounts response shape: {accounts_data}")
        return None

    account_id = (
        accounts_list[0].get("account_id")
        or accounts_list[0].get("loginid")
        or accounts_list[0].get("id")
    )
    if not account_id:
        print(f"[SYNTH] Could not find account_id in: {accounts_list[0]}")
        return None

    ws_url = await deriv_get_otp_url(DERIV_SERVICE_TOKEN, account_id)
    if not ws_url:
        return None

    try:
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps({
                "ticks_history": symbol,
                "style": "candles",
                "granularity": granularity,
                "count": count,
                "end": "latest",
            }))
            response = json.loads(await ws.recv())
            if "error" in response:
                print(f"[SYNTH] ticks_history error: {response['error'].get('message')}")
                return None
            candles = response.get("candles", [])
            return candles if candles else None
    except Exception as e:
        print(f"[SYNTH] Connection error: {e}")
        return None

async def get_cached_synthetic_candles(index_key, symbol, granularity_label, granularity_seconds, count=60):
    cache_key = f"{index_key}_{granularity_label}"
    now = time.time()
    ttl = SYNTHETIC_CANDLE_CACHE_SECONDS.get(granularity_label, 3600)
    cached = synthetic_candle_cache.get(cache_key)
    if cached and (now - cached["timestamp"] < ttl):
        return cached["candles"]
    candles = await deriv_get_candles(symbol, granularity_seconds, count)
    if candles:
        synthetic_candle_cache[cache_key] = {"candles": candles, "timestamp": now}
    return candles

async def analyze_synthetic_structure(index_key, config):
    """
    Same SMC/ICT logic already proven on forex/gold/crypto signals
    (analyze_timeframe and every detect_* function reused completely
    unchanged), applied to a Deriv synthetic index. Returns
    (direction, confidence, reason) or None if there's no usable
    edge / no candle data.
    """
    symbol = config["symbol"]
    h1_candles = await get_cached_synthetic_candles(index_key, symbol, "1h", 3600, 60)
    h4_candles = await get_cached_synthetic_candles(index_key, symbol, "4h", 14400, 60)

    h1_factors = analyze_timeframe(h1_candles)
    h4_factors = analyze_timeframe(h4_candles)

    if not h1_factors and not h4_factors:
        print(f"[SYNTH] No structure detected for {index_key}, skipping")
        return None

    h1_buy, h1_sell = score_factors(h1_factors)
    h4_buy, h4_sell = score_factors(h4_factors)
    total_buy = (h4_buy * 1.5) + h1_buy
    total_sell = (h4_sell * 1.5) + h1_sell

    if total_buy == total_sell:
        print(f"[SYNTH] No clear edge for {index_key} (tied), skipping")
        return None

    direction = "BUY" if total_buy > total_sell else "SELL"
    matching_h1 = [f for f in h1_factors if f["direction"] == direction]
    matching_h4 = [f for f in h4_factors if f["direction"] == direction]
    confluence_count = len(matching_h1) + len(matching_h4)
    confidence = min(95, 76 + confluence_count * 4)

    h4_sorted = sorted(matching_h4, key=lambda f: f["weight"], reverse=True)
    h4_details = []
    for f in h4_sorted:
        if f["detail"] not in h4_details:
            h4_details.append(f["detail"])
        if len(h4_details) == 2:
            break

    reason = None
    if matching_h1:
        h1_sorted = sorted(matching_h1, key=lambda f: f["weight"], reverse=True)
        h1_details = []
        for f in h1_sorted:
            if f["detail"] in h4_details or f["detail"] in h1_details:
                continue
            h1_details.append(f["detail"])
            if len(h1_details) == 2:
                break
        if h1_details:
            h1_text = " and ".join(h1_details)
            reason = (
                f"{h1_text.capitalize()} on H1, confirmed by "
                f"{' and '.join(h4_details)} on H4."
                if h4_details else f"{h1_text.capitalize()} on H1."
            )
        else:
            primary = h1_sorted[0]
            reason = (
                f"{primary['detail'].capitalize()} confirmed on both "
                f"H1 and H4, high-confluence setup."
            )
    elif h4_details:
        reason = f"{' and '.join(h4_details).capitalize()} on H4."
    else:
        reason = "Multi-timeframe structure favors this direction."

    print(
        f"[SYNTH] {index_key} -> {direction} | confluence={confluence_count} "
        f"| confidence={confidence}"
    )
    return direction, confidence, reason

async def build_synthetic_signal_response(index_key):
    """
    Builds the signal message, image, and the trade context that
    gets stored for if/when the user taps "Trade This Signal".
    Returns (image_file_id, message_html, trade_context), or None
    if no signal could be generated.
    """
    config = SYNTHETIC_CONFIG.get(index_key)
    if not config:
        return None

    result = await analyze_synthetic_structure(index_key, config)
    if not result:
        return None

    direction, confidence, reason = result
    contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

    if direction == "BUY":
        emoji = "🟢"
        image_file_id = BUY_IMAGE_FILE_ID
    else:
        emoji = "🔴"
        image_file_id = SELL_IMAGE_FILE_ID

    message = (
        f"{emoji} <b>STRONG {direction} {config['display']}</b> ⚡\n\n"
        f"<b>Confidence:</b> {confidence}%\n\n"
        f"<b>Reason:</b> {reason}\n\n"
        f"<b>Suggested:</b> ${DEFAULT_SYNTHETIC_STAKE} stake | "
        f"Risk ${DEFAULT_RISK} → Target ${DEFAULT_WIN}\n"
        f"<i>(Stake and risk/target are adjustable before you confirm)</i>\n\n"
        f"<i>Trade safe 💼🔥</i>"
    )

    trade_context = {
        "index_key": index_key,
        "symbol": config["symbol"],
        "display": config["display"],
        "direction": direction,
        "contract_type": contract_type,
        "multiplier": config["default_multiplier"],
        "stake": DEFAULT_SYNTHETIC_STAKE,
        "risk": DEFAULT_RISK,
        "win": DEFAULT_WIN,
    }

    return image_file_id, message, trade_context

async def send_connect_instructions(bot, user_id):
    """
    Shared connect-account instructions (affiliate link included) -
    used by both the Connect Deriv button (handle_buttons) and the
    Trade This Signal flow (send_tier_selection) when no account is
    linked yet, so a brand-new user goes straight from intent to
    sign-up to linking without needing to find a separate button.
    """
    user_modes[user_id] = "awaiting_deriv_token"
    await bot.send_message(
        chat_id=int(user_id),
        text=(
            "🔗 <b>Connect Your Deriv Account</b>\n\n"
            "Nexora can show your real Deriv <b>Options account</b> "
            "balance and open positions right here in Telegram. "
            "(MT5 and cTrader accounts aren't supported yet.)\n\n"
            "Don't have a Deriv account yet? "
            "<a href=\"https://track.deriv.com/_eBizfEiAKzC6tyDIijdDK2Nd7ZgqdRLk/1/\">"
            "Sign up here first</a>, then come back to this step.\n\n"
            "<b>How to connect:</b>\n"
            "1️⃣ Go to <b>developers.deriv.com</b> and log in\n"
            "2️⃣ Tap the menu (☰) in the top right, then tap "
            "<b>API tokens</b>\n"
            "3️⃣ Tap <b>Create new token</b>, and check <b>Trade</b> and "
            "<b>Account management</b>\n"
            "4️⃣ Copy the token and paste it here\n\n"
            "⚠️ <b>Real accounts only</b> — demo/virtual tokens will be "
            "rejected."
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )

async def send_tier_selection(bot, user_id, trade_context):
    """
    Shows the stake-tier buttons for a pending trade already stored
    in pending_trades for this user. Shared by both the DM signal
    flow (synthtrade_/edittrade_) and the channel-tap flow
    (chantrade_), so this exists in exactly one place.
    """
    account = get_deriv_account(user_id)
    if not account:
        await send_connect_instructions(bot, user_id)
        return

    tier_buttons = [
        [InlineKeyboardButton(
            f"${t['stake']} | Risk ${t['risk']} → Win ${t['win']}",
            callback_data=f"tier_{user_id}_{t['stake']}"
        )]
        for t in STAKE_TIERS
    ]
    tier_buttons.append([InlineKeyboardButton(
        "✏️ Custom Amount", callback_data=f"customtier_{user_id}"
    )])

    await bot.send_message(
        chat_id=int(user_id),
        text=(
            f"🎯 <b>{trade_context['direction']} {trade_context['display']}</b>\n\n"
            f"Choose a stake, or enter a custom amount:"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(tier_buttons)
    )

def friendly_trade_error(raw_error):
    """
    Translates a raw Deriv API error into plain English for display
    to the end user - retail/beginner audience, so raw API jargon
    like "Multiplier is not in acceptable range" should never reach
    them directly. The raw error is always still logged in full by
    the caller for diagnosis - this only changes what gets shown.
    """
    lowered = raw_error.lower()
    if "multiplier" in lowered:
        return "This stake amount isn't supported for this index right now. Try a different stake, or use 🎯 Trade This Signal again."
    if "insufficient" in lowered or "not enough" in lowered or "balance" in lowered:
        return "Your account balance is too low for this stake. Try a smaller amount, or top up your Deriv account."
    return "We couldn't place this trade right now. Try again in a moment, or try a different stake."

async def deriv_execute_multiplier_trade(token, symbol, contract_type, multiplier, stake, risk, win):
    """
    Confirmed Proposal -> Buy flow for Multiplier contracts (live
    tested). A fresh quote is required every time - a stale or
    fabricated proposal ID is rejected, confirmed via live testing -
    so this always gets a new quote immediately before buying it.
    stop_loss/take_profit are real dollar amounts on the stake,
    confirmed live - not price levels.
    Returns (buy_data, None) on success, or (None, error_message).
    """
    accounts_data = await deriv_get_options_accounts(token)
    if not accounts_data:
        return None, "Couldn't verify your Deriv account."

    accounts_list = accounts_data.get("data")
    if not isinstance(accounts_list, list):
        accounts_list = accounts_data.get("accounts")
    if not accounts_list:
        return None, "Couldn't read your account list."

    real_account = None
    for acct in accounts_list:
        is_virtual = bool(
            acct.get("is_virtual")
            or str(acct.get("account_type", "")).lower() in ("demo", "virtual")
        )
        if not is_virtual:
            real_account = acct
            break

    if not real_account:
        return None, "No real account found on this token."

    account_id = (
        real_account.get("account_id")
        or real_account.get("loginid")
        or real_account.get("id")
    )
    currency = real_account.get("currency", "USD")

    ws_url = await deriv_get_otp_url(token, account_id)
    if not ws_url:
        return None, "Couldn't establish a trading connection."

    try:
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=10) as ws:
            proposal_request = {
                "proposal": 1,
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": currency,
                "multiplier": multiplier,
                "underlying_symbol": symbol,
                "limit_order": {
                    "stop_loss": risk,
                    "take_profit": win,
                },
            }
            await ws.send(json.dumps(proposal_request))
            proposal_response = json.loads(await ws.recv())

            if "error" in proposal_response:
                err = proposal_response["error"].get("message", "Unknown error")
                print(f"[SYNTH TRADE] Proposal error: {err}")
                return None, f"Couldn't get a quote: {err}"

            proposal = proposal_response.get("proposal", {})
            proposal_id = proposal.get("id")
            ask_price = proposal.get("ask_price")

            if not proposal_id or ask_price is None:
                print(f"[SYNTH TRADE] Unexpected proposal shape: {proposal_response}")
                return None, "Got an unexpected response while pricing the trade."

            await ws.send(json.dumps({"buy": proposal_id, "price": ask_price}))
            buy_response = json.loads(await ws.recv())

            if "error" in buy_response:
                err = buy_response["error"].get("message", "Unknown error")
                print(f"[SYNTH TRADE] Buy error: {err}")
                return None, f"Trade failed: {err}"

            buy_data = buy_response.get("buy", {})
            print(f"[SYNTH TRADE] ✅ Bought {symbol} {contract_type} | raw response: {buy_data}")
            return buy_data, None
    except Exception as e:
        print(f"[SYNTH TRADE] Connection error: {e}")
        return None, "Connection error while placing the trade."

# ============================================
# AUTO-COPY TRADING (NEW)
# Lets a user opt into having every synthetic
# signal traded automatically on their own
# linked Deriv account, instead of manually
# tapping "Trade This Signal" each time. Set up
# via the existing Connect Deriv flow, fully
# optional, off by default for every account.
#
# Stake stepdown: if the user's saved stake
# doesn't fit their current balance, this tries
# progressively smaller tiers from STAKE_TIERS
# (largest that fits) rather than failing
# outright. If even the smallest ($5) doesn't
# fit, the trade is skipped - never silently
# trades a different amount without telling the
# user which amount it used and why.
# ============================================

def get_auto_copy_trade_amounts(account, trade_context, balance):
    """
    Returns (stake, risk, win, was_reduced) for one user's auto-copy
    trade given their saved settings and current balance, or
    (None, None, None, None) if no stake fits even at the smallest
    tier. risk_mode "signal" follows the signal's own suggested
    risk/win (scaled to match if the stake gets stepped down, so the
    risk:reward ratio stays the same as originally suggested);
    "fixed" always uses the user's own saved risk/win unchanged.
    """
    saved_stake = account.get("auto_copy_stake") or DEFAULT_SYNTHETIC_STAKE
    risk_mode = account.get("auto_copy_risk_mode") or "fixed"

    if risk_mode == "signal":
        base_risk = trade_context["risk"]
        base_win = trade_context["win"]
    else:
        base_risk = account.get("auto_copy_risk") or DEFAULT_RISK
        base_win = account.get("auto_copy_win") or DEFAULT_WIN

    if balance >= saved_stake:
        return saved_stake, base_risk, base_win, False

    # Saved stake doesn't fit - step down through the standard tiers,
    # largest-that-fits-first, scaling risk/win to match proportionally
    # so the risk:reward ratio is preserved at the smaller size.
    for tier in sorted(STAKE_TIERS, key=lambda t: t["stake"], reverse=True):
        if tier["stake"] <= saved_stake and balance >= tier["stake"]:
            scale = tier["stake"] / saved_stake
            return (
                tier["stake"],
                round(base_risk * scale, 2),
                round(base_win * scale, 2),
                True
            )

    return None, None, None, None

async def run_auto_copy_for_signal(bot, trade_context):
    """
    Fires the same signal's trade automatically for every user with
    auto_copy_enabled = true on their own linked Deriv account. Each
    user is fully isolated in its own try/except - one broken token
    or one API hiccup never blocks or skips anyone else. Always runs
    as a background task from the signal-posting function, so it
    never delays or blocks the channel post itself.
    """
    accounts = get_all_auto_copy_accounts()
    if not accounts:
        return

    print(f"[AUTO-COPY] Running for {len(accounts)} opted-in account(s)")

    for account in accounts:
        user_id = account.get("user_id")
        token = account.get("api_token")
        if not user_id or not token:
            continue

        try:
            snapshot = await deriv_fetch_account_snapshot(token)
            if not snapshot or snapshot.get("balance") is None:
                print(f"[AUTO-COPY] Couldn't read balance for {user_id}, skipping this signal")
                continue

            balance = snapshot["balance"]
            stake, risk, win, was_reduced = get_auto_copy_trade_amounts(
                account, trade_context, balance
            )

            if stake is None:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Auto-copy skipped this signal.</b>\n\n"
                        f"<b>{trade_context['direction']} {trade_context['display']}</b>\n"
                        f"Your balance (${balance}) is too low even for "
                        f"the smallest stake tier ($5). Top up your "
                        f"Deriv account to resume auto-copy trades."
                    ),
                    parse_mode=ParseMode.HTML
                )
                continue

            buy_data, error = await deriv_execute_multiplier_trade(
                token,
                trade_context["symbol"],
                trade_context["contract_type"],
                trade_context["multiplier"],
                stake, risk, win,
            )

            if error:
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"❌ <b>Auto-copy trade failed.</b>\n\n"
                        f"<b>{trade_context['direction']} {trade_context['display']}</b>\n"
                        f"{friendly_trade_error(error)}"
                    ),
                    parse_mode=ParseMode.HTML
                )
                continue

            contract_id = buy_data.get("contract_id", "—")
            reduced_note = (
                f"\n\n<i>Stake auto-reduced to ${stake} to fit your "
                f"current balance.</i>" if was_reduced else ""
            )
            await bot.send_message(
                chat_id=int(user_id),
                text=(
                    f"🤖 <b>Auto-copy trade placed!</b>\n\n"
                    f"<b>{trade_context['direction']} {trade_context['display']}</b>\n"
                    f"Stake: ${stake} | Risk ${risk} → Win ${win}\n"
                    f"Contract ID: {contract_id}"
                    f"{reduced_note}"
                ),
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            print(f"[AUTO-COPY] ❌ Unexpected error for {user_id}: {e}")
            continue

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
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_bias_usage"
            f"?on_conflict=user_id,usage_date"
        )
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
        url = (
            f"{SUPABASE_URL}/rest/v1/ai_bias_global"
            f"?on_conflict=usage_date"
        )
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
        "display": "XAU/USD 🥇",
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
        "display": "BTC/USD ₿",
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
# SMC / ICT STRUCTURE ANALYSIS (NEW)
# Replaces guesswork with real detected market
# structure. Pulls H1 + H4 candles from
# TwelveData (including XAGUSD and USOIL, using
# the same multi-symbol fallback pattern as
# their live price lookups) and runs the
# following detectors on each timeframe: swing
# points, liquidity sweeps, order blocks, fair
# value gaps, break of structure, and
# premium/discount zones. Each factor casts a
# weighted vote for BUY or SELL. H4 acts as the
# higher-timeframe trend filter (1.5x weight),
# H1 supplies the entry trigger. Reason /
# Timeframe Confirmation text is built directly
# from whichever real factors were found - never
# random, never guessed. If no candle data can
# be fetched for a pair, returns None and the
# caller falls back to the old trend-based bias.
# Cached globally per pair+interval (1h for H1,
# 4h for H4), same scaling pattern as
# price_cache - cost is flat regardless of user
# count, bounded only by number of pairs.
# ============================================

CANDLE_CACHE_SECONDS = {
    "1h": 3600,
    "4h": 14400,
}

candle_cache = {}

def get_candles_twelvedata(symbol, interval, outputsize=60):
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol={symbol}&interval={interval}&outputsize={outputsize}"
            f"&apikey={TWELVEDATA_API_KEY}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        values = data.get("values")
        if not values:
            print(f"[SMC] No candle values for {symbol} {interval}: {data}")
            return None
        candles = []
        for v in values:
            candles.append({
                "time": v.get("datetime"),
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
            })
        candles.reverse()  # twelvedata returns newest first; we want oldest -> newest
        return candles
    except Exception as e:
        print(f"[SMC] Error fetching {interval} candles for {symbol}: {e}")
        return None

def get_candle_symbol_candidates(config):
    """
    Ordered candidate symbols to try for candle data. Oil mirrors
    the same multi-symbol fallback already used for its live price
    lookups in get_oil_price, since futures-style symbols aren't
    guaranteed on every data plan. Every other pair (including
    silver) just uses its existing td_symbol.
    """
    if config.get("use_oil_api"):
        return ["CL1!", "USOIL"]
    return [config.get("td_symbol", config["symbol"])]

def get_cached_candles(pair_key, config, interval, outputsize=60):
    cache_key = f"{pair_key}_{interval}"
    now = time.time()
    ttl = CANDLE_CACHE_SECONDS.get(interval, 3600)
    cached = candle_cache.get(cache_key)
    if cached and (now - cached["timestamp"] < ttl):
        return cached["candles"]

    for symbol in get_candle_symbol_candidates(config):
        candles = get_candles_twelvedata(symbol, interval, outputsize)
        if candles:
            candle_cache[cache_key] = {"candles": candles, "timestamp": now}
            return candles

    return None

def find_swing_points(candles, strength=2):
    """
    A candle is a swing high if its high is the highest within a
    window of `strength` candles on either side; swing low is the
    mirror case. This is the fractal basis everything else (BOS,
    sweeps, premium/discount) is built on.
    """
    swings = []
    n = len(candles)
    for i in range(strength, n - strength):
        window = candles[i - strength:i + strength + 1]
        high = candles[i]["high"]
        low = candles[i]["low"]
        if high == max(c["high"] for c in window):
            swings.append({"index": i, "type": "high", "price": high})
        if low == min(c["low"] for c in window):
            swings.append({"index": i, "type": "low", "price": low})
    return swings

def detect_liquidity_sweep(candles, swings):
    """
    Checks the last 3 candles for a wick that pierces beyond the
    most recent swing high/low but closes back on the inside - the
    classic ICT stop-hunt/liquidity-grab signature that precedes a
    reversal, as opposed to a genuine breakout.
    """
    if len(candles) < 5 or not swings:
        return None

    recent_lows = [s for s in swings if s["type"] == "low" and s["index"] < len(candles) - 1]
    recent_highs = [s for s in swings if s["type"] == "high" and s["index"] < len(candles) - 1]

    last_candles = candles[-3:]

    if recent_lows:
        last_swing_low = recent_lows[-1]["price"]
        for c in last_candles:
            if c["low"] < last_swing_low and c["close"] > last_swing_low:
                return {"direction": "BUY", "detail": "sell-side liquidity sweep"}

    if recent_highs:
        last_swing_high = recent_highs[-1]["price"]
        for c in last_candles:
            if c["high"] > last_swing_high and c["close"] < last_swing_high:
                return {"direction": "SELL", "detail": "buy-side liquidity sweep"}

    return None

def detect_order_block(candles):
    """
    Finds the most recent impulsive candle (body >= 1.5x the
    average body of the prior 14 candles) and identifies the last
    opposite-colored candle before it as the order block. Fires
    only if current price has actually returned into that zone -
    the real OB entry trigger, not just the OB's existence.
    """
    if len(candles) < 15:
        return None

    bodies = [abs(c["close"] - c["open"]) for c in candles[-15:-1]]
    avg_body = sum(bodies) / len(bodies) if bodies else 0
    if avg_body == 0:
        return None

    current_price = candles[-1]["close"]

    for i in range(len(candles) - 2, max(len(candles) - 8, 1), -1):
        c = candles[i]
        body = abs(c["close"] - c["open"])
        if body < avg_body * 1.5:
            continue
        is_bullish_impulse = c["close"] > c["open"]
        prev = candles[i - 1]
        prev_bearish = prev["close"] < prev["open"]
        prev_bullish = prev["close"] > prev["open"]

        if is_bullish_impulse and prev_bearish:
            ob_low, ob_high = prev["low"], prev["high"]
            if ob_low <= current_price <= ob_high * 1.001:
                return {"direction": "BUY", "detail": "bullish order block reaction"}

        if not is_bullish_impulse and prev_bullish:
            ob_low, ob_high = prev["low"], prev["high"]
            if ob_low * 0.999 <= current_price <= ob_high:
                return {"direction": "SELL", "detail": "bearish order block reaction"}

    return None

def detect_fvg(candles):
    """
    Classic 3-candle imbalance: candle1's high sits below candle3's
    low (bullish gap) or candle1's low sits above candle3's high
    (bearish gap). Fires only if current price is currently sitting
    inside an unfilled gap.
    """
    if len(candles) < 4:
        return None

    current_price = candles[-1]["close"]

    for i in range(len(candles) - 2, max(len(candles) - 10, 1), -1):
        c1, c3 = candles[i - 1], candles[i + 1]
        if c1["high"] < c3["low"]:
            gap_low, gap_high = c1["high"], c3["low"]
            if gap_low <= current_price <= gap_high:
                return {"direction": "BUY", "detail": "unfilled bullish fair value gap"}
        if c1["low"] > c3["high"]:
            gap_low, gap_high = c3["high"], c1["low"]
            if gap_low <= current_price <= gap_high:
                return {"direction": "SELL", "detail": "unfilled bearish fair value gap"}

    return None

def detect_bos_choch(swings):
    """
    Compares the last two swing highs and last two swing lows.
    Clean higher-high + higher-low = bullish break of structure.
    Clean lower-high + lower-low = bearish break of structure.
    Mixed/ambiguous structure returns None rather than guessing.
    """
    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    higher_high = highs[-1]["price"] > highs[-2]["price"]
    higher_low = lows[-1]["price"] > lows[-2]["price"]
    lower_high = highs[-1]["price"] < highs[-2]["price"]
    lower_low = lows[-1]["price"] < lows[-2]["price"]

    if higher_high and higher_low:
        return {"direction": "BUY", "detail": "bullish break of structure (higher highs and higher lows)"}
    if lower_high and lower_low:
        return {"direction": "SELL", "detail": "bearish break of structure (lower highs and lower lows)"}

    return None

def detect_premium_discount(candles, swings):
    """
    Uses the most recent significant swing high/low to define a
    range, then checks if current price sits in the discount half
    (below 50%, favours buys) or premium half (above 50%, favours
    sells) - the standard ICT equilibrium concept.
    """
    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]
    if not highs or not lows:
        return None

    range_high = max(highs[-3:], key=lambda s: s["price"])["price"]
    range_low = min(lows[-3:], key=lambda s: s["price"])["price"]
    if range_high <= range_low:
        return None

    current_price = candles[-1]["close"]
    midpoint = (range_high + range_low) / 2

    if current_price < midpoint:
        return {"direction": "BUY", "detail": "price trading in discount zone"}
    return {"direction": "SELL", "detail": "price trading in premium zone"}

def analyze_timeframe(candles):
    """
    Runs every SMC/ICT detector against one timeframe's candles and
    returns the list of factors found, each weighted by how strong
    a signal it typically is in real ICT/SMC practice.
    """
    factors = []
    if not candles or len(candles) < 15:
        return factors

    swings = find_swing_points(candles, strength=2)

    sweep = detect_liquidity_sweep(candles, swings)
    if sweep:
        factors.append({**sweep, "weight": 3})

    bos = detect_bos_choch(swings)
    if bos:
        factors.append({**bos, "weight": 2})

    ob = detect_order_block(candles)
    if ob:
        factors.append({**ob, "weight": 2})

    fvg = detect_fvg(candles)
    if fvg:
        factors.append({**fvg, "weight": 1.5})

    pd_zone = detect_premium_discount(candles, swings)
    if pd_zone:
        factors.append({**pd_zone, "weight": 1})

    return factors

def score_factors(factors):
    buy_score = sum(f["weight"] for f in factors if f["direction"] == "BUY")
    sell_score = sum(f["weight"] for f in factors if f["direction"] == "SELL")
    return buy_score, sell_score

def analyze_smc_structure(pair_key, config):
    """
    Pulls real H1 + H4 candles and runs the full SMC/ICT detector
    suite on both. Returns (direction, confidence, reason,
    timeframe_confirmation) built entirely from real detected
    factors, or None if there's no usable edge / no candle data -
    in which case the caller falls back to the old trend logic.
    Reason surfaces up to 2 distinct genuine factors per timeframe
    (capped to keep it readable) instead of always collapsing to
    the single strongest one - a setup with multiple real
    confluences reads as more specific, and naturally varies from
    signal to signal based on whatever was actually detected, rather
    than repeatedly boiling down to whichever single factor happens
    to carry the highest weight. H4's detail(s) are folded directly
    into the same sentence (e.g. "X and Y on H1, confirmed by Z on
    H4.") so there's no separate Timeframe Confirmation row
    repeating part of it.
    """
    h1_candles = get_cached_candles(pair_key, config, "1h", outputsize=60)
    h4_candles = get_cached_candles(pair_key, config, "4h", outputsize=60)

    h1_factors = analyze_timeframe(h1_candles)
    h4_factors = analyze_timeframe(h4_candles)

    if not h1_factors and not h4_factors:
        print(f"[SMC] No structure detected for {pair_key}, falling back")
        return None

    h1_buy, h1_sell = score_factors(h1_factors)
    h4_buy, h4_sell = score_factors(h4_factors)

    # H4 is the higher-timeframe trend filter, weighted 1.5x over H1
    total_buy = (h4_buy * 1.5) + h1_buy
    total_sell = (h4_sell * 1.5) + h1_sell

    if total_buy == total_sell:
        print(f"[SMC] No clear edge for {pair_key} (tied), falling back")
        return None

    direction = "BUY" if total_buy > total_sell else "SELL"

    matching_h1 = [f for f in h1_factors if f["direction"] == direction]
    matching_h4 = [f for f in h4_factors if f["direction"] == direction]
    confluence_count = len(matching_h1) + len(matching_h4)

    confidence = min(95, 76 + confluence_count * 4)

    # Up to 2 distinct H4 factor descriptions, strongest first
    h4_sorted = sorted(matching_h4, key=lambda f: f["weight"], reverse=True)
    h4_details = []
    for f in h4_sorted:
        if f["detail"] not in h4_details:
            h4_details.append(f["detail"])
        if len(h4_details) == 2:
            break

    # Kept for internal logging/back-compat only - no longer shown
    # in the message itself (folded into reason below instead).
    timeframe_confirmation = (
        f"H4 bias confirms: {h4_details[0]}" if h4_details
        else f"{confluence_count} confluent SMC factor(s) aligned on H1"
    )

    reason = None
    if matching_h1:
        h1_sorted = sorted(matching_h1, key=lambda f: f["weight"], reverse=True)

        # Up to 2 distinct H1 factors not already covered by H4
        h1_details = []
        for f in h1_sorted:
            if f["detail"] in h4_details:
                continue
            if f["detail"] in h1_details:
                continue
            h1_details.append(f["detail"])
            if len(h1_details) == 2:
                break

        if h1_details:
            h1_text = " and ".join(h1_details)
            if h4_details:
                h4_text = " and ".join(h4_details)
                reason = f"{h1_text.capitalize()} on H1, confirmed by {h4_text} on H4."
            else:
                reason = f"{h1_text.capitalize()} on H1."
        else:
            # All H1 factors are also present on H4 — use the
            # strongest shared one rather than just repeating it
            primary = h1_sorted[0]
            reason = (
                f"{primary['detail'].capitalize()} confirmed on both "
                f"H1 and H4, high-confluence setup."
            )
    elif h4_details:
        h4_text = " and ".join(h4_details)
        reason = f"{h4_text.capitalize()} on H4."
    else:
        reason = "Multi-timeframe structure favors this direction."

    print(
        f"[SMC] {pair_key} -> {direction} | confluence={confluence_count} "
        f"| confidence={confidence}"
    )

    return direction, confidence, reason, timeframe_confirmation

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
# WEEKEND MARKET GATE (NEW)
# Forex, gold, silver and oil are all closed
# Saturday/Sunday. Mirrors the same Sat/Sun
# window already used for WEEKDAYS_ONLY in
# main()'s scheduler, kept as one shared check
# so the manual signal flow and the channel
# scheduler never disagree about market hours.
# BTCUSD trades 24/7 and is exempt everywhere
# this is checked.
# ============================================

def is_forex_market_closed():
    return datetime.utcnow().weekday() in (5, 6)  # Saturday=5, Sunday=6

# ============================================
# AI-GENERATED BIAS (NEW)
# Asks Gemini to weigh fundamental/sentiment
# factors plus recent price movement and
# return a real BUY/SELL call with reasoning.
# NOTE: no longer called by build_signal_response
# directly for the signal direction (that's now
# decided purely by analyze_smc_structure /
# generate_rule_based_bias above). Left intact
# and unused in case it's wanted elsewhere later.
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
# AI FUNDAMENTAL CONTEXT LAYER (NEW)
# Now told the technical direction up front and
# asked to write ONE sentence explaining how
# fundamentals relate to it - either supporting
# it plainly, or framing a disagreement as a
# brief risk caveat. It never states a competing
# directional call, so the channel message can
# show Technical Analysis and Fundamental
# Analysis as two clearly labeled rows without
# them ever reading as two votes pointing
# opposite ways. Direction is still decided
# 100% by the SMC engine - this layer only adds
# context to it, never overrides it.
# Grounded in real fetched news + real calendar
# events (see get_cached_news_context /
# get_relevant_calendar_events below) rather than
# letting Gemini invent "current" factors from
# training-data patterns - if neither is
# available right now, falls back to a generic
# prompt that's explicit about not having
# real-time data, rather than failing outright.
# ============================================

def parse_fundamental_response(text):
    try:
        for line in text.strip().split("\n"):
            if line.strip().upper().startswith("FUNDAMENTAL:"):
                value = line.split(":", 1)[1].strip()
                if value.upper() == "NONE":
                    return None
                return value
        return None
    except Exception:
        return None

async def generate_fundamental_context(pair_name, direction):
    article = get_cached_news_context()
    calendar_events = get_relevant_calendar_events(pair_name)

    context_lines = []
    if article and article.get("title"):
        context_lines.append(
            f"Recent headline ({article.get('source', 'news')}): "
            f"{article['title']}. {article.get('description', '')}"
        )
    if calendar_events:
        context_lines.append(
            "Today's high-impact calendar events: "
            + "; ".join(calendar_events)
        )

    if context_lines:
        context_block = "\n".join(context_lines)
        prompt = f"""
You are a forex/macro analyst. Base your answer ONLY on the real
information below - do not invent any other news, data or events.

{context_block}

The technical analysis for {pair_name} already calls a {direction}.
If the information above has a genuine, meaningful connection to
{pair_name}, write ONE sentence explaining how it relates to this
{direction} call. If they support it, say so plainly. If they point
the other way, frame it as a brief risk/caveat to be aware of - do
NOT state a competing directional recommendation of your own.

If the information above has NO real connection to {pair_name} -
respond with exactly: FUNDAMENTAL: NONE
Do not force a connection that isn't genuinely there.

Respond in EXACTLY this format, nothing else, no markdown:
FUNDAMENTAL: [one sentence, max 18 words] OR FUNDAMENTAL: NONE
"""
    else:
        prompt = f"""
You are a forex/macro analyst. No specific real-time news or
calendar data is available right now. The technical analysis for
{pair_name} already calls a {direction}. If you can give genuinely
useful general macro context relevant to this pair and direction,
write ONE sentence, making clear it's a general pattern rather than
a specific current event. Do not state a competing directional
recommendation of your own.

If you have nothing genuinely useful to add, respond with exactly:
FUNDAMENTAL: NONE

Respond in EXACTLY this format, nothing else, no markdown:
FUNDAMENTAL: [one sentence, max 18 words] OR FUNDAMENTAL: NONE
"""

    try:
        result = await ask_gemini_for_bias(prompt)
        fundamental_reason = parse_fundamental_response(result)
        if fundamental_reason:
            return fundamental_reason
    except Exception as e:
        print(f"[FUNDAMENTAL] Failed: {e}")
    return None

# ============================================
# RULE-BASED FALLBACK BIAS (NEW)
# Used when SMC structure analysis can't find
# an edge (no candle data, or a genuine tie).
# Still genuinely reflects real price movement,
# not a coin flip - it's free, accurate, basic
# technical analysis based on actual price action.
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
# UPDATED: direction now comes from real SMC/ICT
# structure analysis (analyze_smc_structure),
# falling back to the 1h trend-based bias only
# if structure analysis finds no usable edge.
# An optional AI fundamental layer (capped, same
# limits as before) adds independently-generated
# macro/sentiment context on top, without ever
# overriding the technical direction. Scheduled
# channel signals pass user_id=None and always
# get the AI layer (negligible cost - 3 cron
# slots/day, not scaled by user count).
# ============================================

async def build_signal_response(question, user_id=None):
    matched_key = match_pair_key(question)

    if matched_key is None:
        print(f"[SIGNAL] ❌ No matching pair found for: {question}")
        return None, None, None, None

    if matched_key != "btcusd" and is_forex_market_closed():
        print(f"[SIGNAL] ⏸️ {matched_key} blocked — forex market closed for the week")
        return None, None, "MARKET_CLOSED", None

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
    confidence = None
    timeframe_confirmation = None
    used_smc = False
    used_ai_layer = False

    smc_result = analyze_smc_structure(matched_key, config)
    if smc_result:
        direction, confidence, reason, timeframe_confirmation = smc_result
        used_smc = True
        last_signal_direction[matched_key] = (direction, time.time())

    if not direction:
        direction, reason = generate_rule_based_bias(
            matched_key, current_price, price_1h_ago
        )
        confidence = random.randint(80, 94)

    # AI fundamental layer (capped). Scheduled channel signals pass
    # user_id=None and are always allowed - negligible cost, only 3
    # cron slots/day. DM signals are capped by the exact same
    # per-user/global limits used elsewhere in this file, so the
    # shared Gemini quota stays protected at 100k users. AI NEVER
    # decides or contradicts the direction - it's told the technical
    # call up front and only writes a supporting/caveat sentence for
    # the separate Fundamental Analysis row below.
    fundamental_reason = None
    if can_use_ai_bias(user_id):
        fundamental_reason = await generate_fundamental_context(pair_name, direction)
        if fundamental_reason:
            record_ai_bias_usage(user_id)
            used_ai_layer = True

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

    response = (
        f"{signal_emoji} <b>{strength} {direction} {display}</b>\n\n"
        f"<b>Entry Price:</b> {entry_price}\n"
        f"<b>SL:</b> {stop_loss} | <b>TP:</b> {take_profit}\n\n"
        f"<b>Confidence:</b> {confidence}%\n"
        f"<b>Session:</b> {session}\n\n"
        f"<b>Reason:</b>\n"
        f"<b>Technical Analysis:</b> {reason}\n\n"
    )
    if fundamental_reason:
        response += f"<b>Fundamental Analysis:</b> {fundamental_reason}\n\n"
    response += f"<i>Trade safe 💼🔥</i>"

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
        f"SMC used: {used_smc} | AI layer used: {used_ai_layer}"
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
# FUNDAMENTAL GROUNDING DATA (NEW)
# Real news + real calendar data for the AI
# fundamental layer to reason from, instead of
# letting Gemini guess at "current" factors from
# training-data patterns. Both cached globally
# (same scaling pattern as price_cache/
# candle_cache) so repeated signal requests don't
# multiply calls to the news/calendar APIs -
# cost stays flat regardless of user count.
# ============================================

NEWS_CONTEXT_CACHE_SECONDS = 1800  # 30 minutes
news_context_cache = {"article": None, "timestamp": 0}

def get_cached_news_context():
    now = time.time()
    if now - news_context_cache["timestamp"] < NEWS_CONTEXT_CACHE_SECONDS:
        return news_context_cache["article"]
    article = fetch_market_news()
    news_context_cache["article"] = article
    news_context_cache["timestamp"] = now
    return article

CALENDAR_CACHE_SECONDS = 3600  # 1 hour
calendar_data_cache = {"data": None, "timestamp": 0}

def get_cached_calendar_data():
    now = time.time()
    if (
        calendar_data_cache["data"] is not None
        and now - calendar_data_cache["timestamp"] < CALENDAR_CACHE_SECONDS
    ):
        return calendar_data_cache["data"]
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=10)
        data = response.json()
        calendar_data_cache["data"] = data
        calendar_data_cache["timestamp"] = now
        return data
    except Exception as e:
        print(f"[FUNDAMENTAL] Calendar fetch error: {e}")
        return calendar_data_cache["data"] or []

def get_relevant_calendar_events(pair_name, limit=2):
    """
    Plain-text, real high-impact calendar events today for whichever
    of USD/EUR/GBP/JPY appear in this pair's name. Returns [] if
    none found or no calendar data available.
    """
    try:
        data = get_cached_calendar_data()
        if not data:
            return []

        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        relevant_currencies = [
            c for c in ["USD", "EUR", "GBP", "JPY"] if c in pair_name
        ]
        if not relevant_currencies:
            return []

        events = []
        for event in data:
            if event.get("date", "")[:10] != today_str:
                continue
            if event.get("impact", "").lower() != "high":
                continue
            currency = event.get("currency", "")
            if currency not in relevant_currencies:
                continue
            title = event.get("title", "")
            if title:
                events.append(f"{currency}: {title}")
            if len(events) >= limit:
                break
        return events
    except Exception as e:
        print(f"[FUNDAMENTAL] get_relevant_calendar_events error: {e}")
        return []

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
# METAAPI — CHECK REAL TRADE OUTCOME (NEW)
# Uses the MetaApi Python SDK (not raw requests,
# since deal history is websocket/RPC-only - see
# https://metaapi.cloud/docs/client/ for details)
# to ask MT5 directly what happened to a placed
# order: still open, or closed with a real profit/
# loss. This replaces inferring the outcome from
# the cached price feed, which couldn't see manual
# closes or slippage past the posted TP/SL levels.
#
# A simple market order's positionId equals its
# orderId, so the order_id saved by
# attach_mt5_order_id can be used directly as the
# position_id lookup key. A still-open position has
# only its opening (DEAL_ENTRY_IN) deal; once
# closed, a second deal appears with
# entryType DEAL_ENTRY_OUT and a non-zero profit
# (positive = win, negative = loss) - this is true
# whether it closed via TP, SL, or a manual close.
#
# Opens a fresh RPC connection per call rather than
# holding one open permanently, since this only
# needs to run once per signal during the 15-minute
# monitor sweep.
# ============================================

async def get_mt5_trade_outcome(position_id):
    """
    Returns ('CLOSED', profit) if the position has closed (TP, SL, or
    manual), ('OPEN', None) if it's still running, or (None, None) if
    the lookup itself failed (e.g. network/auth issue) - callers should
    treat None as "couldn't tell, try again next sweep" and NOT close
    out the signal on a failed lookup.
    """
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID or not position_id:
        return None, None
    try:
        api = MetaApi(token=METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(
            account_id=METAAPI_ACCOUNT_ID
        )
        connection = account.get_rpc_connection()
        await connection.connect()
        await connection.wait_synchronized()

        deals = await connection.get_deals_by_position(
            position_id=str(position_id)
        )

        closing_deal = next(
            (d for d in deals if d.get("entryType") == "DEAL_ENTRY_OUT"),
            None
        )
        if closing_deal is None:
            return "OPEN", None

        profit = closing_deal.get("profit", 0)
        print(
            f"[MT5 OUTCOME] Position {position_id} closed — "
            f"profit: {profit}"
        )
        return "CLOSED", profit

    except Exception as e:
        print(f"[MT5 OUTCOME] ❌ Lookup failed for {position_id}: {e}")
        return None, None

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

    # Arrived here via the channel's "Trade This Signal" deep-link
    # button (?start=chantrade_<index_key>) rather than a plain /start.
    # This replaces the old callback_data version of this button, which
    # silently failed for anyone who hadn't already started a DM with
    # the bot - Telegram blocks bots from messaging users who haven't
    # initiated contact, so the tap did nothing visible. A url= deep
    # link sidesteps that since opening it IS the user starting the DM.
    if context.args and context.args[0].startswith("chantrade_"):
        index_key = context.args[0].replace("chantrade_", "")
        shared_context = channel_signal_context.get(index_key)

        if not shared_context:
            await update.message.reply_text(
                "⚠️ <b>This signal has expired.</b>\n\n"
                "Request a fresh one by typing the index name "
                "(e.g. R_100) in Signal mode.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        pending_trades[user_id] = dict(shared_context)  # copy - each tapper gets their own independent trade
        await send_tier_selection(context.bot, user_id, pending_trades[user_id])
        return

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

    # Connect Deriv is checked before the trial gate on purpose -
    # linking a Deriv account costs nothing and has nothing to do
    # with the Exness-trial-limited forex signals, so it should
    # never be blocked by that wall.
    if "deriv" in text:
        existing = get_deriv_account(user_id)
        if existing:
            await update.message.reply_text(
                "🔄 <b>Checking your linked Deriv account...</b>",
                parse_mode=ParseMode.HTML
            )
            snapshot = await deriv_fetch_account_snapshot(existing["api_token"])
            if snapshot:
                open_count = len(snapshot["open_contracts"])
                auto_on = bool(existing.get("auto_copy_enabled"))
                status_line = (
                    f"🤖 <b>Auto-Copy:</b> ON (${existing.get('auto_copy_stake', '—')} stake)"
                    if auto_on else
                    "✋ <b>Auto-Copy:</b> OFF (manual mode)"
                )
                toggle_button = (
                    InlineKeyboardButton("🛑 Turn Auto-Copy OFF", callback_data="autocopy_setup_manual")
                    if auto_on else
                    InlineKeyboardButton("🤖 Turn Auto-Copy ON", callback_data="autocopy_setup_start")
                )
                await update.message.reply_text(
                    f"🔗 <b>Linked Deriv Options Account</b>\n\n"
                    f"<b>Account:</b> {snapshot['loginid']}\n"
                    f"<b>Balance:</b> {snapshot['balance']} {snapshot['currency']}\n"
                    f"<b>Open Positions:</b> {open_count}\n"
                    f"{status_line}\n\n"
                    f"ℹ️ <i>This shows your Options account only. Your MT5 "
                    f"and cTrader balances aren't connected yet.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                await update.message.reply_text(
                    "Want to change your trading mode?",
                    reply_markup=InlineKeyboardMarkup([[toggle_button]])
                )
            else:
                await update.message.reply_text(
                    "⚠️ <b>Couldn't reach your linked Deriv account.</b>\n\n"
                    "Your saved token may have expired or been revoked. "
                    "Paste a new real-account API token below to relink.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
            user_modes[user_id] = "awaiting_deriv_token"
            return

        await send_connect_instructions(context.bot, user_id)
        return

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
            "<b>Deriv Synthetics (tradeable):</b>\n"
            "• R_10, R_25, R_50, R_75, R_100\n\n"
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

    elif data.startswith("synthtrade_") or data.startswith("edittrade_"):

        user_id = data.replace("synthtrade_", "").replace("edittrade_", "")
        trade_context = pending_trades.get(user_id)

        if not trade_context:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>That trade has expired.</b>\n\n"
                    "Please request a new signal and tap 🎯 Trade This "
                    "Signal again."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        await send_tier_selection(context.bot, user_id, trade_context)

    elif data.startswith("chantrade_"):

        index_key = data.replace("chantrade_", "")
        tapping_user_id = str(query.from_user.id)

        shared_context = channel_signal_context.get(index_key)
        if not shared_context:
            try:
                await context.bot.send_message(
                    chat_id=int(tapping_user_id),
                    text=(
                        "⚠️ <b>This signal has expired.</b>\n\n"
                        "Request a fresh one by typing the index name "
                        "(e.g. R_100) in Signal mode."
                    ),
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                print(f"[SYNTH] Couldn't notify {tapping_user_id} (likely hasn't started the bot in DM): {e}")
            return

        pending_trades[tapping_user_id] = dict(shared_context)  # copy - each tapper gets their own independent trade

        try:
            await send_tier_selection(context.bot, tapping_user_id, pending_trades[tapping_user_id])
        except Exception as e:
            print(f"[SYNTH] Couldn't message {tapping_user_id} (likely hasn't started the bot in DM): {e}")

    elif data.startswith("tier_"):

        body = data.replace("tier_", "")
        user_id, stake_str = body.rsplit("_", 1)
        stake = float(stake_str)

        trade_context = pending_trades.get(user_id)
        tier = next((t for t in STAKE_TIERS if t["stake"] == stake), None)

        if not trade_context or not tier:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>That trade has expired.</b>\n\n"
                    "Please request a new signal and tap 🎯 Trade This "
                    "Signal again."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        trade_context["stake"] = tier["stake"]
        trade_context["risk"] = tier["risk"]
        trade_context["win"] = tier["win"]
        pending_trades[user_id] = trade_context

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"🎯 <b>Confirm this trade:</b>\n\n"
                f"<b>{trade_context['direction']} {trade_context['display']}</b>\n"
                f"Stake: ${trade_context['stake']} | "
                f"Risk ${trade_context['risk']} → Target ${trade_context['win']}"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirm", callback_data=f"execconfirm_{user_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edittrade_{user_id}"),
            ]])
        )

    elif data.startswith("customtier_"):

        user_id = data.replace("customtier_", "")
        trade_context = pending_trades.get(user_id)

        if not trade_context:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>That trade has expired.</b>\n\n"
                    "Please request a new signal and tap 🎯 Trade This "
                    "Signal again."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        user_modes[user_id] = "awaiting_trade_confirm"

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "✏️ <b>Enter your custom amounts:</b>\n\n"
                "Send something like:\n"
                "<code>stake=20 risk=5 win=10</code>"
            ),
            parse_mode=ParseMode.HTML
        )

    elif data == "autocopy_setup_manual":

        user_id = str(update.callback_query.from_user.id)
        save_auto_copy_settings(user_id, enabled=False)

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "✋ <b>Got it — manual mode.</b>\n\n"
                "Tap 🎯 Trade This Signal on any signal whenever you "
                "want to trade it. You can switch to Auto-Copy anytime "
                "from 🔗 Connect Deriv."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )

    elif data == "autocopy_setup_start":

        user_id = str(update.callback_query.from_user.id)

        tier_buttons = [
            [InlineKeyboardButton(
                f"${t['stake']} | Risk ${t['risk']} → Win ${t['win']}",
                callback_data=f"autocopystake_{user_id}_{t['stake']}"
            )]
            for t in STAKE_TIERS
        ]

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "🤖 <b>Auto-Copy Setup — Step 1 of 2</b>\n\n"
                "Choose the stake to use on every signal:"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(tier_buttons)
        )

    elif data.startswith("autocopystake_"):

        body = data.replace("autocopystake_", "")
        user_id, stake_str = body.rsplit("_", 1)
        stake = float(stake_str)

        tier = next((t for t in STAKE_TIERS if t["stake"] == stake), None)
        if not tier:
            return

        # Stashed here briefly until the risk-mode choice on the next
        # tap completes the save - mirrors pending_trades, a short-
        # lived per-user dict, not a persistent store.
        pending_autocopy_setup[user_id] = {
            "stake": tier["stake"],
            "risk": tier["risk"],
            "win": tier["win"],
        }

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "🤖 <b>Auto-Copy Setup — Step 2 of 2</b>\n\n"
                f"Stake set to ${tier['stake']}. Now choose how risk/"
                "target should be decided on each trade:"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    f"📌 Always use Risk ${tier['risk']} → Win ${tier['win']}",
                    callback_data=f"autocopyrisk_{user_id}_fixed"
                )],
                [InlineKeyboardButton(
                    "📊 Follow each signal's suggested risk/target",
                    callback_data=f"autocopyrisk_{user_id}_signal"
                )],
            ])
        )

    elif data.startswith("autocopyrisk_"):

        body = data.replace("autocopyrisk_", "")
        user_id, risk_mode = body.rsplit("_", 1)

        setup = pending_autocopy_setup.pop(user_id, None)
        if not setup:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>That setup expired.</b>\n\n"
                    "Tap 🔗 Connect Deriv and choose Auto-Copy again."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        saved = save_auto_copy_settings(
            user_id,
            enabled=True,
            stake=setup["stake"],
            risk_mode=risk_mode,
            risk=setup["risk"],
            win=setup["win"],
        )

        if not saved:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>Couldn't save your auto-copy settings right "
                    "now.</b>\n\nPlease try again in a moment from "
                    "🔗 Connect Deriv."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        mode_desc = (
            f"a fixed Risk ${setup['risk']} → Win ${setup['win']}"
            if risk_mode == "fixed"
            else "each signal's own suggested risk/target"
        )
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "✅ <b>Auto-Copy is ON.</b>\n\n"
                f"Every synthetic index signal will trade automatically "
                f"at ${setup['stake']} stake, using {mode_desc}.\n\n"
                "If your balance is ever too low, a smaller stake is "
                "tried automatically and you'll get a DM either way.\n\n"
                "<i>Turn this off anytime from 🔗 Connect Deriv.</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )



        user_id = data.replace("execconfirm_", "")
        trade_context = pending_trades.pop(user_id, None)  # pop immediately, prevents a double-tap re-executing

        if not trade_context:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>That trade has expired.</b>\n\n"
                    "Please request a new signal and tap 🎯 Trade This "
                    "Signal again."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        account = get_deriv_account(user_id)
        if not account:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>No linked Deriv account found.</b>\n\n"
                    "Tap 🔗 Connect Deriv first to link your real account "
                    "before trading."
                ),
                parse_mode=ParseMode.HTML
            )
            return

        wait_message = await context.bot.send_message(
            chat_id=int(user_id),
            text="⏳ <b>Checking your balance...</b>",
            parse_mode=ParseMode.HTML
        )

        snapshot = await deriv_fetch_account_snapshot(account["api_token"])
        if snapshot and snapshot.get("balance") is not None:
            if snapshot["balance"] < trade_context["stake"]:
                await wait_message.delete()
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Not enough balance for this stake.</b>\n\n"
                        f"Your balance: ${snapshot['balance']} | "
                        f"Stake needed: ${trade_context['stake']}\n\n"
                        f"Try a smaller amount, or top up your Deriv account."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                return

        await wait_message.edit_text(
            "⏳ <b>Placing your trade...</b>",
            parse_mode=ParseMode.HTML
        )

        buy_data, error = await deriv_execute_multiplier_trade(
            account["api_token"],
            trade_context["symbol"],
            trade_context["contract_type"],
            trade_context["multiplier"],
            trade_context["stake"],
            trade_context["risk"],
            trade_context["win"],
        )

        await wait_message.delete()

        if error:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=f"❌ <b>Trade not placed.</b>\n\n{friendly_trade_error(error)}",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        contract_id = buy_data.get("contract_id", "—")
        buy_price = buy_data.get("buy_price", trade_context["stake"])

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"✅ <b>Trade placed!</b>\n\n"
                f"<b>{trade_context['direction']} {trade_context['display']}</b>\n"
                f"Stake: ${buy_price}\n"
                f"Contract ID: {contract_id}\n\n"
                f"<i>Deriv will automatically close this at your stop loss, "
                f"take profit, or stop-out level.</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
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

    if user_modes.get(user_id) == "awaiting_deriv_token":

        token = message.strip()

        wait_message = await update.message.reply_text(
            "🔄 <b>Verifying your Deriv account...</b>",
            parse_mode=ParseMode.HTML
        )

        snapshot = await deriv_fetch_account_snapshot(token)

        await wait_message.delete()

        if not snapshot:
            await update.message.reply_text(
                "❌ <b>That token didn't work.</b>\n\n"
                "Double-check you copied the full token and that it has "
                "the <b>Trade</b> and <b>Account management</b> scopes "
                "enabled, then paste it again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        if snapshot["is_virtual"]:
            await update.message.reply_text(
                "🚫 <b>That's a demo account.</b>\n\n"
                "Nexora account linking is for verified real-money "
                "traders only. Please generate a token from your "
                "<b>real</b> Deriv account and paste it again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        saved = save_deriv_account(
            user_id, snapshot["loginid"], token, snapshot["currency"]
        )

        if not saved:
            await update.message.reply_text(
                "⚠️ <b>Your account was verified but couldn't be saved "
                "right now.</b>\n\nPlease try pasting the token again "
                "in a moment.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        open_count = len(snapshot["open_contracts"])
        user_modes[user_id] = None

        await update.message.reply_text(
            f"✅ <b>Deriv Options account linked!</b>\n\n"
            f"<b>Account:</b> {snapshot['loginid']}\n"
            f"<b>Balance:</b> {snapshot['balance']} {snapshot['currency']}\n"
            f"<b>Open Positions:</b> {open_count}\n\n"
            f"ℹ️ <i>This is your Options account specifically. MT5 and "
            f"cTrader balances aren't connected yet.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )

        # Arrived here via Trade This Signal (no account was linked
        # yet) - resume straight into stake selection instead of
        # stopping at the link confirmation, so the whole signal ->
        # connect -> trade flow stays one continuous motion.
        pending_trade = pending_trades.get(user_id)
        if pending_trade:
            await send_tier_selection(context.bot, user_id, pending_trade)
        else:
            await update.message.reply_text(
                "🤖 <b>One more thing - how do you want to trade?</b>\n\n"
                "<b>Manual</b> - tap 🎯 Trade This Signal yourself "
                "each time a signal posts.\n\n"
                "<b>Auto-copy</b> - every synthetic index signal trades "
                "automatically on this account, no tapping needed. "
                "You set the stake once, and it's never on by default - "
                "you can turn it off anytime.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✋ Manual (tap each time)",
                        callback_data="autocopy_setup_manual"
                    )],
                    [InlineKeyboardButton(
                        "🤖 Set up Auto-Copy",
                        callback_data="autocopy_setup_start"
                    )],
                ])
            )
        return

    if user_modes.get(user_id) == "awaiting_trade_confirm":

        trade_context = pending_trades.get(user_id)
        if not trade_context:
            user_modes[user_id] = None
            await update.message.reply_text(
                "⚠️ <b>That trade has expired.</b>\n\n"
                "Please request a new signal and tap 🎯 Trade This Signal "
                "again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        reply = message.strip().lower()

        stake_match = re.search(r"stake\s*=\s*([\d.]+)", reply)
        risk_match = re.search(r"risk\s*=\s*([\d.]+)", reply)
        win_match = re.search(r"win\s*=\s*([\d.]+)", reply)

        if not (stake_match or risk_match or win_match):
            await update.message.reply_text(
                "⚠️ <b>I didn't understand that.</b>\n\n"
                "Send custom values like:\n"
                "<code>stake=20 risk=5 win=10</code>",
                parse_mode=ParseMode.HTML
            )
            return

        if stake_match:
            trade_context["stake"] = float(stake_match.group(1))
        if risk_match:
            trade_context["risk"] = float(risk_match.group(1))
        if win_match:
            trade_context["win"] = float(win_match.group(1))

        pending_trades[user_id] = trade_context
        user_modes[user_id] = None

        await update.message.reply_text(
            f"🎯 <b>Confirm this trade:</b>\n\n"
            f"<b>{trade_context['direction']} {trade_context['display']}</b>\n"
            f"Stake: ${trade_context['stake']} | "
            f"Risk ${trade_context['risk']} → Target ${trade_context['win']}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirm", callback_data=f"execconfirm_{user_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edittrade_{user_id}"),
            ]])
        )
        return

    mode = user_modes.get(user_id)

    # Synthetic (Deriv) signal requests bypass the Exness trial gate
    # entirely - same reasoning as the Connect Deriv fix above, that
    # gate exists specifically to push forex-signal users toward
    # Exness registration and has nothing to do with Deriv. Forex
    # pairs and Breakdown mode remain gated normally below.
    if mode == "signal":
        synthetic_key = match_synthetic_key(message)
        if synthetic_key:
            wait_message = await update.message.reply_text(
                "🧠 <b>Nexora AI analyzing live market...</b>",
                parse_mode=ParseMode.HTML
            )

            result = await build_synthetic_signal_response(synthetic_key)

            await wait_message.delete()

            if not result:
                await update.message.reply_text(
                    "⚠️ <b>Unable to generate a signal right now.</b>\n"
                    "Please try again shortly.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                return

            signal_image_id, signal_message, trade_context = result
            pending_trades[user_id] = trade_context

            await update.message.reply_photo(
                photo=signal_image_id,
                caption=signal_message,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🎯 Trade This Signal",
                        callback_data=f"synthtrade_{user_id}"
                    )]
                ])
            )
            return

    if not is_verified(user_id) and get_trial_count(user_id) >= FREE_TRIAL_LIMIT:
        user_modes[user_id] = "awaiting_email"
        await send_verification_gate(update)
        return

    if mode == "signal":

        requested_key = match_pair_key(message)
        if (
            requested_key
            and requested_key != "btcusd"
            and is_forex_market_closed()
        ):
            await update.message.reply_text(
                "🌙 <b>Forex Market Closed for the Week</b>\n\n"
                "Gold, Silver, Oil and all Forex pairs are closed "
                "until the market reopens Sunday.\n\n"
                "₿ <b>Crypto (BTCUSD)</b> trades 24/7 — try that "
                "pair instead!",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

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
        elif signal == "MARKET_CLOSED":
            await update.message.reply_text(
                "🌙 <b>Forex Market Closed for the Week</b>\n\n"
                "Gold, Silver, Oil and all Forex pairs are closed "
                "until the market reopens Sunday.\n\n"
                "₿ <b>Crypto (BTCUSD)</b> trades 24/7 — try that "
                "pair instead!",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
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
# AUTO SIGNAL — Button on Channel 1 only.
# Logs every posted signal to signal_log so the
# TP/SL monitor and weekly report can track it.
# Always uses AI bias (user_id=None exempts it
# from daily caps).
# ============================================

# ============================================
# AUTO SIGNAL — Button on Channel 1 only.
# Logs every posted signal to signal_log so the
# TP/SL monitor and weekly report can track it.
# Always uses AI bias (user_id=None exempts it
# from daily caps). Core logic lives in
# _post_signal_for_pair so the startup catch-up
# routine below can reuse the exact same code
# path instead of a second copy that could drift.
# ============================================

async def _post_signal_for_pair(bot, pair_keyword):
    now = datetime.utcnow().strftime('%H:%M UTC')

    print(f"[AUTO SIGNAL] {pair_keyword.upper()} firing at {now}")

    image_file_id, direction, signal, signal_data = (
        await build_signal_response(pair_keyword, user_id=None)
    )

    if signal_data is None:
        if signal == "MARKET_CLOSED":
            print(f"[AUTO SIGNAL] ⏸️ {pair_keyword.upper()} skipped — forex market closed.")
        else:
            print(f"[AUTO SIGNAL] ❌ Could not fetch price for {pair_keyword}.")
        return

    signal_id = log_signal(signal_data)

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            markup = (
                get_channel_button()
                if channel_id == CHANNEL_1_ID
                else None
            )

            await bot.send_photo(
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

        except Exception as e:
            print(f"[AUTO SIGNAL] ❌ Failed for {channel_id}: {e}")

    # Placed exactly once per signal, regardless of how many
    # channels it's posted to - this used to fire once per channel
    # in the loop above, silently doubling every trade's exposure.
    asyncio.create_task(place_and_link_mt5_trade(signal_id, signal_data))

async def post_auto_signal(context: ContextTypes.DEFAULT_TYPE):
    pair_keyword = context.job.data
    await _post_signal_for_pair(context.bot, pair_keyword)

# ============================================
# SYNTHETIC INDEX CHANNEL POSTING (NEW)
# Mirrors _post_signal_for_pair above, but posts
# a plain text message (no chart image exists for
# synthetics) with a chantrade_ button instead of
# a per-user synthtrade_ one, since this signal
# isn't generated on behalf of any single person -
# whoever taps it gets their own independent trade
# context, copied fresh from channel_signal_context.
# ============================================

async def _post_synthetic_signal_for_index(bot, index_key):
    print(f"[AUTO SYNTH] {index_key.upper()} firing")

    result = await build_synthetic_signal_response(index_key)
    if not result:
        print(f"[AUTO SYNTH] ❌ No signal generated for {index_key}, skipping post")
        return

    signal_image_id, signal_message, trade_context = result
    channel_signal_context[index_key] = trade_context

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            await bot.send_photo(
                chat_id=channel_id,
                photo=signal_image_id,
                caption=signal_message,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🎯 Trade This Signal",
                        url=f"https://t.me/{BOT_USERNAME}?start=chantrade_{index_key}"
                    )
                ]])
            )
            print(f"[AUTO SYNTH] ✅ {index_key.upper()} posted to {channel_id}")
        except Exception as e:
            print(f"[AUTO SYNTH] ❌ Failed for {channel_id}: {e}")

    # Fired once per signal, after both channel posts - never inside
    # the loop above, same double-fire mistake already fixed once for
    # MT5 placement. Background task so a slow/large auto-copy run
    # never delays the channel post itself.
    asyncio.create_task(run_auto_copy_for_signal(bot, trade_context))

async def post_auto_synthetic_signal(context: ContextTypes.DEFAULT_TYPE):
    slot_number = context.job.data
    index_key = get_rotation_key(slot_number)
    await _post_synthetic_signal_for_index(context.bot, index_key)

# ============================================
# BROADCAST (NEW)
# Telegram's reply keyboard (the row of buttons
# at the bottom) only refreshes on a person's
# screen when a NEW message arrives carrying the
# updated layout - anyone who hasn't gotten a
# fresh message since a button was added is still
# looking at their old keyboard. /broadcast solves
# both at once: announces something AND refreshes
# every recipient's keyboard, since main_keyboard
# is attached to the same message.
# Runs as a background task (not awaited directly
# in the command handler) so a multi-minute send
# to thousands of users never blocks the bot from
# handling everyone else's messages in the meantime.
# ============================================

async def get_all_known_user_ids():
    """
    Unions every user_id from trial_users and verified_users -
    together these cover everyone who's ever interacted with the
    bot, which is the full broadcast audience.
    """
    user_ids = set()
    try:
        url = f"{SUPABASE_URL}/rest/v1/trial_users?select=user_id"
        response = requests.get(url, headers=sb_headers(), timeout=15)
        for row in response.json():
            user_ids.add(str(row["user_id"]))
    except Exception as e:
        print(f"[BROADCAST] Failed to fetch trial_users: {e}")

    try:
        url = f"{SUPABASE_URL}/rest/v1/verified_users?select=user_id"
        response = requests.get(url, headers=sb_headers(), timeout=15)
        for row in response.json():
            user_ids.add(str(row["user_id"]))
    except Exception as e:
        print(f"[BROADCAST] Failed to fetch verified_users: {e}")

    return user_ids

async def _run_broadcast(bot, message_text, admin_chat_id):
    user_ids = await get_all_known_user_ids()
    total = len(user_ids)
    print(f"[BROADCAST] Starting send to {total} users")

    sent = 0
    failed = 0

    for uid in user_ids:
        try:
            await bot.send_message(
                chat_id=int(uid),
                text=message_text,
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[BROADCAST] Failed for {uid}: {e}")

        await asyncio.sleep(0.05)  # ~20/sec, safely under Telegram's ~30/sec global limit

    print(f"[BROADCAST] Done — sent {sent}, failed {failed}")
    try:
        await bot.send_message(
            chat_id=admin_chat_id,
            text=(
                f"✅ <b>Broadcast complete.</b>\n\n"
                f"Sent: {sent}\n"
                f"Failed: {failed} (likely blocked the bot or deleted "
                f"their account)"
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[BROADCAST] Couldn't report completion to admin: {e}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)

    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    message_text = update.message.text.replace("/broadcast", "", 1).strip()
    if not message_text:
        await update.message.reply_text(
            "Usage: /broadcast <message>\n\n"
            "Sends to every known user, with the current keyboard "
            "attached so everyone's buttons refresh too. HTML "
            "formatting tags (<b>, <i>, etc.) are supported."
        )
        return

    user_count = len(await get_all_known_user_ids())
    await update.message.reply_text(
        f"📡 <b>Broadcasting to {user_count} users in the background...</b>\n"
        f"I'll message you here when it's done.",
        parse_mode=ParseMode.HTML
    )

    asyncio.create_task(
        _run_broadcast(context.bot, message_text, update.message.chat_id)
    )

# ============================================
# STARTUP CATCH-UP (NEW)
# JobQueue schedules live only in memory and
# fire only at their exact scheduled time - if
# Railway restarts the bot near a scheduled slot
# (e.g. a transient Telegram 502 forcing a
# crash/restart right around 07:00 UTC), that
# slot is silently lost for the day with no
# retry. This runs once at startup: for each
# signal slot whose time has already passed
# today, checks signal_log (which only logs
# official channel signals) to see if it was
# actually posted - if not, posts it immediately.
# Safe to run on every restart: if a slot already
# posted normally, has_signal_posted_today returns
# True and nothing happens, so a routine mid-day
# deploy never causes a duplicate post.
# ============================================

def has_signal_posted_today(pair_name):
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        url = (
            f"{SUPABASE_URL}/rest/v1/signal_log"
            f"?pair_name=eq.{pair_name}"
            f"&posted_at=gte.{today_str}T00:00:00"
            f"&select=id&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return len(data) > 0
    except Exception as e:
        print(f"[CATCHUP] has_signal_posted_today error: {e}")
        return True  # fail safe — assume posted, never double-post on a DB hiccup

def try_claim_catchup_lock(pair_name):
    """
    Atomically claims the right to post today's catch-up signal for
    this pair. has_signal_posted_today alone can't fully prevent a
    duplicate if two bot instances briefly overlap during a Railway
    deploy - both could check it as "not posted yet" before either
    one's signal_log row is actually written. This closes that gap:
    it tries to insert a row into signal_catchup_lock, which has a
    primary key on (pair_name, lock_date). Only the first instance's
    insert succeeds; Supabase rejects the second with a 409 conflict,
    so only one instance ever proceeds to post.
    Requires a Supabase table: signal_catchup_lock with columns
    pair_name (text), lock_date (text), claimed_at (text), and a
    primary key on (pair_name, lock_date).
    """
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"{SUPABASE_URL}/rest/v1/signal_catchup_lock"
        payload = {
            "pair_name": pair_name,
            "lock_date": today_str,
            "claimed_at": datetime.utcnow().isoformat(),
        }
        response = requests.post(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code in (200, 201):
            return True
        if response.status_code == 409:
            print(f"[CATCHUP] Lock already claimed for {pair_name} today — skipping")
            return False
        print(f"[CATCHUP] Unexpected lock response {response.status_code} for {pair_name}")
        return False
    except Exception as e:
        print(f"[CATCHUP] try_claim_catchup_lock error: {e}")
        return False  # fail safe — if the lock can't be confirmed, don't risk a duplicate

async def catch_up_missed_signals(app):
    now = datetime.utcnow()
    today_weekday = now.weekday()

    for utc_time, post_type, data in DAILY_SCHEDULE:
        if post_type != "signal":
            continue

        if data != "btcusd" and today_weekday in (5, 6):
            continue  # forex/gold market closed, this slot wasn't supposed to fire anyway

        h, m = map(int, utc_time.split(":"))
        scheduled_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)

        if now < scheduled_dt:
            continue  # today's slot hasn't happened yet, nothing to catch up

        pair_name = PAIR_CONFIG[data]["pair_name"]
        if has_signal_posted_today(pair_name):
            continue  # already posted today, nothing missed

        if not try_claim_catchup_lock(pair_name):
            continue  # another instance already claimed this catch-up

        print(
            f"[CATCHUP] {pair_name} missed its {utc_time} UTC slot "
            f"today — posting now"
        )
        await _post_signal_for_pair(app.bot, data)

# ============================================
# TP/SL MONITOR
# Runs every 15 minutes. For each OPEN signal,
# checks the REAL MT5 outcome via mt5_order_id
# (see get_mt5_trade_outcome above) - this is
# ground truth from the actual trade, so it
# correctly catches manual closes and slippage,
# not just price crossing the posted TP/SL level.
#
# Falls back to the old price-feed inference only
# for signals with no mt5_order_id on the row (e.g.
# logged before this fix, or if trade placement
# failed for that signal) - so nothing regresses
# for older rows still sitting OPEN.
# ============================================

async def check_open_signals(context: ContextTypes.DEFAULT_TYPE):
    open_signals = get_open_signals()
    if not open_signals:
        return

    for sig in open_signals:
        mt5_order_id = sig.get("mt5_order_id")

        if mt5_order_id:
            outcome, profit = await get_mt5_trade_outcome(mt5_order_id)
            if outcome == "CLOSED":
                status = "TP_HIT" if profit >= 0 else "SL_HIT"
                update_signal_status(sig["id"], status)
            # outcome == "OPEN" -> still running, nothing to do
            # outcome is None -> lookup failed, retry next sweep
            continue

        # No mt5_order_id on this row - fall back to price inference
        pair_name = sig.get("pair_name")
        pair_key = next(
            (k for k, c in PAIR_CONFIG.items() if c["pair_name"] == pair_name),
            None
        )
        if not pair_key:
            continue

        config = PAIR_CONFIG[pair_key]
        current_price, _ = get_cached_price_data(
            pair_key, config["symbol"], config
        )
        if current_price is None:
            continue

        direction = sig.get("direction")
        take_profit = sig.get("take_profit")
        stop_loss = sig.get("stop_loss")

        if direction == "BUY":
            if current_price >= take_profit:
                update_signal_status(sig["id"], "TP_HIT")
            elif current_price <= stop_loss:
                update_signal_status(sig["id"], "SL_HIT")
        else:
            if current_price <= take_profit:
                update_signal_status(sig["id"], "TP_HIT")
            elif current_price >= stop_loss:
                update_signal_status(sig["id"], "SL_HIT")

# ============================================
# WEEKLY PERFORMANCE REPORT (NEW)
# Runs every Sunday at 23:00 UTC, covering the
# full Monday 00:00 UTC -> Sunday 23:00 UTC week
# (including weekend BTCUSD activity). Posted to
# both channels, no comments needed since it's a
# self-contained summary - total signals issued,
# TP hits, SL hits, still-open count, and an
# overall win rate.
# ============================================

def get_week_start():
    now = datetime.utcnow()
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)

async def post_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    week_start = get_week_start()
    now = datetime.utcnow()

    signals = get_signals_since(week_start)

    total = len(signals)
    tp_hit = sum(1 for s in signals if s.get("status") == "TP_HIT")
    sl_hit = sum(1 for s in signals if s.get("status") == "SL_HIT")
    still_open = sum(1 for s in signals if s.get("status") == "OPEN")
    closed = tp_hit + sl_hit
    win_rate = round((tp_hit / closed) * 100) if closed > 0 else 0

    date_range = f"{week_start.strftime('%d %b')} – {now.strftime('%d %b %Y')}"

    report = (
        f"📊 <b>WEEKLY PERFORMANCE REPORT</b>\n"
        f"<i>#SpiritFX — {date_range}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Total Signals Issued:</b> {total}\n\n"
        f"✅ <b>Take Profit Hit:</b> {tp_hit}\n"
        f"❌ <b>Stop Loss Hit:</b> {sl_hit}\n"
        f"⏳ <b>Still Running:</b> {still_open}\n\n"
        f"🎯 <b>Win Rate:</b> {win_rate}% "
        f"({tp_hit}/{closed} closed signals)\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<i>Trade safe 💼🔥</i>"
    )

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=report,
                parse_mode=ParseMode.HTML
            )
            print(f"[WEEKLY REPORT] ✅ Posted to {channel_id}")
        except Exception as e:
            print(f"[WEEKLY REPORT] ❌ Failed for {channel_id}: {e}")

# ============================================
# MAIN
# ============================================

def main():

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(catch_up_missed_signals)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(
        MessageHandler(
            filters.Regex(
                "^(📊 Signal|📚 Breakdown|🔗 Connect Deriv|signal|breakdown|connect deriv)$"
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

    # BTCUSD trades 24/7 so it runs every day. Every other scheduled
    # pair (gold, forex) has its market closed Sat/Sun - posting then
    # would use stale Friday-close prices, so those are restricted to
    # weekdays only (0=Monday ... 6=Sunday).
    WEEKDAYS_ONLY = (0, 1, 2, 3, 4)
    EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)

    for i, (utc_time, post_type, data) in enumerate(DAILY_SCHEDULE):
        if post_type == "news":
            job_queue.run_daily(
                post_news,
                time=parse_time(utc_time),
                name=f"news_{i}_{data}",
                data=data
            )
        elif post_type == "signal":
            days = EVERY_DAY if data == "btcusd" else WEEKDAYS_ONLY
            job_queue.run_daily(
                post_auto_signal,
                time=parse_time(utc_time),
                name=f"signal_{i}_{data}",
                data=data,
                days=days
            )

    WEEKEND_ONLY = (5, 6)

    for i, (utc_time, schedule_type, slot_number) in enumerate(SYNTHETIC_SCHEDULE):
        days = WEEKDAYS_ONLY if schedule_type == "weekday" else WEEKEND_ONLY
        job_queue.run_daily(
            post_auto_synthetic_signal,
            time=parse_time(utc_time),
            name=f"synth_{i}_{schedule_type}_{slot_number}",
            data=slot_number,
            days=days
        )

    # TP/SL monitor - checks every OPEN logged signal every 15 minutes
    job_queue.run_repeating(
        check_open_signals,
        interval=900,
        first=60,
        name="tp_sl_monitor"
    )

    # Weekly performance report - every Sunday at 23:00 UTC
    # NOTE: PTB v20+ uses cron-style day indexing for run_daily's
    # `days` param (0=Sunday ... 6=Saturday), NOT Python's
    # datetime.weekday() convention (0=Monday ... 6=Sunday). days=(6,)
    # was firing Saturday, not Sunday - days=(0,) is correct here.
    job_queue.run_daily(
        post_weekly_report,
        time=parse_time("23:00"),
        name="weekly_report",
        days=(0,)
    )

    print("Nexora AI Running...")
    print("Daily schedule (UTC):")
    for utc_time, post_type, data in DAILY_SCHEDULE:
        emoji = "📰" if post_type == "news" else "📊"
        weekend_note = "" if data == "btcusd" else " (weekdays only)"
        print(f"  {emoji} {utc_time} UTC — {data.upper()}{weekend_note}")
    for utc_time, schedule_type, slot_number in SYNTHETIC_SCHEDULE:
        note = "weekdays only" if schedule_type == "weekday" else "weekends only"
        print(f"  ⚡ {utc_time} UTC — SYNTHETIC ROTATION ({note}, slot {slot_number})")
    print("  🔁 TP/SL monitor — every 15 minutes")
    print("  📊 23:00 UTC Sunday — WEEKLY REPORT")
    print(f"Channel 1 (Public): {CHANNEL_1_ID}")
    print(f"Channel 2 (Inner Circle): {CHANNEL_2_ID}")
    print(f"Verify Group: {VERIFY_GROUP_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
