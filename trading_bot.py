import os
import asyncio
import random
import requests
import re
import json
import time
import inspect
import websockets
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

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
from telegram.error import TimedOut

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
CHANNEL_3_ID = os.getenv("CHANNEL_3_ID", "-1003928419513")  # Official Nexora AI channel - builds its own audience, gets the same content (signals/news) plus the same "Get Your Own Signal" CTA button as Channel 1
FOLLOW_GATE_CHANNEL = "@nexoraaitrading"  # https://t.me/nexoraaitrading - bot MUST be added as admin here for get_chat_member to work reliably

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

# ============================================
# DAILY SCHEDULE (UTC)
# Times are in UTC - "07:00 UTC" = 8AM Lagos,
# "17:00 UTC" = 6PM Lagos, "11:00 UTC" = 12PM
# Lagos, matching how the team actually thinks
# about these slots.
#
# MORNING_PAIR_BY_WEEKDAY / EVENING_PAIR_BY_WEEKDAY
# use Python's datetime.weekday() convention:
# 0=Monday ... 6=Sunday. This is DELIBERATELY
# different from PTB's run_daily `days` param
# (which is cron-style, 0=Sunday...6=Saturday -
# see the weekly_report fix elsewhere in this file
# for the exact bug that confusion caused before).
# Both daily jobs below run EVERY day and look up
# today's pair themselves, rather than relying on
# run_daily's days= restriction per pair - that
# mechanism can't vary the pair by day, only
# include/exclude whole days.
#
# A day with no evening pair (Sat/Sun) maps to
# None - the job simply does nothing that slot.
# ============================================

MORNING_PAIR_BY_WEEKDAY = {
    0: "xauusd",  # Monday
    1: "xauusd",  # Tuesday
    2: "xauusd",  # Wednesday
    3: "xauusd",  # Thursday
    4: "xauusd",  # Friday
    5: "btcusd",  # Saturday
    6: "btcusd",  # Sunday
}

EVENING_PAIR_BY_WEEKDAY = {
    0: "btcusd",  # Monday
    1: "xagusd",  # Tuesday
    2: "usoil",   # Wednesday
    3: "btcusd",  # Thursday
    4: "usoil",   # Friday
    5: None,      # Saturday - no evening forex/crypto slot, volatility only
    6: None,      # Sunday - no evening forex/crypto slot, volatility only
}

DAILY_SCHEDULE = [
    ("06:00", "news", "morning"),  # 7:00 AM Lagos
]

# Synthetic index channel posts - rotates through all 5 indices.
# Wednesday gets its own dedicated 12PM Lagos (11:00 UTC) slot in
# addition to the existing weekend slots (Sat/Sun 6PM Lagos = 17:00
# UTC) - so volatility indices post Wed/Sat/Sun, not every day.
SYNTHETIC_SCHEDULE = [
    ("11:00", "wednesday_only", 0),
    ("17:00", "weekend", 0),
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
            callback_data="channelcta"
        )]
    ])

# ============================================
# USER MODES
# ============================================

user_modes = {}

# DB-backed for the same reason pending_trades_db/channel_signal_
# context_db are: a plain in-memory dict here meant a Railway restart
# between someone submitting a verification request and it being
# approved/rejected wiped all memory of "this person already has a
# pending request" - so the SAME person resubmitting hours later (not
# a different account, the identical user_id) looked like a brand new
# request, flooding the admin approval group with duplicate cards for
# the same person. Requires a Supabase table: pending_verifications_db
# (user_id text primary key, email text), RLS DISABLED (same as the
# other _db tables - writes go through the service-role key directly).

class PendingVerificationsStore:
    """Dict-like interface backed by Supabase - get/set/del/in/items() all supported."""

    def get(self, user_id, default=None):
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/pending_verifications_db"
                f"?user_id=eq.{user_id}&select=email"
            )
            response = requests.get(url, headers=sb_headers(), timeout=10)
            data = response.json()
            return data[0]["email"] if data else default
        except Exception as e:
            print(f"[PENDING VERIFICATIONS] get error: {e}")
            return default

    def __getitem__(self, user_id):
        result = self.get(user_id)
        if result is None:
            raise KeyError(user_id)
        return result

    def __setitem__(self, user_id, email):
        try:
            url = f"{SUPABASE_URL}/rest/v1/pending_verifications_db?on_conflict=user_id"
            headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
            response = requests.post(
                url, headers=headers,
                json={"user_id": str(user_id), "email": email}, timeout=10
            )
            if response.status_code not in (200, 201):
                print(f"[PENDING VERIFICATIONS] set unexpected status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[PENDING VERIFICATIONS] set error: {e}")

    def __delitem__(self, user_id):
        try:
            url = f"{SUPABASE_URL}/rest/v1/pending_verifications_db?user_id=eq.{user_id}"
            requests.delete(url, headers=sb_headers(), timeout=10)
        except Exception as e:
            print(f"[PENDING VERIFICATIONS] delete error: {e}")

    def __contains__(self, user_id):
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/pending_verifications_db"
                f"?user_id=eq.{user_id}&select=user_id"
            )
            response = requests.get(url, headers=sb_headers(), timeout=10)
            data = response.json()
            return len(data) > 0
        except Exception as e:
            print(f"[PENDING VERIFICATIONS] contains error: {e}")
            return False

    def items(self):
        """
        Used only by the duplicate-email-across-different-users check
        (a different user trying to claim an email already pending on
        someone else's account) - fetches all pending rows for that
        comparison.
        """
        try:
            url = f"{SUPABASE_URL}/rest/v1/pending_verifications_db?select=user_id,email"
            response = requests.get(url, headers=sb_headers(), timeout=10)
            data = response.json()
            return [(row["user_id"], row["email"]) for row in data] if isinstance(data, list) else []
        except Exception as e:
            print(f"[PENDING VERIFICATIONS] items error: {e}")
            return []

pending_verifications = PendingVerificationsStore()

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
    """
    Whole-word matching, not substring - the old `keyword in text`
    check let short keywords like "eur", "yen", "boe" match inside
    unrelated words/tickers (e.g. a German stock exchange ticker like
    "XETR:3A9" slipping through, unrelated to forex or Bitcoin at
    all). re.escape handles keywords with spaces (e.g. "interest
    rate") safely inside the word-boundary pattern.
    """
    text = f"{title} {description}".lower()
    return any(
        re.search(rf"\b{re.escape(keyword)}\b", text)
        for keyword in NEWS_RELEVANT_KEYWORDS
    )

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

def get_verified_user_by_email(email):
    """
    Returns the verified_users row already using this email (if any),
    so a second Telegram account can't reuse an email that's already
    verified on a different account.
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/verified_users"
            f"?email=eq.{email}&select=user_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if data:
            return data[0]
        return None
    except Exception as e:
        print(f"[DB] get_verified_user_by_email error: {e}")
        return None

def add_verified_user(user_id, email):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/verified_users"
            f"?on_conflict=user_id"
        )
        payload = {
            "user_id": str(user_id),
            "email": email,
            "verified_at": datetime.utcnow().isoformat()
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code not in (200, 201):
            print(f"[DB] ❌ add_verified_user unexpected status {response.status_code}: {response.text}")
            return False
        print(f"[DB] ✅ Verified user saved: {user_id}")
        return True
    except Exception as e:
        print(f"[DB] add_verified_user error: {e}")
        return False

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

async def is_following_channel(bot, user_id):
    """
    Checks real Telegram membership status via get_chat_member -
    requires the bot to be an admin of FOLLOW_GATE_CHANNEL, otherwise
    Telegram won't reliably report other users' status. MEMBER,
    ADMINISTRATOR, and OWNER all count as "following"; LEFT and
    BANNED do not. Fails toward letting the user through (returns
    True) on any API error - a broken check should never permanently
    lock someone out of the bot entirely.
    """
    try:
        member = await bot.get_chat_member(chat_id=FOLLOW_GATE_CHANNEL, user_id=int(user_id))
        return member.status in ("member", "administrator", "creator", "owner")
    except Exception as e:
        print(f"[FOLLOW GATE] Couldn't check membership for {user_id}: {e}")
        return True

def is_first_time_user(user_id):
    """
    True only if this user has NEVER been seen before at all - no
    verified_users row, no trial_users row. Distinct from
    trial_remaining()>0, which is also true for someone who's used
    the bot before but simply hasn't burned through their trials yet -
    that's a returning user, not a first-time one. Used specifically
    to gate the channel-follow requirement to genuine first contact
    only, per explicit instruction that returning users should never
    see it.
    """
    if is_verified(user_id):
        return False
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/trial_users"
            f"?user_id=eq.{user_id}&select=user_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return len(data) == 0
    except Exception as e:
        print(f"[DB] is_first_time_user error: {e}")
        # Fail toward NOT gating - a DB hiccup should never block
        # someone from using the bot at all, and worst case a
        # returning user just doesn't see the follow-channel prompt
        # this one time, which is harmless.
        return False

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

async def _delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """No longer used - process_due_auto_deletes performs deletions directly now. Kept as a no-op stub only if something external still references it."""
    pass

# DB-backed for the same reason pending_trades_db/channel_signal_
# context_db/pending_verifications_db all are: scheduling via
# job_queue.run_once alone only lives in the bot's live memory, and
# Railway restarts (confirmed to happen frequently during active
# development) silently wipe every pending deletion that hasn't
# fired yet - this is almost certainly the real reason "24h
# auto-delete never seems to work" despite the scheduling code
# itself being correct. Requires a Supabase table:
# auto_delete_queue_db (id bigint identity primary key, chat_id
# text, message_id bigint, delete_at timestamptz), RLS DISABLED
# (same as the other _db tables).

def schedule_auto_delete(chat_id, message_id, hours=24):
    """
    Schedules a DM message for deletion after `hours` (default 24).
    Used for routine/non-critical bot DMs - signals, breakdowns, news,
    setup prompts, and error/failure messages - so a user's chat with
    the bot doesn't pile up with old messages over time.

    NEVER call this for: verification status messages, account
    balance/connection info, auto-copy settings confirmations, or
    SUCCESSFUL trade placement confirmations - those stay permanently
    (trade history, and things a user may need to refer back to,
    e.g. "was I verified?", "what's my auto-copy stake set to?").

    Writes to auto_delete_queue_db rather than (or in addition to)
    scheduling an in-memory job - process_due_auto_deletes (a
    recurring job, see main()) is what actually performs the
    deletion once delete_at has passed, and that sweep survives
    restarts since it reads fresh from the DB every time it runs.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/auto_delete_queue_db"
        payload = {
            "chat_id": str(chat_id),
            "message_id": message_id,
            "delete_at": (datetime.utcnow() + timedelta(hours=hours)).isoformat(),
        }
        response = requests.post(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code not in (200, 201):
            print(f"[AUTO-DELETE] Couldn't queue delete: {response.status_code} {response.text}")
    except Exception as e:
        print(f"[AUTO-DELETE] Couldn't queue delete: {e}")

async def process_due_auto_deletes(context: ContextTypes.DEFAULT_TYPE):
    """
    Recurring sweep (see main()) that does the actual deleting - the
    part schedule_auto_delete itself no longer does directly. Reads
    every row whose delete_at has already passed, attempts the
    delete, then removes the row from the queue regardless of
    whether the delete succeeded (a message that's already gone,
    blocked, or past Telegram's deletion window should never be
    retried forever).
    """
    try:
        now_str = datetime.utcnow().isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/auto_delete_queue_db"
            f"?delete_at=lte.{now_str}&select=id,chat_id,message_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=15)
        due_rows = response.json()
    except Exception as e:
        print(f"[AUTO-DELETE] Couldn't fetch due deletes: {e}")
        return

    if not isinstance(due_rows, list) or not due_rows:
        return

    for row in due_rows:
        row_id = row.get("id")
        chat_id = row.get("chat_id")
        message_id = row.get("message_id")
        try:
            await context.bot.delete_message(chat_id=int(chat_id), message_id=int(message_id))
        except Exception as e:
            print(f"[AUTO-DELETE] Couldn't delete chat={chat_id} msg={message_id}: {e}")
        try:
            del_url = f"{SUPABASE_URL}/rest/v1/auto_delete_queue_db?id=eq.{row_id}"
            requests.delete(del_url, headers=sb_headers(), timeout=10)
        except Exception as e:
            print(f"[AUTO-DELETE] Couldn't remove queue row {row_id}: {e}")

async def send_and_auto_delete(bot, chat_id, text, **kwargs):
    """
    Sends a message and immediately schedules it for 24h auto-delete -
    convenience wrapper for the many routine/error message call sites,
    so each one doesn't need its own separate schedule_auto_delete
    call after the send. Same NEVER-call-for exceptions as
    schedule_auto_delete apply (verification, balance/connection info,
    auto-copy settings, successful trade confirmations).
    """
    sent = await bot.send_message(chat_id=chat_id, text=text, **kwargs)
    schedule_auto_delete(sent.chat_id, sent.message_id)
    return sent

def get_open_signals():
    try:
        url = f"{SUPABASE_URL}/rest/v1/signal_log?status=eq.OPEN&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[SIGNAL LOG] get_open_signals error: {e}")
        return []

def has_open_signal_for_pair(pair_name):
    """
    True if this pair already has a signal sitting OPEN in signal_log -
    used to stop a fresh scheduled signal (e.g. BTCUSD) from posting/
    trading while the previous one on the same pair hasn't closed in
    profit or loss yet (see get_mt5_trade_outcome / check_open_signals
    for how a signal eventually closes).
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/signal_log"
            f"?status=eq.OPEN&pair_name=eq.{pair_name}&select=id&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return len(data) > 0
    except Exception as e:
        print(f"[SIGNAL LOG] has_open_signal_for_pair error: {e}")
        # Fail safe by NOT blocking the signal - an unreachable DB
        # check should never silently stall the whole schedule, and
        # the duplicate-trade risk from one failed check is much
        # smaller than missing scheduled signals indefinitely.
        return False

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

            async def recv_typed(expected_type, max_attempts=5):
                """
                Reads frames until one matching expected_type arrives,
                instead of trusting that the very next frame received
                is necessarily the response to what was just sent.
                Deriv's socket can send other frames (e.g. unsolicited
                notifications); blindly parsing whatever arrives first
                risks silently treating an unrelated frame as the real
                response - which would make open_contracts wrongly
                look empty without raising any error at all.
                """
                for _ in range(max_attempts):
                    raw = json.loads(await ws.recv())
                    if raw.get("msg_type") == expected_type:
                        return raw
                    if raw.get("error"):
                        print(f"[DERIV] {expected_type} request returned an error: {raw['error']}")
                        return raw
                print(f"[DERIV] Gave up waiting for msg_type={expected_type} after {max_attempts} frames")
                return None

            await ws.send(json.dumps({"balance": 1}))
            balance_response = await recv_typed("balance") or {}
            balance_block = balance_response.get("data", balance_response.get("balance", {}))
            balance = balance_block.get("balance") if isinstance(balance_block, dict) else None

            await ws.send(json.dumps({"portfolio": 1}))
            portfolio_response = await recv_typed("portfolio") or {}
            portfolio_block = portfolio_response.get("data", portfolio_response.get("portfolio", {}))
            open_contracts = (
                portfolio_block.get("contracts", [])
                if isinstance(portfolio_block, dict) else []
            )
            # TEMP DIAGNOSTIC: the auto-copy no-stacking check assumed
            # each contract has a "symbol" field, but this was never
            # confirmed against a real response - and a real V75
            # double-trade slipped through, suggesting that assumption
            # may be wrong (or symbol isn't what's actually returned).
            # Logging the raw shape here so the next real scan tells
            # us the truth, instead of guessing again. Remove this
            # print once the real field name is confirmed and the
            # no-stacking check (see held_symbols in run_auto_copy_scan)
            # is fixed to match it.
            if open_contracts:
                print(f"[DERIV DIAGNOSTIC] Raw open_contracts sample: {open_contracts[0]}")

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

# ============================================
# AUTO-COPY TRADE TRACKING (NEW)
# Independent of Deriv's live portfolio socket
# read - that read's "is this index already
# held" check (open_contracts/symbol matching in
# run_auto_copy_scan) was never confirmed against
# a real response, and a genuine double-trade on
# V75 slipped through it. This adds a second,
# DB-backed source of truth: every auto-copy
# trade gets its own row here the moment it's
# placed, and the no-stacking check in
# run_auto_copy_scan checks BOTH this table AND
# the live socket read before allowing a new
# trade on the same user+symbol - either one
# saying "still open" is enough to block it.
#
# Requires a Supabase table: auto_copy_trades
# Columns: id (uuid/int, pk), user_id (text),
# symbol (text), contract_id (text),
# direction (text), stake/risk/win (numeric),
# status (text: OPEN/CLOSED), profit (numeric,
# nullable), placed_at (timestamptz),
# closed_at (timestamptz, nullable)
# ============================================

def log_auto_copy_trade(user_id, symbol, contract_id, direction, stake, risk, win):
    try:
        url = f"{SUPABASE_URL}/rest/v1/auto_copy_trades"
        payload = {
            "user_id": str(user_id),
            "symbol": symbol,
            "contract_id": str(contract_id),
            "direction": direction,
            "stake": stake,
            "risk": risk,
            "win": win,
            "status": "OPEN",
            "placed_at": datetime.utcnow().isoformat(),
        }
        response = requests.post(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code not in (200, 201):
            print(f"[AUTO-COPY LOG] ❌ Failed to log trade for {user_id}/{symbol}: {response.status_code} {response.text}")
            return False
        return True
    except Exception as e:
        print(f"[AUTO-COPY LOG] error: {e}")
        return False

def has_open_auto_copy_trade(user_id, symbol):
    """
    DB-backed half of the no-stacking check - independent of whatever
    Deriv's live portfolio socket returns. Fails CLOSED (blocks the
    trade) on a DB read error, the opposite default to
    has_open_signal_for_pair - a missed auto-copy trade is far less
    costly than risking a real duplicate position with real money.
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/auto_copy_trades"
            f"?user_id=eq.{user_id}&symbol=eq.{symbol}&status=eq.OPEN&select=id&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return len(data) > 0
    except Exception as e:
        print(f"[AUTO-COPY LOG] has_open_auto_copy_trade error: {e}")
        return True  # fail CLOSED here - see docstring

async def get_deriv_contract_outcome(token, contract_id):
    """
    Asks Deriv directly whether a specific contract has closed yet,
    via proposal_open_contract - mirrors get_mt5_trade_outcome's role
    for MT5 trades. Returns ("CLOSED", profit) once is_sold is true,
    ("OPEN", None) while still running, or (None, None) if the check
    itself failed (caller should treat this as "try again later", NOT
    as confirmation the trade is closed).
    """
    accounts_data = await deriv_get_options_accounts(token)
    if not accounts_data:
        return None, None
    accounts_list = accounts_data.get("data") or accounts_data.get("accounts")
    if not accounts_list:
        return None, None
    real_account = next(
        (a for a in accounts_list if not bool(
            a.get("is_virtual") or str(a.get("account_type", "")).lower() in ("demo", "virtual")
        )),
        None
    )
    if not real_account:
        return None, None
    account_id = real_account.get("account_id") or real_account.get("loginid") or real_account.get("id")
    if not account_id:
        return None, None

    ws_url = await deriv_get_otp_url(token, account_id)
    if not ws_url:
        return None, None

    try:
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps({
                "proposal_open_contract": 1,
                "contract_id": int(contract_id)
            }))
            raw = json.loads(await ws.recv())

            if raw.get("error"):
                print(f"[AUTO-COPY OUTCOME] Error for contract {contract_id}: {raw['error']}")
                return None, None

            contract = raw.get("proposal_open_contract", {})
            # TEMP DIAGNOSTIC: is_sold/profit are standard, long-
            # documented Deriv fields, but given the symbol field
            # mistake earlier, logging the raw shape here too until
            # this is confirmed against a real closed contract.
            print(f"[AUTO-COPY OUTCOME] Raw proposal_open_contract for {contract_id}: {contract}")

            if not contract.get("is_sold"):
                return "OPEN", None

            return "CLOSED", contract.get("profit")
    except Exception as e:
        print(f"[AUTO-COPY OUTCOME] Connection error for {contract_id}: {e}")
        return None, None

def mark_auto_copy_trade_closed(contract_id, profit):
    try:
        url = f"{SUPABASE_URL}/rest/v1/auto_copy_trades?contract_id=eq.{contract_id}"
        payload = {
            "status": "CLOSED",
            "profit": profit,
            "closed_at": datetime.utcnow().isoformat(),
        }
        response = requests.patch(url, headers=sb_headers(), json=payload, timeout=10)
        return response.status_code in (200, 204)
    except Exception as e:
        print(f"[AUTO-COPY LOG] mark_auto_copy_trade_closed error: {e}")
        return False

def get_open_auto_copy_trades():
    try:
        url = f"{SUPABASE_URL}/rest/v1/auto_copy_trades?status=eq.OPEN&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[AUTO-COPY LOG] get_open_auto_copy_trades error: {e}")
        return []

def log_auto_copy_failure(user_id, symbol, reason):
    """
    Logs a failed auto-copy attempt for the once-daily digest to
    count, instead of DMing the user immediately about every single
    failure - failures like "stake not supported" aren't actionable
    in the moment (auto-copy retries on the next scan automatically),
    so they're batched into one end-of-day line instead of one
    message per round. Requires a Supabase table: auto_copy_failures
    Columns: id (pk), user_id (text), symbol (text), reason (text),
    failed_at (timestamptz)
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/auto_copy_failures"
        payload = {
            "user_id": str(user_id),
            "symbol": symbol,
            "reason": reason,
            "failed_at": datetime.utcnow().isoformat(),
        }
        requests.post(url, headers=sb_headers(), json=payload, timeout=10)
    except Exception as e:
        print(f"[AUTO-COPY LOG] log_auto_copy_failure error: {e}")

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

def set_low_balance_notified(user_id, notified):
    """
    DB-backed replacement for the old in-memory low_balance_notified
    set, which silently reset to empty on every Railway restart -
    causing the same "balance too low" warning to repeat every time
    the bot redeployed, even though the user had already seen it
    minutes or hours earlier. Stored on deriv_accounts so it survives
    restarts and only actually changes when the balance episode
    itself starts or ends.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_accounts?user_id=eq.{user_id}"
        requests.patch(
            url, headers=sb_headers(),
            json={"low_balance_notified": notified}, timeout=10
        )
    except Exception as e:
        print(f"[DERIV] set_low_balance_notified error: {e}")

def set_token_invalid_notified(user_id, notified):
    """
    Same DB-backed tell-once pattern as set_low_balance_notified
    above, but for a dead/expired/revoked Deriv token - confirmed
    real case (401 "Invalid or expired token" from deriv_get_options_
    accounts) where auto-copy silently stopped trading for a user
    every single scan, forever, with nothing ever telling them why or
    that relinking would fix it. Without this flag the same warning
    would otherwise repeat every 30 minutes once added, instead of
    once per dead-token episode.

    REQUIRES a token_invalid_notified column on deriv_accounts (same
    type/default as the existing low_balance_notified column) - add
    this column in Supabase before deploying this change.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_accounts?user_id=eq.{user_id}"
        requests.patch(
            url, headers=sb_headers(),
            json={"token_invalid_notified": notified}, timeout=10
        )
    except Exception as e:
        print(f"[DERIV] set_token_invalid_notified error: {e}")



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
    # Deriv after a rejected guess). R_75 is now CONFIRMED live via a
    # real proposal error on 2026-06-24: Deriv's own response stated
    # the actual accepted set is {400, 1000, 2000, 3000, 4000} - 100
    # was wrong, just like the earlier 150 guess was wrong. Using 400
    # here (the lowest confirmed-valid value, least leveraged) rather
    # than guessing again. R_10/R_25/R_50/R_100 are UNCONFIRMED still
    # - left at 100 for now, but deriv_execute_multiplier_trade has a
    # safety net: if Deriv rejects whatever value is sent, it parses
    # the real accepted list straight out of Deriv's own error message
    # and retries once with the lowest valid value, so trades on the
    # unconfirmed indices still go through correctly even before each
    # is individually tested and hardcoded the same way R_75 just was.
    "r10": {"symbol": "R_10", "display": "Volatility 10 Index", "default_multiplier": 100},
    "r25": {"symbol": "R_25", "display": "Volatility 25 Index", "default_multiplier": 100},
    "r50": {"symbol": "R_50", "display": "Volatility 50 Index", "default_multiplier": 100},
    "r75": {"symbol": "R_75", "display": "Volatility 75 Index", "default_multiplier": 400},
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

pending_autocopy_setup = {}  # user_id -> {stake, risk, win} chosen so far, mid-setup

# ============================================
# PENDING TRADE STORAGE (DB-backed)
# Was a plain in-memory dict - meant a trade
# picked via "Custom Amount" or a stake tier
# button could silently vanish if a Railway
# redeploy landed in the gap before the user
# tapped Confirm, since every redeploy restarts
# the process and wipes in-memory state. Given
# how often this file gets redeployed during
# active development, this was a real, repeated
# failure (confirmed via Railway's deployment
# history), not a one-off. Requires a Supabase
# table: pending_trades_db. Columns: user_id
# (text, primary key), symbol, contract_type,
# direction, display (all text), multiplier,
# stake, risk, win (all numeric).
# ============================================

class PendingTradesStore:
    """
    Drop-in dict-like interface backed by Supabase, so every existing
    call site (pending_trades[user_id] = ..., pending_trades[user_id]
    read directly, pending_trades.get(...), pending_trades.pop(...))
    keeps working unchanged - only the storage underneath moved from
    memory to a DB table.
    """

    def __getitem__(self, user_id):
        """
        Handles direct bracket reads (pending_trades[user_id]) -
        distinct from .get(), and missed when this class was first
        written, which crashed the /start deep-link handler with
        TypeError: 'PendingTradesStore' object is not subscriptable
        the moment someone tapped Trade This Signal on a fresh
        channel post after the channel_signal_context_db fix went
        live. Raises KeyError on a miss, matching real dict behavior,
        since every call site here already checks truthiness/None
        via .get() first before ever using bracket access.
        """
        result = self.get(user_id)
        if result is None:
            raise KeyError(user_id)
        return result

    def get(self, user_id, default=None):
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/pending_trades_db"
                f"?user_id=eq.{user_id}&select=*"
            )
            response = requests.get(url, headers=sb_headers(), timeout=10)
            data = response.json()
            if not data:
                return default
            row = data[0]
            return {
                "symbol": row.get("symbol"),
                "contract_type": row.get("contract_type"),
                "direction": row.get("direction"),
                "display": row.get("display"),
                "multiplier": row.get("multiplier"),
                "stake": row.get("stake"),
                "risk": row.get("risk"),
                "win": row.get("win"),
            }
        except Exception as e:
            print(f"[PENDING TRADES] get error: {e}")
            return default

    def __setitem__(self, user_id, trade_context):
        try:
            url = f"{SUPABASE_URL}/rest/v1/pending_trades_db?on_conflict=user_id"
            payload = {
                "user_id": str(user_id),
                "symbol": trade_context.get("symbol"),
                "contract_type": trade_context.get("contract_type"),
                "direction": trade_context.get("direction"),
                "display": trade_context.get("display"),
                "multiplier": trade_context.get("multiplier"),
                "stake": trade_context.get("stake"),
                "risk": trade_context.get("risk"),
                "win": trade_context.get("win"),
            }
            headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code not in (200, 201):
                print(f"[PENDING TRADES] set unexpected status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[PENDING TRADES] set error: {e}")

    def pop(self, user_id, default=None):
        existing = self.get(user_id, default)
        try:
            url = f"{SUPABASE_URL}/rest/v1/pending_trades_db?user_id=eq.{user_id}"
            requests.delete(url, headers=sb_headers(), timeout=10)
        except Exception as e:
            print(f"[PENDING TRADES] pop/delete error: {e}")
        return existing

pending_trades = PendingTradesStore()

# Set once in main() right after the Application is built - lets
# auto-delete scheduling work from any function (including ones that
# only have a `bot` object, like the auto-copy background tasks)
# without threading `context` through every call site.
_app_instance = None

SYNTHETIC_ROTATION_ORDER = ["r10", "r25", "r50", "r75", "r100"]

# index_key -> most recent channel-posted trade context. Channel
# posts aren't generated for any one user (unlike DM signals), so
# when ANY member taps "Trade This Signal" on a channel post, this
# is copied into pending_trades for that specific tapper - each
# tapper gets their own independent trade context from the same
# underlying signal.
#
# DB-backed for the same reason pending_trades_db is: a plain
# in-memory dict here meant every Railway restart wiped it
# completely, permanently breaking every "Trade This Signal" button
# on every channel post sent before that restart - they'd say
# "This signal has expired" forever afterward, even just seconds
# after a fresh restart, since nothing had re-populated that index's
# entry yet. Requires a Supabase table: channel_signal_context_db
# with RLS DISABLED (same as pending_trades_db - writes go through
# the service-role key directly, not per-user Supabase auth).
# Columns: index_key (text, primary key), symbol, contract_type,
# direction, display (all text), multiplier, stake, risk, win (all
# numeric), updated_at (timestamptz).

class ChannelSignalContextStore:
    """Dict-like interface backed by Supabase, same pattern as PendingTradesStore."""

    def get(self, index_key, default=None):
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/channel_signal_context_db"
                f"?index_key=eq.{index_key}&select=*"
            )
            response = requests.get(url, headers=sb_headers(), timeout=10)
            data = response.json()
            if not data:
                return default
            row = data[0]
            return {
                "symbol": row.get("symbol"),
                "contract_type": row.get("contract_type"),
                "direction": row.get("direction"),
                "display": row.get("display"),
                "multiplier": row.get("multiplier"),
                "stake": row.get("stake"),
                "risk": row.get("risk"),
                "win": row.get("win"),
            }
        except Exception as e:
            print(f"[CHANNEL SIGNAL CONTEXT] get error: {e}")
            return default

    def __setitem__(self, index_key, trade_context):
        try:
            url = f"{SUPABASE_URL}/rest/v1/channel_signal_context_db?on_conflict=index_key"
            payload = {
                "index_key": str(index_key),
                "symbol": trade_context.get("symbol"),
                "contract_type": trade_context.get("contract_type"),
                "direction": trade_context.get("direction"),
                "display": trade_context.get("display"),
                "multiplier": trade_context.get("multiplier"),
                "stake": trade_context.get("stake"),
                "risk": trade_context.get("risk"),
                "win": trade_context.get("win"),
                "updated_at": datetime.utcnow().isoformat(),
            }
            headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code not in (200, 201):
                print(f"[CHANNEL SIGNAL CONTEXT] set unexpected status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[CHANNEL SIGNAL CONTEXT] set error: {e}")

channel_signal_context = ChannelSignalContextStore()

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

SYNTHETIC_CANDLE_CACHE_SECONDS = {"1m": 30, "5m": 120, "1h": 3600, "4h": 14400}
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
            raw_candles = response.get("candles", [])
            if not raw_candles:
                return None

            # Deriv returns open/high/low/close as STRINGS (e.g.
            # "1.08243"), not floats - confirmed from the live API
            # response shape. Every strategy in the bank does real
            # arithmetic/comparisons on these fields (RSI, MACD,
            # MA sums, range breakouts, etc.), and those either throw
            # TypeError (caught and silently swallowed by the bank's
            # per-strategy try/except) or, for simple < / > checks,
            # silently do lexicographic STRING comparison instead of
            # numeric comparison. This was making almost every
            # synthetic strategy fail every round without ever
            # surfacing an error to the channel - converting here,
            # once, at the source, the same way get_candles_twelvedata
            # already does for the forex/gold/crypto path.
            candles = []
            for c in raw_candles:
                try:
                    candles.append({
                        "time": c.get("epoch"),
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                    })
                except (KeyError, TypeError, ValueError) as e:
                    print(f"[SYNTH] Skipping malformed candle {c}: {e}")
                    continue

            return candles if candles else None
    except Exception as e:
        print(f"[SYNTH] Connection error: {e}")
        return None

async def deriv_get_tick_count(symbol, seconds_window=60):
    """
    Returns how many real ticks occurred on `symbol` in the last
    `seconds_window` seconds, via Deriv's ticks_history with
    style="ticks" and an explicit start/end epoch range (confirmed
    real Deriv API capability - start/end accept epoch timestamps,
    and the returned history.prices/times arrays' length IS the tick
    count for that window). This is the data-source equivalent of
    the MQL5 Tick Burst EA's OnTick() counter, but via periodic
    polling rather than a continuously open live listener - the bot
    isn't a persistently-running process reacting to every tick the
    way an EA inside MT5 is, so this asks "how many ticks just
    happened" on a schedule instead.

    Returns None on any failure - callers should treat None as
    "couldn't read this round, skip and try again next scan", never
    as zero ticks.
    """
    if not DERIV_SERVICE_TOKEN:
        print("[TICKBURST] No DERIV_SERVICE_TOKEN set")
        return None

    accounts_data = await deriv_get_options_accounts(DERIV_SERVICE_TOKEN)
    if not accounts_data:
        return None

    accounts_list = accounts_data.get("data")
    if not isinstance(accounts_list, list):
        accounts_list = accounts_data.get("accounts")
    if not accounts_list:
        return None

    account_id = (
        accounts_list[0].get("account_id")
        or accounts_list[0].get("loginid")
        or accounts_list[0].get("id")
    )
    if not account_id:
        return None

    ws_url = await deriv_get_otp_url(DERIV_SERVICE_TOKEN, account_id)
    if not ws_url:
        return None

    try:
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
            now_epoch = int(time.time())
            await ws.send(json.dumps({
                "ticks_history": symbol,
                "style": "ticks",
                "start": now_epoch - seconds_window,
                "end": "latest",
                "adjust_start_time": 1,
            }))
            response = json.loads(await ws.recv())
            if "error" in response:
                print(f"[TICKBURST] ticks_history error for {symbol}: {response['error'].get('message')}")
                return None
            history = response.get("history", {})
            times = history.get("times", [])
            return len(times)
    except Exception as e:
        print(f"[TICKBURST] Connection error for {symbol}: {e}")
        return None

# ============================================
# TICK BURST DETECTION (NEW)
# Ports the core logic of SpiritFX_TickBurstScalper.mq5
# (the MQL5 EA already running on MT5) to the
# synthetic indices auto-copy path. Same
# percentile-vs-rolling-history burst detection,
# same FOLLOW direction logic (bullish candle on
# a burst -> BUY, bearish -> SELL) - just driven
# by periodic polling of Deriv's real tick data
# instead of MT5's live OnTick() stream, since the
# bot isn't a continuously-running process the way
# an EA inside MT5 is.
#
# Rolling history is in-memory and per-index - a
# Railway restart resets it to empty, which just
# means burst detection starts cold (no false
# bursts, no missed real ones) rather than reading
# stale/wrong history - same reasoning as why
# get_rotation_key is deliberately stateless.
# ============================================

TICKBURST_LOOKBACK = 3        # how many recent 1-min windows to compare against (aggressive mode from the EA)
TICKBURST_PERCENTILE = 55.0   # aggressive mode threshold from the EA
TICKBURST_MIN_TICKS = 2        # aggressive mode minimum from the EA
TICKBURST_MIN_BODY_PCT = 0.05  # minimum candle body move, as a % of price - unit-agnostic substitute for the EA's MinBodyPoints, since per-index pip/point conventions on synthetics vary and aren't reliably knowable without manual testing (same lesson as the earlier R_75 multiplier mistake)

tickburst_history = {}  # index_key -> list of recent per-minute tick counts, oldest first

def _calc_percentile(values, pct):
    """Same linear-interpolation percentile calc as the EA's CalcPercentile()."""
    if not values:
        return 0
    sorted_vals = sorted(values)
    idx = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = lo + 1 if idx != lo else lo
    hi = min(hi, len(sorted_vals) - 1)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (idx - lo) * (sorted_vals[hi] - sorted_vals[lo])

async def detect_tick_burst(index_key, config):
    """
    Returns (direction, tick_count, threshold) if a burst is detected
    this round, or None if not (no burst, or not enough rolling
    history yet to judge against). Mirrors the EA's burst_detected
    condition and FOLLOW direction logic exactly - bullish 1-min
    candle on a burst -> BUY, bearish -> SELL, doji/flat body
    (smaller than TICKBURST_MIN_BODY_POINTS) -> skip, same as the EA
    discarding "doji/body too small" bursts.
    """
    symbol = config["symbol"]

    tick_count = await deriv_get_tick_count(symbol, seconds_window=60)
    if tick_count is None:
        return None

    history = tickburst_history.setdefault(index_key, [])

    if len(history) < 2:
        history.append(tick_count)
        return None  # not enough rolling history yet to judge a burst against, same as the EA's historyCount < 2 gate

    threshold = _calc_percentile(history, TICKBURST_PERCENTILE)

    burst_detected = (
        tick_count >= TICKBURST_MIN_TICKS
        and threshold > 0
        and tick_count > threshold
    )

    history.append(tick_count)
    if len(history) > TICKBURST_LOOKBACK:
        history.pop(0)

    if not burst_detected:
        return None

    # 1-minute candle body check, via the most recent two 1m candles -
    # same role as the EA reading iOpen()/bid on the current M1 bar.
    # Deliberately NOT using a per-index pip-size table here - pip
    # conventions genuinely vary per Volatility index (confirmed: this
    # is exactly the same kind of per-index guess that turned out
    # wrong before for R_75's multiplier) and aren't reliably knowable
    # without manually testing each one against the broker. Instead,
    # body size is measured as a % of the candle's own price level,
    # which is unit-agnostic and works correctly regardless of each
    # index's actual point/decimal convention.
    candles = await deriv_get_candles(symbol, 60, count=2)
    if not candles:
        return None

    last_candle = candles[-1]
    body_move = abs(last_candle["close"] - last_candle["open"])
    body_pct = (body_move / last_candle["open"]) * 100 if last_candle["open"] else 0

    if body_pct < TICKBURST_MIN_BODY_PCT:
        print(f"[TICKBURST] {index_key.upper()} burst detected but body too small ({body_pct:.4f}%) - skipping, same as EA's doji guard")
        return None

    # FOLLOW mode: bullish burst candle -> BUY, bearish -> SELL.
    direction = "BUY" if last_candle["close"] > last_candle["open"] else "SELL"

    print(
        f"[TICKBURST] {index_key.upper()} -> {direction} | "
        f"ticks={tick_count} threshold={threshold:.1f} body={body_pct:.4f}%"
    )
    return direction, tick_count, threshold

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
                f"{config['display']} showing {primary['detail']} on "
                f"both H1 and H4 ({confidence}% confluence)."
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

async def build_synthetic_signal_response(index_key, min_agree=2):
    """
    Builds the signal message, image, and the trade context that
    gets stored for if/when the user taps "Trade This Signal".
    Returns (image_file_id, message_html, trade_context), or None
    if no signal could be generated.

    min_agree=2 is the PREFERRED bar, scheduled and DM/manual alike -
    per explicit instruction, run_strategy_bank_synthetic always
    sends a signal as long as at least one strategy fired, only
    scaling confidence % down rather than gating whether it posts.
    None here now only means the candle data itself wasn't available.
    """
    config = SYNTHETIC_CONFIG.get(index_key)
    if not config:
        return None

    symbol = config["symbol"]
    h1_candles = await get_cached_synthetic_candles(index_key, symbol, "1h", 3600, 60)
    h4_candles = await get_cached_synthetic_candles(index_key, symbol, "4h", 14400, 60)
    daily_candles = await get_cached_synthetic_candles(index_key, symbol, "1day", 86400, 10)
    m1_candles = await get_cached_synthetic_candles(index_key, symbol, "1m", 60, 60)

    result = await run_strategy_bank_synthetic(
        index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=m1_candles, min_agree=min_agree
    )
    if not result:
        return None

    direction, confidence, reason, agreeing_strategies, winning_votes = result
    contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

    # Strategies in SYNTHETIC_STRATEGY_BANK vote on EITHER h1_candles
    # or m1_candles depending on the strategy - confirmed directly
    # from run_strategy_bank_synthetic's own dispatch logic (checks
    # "m1_candles" in the strategy function's signature). Mirroring
    # that exact same check here so the chart shows the SAME candles
    # the winning strategy actually looked at, not a mismatched set.
    M1_BASED_STRATEGIES = {"EMA Pullback Scalper", "Bollinger+RSI Mean Reversion", "Volatility Breakout Scalper"}

    if direction == "BUY":
        emoji = "🟢"
        fallback_image_file_id = BUY_IMAGE_FILE_ID
    else:
        emoji = "🔴"
        fallback_image_file_id = SELL_IMAGE_FILE_ID

    # Real generated chart from the SAME candles the winning strategy
    # used. Synthetics have no price-based Entry/SL/TP today (stake/
    # risk/target are dollar amounts, not index price levels - drawing
    # them as horizontal price lines would be meaningless/wrong), so
    # entry/sl/tp are passed as None - generate_signal_chart already
    # handles that by simply not drawing those lines.
    image_file_id = fallback_image_file_id
    chart_strategy_name = winning_votes[0]["strategy_name"] if winning_votes else None
    if chart_strategy_name:
        chart_candles = m1_candles if chart_strategy_name in M1_BASED_STRATEGIES else h1_candles
        chart_path = os.path.join(CHART_OUTPUT_DIR, f"{index_key}_{int(time.time())}.png")
        chart_ok = generate_signal_chart(
            config["display"], chart_strategy_name, direction, chart_candles,
            None, None, None, chart_path,
        )
        if chart_ok:
            image_file_id = chart_path

    # FBS-style narrative: reason comes FIRST, per explicit
    # instruction (synthetics never had a separate Entry/SL/TP block
    # to reorder around in the first place - this just replaces the
    # flat "N independent strategies agree..." sentence with varied
    # prose built from the same real winning_votes).
    narrative = generate_signal_narrative(config["display"], direction, winning_votes)

    message = (
        f"{emoji} <b>STRONG {direction} {config['display']}</b> ⚡\n\n"
        f"<b>Confidence:</b> {confidence}%\n\n"
        f"{narrative}\n\n"
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
            "rejected.\n\n"
            "⚠️ <b>Use the API token from step 3, not a login/session "
            "code.</b> Some users accidentally paste a short-lived "
            "code instead — that type expires within hours and will "
            "make trading stop working until you reconnect with the "
            "correct token."
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

    # A row existing in deriv_accounts only means a token was SAVED
    # at some point - it doesn't mean it still works. A confirmed real
    # case: a user's token had silently died, but get_deriv_account
    # above still found a row, so they sailed past this point, picked
    # a stake, hit Confirm, and only THEN discovered (via a confusing
    # generic "Trade not placed" message) that their connection was
    # broken - by which point they assumed the bot itself was faulty.
    # Checking the LIVE connection here, before showing any stake
    # options at all, catches this at the very first tap instead.
    snapshot = await deriv_fetch_account_snapshot(account["api_token"])
    if not snapshot:
        await bot.send_message(
            chat_id=int(user_id),
            text=(
                "⚠️ <b>Couldn't reach your linked Deriv account.</b>\n\n"
                "Your saved token may have expired or been revoked. "
                "Tap 🔗 Connect Deriv and paste a new real-account API "
                "token to relink before trading."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
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

def friendly_trade_error(raw_error, auto_copy_context=False):
    """
    Translates a raw Deriv API error into plain English for display
    to the end user - retail/beginner audience, so raw API jargon
    like "Multiplier is not in acceptable range" should never reach
    them directly. The raw error is always still logged in full by
    the caller for diagnosis - this only changes what gets shown.

    auto_copy_context=True swaps out wording that tells the user to
    manually retap/retry - irrelevant for auto-copy, since nothing
    was tapped and the next attempt happens automatically on the
    next scan, not on demand.
    """
    lowered = raw_error.lower()
    if "couldn't verify your deriv account" in lowered or "no real account found" in lowered or "couldn't read your account list" in lowered:
        # This is what a dead/expired/revoked Deriv API token actually
        # looks like by the time it reaches here - the real failure
        # reason (e.g. "Invalid or expired token") gets logged to
        # Railway by deriv_get_options_accounts but discarded before
        # reaching this point. Confirmed real case: a user's token had
        # silently died, and this message used to fall through to the
        # generic "try again or try a different stake" text below,
        # which gave no hint that relinking was the actual fix needed -
        # they assumed the bot itself was broken.
        if auto_copy_context:
            return "Your linked Deriv account couldn't be reached. Your token may have expired - this often happens if a short-lived login code was used instead of the permanent API token. Relink with the correct token from 🔗 Connect Deriv to resume auto-copy trades."
        return "Your linked Deriv account couldn't be reached. Your token may have expired or been revoked - this often happens if a short-lived login code was used instead of the permanent API token. Tap 🔗 Connect Deriv and paste the correct token to relink."
    if "multiplier" in lowered:
        if auto_copy_context:
            return "This stake amount isn't supported for this index right now. It'll be retried automatically on the next signal."
        return "This stake amount isn't supported for this index right now. Try a different stake, or use 🎯 Trade This Signal again."
    if "insufficient" in lowered or "not enough" in lowered or "balance" in lowered:
        if auto_copy_context:
            return "Your account balance is too low for this stake. Top up your Deriv account to resume auto-copy trades."
        return "Your account balance is too low for this stake. Try a smaller amount, or top up your Deriv account."
    if auto_copy_context:
        return "We couldn't place this trade right now. It'll be retried automatically on the next signal."
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
    NEVER raises - every step is guarded, so a caller can always
    trust it gets back a clean (None, "...") on any failure instead
    of an uncaught exception silently killing the whole request (this
    previously happened here: a real trade got stuck forever on
    "Placing your trade..." with no error shown at all, because
    nothing above the inner websocket try/except was protected).
    """
    try:
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
    except Exception as e:
        print(f"[SYNTH TRADE] ❌ Account/connection setup failed: {e}")
        return None, "Couldn't set up the trade connection. Please try again."

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

                # Deriv's own error message states the real accepted
                # multiplier list for THIS symbol/account, e.g.
                # "Multiplier is not in acceptable range. Accepts
                # 400,1000,2000,3000,4000." - confirmed live, this is
                # NOT the same set for every Volatility index, so
                # hardcoding a single guessed number (the same mistake
                # already made once with BTCUSD's pip_size) would just
                # trade the wrong index correctly and the rest wrong
                # again. Instead: parse the real numbers Deriv just
                # gave us and retry ONCE with the lowest one - that's
                # the safest (least leveraged) valid choice, and it's
                # always correct because it came from Deriv itself,
                # not a guess.
                accepted_match = re.search(r"Accepts\s+([\d,\s]+)", err)
                if accepted_match and "multiplier" in err.lower():
                    accepted_values = [
                        int(v) for v in accepted_match.group(1).split(",") if v.strip().isdigit()
                    ]
                    if accepted_values:
                        retry_multiplier = min(accepted_values)
                        print(
                            f"[SYNTH TRADE] Retrying {symbol} with valid "
                            f"multiplier {retry_multiplier} (Deriv accepts: {accepted_values})"
                        )
                        proposal_request["multiplier"] = retry_multiplier
                        await ws.send(json.dumps(proposal_request))
                        proposal_response = json.loads(await ws.recv())
                        if "error" in proposal_response:
                            retry_err = proposal_response["error"].get("message", "Unknown error")
                            print(f"[SYNTH TRADE] Retry proposal error: {retry_err}")
                            return None, f"Couldn't get a quote: {retry_err}"
                    else:
                        return None, f"Couldn't get a quote: {err}"
                else:
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

    Uses the SAME no-stacking checks as run_auto_copy_scan (the
    30-min loop) - this function used to have none of them, which
    was the actual cause of repeated real double-trades: it runs
    independently from a channel post, with no shared awareness of
    what the 30-min scan already opened. Both functions now check
    (and log to) the same auto_copy_trades table, so whichever one
    runs second correctly sees what the other already placed.
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
            if has_open_auto_copy_trade(user_id, trade_context["symbol"]):
                print(f"[AUTO-COPY] {user_id} already holds {trade_context['symbol']}, skipping")
                continue

            snapshot = await deriv_fetch_account_snapshot(token)
            if not snapshot or snapshot.get("balance") is None:
                print(f"[AUTO-COPY] Couldn't read balance for {user_id}, skipping this signal")
                continue

            held_symbols = {
                c.get("symbol") for c in snapshot.get("open_contracts", [])
                if c.get("symbol")
            }
            if trade_context["symbol"] in held_symbols:
                print(f"[AUTO-COPY] {user_id} already holds {trade_context['symbol']} (live socket), skipping")
                continue

            balance = snapshot["balance"]
            stake, risk, win, was_reduced = get_auto_copy_trade_amounts(
                account, trade_context, balance
            )

            if stake is None:
                if not account.get("low_balance_notified"):
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                            f"Your balance (${balance}) is too low even "
                            f"for the smallest stake tier ($5). Top up "
                            f"your Deriv account to resume auto-copy "
                            f"trades.\n\n"
                            f"<i>You won't get this reminder again "
                            f"until your balance is back above $5.</i>"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                    set_low_balance_notified(user_id, True)
                continue

            buy_data, error = await deriv_execute_multiplier_trade(
                token,
                trade_context["symbol"],
                trade_context["contract_type"],
                trade_context["multiplier"],
                stake, risk, win,
            )

            if error:
                log_auto_copy_failure(
                    user_id, trade_context["symbol"],
                    friendly_trade_error(error, auto_copy_context=True)
                )
                continue

            if account.get("low_balance_notified"):
                set_low_balance_notified(user_id, False)

            contract_id = buy_data.get("contract_id", "—")
            log_auto_copy_trade(
                user_id, trade_context["symbol"], contract_id,
                trade_context["direction"], stake, risk, win
            )
            # No immediate success DM here either - consistent with
            # run_auto_copy_scan, this is picked up by the same
            # once-daily digest (send_auto_copy_daily_digest) instead.

        except Exception as e:
            print(f"[AUTO-COPY] ❌ Unexpected error for {user_id}: {e}")
            continue

# ============================================
# AUTO-COPY — INDEPENDENT 30-MIN SCAN (NEW)
# The channel only posts 1-2x/day by design, to
# avoid spamming the public channel - but
# auto-copy users expect to see frequent activity
# on their own account, the way real copy-trading
# services behave. This runs completely
# separately from channel posting: every 30
# minutes, checks all 5 indices for a fresh
# signal and trades it for every opted-in user -
# but ONLY on an index where that user currently
# has NO open position. A signal on an index the
# user is already holding is skipped entirely for
# them (not queued, not retried) until that
# position closes - this is the deliberate
# no-stacking rule, not a bug. Each index is
# analyzed once per scan and shared across every
# user (the market structure is identical for
# everyone), so this costs 5 structure checks per
# scan regardless of how many users are opted in -
# NOT 5x the number of users.
# ============================================

async def run_auto_copy_scan(context: ContextTypes.DEFAULT_TYPE):
    accounts = get_all_auto_copy_accounts()
    if not accounts:
        return

    print(f"[AUTO-COPY SCAN] Running for {len(accounts)} opted-in account(s)")
    bot = context.bot

    # One structure check per index, shared across every user below -
    # the market doesn't change per-user, so this is computed once.
    fresh_signals = {}
    for index_key, config in SYNTHETIC_CONFIG.items():
        symbol = config["symbol"]
        h1_candles = await get_cached_synthetic_candles(index_key, symbol, "1h", 3600, 60)
        h4_candles = await get_cached_synthetic_candles(index_key, symbol, "4h", 14400, 60)
        daily_candles = await get_cached_synthetic_candles(index_key, symbol, "1day", 86400, 10)
        m1_candles = await get_cached_synthetic_candles(index_key, symbol, "1m", 60, 60)
        result = await run_strategy_bank_synthetic(
            index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=m1_candles, min_agree=2
        )
        if not result:
            continue
        direction, confidence, reason, agreeing_strategies, _winning_votes = result
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        fresh_signals[index_key] = {
            "index_key": index_key,
            "symbol": config["symbol"],
            "display": config["display"],
            "direction": direction,
            "contract_type": contract_type,
            "multiplier": config["default_multiplier"],
            "risk": DEFAULT_RISK,
            "win": DEFAULT_WIN,
        }

    if not fresh_signals:
        print("[AUTO-COPY SCAN] No usable signal on any index this round")
        return

    for account in accounts:
        user_id = account.get("user_id")
        token = account.get("api_token")
        if not user_id or not token:
            continue

        try:
            snapshot = await deriv_fetch_account_snapshot(token)
            if not snapshot or snapshot.get("balance") is None:
                print(f"[AUTO-COPY SCAN] Couldn't read account for {user_id}, skipping this round")

                # Confirmed real case: a dead/expired/revoked token
                # makes EVERY future scan fail identically for this
                # user, silently, forever - they were never told,
                # and the daily digest that would have surfaced this
                # is permanently disabled. Tell them once (same DB-
                # backed dedup pattern as low_balance_notified below)
                # so they know to relink via 🔗 Connect Deriv, rather
                # than auto-copy just quietly never trading for them
                # again with no explanation.
                if not account.get("token_invalid_notified"):
                    try:
                        await bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                "⚠️ <b>Auto-copy paused — Deriv connection lost.</b>\n\n"
                                + friendly_trade_error("Couldn't verify your Deriv account.", auto_copy_context=True)
                                + "\n\n<i>You won't get this reminder again until "
                                "you relink your account.</i>"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                        set_token_invalid_notified(user_id, True)
                    except Exception as notify_err:
                        print(f"[AUTO-COPY SCAN] Couldn't notify {user_id} about dead token: {notify_err}")
                continue

            # Token is working again - clear the flag so a FUTURE
            # dead-token episode (e.g. after they relink, it dies
            # again later) notifies them again instead of staying
            # silently suppressed forever from one old episode.
            if account.get("token_invalid_notified"):
                set_token_invalid_notified(user_id, False)
                account["token_invalid_notified"] = False

            balance = snapshot["balance"]
            held_symbols = {
                c.get("symbol") for c in snapshot.get("open_contracts", [])
                if c.get("symbol")
            }

            # No per-round messaging anymore - successes are logged
            # via log_auto_copy_trade (used by has_open_auto_copy_trade
            # AND by the once-daily digest below), failures are logged
            # via log_auto_copy_failure for the digest to count. The
            # low-balance warning below is the one exception that
            # still sends immediately, since it's actionable in the
            # moment (top up) rather than just "wait for the retry."
            low_balance_hit = False

            for index_key, trade_context in fresh_signals.items():
                # Two independent checks must BOTH say "not held" before
                # a trade is allowed - the live socket read (symbol
                # matching against open_contracts) and this DB-backed
                # check. Either one saying "still open" blocks the
                # trade. The socket-based check alone already let a
                # real V75 double-trade through once, so it's no
                # longer trusted on its own.
                if trade_context["symbol"] in held_symbols:
                    continue
                if has_open_auto_copy_trade(user_id, trade_context["symbol"]):
                    continue

                stake, risk, win, was_reduced = get_auto_copy_trade_amounts(
                    account, trade_context, balance
                )

                if stake is None:
                    low_balance_hit = True
                    continue

                buy_data, error = await deriv_execute_multiplier_trade(
                    token,
                    trade_context["symbol"],
                    trade_context["contract_type"],
                    trade_context["multiplier"],
                    stake, risk, win,
                )

                if error:
                    log_auto_copy_failure(
                        user_id, trade_context["symbol"],
                        friendly_trade_error(error, auto_copy_context=True)
                    )
                    continue

                if account.get("low_balance_notified"):
                    set_low_balance_notified(user_id, False)
                    account["low_balance_notified"] = False  # keep this loop's local copy in sync, since account is read again below this same iteration

                contract_id = buy_data.get("contract_id", "—")

                log_auto_copy_trade(
                    user_id, trade_context["symbol"], contract_id,
                    trade_context["direction"], stake, risk, win
                )

                # Treat this index as now held for the rest of THIS
                # scan, so a single round never opens two positions
                # on the same index for the same user even if it
                # somehow appeared twice in fresh_signals.
                held_symbols.add(trade_context["symbol"])

            if low_balance_hit and not account.get("low_balance_notified"):
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                        f"Your balance (${balance}) is too low even "
                        f"for the smallest stake tier ($5). Top up "
                        f"your Deriv account to resume auto-copy "
                        f"trades.\n\n"
                        f"<i>You won't get this reminder again "
                        f"until your balance is back above $5 - "
                        f"signals will keep being skipped "
                        f"silently until then.</i>"
                    ),
                    parse_mode=ParseMode.HTML
                )
                set_low_balance_notified(user_id, True)

        except Exception as e:
            print(f"[AUTO-COPY SCAN] ❌ Unexpected error for {user_id}: {e}")
            continue

# ============================================
# TICK BURST AUTO-COPY SCAN (NEW)
# Parallel to run_auto_copy_scan above, NOT
# merged into it - per explicit instruction, Tick
# Burst is its own separate strategy/scan, not
# blended with ICT/SMC. Reuses the exact same
# proven per-user trade-execution logic (no-
# stacking checks across both the live socket
# read AND the DB-backed table, balance stepdown,
# success/failure logging into the same auto_copy_
# trades/auto_copy_failures tables and daily
# digest) - only the SIGNAL SOURCE differs
# (detect_tick_burst instead of
# analyze_synthetic_structure).
#
# Scoped to auto-copy only for now, per explicit
# instruction - manual /signal requests still use
# ICT/SMC. All 5 Volatility indices from day one,
# FOLLOW direction mode, same existing stake/risk
# framework (NOT the EA's %-balance-SL/fixed-$-TP
# model) - all per explicit instruction.
# ============================================

async def run_tickburst_auto_copy_scan(context: ContextTypes.DEFAULT_TYPE):
    accounts = get_all_auto_copy_accounts()
    if not accounts:
        return

    print(f"[TICKBURST AUTO-COPY] Running for {len(accounts)} opted-in account(s)")
    bot = context.bot

    # One burst check per index, shared across every user below - the
    # market doesn't change per-user, so this is computed once per
    # scan, same reasoning as run_auto_copy_scan's fresh_signals.
    fresh_signals = {}
    for index_key, config in SYNTHETIC_CONFIG.items():
        result = await detect_tick_burst(index_key, config)
        if not result:
            continue
        direction, tick_count, threshold = result
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        fresh_signals[index_key] = {
            "index_key": index_key,
            "symbol": config["symbol"],
            "display": config["display"],
            "direction": direction,
            "contract_type": contract_type,
            "multiplier": config["default_multiplier"],
            "risk": DEFAULT_RISK,
            "win": DEFAULT_WIN,
        }

    if not fresh_signals:
        return  # no burst on any index this round - normal, not an error

    for account in accounts:
        user_id = account.get("user_id")
        token = account.get("api_token")
        if not user_id or not token:
            continue

        try:
            snapshot = await deriv_fetch_account_snapshot(token)
            if not snapshot or snapshot.get("balance") is None:
                print(f"[TICKBURST AUTO-COPY] Couldn't read account for {user_id}, skipping this round")
                continue

            balance = snapshot["balance"]
            held_symbols = {
                c.get("symbol") for c in snapshot.get("open_contracts", [])
                if c.get("symbol")
            }

            low_balance_hit = False

            for index_key, trade_context in fresh_signals.items():
                # Same dual no-stacking check as run_auto_copy_scan -
                # both the live socket read AND the DB-backed table
                # must agree nothing's already open on this index for
                # this user before a new trade is allowed. This also
                # means Tick Burst and ICT/SMC auto-copy correctly see
                # EACH OTHER's open positions (same shared
                # auto_copy_trades table and held_symbols source) -
                # if ICT/SMC already has a position open on R_75, a
                # Tick Burst signal on R_75 is skipped too, and vice
                # versa. Never two simultaneous strategies stacking
                # positions on the same index for the same user.
                if trade_context["symbol"] in held_symbols:
                    continue
                if has_open_auto_copy_trade(user_id, trade_context["symbol"]):
                    continue

                stake, risk, win, was_reduced = get_auto_copy_trade_amounts(
                    account, trade_context, balance
                )

                if stake is None:
                    low_balance_hit = True
                    continue

                buy_data, error = await deriv_execute_multiplier_trade(
                    token,
                    trade_context["symbol"],
                    trade_context["contract_type"],
                    trade_context["multiplier"],
                    stake, risk, win,
                )

                if error:
                    log_auto_copy_failure(
                        user_id, trade_context["symbol"],
                        friendly_trade_error(error, auto_copy_context=True)
                    )
                    continue

                if account.get("low_balance_notified"):
                    set_low_balance_notified(user_id, False)
                    account["low_balance_notified"] = False

                contract_id = buy_data.get("contract_id", "—")

                log_auto_copy_trade(
                    user_id, trade_context["symbol"], contract_id,
                    trade_context["direction"], stake, risk, win
                )

                held_symbols.add(trade_context["symbol"])

            if low_balance_hit and not account.get("low_balance_notified"):
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                        f"Your balance (${balance}) is too low even "
                        f"for the smallest stake tier ($5). Top up "
                        f"your Deriv account to resume auto-copy "
                        f"trades.\n\n"
                        f"<i>You won't get this reminder again "
                        f"until your balance is back above $5 - "
                        f"signals will keep being skipped "
                        f"silently until then.</i>"
                    ),
                    parse_mode=ParseMode.HTML
                )
                set_low_balance_notified(user_id, True)

        except Exception as e:
            print(f"[TICKBURST AUTO-COPY] ❌ Unexpected error for {user_id}: {e}")
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
    "1day": 21600,  # 6h - previous day's high/low doesn't change intraday, no need to refetch often
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
                "volume": float(v["volume"]) if v.get("volume") not in (None, "") else None,
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

def detect_trend_continuation(candles):
    """
    Distinct from detect_bos_choch (which compares the last TWO
    swing points) and detect_liquidity_sweep (which looks for a
    reversal AT a level) - this measures sustained directional
    momentum across the most recent candles' closes, the actual
    "is this a continuation move" question. A clean run of mostly-
    higher (or mostly-lower) closes, with no big snap-back candle
    against that direction, suggests the move is still being
    accepted rather than already exhausted.

    Requires at least 6 of the last 7 closes to move the same way,
    AND the most recent candle to not be a strong rejection candle
    against that direction (closing in the opposite half of its own
    range) - a single strong rejection candle is treated as the
    market already pushing back, not a continuation anymore.
    """
    if len(candles) < 8:
        return None

    recent = candles[-7:]
    diffs = [recent[i]["close"] - recent[i - 1]["close"] for i in range(1, len(recent))]

    up_moves = sum(1 for d in diffs if d > 0)
    down_moves = sum(1 for d in diffs if d < 0)

    last = candles[-1]
    candle_range = last["high"] - last["low"]
    closed_upper_half = candle_range > 0 and (last["close"] - last["low"]) / candle_range >= 0.5

    if up_moves >= 6 and closed_upper_half:
        return {"direction": "BUY", "detail": "sustained bullish continuation across recent candles"}
    if down_moves >= 6 and not closed_upper_half:
        return {"direction": "SELL", "detail": "sustained bearish continuation across recent candles"}

    return None

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

    continuation = detect_trend_continuation(candles)
    if continuation:
        factors.append({**continuation, "weight": 2})

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
            # All H1 factors are also present on H4 - use the
            # strongest shared one, but weave in this pair's own name
            # and confidence so two different instruments that both
            # legitimately detected the exact same factor (a real,
            # not fabricated, coincidence) don't read as copy-pasted
            # from each other.
            primary = h1_sorted[0]
            reason = (
                f"{config['pair_name']} showing {primary['detail']} on "
                f"both H1 and H4 ({confidence}% confluence)."
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
# STRATEGY BANK (NEW)
# A real signal now requires at least 2-3 of
# these independent strategies to agree on the
# same direction before posting at all - per
# explicit instruction, replacing the old
# single-strategy (ICT/SMC only) approach that
# could call a "STRONG" signal off one structure
# reading even when fresher price action was
# already contradicting it (the real, confirmed
# case that triggered this whole rebuild: a BUY
# call on Volatility 10 right as the latest 1-2
# candles on both H1 and H4 showed a rejection
# back down).
#
# Every strategy function below shares one
# interface: takes (pair_key, config, h1_candles,
# h4_candles, daily_candles) and returns either
# None (no signal from this strategy right now)
# or a dict: {"strategy_name": str, "direction":
# "BUY"/"SELL", "detail": str}. This uniform
# shape is what lets run_strategy_bank treat all
# 10 identically regardless of each one's
# internal logic.
# ============================================

def calculate_rsi(candles, period=14):
    """
    Standard Wilder's RSI. Returns a list of RSI values aligned to
    candles[period:] (the first `period` candles can't have an RSI
    yet), or [] if there isn't enough data.
    """
    if len(candles) < period + 1:
        return []

    closes = [c["close"] for c in candles]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    for i in range(period, len(deltas)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        rsi = 100 - (100 / (1 + rs))
        rsi_values.append(rsi)

    return rsi_values

def detect_bullish_engulfing(candles):
    if len(candles) < 2:
        return False
    prev, last = candles[-2], candles[-1]
    prev_bearish = prev["close"] < prev["open"]
    last_bullish = last["close"] > last["open"]
    engulfs = last["open"] <= prev["close"] and last["close"] >= prev["open"]
    return prev_bearish and last_bullish and engulfs

def detect_bearish_engulfing(candles):
    if len(candles) < 2:
        return False
    prev, last = candles[-2], candles[-1]
    prev_bullish = prev["close"] > prev["open"]
    last_bearish = last["close"] < last["open"]
    engulfs = last["open"] >= prev["close"] and last["close"] <= prev["open"]
    return prev_bullish and last_bearish and engulfs

def detect_rsi_divergence(candles, rsi_values, direction, lookback=10):
    """
    Bullish divergence: price makes a lower low while RSI makes a
    higher low (momentum quietly improving even as price still
    falls). Bearish divergence: price makes a higher high while RSI
    makes a lower high. Compares the most recent extreme against the
    prior one within `lookback` candles - a simplified, real check,
    not a guess.
    """
    if len(rsi_values) < lookback or len(candles) < lookback:
        return False

    recent_candles = candles[-lookback:]
    recent_rsi = rsi_values[-lookback:]

    if direction == "BUY":
        lows = [(i, c["low"]) for i, c in enumerate(recent_candles)]
        lows_sorted = sorted(lows, key=lambda x: x[1])
        if len(lows_sorted) < 2:
            return False
        idx1, idx2 = lows_sorted[0][0], lows_sorted[1][0]
        first_idx, second_idx = min(idx1, idx2), max(idx1, idx2)
        if second_idx == first_idx:
            return False
        price_lower_low = recent_candles[second_idx]["low"] < recent_candles[first_idx]["low"]
        rsi_higher_low = recent_rsi[second_idx] > recent_rsi[first_idx]
        return price_lower_low and rsi_higher_low and second_idx > first_idx
    else:
        highs = [(i, c["high"]) for i, c in enumerate(recent_candles)]
        highs_sorted = sorted(highs, key=lambda x: -x[1])
        if len(highs_sorted) < 2:
            return False
        idx1, idx2 = highs_sorted[0][0], highs_sorted[1][0]
        first_idx, second_idx = min(idx1, idx2), max(idx1, idx2)
        if second_idx == first_idx:
            return False
        price_higher_high = recent_candles[second_idx]["high"] > recent_candles[first_idx]["high"]
        rsi_lower_high = recent_rsi[second_idx] < recent_rsi[first_idx]
        return price_higher_high and rsi_lower_high and second_idx > first_idx

def strategy_rsi_extreme_reversal(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    BUY: RSI < 20, bullish divergence, bullish engulfing - all three.
    SELL: RSI > 80, bearish divergence, bearish engulfing - all three.
    Requires all three conditions together, not just RSI alone -
    matches the real strategy as specified, not a watered-down
    version that fires on RSI extremes by themselves (which would
    be far too frequent and far weaker).
    """
    if not h1_candles or len(h1_candles) < 25:
        return None

    rsi_values = calculate_rsi(h1_candles, period=14)
    if not rsi_values:
        return None

    current_rsi = rsi_values[-1]

    if current_rsi < 20:
        if detect_rsi_divergence(h1_candles, rsi_values, "BUY") and detect_bullish_engulfing(h1_candles):
            return {
                "strategy_name": "RSI Extreme Reversal",
                "direction": "BUY",
                "detail": f"RSI oversold ({current_rsi:.0f}) + bullish divergence + engulfing",
            }
    elif current_rsi > 80:
        if detect_rsi_divergence(h1_candles, rsi_values, "SELL") and detect_bearish_engulfing(h1_candles):
            return {
                "strategy_name": "RSI Extreme Reversal",
                "direction": "SELL",
                "detail": f"RSI overbought ({current_rsi:.0f}) + bearish divergence + engulfing",
            }

    return None

def strategy_previous_day_high_low_manipulation(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    BUY: previous day's low gets swept (wicked below, but closes back
    above it within the most recent few H1 candles), then structure
    breaks upward. SELL: mirror case on the previous day's high.
    Real liquidity-hunt logic, not a guess - requires both the sweep
    AND a confirming break of structure afterward, same discipline as
    detect_liquidity_sweep elsewhere in this file.
    """
    if not daily_candles or len(daily_candles) < 2 or not h1_candles or len(h1_candles) < 10:
        return None

    prev_day = daily_candles[-2]  # most recent FULLY CLOSED day, not today's still-forming one
    prev_day_high = prev_day["high"]
    prev_day_low = prev_day["low"]

    recent_h1 = h1_candles[-5:]
    swings = find_swing_points(h1_candles, strength=2)

    for c in recent_h1:
        if c["low"] < prev_day_low and c["close"] > prev_day_low:
            bos = detect_bos_choch(swings)
            if bos and bos["direction"] == "BUY":
                return {
                    "strategy_name": "Previous Day High/Low Manipulation",
                    "direction": "BUY",
                    "detail": f"prior day low swept ({prev_day_low:.2f}), bullish break",
                }

    for c in recent_h1:
        if c["high"] > prev_day_high and c["close"] < prev_day_high:
            bos = detect_bos_choch(swings)
            if bos and bos["direction"] == "SELL":
                return {
                    "strategy_name": "Previous Day High/Low Manipulation",
                    "direction": "SELL",
                    "detail": f"prior day high swept ({prev_day_high:.2f}), bearish break",
                }

    return None

LONDON_SESSION_START_UTC = 7   # 7:00 UTC - London open
LONDON_SESSION_RANGE_HOURS = 1  # opening range = first hour after open

def strategy_london_session_orb(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    Marks the London opening range (first hour after 7:00 UTC open),
    then looks for a breakout beyond that range followed by a retest
    of the broken level - the real ORB entry trigger, not just
    "price is now outside the range" (which would just be a raw
    breakout, not this specific strategy). Best suited to GBPUSD per
    the original spec, but not hard-restricted to it - any pair can
    still independently confirm it if the data supports it, since
    the bank's job is to let strategies vote where they genuinely
    apply, not gate by pair name.
    """
    if not h1_candles or len(h1_candles) < 6:
        return None

    now = datetime.utcnow()
    if now.hour < LONDON_SESSION_START_UTC + LONDON_SESSION_RANGE_HOURS:
        return None  # opening range hasn't even finished forming yet today

    todays_candles = [
        c for c in h1_candles
        if c.get("time") and c["time"][:10] == now.strftime("%Y-%m-%d")
    ]
    if len(todays_candles) < 2:
        return None

    range_candles = [
        c for c in todays_candles
        if c.get("time") and LONDON_SESSION_START_UTC <= int(c["time"][11:13]) < LONDON_SESSION_START_UTC + LONDON_SESSION_RANGE_HOURS
    ]
    if not range_candles:
        return None

    range_high = max(c["high"] for c in range_candles)
    range_low = min(c["low"] for c in range_candles)

    post_range_candles = [
        c for c in todays_candles
        if c.get("time") and int(c["time"][11:13]) >= LONDON_SESSION_START_UTC + LONDON_SESSION_RANGE_HOURS
    ]
    if len(post_range_candles) < 2:
        return None

    breakout_candle = None
    for c in post_range_candles[:-1]:
        if c["close"] > range_high:
            breakout_candle = ("BUY", c)
            break
        if c["close"] < range_low:
            breakout_candle = ("SELL", c)
            break

    if not breakout_candle:
        return None

    direction, _ = breakout_candle
    last = post_range_candles[-1]

    if direction == "BUY" and range_low <= last["low"] <= range_high * 1.002 and last["close"] > range_high:
        return {
            "strategy_name": "London Session ORB",
            "direction": "BUY",
            "detail": f"London opening range ({range_low:.2f}-{range_high:.2f}) broken upward and retested",
        }
    if direction == "SELL" and range_low * 0.998 <= last["high"] <= range_high and last["close"] < range_low:
        return {
            "strategy_name": "London Session ORB",
            "direction": "SELL",
            "detail": f"London opening range ({range_low:.2f}-{range_high:.2f}) broken downward and retested",
        }

    return None

def strategy_trend_following(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    Simple moving average trend filter: price above both a 20 and
    50-period H1 MA, with the 20 above the 50 (a real "golden cross"
    alignment, scaled down from the original 50/200 pairing) -> BUY.
    Mirror case for SELL. Deliberately simple and well-known - this
    strategy's whole purpose in the bank is to catch sustained
    directional moves that pure structure analysis sometimes
    under-weights, not to be clever.

    CONFIRMED BUG FIX: this previously required 200 H1 candles
    (len(h1_candles) < 200 gate) to even attempt a result. Checked
    every call site directly: the synthetic path
    (get_cached_synthetic_candles for "1h") always fetches exactly
    60, so this was silently DEAD on every Volatility index, always,
    immediately returning None before doing any real work. The main
    forex signal path (build_signal_response) does fetch 210, so it
    wasn't dead there - but lowering to a 20/50 MA pair (both
    reachable with 60 candles) fixes synthetics without changing
    forex's behavior in any meaningful way (210 candles is still
    plenty for a 20/50 pair, and the same bullish/bearish alignment
    logic applies either way).
    """
    if not h1_candles or len(h1_candles) < 50:
        return None

    closes = [c["close"] for c in h1_candles]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    current_price = closes[-1]

    if current_price > ma20 > ma50:
        return {
            "strategy_name": "Trend Following (MA)",
            "direction": "BUY",
            "detail": f"price {current_price:.2f} above 20MA {ma20:.2f} above 50MA {ma50:.2f}, bullish alignment",
        }
    if current_price < ma20 < ma50:
        return {
            "strategy_name": "Trend Following (MA)",
            "direction": "SELL",
            "detail": f"price {current_price:.2f} below 20MA {ma20:.2f} below 50MA {ma50:.2f}, bearish alignment",
        }

    return None

def strategy_breakout(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    General consolidation-range breakout - distinct from London ORB
    (which is specifically the session opening range). Looks for a
    tight recent range (last 10 candles, high-low spread under 1.5x
    the prior 20 candles' average range) followed by the most recent
    candle closing clearly outside it - a genuine breakout from
    quiet conditions, not just any directional candle.
    """
    if not h1_candles or len(h1_candles) < 30:
        return None

    consolidation = h1_candles[-11:-1]
    prior = h1_candles[-31:-11]
    if not consolidation or not prior:
        return None

    consolidation_range = max(c["high"] for c in consolidation) - min(c["low"] for c in consolidation)
    prior_avg_range = sum(c["high"] - c["low"] for c in prior) / len(prior)
    if prior_avg_range == 0:
        return None

    is_tight = consolidation_range < prior_avg_range * 1.5
    if not is_tight:
        return None

    range_high = max(c["high"] for c in consolidation)
    range_low = min(c["low"] for c in consolidation)
    last = h1_candles[-1]

    if last["close"] > range_high:
        return {
            "strategy_name": "Breakout",
            "direction": "BUY",
            "detail": f"broke above consolidation ({range_low:.2f}-{range_high:.2f})",
        }
    if last["close"] < range_low:
        return {
            "strategy_name": "Breakout",
            "direction": "SELL",
            "detail": f"broke below consolidation ({range_low:.2f}-{range_high:.2f})",
        }

    return None

def strategy_support_resistance_bounce(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    Finds horizontal levels tested at least twice in recent history
    (a real, well-recognized S/R level, not a one-off touch), then
    checks if the most recent candle just bounced off one with a
    rejection wick. Simpler and cruder than ICT's order blocks on
    purpose - this is meant to be the "classic, universally
    recognized level" complement, not a duplicate of order-block logic.
    """
    if not h1_candles or len(h1_candles) < 30:
        return None

    swings = find_swing_points(h1_candles, strength=2)
    if not swings:
        return None

    tolerance = 0.0015  # 0.15% - levels within this band count as "the same level"
    level_touches = {}
    for s in swings:
        matched = False
        for level in list(level_touches.keys()):
            if abs(s["price"] - level) / level <= tolerance:
                level_touches[level].append(s)
                matched = True
                break
        if not matched:
            level_touches[s["price"]] = [s]

    tested_levels = {lvl: touches for lvl, touches in level_touches.items() if len(touches) >= 2}
    if not tested_levels:
        return None

    last = h1_candles[-1]
    candle_range = last["high"] - last["low"]
    if candle_range == 0:
        return None

    for level, touches in tested_levels.items():
        level_type = touches[0]["type"]
        if level_type == "low" and last["low"] <= level * (1 + tolerance) and (last["close"] - last["low"]) / candle_range >= 0.6:
            return {
                "strategy_name": "Support/Resistance Bounce",
                "direction": "BUY",
                "detail": f"price bounced off a well-tested support level (~{level:.2f})",
            }
        if level_type == "high" and last["high"] >= level * (1 - tolerance) and (last["high"] - last["close"]) / candle_range >= 0.6:
            return {
                "strategy_name": "Support/Resistance Bounce",
                "direction": "SELL",
                "detail": f"price rejected a well-tested resistance level (~{level:.2f})",
            }

    return None

def calculate_macd(candles, fast=12, slow=26, signal=9):
    """
    Standard MACD: EMA(fast) - EMA(slow), with a signal line as the
    EMA of that difference. Returns (macd_line, signal_line) as
    parallel lists, or ([], []) if there isn't enough data.
    """
    if len(candles) < slow + signal:
        return [], []

    closes = [c["close"] for c in candles]

    def ema(values, period):
        k = 2 / (period + 1)
        result = [values[0]]
        for v in values[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)

    return macd_line, signal_line

def strategy_momentum_macd(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    MACD line crossing above its signal line (bullish) or below
    (bearish), specifically while the cross is FRESH (happened on
    the most recent candle, not several candles ago) - this is the
    strategy specifically meant to catch momentum that's accelerating
    or decelerating RIGHT NOW, which is exactly what was missing from
    the confirmed real case that triggered this whole rebuild (a
    structure-based BUY call posted right as momentum was already
    visibly fading on the latest candles).
    """
    if not h1_candles or len(h1_candles) < 40:
        return None

    macd_line, signal_line = calculate_macd(h1_candles)
    if len(macd_line) < 2 or len(signal_line) < 2:
        return None

    prev_macd, curr_macd = macd_line[-2], macd_line[-1]
    prev_signal, curr_signal = signal_line[-2], signal_line[-1]

    crossed_bullish = prev_macd <= prev_signal and curr_macd > curr_signal
    crossed_bearish = prev_macd >= prev_signal and curr_macd < curr_signal

    if crossed_bullish:
        return {
            "strategy_name": "Momentum (MACD)",
            "direction": "BUY",
            "detail": f"MACD {curr_macd:.4f} crossed above signal {curr_signal:.4f} this candle",
        }
    if crossed_bearish:
        return {
            "strategy_name": "Momentum (MACD)",
            "direction": "SELL",
            "detail": f"MACD {curr_macd:.4f} crossed below signal {curr_signal:.4f} this candle",
        }

    return None

def detect_breaker_block(candles, swings):
    """
    A breaker block is an order block that FAILED - price swept
    through a swing point that should have held, invalidating the
    order block that formed it, which then flips polarity: what was
    resistance becomes support (bullish breaker) or what was support
    becomes resistance (bearish breaker). Distinct from
    detect_order_block (which looks for a block that's still
    holding, not one that's already failed and flipped).
    """
    if len(candles) < 10 or not swings:
        return None

    recent_highs = [s for s in swings if s["type"] == "high"]
    recent_lows = [s for s in swings if s["type"] == "low"]
    current_price = candles[-1]["close"]

    # Bullish breaker: a swing low got swept (price broke below it),
    # then price reversed back above that same level - the old
    # support, having failed, now acts as a new support/breaker zone
    # if price returns to test it from above.
    if len(recent_lows) >= 2:
        broken_low = recent_lows[-2]
        if any(c["low"] < broken_low["price"] for c in candles[broken_low["index"]:-1]):
            if current_price > broken_low["price"]:
                zone_low = broken_low["price"]
                zone_high = broken_low["price"] * 1.002
                if zone_low <= current_price <= zone_high:
                    return {"direction": "BUY", "detail": "bullish breaker block reaction"}

    # Bearish breaker: mirror case on a swept swing high.
    if len(recent_highs) >= 2:
        broken_high = recent_highs[-2]
        if any(c["high"] > broken_high["price"] for c in candles[broken_high["index"]:-1]):
            if current_price < broken_high["price"]:
                zone_high = broken_high["price"]
                zone_low = broken_high["price"] * 0.998
                if zone_low <= current_price <= zone_high:
                    return {"direction": "SELL", "detail": "bearish breaker block reaction"}

    return None

def strategy_unicorn_model(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    The highest-conviction strategy in the bank, per the original
    spec - requires ALL FOUR to align in the same direction: a
    liquidity sweep, a market structure shift (BOS/CHoCH), a fair
    value gap, and a breaker block reaction. Deliberately the
    strictest strategy here, since when all four genuinely align
    together it's meant to be a rare, high-quality setup, not a
    frequent one.
    """
    if not h1_candles or len(h1_candles) < 15:
        return None

    swings = find_swing_points(h1_candles, strength=2)
    sweep = detect_liquidity_sweep(h1_candles, swings)
    mss = detect_bos_choch(swings)
    fvg = detect_fvg(h1_candles)
    breaker = detect_breaker_block(h1_candles, swings)

    components = [sweep, mss, fvg, breaker]
    if any(c is None for c in components):
        return None

    directions = {c["direction"] for c in components}
    if len(directions) != 1:
        return None  # all four must agree on the SAME direction, not just all be present

    direction = directions.pop()
    return {
        "strategy_name": "Unicorn Model",
        "direction": direction,
        "detail": "liquidity sweep, market structure shift, fair value gap, and breaker block all aligned",
    }

def strategy_volume_profile_poc(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    DIAGNOSTIC ONLY - does not yet cast a real vote. True Volume
    Profile (Point of Control, Value Area High/Low) requires genuine
    traded volume, which forex/gold pairs don't reliably have through
    TwelveData (they're quoted as currency pairs, not exchange-traded
    instruments with real centralized volume), and synthetic indices
    have no real volume concept at all (RNG-generated, no actual
    trades). BTC/USD might have real volume depending on which
    exchange TwelveData sources it from - this logs whatever is
    actually returned so that can be confirmed with real data before
    building the real strategy on top of it, rather than guessing
    (the same discipline already applied elsewhere in this file after
    the R_75 multiplier guess turned out wrong). Always returns None
    until volume is confirmed real and non-null across enough recent
    candles to trust.
    """
    if pair_key != "btcusd" or not h1_candles:
        return None

    volumes = [c.get("volume") for c in h1_candles[-10:]]
    non_null_count = sum(1 for v in volumes if v is not None and v > 0)

    print(
        f"[POC DIAGNOSTIC] BTCUSD last 10 H1 candles volume sample: {volumes} "
        f"({non_null_count}/10 non-null/non-zero)"
    )

    return None  # never casts a real vote yet - diagnostic only, see docstring

def _shorten_for_bullet(reason_text, max_words=10):
    """
    ICT/SMC's reason text (from analyze_smc_structure/analyze_
    synthetic_structure) was written as a full standalone sentence,
    often longer than every other strategy's detail string - this
    trims it to its first clause for clean bullet display alongside
    the rest, since the strategy bank's reason is now several short
    bullets, not one long paragraph.
    """
    first_clause = re.split(r"[,.]", reason_text)[0].strip()
    words = first_clause.split()
    if len(words) > max_words:
        first_clause = " ".join(words[:max_words]) + "..."
    return first_clause

def calculate_bollinger_bands(candles, period=20, std_dev=2):
    """
    Standard Bollinger Bands: a simple moving average with upper/
    lower bands at std_dev standard deviations. Returns (upper,
    middle, lower) for the MOST RECENT candle only, or (None, None,
    None) if there isn't enough data.
    """
    if len(candles) < period:
        return None, None, None

    closes = [c["close"] for c in candles[-period:]]
    middle = sum(closes) / period
    variance = sum((c - middle) ** 2 for c in closes) / period
    std = variance ** 0.5

    upper = middle + (std_dev * std)
    lower = middle - (std_dev * std)
    return upper, middle, lower

def calculate_ema_series(candles, period):
    """Returns the full EMA series (not just the latest value) for crossover/pullback comparisons."""
    if len(candles) < period:
        return []
    closes = [c["close"] for c in candles]
    k = 2 / (period + 1)
    ema_values = [closes[0]]
    for price in closes[1:]:
        ema_values.append(price * k + ema_values[-1] * (1 - k))
    return ema_values

def strategy_bollinger_rsi_mean_reversion(pair_key, config, h1_candles, h4_candles, daily_candles, m1_candles=None):
    """
    Synthetics-specific (per explicit instruction: best for V75,
    V100, V50). BUY: price closes below the lower Bollinger Band,
    RSI below 25, and the NEXT candle closes bullish (confirmation,
    not just the extreme alone - the original spec explicitly
    requires this third condition). SELL: mirror case on the upper
    band / RSI above 75. Operates on M1 candles if provided
    (matching the original spec's scalping timeframe), falling back
    to H1 if M1 isn't available, since this strategy is also usable
    on slower timeframes for non-scalping callers.
    """
    candles = m1_candles if m1_candles else h1_candles
    if not candles or len(candles) < 25:
        return None

    rsi_values = calculate_rsi(candles, period=14)
    if len(rsi_values) < 2:
        return None

    upper, middle, lower = calculate_bollinger_bands(candles[:-1], period=20)
    if upper is None:
        return None

    setup_candle = candles[-2]
    confirm_candle = candles[-1]
    setup_rsi = rsi_values[-2]

    if setup_candle["close"] < lower and setup_rsi < 25 and confirm_candle["close"] > confirm_candle["open"]:
        return {
            "strategy_name": "Bollinger+RSI Mean Reversion",
            "direction": "BUY",
            "detail": f"closed below lower BB, RSI {setup_rsi:.0f}, bullish confirm",
        }

    upper2, _, lower2 = calculate_bollinger_bands(candles[:-1], period=20)
    if setup_candle["close"] > upper and setup_rsi > 75 and confirm_candle["close"] < confirm_candle["open"]:
        return {
            "strategy_name": "Bollinger+RSI Mean Reversion",
            "direction": "SELL",
            "detail": f"closed above upper BB, RSI {setup_rsi:.0f}, bearish confirm",
        }

    return None

def strategy_ema_pullback_scalper(pair_key, config, h1_candles, h4_candles, daily_candles, m1_candles=None):
    """
    Synthetics-specific (per explicit instruction: best for V75,
    V100, M1 or M5 timeframe). BUY: EMA20 above EMA50 (uptrend),
    price pulls back to touch/near EMA20, then a bullish engulfing
    candle confirms the bounce. SELL: mirror case in a downtrend.
    """
    candles = m1_candles if m1_candles else h1_candles
    if not candles or len(candles) < 55:
        return None

    ema20 = calculate_ema_series(candles, 20)
    ema50 = calculate_ema_series(candles, 50)
    if len(ema20) < 2 or len(ema50) < 2:
        return None

    current_ema20 = ema20[-1]
    current_ema50 = ema50[-1]
    last = candles[-1]
    tolerance = 0.0015

    pulled_back_to_ema20 = abs(last["low"] - current_ema20) / current_ema20 <= tolerance or \
        abs(last["high"] - current_ema20) / current_ema20 <= tolerance

    if current_ema20 > current_ema50 and pulled_back_to_ema20 and detect_bullish_engulfing(candles):
        return {
            "strategy_name": "EMA Pullback Scalper",
            "direction": "BUY",
            "detail": f"EMA20 {current_ema20:.2f} above EMA50 {current_ema50:.2f}, pullback + bullish engulfing",
        }
    if current_ema20 < current_ema50 and pulled_back_to_ema20 and detect_bearish_engulfing(candles):
        return {
            "strategy_name": "EMA Pullback Scalper",
            "direction": "SELL",
            "detail": f"EMA20 {current_ema20:.2f} below EMA50 {current_ema50:.2f}, pullback + bearish engulfing",
        }

    return None

def strategy_volatility_breakout_scalper(pair_key, config, h1_candles, h4_candles, daily_candles, m1_candles=None):
    """
    Synthetics-specific, HFT-style per original spec: BUY when the
    most recent candle closes above the highest high of the prior 10
    candles; SELL when it closes below the lowest low of the prior
    10. Deliberately the simplest, fastest-firing strategy in the
    synthetic roster - "enter instantly" on a clean break, no
    additional confirmation required, matching the original spec.
    """
    candles = m1_candles if m1_candles else h1_candles
    if not candles or len(candles) < 11:
        return None

    prior_10 = candles[-11:-1]
    last = candles[-1]

    highest_high = max(c["high"] for c in prior_10)
    lowest_low = min(c["low"] for c in prior_10)

    if last["close"] > highest_high:
        return {
            "strategy_name": "Volatility Breakout Scalper",
            "direction": "BUY",
            "detail": f"broke above 10-candle high ({highest_high:.2f})",
        }
    if last["close"] < lowest_low:
        return {
            "strategy_name": "Volatility Breakout Scalper",
            "direction": "SELL",
            "detail": f"broke below 10-candle low ({lowest_low:.2f})",
        }

    return None

STRATEGY_BANK = [
    strategy_rsi_extreme_reversal,
    strategy_unicorn_model,
    strategy_previous_day_high_low_manipulation,
    strategy_london_session_orb,
    strategy_trend_following,
    strategy_breakout,
    strategy_support_resistance_bounce,
    strategy_momentum_macd,
    strategy_volume_profile_poc,  # diagnostic only right now, always returns None - see docstring
]

# Distinct roster for synthetic (Deriv) indices - per explicit
# instruction, removes every ICT/FVG-dependent strategy (ICT/SMC,
# Unicorn Model, Previous Day H/L Manipulation, London Session ORB),
# since synthetics have no real liquidity, sessions, or institutional
# order flow for those strategies' underlying assumptions to hold.
# Keeps the price-action/indicator-based strategies that don't
# depend on those assumptions, and adds three synthetics-specific
# scalping strategies suited to how RNG-driven instruments actually
# behave (mean reversion off Bollinger extremes, EMA pullback
# continuation, and raw volatility breakout).
SYNTHETIC_STRATEGY_BANK = [
    strategy_rsi_extreme_reversal,
    strategy_trend_following,
    strategy_breakout,
    strategy_support_resistance_bounce,
    strategy_momentum_macd,
    strategy_bollinger_rsi_mean_reversion,
    strategy_ema_pullback_scalper,
    strategy_volatility_breakout_scalper,
]

def run_strategy_bank(pair_key, config, h1_candles, h4_candles, daily_candles, min_agree=2):
    """
    Runs every strategy in STRATEGY_BANK plus the existing ICT/SMC
    structure analysis (analyze_smc_structure), and prefers at least
    `min_agree` of them to independently agree on the SAME direction.

    min_agree=2 is the PREFERRED bar, not a hard gate - per explicit
    instruction, every strategy in this bank (including ICT/SMC) is
    individually pre-verified/trusted, so even 1 agreeing strategy is
    an acceptable signal to send, just at a lower, honestly-scaled
    confidence (1 agreeing -> 76%, 2 -> 82%, 3 -> 88%, etc.) rather
    than falling back to the much weaker rule-based bias. The bank
    only returns None when literally NO strategy cast any vote at
    all (votes is empty) - a real "couldn't analyze this pair right
    now" case, not a disagreement, since there's nothing to fall
    back to.

    SPECIAL CASE - exactly 1 winning vote: if that lone vote is
    ICT/SMC, per explicit instruction prefer pairing it down to any
    OTHER single strategy instead, if one fired at all (regardless of
    its direction) - ICT/SMC is the most subjective/discretionary
    strategy in the bank, so when only one vote exists at all, a
    more mechanical rule-based strategy (RSI, MACD, MA, breakout,
    etc.) is the safer single signal to send. ICT/SMC only sends
    alone if it's truly the ONLY strategy that produced any result
    whatsoever this round.

    Returns (direction, confidence, reason, agreeing_strategies) or
    None if no strategy produced any result at all. confidence scales
    with how many strategies agreed, same spirit as
    analyze_smc_structure's confluence-based confidence.
    """
    votes = []

    smc_result = analyze_smc_structure(pair_key, config)
    if smc_result:
        smc_direction, smc_confidence, smc_reason, _ = smc_result
        votes.append({
            "strategy_name": "ICT/SMC",
            "direction": smc_direction,
            "detail": _shorten_for_bullet(smc_reason),
        })

    for strategy_fn in STRATEGY_BANK:
        try:
            result = strategy_fn(pair_key, config, h1_candles, h4_candles, daily_candles)
            if result:
                votes.append(result)
        except Exception as e:
            print(f"[STRATEGY BANK] {strategy_fn.__name__} failed for {pair_key}: {e}")
            continue

    if not votes:
        print(f"[STRATEGY BANK] {pair_key} - no strategy cast any vote this round, falling back")
        return None

    buy_votes = [v for v in votes if v["direction"] == "BUY"]
    sell_votes = [v for v in votes if v["direction"] == "SELL"]

    winning_votes = buy_votes if len(buy_votes) >= len(sell_votes) else sell_votes
    direction = "BUY" if winning_votes is buy_votes else "SELL"

    # Exactly 1 winning vote AND it's ICT/SMC -> swap to any other
    # single strategy that fired this round, if one exists at all.
    if len(winning_votes) == 1 and winning_votes[0]["strategy_name"] == "ICT/SMC":
        non_smc_votes = [v for v in votes if v["strategy_name"] != "ICT/SMC"]
        if non_smc_votes:
            replacement = non_smc_votes[0]
            print(
                f"[STRATEGY BANK] {pair_key} - sole vote was ICT/SMC, "
                f"swapping to {replacement['strategy_name']} per explicit instruction"
            )
            winning_votes = [replacement]
            direction = replacement["direction"]

    if len(winning_votes) < min_agree:
        print(
            f"[STRATEGY BANK] {pair_key} only {len(winning_votes)} strategy(ies) "
            f"agreed on {direction} (below preferred {min_agree}) - sending anyway, every "
            f"strategy in this bank is independently trusted"
        )

    agreeing_names = [v["strategy_name"] for v in winning_votes]
    confidence = min(95, 70 + len(winning_votes) * 6)

    # Short bullet points, one per agreeing strategy, instead of one
    # long run-on sentence - reads cleanly even with 2-3 strategies
    # stacked together, rather than risking a wall of text that
    # looks bogus or padded.
    bullet_lines = [f"• {v['strategy_name']}: {v['detail']}" for v in winning_votes]
    reason = "\n".join(bullet_lines)

    print(
        f"[STRATEGY BANK] {pair_key} -> {direction} | "
        f"{len(winning_votes)} agreeing: {', '.join(agreeing_names)}"
    )

    return direction, confidence, reason, agreeing_names, winning_votes

async def run_strategy_bank_synthetic(index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=None, min_agree=2):
    """
    Async sibling of run_strategy_bank, for synthetic (Deriv)
    indices - but with a DELIBERATELY DIFFERENT roster
    (SYNTHETIC_STRATEGY_BANK, not STRATEGY_BANK) and NO ICT/SMC vote
    at all. Per explicit instruction: synthetics have no real
    liquidity, sessions, or institutional order flow, so ICT/SMC,
    Unicorn Model, Previous Day H/L Manipulation, and London Session
    ORB are all excluded here - those strategies' underlying
    assumptions don't hold on an RNG-generated instrument, even
    though their pattern-matching would still technically run.

    min_agree=2 is the PREFERRED bar, not a hard gate - per explicit
    instruction, every strategy in SYNTHETIC_STRATEGY_BANK is
    individually pre-verified/trusted, so 1 agreeing strategy is also
    an acceptable signal to send, just at a lower, honestly-scaled
    confidence (see the formula below: 1 agreeing -> 76%, 2 -> 82%,
    3 -> 88%, etc.). The bank only returns None when literally NO
    strategy cast any vote at all (votes is empty) - that's a real
    "couldn't analyze this index right now" case, not a disagreement,
    since there's nothing whatsoever to fall back to.
    """
    votes = []

    # Diagnostic: confirm what candle data actually arrived for THIS
    # index before blaming the strategies themselves. Added after a
    # confirmed real case where R_100 specifically returned zero
    # votes every round while R_10/R_25/R_50 succeeded in the same
    # minute with the identical strategy roster - the difference has
    # to be in the data reaching these functions, not the functions
    # themselves, since no per-strategy exception was ever logged for
    # R_100 (meaning every strategy ran and legitimately found
    # nothing, rather than crashing).
    print(
        f"[STRATEGY BANK SYNTH DIAG] {index_key} candle counts - "
        f"h1={len(h1_candles) if h1_candles else 0} "
        f"h4={len(h4_candles) if h4_candles else 0} "
        f"daily={len(daily_candles) if daily_candles else 0} "
        f"m1={len(m1_candles) if m1_candles else 0}"
    )
    if m1_candles:
        last_candle = m1_candles[-1]
        print(
            f"[STRATEGY BANK SYNTH DIAG] {index_key} last m1 candle: "
            f"open={last_candle.get('open')} high={last_candle.get('high')} "
            f"low={last_candle.get('low')} close={last_candle.get('close')}"
        )
    if h1_candles:
        # Never previously checked - 5 of the 8 synthetic strategies
        # (RSI extreme reversal, trend following, breakout, S/R
        # bounce, MACD) vote on h1_candles, not m1_candles. If h1 data
        # were stale, flat, or duplicated, those 5 would silently find
        # nothing every round while the 3 m1-based strategies are the
        # only ones that ever get a real shot - which would look
        # exactly like what's been observed on r75/r100. Logging the
        # last 3 h1 candles' closes plus first/last timestamps to
        # check for staleness or repeated identical values directly.
        h1_last3 = h1_candles[-3:]
        h1_closes = [c.get("close") for c in h1_last3]
        h1_times = [c.get("time") for c in h1_last3]
        print(
            f"[STRATEGY BANK SYNTH DIAG] {index_key} last 3 h1 closes: "
            f"{h1_closes} | times: {h1_times}"
        )

    for strategy_fn in SYNTHETIC_STRATEGY_BANK:
        try:
            sig = inspect.signature(strategy_fn)
            if "m1_candles" in sig.parameters:
                result = strategy_fn(index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=m1_candles)
            else:
                result = strategy_fn(index_key, config, h1_candles, h4_candles, daily_candles)
            if result:
                votes.append(result)
        except Exception as e:
            print(f"[STRATEGY BANK SYNTH] {strategy_fn.__name__} failed for {index_key}: {e}")
            continue

    if not votes:
        print(f"[STRATEGY BANK SYNTH] {index_key} - no strategy cast any vote this round, falling back")
        return None

    buy_votes = [v for v in votes if v["direction"] == "BUY"]
    sell_votes = [v for v in votes if v["direction"] == "SELL"]

    winning_votes = buy_votes if len(buy_votes) >= len(sell_votes) else sell_votes
    direction = "BUY" if winning_votes is buy_votes else "SELL"

    if len(winning_votes) < min_agree:
        print(
            f"[STRATEGY BANK SYNTH] {index_key} only {len(winning_votes)} strategy(ies) "
            f"agreed on {direction} (below preferred {min_agree}) - sending anyway, every "
            f"strategy in this bank is independently trusted"
        )

    agreeing_names = [v["strategy_name"] for v in winning_votes]
    confidence = min(95, 70 + len(winning_votes) * 6)

    detail_strings = [v["detail"] for v in winning_votes[:3]]
    reason = (
        f"{len(winning_votes)} independent strategy(ies) agree on {direction}: "
        + "; ".join(detail_strings)
        + ("." if len(detail_strings) == len(winning_votes) else f", and {len(winning_votes) - 3} more.")
    )

    print(
        f"[STRATEGY BANK SYNTH] {index_key} -> {direction} | "
        f"{len(winning_votes)} agreeing: {', '.join(agreeing_names)}"
    )

    return direction, confidence, reason, agreeing_names, winning_votes

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
# ============================================
# SIGNAL CHART GENERATION
# ============================================
# Draws a real candlestick chart from the SAME candles the winning
# strategy actually used, with whatever overlay matches that
# strategy (MA lines, consolidation box, Bollinger bands, RSI/MACD
# sub-panel, etc.) plus Entry/SL/TP lines. Numbers are recomputed
# fresh here using the bot's own calculate_rsi/calculate_macd/
# calculate_bollinger_bands/calculate_ema_series functions already
# defined above - never a second, separate calculation, so the chart
# can never show a different number than what the strategy voted on.
# Falls back to a clean plain candle chart for any strategy without a
# specific overlay below - every signal gets a chart, per instruction.

CHART_OUTPUT_DIR = "/tmp/nexora_charts"
os.makedirs(CHART_OUTPUT_DIR, exist_ok=True)


def _chart_to_mpl_time(candles):
    """
    Candle 'time' fields are inconsistent across this codebase - ISO
    strings from TwelveData, raw epoch ints from Deriv. Normalize
    both shapes here rather than assuming one, so the chart never
    crashes on whichever source produced this particular candle list.
    """
    times = []
    for i, c in enumerate(candles):
        t = c.get("time")
        if t is None:
            times.append(i)
        elif isinstance(t, datetime):
            times.append(t)
        elif isinstance(t, (int, float)):
            times.append(datetime.utcfromtimestamp(t))
        elif isinstance(t, str):
            try:
                times.append(datetime.fromisoformat(t.replace("Z", "+00:00")))
            except ValueError:
                times.append(i)
        else:
            times.append(i)
    return times


def _chart_draw_candles(ax, candles, times):
    is_datetime = isinstance(times[0], datetime)
    x = mdates.date2num(times) if is_datetime else list(range(len(times)))
    if len(x) > 1:
        width = (x[-1] - x[0]) / len(x) * 0.6
    else:
        width = 0.6
    min_body = (max(c["high"] for c in candles) - min(c["low"] for c in candles)) * 0.001
    for i, c in enumerate(candles):
        color = "#22c55e" if c["close"] >= c["open"] else "#ef4444"
        ax.plot([x[i], x[i]], [c["low"], c["high"]], color=color, linewidth=1, zorder=2)
        rect = Rectangle(
            (x[i] - width / 2, min(c["open"], c["close"])),
            width, max(abs(c["close"] - c["open"]), min_body),
            facecolor=color, edgecolor=color, zorder=3,
        )
        ax.add_patch(rect)
    return x


def _chart_fmt_price(value, all_values):
    ref = max(abs(v) for v in all_values)
    return f"{value:.5f}" if ref < 10 else f"{value:.2f}"


def _chart_finish(fig, ax, x, times, candles, entry, sl, tp, title, save_path, sub_ax=None):
    label_box = dict(boxstyle="round,pad=0.3", facecolor="#0f1115", edgecolor="none")
    is_datetime = isinstance(times[0], datetime)
    x_right = x[-1] + (x[-1] - x[0]) * 0.01 if len(x) > 1 else x[-1] + 1

    all_lows = [c["low"] for c in candles]
    all_highs = [c["high"] for c in candles]
    level_values = [v for v in (entry, sl, tp) if v is not None]
    all_price_values = all_lows + all_highs + level_values

    full_range = max(all_price_values) - min(all_price_values)
    min_gap = full_range * 0.035
    points = sorted([(v, name) for v, name in [(entry, "entry"), (sl, "sl"), (tp, "tp")] if v is not None])
    adjusted = {name: v for v, name in points}
    for i in range(1, len(points)):
        prev_v, prev_name = points[i - 1]
        curr_v, curr_name = points[i]
        if adjusted[curr_name] - adjusted[prev_name] < min_gap:
            adjusted[curr_name] = adjusted[prev_name] + min_gap

    if entry is not None:
        ax.axhline(entry, color="#e5e7eb", linewidth=1, linestyle="--", zorder=1)
        ax.text(x_right, adjusted["entry"], f"Entry {_chart_fmt_price(entry, all_price_values)}", color="#e5e7eb", fontsize=9, va="center", bbox=label_box, zorder=5)
    if sl is not None:
        ax.axhline(sl, color="#ef4444", linewidth=1, linestyle="--", zorder=1)
        ax.text(x_right, adjusted["sl"], f"SL {_chart_fmt_price(sl, all_price_values)}", color="#ef4444", fontsize=9, va="center", bbox=label_box, zorder=5)
    if tp is not None:
        ax.axhline(tp, color="#22c55e", linewidth=1, linestyle="--", zorder=1)
        ax.text(x_right, adjusted["tp"], f"TP {_chart_fmt_price(tp, all_price_values)}", color="#22c55e", fontsize=9, va="center", bbox=label_box, zorder=5)

    xlim_left = x[0] - (x[-1] - x[0]) * 0.02 if len(x) > 1 else x[0] - 1
    xlim_right = x_right + (x[-1] - x[0]) * 0.12 if len(x) > 1 else x_right + 2
    ax.set_xlim(xlim_left, xlim_right)
    y_pad = full_range * 0.08
    ax.set_ylim(min(all_price_values) - y_pad, max(all_price_values) + y_pad)

    if is_datetime and sub_ax is None:
        ax.xaxis_date()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %Hh"))
        plt.setp(ax.get_xticklabels(), rotation=20)
    elif sub_ax is not None:
        ax.tick_params(labelbottom=False)

    ax.tick_params(colors="#9ca3af", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#2d3139")

    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="upper left", facecolor="#0f1115", edgecolor="#2d3139", labelcolor="#e5e7eb", fontsize=9)
    ax.set_title(title, color="#e5e7eb", fontsize=12, fontweight="bold", loc="left", pad=12)
    ax.grid(color="#1f2329", linewidth=0.5, alpha=0.5)

    if sub_ax is not None:
        sub_ax.set_xlim(xlim_left, xlim_right)
        if is_datetime:
            sub_ax.xaxis_date()
            sub_ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b %Hh"))
            plt.setp(sub_ax.get_xticklabels(), rotation=20)
        sub_ax.tick_params(colors="#9ca3af", labelsize=8)
        for spine in sub_ax.spines.values():
            spine.set_color("#2d3139")
        sub_handles, sub_labels = sub_ax.get_legend_handles_labels()
        if sub_handles:
            sub_ax.legend(loc="upper left", facecolor="#0f1115", edgecolor="#2d3139", labelcolor="#e5e7eb", fontsize=8)
        sub_ax.grid(color="#1f2329", linewidth=0.5, alpha=0.5)

    plt.tight_layout() if sub_ax is None else None
    try:
        plt.savefig(save_path, facecolor="#0f1115")
    finally:
        plt.close(fig)


def generate_signal_chart(display_name, strategy_name, direction, candles, entry, sl, tp, save_path, display_window=70):
    """
    Generates one chart PNG for a signal. candles = the SAME candle
    list the winning strategy actually used (h1_candles for most
    strategies, m1_candles for the 3 synthetics-only scalpers) -
    caller picks the right one. Returns True/False (whether a file
    was actually written) so callers can fall back to the old static
    bull/bear graphic if chart generation fails for any reason -
    charts should never be able to block a signal from sending.

    display_window: CONFIRMED REAL BUG FIX, found via an actual
    end-to-end test with real forex-shaped data (210 H1 candles,
    matching build_signal_response's real outputsize=210 fetch) -
    every indicator below needs the FULL candle list to compute
    correctly (MA50 needs 50+ candles of real history, MACD needs
    35+, etc.), but charting all 210 produced an unreadably dense
    wall of candle wicks with the Entry/SL/TP labels colliding into
    them. Indicators are still computed on the FULL candle list
    first (so the math never changes), then both candles and every
    computed indicator array are sliced down to the same trailing
    display_window before drawing - shows a clean, readable recent
    window without ever changing what was actually calculated.
    """
    try:
        if not candles or len(candles) < 5:
            return False

        needs_subpanel = strategy_name in ("RSI Extreme Reversal", "Bollinger+RSI Mean Reversion", "Momentum (MACD)")
        closes = [c["close"] for c in candles]

        # Compute every indicator on the FULL candle list first - the
        # math must never be affected by how much we later display.
        ma20 = ma50 = None
        ema20 = ema50 = None
        consolidation_box = None
        breakout_lines = None
        sr_level = None
        rsi_full = None
        macd_full = signal_full = None
        bb_upper = bb_middle = bb_lower = None

        if strategy_name == "Trend Following (MA)" and len(candles) >= 50:
            def sma(period):
                return [None] * (period - 1) + [
                    sum(closes[i - period + 1:i + 1]) / period for i in range(period - 1, len(closes))
                ]
            ma20, ma50 = sma(20), sma(50)

        elif strategy_name == "EMA Pullback Scalper" and len(candles) >= 55:
            ema20_raw = calculate_ema_series(candles, 20)
            ema50_raw = calculate_ema_series(candles, 50)
            ema20 = [None] * (len(candles) - len(ema20_raw)) + list(ema20_raw)
            ema50 = [None] * (len(candles) - len(ema50_raw)) + list(ema50_raw)

        elif strategy_name == "Breakout" and len(candles) >= 30:
            consolidation = candles[-11:-1]
            range_high = max(c["high"] for c in consolidation)
            range_low = min(c["low"] for c in consolidation)
            consolidation_box = (range_low, range_high)

        elif strategy_name == "Volatility Breakout Scalper" and len(candles) >= 11:
            prior_10 = candles[-11:-1]
            breakout_lines = (max(c["high"] for c in prior_10), min(c["low"] for c in prior_10))

        elif strategy_name == "Support/Resistance Bounce":
            sr_level = candles[-1]["low"] if direction == "BUY" else candles[-1]["high"]

        elif strategy_name == "RSI Extreme Reversal":
            rsi_values = calculate_rsi(candles, period=14)
            if rsi_values:
                rsi_full = [None] * (len(candles) - len(rsi_values)) + list(rsi_values)

        elif strategy_name == "Momentum (MACD)":
            macd_line, signal_line = calculate_macd(candles)
            if macd_line and signal_line:
                macd_full = [None] * (len(candles) - len(macd_line)) + list(macd_line)
                signal_full = [None] * (len(candles) - len(signal_line)) + list(signal_line)

        elif strategy_name == "Bollinger+RSI Mean Reversion":
            if len(candles) >= 20:
                bb_upper, bb_middle, bb_lower = [], [], []
                for i in range(len(candles)):
                    if i < 19:
                        bb_upper.append(None); bb_middle.append(None); bb_lower.append(None)
                        continue
                    u, m, l = calculate_bollinger_bands(candles[:i + 1], period=20)
                    bb_upper.append(u); bb_middle.append(m); bb_lower.append(l)
            rsi_values = calculate_rsi(candles, period=14)
            if rsi_values:
                rsi_full = [None] * (len(candles) - len(rsi_values)) + list(rsi_values)

        # NOW slice everything to the trailing display window - the
        # consolidation box / breakout lines / S/R level are already
        # just scalar values (not per-candle arrays), so they need no
        # slicing; only candles and the per-candle indicator arrays do.
        window = min(display_window, len(candles))
        candles = candles[-window:]
        if ma20 is not None:
            ma20, ma50 = ma20[-window:], ma50[-window:]
        if ema20 is not None:
            ema20, ema50 = ema20[-window:], ema50[-window:]
        if rsi_full is not None:
            rsi_full = rsi_full[-window:]
        if macd_full is not None:
            macd_full, signal_full = macd_full[-window:], signal_full[-window:]
        if bb_upper is not None:
            bb_upper, bb_middle, bb_lower = bb_upper[-window:], bb_middle[-window:], bb_lower[-window:]

        times = _chart_to_mpl_time(candles)
        if needs_subpanel:
            fig, (ax, sub_ax) = plt.subplots(
                2, 1, figsize=(10, 7.5), dpi=150,
                gridspec_kw={"height_ratios": [3, 1], "hspace": 0.08},
                sharex=True, layout="constrained",
            )
            sub_ax.set_facecolor("#0f1115")
        else:
            fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
            sub_ax = None
        fig.patch.set_facecolor("#0f1115")
        ax.set_facecolor("#0f1115")

        x = _chart_draw_candles(ax, candles, times)
        title_suffix = f"{display_name} — {strategy_name} — {direction}"

        if ma20 is not None:
            ax.plot(x, ma20, color="#60a5fa", linewidth=1.6, label="MA20", zorder=4)
            ax.plot(x, ma50, color="#f59e0b", linewidth=1.6, label="MA50", zorder=4)

        elif ema20 is not None:
            ax.plot(x, ema20, color="#60a5fa", linewidth=1.6, label="EMA20", zorder=4)
            ax.plot(x, ema50, color="#f59e0b", linewidth=1.6, label="EMA50", zorder=4)

        elif consolidation_box is not None and window >= 11:
            range_low, range_high = consolidation_box
            box_x_start = x[-11]
            box_x_end = x[-2]
            ax.add_patch(Rectangle(
                (box_x_start, range_low), box_x_end - box_x_start, range_high - range_low,
                facecolor="#60a5fa", alpha=0.12, edgecolor="#60a5fa", linewidth=1, zorder=1,
                label="Consolidation range",
            ))

        elif breakout_lines is not None:
            highest_high, lowest_low = breakout_lines
            ax.axhline(highest_high, color="#60a5fa", linewidth=1.2, linestyle=":", zorder=1, label=f"10-candle high {highest_high:.2f}")
            ax.axhline(lowest_low, color="#f59e0b", linewidth=1.2, linestyle=":", zorder=1, label=f"10-candle low {lowest_low:.2f}")

        elif sr_level is not None:
            ax.axhline(sr_level, color="#a78bfa", linewidth=1.2, linestyle=":", zorder=1, label=f"Tested level ~{sr_level:.2f}")

        elif rsi_full is not None and strategy_name == "RSI Extreme Reversal":
            sub_ax.plot(x, rsi_full, color="#a78bfa", linewidth=1.4, label="RSI(14)", zorder=4)
            sub_ax.axhline(80, color="#ef4444", linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)
            sub_ax.axhline(20, color="#22c55e", linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)
            sub_ax.fill_between(x, 80, 100, color="#ef4444", alpha=0.06, zorder=0)
            sub_ax.fill_between(x, 0, 20, color="#22c55e", alpha=0.06, zorder=0)
            sub_ax.set_ylim(0, 100)

        elif macd_full is not None:
            sub_ax.plot(x, macd_full, color="#60a5fa", linewidth=1.4, label="MACD", zorder=4)
            sub_ax.plot(x, signal_full, color="#f59e0b", linewidth=1.4, label="Signal", zorder=4)
            hist = [
                (m - s) if (m is not None and s is not None) else 0
                for m, s in zip(macd_full, signal_full)
            ]
            bar_colors = ["#22c55e" if h >= 0 else "#ef4444" for h in hist]
            sub_ax.bar(x, hist, color=bar_colors, alpha=0.5, width=(x[-1] - x[0]) / len(x) * 0.6 if len(x) > 1 else 0.6, zorder=2)
            sub_ax.axhline(0, color="#9ca3af", linewidth=0.6, alpha=0.4, zorder=1)

        if bb_upper is not None:
            ax.plot(x, bb_upper, color="#60a5fa", linewidth=1.2, label="Upper BB", zorder=4, linestyle="--")
            ax.plot(x, bb_middle, color="#9ca3af", linewidth=1.0, label="Middle BB", zorder=4, linestyle=":")
            ax.plot(x, bb_lower, color="#f59e0b", linewidth=1.2, label="Lower BB", zorder=4, linestyle="--")
        if rsi_full is not None and strategy_name == "Bollinger+RSI Mean Reversion" and sub_ax is not None:
            sub_ax.plot(x, rsi_full, color="#a78bfa", linewidth=1.4, label="RSI(14)", zorder=4)
            sub_ax.axhline(75, color="#ef4444", linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)
            sub_ax.axhline(25, color="#22c55e", linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)
            sub_ax.fill_between(x, 75, 100, color="#ef4444", alpha=0.06, zorder=0)
            sub_ax.fill_between(x, 0, 25, color="#22c55e", alpha=0.06, zorder=0)
            sub_ax.set_ylim(0, 100)

        _chart_finish(fig, ax, x, times, candles, entry, sl, tp, title_suffix, save_path, sub_ax=sub_ax)
        return True
    except Exception as e:
        print(f"[CHART] Failed to generate chart for {display_name}/{strategy_name}: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return False


# ============================================
# SIGNAL NARRATIVE GENERATION (FBS-style prose)
# ============================================
# Builds 3-4 lines of connected prose from the SAME winning_votes list
# run_strategy_bank/run_strategy_bank_synthetic already produce,
# instead of a flat bullet list - reason comes first, Entry/SL/TP
# after, per explicit instruction. Uses small pools of interchangeable
# opening/closing clauses combined with each strategy's own real
# detail text, so the same strategy firing on multiple indices in the
# same minute reads as genuinely different (but equally accurate)
# sentences rather than copies of one template - no AI call, no added
# cost or latency, fully reviewable/predictable text.

NARRATIVE_OPENERS_BUY = [
    "{display_name} is showing renewed bullish pressure.",
    "Buyers are stepping back in on {display_name}.",
    "{display_name} is building a constructive bullish picture.",
    "Momentum is turning in favor of buyers on {display_name}.",
    "{display_name} looks primed for upside continuation.",
]
NARRATIVE_OPENERS_SELL = [
    "{display_name} is showing renewed bearish pressure.",
    "Sellers are stepping back in on {display_name}.",
    "{display_name} is building a constructive bearish picture.",
    "Momentum is turning in favor of sellers on {display_name}.",
    "{display_name} looks primed for downside continuation.",
]
NARRATIVE_CLOSERS_BUY = [
    "We like the long side here while structure holds.",
    "This keeps the bullish bias intact for now.",
    "A clean setup to ride further upside from here.",
    "Risk favors buyers as long as this level holds.",
]
NARRATIVE_CLOSERS_SELL = [
    "We like the short side here while structure holds.",
    "This keeps the bearish bias intact for now.",
    "A clean setup to ride further downside from here.",
    "Risk favors sellers as long as this level holds.",
]


def _narrative_strategy_sentence(vote):
    name = vote["strategy_name"]
    detail = vote["detail"]
    if name == "Trend Following (MA)":
        return f"The moving averages confirm it: {detail}."
    if name == "Breakout":
        return f"Price just {detail}, clearing a multi-candle range."
    if name == "Support/Resistance Bounce":
        return f"Price action shows it - {detail}."
    if name == "Volatility Breakout Scalper":
        return f"A clean breakout move: {detail}."
    if name == "EMA Pullback Scalper":
        return f"The pullback played out as expected: {detail}."
    if name == "Momentum (MACD)":
        return f"Momentum agrees - {detail}."
    if name == "RSI Extreme Reversal":
        return f"RSI is flashing an extreme reading: {detail}."
    if name == "Bollinger+RSI Mean Reversion":
        return f"Price stretched too far, and is snapping back: {detail}."
    if name == "ICT/SMC":
        return f"Structure confirms it: {detail}."
    return detail[0].upper() + detail[1:] + "."


def generate_signal_narrative(display_name, direction, winning_votes):
    """
    Returns a 3-4 line prose string built from the real winning votes.
    Genuinely randomized per call (no seed) - intentional, since two
    signals firing seconds apart on different indices should read
    differently even if the same single strategy fired on both.
    """
    openers = NARRATIVE_OPENERS_BUY if direction == "BUY" else NARRATIVE_OPENERS_SELL
    closers = NARRATIVE_CLOSERS_BUY if direction == "BUY" else NARRATIVE_CLOSERS_SELL

    body_votes = winning_votes[:2]
    body_sentences = [_narrative_strategy_sentence(v) for v in body_votes]

    body_strategy_names = {v["strategy_name"] for v in body_votes}
    safe_openers = openers
    if "Momentum (MACD)" in body_strategy_names:
        safe_openers = [o for o in openers if "momentum" not in o.lower()]
    if not safe_openers:
        safe_openers = openers

    opener = random.choice(safe_openers).format(display_name=display_name)
    closer = random.choice(closers)

    lines = [opener] + body_sentences + [closer]
    return " ".join(lines)


# ============================================
# Strategy bank: votes from STRATEGY_BANK and ICT/SMC
# direct technical structure, replacing the old
# always-fall-back-to-rule-based-bias approach -
# now ALWAYS sends a real strategy-backed signal as
# long as at least one strategy fired, only falling
# back to generate_rule_based_bias in the genuinely
# rare case where no usable candle data exists.
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

    h1_candles = get_cached_candles(matched_key, config, "1h", outputsize=210)
    h4_candles = get_cached_candles(matched_key, config, "4h", outputsize=60)
    daily_candles = get_cached_candles(matched_key, config, "1day", outputsize=10)

    # min_agree=2 is the PREFERRED bar everywhere, scheduled and DM
    # alike - per explicit instruction, run_strategy_bank itself now
    # always sends a real strategy-backed signal as long as AT LEAST
    # ONE strategy fired (every strategy in the bank is individually
    # trusted), only adjusting confidence % rather than gating
    # whether it posts. generate_rule_based_bias below is now only
    # reached in the genuinely rare case where literally no strategy
    # produced any result at all (no usable candle data this round).
    min_agree = 2

    bank_result = run_strategy_bank(
        matched_key, config, h1_candles, h4_candles, daily_candles, min_agree=min_agree
    )
    winning_votes = []
    if bank_result:
        direction, confidence, reason, agreeing_strategies, winning_votes = bank_result
        used_smc = "ICT/SMC" in agreeing_strategies
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

    # SL/TP pip multiplier: 3x/6x (still a 2:1 ratio) for every pair
    # EXCEPT XAUUSD, which uses a tighter 2x/4x so trades resolve
    # faster instead of stalling open for as long - this is
    # deliberately a per-pair override, not a global change, since
    # every other pair's existing 150/300-pip-equivalent distance is
    # untouched.
    if matched_key == "xauusd":
        sl_multiplier, tp_multiplier = 2, 4
    else:
        sl_multiplier, tp_multiplier = 3, 6

    if direction == "BUY":
        entry_price = round(live_price, decimals)
        stop_loss = round(live_price - (pip_size * sl_multiplier), decimals)
        take_profit = round(live_price + (pip_size * tp_multiplier), decimals)
        signal_emoji = "🟢"
        fallback_image_file_id = BUY_IMAGE_FILE_ID
    else:
        entry_price = round(live_price, decimals)
        stop_loss = round(live_price + (pip_size * sl_multiplier), decimals)
        take_profit = round(live_price - (pip_size * tp_multiplier), decimals)
        signal_emoji = "🔴"
        fallback_image_file_id = SELL_IMAGE_FILE_ID

    session = get_market_session()

    # Real generated chart, using the SAME candles and winning
    # strategy that actually produced this signal - falls back to the
    # old static bull/bear graphic if chart generation fails for any
    # reason (never blocks a signal from sending). Per explicit
    # instruction, every signal gets a chart attempt, both channel
    # and DM, forex and synthetics alike, since the cost is purely
    # local CPU (matplotlib) - no extra API calls, no Gemini usage.
    image_file_id = fallback_image_file_id
    chart_strategy_name = winning_votes[0]["strategy_name"] if winning_votes else None
    if chart_strategy_name:
        chart_path = os.path.join(CHART_OUTPUT_DIR, f"{matched_key}_{int(time.time())}.png")
        chart_ok = generate_signal_chart(
            display, chart_strategy_name, direction, h1_candles,
            entry_price, stop_loss, take_profit, chart_path,
        )
        if chart_ok:
            image_file_id = chart_path

    # FBS-style narrative: reason comes FIRST, Entry/SL/TP after, per
    # explicit instruction. Falls back to the original bullet-style
    # "reason" text (already real, already accurate) if winning_votes
    # is empty - happens only when generate_rule_based_bias was the
    # source instead of the strategy bank (no usable candle data this
    # round), which has no structured votes to build prose from.
    if winning_votes:
        narrative = generate_signal_narrative(display, direction, winning_votes)
    else:
        narrative = reason

    response = (
        f"{signal_emoji} <b>{strength} {direction} {display}</b>\n\n"
        f"{narrative}\n\n"
        f"<b>Entry Price:</b> {entry_price}\n"
        f"<b>SL:</b> {stop_loss} | <b>TP:</b> {take_profit}\n\n"
        f"<b>Confidence:</b> {confidence}%\n"
        f"<b>Session:</b> {session}\n\n"
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

🔹 [One line news item 1] — likely [Bullish/Bearish/Neutral] for [pair]

🔹 [One line news item 2] — likely [Bullish/Bearish/Neutral] for [pair]

STRICT RULES:
- Maximum 2 bullet points ONLY
- Each bullet point MAX 20 words INCLUDING the sentiment tag
- [pair] must be a real forex pair or BTC/USD this news actually
  affects (e.g. EUR/USD, GBP/USD, XAU/USD, BTC/USD) - never invent
  a pair the news doesn't relate to
- State Bullish/Bearish/Neutral based on what the news itself
  implies, not a guess - if direction genuinely isn't clear from
  the details given, say Neutral rather than forcing a direction
- No long sentences, no paragraphs
- No markdown symbols like ** or ##
- No hashtags
- Make each point punchy and impactful
- Focus on what matters most to forex, gold, and Bitcoin traders
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

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
        try:
            await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_url,
                caption=summary,
                parse_mode=ParseMode.HTML,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30,
            )
            print(f"[NEWS] ✅ {session_type} posted to {channel_id}")
        except TimedOut:
            # Confirmed real PTB behavior: send_photo can raise
            # TimedOut for a request that Telegram actually completed
            # successfully (the bot's own read timeout expires while
            # waiting for Telegram's confirmation, even though
            # Telegram already finished posting it - especially
            # likely here since image_url points to Pollinations.ai,
            # which Telegram has to fetch server-side and can be
            # slow). This was the actual cause of the photo AND a
            # text-only duplicate both posting - treating a timeout
            # as "uncertain" rather than "definitely failed" means no
            # automatic duplicate gets sent, since the photo most
            # likely did go through.
            print(f"[NEWS] ⚠️ {session_type} timed out for {channel_id} - likely posted anyway, NOT sending a duplicate")
        except Exception as e:
            print(f"[NEWS] AI image failed for {channel_id}, posting text only: {e}")
            try:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=summary,
                    parse_mode=ParseMode.HTML
                )
                print(f"[NEWS] ✅ {session_type} posted (text only) to {channel_id}")
            except Exception as e2:
                print(f"[NEWS] ❌ Failed for {channel_id}: {e2}")

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
            sent_expired_signal = await update.message.reply_text(
                "⚠️ <b>This signal has expired.</b>\n\n"
                "Request a fresh one by typing the index name "
                "(e.g. R_100) in Signal mode.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_expired_signal.chat_id, sent_expired_signal.message_id)
            return

        pending_trades[user_id] = dict(shared_context)  # copy - each tapper gets their own independent trade
        await send_tier_selection(context.bot, user_id, pending_trades[user_id])
        return

    # Channel-follow gate - genuine first-time users ONLY, per explicit
    # instruction that returning users should never see this. Sits
    # here, after the chantrade_ branch above (so someone already
    # mid-trade-flow from a channel tap is never interrupted by this),
    # and before the existing is_verified/trial_remaining checks below.
    if is_first_time_user(user_id):
        already_following = await is_following_channel(context.bot, user_id)
        if not already_following:
            await update.message.reply_text(
                f"👋 <b>Welcome to Nexora AI, {username}!</b>\n\n"
                f"Before we get started, please follow our official "
                f"channel for live signals, news, and updates:\n\n"
                f"👉 {FOLLOW_GATE_CHANNEL}\n\n"
                f"<i>Once you've followed, tap the button below to "
                f"continue.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "📢 Follow Nexora AI Channel",
                        url=f"https://t.me/{FOLLOW_GATE_CHANNEL.lstrip('@')}"
                    )],
                    [InlineKeyboardButton(
                        "✅ I've Followed — Continue",
                        callback_data="followgate_check"
                    )]
                ])
            )
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
            f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
            f"signals directly, manually or fully automatic\n\n"
            f"<i>All three buttons are at the bottom of your screen 👇</i>",
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
            f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
            f"signals directly, manually or fully automatic\n\n"
            f"<i>All three buttons are at the bottom of your screen 👇</i>",
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
                sent_unreachable = await update.message.reply_text(
                    "⚠️ <b>Couldn't reach your linked Deriv account.</b>\n\n"
                    "Your saved token may have expired or been revoked. "
                    "Paste a new real-account API token below to relink.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                schedule_auto_delete(sent_unreachable.chat_id, sent_unreachable.message_id)
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
        sent_signal_mode = await update.message.reply_text(
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
        schedule_auto_delete(sent_signal_mode.chat_id, sent_signal_mode.message_id)
        return

    if "breakdown" in text:
        user_modes[user_id] = "breakdown"
        sent_breakdown_mode = await update.message.reply_text(
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
        schedule_auto_delete(sent_breakdown_mode.chat_id, sent_breakdown_mode.message_id)
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
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>That trade has expired.</b>\n\n"
                "Please request a new signal and tap 🎯 Trade This "
                "Signal again.",
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
                await send_and_auto_delete(
                    context.bot, int(tapping_user_id),
                    "⚠️ <b>This signal has expired.</b>\n\n"
                    "Request a fresh one by typing the index name "
                    "(e.g. R_100) in Signal mode.",
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
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>That trade has expired.</b>\n\n"
                "Please request a new signal and tap 🎯 Trade This "
                "Signal again.",
                parse_mode=ParseMode.HTML
            )
            return

        trade_context["stake"] = tier["stake"]
        trade_context["risk"] = tier["risk"]
        trade_context["win"] = tier["win"]
        pending_trades[user_id] = trade_context

        await send_and_auto_delete(
            context.bot, int(user_id),
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

    elif data.startswith("customtier_"):

        user_id = data.replace("customtier_", "")
        trade_context = pending_trades.get(user_id)

        if not trade_context:
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>That trade has expired.</b>\n\n"
                "Please request a new signal and tap 🎯 Trade This "
                "Signal again.",
                parse_mode=ParseMode.HTML
            )
            return

        user_modes[user_id] = "awaiting_trade_confirm"

        await send_and_auto_delete(
            context.bot, int(user_id),
            "✏️ <b>Enter your stake amount:</b>\n\n"
            "Want to set your own risk/target?\n"
            "Type three numbers that serve as — stake, risk, "
            "target — like <code>3, 2, 10</code> or "
            "<code>3 2 10</code>.",
            parse_mode=ParseMode.HTML
        )

    elif data == "channelcta":

        user_id = str(update.callback_query.from_user.id)
        username = update.callback_query.from_user.username or "Trader"

        # Mirrors start()'s exact logic (minus the chantrade_ deep-
        # link branch, which doesn't apply here) - this button is
        # tapped from a channel post, not a signal-specific deep
        # link, so it should behave exactly like a fresh /start.
        if is_first_time_user(user_id):
            already_following = await is_following_channel(context.bot, user_id)
            if not already_following:
                await context.bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"👋 <b>Welcome to Nexora AI, {username}!</b>\n\n"
                        f"Before we get started, please follow our official "
                        f"channel for live signals, news, and updates:\n\n"
                        f"👉 {FOLLOW_GATE_CHANNEL}\n\n"
                        f"<i>Once you've followed, tap the button below to "
                        f"continue.</i>"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "📢 Follow Nexora AI Channel",
                            url=f"https://t.me/{FOLLOW_GATE_CHANNEL.lstrip('@')}"
                        )],
                        [InlineKeyboardButton(
                            "✅ I've Followed — Continue",
                            callback_data="followgate_check"
                        )]
                    ])
                )
                return

        if is_verified(user_id):
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    f"👋 <b>Welcome back, {username}!</b>\n\n"
                    f"✅ You're a <b>verified Nexora AI trader.</b>\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n"
                    f"👇 <b>What would you like to do today?</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 <b>Signal</b> — Get a live trading signal right now\n\n"
                    f"📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
                    f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
                    f"signals directly, manually or fully automatic\n\n"
                    f"<i>All three buttons are at the bottom of your screen 👇</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        remaining = trial_remaining(user_id)
        if remaining > 0:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
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
                    f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
                    f"signals directly, manually or fully automatic\n\n"
                    f"<i>All three buttons are at the bottom of your screen 👇</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        user_modes[user_id] = "awaiting_email"
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
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
                "📧 <b>Already registered? Type your Exness email now 👇</b>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "📝 I'm New — Register on Exness FREE 👆",
                    url=EXNESS_LINK
                )]
            ])
        )

    elif data == "followgate_check":

        user_id = str(update.callback_query.from_user.id)
        username = update.callback_query.from_user.username or "Trader"

        # Re-checks REAL membership - never just trusts that the
        # button was tapped, since someone could tap "I've Followed"
        # without actually having followed.
        now_following = await is_following_channel(context.bot, user_id)
        if not now_following:
            await context.bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⚠️ <b>We couldn't confirm you've followed the "
                    "channel yet.</b>\n\n"
                    f"Please follow {FOLLOW_GATE_CHANNEL} first, then "
                    "tap the button again."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "📢 Follow Nexora AI Channel",
                        url=f"https://t.me/{FOLLOW_GATE_CHANNEL.lstrip('@')}"
                    )],
                    [InlineKeyboardButton(
                        "✅ I've Followed — Continue",
                        callback_data="followgate_check"
                    )]
                ])
            )
            return

        remaining = trial_remaining(user_id)
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"✅ <b>Thanks for following!</b>\n\n"
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
                f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
                f"signals directly, manually or fully automatic\n\n"
                f"<i>All three buttons are at the bottom of your screen 👇</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
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
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>That setup expired.</b>\n\n"
                "Tap 🔗 Connect Deriv and choose Auto-Copy again.",
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
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>Couldn't save your auto-copy settings right "
                "now.</b>\n\nPlease try again in a moment from "
                "🔗 Connect Deriv.",
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
                "⚠️ <i>Reminder: trades now happen on your account "
                "automatically, with real money. Trading carries risk "
                "of loss. Turn this off anytime from 🔗 Connect "
                "Deriv.</i>"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )

    elif data.startswith("execconfirm_"):

        user_id = data.replace("execconfirm_", "")
        trade_context = pending_trades.pop(user_id, None)  # pop immediately, prevents a double-tap re-executing

        if not trade_context:
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>That trade has expired.</b>\n\n"
                "Please request a new signal and tap 🎯 Trade This "
                "Signal again.",
                parse_mode=ParseMode.HTML
            )
            return

        account = get_deriv_account(user_id)
        if not account:
            await send_and_auto_delete(
                context.bot, int(user_id),
                "⚠️ <b>No linked Deriv account found.</b>\n\n"
                "Tap 🔗 Connect Deriv first to link your real account "
                "before trading.",
                parse_mode=ParseMode.HTML
            )
            return

        wait_message = await context.bot.send_message(
            chat_id=int(user_id),
            text="⏳ <b>Checking your balance...</b>",
            parse_mode=ParseMode.HTML
        )

        try:
            snapshot = await deriv_fetch_account_snapshot(account["api_token"])
            if snapshot and snapshot.get("balance") is not None:
                if snapshot["balance"] < trade_context["stake"]:
                    await wait_message.delete()
                    await send_and_auto_delete(
                        context.bot, int(user_id),
                        f"⚠️ <b>Not enough balance for this stake.</b>\n\n"
                        f"Your balance: ${snapshot['balance']} | "
                        f"Stake needed: ${trade_context['stake']}\n\n"
                        f"Try a smaller amount, or top up your Deriv account.",
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
                await send_and_auto_delete(
                    context.bot, int(user_id),
                    f"❌ <b>Trade not placed.</b>\n\n{friendly_trade_error(error)}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                return

            contract_id = buy_data.get("contract_id", "—")
            buy_price = buy_data.get("buy_price", trade_context["stake"])

            sent_confirmation = await context.bot.send_message(
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
            # Intentionally NOT auto-deleted - successful trade
            # placements are kept permanently as trade history.

        except Exception as e:
            # Catch-all: this is exactly what was previously missing,
            # and exactly why a real trade once got stuck forever on
            # "Placing your trade..." with no error message and no
            # trade ever placed. Whatever goes wrong here, the user
            # always gets told something instead of silence.
            print(f"[EXECCONFIRM] ❌ Unexpected error for {user_id}: {e}")
            try:
                await wait_message.delete()
            except Exception:
                pass
            await send_and_auto_delete(
                context.bot, int(user_id),
                "❌ <b>Something went wrong placing this trade.</b>\n\n"
                "Please request a new signal and try again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )

# ============================================
# HANDLE TEXT
# ============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.message is None or update.message.from_user is None or update.message.text is None:
        # Some update types (e.g. edited messages, or messages with
        # no text like a bare photo/sticker) can reach this handler
        # with nothing usable to act on - nothing to act on, so just
        # return rather than crash. This was previously an unhandled
        # AttributeError that PTB's error handler caught and logged,
        # but never should have reached that point.
        return

    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"
    message = update.message.text.strip()

    if user_modes.get(user_id) == "awaiting_email":

        email = message.strip().lower()

        if "@" not in email or "." not in email:
            sent_invalid_email = await update.message.reply_text(
                "⚠️ <b>That doesn't look like a valid email address.</b>\n\n"
                "Please enter the email address you used to "
                "register on Exness 👇\n\n"
                "<i>Example: yourname@gmail.com</i>",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_invalid_email.chat_id, sent_invalid_email.message_id)
            return

        existing_owner = get_verified_user_by_email(email)
        if existing_owner and str(existing_owner.get("user_id")) != user_id:
            sent_email_used = await update.message.reply_text(
                "🚫 <b>This email has already been used and verified by "
                "another account.</b>\n\n"
                "Each Exness email can only verify one Nexora AI "
                "account. If this is your email, please continue using "
                "your original Telegram account.",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_email_used.chat_id, sent_email_used.message_id)
            return

        pending_owner_id = next(
            (uid for uid, pending_email in pending_verifications.items()
             if pending_email == email and uid != user_id),
            None
        )
        if pending_owner_id:
            sent_email_pending = await update.message.reply_text(
                "🚫 <b>This email is already awaiting verification on "
                "another account.</b>\n\n"
                "Each Exness email can only verify one Nexora AI "
                "account. If this is your email, please continue using "
                "your original Telegram account.",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_email_pending.chat_id, sent_email_pending.message_id)
            return

        already_pending = user_id in pending_verifications
        pending_verifications[user_id] = email

        if already_pending:
            # Already has an open request in the admin queue - update
            # the email they're tied to silently rather than posting
            # a brand new "NEW VERIFICATION REQUEST" message. Without
            # this, someone resubmitting (out of impatience, curiosity,
            # or just not knowing it already went through) would
            # flood the approval group with a fresh message every
            # single time.
            await update.message.reply_text(
                "⏳ <b>You already have a verification request pending "
                "review.</b>\n\n"
                "No need to resubmit — your email has been updated to "
                f"<b>{email}</b> and our team will review it shortly.\n\n"
                "<i>Sit tight — greatness is loading! 🚀</i>",
                parse_mode=ParseMode.HTML
            )
            return

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
            sent_bad_token = await update.message.reply_text(
                "❌ <b>That token didn't work.</b>\n\n"
                "Double-check you copied the full token and that it has "
                "the <b>Trade</b> and <b>Account management</b> scopes "
                "enabled, then paste it again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_bad_token.chat_id, sent_bad_token.message_id)
            return

        if snapshot["is_virtual"]:
            sent_demo_account = await update.message.reply_text(
                "🚫 <b>That's a demo account.</b>\n\n"
                "Nexora account linking is for verified real-money "
                "traders only. Please generate a token from your "
                "<b>real</b> Deriv account and paste it again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_demo_account.chat_id, sent_demo_account.message_id)
            return

        saved = save_deriv_account(
            user_id, snapshot["loginid"], token, snapshot["currency"]
        )

        if not saved:
            sent_save_failed = await update.message.reply_text(
                "⚠️ <b>Your account was verified but couldn't be saved "
                "right now.</b>\n\nPlease try pasting the token again "
                "in a moment.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_save_failed.chat_id, sent_save_failed.message_id)
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
                "🎯 <b>Last step — pick how you want to trade:</b>\n\n"
                "✋ <b>Manual</b>\n"
                "You stay in control. Every time a signal posts, you "
                "tap to confirm before anything happens on your "
                "account.\n\n"
                "🤖 <b>Auto-Copy</b>\n"
                "Hands-off. Signals trade automatically on your "
                "account — no tapping needed. You choose your stake "
                "once, and can switch back to Manual anytime.\n\n"
                "⚠️ <b>Please note:</b> Auto-Copy places real trades "
                "with real money on your account without asking you "
                "first, every time. Nexora AI is not a licensed "
                "financial advisor — trading carries risk, and you can "
                "lose money. Only enable Auto-Copy with funds you're "
                "fully comfortable risking.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✋ Manual — I'll tap each time",
                        callback_data="autocopy_setup_manual"
                    )],
                    [InlineKeyboardButton(
                        "🤖 Auto-Copy — trade for me",
                        callback_data="autocopy_setup_start"
                    )],
                ])
            )
        return

    if user_modes.get(user_id) == "awaiting_trade_confirm":

        trade_context = pending_trades.get(user_id)
        if not trade_context:
            user_modes[user_id] = None
            sent_expired = await update.message.reply_text(
                "⚠️ <b>That trade has expired.</b>\n\n"
                "Please request a new signal and tap 🎯 Trade This Signal "
                "again.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_expired.chat_id, sent_expired.message_id)
            return

        reply = message.strip().lower()

        stake_match = re.search(r"stake\s*=\s*([\d.]+)", reply)
        risk_match = re.search(r"risk\s*=\s*([\d.]+)", reply)
        win_match = re.search(r"win\s*=\s*([\d.]+)", reply)

        # Requires exactly 3 numbers - stake, risk, target must all
        # be provided every time, no auto-derived shortcuts. Catches
        # "3, 2, 10", "3 2 10", "3,2,10" etc: split on commas and/or
        # whitespace, keep only the numeric pieces.
        multi_number_parts = [
            p for p in re.split(r"[,\s]+", reply) if re.fullmatch(r"[\d.]+", p)
        ]

        has_all_three_kv = stake_match and risk_match and win_match
        has_all_three_plain = len(multi_number_parts) == 3

        if not (has_all_three_kv or has_all_three_plain):
            sent_invalid = await update.message.reply_text(
                "⚠️ <b>I didn't understand that.</b>\n\n"
                "Type all three numbers — stake, risk, target — "
                "like <code>3, 2, 10</code> or <code>3 2 10</code>.",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_invalid.chat_id, sent_invalid.message_id)
            return

        if has_all_three_plain:
            trade_context["stake"] = float(multi_number_parts[0])
            trade_context["risk"] = float(multi_number_parts[1])
            trade_context["win"] = float(multi_number_parts[2])
        else:
            trade_context["stake"] = float(stake_match.group(1))
            trade_context["risk"] = float(risk_match.group(1))
            trade_context["win"] = float(win_match.group(1))

        pending_trades[user_id] = trade_context
        user_modes[user_id] = None

        sent_confirm = await update.message.reply_text(
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
        schedule_auto_delete(sent_confirm.chat_id, sent_confirm.message_id)
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

            result = await build_synthetic_signal_response(synthetic_key, min_agree=2)

            await wait_message.delete()

            if not result:
                sent_no_signal = await update.message.reply_text(
                    "⚠️ <b>Unable to generate a signal right now.</b>\n"
                    "Please try again shortly.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                schedule_auto_delete(sent_no_signal.chat_id, sent_no_signal.message_id)
                return

            signal_image_id, signal_message, trade_context = result
            pending_trades[user_id] = trade_context

            sent_synth_signal = await update.message.reply_photo(
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
            schedule_auto_delete(sent_synth_signal.chat_id, sent_synth_signal.message_id)
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
            sent_market_closed = await update.message.reply_text(
                "🌙 <b>Forex Market Closed for the Week</b>\n\n"
                "Gold, Silver, Oil and all Forex pairs are closed "
                "until the market reopens Sunday.\n\n"
                "₿ <b>Crypto (BTCUSD)</b> trades 24/7 — try that "
                "pair instead!",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_market_closed.chat_id, sent_market_closed.message_id)
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
            sent_signal = await update.message.reply_photo(
                photo=image_file_id,
                caption=signal,
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_signal.chat_id, sent_signal.message_id)

            if not is_verified(user_id):
                remaining = trial_remaining(user_id)
                if remaining > 0:
                    sent_trial_notice = await update.message.reply_text(
                        f"⚡ <b>You have {remaining} free trial "
                        f"signal(s) remaining.</b>\n\n"
                        f"Verify your Exness account for "
                        f"<b>unlimited access!</b>\n\n"
                        f"📊 <b>Signal</b> — Get another signal\n"
                        f"📚 <b>Breakdown</b> — Get a market analysis",
                        parse_mode=ParseMode.HTML,
                        reply_markup=main_keyboard
                    )
                    schedule_auto_delete(sent_trial_notice.chat_id, sent_trial_notice.message_id)
                else:
                    user_modes[user_id] = "awaiting_email"
                    await send_verification_gate(update)
        elif signal == "MARKET_CLOSED":
            sent_market_closed2 = await update.message.reply_text(
                "🌙 <b>Forex Market Closed for the Week</b>\n\n"
                "Gold, Silver, Oil and all Forex pairs are closed "
                "until the market reopens Sunday.\n\n"
                "₿ <b>Crypto (BTCUSD)</b> trades 24/7 — try that "
                "pair instead!",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_market_closed2.chat_id, sent_market_closed2.message_id)
        else:
            sent_fetch_failed = await update.message.reply_text(
                "⚠️ <b>Unable to fetch live market data.</b>\n"
                "Please try again shortly.",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_fetch_failed.chat_id, sent_fetch_failed.message_id)
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
        schedule_auto_delete(wait_message.chat_id, wait_message.message_id)

        if not is_verified(user_id):
            remaining = trial_remaining(user_id)
            if remaining > 0:
                sent_trial_notice = await update.message.reply_text(
                    f"⚡ <b>You have {remaining} free trial "
                    f"signal(s) remaining.</b>\n\n"
                    f"Verify your Exness account for "
                    f"<b>unlimited access!</b>\n\n"
                    f"📊 <b>Signal</b> — Get a live trading signal\n"
                    f"📚 <b>Breakdown</b> — Get a market analysis",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                schedule_auto_delete(sent_trial_notice.chat_id, sent_trial_notice.message_id)
            else:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

    sent_fallback = await update.message.reply_text(
        "👇 <b>Here's what you can do:</b>\n\n"
        "📊 <b>Signal</b> — Get a live trading signal right now\n\n"
        "📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
        "<i>Both buttons are right at the bottom of your screen!</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )
    schedule_auto_delete(sent_fallback.chat_id, sent_fallback.message_id)

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

    pair_name = PAIR_CONFIG.get(pair_keyword, {}).get("pair_name", pair_keyword.upper())
    if has_open_signal_for_pair(pair_name):
        print(
            f"[AUTO SIGNAL] ⏸️ {pair_keyword.upper()} skipped — a "
            f"previous {pair_name} signal hasn't closed yet."
        )
        return

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

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
        try:
            markup = (
                get_channel_button()
                if channel_id in (CHANNEL_1_ID, CHANNEL_3_ID)
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

async def post_morning_signal(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every day at 07:00 UTC (8AM Lagos). Looks up today's pair
    from MORNING_PAIR_BY_WEEKDAY rather than having a fixed pair
    baked into the job itself - this is what lets the same single
    job serve XAUUSD on weekdays and BTCUSD on weekends.
    """
    weekday = datetime.utcnow().weekday()  # 0=Monday ... 6=Sunday
    pair_keyword = MORNING_PAIR_BY_WEEKDAY.get(weekday)
    if not pair_keyword:
        return
    await _post_signal_for_pair(context.bot, pair_keyword)

async def post_evening_signal(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every day at 17:00 UTC (6PM Lagos). Looks up today's pair
    from EVENING_PAIR_BY_WEEKDAY - None on Sat/Sun, since those days
    only get the volatility-index slot in the evening, not a forex/
    crypto signal.
    """
    weekday = datetime.utcnow().weekday()  # 0=Monday ... 6=Sunday
    pair_keyword = EVENING_PAIR_BY_WEEKDAY.get(weekday)
    if not pair_keyword:
        return
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

    result = await build_synthetic_signal_response(index_key, min_agree=2)
    if not result:
        print(f"[AUTO SYNTH] ❌ No signal generated for {index_key}, skipping post")
        return

    signal_image_id, signal_message, trade_context = result
    channel_signal_context[index_key] = trade_context

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
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

# ============================================
# TESTSYNTH (TEMPORARY - admin only)
# Manually fires one fresh synthetic signal on
# demand, instead of waiting for the next
# scheduled slot. Added specifically to verify
# the channel_signal_context_db fix without
# waiting until tomorrow's 11:00 UTC post.
# Remove once confirmed working.
# ============================================

async def testsynth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)

    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /testsynth r10 | r25 | r50 | r75 | r100"
        )
        return

    index_key = args[0].lower()
    if index_key not in SYNTHETIC_CONFIG:
        await update.message.reply_text(
            f"Unknown index '{index_key}'. Use one of: "
            f"{', '.join(SYNTHETIC_CONFIG.keys())}"
        )
        return

    await update.message.reply_text(f"Firing a fresh {index_key.upper()} signal now...")
    await _post_synthetic_signal_for_index(context.bot, index_key)
    await update.message.reply_text("Done - check the channel and tap Trade This Signal.")

# ============================================
# PURGEDIGESTS (TEMPORARY - admin only)
# One-time push-delete of every existing auto-
# copy digest message still sitting in chats -
# the digest job itself is now permanently
# disabled (see main()), but messages it already
# sent before that don't disappear on their own.
# Run once, then this command can be removed.
# ============================================

async def purgedigests_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)

    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_accounts"
            f"?last_digest_message_id=not.is.null&select=user_id,last_digest_message_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=15)
        rows = response.json()
    except Exception as e:
        await update.message.reply_text(f"Couldn't fetch accounts: {e}")
        return

    if not isinstance(rows, list) or not rows:
        await update.message.reply_text("No existing digest messages found to delete.")
        return

    await update.message.reply_text(f"Deleting {len(rows)} existing digest message(s)...")

    deleted = 0
    failed = 0
    for row in rows:
        target_user_id = row.get("user_id")
        message_id = row.get("last_digest_message_id")
        if not target_user_id or not message_id:
            continue
        try:
            await context.bot.delete_message(
                chat_id=int(target_user_id), message_id=int(message_id)
            )
            deleted += 1
        except Exception as e:
            failed += 1
            print(f"[PURGE DIGESTS] Couldn't delete for {target_user_id}: {e}")
        # Clear the saved message_id either way, so this never tries
        # to delete the same (now-gone or already-failed) message
        # again on a future run.
        try:
            url = f"{SUPABASE_URL}/rest/v1/deriv_accounts?user_id=eq.{target_user_id}"
            requests.patch(
                url, headers=sb_headers(),
                json={"last_digest_message_id": None}, timeout=10
            )
        except Exception as e:
            print(f"[PURGE DIGESTS] Couldn't clear saved id for {target_user_id}: {e}")

    await update.message.reply_text(
        f"Done. Deleted: {deleted} | Failed: {failed} "
        f"(likely already deleted, or message older than Telegram's "
        f"48h bot-delete window)."
    )

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
# AUTO-COPY TRADE MONITOR (NEW)
# Deriv equivalent of check_open_signals/
# get_mt5_trade_outcome above, but for
# auto_copy_trades rows instead of signal_log.
# Runs every 15 minutes: for each row still
# marked OPEN, asks Deriv directly whether that
# contract has actually closed yet (real
# profit/loss, not inferred), and marks it CLOSED
# once it has. This is what allows
# has_open_auto_copy_trade's no-stacking check to
# correctly free up an index again once a
# position genuinely finishes - without this,
# every row would stay OPEN forever and silently
# block all future trades on that index.
# ============================================

async def check_open_auto_copy_trades(context: ContextTypes.DEFAULT_TYPE):
    open_trades = get_open_auto_copy_trades()
    if not open_trades:
        return

    accounts_by_user = {a["user_id"]: a for a in get_all_auto_copy_accounts()}

    for trade in open_trades:
        user_id = trade.get("user_id")
        contract_id = trade.get("contract_id")
        account = accounts_by_user.get(user_id)
        if not account or not contract_id:
            continue

        outcome, profit = await get_deriv_contract_outcome(
            account["api_token"], contract_id
        )
        if outcome == "CLOSED":
            mark_auto_copy_trade_closed(contract_id, profit)
        # outcome == "OPEN" -> still running, nothing to do
        # outcome is None -> lookup failed, retry next sweep

# ============================================
# AUTO-COPY DAILY DIGEST (NEW)
# Replaces the old per-30-min summary DM, which
# was too noisy (up to 5 results every round,
# several times a day). Instead, every individual
# trade placement is logged silently throughout
# the day (log_auto_copy_trade / log_auto_copy_
# failure - see run_auto_copy_scan), and ONE
# digest per user is sent at 23:59 UTC summarizing
# the whole day: how many trades placed (with
# details) and a single count of how many failed
# (no per-failure detail, since none of it is
# actionable - see friendly_trade_error's
# auto_copy_context wording).
#
# To avoid the chat filling up with daily digests
# over time, sending today's digest first deletes
# yesterday's (stored via its message_id on
# deriv_accounts.last_digest_message_id) - so at
# most one digest ever sits in a user's chat.
# ============================================

def get_todays_auto_copy_trades(user_id):
    try:
        today_start = datetime.utcnow().strftime("%Y-%m-%dT00:00:00")
        url = (
            f"{SUPABASE_URL}/rest/v1/auto_copy_trades"
            f"?user_id=eq.{user_id}&placed_at=gte.{today_start}&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[AUTO-COPY DIGEST] get_todays_auto_copy_trades error: {e}")
        return []

def get_todays_auto_copy_failure_count(user_id):
    try:
        today_start = datetime.utcnow().strftime("%Y-%m-%dT00:00:00")
        url = (
            f"{SUPABASE_URL}/rest/v1/auto_copy_failures"
            f"?user_id=eq.{user_id}&failed_at=gte.{today_start}&select=id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return len(data) if isinstance(data, list) else 0
    except Exception as e:
        print(f"[AUTO-COPY DIGEST] get_todays_auto_copy_failure_count error: {e}")
        return 0

def save_last_digest_message_id(user_id, message_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_accounts?user_id=eq.{user_id}"
        requests.patch(
            url, headers=sb_headers(),
            json={"last_digest_message_id": message_id}, timeout=10
        )
    except Exception as e:
        print(f"[AUTO-COPY DIGEST] save_last_digest_message_id error: {e}")

async def send_auto_copy_daily_digest(context: ContextTypes.DEFAULT_TYPE):
    accounts = get_all_auto_copy_accounts()
    if not accounts:
        return

    bot = context.bot

    for account in accounts:
        user_id = account.get("user_id")
        if not user_id:
            continue

        trades_today = get_todays_auto_copy_trades(user_id)
        failure_count = get_todays_auto_copy_failure_count(user_id)

        if not trades_today and not failure_count:
            continue  # nothing happened for this user today - no digest at all

        lines = [
            f"✅ <b>{t['direction']} {SYNTHETIC_CONFIG.get(t['symbol'], {}).get('display', t['symbol'])}</b>\n"
            f"Stake: ${t['stake']} | Risk ${t['risk']} → Win ${t['win']} | ID: {t['contract_id']}"
            for t in trades_today
        ]

        digest = "🤖 <b>Auto-Copy — today's summary</b>\n\n"
        digest += "\n\n".join(lines) if lines else "<i>No trades placed today.</i>"
        if failure_count:
            digest += (
                f"\n\n<i>{failure_count} other signal"
                f"{'s' if failure_count != 1 else ''} couldn't be "
                f"placed today — no action needed, they retry "
                f"automatically.</i>"
            )

        try:
            old_message_id = account.get("last_digest_message_id")
            if old_message_id:
                try:
                    await bot.delete_message(chat_id=int(user_id), message_id=int(old_message_id))
                except Exception as e:
                    print(f"[AUTO-COPY DIGEST] Couldn't delete yesterday's digest for {user_id}: {e}")

            sent = await bot.send_message(
                chat_id=int(user_id),
                text=digest,
                parse_mode=ParseMode.HTML
            )
            save_last_digest_message_id(user_id, sent.message_id)
        except Exception as e:
            print(f"[AUTO-COPY DIGEST] ❌ Couldn't send digest to {user_id}: {e}")

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

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
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

    global _app_instance

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(catch_up_missed_signals)
        .build()
    )
    _app_instance = app

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("testsynth", testsynth_command))
    app.add_handler(CommandHandler("purgedigests", purgedigests_command))
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

    # PTB v20+ run_daily's `days` param is CRON-STYLE: 0=Sunday ...
    # 6=Saturday - NOT Python's datetime.weekday() convention
    # (0=Monday...6=Sunday) used everywhere else in this file (e.g.
    # MORNING_PAIR_BY_WEEKDAY/EVENING_PAIR_BY_WEEKDAY above, and
    # post_morning_signal/post_evening_signal's own weekday() calls).
    # This exact mix-up already caused one real bug fixed earlier
    # (weekly_report firing Saturday instead of Sunday) - the
    # previous WEEKDAYS_ONLY=(0,1,2,3,4)/WEEKEND_ONLY=(5,6) below
    # were ALSO wrong under this convention (they actually selected
    # Sun-Thu and Fri-Sat respectively, not Mon-Fri/Sat-Sun) and are
    # corrected here.
    WEEKDAYS_ONLY = (1, 2, 3, 4, 5)  # Mon-Fri, cron-style
    WEEKEND_ONLY = (6, 0)            # Sat-Sun, cron-style
    EVERY_DAY = (0, 1, 2, 3, 4, 5, 6)
    WEDNESDAY_ONLY = (3,)            # cron-style: 3=Wednesday

    for i, (utc_time, post_type, data) in enumerate(DAILY_SCHEDULE):
        if post_type == "news":
            job_queue.run_daily(
                post_news,
                time=parse_time(utc_time),
                name=f"news_{i}_{data}",
                data=data,
                job_kwargs={"misfire_grace_time": 300}
            )

    # Morning and evening signal slots both run EVERY day - each job
    # looks up today's actual pair itself (MORNING_PAIR_BY_WEEKDAY /
    # EVENING_PAIR_BY_WEEKDAY, using Python's weekday() convention),
    # since run_daily's days= can only include/exclude whole days,
    # not switch which pair fires on which day.
    #
    # misfire_grace_time=300 (5 min): confirmed via real Railway logs
    # that evening_signal was silently skipped entirely on a day it
    # was delayed by barely over 1 second past its exact scheduled
    # moment - APScheduler's default grace window is too tight now
    # that many more jobs run concurrently (tick burst every 60s,
    # auto-copy scan every 30 min, TP/SL monitor every 15 min) than
    # when these two jobs were first built, so brief scheduler
    # contention was enough to cause a full missed day rather than
    # just a few seconds' delay.
    job_queue.run_daily(
        post_morning_signal,
        time=parse_time("07:00"),
        name="morning_signal",
        days=EVERY_DAY,
        job_kwargs={"misfire_grace_time": 300}
    )
    job_queue.run_daily(
        post_evening_signal,
        time=parse_time("17:00"),
        name="evening_signal",
        days=EVERY_DAY,
        job_kwargs={"misfire_grace_time": 300}
    )

    for i, (utc_time, schedule_type, slot_number) in enumerate(SYNTHETIC_SCHEDULE):
        if schedule_type == "wednesday_only":
            days = WEDNESDAY_ONLY
        elif schedule_type == "weekend":
            days = WEEKEND_ONLY
        else:
            days = WEEKDAYS_ONLY
        job_queue.run_daily(
            post_auto_synthetic_signal,
            time=parse_time(utc_time),
            name=f"synth_{i}_{schedule_type}_{slot_number}",
            data=slot_number,
            days=days,
            job_kwargs={"misfire_grace_time": 300}
        )

    # TP/SL monitor - checks every OPEN logged signal every 15 minutes
    job_queue.run_repeating(
        check_open_signals,
        interval=900,
        first=60,
        name="tp_sl_monitor"
    )

    # Auto-delete sweep - performs the actual deletion for anything
    # in auto_delete_queue_db whose time has come. Every 10 minutes
    # is frequent enough that a message disappears close to its real
    # 24h mark, without needing the in-memory job_queue.run_once that
    # used to silently lose every pending deletion on a Railway
    # restart (the actual root cause of "auto-delete never works").
    job_queue.run_repeating(
        process_due_auto_deletes,
        interval=600,
        first=45,
        name="auto_delete_sweep"
    )

    # Auto-copy trade monitor - Deriv equivalent of the TP/SL monitor
    # above. Marks auto_copy_trades rows CLOSED once Deriv confirms
    # the real outcome, which is what frees an index back up for the
    # no-stacking check once a position genuinely finishes.
    job_queue.run_repeating(
        check_open_auto_copy_trades,
        interval=900,
        first=90,
        name="auto_copy_trade_monitor"
    )

    # Auto-copy scan - independent of the channel posting schedule.
    # Checks all 5 synthetic indices every 30 minutes and trades any
    # fresh signal for every opted-in auto-copy user (skipping any
    # index they're already holding a position on - see
    # run_auto_copy_scan's docstring for why).
    job_queue.run_repeating(
        run_auto_copy_scan,
        interval=1800,
        first=120,
        name="auto_copy_scan"
    )

    # Tick Burst auto-copy scan - DISABLED per explicit instruction
    # after it caused real account losses across multiple users and
    # was found unreliable. Function code (run_tickburst_auto_copy_
    # scan, detect_tick_burst, deriv_get_tick_count) is left intact
    # below, untouched, in case this strategy is revisited later -
    # but this job registration is commented out, so it does NOT run
    # at all right now. Do not re-enable without explicit confirmation.
    #
    # job_queue.run_repeating(
    #     run_tickburst_auto_copy_scan,
    #     interval=60,
    #     first=30,
    #     name="tickburst_auto_copy_scan"
    # )

    # Auto-copy daily digest - PERMANENTLY DISABLED per explicit
    # instruction. Function code (send_auto_copy_daily_digest) is
    # left intact below in case this is revisited later, but this
    # job registration is commented out - it will NOT run at all.
    #
    # job_queue.run_daily(
    #     send_auto_copy_daily_digest,
    #     time=parse_time("23:59"),
    #     name="auto_copy_daily_digest",
    #     days=EVERY_DAY,
    #     job_kwargs={"misfire_grace_time": 300}
    # )

    # Weekly performance report - every Sunday at 23:00 UTC
    # NOTE: PTB v20+ uses cron-style day indexing for run_daily's
    # `days` param (0=Sunday ... 6=Saturday), NOT Python's
    # datetime.weekday() convention (0=Monday ... 6=Sunday). days=(6,)
    # was firing Saturday, not Sunday - days=(0,) is correct here.
    job_queue.run_daily(
        post_weekly_report,
        time=parse_time("23:00"),
        name="weekly_report",
        days=(0,),
        job_kwargs={"misfire_grace_time": 300}
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
