import os
import asyncio
import random
import math
import hmac
import hashlib
import threading
import requests
import re
import json
import time
import secrets
import base64
import inspect
import websockets
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle

from metaapi_cloud_sdk import MetaApi
from cryptography.fernet import Fernet
from aiohttp import web

from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
    MenuButtonDefault,
    BotCommand,
    InputMediaPhoto,
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
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
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
# Separate app_id + redirect used ONLY for "Login with Deriv" - kept
# apart from DERIV_APP_ID (used for every other Deriv API call) so
# nothing about the existing manual-token flow is ever at risk from
# this. See the PKCE OAuth block further down for how these are used.
DERIV_OAUTH_APP_ID = os.getenv("DERIV_OAUTH_APP_ID")
DERIV_OAUTH_REDIRECT_URL = os.getenv("DERIV_OAUTH_REDIRECT_URL")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")  # restricts /broadcast to this Telegram user ID only

# ============================================
# METAAPI CONFIG
# ============================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# ============================================
# MT5 AUTO-TRADE (NEW) - Exness auto-trading for
# verified, subscribed clients. Per explicit
# instruction: separate from the bot's own single
# MT5 account above - each client connects THEIR
# OWN MT5/MT4 login via MetaAPI, gated behind BOTH
# being a verified Exness client AND having an
# active paid KoraPay subscription.
# ============================================

# Never hardcode real secrets in this file - both of these must be
# set as Railway environment variables, matching every other secret
# already used throughout this bot (METAAPI_TOKEN, GEMINI_API_KEY, etc).
KORAPAY_SECRET_KEY = os.getenv("KORAPAY_SECRET_KEY")
CREDENTIAL_ENCRYPTION_KEY = os.getenv("CREDENTIAL_ENCRYPTION_KEY")

KORAPAY_BASE_URL = "https://api.korapay.com/merchant/api/v1"

# Set this AFTER exposing this Railway service publicly (Settings ->
# Networking -> Generate Domain), e.g.
# "https://your-service.up.railway.app/korapay-webhook" - KoraPay
# needs a real, internet-reachable URL to send payment confirmations
# to. This bot has never needed a public HTTP endpoint before now.
KORAPAY_WEBHOOK_URL = os.getenv("KORAPAY_WEBHOOK_URL")

# $50/month, confirmed by SpiritFX. NOTE: KoraPay's documented amount
# format for NGN/GHS/KES is the currency's own base unit (e.g. 50000
# means ₦50,000, not kobo) - USD wasn't explicitly confirmed either
# way in their docs, so this assumes the same base-unit convention
# (50 = $50.00, not $0.50 or $5000). Worth a $1 test charge before
# taking real payments, just to be certain before this touches real
# client money.
# $50/month is the price shown to users everywhere - kept as a clean,
# stable USD reference figure. The ACTUAL charge sent to KoraPay is
# in NGN instead (USD checkout wasn't enabled on this KoraPay
# account), converted at a real, current rate as of mid-July 2026
# (~₦1,380/$1, confirmed via live search, not from memory - exchange
# rates move daily). NEEDS PERIODIC MANUAL UPDATING - this is a
# snapshot rate, not a live lookup, so it will drift out of sync with
# the true $50 value over time. Revisit every few weeks, or ask to
# wire in a live FX API call at charge-time for a more precise,
# always-current conversion if that drift becomes noticeable.
MT5_AUTOTRADE_DISPLAY_PRICE = "$50/month"
MT5_AUTOTRADE_MONTHLY_FEE = 69000
MT5_AUTOTRADE_CURRENCY = "NGN"

MT5_SUBSCRIPTION_DAYS = 30

# EXPERIMENTAL, per explicit instruction - a one-week test flipping
# every final BUY/SELL call to its opposite, applied EVERYWHERE
# (channel signals, manual /Signal, Exness Auto-Trade, Deriv
# Auto-Copy), without touching any strategy logic itself. Deliberately
# a single flag in one place so this is trivial to revert - set back
# to False (or delete the flip calls) to return to exactly how things
# were before. No strategies were removed or added for this test;
# only the final direction gets flipped, at the last possible moment,
# right before it's displayed or traded on.
EXPERIMENTAL_INVERT_SIGNALS = False


def maybe_invert_direction(direction):
    """
    Applies the EXPERIMENTAL_INVERT_SIGNALS flip, if it's on. Kept as
    one small, reusable function so every call site (channel signals,
    manual signals, Exness Auto-Trade, Deriv Auto-Copy) stays in sync
    automatically - flip the single flag above, not each call site.
    """
    if not EXPERIMENTAL_INVERT_SIGNALS:
        return direction
    return "SELL" if direction == "BUY" else "BUY"

# 4 bot presets for Exness Auto-Trade - SAME 4 bots as always, never
# renamed. Each pairs its own original base strategy with a second
# one (OR-vote: fires on whichever of the two confirms first).
# Current pairings, after two rounds of explicit-instruction changes:
#   aggressive_scalper      ema_pullback_scalper       + macd_momentum
#   aggressive_breakout     volatility_breakout_scalper + atr_volatility_breakout (was rsi_extreme_reversal - removed per explicit instruction, too subjective a concept)
#   conservative_trend      trend_following             + macd_momentum
#   conservative_structure  support_resistance_bounce   + supertrend (was ict_smc/strategy_unicorn_model - removed per explicit instruction, the most subjective/discretionary strategy in the whole bank)
#
# "strategy_functions" (plural) replaces the old single
# "strategy_function". "timeframe": "1min" for Aggressive, "5min" for
# Conservative (down from the old 1h/4h) - per explicit instruction.
MT5_AUTOTRADE_BOTS = {
    "aggressive_scalper": {
        "label": "🐆 Aggressive Scalper",
        # EXPANDED per explicit instruction, mirroring the exact
        # synthetics pattern - kept the bot's original pair (EMA
        # Pullback Scalper + Momentum MACD) as the "core", added all 8
        # new strategies on top (10 total). A bigger, genuinely
        # independent pool means the real 2-vote gate below has room
        # to find agreement often, not just occasionally.
        "strategy_functions": [
            "strategy_ema_pullback_scalper", "strategy_momentum_macd",
            "strategy_parabolic_sar", "strategy_ichimoku_breakout", "strategy_keltner_breakout",
            "strategy_ema_ribbon", "strategy_rate_of_change", "strategy_cci_breakout",
            "strategy_williams_r", "strategy_heikin_ashi_trend",
        ],
        "timeframe": "1min",
        "description": "Fast, frequent entries on short-term pullbacks.",
    },
    "aggressive_breakout": {
        "label": "⚡ Aggressive Breakout",
        # EXPANDED per explicit instruction - core: Volatility
        # Breakout Scalper + ATR Volatility Breakout, plus all 8 new.
        "strategy_functions": [
            "strategy_volatility_breakout_scalper", "strategy_atr_volatility_breakout",
            "strategy_parabolic_sar", "strategy_ichimoku_breakout", "strategy_keltner_breakout",
            "strategy_ema_ribbon", "strategy_rate_of_change", "strategy_cci_breakout",
            "strategy_williams_r", "strategy_heikin_ashi_trend",
        ],
        "timeframe": "1min",
        "description": "Fires on sharp volatility expansions.",
    },
    "conservative_trend": {
        "label": "🛡️ Conservative Trend",
        # EXPANDED per explicit instruction - core: Trend Following +
        # Momentum MACD, plus all 8 new.
        "strategy_functions": [
            "strategy_trend_following", "strategy_momentum_macd",
            "strategy_parabolic_sar", "strategy_ichimoku_breakout", "strategy_keltner_breakout",
            "strategy_ema_ribbon", "strategy_rate_of_change", "strategy_cci_breakout",
            "strategy_williams_r", "strategy_heikin_ashi_trend",
        ],
        "timeframe": "5min",
        "description": "Slower, higher-conviction trend-following entries.",
    },
    "conservative_structure": {
        "label": "🏛️ Conservative Structure",
        # EXPANDED per explicit instruction - core: Bollinger Squeeze
        # Breakout + Supertrend, plus all 8 new.
        "strategy_functions": [
            "strategy_bollinger_squeeze_breakout", "strategy_supertrend",
            "strategy_parabolic_sar", "strategy_ichimoku_breakout", "strategy_keltner_breakout",
            "strategy_ema_ribbon", "strategy_rate_of_change", "strategy_cci_breakout",
            "strategy_williams_r", "strategy_heikin_ashi_trend",
        ],
        "timeframe": "5min",
        "description": "Patient, level-based support/resistance entries.",
    },
}

# Curated subset of PAIR_CONFIG for bot trading - not all 12 pairs,
# to keep the choice simple and recognizable.
MT5_AUTOTRADE_PAIRS = ["xauusd", "gbpjpy", "btcusd", "eurusd", "gbpusd", "usdjpy"]

# Deriv's mirror of MT5_AUTOTRADE_BOTS. IMPORTANT DIFFERENCE, stated
# plainly rather than glossed over: synthetic indices already run
# their OWN dedicated strategy roster (SYNTHETIC_STRATEGY_BANK, via
# run_strategy_bank_synthetic) rather than the forex STRATEGY_BANK
# the MT5 bots pull named subsets from - ICT/SMC and session-based
# strategies are deliberately excluded there since their assumptions
# don't hold on an RNG-generated instrument. So "same entry logic"
# here means the same STRUCTURAL idea (one shared strategy universe,
# tiers differ by how much agreement is required), not literally the
# same named functions - Aggressive fires on just 1 agreeing
# strategy (fast, frequent), Conservative needs 3+ to agree (slower,
# higher-conviction), both drawing from the full synthetic pool.
DERIV_AUTOTRADE_BOTS = {
    "aggressive": {
        "label": "🐆 Aggressive",
        "min_agree": 1,
        "description": "Fast, frequent entries - fires on just 1 agreeing strategy.",
    },
    "conservative": {
        "label": "🛡️Conservative",
        "min_agree": 3,
        "description": "Slower, higher-conviction entries - needs 3+ strategies to agree.",
    },
}
# DERIV_AUTOTRADE_PAIRS is defined further down, right after
# SYNTHETIC_CONFIG/AUTO_COPY_EXCLUDED_INDICES exist - this dict here
# doesn't depend on it, but that list does, and SYNTHETIC_CONFIG
# isn't defined yet at this point in the file.

_fernet = Fernet(CREDENTIAL_ENCRYPTION_KEY.encode()) if CREDENTIAL_ENCRYPTION_KEY else None

def encrypt_credential(plain_text):
    """
    Encrypts a raw MT5/MT4 password before it ever touches Supabase -
    per explicit instruction, real broker passwords must never be
    stored in plain text. Raises clearly if the encryption key isn't
    configured, rather than silently storing something unsafe.
    """
    if not _fernet:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not configured - cannot safely store this password.")
    return _fernet.encrypt(plain_text.encode()).decode()

def decrypt_credential(encrypted_text):
    """
    Decrypts a stored MT5/MT4 password - only ever called transiently,
    right when actually needed to (re)provision a MetaAPI account,
    never logged or displayed anywhere.
    """
    if not _fernet:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY not configured - cannot decrypt stored password.")
    return _fernet.decrypt(encrypted_text.encode()).decode()


async def korapay_initialize_charge(user_id, email, reference):
    """
    Starts a KoraPay payment for the MT5 auto-trade subscription.
    Returns the checkout_url to send the user, or None on failure.
    Confirmed against KoraPay's current live documentation
    (developers.korapay.com/docs/checkout-redirect) - server-side
    "Initialize Charge" endpoint, since a Telegram bot has no browser
    context for the JS-widget "Checkout Standard" flow.
    """
    if not KORAPAY_SECRET_KEY:
        print("[KORAPAY] KORAPAY_SECRET_KEY not configured.")
        return None
    if not MT5_AUTOTRADE_MONTHLY_FEE:
        print("[KORAPAY] MT5_AUTOTRADE_MONTHLY_FEE not set yet - waiting on the real price from SpiritFX.")
        return None
    try:
        response = requests.post(
            f"{KORAPAY_BASE_URL}/charges/initialize",
            headers={"Authorization": f"Bearer {KORAPAY_SECRET_KEY}"},
            json={
                "amount": MT5_AUTOTRADE_MONTHLY_FEE,
                "currency": MT5_AUTOTRADE_CURRENCY,
                "reference": reference,
                "customer": {"email": email, "name": str(user_id)},
                "notification_url": KORAPAY_WEBHOOK_URL,
            },
            timeout=15,
        )
        data = response.json()
        if data.get("status") and data.get("data", {}).get("checkout_url"):
            return data["data"]["checkout_url"]
        print(f"[KORAPAY] Charge init failed: {data}")
        return None
    except Exception as e:
        print(f"[KORAPAY] Charge init error: {e}")
        return None


def verify_korapay_signature(data_dict, signature_header):
    """
    Confirms a webhook genuinely came from KoraPay, per their
    documented scheme (developers.korapay.com/docs/webhooks) - an
    HMAC-SHA256 of the JSON-encoded `data` object, signed with the
    same secret key, compared against the x-korapay-signature header.
    NEVER trust a webhook without this check - anyone could otherwise
    POST a fake "payment successful" straight to this endpoint.

    NOTE: re-serializes the parsed `data` dict compactly rather than
    using raw request bytes. This matches KoraPay's own JSON.stringify
    approach in the overwhelming majority of cases (Python's json
    module preserves key order from parsing, same as JS), but if
    signature checks ever mysteriously fail in production, the first
    thing to check is exact byte-for-byte reconstruction here.
    """
    if not KORAPAY_SECRET_KEY or not signature_header:
        return False
    computed = hmac.new(
        KORAPAY_SECRET_KEY.encode(),
        json.dumps(data_dict, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature_header)


def log_korapay_transaction(reference, user_id, amount, currency):
    try:
        url = f"{SUPABASE_URL}/rest/v1/korapay_transactions"
        requests.post(url, headers=sb_headers(), json={
            "reference": reference, "user_id": user_id,
            "amount": amount, "currency": currency, "status": "pending"
        }, timeout=10)
    except Exception as e:
        print(f"[KORAPAY DB] log_korapay_transaction error: {e}")


def get_unprocessed_confirmed_transactions():
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/korapay_transactions"
            f"?status=eq.success&processed=eq.false&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[KORAPAY DB] get_unprocessed_confirmed_transactions error: {e}")
        return []


def mark_korapay_transaction_processed(reference):
    try:
        url = f"{SUPABASE_URL}/rest/v1/korapay_transactions?reference=eq.{reference}"
        requests.patch(url, headers=sb_headers(), json={"processed": True}, timeout=10)
    except Exception as e:
        print(f"[KORAPAY DB] mark_korapay_transaction_processed error: {e}")


def get_mt5_autotrade_account(user_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_auto_trade_accounts?user_id=eq.{user_id}&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return data[0] if data else None
    except Exception as e:
        print(f"[MT5 AUTOTRADE DB] get_mt5_autotrade_account error: {e}")
        return None


def upsert_mt5_autotrade_account(user_id, fields):
    """
    REAL BUG FOUND AND FIXED, per explicit instruction - this used to
    call requests.post and never check the response at all. If
    Supabase rejected the write for ANY reason (a confirmed real case:
    a payment got marked "processed" in korapay_transactions, but the
    matching subscription row was never created here, with zero error
    anywhere), this function reported silent success regardless,
    since requests.post() only raises on network-level failures, not
    on Supabase/PostgREST rejecting the request itself. Now actually
    checks the status code and raises with the real response body, so
    a rejected write is loud and traceable instead of vanishing.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_auto_trade_accounts"
        payload = {**fields, "user_id": user_id}
        response = requests.post(
            url,
            headers={**sb_headers(), "Prefer": "resolution=merge-duplicates"},
            json=payload, timeout=10
        )
        if response.status_code not in (200, 201, 204):
            raise Exception(f"Supabase rejected the write (status {response.status_code}): {response.text[:500]}")
    except Exception as e:
        print(f"[MT5 AUTOTRADE DB] upsert_mt5_autotrade_account error for user {user_id}: {e}")
        raise


def is_mt5_autotrade_active(user_id):
    """
    The BOTH-gates check, per explicit instruction: verified Exness
    client AND currently subscribed (not expired) AND has a connected
    MetaAPI account - all three, not just one.
    """
    if not is_verified(user_id):
        return False
    account = get_mt5_autotrade_account(user_id)
    if not account or not account.get("metaapi_account_id"):
        return False
    expires_at = account.get("subscription_expires_at")
    if not expires_at:
        return False
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00")).replace(tzinfo=None)
        return datetime.utcnow() < expiry
    except Exception:
        return False


async def deprovision_mt5_account(metaapi_account_id):
    """
    Removes a MetaAPI-managed account resource entirely - used when a
    customer chooses to CONNECT A NEW account instead of keeping their
    current one on renewal, per explicit instruction, so the old one
    doesn't sit around as an orphaned, unused resource. Best-effort:
    logs on failure but never raises, since a failed cleanup shouldn't
    block the customer from connecting their new account.
    """
    try:
        api = MetaApi(token=METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(account_id=metaapi_account_id)
        await account.remove()
        print(f"[MT5 PROVISION] Removed old account {metaapi_account_id}")
    except Exception as e:
        print(f"[MT5 PROVISION] Couldn't remove old account {metaapi_account_id}: {e}")


async def provision_mt5_account(login, password, server, platform="mt5", account_name=None):
    """
    Creates a NEW MetaAPI-managed trading account for a CLIENT'S OWN
    MT5/MT4 login, using the SAME single METAAPI_TOKEN already used
    for the bot's own account - MetaAPI supports managing many
    separate accounts under one token, each identified by its own
    returned account_id. Confirmed against MetaAPI's current live
    documentation (metaapi.cloud/docs/provisioning/api/account/
    createAccount) - omitting provisioningProfileId lets MetaAPI
    auto-detect Exness's broker settings automatically.

    Returns (account_id, error_message). Handles the documented
    "Automatic broker settings detection is in progress" response by
    waiting and retrying up to 3 times before giving up, rather than
    treating it as an outright failure on the first try.
    """
    try:
        api = MetaApi(token=METAAPI_TOKEN)
        payload = {
            "login": login,
            "password": password,
            "name": account_name or f"Client {login}",
            "server": server,
            "platform": platform,
            "keywords": ["Exness"],
            # REAL FIX, per explicit instruction - MetaApi's own
            # validation error (surfaced only after fixing the error
            # logging above) confirmed this exact field was missing:
            # {'parameter': 'magic', 'message': 'Required value.'}.
            # This wasn't specific to any one customer's login/
            # password/server - EVERY account creation attempt was
            # hitting this exact wall, regardless of what credentials
            # were entered. 'magic' is a standard MT4/MT5 concept (a
            # numeric tag identifying which system placed a trade) -
            # any consistent value works here since MetaApi just
            # requires the field to be present.
            "magic": 123456,
        }
        for attempt in range(3):
            try:
                account = await api.metatrader_account_api.create_account(payload)
                return account.id, None
            except Exception as e:
                msg = str(e)
                # REAL FIX, per explicit instruction - MetaApi's own SDK
                # source confirms its ValidationException carries a
                # separate .details field (a specific error code like
                # E_SERVER_TIMEZONE, E_BROKEN_FILE, E_RESOURCE_SLOTS) -
                # str(e) alone only returns the generic message, which
                # literally says "check error.details for more
                # information" without ever actually surfacing it. This
                # was a real, confirmed case: a customer got that exact
                # unhelpful generic message with no way to know what
                # was actually wrong.
                details = getattr(e, "details", None)
                if details:
                    msg = f"{msg} | details: {details}"
                if "retry" in msg.lower() and attempt < 2:
                    print(f"[MT5 PROVISION] Broker detection in progress, waiting 60s (attempt {attempt + 1}/3)...")
                    await asyncio.sleep(60)
                    continue
                print(f"[MT5 PROVISION] Failed for login {login}/{server}: {msg}")
                return None, msg
        return None, "Broker settings detection timed out after 3 attempts."
    except Exception as e:
        return None, str(e)


# ============================================
# MT5 AUTO-TRADE — LIVE EXECUTION (NEW)
# Per explicit instruction, wires the 4 bots (real,
# already-proven strategies) into actual trade
# placement on each CLIENT'S OWN MetaAPI account -
# the first place in this file real trades go
# anywhere other than the bot's single own account.
# ============================================

# Separate cache from _SHARED_MT5_CONNECTION (the bot's OWN account) -
# this one holds one connection per CLIENT account, keyed by their
# metaapi_account_id. Same 15s hard-timeout protection as the shared
# connection, for the exact same reason (a hung connection to one
# client's account must never block the scan job for everyone else).
_CLIENT_MT5_CONNECTIONS = {}

async def get_client_mt5_connection(metaapi_account_id):
    if metaapi_account_id in _CLIENT_MT5_CONNECTIONS:
        return _CLIENT_MT5_CONNECTIONS[metaapi_account_id]
    try:
        async def _connect():
            api = MetaApi(token=METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(account_id=metaapi_account_id)
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            return connection

        connection = await asyncio.wait_for(_connect(), timeout=15)
        _CLIENT_MT5_CONNECTIONS[metaapi_account_id] = connection
        return connection
    except Exception as e:
        print(f"[MT5 AUTOTRADE] Connection failed for client account {metaapi_account_id}: {e}")
        return None


def _reset_client_mt5_connection(metaapi_account_id):
    _CLIENT_MT5_CONNECTIONS.pop(metaapi_account_id, None)


async def get_client_mt5_balance(metaapi_account_id):
    try:
        connection = await get_client_mt5_connection(metaapi_account_id)
        if connection is None:
            return None
        info = await asyncio.wait_for(connection.get_account_information(), timeout=15)
        return info.get("balance")
    except Exception as e:
        print(f"[MT5 AUTOTRADE] Balance lookup failed for {metaapi_account_id}: {e}")
        _reset_client_mt5_connection(metaapi_account_id)
        return None


async def has_client_open_mt5_position(metaapi_account_id, mt5_symbol):
    try:
        connection = await get_client_mt5_connection(metaapi_account_id)
        if connection is None:
            return False
        positions = await asyncio.wait_for(connection.get_positions(), timeout=15)
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        target = mt5_symbol.upper()
        for pos in positions:
            pos_symbol = (pos.get("symbol") if isinstance(pos, dict) else None) or ""
            if pos_symbol.upper().startswith(target) or target.startswith(pos_symbol.upper()):
                return True
        return False
    except Exception as e:
        print(f"[MT5 AUTOTRADE] Position check failed for {metaapi_account_id}: {e}")
        _reset_client_mt5_connection(metaapi_account_id)
        return False


def compute_lot_size(account, entry_price, stop_loss):
    """
    Returns a lot size for this trade based on the account's own
    saved mode - either their fixed lot size directly, or computed
    from their risk % and this trade's real stop distance.

    IMPORTANT CAVEAT, stated plainly rather than hidden in a comment
    only: this uses a SIMPLIFIED position-sizing approximation (stop
    distance as a fraction of entry price, applied against a flat
    $100,000-per-standard-lot notional assumption) - real pip value
    varies meaningfully by instrument (JPY pairs, metals, indices,
    crypto all differ) and by account currency. This is a reasonable
    starting point, not a precision instrument - worth validating
    against a couple of real test trades per instrument before
    trusting it at scale with client money.
    """
    risk_mode = account.get("risk_mode", "lot")
    if risk_mode == "lot":
        return float(account.get("lot_size", 0.01))

    # NOTE: "account_flip" mode does NOT go through this function -
    # it has its own dedicated entry/layering logic in
    # run_account_flip_entry_scan / manage_account_flip_stacks,
    # since layer sizing depends on floating profit within a single
    # open stack, not a flat per-trade calculation like the modes
    # below.

    risk_percent = float(account.get("risk_percent", 1.0))
    balance = account.get("_live_balance")
    if not balance or not entry_price or not stop_loss:
        return 0.01  # safe fallback - smallest reasonable size, never skip sizing silently

    stop_distance_fraction = abs(entry_price - stop_loss) / entry_price
    notional_per_lot = 100000  # standard lot approximation
    risk_amount = balance * (risk_percent / 100.0)
    lot_size = risk_amount / (stop_distance_fraction * notional_per_lot)
    return round(max(0.01, min(lot_size, 10.0)), 2)  # hard-capped 0.01-10.0 as a sanity bound


async def place_client_mt5_trade(metaapi_account_id, mt5_symbol, direction, volume, stop_loss, take_profit):
    """
    Generalizes place_mt5_trade for an arbitrary CLIENT account
    rather than the bot's single own account - same proven REST
    approach (no websocket subscription needed for trade placement).

    NOTE: hardcodes the "london" MetaAPI region, same as the bot's
    own place_mt5_trade - client accounts could in principle be
    provisioned in a different region, which would show up as a
    trade failure here. Worth watching for in the logs; if it comes
    up, this needs a per-account region lookup rather than an
    assumption.
    """
    if not METAAPI_TOKEN:
        return None
    try:
        headers = {"auth-token": METAAPI_TOKEN, "Content-Type": "application/json"}
        order_type = "ORDER_TYPE_BUY" if direction == "BUY" else "ORDER_TYPE_SELL"
        payload = {
            "symbol": mt5_symbol,
            "volume": volume,
            "actionType": order_type,
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "comment": "NexoraAI AutoTrade",
        }
        url = (
            f"https://mt-client-api-v1.london.agiliumtrade.ai"
            f"/users/current/accounts/{metaapi_account_id}/trade"
        )
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code in (200, 201):
            result = response.json()
            order_id = result.get("orderId", "unknown")
            print(f"[MT5 AUTOTRADE] ✅ Trade placed for {metaapi_account_id} — Order ID: {order_id}")
            return order_id
        print(f"[MT5 AUTOTRADE] ❌ Trade failed for {metaapi_account_id}: {response.status_code} {response.text}")
        return None
    except Exception as e:
        print(f"[MT5 AUTOTRADE] ❌ Exception placing trade for {metaapi_account_id}: {e}")
        return None


async def get_client_mt5_positions_for_symbol(metaapi_account_id, mt5_symbol):
    """
    Returns the list of currently open MetaTrader positions for one
    symbol on a client account (raw position dicts - id, currentPrice,
    profit, volume, etc. straight from MetaAPI). Used by Account
    Flip's stack manager to check floating price/profit; deliberately
    separate from has_client_open_mt5_position (which only returns a
    bool) since the stack manager needs the actual numbers.
    """
    try:
        connection = await get_client_mt5_connection(metaapi_account_id)
        if connection is None:
            return []
        positions = await asyncio.wait_for(connection.get_positions(), timeout=15)
        if isinstance(positions, dict):
            positions = positions.get("positions", [])
        target = mt5_symbol.upper()
        return [
            p for p in positions
            if isinstance(p, dict) and (
                p.get("symbol", "").upper().startswith(target)
                or target.startswith(p.get("symbol", "").upper())
            )
        ]
    except Exception as e:
        print(f"[ACCOUNT FLIP] Position fetch failed for {metaapi_account_id}: {e}")
        _reset_client_mt5_connection(metaapi_account_id)
        return []


async def close_all_client_mt5_positions(metaapi_account_id, mt5_symbol):
    """
    Closes every open position on one symbol for one client account in
    a single call (MetaAPI's POSITIONS_CLOSE_SYMBOL action) - this is
    how Account Flip closes an entire layered stack at once when the
    trailing stop triggers, rather than closing each layer
    individually. Confirmed against MetaAPI's live REST trade docs
    (metaapi.cloud/docs/client/restApi/api/trade) - same endpoint
    place_client_mt5_trade already uses successfully.
    """
    if not METAAPI_TOKEN:
        return False
    try:
        headers = {"auth-token": METAAPI_TOKEN, "Content-Type": "application/json"}
        payload = {"actionType": "POSITIONS_CLOSE_SYMBOL", "symbol": mt5_symbol}
        url = (
            f"https://mt-client-api-v1.london.agiliumtrade.ai"
            f"/users/current/accounts/{metaapi_account_id}/trade"
        )
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        if response.status_code in (200, 201):
            print(f"[ACCOUNT FLIP] ✅ Closed full stack for {metaapi_account_id} on {mt5_symbol}")
            return True
        print(f"[ACCOUNT FLIP] ❌ Close-stack failed for {metaapi_account_id}: {response.status_code} {response.text}")
        return False
    except Exception as e:
        print(f"[ACCOUNT FLIP] ❌ Exception closing stack for {metaapi_account_id}: {e}")
        return False


def get_open_flip_stack(user_id):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/mt5_flip_stacks"
            f"?user_id=eq.{user_id}&status=eq.OPEN&select=*&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        rows = response.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[ACCOUNT FLIP] Error fetching open stack for {user_id}: {e}")
        return None


def get_all_open_flip_stacks():
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_flip_stacks?status=eq.OPEN&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[ACCOUNT FLIP] Error fetching open stacks: {e}")
        return []


def create_flip_stack(user_id, metaapi_account_id, pair_key, mt5_symbol, direction, entry_price, stop_loss):
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_flip_stacks"
        payload = {
            "user_id": str(user_id),
            "metaapi_account_id": metaapi_account_id,
            "pair_key": pair_key,
            "mt5_symbol": mt5_symbol,
            "direction": direction,
            "layer_count": 1,
            "last_layer_price": entry_price,
            "peak_price": entry_price,
            "initial_stop_loss": stop_loss,
            "status": "OPEN",
        }
        requests.post(url, headers=sb_headers(), json=payload, timeout=10)
    except Exception as e:
        print(f"[ACCOUNT FLIP] Error creating stack for {user_id}: {e}")


def update_flip_stack(stack_id, fields):
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_flip_stacks?id=eq.{stack_id}"
        requests.patch(url, headers=sb_headers(), json=fields, timeout=10)
    except Exception as e:
        print(f"[ACCOUNT FLIP] Error updating stack {stack_id}: {e}")


def get_all_active_mt5_autotrade_accounts():
    try:
        now_iso = datetime.utcnow().isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/mt5_auto_trade_accounts"
            f"?is_active=eq.true&subscription_expires_at=gt.{now_iso}"
            f"&metaapi_account_id=not.is.null&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[MT5 AUTOTRADE] Error fetching active accounts: {e}")
        return []


# Tracks (user_id, signal_source_id) pairs already copied, so the
# scan doesn't re-fire the same signal on every tick while it's still
# "fresh". Reset naturally over time as old entries stop matching new
# signals - not persisted across restarts, matching the same
# reasoning as NOTIFIED_EVENTS_TODAY elsewhere in this file.
MT5_AUTOTRADE_COPIED_SIGNALS = set()


async def run_mt5_autotrade_bot_scan(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 5 minutes. For every unique (bot, pair) combination
    actually in use by an active subscriber, runs that bot's real
    strategy function once and copies any fresh signal to every
    subscriber using that exact bot+pair - one shared strategy check
    per combination, not one per user, matching the same efficiency
    reasoning as every other scan job in this file.
    """
    accounts = get_all_active_mt5_autotrade_accounts()
    bot_accounts = [
        a for a in accounts
        if a.get("bot_choice") not in ("follow_channel", "account_flip") and a.get("pair_choice")
    ]
    if not bot_accounts:
        return

    combos = {}
    for account in bot_accounts:
        key = (account["bot_choice"], account["pair_choice"])
        combos.setdefault(key, []).append(account)

    for (bot_key, pair_key), subscribers in combos.items():
        bot_info = MT5_AUTOTRADE_BOTS.get(bot_key)
        pair_config = PAIR_CONFIG.get(pair_key)
        if not bot_info or not pair_config:
            continue

        strategy_fns = []
        for fn_name in bot_info["strategy_functions"]:
            fn = globals().get(fn_name)
            if not fn:
                print(f"[MT5 AUTOTRADE] ⚠️ Strategy function {fn_name} not found.")
                continue
            strategy_fns.append(fn)
        if not strategy_fns:
            continue

        try:
            # Primary analysis timeframe is now the bot's own setting
            # (1m for Aggressive, 5m for Conservative), per explicit
            # instruction - fed into the same h1_candles slot every
            # strategy function already reads from, so no strategy
            # needed rewriting to support this. h4/daily are still
            # fetched normally in case a strategy also leans on the
            # genuinely-higher-timeframe context for trend alignment.
            primary_candles = get_cached_candles(pair_key, pair_config, bot_info["timeframe"], outputsize=210)
            h4_candles = get_cached_candles(pair_key, pair_config, "4h", outputsize=60)
            daily_candles = get_cached_candles(pair_key, pair_config, "1day", outputsize=10)
            if not primary_candles:
                continue

            # Two-tier per explicit instruction, mirroring the exact
            # filter/entry split already built for channel/manual
            # forex signals - a lagging trend-confirming strategy
            # (Trend Following, Heikin-Ashi, Supertrend, Parabolic SAR,
            # EMA Ribbon) can no longer trigger a trade alone; it only
            # sets the direction lean, and a real entry-tier trigger
            # in that same direction is required to actually fire.
            #
            # FALLBACK, per explicit instruction ("leave a gap so we
            # don't miss any auto-trade calls"): if this bot's own
            # strategy list doesn't have a genuine mix of both roles,
            # or the two-tier check finds nothing this round, this
            # falls straight through to the rule-based price-trend
            # fallback below - per explicit instruction, the flat "2+
            # of everything must agree" middle tier that used to sit
            # here has been removed entirely, not just deprioritized.
            filter_fns, entry_fns = split_strategies_by_role(strategy_fns)
            direction = None
            winning_votes = []

            if filter_fns and entry_fns:
                filter_votes = []
                for fn in filter_fns:
                    try:
                        result = fn(pair_key, pair_config, primary_candles, h4_candles, daily_candles)
                        if result:
                            filter_votes.append(result)
                    except Exception as e:
                        print(f"[MT5 AUTOTRADE] filter {fn.__name__} failed for {pair_key}: {e}")

                f_buy = [v for v in filter_votes if v["direction"] == "BUY"]
                f_sell = [v for v in filter_votes if v["direction"] == "SELL"]
                if filter_votes and len(f_buy) != len(f_sell):
                    filter_direction = "BUY" if len(f_buy) > len(f_sell) else "SELL"
                    entry_votes = []
                    for fn in entry_fns:
                        try:
                            result = fn(pair_key, pair_config, primary_candles, h4_candles, daily_candles)
                            if result:
                                entry_votes.append(result)
                        except Exception as e:
                            print(f"[MT5 AUTOTRADE] entry {fn.__name__} failed for {pair_key}: {e}")
                    matching_entries = [v for v in entry_votes if v["direction"] == filter_direction]
                    if matching_entries:
                        direction = filter_direction
                        winning_votes = matching_entries

            if not direction:
                # THIRD tier, per explicit instruction (confirmed
                # understanding real money would now act on a
                # weaker, non-strategy-backed signal here) - the same
                # rule-based price-trend fallback already used for
                # manual/scheduled signals (forex and, since the last
                # build, Deriv too), now also applied to real-money
                # Auto-Trade specifically because that was explicitly
                # asked for and confirmed, not because it's the
                # default policy for every automated path (Auto-Copy
                # is untouched - it has no bank of its own, it purely
                # mirrors whatever the scheduled channel signal
                # already decided).
                if primary_candles and len(primary_candles) >= 2:
                    current_price = primary_candles[-1]["close"]
                    price_1h_ago = primary_candles[-2]["close"]
                    fallback_direction, fallback_reason = generate_rule_based_bias(
                        pair_key, current_price, price_1h_ago
                    )
                    if fallback_direction:
                        direction = fallback_direction
                        winning_votes = [{
                            "strategy_name": "Rule-Based Trend Fallback",
                            "direction": direction,
                            "detail": fallback_reason,
                        }]

            if not direction:
                print(f"[MT5 AUTOTRADE] {bot_key}/{pair_key} - no qualifying setup (two-tier, fallback, or rule-based) this round")
                continue

            vote = winning_votes[0]

            # EXPERIMENTAL flip, per explicit instruction - see
            # EXPERIMENTAL_INVERT_SIGNALS above. Applied after the
            # 2-vote gate and its logging above (so that log still
            # shows the TRUE, un-flipped consensus for later review) -
            # only the direction actually traded on is inverted.
            direction = maybe_invert_direction(direction)

            entry_price = primary_candles[-1]["close"]
            atr = _accum_zone_atr(primary_candles, 14) if len(primary_candles) > 15 else None
            if not atr:
                continue
            if direction == "BUY":
                stop_loss = entry_price - atr * 1.5
                take_profit = entry_price + atr * 3.0
            else:
                stop_loss = entry_price + atr * 1.5
                take_profit = entry_price - atr * 3.0

            signal_marker = f"{bot_key}_{pair_key}_{int(time.time() // 300)}"  # 5-min bucket

            for account in subscribers:
                user_id = account["user_id"]
                metaapi_account_id = account["metaapi_account_id"]
                copy_key = (user_id, signal_marker)
                if copy_key in MT5_AUTOTRADE_COPIED_SIGNALS:
                    continue

                if await has_client_open_mt5_position(metaapi_account_id, pair_config["mt5_symbol"]):
                    continue

                if account.get("risk_mode") == "percent":
                    balance = await get_client_mt5_balance(metaapi_account_id)
                    account["_live_balance"] = balance

                volume = compute_lot_size(account, entry_price, stop_loss)

                order_id = await place_client_mt5_trade(
                    metaapi_account_id, pair_config["mt5_symbol"], direction,
                    volume, stop_loss, take_profit
                )
                MT5_AUTOTRADE_COPIED_SIGNALS.add(copy_key)
                if order_id:
                    log_mt5_autotrade_order(user_id, metaapi_account_id, order_id, pair_config["display"], direction)
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                f"🤖 <b>{bot_info['label']} — {direction} {pair_config['display']}</b>\n\n"
                                f"Entry: {entry_price:.4f} | SL: {stop_loss:.4f} | TP: {take_profit:.4f}\n"
                                f"Volume: {volume} lots"
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        print(f"[MT5 AUTOTRADE] Couldn't notify {user_id}: {e}")
        except Exception as e:
            print(f"[MT5 AUTOTRADE] ❌ Scan error for {bot_key}/{pair_key}: {e}")


async def run_mt5_autotrade_follow_channel_scan(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 2 minutes. For "Full Signal Coverage" subscribers,
    copies whatever the channel itself already posted for XAUUSD,
    GBPJPY, or BTCUSD - reuses signal_log (the same table every
    channel post already writes to) rather than re-deciding anything,
    so these clients trade exactly what the channel shows, nothing
    invented separately for them.
    """
    accounts = get_all_active_mt5_autotrade_accounts()
    followers = [a for a in accounts if a.get("bot_choice") == "follow_channel"]
    if not followers:
        return

    try:
        cutoff = (datetime.utcnow() - timedelta(minutes=10)).isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/signal_log"
            f"?posted_at=gt.{cutoff}&select=*&order=posted_at.desc&limit=10"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        recent_signals = response.json()
    except Exception as e:
        print(f"[MT5 AUTOTRADE FOLLOW] Error fetching recent signals: {e}")
        return

    for signal in recent_signals:
        pair_name = signal.get("pair_name", "")
        pair_key = next((k for k, v in PAIR_CONFIG.items() if v.get("pair_name") == pair_name), None)
        if not pair_key:
            continue
        pair_config = PAIR_CONFIG[pair_key]
        signal_id = signal.get("id")

        for account in followers:
            user_id = account["user_id"]
            metaapi_account_id = account["metaapi_account_id"]
            copy_key = (user_id, f"channel_{signal_id}")
            if copy_key in MT5_AUTOTRADE_COPIED_SIGNALS:
                continue

            if await has_client_open_mt5_position(metaapi_account_id, pair_config["mt5_symbol"]):
                MT5_AUTOTRADE_COPIED_SIGNALS.add(copy_key)
                continue

            entry_price = signal.get("entry_price")
            stop_loss = signal.get("stop_loss")
            take_profit = signal.get("take_profit")
            direction = signal.get("direction")
            if not all([entry_price, stop_loss, take_profit, direction]):
                continue

            if account.get("risk_mode") == "percent":
                balance = await get_client_mt5_balance(metaapi_account_id)
                account["_live_balance"] = balance

            volume = compute_lot_size(account, entry_price, stop_loss)

            order_id = await place_client_mt5_trade(
                metaapi_account_id, pair_config["mt5_symbol"], direction,
                volume, stop_loss, take_profit
            )
            MT5_AUTOTRADE_COPIED_SIGNALS.add(copy_key)
            if order_id:
                log_mt5_autotrade_order(user_id, metaapi_account_id, order_id, pair_config["display"], direction)
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"📡 <b>Channel Signal Copied — {direction} {pair_config['display']}</b>\n\n"
                            f"Entry: {entry_price} | SL: {stop_loss} | TP: {take_profit}\n"
                            f"Volume: {volume} lots"
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[MT5 AUTOTRADE FOLLOW] Couldn't notify {user_id}: {e}")


async def run_account_flip_entry_scan(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 5 minutes on the M15 timeframe. For every Account Flip
    subscriber with NO currently open stack, checks their one chosen
    pair for a fresh price-action signal (account_flip_signal - the
    dedicated Engulfing/Pin Bar/Inside Bar pool, deliberately separate
    from every other bot's indicator strategies). If one fires, opens
    the FIRST layer with a real broker-side stop loss and starts a new
    row in mt5_flip_stacks for manage_account_flip_stacks to take over
    from there.
    """
    accounts = get_all_active_mt5_autotrade_accounts()
    flip_accounts = [a for a in accounts if a.get("bot_choice") == "account_flip" and a.get("pair_choice")]
    if not flip_accounts:
        return

    combos = {}
    for account in flip_accounts:
        combos.setdefault(account["pair_choice"], []).append(account)

    for pair_key, subscribers in combos.items():
        pair_config = PAIR_CONFIG.get(pair_key)
        if not pair_config:
            continue
        try:
            candles = get_cached_candles(pair_key, pair_config, "15min", outputsize=210)
            if not candles or len(candles) < 5:
                continue

            vote = account_flip_signal(pair_key, pair_config, candles)
            if not vote:
                continue

            direction = vote["direction"]
            entry_price = candles[-1]["close"]
            invalidation = vote["invalidation"]
            # Small buffer beyond the pattern's own invalidation point,
            # using the pair's own pip_size so it scales sensibly per
            # instrument rather than a flat price offset.
            buffer_dist = pair_config["pip_size"] * 0.2
            if direction == "BUY":
                stop_loss = min(invalidation - buffer_dist, entry_price - pair_config["pip_size"] * 0.5)
            else:
                stop_loss = max(invalidation + buffer_dist, entry_price + pair_config["pip_size"] * 0.5)

            signal_marker = f"accountflip_{pair_key}_{int(time.time() // 300)}"

            for account in subscribers:
                user_id = account["user_id"]
                metaapi_account_id = account["metaapi_account_id"]
                copy_key = (user_id, signal_marker)
                if copy_key in MT5_AUTOTRADE_COPIED_SIGNALS:
                    continue

                if get_open_flip_stack(user_id):
                    continue  # already riding a stack - entry scan sits out until it closes

                if await has_client_open_mt5_position(metaapi_account_id, pair_config["mt5_symbol"]):
                    continue

                base_lot = float(account.get("flip_base_lot") or 0.01)
                order_id = await place_client_mt5_trade(
                    metaapi_account_id, pair_config["mt5_symbol"], direction,
                    base_lot, stop_loss, None
                )
                MT5_AUTOTRADE_COPIED_SIGNALS.add(copy_key)
                if order_id:
                    create_flip_stack(
                        user_id, metaapi_account_id, pair_key, pair_config["mt5_symbol"],
                        direction, entry_price, stop_loss
                    )
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                f"🚀 <b>Account Flip — {direction} {pair_config['display']}</b>\n\n"
                                f"Pattern: {vote['strategy']}\n"
                                f"Entry: {entry_price:.4f} | SL: {stop_loss:.4f}\n"
                                f"Volume: {base_lot} lots (layer 1)\n\n"
                                f"No take-profit set - this position rides on a trailing "
                                f"stop across the whole stack as layers get added."
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        print(f"[ACCOUNT FLIP] Couldn't notify {user_id}: {e}")
        except Exception as e:
            print(f"[ACCOUNT FLIP] ❌ Entry scan error for {pair_key}: {e}")


async def manage_account_flip_stacks(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 1 minute. For every OPEN stack: checks live floating
    price, adds another layer once price has moved flip_trigger_pips
    further in favor since the last layer (capped at flip_max_layers),
    tracks the best price seen (peak_price) to run a trailing stop,
    and closes the ENTIRE stack at once (all layers together) if
    price pulls back through the trail - but only once the stack has
    reached the trigger distance at least once (trigger_reached),
    so a brand-new single-layer position isn't closed by trail logic
    before it's ever shown real profit; before that point the
    original broker-side stop loss on layer 1 is its only protection.
    """
    stacks = get_all_open_flip_stacks()
    if not stacks:
        return

    for stack in stacks:
        try:
            stack_id = stack["id"]
            user_id = stack["user_id"]
            metaapi_account_id = stack["metaapi_account_id"]
            pair_key = stack["pair_key"]
            mt5_symbol = stack["mt5_symbol"]
            direction = stack["direction"]
            pair_config = PAIR_CONFIG.get(pair_key)
            if not pair_config:
                continue

            account = get_mt5_autotrade_account(user_id)
            if not account or account.get("bot_choice") != "account_flip":
                continue  # user switched modes mid-stack - stop managing it further

            positions = await get_client_mt5_positions_for_symbol(metaapi_account_id, mt5_symbol)
            if not positions:
                # Closed already (SL hit, or manually closed) - reconcile and stop.
                update_flip_stack(stack_id, {"status": "CLOSED", "closed_at": datetime.utcnow().isoformat()})
                try:
                    await context.bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"🚀 <b>Account Flip stack closed</b> — {pair_config['display']}\n\n"
                            f"Position(s) no longer open on the account (stop loss hit, or closed "
                            f"manually). A new stack can start on the next fresh signal."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[ACCOUNT FLIP] Couldn't notify {user_id} of stack close: {e}")
                continue

            current_price = positions[0].get("currentPrice")
            if not current_price:
                continue

            pip_size = pair_config["pip_size"]
            trigger_pips = float(account.get("flip_trigger_pips") or 10)
            trail_pips = float(account.get("flip_trail_pips") or 10)
            max_layers = int(account.get("flip_max_layers") or 3)
            step = float(account.get("flip_step") or 0.01)
            max_lot = float(account.get("flip_max_lot") or account.get("flip_base_lot") or 0.01)

            layer_count = stack["layer_count"]
            last_layer_price = float(stack["last_layer_price"])
            peak_price = float(stack["peak_price"])
            trigger_reached = stack.get("trigger_reached", False)

            favorable_move = (
                (current_price - last_layer_price) if direction == "BUY"
                else (last_layer_price - current_price)
            )
            favorable_pips = favorable_move / pip_size

            # Update peak (best price seen so far in the favorable direction)
            if direction == "BUY":
                peak_price = max(peak_price, current_price)
            else:
                peak_price = min(peak_price, current_price)

            peak_favorable_pips = (
                (peak_price - stack["last_layer_price"]) if direction == "BUY"
                else (stack["last_layer_price"] - peak_price)
            ) / pip_size

            updates = {"peak_price": peak_price}

            if peak_favorable_pips >= trigger_pips:
                trigger_reached = True
                updates["trigger_reached"] = True

            # Add a new layer once price has moved far enough in favor
            # since the LAST layer specifically (not just off the peak) -
            # keeps layer spacing consistent even if price has been
            # choppy rather than moving in a straight line.
            if favorable_pips >= trigger_pips and layer_count < max_layers:
                new_lot = round(min(float(account.get("flip_base_lot") or 0.01) + step * layer_count, max_lot), 2)
                order_id = await place_client_mt5_trade(
                    metaapi_account_id, mt5_symbol, direction, new_lot, None, None
                )
                if order_id:
                    layer_count += 1
                    updates["layer_count"] = layer_count
                    updates["last_layer_price"] = current_price
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                f"🚀 <b>Account Flip — layer {layer_count} added</b> "
                                f"({pair_config['display']})\n\n"
                                f"Added {new_lot} lots at {current_price:.4f} — "
                                f"{favorable_pips:.1f} pips in favor since the last layer."
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        print(f"[ACCOUNT FLIP] Couldn't notify {user_id} of new layer: {e}")

            # Trailing-stop close check - only once the stack has shown
            # real profit at least once (trigger_reached).
            if trigger_reached:
                trail_price = (
                    peak_price - trail_pips * pip_size if direction == "BUY"
                    else peak_price + trail_pips * pip_size
                )
                pulled_back = (
                    current_price <= trail_price if direction == "BUY"
                    else current_price >= trail_price
                )
                if pulled_back:
                    total_profit = sum(p.get("profit", 0) or 0 for p in positions)
                    await close_all_client_mt5_positions(metaapi_account_id, mt5_symbol)
                    update_flip_stack(stack_id, {
                        "status": "CLOSED",
                        "total_profit": total_profit,
                        "closed_at": datetime.utcnow().isoformat(),
                    })
                    try:
                        await context.bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                f"🚀 <b>Account Flip — trailing stop closed the stack</b> "
                                f"({pair_config['display']})\n\n"
                                f"{layer_count} layer(s) closed together at {current_price:.4f}.\n"
                                f"Total P&L: {total_profit:.2f}\n\n"
                                f"A new stack can start on the next fresh signal."
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        print(f"[ACCOUNT FLIP] Couldn't notify {user_id} of stack close: {e}")
                    continue

            update_flip_stack(stack_id, updates)
        except Exception as e:
            print(f"[ACCOUNT FLIP] ❌ Stack manager error for stack {stack.get('id')}: {e}")


async def korapay_webhook_handler(request):
    """
    Receives KoraPay's payment confirmation. Deliberately does the
    MINIMUM here - verify the signature, update the database - and
    nothing else. Never calls the Telegram bot directly from this
    handler (it runs in its own thread/event loop, separate from the
    bot's own - see run_korapay_webhook_server's docstring). The
    actual user notification and subscription activation happens
    separately, in process_confirmed_korapay_payments, which runs on
    the bot's own proven event loop via job_queue.
    """
    try:
        payload = await request.json()
    except Exception:
        return web.Response(status=400, text="bad request")

    signature = request.headers.get("x-korapay-signature", "")
    data = payload.get("data", {})
    if not verify_korapay_signature(data, signature):
        print("[KORAPAY WEBHOOK] ❌ Signature verification failed - ignoring, possible spoofed request.")
        return web.Response(status=401, text="invalid signature")

    if payload.get("event") == "charge.success":
        reference = data.get("reference") or data.get("payment_reference")
        print(f"[KORAPAY WEBHOOK] ✅ Payment confirmed for reference {reference}")
        try:
            url = f"{SUPABASE_URL}/rest/v1/korapay_transactions?reference=eq.{reference}"
            requests.patch(url, headers=sb_headers(), json={
                "status": "success",
                "confirmed_at": datetime.utcnow().isoformat(),
            }, timeout=10)
        except Exception as e:
            print(f"[KORAPAY WEBHOOK] DB update failed: {e}")

    return web.Response(status=200, text="ok")


def render_deriv_oauth_page(heading, message, kind="info", emoji=None):
    """
    Shared styled page for every deriv_oauth_callback_handler response
    below - added after real user confusion (tiny, unstyled system-
    default text on mobile Safari, since none of these had a viewport
    meta tag or any CSS at all before). One template, one visual
    language (color + icon by kind) across all 5 outcomes instead of
    5 different bare <h2>/<p> snippets. Also includes a real tappable
    "Back to Telegram" button (deep link to the bot chat) - every one
    of these pages already told the person to go back to Telegram in
    plain text; this makes that an actual one-tap action instead of
    something they have to do manually (switch apps, find the chat).
    """
    colors = {
        "success": "#16a34a", "error": "#dc2626",
        "warning": "#d97706", "info": "#2563eb",
    }
    icons = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}
    color = colors.get(kind, colors["info"])
    icon = emoji or icons.get(kind, icons["info"])
    telegram_url = f"https://t.me/{BOT_USERNAME}"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{heading}</title>
<style>
  body {{
    margin: 0; padding: 32px 20px; min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    background: #f4f5f7;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    box-sizing: border-box;
  }}
  .card {{
    max-width: 420px; width: 100%; background: #ffffff;
    border-radius: 16px; padding: 32px 24px;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
    text-align: center;
  }}
  .icon {{ font-size: 48px; line-height: 1; margin-bottom: 16px; }}
  h1 {{
    font-size: 22px; font-weight: 700; margin: 0 0 12px;
    color: {color};
  }}
  p {{
    font-size: 17px; line-height: 1.5; color: #374151;
    margin: 0 0 8px;
  }}
  .bar {{
    height: 4px; width: 48px; background: {color};
    border-radius: 2px; margin: 0 auto 20px;
  }}
  .btn {{
    display: inline-block; margin-top: 20px;
    background: {color}; color: #ffffff !important;
    text-decoration: none; font-weight: 600; font-size: 16px;
    padding: 14px 28px; border-radius: 12px;
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="icon">{icon}</div>
    <div class="bar"></div>
    <h1>{heading}</h1>
    <p>{message}</p>
    <a class="btn" href="{telegram_url}">↩ Back to Telegram</a>
  </div>
  <script>
    // Auto-redirect on the success page specifically, after a short
    // pause so the checkmark is actually visible first rather than
    // vanishing instantly - the button above still covers every case
    // where a browser blocks the auto-redirect (Safari sometimes
    // does), so nobody gets stuck either way.
    {"setTimeout(function(){ window.location.href = '" + telegram_url + "'; }, 1800);" if kind == "success" else ""}
  </script>
</body>
</html>"""


async def deriv_oauth_callback_handler(request):
    """
    Deriv redirects here after a user logs in via the modern PKCE flow
    (auth.deriv.com). Unlike the old legacy-flow attempt, Deriv sends
    back a temporary ?code=...&state=... - not a token directly - so
    this handler does the full exchange itself (resolve state -> user
    + code_verifier, trade the code for a real access_token, look up
    the real account's loginid) before handing off to the pending
    table. Still follows the same rule as korapay_webhook_handler:
    never talks to Telegram directly from here - that happens in
    process_pending_deriv_oauth_connections, on the bot's own event
    loop.
    """
    params = request.query
    state = params.get("state", "")
    code = params.get("code", "")

    if state:
        user_id, code_verifier, prompt_chat_id, prompt_message_id = resolve_deriv_oauth_state(state)
    else:
        user_id, code_verifier, prompt_chat_id, prompt_message_id = None, None, None, None
    if not user_id or not code_verifier:
        return web.Response(
            status=400, content_type="text/html",
            text=render_deriv_oauth_page(
                "This login link has expired or was already used.",
                "Go back to Telegram and tap Connect Deriv again.",
                kind="warning",
            )
        )

    if not code:
        error = params.get("error_description") or params.get("error") or "unknown error"
        print(f"[DERIV OAUTH] No code in callback for user {user_id}: {error}")
        return web.Response(
            status=200, content_type="text/html",
            text=render_deriv_oauth_page(
                "Login didn't complete.",
                f"{error}<br>Go back to Telegram and try again.",
                kind="error",
            )
        )

    access_token = exchange_deriv_oauth_code(code, code_verifier)
    if not access_token:
        return web.Response(
            status=200, content_type="text/html",
            text=render_deriv_oauth_page(
                "Something went wrong finishing the login.",
                "Go back to Telegram and try again, or paste an API token instead.",
                kind="error",
            )
        )

    accounts_data = await deriv_get_options_accounts(access_token)
    accounts_list = None
    if accounts_data:
        accounts_list = accounts_data.get("data")
        if not isinstance(accounts_list, list):
            accounts_list = accounts_data.get("accounts")

    real_loginid, real_currency = None, None
    if accounts_list:
        for acct in accounts_list:
            is_virtual = bool(
                acct.get("is_virtual")
                or str(acct.get("account_type", "")).lower() in ("demo", "virtual")
            )
            if not is_virtual:
                real_loginid = acct.get("loginid") or acct.get("account_id") or acct.get("id")
                real_currency = str(acct.get("currency", "")).upper()
                break

    if not real_loginid:
        return web.Response(
            status=200, content_type="text/html",
            text=render_deriv_oauth_page(
                "No real Deriv account found.",
                "Only demo accounts were found on this login. Go back to Telegram, and "
                "either paste a real-account API token manually, or log in with an "
                "account that has a real Deriv account too.",
                kind="warning",
            )
        )

    save_pending_deriv_oauth_connection(
        user_id, real_loginid, access_token, real_currency,
        prompt_chat_id, prompt_message_id
    )

    return web.Response(
        status=200, content_type="text/html",
        text=render_deriv_oauth_page(
            "Deriv account connected!",
            "Go back to Telegram - you'll get a confirmation message there in a few seconds.",
            kind="success",
        )
    )


def run_korapay_webhook_server():
    """
    Runs a small, INDEPENDENT web server in its own background thread
    with its OWN event loop - deliberately NOT sharing the bot's main
    asyncio loop (which app.run_polling() manages internally, and
    which this whole bot's Telegram handling depends on completely).
    Keeping these fully decoupled means nothing here can interfere
    with Telegram polling, and Telegram polling can't block webhook
    delivery. Requires this Railway service to be exposed publicly
    (Settings -> Networking -> Generate Domain) - it's never needed
    an inbound HTTP endpoint before now.
    """
    webhook_app = web.Application()
    webhook_app.router.add_post("/korapay-webhook", korapay_webhook_handler)
    webhook_app.router.add_get("/deriv-oauth-callback", deriv_oauth_callback_handler)
    port = int(os.getenv("PORT", 8080))
    print(f"[KORAPAY WEBHOOK] Starting webhook server on port {port}...")
    # handle_signals=False - CONFIRMED REAL CRASH via live logs:
    # aiohttp's run_app() tries to register a SIGINT handler by
    # default (for graceful Ctrl-C shutdown), but Python only allows
    # signal handlers to be set from the main thread - and this
    # deliberately runs in a background thread (see docstring above).
    # Without this, the whole thread crashed with "set_wakeup_fd only
    # works in main thread of the main interpreter" every time.
    web.run_app(webhook_app, host="0.0.0.0", port=port, print=None, handle_signals=False)


async def check_mt5_autotrade_expiry(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs once daily. Per explicit instruction: clients are
    automatically removed once their subscription expires - no
    manual intervention needed. Sets is_active=false (auto-trading
    stops immediately) and notifies the user, rather than silently
    letting an expired subscription keep trading.
    """
    try:
        now_iso = datetime.utcnow().isoformat()
        url = (
            f"{SUPABASE_URL}/rest/v1/mt5_auto_trade_accounts"
            f"?is_active=eq.true&subscription_expires_at=lt.{now_iso}&select=user_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        expired_accounts = response.json()
    except Exception as e:
        print(f"[MT5 AUTOTRADE EXPIRY] Error fetching expired accounts: {e}")
        return

    if not expired_accounts:
        return

    bot = context.bot
    for row in expired_accounts:
        user_id = row.get("user_id")
        if not user_id:
            continue
        try:
            upsert_mt5_autotrade_account(user_id, {"is_active": False})
            print(f"[MT5 AUTOTRADE EXPIRY] ⏸️ Deactivated expired subscription for {user_id}")
            await bot.send_message(
                chat_id=int(user_id),
                text=(
                    "⏸️ <b>Your Exness Auto-Trade subscription has expired.</b>\n\n"
                    "Auto-trading has been paused. Tap 🤖 Exness Auto-Trade "
                    "to renew."
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[MT5 AUTOTRADE EXPIRY] ❌ Failed to deactivate/notify {user_id}: {e}")


async def process_confirmed_korapay_payments(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every ~20 seconds on the bot's OWN event loop (unlike the
    webhook handler above). Picks up payments the webhook already
    confirmed in the database, activates the subscription, and
    notifies the user - safe to call bot.send_message here since this
    runs through job_queue, not the separate webhook thread.
    """
    transactions = get_unprocessed_confirmed_transactions()
    if not transactions:
        return

    bot = context.bot
    for txn in transactions:
        user_id = txn.get("user_id")
        reference = txn.get("reference")
        if not user_id or not reference:
            continue
        try:
            expires_at = (datetime.utcnow() + timedelta(days=MT5_SUBSCRIPTION_DAYS)).isoformat()
            account = get_mt5_autotrade_account(user_id)
            upsert_mt5_autotrade_account(user_id, {"subscription_expires_at": expires_at})
            mark_korapay_transaction_processed(reference)
            print(f"[MT5 AUTOTRADE] ✅ Subscription activated for {user_id} until {expires_at}")

            if account and account.get("metaapi_account_id"):
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        "✅ <b>Payment confirmed!</b>\n\n"
                        f"Your MT5 auto-trade subscription is active "
                        f"for {MT5_SUBSCRIPTION_DAYS} more days.\n\n"
                        "Keep using your current trading account, or "
                        "connect a different one?"
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("✅ Keep current account", callback_data="mt5renew_keep")],
                        [InlineKeyboardButton("🔄 Connect a new account", callback_data="mt5renew_new")],
                    ])
                )
            else:
                user_modes[user_id] = "mt5_awaiting_account_number"
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        "✅ <b>Payment confirmed!</b>\n\n"
                        "Now let's connect your Exness MT5/MT4 account.\n\n"
                        "Send your <b>account number</b>:"
                    ),
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            print(f"[MT5 AUTOTRADE] ❌ Failed to process payment for {user_id}: {e}")


async def process_pending_deriv_oauth_connections(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every ~20 seconds on the bot's OWN event loop, mirroring
    process_confirmed_korapay_payments above - picks up whatever
    deriv_oauth_callback_handler already resolved and wrote to the
    pending table, saves it through save_deriv_account (the EXACT
    same function the manual-token flow uses - an OAuth-issued
    access_token is stored and used identically to a manually-pasted
    one, confirmed via this bot's own existing Deriv integration
    already being Bearer-token-based end to end), and confirms to the
    user. Safe to call bot.send_message here since this runs through
    job_queue, not the separate webhook thread.
    """
    pending = get_unprocessed_deriv_oauth_connections()
    if not pending:
        return

    bot = context.bot
    for row in pending:
        row_id = row.get("id")
        user_id = row.get("user_id")
        loginid = row.get("loginid")
        token = row.get("token")
        currency = row.get("currency")
        prompt_chat_id = row.get("prompt_chat_id")
        prompt_message_id = row.get("prompt_message_id")
        if not all([row_id, user_id, loginid, token]):
            continue

        try:
            saved = save_deriv_account(user_id, loginid, token, currency, auth_method="oauth")
            mark_deriv_oauth_connection_processed(row_id)
            if saved:
                # Clean up the "Login with Deriv" prompt now that it's
                # done its job - per explicit instruction, leaving a
                # still-tappable login button visible after a
                # successful connection risks a newbie tapping it
                # again out of confusion.
                if prompt_chat_id and prompt_message_id:
                    try:
                        await bot.delete_message(chat_id=int(prompt_chat_id), message_id=int(prompt_message_id))
                    except Exception as e:
                        print(f"[DERIV OAUTH] Couldn't delete prompt message for {user_id}: {e}")
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"✅ <b>Deriv account connected!</b>\n\n"
                        f"Account: {loginid}\n\n"
                        f"Tap 🔗 Connect Deriv below anytime to check your "
                        f"balance or manage Auto Trade."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
            else:
                await bot.send_message(
                    chat_id=int(user_id),
                    text="⚠️ Couldn't save your Deriv connection - please try again from 🔗 Connect Deriv.",
                    reply_markup=main_keyboard
                )
        except Exception as e:
            print(f"[DERIV OAUTH] Failed to process pending connection for {user_id}: {e}")




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
# "13:00 UTC" = 2PM Lagos, "17:00 UTC" = 6PM
# Lagos, "11:00 UTC" = 12PM Lagos, matching how
# the team actually thinks about these slots.
#
# Per explicit instruction (schedule rebuilt again
# with XAGUSD/USOIL added, post-MetaAPI-candles
# fix): weekdays now rotate XAUUSD, XAGUSD, USOIL,
# and BTCUSD across the 3 daily slots, a different
# combination each weekday (Mon-Fri) - see the 3
# dicts below for the exact per-day mapping.
# GBPJPY no longer has a fixed schedule slot at
# all - still reachable via manual DM "Signal"
# only. Saturday/Sunday UNCHANGED from before this
# rebuild: Saturday = synthetic-morning (8AM) +
# BTCUSD-evening (6PM), no midday; Sunday = single
# synthetic post at 2PM only (see SYNTHETIC_
# SCHEDULE's sunday_only entry), no morning/midday/
# evening major-pair posts at all.
#
# MORNING_PAIR_BY_WEEKDAY / MIDDAY_PAIR_BY_WEEKDAY
# / EVENING_PAIR_BY_WEEKDAY use Python's
# datetime.weekday() convention:
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
# A day with no pair for a given slot maps to None -
# the job simply does nothing that slot.
# ============================================

MORNING_PAIR_BY_WEEKDAY = {
    0: "xauusd",  # Monday
    1: "xagusd",  # Tuesday
    2: "usoil",   # Wednesday
    3: "usoil",   # Thursday
    4: "xauusd",  # Friday
    5: None,      # Saturday - unchanged, volatility/synthetic index instead, see SYNTHETIC_SCHEDULE's saturday_only entry
    6: None,      # Sunday - unchanged, no MORNING post (Sunday's only post is the 2PM synthetic, see SYNTHETIC_SCHEDULE's sunday_only entry)
}

# 11:00 UTC / 12PM Lagos midday slot.
MIDDAY_PAIR_BY_WEEKDAY = {
    0: "usoil",   # Monday
    1: "usoil",   # Tuesday
    2: "btcusd",  # Wednesday
    3: "xauusd",  # Thursday
    4: "usoil",   # Friday
    5: None,      # Saturday - unchanged, no midday post
    6: None,      # Sunday - unchanged, no midday post
}

EVENING_PAIR_BY_WEEKDAY = {
    0: "btcusd",  # Monday
    1: "xauusd",  # Tuesday
    2: "xagusd",  # Wednesday
    3: "btcusd",  # Thursday
    4: "xagusd",  # Friday
    5: "btcusd",  # Saturday - unchanged, per explicit instruction: synthetic in the
                  # morning, BTCUSD in the evening, no midday post.
    6: None,      # Sunday - unchanged, no posts at all
}

DAILY_SCHEDULE = [
    ("06:00", "news", "morning"),  # 7:00 AM Lagos
]

# Synthetic index channel posts - rotates through all 5 indices.
# Per explicit instruction (schedule rebuilt again 2026-07-11):
# weekdays (Monday-Friday) no longer carry ANY synthetic post at all -
# only XAUUSD (morning), GBPJPY (Monday-Thursday midday, see
# MIDDAY_PAIR_BY_WEEKDAY), and BTCUSD (evening) run Mon-Fri now. The
# old Friday 12PM synthetic slot is REMOVED. Saturday's morning slot
# (8AM Lagos = 07:00 UTC) is UNCHANGED - still a synthetic post
# instead of BTCUSD, with BTCUSD still running that same evening at
# 6PM Lagos via EVENING_PAIR_BY_WEEKDAY. Sunday now gets a NEW slot -
# a single synthetic post at 2PM Lagos (13:00 UTC) - per explicit
# instruction, safe to add because the weekly performance report
# only ever counts signal_log rows (XAUUSD/EURUSD/GBPUSD/GBPJPY/
# BTCUSD), and synthetic signals never touch signal_log at all (they
# use their own separate chart/auto-copy path) - so this Sunday post
# has zero effect on those stats. slot_number values are kept
# distinct across these two remaining weekly slots purely so
# get_rotation_key's day-of-year math never accidentally lands both
# on the identical index in the same week - not persisted anywhere,
# safe to renumber.
SYNTHETIC_SCHEDULE = [
    ("07:00", "saturday_only", 0),
    ("13:00", "sunday_only", 1),
]

# ============================================
# AI CONFIG
# UPDATED: gemini-2.0-flash was discontinued
# June 1, 2026. Migrated to gemini-2.5-flash-lite
# (same pricing tier, still free-tier eligible).
# ============================================

GEMINI_MODEL = "gemini-3.1-flash-lite"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ============================================
# KEYBOARDS
# ============================================

main_keyboard = ReplyKeyboardMarkup(
    [["📊 Signal", "📰 News", "🔗 Connect Deriv"], ["🤖 Exness Auto-Trade"]],
    resize_keyboard=True,
    is_persistent=True,
    one_time_keyboard=False
)

def styled_button(text, style="primary", **kwargs):
    """
    Wraps InlineKeyboardButton, applying Telegram's Bot API 9.4 button
    color (added Feb 2026 - 'primary'=blue, 'success'=green,
    'danger'=red) when the installed python-telegram-bot version
    supports it, per explicit instruction to make CTA buttons stand
    out in the same blue as the pinned-message UI element. Falls back
    to a plain, unstyled button automatically if the installed library
    is too old to recognize the parameter (raises TypeError) - so this
    can never crash the bot regardless of what's actually deployed on
    Railway, and there's nothing to check or upgrade first.
    """
    try:
        return InlineKeyboardButton(text, style=style, **kwargs)
    except TypeError:
        return InlineKeyboardButton(text, **kwargs)


def get_channel_button():
    # CONFIRMED REAL BUG, same class already fixed once before for
    # the "Trade This Signal" button (see start()'s chantrade_
    # comment): callback_data buttons send an invisible ping back to
    # the bot's server, they NEVER open a chat - and Telegram blocks
    # bots from messaging a user who hasn't already started a DM with
    # them, so the old channelcta callback handler's send_message
    # call silently failed for anyone tapping this from the channel
    # without ever having opened a DM first. That's exactly the
    # "it just notifies, doesn't open the bot" symptom reported live.
    # A url= deep link sidesteps this entirely - opening it IS the
    # user starting the DM, no callback round-trip needed. No special
    # payload branch needed in start() either - falling through to
    # plain /start already runs the exact same follow-gate/welcome
    # logic the old callback handler was duplicating by hand.
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤖 Get Your Own Signal",
            url=f"https://t.me/{BOT_USERNAME}?start=channelcta"
        )]
    ])

# ============================================
# USER MODES
# ============================================

user_modes = {}

# Temporary holding spot for MT5/MT4 credentials WHILE a user is
# mid-signup (account number -> password -> server -> name), before
# they're encrypted and written to Supabase. Cleared as soon as
# signup completes or fails - never persisted here across restarts.
mt5_signup_state = {}
deriv_flip_signup_state = {}

# user_id (str) -> (chat_id, message_id) of their most recent
# "Welcome back... what would you like to do today?" message - per
# explicit instruction, tapping a deep-link button (channelcta,
# chantrade_) repeatedly was sending a fresh full welcome message
# every time with the old one still sitting there, stacking up
# duplicates in the chat. start() now deletes this before sending a
# new one, so only ever one welcome message exists at a time per
# user. Plain in-memory dict, not DB-backed - losing this on a
# Railway restart is purely cosmetic (one old welcome message stays
# on screen an extra time until the next /start cleans it up), never
# a lost trade or signal, so the extra DB-table complexity pending_
# trades/channel_signal_context needed for THEIR restart risk doesn't
# apply here.
last_welcome_message = {}

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
# Caches live price and 1h-ago price per pair.
#
# CORRECTION, per explicit instruction: this was
# previously 3600s (1 hour) - CONFIRMED REAL BUG
# via live logs: a price cached for up to an hour
# was being used as the "Entry Price" shown in a
# signal, while the actual MT5 fill (whether
# automatic seconds later, or manual minutes/
# hours later) used the REAL current price -
# causing the SL/TP's realized risk:reward to
# drift from the intended ratio (sometimes
# severely, e.g. 1:1 or worse instead of 1:2), and
# in one confirmed case, the broker outright
# REJECTED the trade with TRADE_RETCODE_INVALID_
# STOPS (10016) because the stale-price-based SL/
# TP no longer made sense relative to where price
# had actually moved to. Two identical signals 39
# minutes apart confirmed the exact same cached
# price (4032.10) was being reused, real-world
# proof the cache was the cause, not normal market
# movement. 60s keeps almost all of the original
# scaling benefit (a burst of users requesting the
# same pair within the same minute still only
# costs one real API call) while keeping the price
# genuinely fresh for any reasonable signal-to-
# execution gap.
# ============================================

price_cache = {}
PRICE_CACHE_SECONDS = 60  # was 3600 (1 hour) - see correction above

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

# Shared across every AI-retry path (daily news, News Breakdown,
# News Calendar's currency judgment) - promoted to module level so
# there's one single source of truth for "what does a failed AI call
# look like", rather than duplicate copies in each function risking
# drifting out of sync if a new failure string is ever added.
KNOWN_AI_FAILURE_STRINGS = (
    "⚠️ AI service unavailable.",
    "⚠️ AI server busy.",
    "⚠️ AI servers unavailable.",
)

NEWS_RELEVANT_KEYWORDS = [
    "forex", "currency", "currencies", "dollar", "euro", "pound",
    "sterling", "yen", "usd", "eur", "gbp", "jpy", "chf", "franc",
    "aud", "cad", "nzd", "fed", "federal reserve",
    "ecb", "european central bank", "bank of england", "boe",
    "bank of japan", "boj", "swiss national bank", "snb",
    "interest rate", "rate hike", "rate cut",
    "inflation", "cpi", "nonfarm payroll", "nfp", "gdp", "central bank",
    "bitcoin", "btc", "crypto", "cryptocurrency",
    "gold", "xau", "xauusd",
    "usoil", "oil price", "oil prices", "crude oil", "crude", "opec",
    "wti", "brent crude",
]

# Per explicit instruction - only MAJOR currencies move the market for
# this audience (USD, EUR, GBP, JPY, CHF, AUD, CAD, NZD), plus Gold
# and BTC. A minor/exotic currency headline (e.g. Rupee, Rand, Lira)
# can still slip past the keyword list above just because it happens
# to mention "USD" in passing ("Rupee slips vs USD") - this exclusion
# list hard-blocks those regardless of any major-currency keyword
# also present, since the article's actual SUBJECT is the minor
# currency, not something a major-pair/gold/BTC trader can act on.
NEWS_EXCLUDED_MINOR_CURRENCY_KEYWORDS = [
    "rupee", "inr", "rand", "zar", "lira", "peso", "mxn", "ringgit",
    "myr", "baht", "thb", "won", "krw", "yuan", "rmb", "renminbi", "cny",
    "naira", "ngn", "shekel", "ils", "dirham", "aed", "riyal", "sar",
    "rouble", "ruble", "rub", "zloty", "pln", "forint", "huf",
    "koruna", "czk", "rupiah", "idr", "dong", "vnd", "taka", "bdt",
]

def is_news_relevant(title, description):
    """
    Whole-word matching, not substring - the old `keyword in text`
    check let short keywords like "eur", "yen", "boe" match inside
    unrelated words/tickers (e.g. a German stock exchange ticker like
    "XETR:3A9" slipping through, unrelated to forex or Bitcoin at
    all). re.escape handles keywords with spaces (e.g. "interest
    rate") safely inside the word-boundary pattern.

    Minor/exotic currency exclusion runs FIRST and wins even if a
    major-currency keyword also matches - per explicit instruction,
    an article about the Rupee, Rand, Lira etc. isn't something a
    major-pair/gold/BTC trader can act on, regardless of it mentioning
    "USD" or "dollar" in the same breath.
    """
    text = f"{title} {description}".lower()
    if any(
        re.search(rf"\b{re.escape(keyword)}\b", text)
        for keyword in NEWS_EXCLUDED_MINOR_CURRENCY_KEYWORDS
    ):
        return False
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

# ============================================
# PER-USER TIMEZONE (for auto-localized news/event times)
# Telegram's Bot API has no way to read a user's device timezone
# directly, so this is captured once via an optional location share
# (see trigger_timezone_setup / the awaiting_timezone_location mode)
# and stored as a UTC offset in minutes - covers half-hour zones like
# India (+5:30) too, which a plain integer-hours field wouldn't.
# Users who skip or never set one fall back to WAT (UTC+1), which was
# this bot's original fixed assumption everywhere - so nothing breaks
# for anyone who never sets a timezone at all.
# ============================================

DEFAULT_UTC_OFFSET_MINUTES = 60  # WAT - the bot's original fixed assumption


def get_user_utc_offset_minutes(user_id):
    """Returns this user's saved UTC offset in minutes, or None if never set."""
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/user_timezones"
            f"?user_id=eq.{user_id}&select=utc_offset_minutes"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if not isinstance(data, list):
            # Supabase returns an error object (not a list) if the table
            # is missing/misconfigured - logging this explicitly rather
            # than letting it look identical to "user genuinely has no
            # saved offset yet", which was silently causing the setup
            # prompt to repeat forever for every single user.
            print(f"[TIMEZONE DB] Unexpected response (is user_timezones table set up in Supabase?): {data}")
            return None
        if data and data[0].get("utc_offset_minutes") is not None:
            return int(data[0]["utc_offset_minutes"])
        return None
    except Exception as e:
        print(f"[TIMEZONE DB] get_user_utc_offset_minutes error: {e}")
        return None


def save_user_utc_offset_minutes(user_id, offset_minutes):
    try:
        url = f"{SUPABASE_URL}/rest/v1/user_timezones"
        payload = {"user_id": user_id, "utc_offset_minutes": offset_minutes}
        response = requests.post(
            url,
            headers={**sb_headers(), "Prefer": "resolution=merge-duplicates"},
            json=payload, timeout=10
        )
        if response.status_code not in (200, 201, 204):
            # A silent failure here (e.g. the table doesn't exist yet)
            # previously looked identical to a successful save, which
            # meant the timezone prompt kept re-appearing every single
            # time since nothing was ever actually persisted.
            print(f"[TIMEZONE DB] save failed ({response.status_code}): {response.text}")
            return False
        return True
    except Exception as e:
        print(f"[TIMEZONE DB] save_user_utc_offset_minutes error: {e}")
        return False


def get_all_user_utc_offsets():
    """
    Batch fetch of every saved user_id -> utc_offset_minutes, for the
    news-alert broadcast loop - avoids one DB round-trip per recipient
    when notifying potentially many users at once.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/user_timezones?select=user_id,utc_offset_minutes"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return {row["user_id"]: int(row["utc_offset_minutes"]) for row in data if row.get("utc_offset_minutes") is not None}
    except Exception as e:
        print(f"[TIMEZONE DB] get_all_user_utc_offsets error: {e}")
        return {}


def lookup_utc_offset_from_coordinates(latitude, longitude):
    """
    Converts a shared location into a UTC offset in minutes, via a
    free no-key coordinate-to-timezone lookup. Returns None on any
    failure so the caller can fall back to the default gracefully.
    """
    try:
        url = f"https://timeapi.io/api/TimeZone/coordinate?latitude={latitude}&longitude={longitude}"
        response = requests.get(url, timeout=10)
        data = response.json()
        utc_offset_seconds = data.get("currentUtcOffset", {}).get("seconds")
        if utc_offset_seconds is None:
            return None
        return int(utc_offset_seconds // 60)
    except Exception as e:
        print(f"[TIMEZONE LOOKUP] lookup_utc_offset_from_coordinates error: {e}")
        return None


def format_gmt_label(offset_minutes):
    """e.g. 60 -> 'GMT+1', -240 -> 'GMT-4', 330 -> 'GMT+5:30'"""
    sign = "+" if offset_minutes >= 0 else "-"
    abs_minutes = abs(offset_minutes)
    hours, minutes = divmod(abs_minutes, 60)
    return f"GMT{sign}{hours}:{minutes:02d}" if minutes else f"GMT{sign}{hours}"


def format_local_time(event_dt_utc_str, offset_minutes):
    """
    event_dt_utc_str is the "%Y-%m-%dT%H:%M" TRUE UTC string already
    produced by get_todays_high_impact_events - converts it to this
    specific viewer's local time using their own saved offset,
    formatted 12-hour with AM/PM (e.g. "7:00 AM") rather than 24-hour,
    since this is shown to individual traders as their own clock time.
    """
    try:
        dt_utc = datetime.strptime(event_dt_utc_str, "%Y-%m-%dT%H:%M")
        local_dt = dt_utc + timedelta(minutes=offset_minutes)
        hour_12 = local_dt.hour % 12
        if hour_12 == 0:
            hour_12 = 12
        am_pm = "AM" if local_dt.hour < 12 else "PM"
        return f"{hour_12}:{local_dt.minute:02d} {am_pm}"
    except Exception:
        return ""

def get_verified_user_email(user_id):
    """Returns this user's own verified email, or None."""
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/verified_users"
            f"?user_id=eq.{user_id}&select=email"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return data[0]["email"] if data else None
    except Exception as e:
        print(f"[DB] get_verified_user_email error: {e}")
        return None

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

def log_signal(signal_data, source="scheduled"):
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
            # Per-strategy performance tracking - see signal_data's own
            # comment for why this was added.
            "agreeing_strategies": signal_data.get("agreeing_strategies", []),
            "confidence": signal_data.get("confidence"),
            # "manual" (DM-requested) signals are logged for stats only
            # - has_open_signal_for_pair below is scoped to
            # source=scheduled specifically so a manual request can
            # never block/delay the scheduled channel signal for the
            # same pair, which would be a real behavior change, not
            # just added tracking.
            "source": source,
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

def log_channel_message(signal_id, chat_id, message_id):
    """
    CONFIRMED REAL GAP, now fixed: channel posts were never recorded
    anywhere - no chat_id/message_id stored for any signal message
    ever sent. When the "Get Your Own Signal" button turned out to
    be broken on already-posted messages (a callback_data button
    that can never open a chat), there was no way to find and fix
    those old messages at all - Telegram bots cannot list/scan a
    channel's history themselves, they can only act on a message ID
    they already know. Going forward, every channel post's real
    chat_id + message_id gets recorded against its signal_log row,
    so editMessageReplyMarkup/editMessageCaption can actually target
    a specific old message later if anything like this happens
    again. Requires a channel_messages table (signal_id bigint,
    chat_id bigint, message_id bigint) - add this table in Supabase
    before this is useful; the call itself fails safely (logs and
    returns) if the table doesn't exist yet, never blocks the post.
    """
    if not signal_id or not chat_id or not message_id:
        return
    try:
        url = f"{SUPABASE_URL}/rest/v1/channel_messages"
        payload = {
            "signal_id": signal_id,
            "chat_id": chat_id,
            "message_id": message_id,
            "posted_at": datetime.utcnow().isoformat(),
        }
        response = requests.post(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code not in (200, 201):
            print(f"[CHANNEL LOG] log_channel_message got {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[CHANNEL LOG] log_channel_message error: {e}")


def get_last_channel_message(chat_id):
    """
    Looks up the most recently logged signal message for a given
    channel, using the channel_messages table built alongside
    log_channel_message above. Powers the "delete the previous signal
    before posting a new one" behavior in _post_signal_for_pair, per
    explicit instruction that 3 signals/day was starting to feel
    spammy with old messages stacking up.

    Returns (chat_id, message_id) or None if nothing's been logged
    for this channel yet, or the table doesn't exist/lookup fails -
    fails safe, never blocks the new message from sending.
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/channel_messages"
            f"?chat_id=eq.{chat_id}&select=chat_id,message_id"
            f"&order=posted_at.desc&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if data:
            return data[0]["chat_id"], data[0]["message_id"]
        return None
    except Exception as e:
        print(f"[CHANNEL LOG] get_last_channel_message error: {e}")
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
        # Per explicit instruction - this used to return with ZERO log
        # output, meaning a failed real trade placement (place_mt5_
        # trade returning None) left no trace anywhere, indistinguishable
        # from a routine no-op. Logging it now so a genuine placement
        # failure is visible instead of silently falling through to the
        # weaker price-inference fallback in check_open_signals.
        print(f"[SIGNAL LOG] ⚠️ Skipping link for signal {signal_id} - no order_id (trade placement likely failed).")
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
    order_id = await place_mt5_trade(signal_data, signal_id=signal_id)
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
        # FIX: must be timezone-AWARE ("+00:00" suffix), not naive -
        # delete_at is a timestamptz column, and comparing it against
        # a naive "now" string here (no offset) is the one place in
        # the whole codebase where that naive-datetime pattern
        # actually breaks something: PostgREST/Postgres can't safely
        # assume a bare timestamp string means UTC, so this lte.
        # filter was silently failing to match rows that were
        # genuinely due, leaving them stuck in the queue (and the
        # original messages undeleted in Telegram) indefinitely.
        now_str = datetime.utcnow().isoformat() + "+00:00"
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
    True if this pair already has a SCHEDULED signal sitting OPEN in
    signal_log - used to stop a fresh scheduled signal (e.g. BTCUSD)
    from posting/trading while the previous one on the same pair
    hasn't closed in profit or loss yet (see get_mt5_trade_outcome /
    check_open_signals for how a signal eventually closes).

    Scoped to source=scheduled specifically - manual (DM-requested)
    signals are now also logged (for per-strategy performance
    tracking), but must never be able to block or delay the scheduled
    channel signal for the same pair just because a user happened to
    ask for one manually - that would be a real change to channel
    posting behavior, not just added tracking.
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/signal_log"
            f"?status=eq.OPEN&pair_name=eq.{pair_name}&source=eq.scheduled&select=id&limit=1"
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

def record_deriv_service_token_failure():
    """
    Marks DERIV_SERVICE_TOKEN as currently failing - only called for
    a genuine 401/403 on THIS specific token (not any individual
    subscriber's own token failing, which is normal/expected and not
    an outage). Doesn't overwrite failure_detected_at if already
    marked failing, so the alert job below can tell how long it's
    actually been down, not just that it failed once again.
    """
    try:
        existing = get_service_credential("deriv_service_token")
        if existing and existing.get("currently_failing"):
            return  # already flagged, don't reset the detection timestamp
        url = f"{SUPABASE_URL}/rest/v1/service_credentials?on_conflict=credential_name"
        headers = sb_headers()
        headers["Prefer"] = "resolution=merge-duplicates"
        payload = {
            "credential_name": "deriv_service_token",
            "currently_failing": True,
            "failure_detected_at": datetime.utcnow().isoformat(),
        }
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"[SERVICE CREDENTIALS] Error recording failure: {e}")


def record_deriv_service_token_success():
    """
    Clears the failing flag once DERIV_SERVICE_TOKEN works again -
    only does the write if it was actually flagged, to avoid a
    pointless DB write on literally every single successful call.
    """
    try:
        existing = get_service_credential("deriv_service_token")
        if not existing or not existing.get("currently_failing"):
            return
        url = f"{SUPABASE_URL}/rest/v1/service_credentials?credential_name=eq.deriv_service_token"
        requests.patch(url, headers=sb_headers(), json={"currently_failing": False}, timeout=10)
    except Exception as e:
        print(f"[SERVICE CREDENTIALS] Error clearing failure flag: {e}")


def get_service_credential(name):
    try:
        url = f"{SUPABASE_URL}/rest/v1/service_credentials?credential_name=eq.{name}&select=*&limit=1"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        rows = response.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[SERVICE CREDENTIALS] Error fetching {name}: {e}")
        return None


def record_metaapi_candles_failure():
    """
    Same pattern as record_deriv_service_token_failure, for MetaAPI's
    candle-fetching specifically - per explicit instruction, added so
    a disconnected MT5 account or exhausted MetaAPI credit gets
    flagged proactively, even though TwelveData's fallback means
    signals themselves keep working either way. Doesn't overwrite
    failure_detected_at if already flagged, so the alert can say how
    long it's actually been down.
    """
    try:
        existing = get_service_credential("metaapi_candles")
        if existing and existing.get("currently_failing"):
            return
        url = f"{SUPABASE_URL}/rest/v1/service_credentials?on_conflict=credential_name"
        headers = sb_headers()
        headers["Prefer"] = "resolution=merge-duplicates"
        payload = {
            "credential_name": "metaapi_candles",
            "currently_failing": True,
            "failure_detected_at": datetime.utcnow().isoformat(),
        }
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"[SERVICE CREDENTIALS] Error recording MetaAPI candles failure: {e}")


def record_metaapi_candles_success():
    try:
        existing = get_service_credential("metaapi_candles")
        if not existing or not existing.get("currently_failing"):
            return
        url = f"{SUPABASE_URL}/rest/v1/service_credentials?credential_name=eq.metaapi_candles"
        requests.patch(url, headers=sb_headers(), json={"currently_failing": False}, timeout=10)
    except Exception as e:
        print(f"[SERVICE CREDENTIALS] Error clearing MetaAPI candles failure flag: {e}")


async def _alert_if_credential_failing(context, cred_name, human_label, impact_text):
    """
    Shared failure-alert logic, extracted so both DERIV_SERVICE_TOKEN
    and MetaAPI candle-fetching use the exact same alerting behavior
    (max once every 2 hours while broken) instead of two near-
    identical copies of the same function.
    """
    cred = get_service_credential(cred_name)
    if not cred or not cred.get("currently_failing"):
        return
    last_alert = cred.get("last_failure_alert_at")
    if last_alert:
        last_alert_dt = datetime.fromisoformat(last_alert.replace("Z", "+00:00"))
        if (datetime.now(last_alert_dt.tzinfo) - last_alert_dt) < timedelta(hours=2):
            return
    try:
        failure_since = cred.get("failure_detected_at", "unknown time")
        await context.bot.send_message(
            chat_id=int(ADMIN_USER_ID),
            text=(
                f"🚨 <b>{human_label} is down.</b>\n\n"
                f"Failing since: {failure_since}\n\n"
                f"{impact_text}"
            ),
            parse_mode=ParseMode.HTML
        )
        update_url = f"{SUPABASE_URL}/rest/v1/service_credentials?credential_name=eq.{cred_name}"
        requests.patch(update_url, headers=sb_headers(), json={"last_failure_alert_at": datetime.utcnow().isoformat()}, timeout=10)
    except Exception as e:
        print(f"[SERVICE CREDENTIALS] Couldn't send failure alert for {cred_name}: {e}")


async def check_deriv_service_token_health(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 15 minutes. Checks TWO independent credentials now,
    per explicit instruction (MetaAPI candle-fetching added alongside
    the original Deriv check - same underlying mechanism, just two
    things being watched instead of one):

    1. DERIV_SERVICE_TOKEN failing -> alert (max once/2h while broken)
    2. DERIV_SERVICE_TOKEN expiring within 3 days -> alert once/day
    3. MetaAPI candle-fetching failing (disconnected MT5 account,
       exhausted credit, etc.) -> alert (max once/2h while broken).
       No expiry concept for this one - MetaAPI credentials don't
       expire the way Deriv's do, so only the failure check applies.
    """
    if not ADMIN_USER_ID:
        return

    await _alert_if_credential_failing(
        context, "deriv_service_token", "DERIV_SERVICE_TOKEN",
        "Every Deriv synthetic index (channel signals, manual signals, "
        "Aggressive/Conservative, Account Flip) has zero real price data "
        "while this is broken. Generate a fresh token from Deriv's API "
        "tokens page (Trade + Account management scopes) and send it over."
    )
    await _alert_if_credential_failing(
        context, "metaapi_candles", "MetaAPI candle-fetching",
        "Every pair is still generating signals (TwelveData is covering "
        "as fallback), but MetaAPI itself isn't providing price data right "
        "now - worth checking whether the MT5 account got disconnected "
        "from MetaAPI or ran out of credit."
    )

    cred = get_service_credential("deriv_service_token")
    if not cred:
        return
    expires_at = cred.get("expires_at")
    if expires_at:
        expiry_date = datetime.strptime(expires_at, "%Y-%m-%d").date()
        days_left = (expiry_date - datetime.utcnow().date()).days
        already_reminded_today = cred.get("last_expiry_reminder_sent") == datetime.utcnow().date().isoformat()
        if 0 <= days_left <= 3 and not already_reminded_today:
            try:
                await context.bot.send_message(
                    chat_id=int(ADMIN_USER_ID),
                    text=(
                        f"⏰ <b>DERIV_SERVICE_TOKEN expires in {days_left} day(s)</b> "
                        f"({expires_at}).\n\n"
                        f"Generate a fresh one now from Deriv's API tokens page "
                        f"(Trade + Account management scopes, up to 90 days) and "
                        f"send it over before it lapses."
                    ),
                    parse_mode=ParseMode.HTML
                )
                update_url = f"{SUPABASE_URL}/rest/v1/service_credentials?credential_name=eq.deriv_service_token"
                requests.patch(
                    update_url, headers=sb_headers(),
                    json={"last_expiry_reminder_sent": datetime.utcnow().date().isoformat()}, timeout=10
                )
            except Exception as e:
                print(f"[SERVICE CREDENTIALS] Couldn't send expiry reminder: {e}")


async def deriv_get_options_accounts(token):
    """
    Step 1: lists every account (real and virtual) tied to this
    token. Returns the raw parsed JSON on success, or None on any
    failure. The exact field names here are being confirmed against
    live testing - deriv_fetch_account_snapshot below logs the raw
    response if it can't find an account in the shape it expects,
    so the actual shape can be adjusted from real output rather
    than another guess.

    FIX: confirmed live - a real, valid token (proven working just 3
    minutes earlier) got reported to the user as "may have expired or
    been revoked" with zero retry on a single momentary failure, same
    class of bug as the MetaAPI 429 issue fixed earlier today. Retries
    transient failures (timeouts/exceptions and 429/502/503/504) up to
    2 extra times - a genuine auth failure (401/403, meaning the token
    really is dead) is NOT retried, so a truly revoked token still
    reports correctly without unnecessary delay.
    """
    if not DERIV_APP_ID:
        print("[DERIV] No DERIV_APP_ID set")
        return None
    url = f"{DERIV_API_BASE}/trading/v1/options/accounts"
    transient_statuses = (429, 502, 503, 504)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.get(url, headers=deriv_api_headers(token), timeout=10)
            if response.status_code == 200:
                if token == DERIV_SERVICE_TOKEN:
                    record_deriv_service_token_success()
                return response.json()
            if response.status_code not in transient_statuses or attempt == max_attempts:
                print(f"[DERIV] Accounts lookup failed {response.status_code}: {response.text}")
                if token == DERIV_SERVICE_TOKEN and response.status_code in (401, 403):
                    record_deriv_service_token_failure()
                return None
            print(f"[DERIV] Accounts lookup got {response.status_code} (attempt {attempt}/{max_attempts}) - retrying...")
        except Exception as e:
            if attempt == max_attempts:
                print(f"[DERIV] deriv_get_options_accounts error: {e}")
                return None
            print(f"[DERIV] deriv_get_options_accounts transient error (attempt {attempt}/{max_attempts}): {e} - retrying...")
        await asyncio.sleep(1.5 * attempt)
    return None

async def deriv_get_otp_url(token, account_id):
    """
    Step 2: exchanges the token + a specific account ID for the
    short-lived WebSocket URL with the one-time code embedded.

    Same retry fix as deriv_get_options_accounts above, for the same
    confirmed reason.
    """
    url = f"{DERIV_API_BASE}/trading/v1/options/accounts/{account_id}/otp"
    transient_statuses = (429, 502, 503, 504)
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(url, headers=deriv_api_headers(token), timeout=10)
            if response.status_code == 200:
                data = response.json()
                return data.get("data", {}).get("url")
            if response.status_code not in transient_statuses or attempt == max_attempts:
                print(f"[DERIV] OTP request failed {response.status_code}: {response.text}")
                return None
            print(f"[DERIV] OTP request got {response.status_code} (attempt {attempt}/{max_attempts}) - retrying...")
        except Exception as e:
            if attempt == max_attempts:
                print(f"[DERIV] deriv_get_otp_url error: {e}")
                return None
            print(f"[DERIV] deriv_get_otp_url transient error (attempt {attempt}/{max_attempts}): {e} - retrying...")
        await asyncio.sleep(1.5 * attempt)
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

# ============================================
# MT5 AUTO-TRADE ORDER LOG (NEW)
# Mirrors auto_copy_trades below, but for MT5 auto-trade
# (mt5_auto_trade_accounts subscribers) - this never existed before:
# trades were placed and ONE confirmation sent, then nothing ever
# tracked whether they later hit TP or SL. Per explicit instruction,
# clients were finding out their account had gone to zero too late -
# this table + check_mt5_autotrade_closed_orders below is what lets
# an immediate close notification actually fire.
#
# REQUIRED: create this table in Supabase before deploying -
#   create table mt5_autotrade_orders (
#     id uuid primary key default gen_random_uuid(),
#     user_id text not null,
#     metaapi_account_id text not null,
#     order_id text not null,
#     pair_display text,
#     direction text,
#     status text default 'OPEN',
#     profit numeric,
#     opened_at timestamptz default now()
#   );
# ============================================

def log_mt5_autotrade_order(user_id, metaapi_account_id, order_id, pair_display, direction):
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_autotrade_orders"
        payload = {
            "user_id": str(user_id),
            "metaapi_account_id": metaapi_account_id,
            "order_id": str(order_id),
            "pair_display": pair_display,
            "direction": direction,
            "status": "OPEN",
            "opened_at": datetime.utcnow().isoformat(),
        }
        response = requests.post(url, headers=sb_headers(), json=payload, timeout=10)
        if response.status_code not in (200, 201):
            print(f"[MT5 AUTOTRADE LOG] Failed to log order for {user_id}/{order_id}: {response.status_code} {response.text}")
    except Exception as e:
        print(f"[MT5 AUTOTRADE LOG] error: {e}")

def get_open_mt5_autotrade_orders():
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_autotrade_orders?status=eq.OPEN&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=15)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[MT5 AUTOTRADE LOG] get_open_mt5_autotrade_orders error: {e}")
        return []

def mark_mt5_autotrade_order_closed(order_id, profit):
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_autotrade_orders?order_id=eq.{order_id}"
        payload = {"status": "CLOSED", "profit": profit}
        requests.patch(url, headers=sb_headers(), json=payload, timeout=10)
    except Exception as e:
        print(f"[MT5 AUTOTRADE LOG] mark_mt5_autotrade_order_closed error: {e}")

def get_todays_mt5_autotrade_orders(user_id):
    try:
        today_start = datetime.utcnow().strftime("%Y-%m-%dT00:00:00")
        url = (
            f"{SUPABASE_URL}/rest/v1/mt5_autotrade_orders"
            f"?user_id=eq.{user_id}&opened_at=gte.{today_start}&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[MT5 AUTOTRADE DIGEST] get_todays_mt5_autotrade_orders error: {e}")
        return []

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

def save_deriv_account(user_id, loginid, token, currency, auth_method="manual"):
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
            # "oauth" vs "manual" - kept as informational metadata
            # (e.g. useful for support/debugging) even though no
            # feature currently acts on it - per explicit instruction,
            # the proactive expiry-warning feature that used to read
            # this was removed; the existing reactive "couldn't reach
            # your account" fallback (which already has the login
            # button) is the agreed approach instead.
            "auth_method": auth_method,
        }
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        response = requests.post(
            url, headers=headers, json=payload, timeout=10
        )
        if response.status_code not in (200, 201):
            print(f"[DERIV] save_deriv_account unexpected status {response.status_code}: {response.text}")
            return False
        print(f"[DERIV] ✅ Linked account for user {user_id}: {loginid} (via {auth_method})")
        return True
    except Exception as e:
        print(f"[DERIV] save_deriv_account error: {e}")
        return False

# ============================================
# DERIV OAUTH LOGIN (PKCE) - CORRECTED VERSION
# Uses Deriv's modern Authorization Code + PKCE flow (auth.deriv.com,
# not the old oauth.deriv.com endpoint - that one no longer works for
# newly-registered "OAuth"-type apps, confirmed by testing). The
# resulting access_token is a genuine drop-in for save_deriv_account -
# this bot's ENTIRE Deriv integration already runs on Bearer tokens
# against the same REST API (see deriv_api_headers/
# deriv_get_options_accounts above), so nothing downstream needs to
# change to use an OAuth-issued token instead of a manually-pasted one.
# Confirmed with real (not documented-example) tokens: no refresh
# token exists or is needed - Deriv issues access_token with a ~30-day
# expiry_in, so this is "re-auth once a month", not "expires hourly".
#
# Two tables, matching the same "webhook thread never talks to
# Telegram directly" pattern already proven by the KoraPay flow above:
#   1. deriv_oauth_states - correlates the redirect callback back to
#      the Telegram user who started it, AND holds the PKCE
#      code_verifier needed for the token exchange. One-time use.
#   2. pending_deriv_oauth_connections - the callback (running in the
#      separate webhook thread) resolves everything (including the
#      real account's loginid) and writes here; a job on the BOT'S
#      OWN event loop (process_pending_deriv_oauth_connections) picks
#      it up and does the actual save_deriv_account() call + Telegram DM.
#
# REQUIRED: create these tables in Supabase before deploying -
#   create table deriv_oauth_states (
#     state text primary key,
#     user_id text not null,
#     code_verifier text not null,
#     created_at timestamptz default now()
#   );
#   create table pending_deriv_oauth_connections (
#     id uuid primary key default gen_random_uuid(),
#     user_id text not null,
#     loginid text not null,
#     token text not null,
#     currency text,
#     processed boolean default false,
#     created_at timestamptz default now()
#   );
# ============================================

def generate_pkce_pair():
    """Returns (code_verifier, code_challenge) per RFC 7636 - S256 method."""
    code_verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return code_verifier, code_challenge

def create_deriv_oauth_state(user_id, code_verifier):
    """Generates a fresh one-time state token, storing who it belongs to and its PKCE verifier."""
    state = secrets.token_urlsafe(24)
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_oauth_states"
        payload = {"state": state, "user_id": str(user_id), "code_verifier": code_verifier}
        requests.post(url, headers=sb_headers(), json=payload, timeout=10)
        return state
    except Exception as e:
        print(f"[DERIV OAUTH] create_deriv_oauth_state error: {e}")
        return None

def resolve_deriv_oauth_state(state):
    """Looks up (user_id, code_verifier, prompt_chat_id, prompt_message_id) for a state, then deletes it (one-time use)."""
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_oauth_states?state=eq.{state}"
            f"&select=user_id,code_verifier,prompt_chat_id,prompt_message_id"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        if not data:
            return None, None, None, None
        row = data[0]
        requests.delete(
            f"{SUPABASE_URL}/rest/v1/deriv_oauth_states?state=eq.{state}",
            headers=sb_headers(), timeout=10
        )
        return row["user_id"], row["code_verifier"], row.get("prompt_chat_id"), row.get("prompt_message_id")
    except Exception as e:
        print(f"[DERIV OAUTH] resolve_deriv_oauth_state error: {e}")
        return None, None, None, None

def exchange_deriv_oauth_code(code, code_verifier):
    """
    POSTs to Deriv's token endpoint to exchange the authorization code
    for a real access_token. Returns the access_token string, or None
    on any failure. Synchronous (requests, not httpx) - fine to call
    from the aiohttp callback handler as a quick blocking call, same
    pattern already used by deriv_get_options_accounts elsewhere.
    """
    try:
        response = requests.post(
            "https://auth.deriv.com/oauth2/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "client_id": DERIV_OAUTH_APP_ID,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": DERIV_OAUTH_REDIRECT_URL,
            },
            timeout=15,
        )
        if response.status_code != 200:
            print(f"[DERIV OAUTH] Token exchange failed {response.status_code}: {response.text}")
            return None
        return response.json().get("access_token")
    except Exception as e:
        print(f"[DERIV OAUTH] exchange_deriv_oauth_code error: {e}")
        return None

def save_pending_deriv_oauth_connection(user_id, loginid, token, currency, prompt_chat_id=None, prompt_message_id=None):
    try:
        url = f"{SUPABASE_URL}/rest/v1/pending_deriv_oauth_connections"
        payload = {
            "user_id": str(user_id),
            "loginid": loginid,
            "token": token,
            "currency": currency,
            "processed": False,
            "prompt_chat_id": prompt_chat_id,
            "prompt_message_id": prompt_message_id,
        }
        requests.post(url, headers=sb_headers(), json=payload, timeout=10)
    except Exception as e:
        print(f"[DERIV OAUTH] save_pending_deriv_oauth_connection error: {e}")

def get_unprocessed_deriv_oauth_connections():
    try:
        url = f"{SUPABASE_URL}/rest/v1/pending_deriv_oauth_connections?processed=eq.false&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[DERIV OAUTH] get_unprocessed_deriv_oauth_connections error: {e}")
        return []

def mark_deriv_oauth_connection_processed(row_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/pending_deriv_oauth_connections?id=eq.{row_id}"
        requests.patch(url, headers=sb_headers(), json={"processed": True}, timeout=10)
    except Exception as e:
        print(f"[DERIV OAUTH] mark_deriv_oauth_connection_processed error: {e}")

async def build_deriv_login_button(user_id):
    """
    Shared by both send_connect_instructions and the "token expired"
    reconnect message - generates a fresh PKCE pair + one-time state,
    stores them, and returns (markup, state) - markup is a ready
    InlineKeyboardMarkup with the "Login with Deriv" button, or None
    if the OAuth env vars aren't configured (callers fall back to
    manual-token-only in that case). state is returned too so the
    caller can attach the sent message's ID to it afterward (see
    update_deriv_oauth_state_message_id) - lets the prompt message get
    cleaned up automatically once the connection actually succeeds,
    instead of sitting there looking tappable forever.
    """
    if not (DERIV_OAUTH_APP_ID and DERIV_OAUTH_REDIRECT_URL):
        return None, None
    code_verifier, code_challenge = generate_pkce_pair()
    state = create_deriv_oauth_state(user_id, code_verifier)
    if not state:
        return None, None
    oauth_url = (
        "https://auth.deriv.com/oauth2/auth"
        f"?response_type=code&client_id={DERIV_OAUTH_APP_ID}"
        f"&redirect_uri={requests.utils.quote(DERIV_OAUTH_REDIRECT_URL, safe='')}"
        "&scope=trade+account_manage"
        f"&state={state}"
        f"&code_challenge={code_challenge}&code_challenge_method=S256"
    )
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔑 Login with Deriv (recommended)", url=oauth_url)
    ]])
    return markup, state


def update_deriv_oauth_state_message_id(state, chat_id, message_id):
    """Attaches the sent prompt message's location to its state row, for later cleanup on success."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_oauth_states?state=eq.{state}"
        requests.patch(
            url, headers=sb_headers(),
            json={"prompt_chat_id": str(chat_id), "prompt_message_id": message_id},
            timeout=10
        )
    except Exception as e:
        print(f"[DERIV OAUTH] update_deriv_oauth_state_message_id error: {e}")


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

    Also stamps low_balance_last_notified_at whenever notified=True -
    CONFIRMED REAL ISSUE via real screenshots showing this warning
    firing 3 times in one day (9:49, 10:50, 12:50) with 3 DIFFERENT
    balance values ($0.61, $4.4, $3.36) - the balance was genuinely
    oscillating above and below $5 multiple times that day, and the
    existing notified flag correctly resets to False each time
    balance recovers above $5 (working exactly as designed for
    "tell once per low-balance EPISODE") - but per explicit
    instruction, the real want is "tell at most once per CALENDAR
    DAY, period", which the boolean flag alone can never express. See
    should_send_low_balance_notification below for the actual gate.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_accounts?user_id=eq.{user_id}"
        payload = {"low_balance_notified": notified}
        if notified:
            payload["low_balance_last_notified_at"] = datetime.utcnow().isoformat()
        response = requests.patch(url, headers=sb_headers(), json=payload, timeout=10)
        # CONFIRMED REAL RISK during full project audit: requests.patch
        # does NOT raise on 4xx/5xx by default, and this previously
        # never checked response.status_code at all - if Supabase
        # rejects the payload (e.g. low_balance_last_notified_at
        # column doesn't exist yet because the migration hasn't been
        # run), the ENTIRE patch fails, including the pre-existing
        # low_balance_notified field, with zero visibility anywhere.
        # Logging non-2xx explicitly now so a missing migration shows
        # up in Railway logs instead of silently doing nothing.
        if response.status_code not in (200, 204):
            print(f"[DERIV] set_low_balance_notified got {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[DERIV] set_low_balance_notified error: {e}")


def should_send_low_balance_notification(account):
    """
    THE ACTUAL FIX, per explicit instruction: max once per calendar
    day (UTC), regardless of how many times balance crosses above/
    below the $5 threshold that same day. Takes the account dict
    already fetched this scan (no extra DB call) and checks
    low_balance_last_notified_at against today's UTC date - if it's
    missing, or from a previous day, sending is allowed; if it's
    today already, sending is blocked even if low_balance_notified
    itself is currently False (which it legitimately can be, since
    balance recovering above $5 resets that flag independent of the
    daily cap).
    """
    last_notified_at = account.get("low_balance_last_notified_at")
    if not last_notified_at:
        return True
    try:
        last_dt = datetime.fromisoformat(last_notified_at.replace("Z", "+00:00"))
        return last_dt.date() < datetime.utcnow().date()
    except Exception as e:
        print(f"[DERIV] should_send_low_balance_notification parse error: {e}")
        return True  # fail open - don't permanently silence a real warning over a parse hiccup

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
        response = requests.patch(
            url, headers=sb_headers(),
            json={"token_invalid_notified": notified}, timeout=10
        )
        if response.status_code not in (200, 204):
            print(f"[DERIV] set_token_invalid_notified got {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[DERIV] set_token_invalid_notified error: {e}")



def update_deriv_account_fields(user_id, fields):
    """
    Generic PATCH onto a user's existing deriv_accounts row - same
    pattern as save_auto_copy_settings but for the newer bot_choice/
    pair_choice/flip_* columns, which don't need their own named
    setter since there's nothing bespoke about how they're saved.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_accounts?user_id=eq.{user_id}"
        requests.patch(url, headers=sb_headers(), json=fields, timeout=10)
    except Exception as e:
        print(f"[DERIV] update_deriv_account_fields error for {user_id}: {e}")


async def get_deriv_trading_ws_url(token):
    """
    Resolves a token down to its real (non-virtual) account's
    short-lived trading WebSocket URL - the same two-step lookup
    deriv_execute_multiplier_trade already does inline, factored out
    here so get_deriv_contract_live_profit and deriv_sell_contract
    (which need a real account_id too, not None) can share it instead
    of duplicating - or worse, skipping - that resolution step.
    Returns None on any failure.
    """
    try:
        accounts_data = await deriv_get_options_accounts(token)
        if not accounts_data:
            return None
        accounts_list = accounts_data.get("data")
        if not isinstance(accounts_list, list):
            accounts_list = accounts_data.get("accounts")
        if not accounts_list:
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
            return None
        account_id = (
            real_account.get("account_id")
            or real_account.get("loginid")
            or real_account.get("id")
        )
        return await deriv_get_otp_url(token, account_id)
    except Exception as e:
        print(f"[DERIV] get_deriv_trading_ws_url error: {e}")
        return None


async def get_deriv_contract_live_profit(token, contract_id):
    """
    Live profit + is_sold for one open contract, via the same
    proposal_open_contract call get_deriv_contract_outcome already
    uses - a lighter sibling that just wants the current numbers
    rather than a final win/loss outcome. Returns (profit, is_sold)
    or (None, None) on any failure - callers treat None as "couldn't
    read it this round, try again next sweep" rather than a real 0.
    """
    try:
        ws_url = await get_deriv_trading_ws_url(token)
        if not ws_url:
            return None, None
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=10) as ws:
            await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": contract_id}))
            raw = json.loads(await ws.recv())
            contract = raw.get("proposal_open_contract", {})
            if not contract:
                return None, None
            profit = contract.get("profit")
            is_sold = bool(contract.get("is_sold"))
            return (float(profit) if profit is not None else None), is_sold
    except Exception as e:
        print(f"[DERIV FLIP] Live profit read failed for {contract_id}: {e}")
        return None, None


async def deriv_sell_contract(token, contract_id):
    """
    Sells one open contract at market ({"price": 0} = accept any
    price, confirmed against Deriv's own API docs) - how Account
    Flip closes each layer when the stack's trailing stop triggers.
    """
    try:
        ws_url = await get_deriv_trading_ws_url(token)
        if not ws_url:
            return False
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=10) as ws:
            await ws.send(json.dumps({"sell": contract_id, "price": 0}))
            raw = json.loads(await ws.recv())
            if "error" in raw:
                print(f"[DERIV FLIP] Sell failed for {contract_id}: {raw['error'].get('message')}")
                return False
            return True
    except Exception as e:
        print(f"[DERIV FLIP] Sell exception for {contract_id}: {e}")
        return False


def get_open_deriv_flip_stack(user_id):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_flip_stacks"
            f"?user_id=eq.{user_id}&status=eq.OPEN&select=*&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        rows = response.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[DERIV FLIP] Error fetching open stack for {user_id}: {e}")
        return None


def get_all_open_deriv_flip_stacks():
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_flip_stacks?status=eq.OPEN&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        return response.json()
    except Exception as e:
        print(f"[DERIV FLIP] Error fetching open stacks: {e}")
        return []


def create_deriv_flip_stack(user_id, index_key, symbol, direction, contract_type, contract_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_flip_stacks"
        payload = {
            "user_id": str(user_id),
            "index_key": index_key,
            "symbol": symbol,
            "direction": direction,
            "contract_type": contract_type,
            "contract_ids": [contract_id],
            "layer_count": 1,
            "last_layer_profit": 0,
            "peak_profit": 0,
            "status": "OPEN",
        }
        requests.post(url, headers=sb_headers(), json=payload, timeout=10)
    except Exception as e:
        print(f"[DERIV FLIP] Error creating stack for {user_id}: {e}")


def update_deriv_flip_stack(stack_id, fields):
    try:
        url = f"{SUPABASE_URL}/rest/v1/deriv_flip_stacks?id=eq.{stack_id}"
        requests.patch(url, headers=sb_headers(), json=fields, timeout=10)
    except Exception as e:
        print(f"[DERIV FLIP] Error updating stack {stack_id}: {e}")


def get_all_auto_copy_accounts():
    """
    Returns every deriv_accounts row with auto_copy_enabled = true,
    for the signal-posting loop to iterate over. Each row already
    carries its own api_token, so no separate lookup is needed per
    user.

    IMPORTANT: this is the legacy Auto-Copy-specific fetch - it
    excludes anyone who hasn't opted into that specific old feature.
    Since Auto-Copy is no longer offered as a UI option, nobody new
    can ever satisfy this filter. Do NOT use this for the newer bot-
    scan / Account Flip engines - use get_all_deriv_accounts_with_token
    instead, which doesn't pre-filter by auto_copy_enabled at all.
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


def get_all_deriv_accounts_with_token():
    """
    Returns every deriv_accounts row that has a linked api_token,
    with NO pre-filter on auto_copy_enabled - the account-source for
    the Aggressive/Conservative bot scan and Account Flip, both of
    which are gated by their own deriv_autotrade_enabled flag in
    Python, not by the old Auto-Copy toggle. Fixes a real bug: both
    engines were previously calling get_all_auto_copy_accounts, whose
    auto_copy_enabled=eq.true filter silently excluded every Pick-a-
    Bot/Account-Flip user, since that flag belongs to the now-removed
    Auto-Copy feature and nobody can ever satisfy it going forward.
    """
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/deriv_accounts"
            f"?api_token=not.is.null&select=*"
        )
        response = requests.get(url, headers=sb_headers(), timeout=15)
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[DERIV] get_all_deriv_accounts_with_token error: {e}")
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
    # than guessing again.
    #
    # IMPORTANT - per explicit instruction, R_75's floor of 400 is a
    # HARD Deriv-side constraint, not something any config value here
    # can lower. In a multiplier contract, max possible loss is capped
    # at the stake itself, which mathematically caps the widest
    # possible stop distance at exactly 1/multiplier of price -
    # regardless of stake size or dollar risk chosen. At x400 that's
    # 0.25% price movement, full stop, even risking the entire stake.
    # Volatility 75 routinely moves more than that from ordinary
    # noise, which was confirmed as the real driver of auto-copy's
    # poor win rate (real Deriv trade history showed ~17% win rate
    # against a required ~33% breakeven at the intended 1:2 R:R).
    # R_75 has been pulled out of auto-copy entirely below (see
    # run_auto_copy_for_signal/run_auto_copy_scan) rather than left
    # in with a stop distance that can never survive normal noise.
    #
    # R_10/R_25/R_50/R_100 are still UNCONFIRMED - lowered the guess
    # here from 100 to 20 to test for a wider, more survivable stop
    # distance. This is safe to test live: deriv_execute_multiplier_
    # trade's proposal step catches a rejected guess BEFORE any real
    # money moves, and auto-retries with Deriv's own real lowest
    # valid value straight out of the rejection error - same
    # mechanism that already confirmed R_75's true floor above.
    "r10": {"symbol": "R_10", "display": "Volatility 10 Index", "default_multiplier": 20},
    "r25": {"symbol": "R_25", "display": "Volatility 25 Index", "default_multiplier": 20},
    "r50": {"symbol": "R_50", "display": "Volatility 50 Index", "default_multiplier": 20},
    "r75": {"symbol": "R_75", "display": "Volatility 75 Index", "default_multiplier": 400},
    "r100": {"symbol": "R_100", "display": "Volatility 100 Index", "default_multiplier": 20},
}

# R_75 was excluded from auto-copy for a real, data-backed reason:
# Deriv's enforced 400x multiplier floor caps the widest possible
# stop distance at 0.25% of price, mathematically, regardless of
# stake or risk chosen - too tight to survive Volatility 75's normal
# noise, which real trade history confirmed drove a ~17% win rate
# against a required ~33% breakeven. RE-ENABLED per explicit
# instruction - that underlying risk hasn't changed, this is a
# deliberate choice to accept it, not a fix to it. Kept as an empty
# set (not deleted entirely) so this history stays documented and any
# future index can still be excluded the same way if needed.
AUTO_COPY_EXCLUDED_INDICES = set()

# All 5 Volatility indices, minus anything in AUTO_COPY_EXCLUDED_INDICES
# (currently empty - see that set's own comment above for why R_75
# stays in despite its known tighter stop-distance risk). Used by the
# Deriv "Choose a Bot" / Account Flip pair pickers, mirroring
# MT5_AUTOTRADE_PAIRS's role for the Exness side.
DERIV_AUTOTRADE_PAIRS = [k for k in SYNTHETIC_CONFIG if k not in AUTO_COPY_EXCLUDED_INDICES]

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
#
# CONFIRMED REAL BUG, found via direct user report with a real
# screenshot: this table used to be keyed by bare index_key with
# on_conflict=index_key (upsert), meaning every NEW signal for the
# same index (e.g. r10) silently OVERWROTE the previous one's row.
# Every "Trade This Signal" button ever posted for that index -
# including ones from messages sent YESTERDAY - deep-linked to the
# exact same lookup key, so tapping an old button would silently
# execute whatever the MOST RECENT r10 signal's numbers were, not
# the numbers shown in the message actually being tapped. The button
# "worked" in the sense that it opened the bot and built a trade, but
# it was attached to the wrong signal's data, with the gap GROWING
# the longer that old message stayed un-superseded by a fresher one.
#
# Fixed: now keyed by a unique signal_key (index_key + the post
# timestamp, e.g. "r10_1782350412") instead of bare index_key, so
# every signal gets its own permanent row that's never overwritten
# by a later one. The deep link itself now carries this same unique
# signal_key, not just the index name - see _post_synthetic_signal_
# for_index below. get() also now enforces a real 1-hour freshness
# window (per explicit instruction) - a row older than that returns
# None automatically, which reuses the EXISTING "this signal has
# expired" UI path in start()/handle_callback without needing to
# touch that part of the code at all.
#
# Columns: signal_key (text, PRIMARY KEY - NOT index_key anymore),
# index_key (text, plain column now, not unique - kept for
# reference/cleanup queries), symbol, contract_type, direction,
# display (all text), multiplier, stake, risk, win (all numeric),
# updated_at (timestamptz).

class ChannelSignalContextStore:
    """Dict-like interface backed by Supabase, same pattern as PendingTradesStore."""

    def get(self, signal_key, default=None):
        try:
            url = (
                f"{SUPABASE_URL}/rest/v1/channel_signal_context_db"
                f"?signal_key=eq.{signal_key}&select=*"
            )
            response = requests.get(url, headers=sb_headers(), timeout=10)
            data = response.json()
            if not data:
                return default
            row = data[0]

            # 1-hour freshness window, per explicit instruction - a
            # signal older than this is treated exactly like a
            # missing row (returns default/None), which the caller
            # already shows as "This signal has expired" - this is
            # what actually closes the real bug: an old message's
            # button can no longer silently execute fresh numbers,
            # because past 1 hour it simply stops resolving to
            # anything at all, frozen-stale data included.
            updated_at = row.get("updated_at")
            if updated_at:
                try:
                    updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    age_seconds = (datetime.utcnow() - updated_dt.replace(tzinfo=None)).total_seconds()
                    if age_seconds > 3600:
                        print(f"[CHANNEL SIGNAL CONTEXT] {signal_key} is {int(age_seconds)}s old, treating as expired")
                        return default
                except Exception as e:
                    print(f"[CHANNEL SIGNAL CONTEXT] Couldn't parse updated_at for {signal_key}: {e}")

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

    def __setitem__(self, signal_key, trade_context):
        try:
            url = f"{SUPABASE_URL}/rest/v1/channel_signal_context_db?on_conflict=signal_key"
            payload = {
                "signal_key": str(signal_key),
                "index_key": trade_context.get("index_key"),
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

SYNTHETIC_CANDLE_CACHE_SECONDS = {"1m": 30, "5m": 120, "1h": 300, "4h": 14400}
synthetic_candle_cache = {}

async def _deriv_get_candles_once(symbol, granularity, count):
    """
    One full attempt at fetching candles - the entire account-lookup
    + OTP-exchange + WebSocket round trip that deriv_get_candles used
    to do inline. Split out so deriv_get_candles can retry the WHOLE
    thing (not just the WebSocket step) on failure, since a timeout
    or hiccup can happen at any one of those stages, not just the
    final connection.
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


async def deriv_get_candles(symbol, granularity, count=60):
    """
    Fetches real candle history directly from Deriv using the
    service token, via the exact same OTP connection flow already
    confirmed in Phase 1. Returns a list of {open, high, low, close}
    dicts already oldest-to-newest (confirmed live - no reversal
    needed, unlike TwelveData), or None on any failure.

    Retries once after a short delay before giving up - the account-
    lookup/OTP/WebSocket round trip this needs has no margin for a
    single transient timeout or hiccup, and a manual signal request
    fetches this same round trip up to 4 times in a row (H1/H4/daily/
    M1) for whichever index wasn't already cached, so one bad attempt
    out of those 4 was enough to fail the whole signal. A real,
    persistent problem (bad token, wrong symbol, Deriv actually down)
    will still fail on the retry too and correctly return None.
    """
    result = await _deriv_get_candles_once(symbol, granularity, count)
    if result:
        return result

    print(f"[SYNTH] First attempt failed for {symbol} ({granularity}s) - retrying once...")
    await asyncio.sleep(2)
    return await _deriv_get_candles_once(symbol, granularity, count)

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
    # FIX: cache_key must include `count` - same exact bug class
    # already fixed once before for forex candles (get_cached_candles'
    # outputsize fix elsewhere in this file). Without it, two
    # different callers requesting different candle counts for the
    # same index+granularity (e.g. 60 vs 210) would silently share
    # one cache entry, and whichever one ran first decides what every
    # later caller actually receives that round - never visibly
    # erroring, just quietly handing back fewer (or more) candles
    # than what was actually asked for.
    cache_key = f"{index_key}_{granularity_label}_{count}"
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
    # h1 outputsize raised from 60 to 210, per explicit instruction -
    # CONFIRMED REAL ASYMMETRY: forex/crypto's run_strategy_bank
    # fetches 210 h1 candles (build_signal_response), but synthetics
    # only ever fetched 60 here, despite running several of the SAME
    # strategies (Trend Following, MACD, RSI, etc.) that genuinely
    # need a healthy warm-up window to produce trustworthy values -
    # a 26-period EMA (inside calculate_macd) barely stabilizes with
    # only 60 candles total, making fresh MACD crossovers and other
    # longer-lookback patterns structurally rarer to detect reliably
    # on synthetics than on forex/crypto, on top of these strategies
    # already being deliberately strict by design. 210 candles costs
    # nothing extra functionally (analyze_timeframe/the strategy bank
    # have no upper bound, only a len>=15/40/etc floor) - this just
    # gives every h1-based strategy the same real warm-up room
    # forex/crypto signals already get.
    h1_candles = await get_cached_synthetic_candles(index_key, symbol, "1h", 3600, 210)
    h4_candles = await get_cached_synthetic_candles(index_key, symbol, "4h", 14400, 60)
    daily_candles = await get_cached_synthetic_candles(index_key, symbol, "1day", 86400, 10)
    m1_candles = await get_cached_synthetic_candles(index_key, symbol, "1m", 60, 60)

    result = await run_strategy_bank_synthetic(
        index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=m1_candles, min_agree=min_agree
    )
    if not result:
        # FIX: CONFIRMED REAL GAP, now fixed per explicit instruction -
        # this used to just return None here, meaning a manual/
        # scheduled synthetic signal request could get NO response at
        # all when the bank found nothing, unlike forex/gold/oil (see
        # generate_rule_based_bias, called from build_signal_response),
        # which always produces some message via a real price-trend
        # fallback. Auto-Trade and Auto-Copy (real money) deliberately
        # do NOT get this same fallback - see their own callers, which
        # still correctly just skip the round on real money rather
        # than ever act on a coin-flip-style guess. This one is purely
        # the DM/channel MESSAGE path.
        if not h1_candles or len(h1_candles) < 2:
            return None
        current_price = h1_candles[-1]["close"]
        price_1h_ago = h1_candles[-2]["close"]
        direction, reason = generate_rule_based_bias(index_key, current_price, price_1h_ago)
        if not direction:
            return None
        confidence = random.randint(80, 94)
        agreeing_strategies = []
        winning_votes = []
    else:
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

    # ENTRY/SL/TP AS REAL PRICE LEVELS - per explicit instruction,
    # reversing the earlier "dollar-only, no price levels" design.
    # Deriv's multiplier contracts DO let you derive an exact trigger
    # price from stake/risk/win/multiplier, since P&L = stake *
    # multiplier * (price_change / entry_price). Solving that for
    # price at P&L = +win or -risk gives the literal price at which
    # Deriv's own limit_order.take_profit / stop_loss actually fires -
    # these are the SAME numbers already sent to deriv_execute_
    # multiplier_trade, just expressed as price instead of dollars,
    # so they cannot drift out of sync with what really executes.
    # Entry price uses the most recent candle close available (m1 if
    # fetched, else h1) - the closest thing to "right now" we have.
    entry_price = None
    if m1_candles:
        entry_price = m1_candles[-1]["close"]
    elif h1_candles:
        entry_price = h1_candles[-1]["close"]

    sl_price = tp_price = None
    multiplier = config["default_multiplier"]

    # Per explicit instruction: ALL synthetic indices now use a real
    # technical distance (average true range of the last 14 h1
    # candles, SL at 1.5x, TP at 3x for a 1:2 risk:reward) instead of
    # the dollar/multiplier-derived formula. The dollar formula never
    # actually drove real execution anyway - Deriv's own multiplier
    # trade uses the risk/win dollar amounts directly (see
    # deriv_execute_multiplier_trade), never these displayed price
    # levels - so there's no execution-accuracy reason to keep it,
    # and it was mathematically incapable of a meaningful distance on
    # any index with a high forced multiplier floor (R_75 confirmed
    # live: 0.075%/0.15% vs R_25's healthy 1.5%/3.0% on the same
    # formula). A single consistent technical method now applies to
    # every index, with no exposed explanation on the signal itself.
    if entry_price is not None and h1_candles and len(h1_candles) >= 15:
        recent = h1_candles[-14:]
        atr_proxy = sum(c["high"] - c["low"] for c in recent) / len(recent)
        if atr_proxy > 0:
            if direction == "BUY":
                sl_price = entry_price - (atr_proxy * 1.5)
                tp_price = entry_price + (atr_proxy * 3.0)
            else:
                sl_price = entry_price + (atr_proxy * 1.5)
                tp_price = entry_price - (atr_proxy * 3.0)

    # Real generated chart from the SAME candles the winning strategy
    # used. Entry/SL/TP now passed as real derived price levels (see
    # above) instead of None, so the chart draws them exactly like
    # forex/crypto charts do.
    image_file_id = fallback_image_file_id
    # FIX: CONFIRMED REAL BUG - this used to default to None when
    # winning_votes was empty (the rule-based fallback case), which
    # skipped real chart generation entirely and silently fell back
    # to the static branded SELL/BUY image instead of a real
    # candlestick chart - confirmed live on a Volatility 50 Index
    # signal. The forex/gold/oil path (below) already correctly
    # defaults to "Momentum" for this exact case; this one just never
    # got the same fix when the fallback was built.
    chart_strategy_name = winning_votes[0]["strategy_name"] if winning_votes else "Momentum"
    if chart_strategy_name:
        chart_candles = m1_candles if chart_strategy_name in M1_BASED_STRATEGIES else h1_candles
        chart_path = os.path.join(CHART_OUTPUT_DIR, f"{index_key}_{int(time.time())}.png")
        chart_ok = generate_signal_chart(
            config["display"], chart_strategy_name, direction, chart_candles,
            entry_price, sl_price, tp_price, chart_path,
        )
        if not chart_ok:
            # RETRY: same reasoning as the forex/crypto path - chart
            # generation is pure local rendering, no AI/API cost, so a
            # retry with freshly re-fetched candles is cheap insurance
            # against a transient data hiccup on the first attempt.
            print(f"[CHART] {config['display']}/{chart_strategy_name}: first attempt failed, retrying once with a fresh candle fetch...")
            time.sleep(2)
            if chart_strategy_name in M1_BASED_STRATEGIES:
                retry_candles = await get_cached_synthetic_candles(index_key, symbol, "1m", 60, 60)
            else:
                retry_candles = await get_cached_synthetic_candles(index_key, symbol, "1h", 3600, 210)
            if retry_candles:
                chart_ok = generate_signal_chart(
                    config["display"], chart_strategy_name, direction, retry_candles,
                    entry_price, sl_price, tp_price, chart_path,
                )
        if chart_ok:
            image_file_id = chart_path

    # FBS-style narrative: reason comes FIRST, per explicit
    # instruction (synthetics never had a separate Entry/SL/TP block
    # to reorder around in the first place - this just replaces the
    # flat "N independent strategies agree..." sentence with varied
    # prose built from the same real winning_votes).
    narrative = generate_signal_narrative(config["display"], direction, winning_votes)

    entry_sl_tp_block = ""
    if entry_price is not None and sl_price is not None and tp_price is not None:
        entry_sl_tp_block = (
            f"<b>Entry Price:</b> {entry_price:.2f}\n"
            f"<b>SL:</b> {sl_price:.2f} | <b>TP:</b> {tp_price:.2f}\n\n"
        )

    message = (
        f"{emoji} <b>STRONG {direction} {config['display']}</b> ⚡\n\n"
        f"<b>Confidence:</b> {confidence}%\n\n"
        f"{narrative}\n\n"
        f"{entry_sl_tp_block}"
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
        "entry_price": entry_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
    }

    return image_file_id, message, trade_context

async def send_connect_instructions(bot, user_id):
    """
    Shared connect-account instructions (affiliate link included) -
    used by both the Connect Deriv button (handle_buttons) and the
    Trade This Signal flow (send_tier_selection) when no account is
    linked yet, so a brand-new user goes straight from intent to
    sign-up to linking without needing to find a separate button.

    Leads with "Login with Deriv" (PKCE OAuth) as the recommended
    option, per explicit instruction - manually-copied API tokens are
    the actual source of most "expired/broken" support issues. The
    resulting access_token is a genuine drop-in for save_deriv_account
    (this bot's whole Deriv integration is already Bearer-token-based),
    and Deriv's real tokens last ~30 days, not the 1-hour figure their
    generic docs example shows - confirmed against a real live token.
    Manual token paste stays available as a fallback either way.

    Wording kept deliberately simple throughout, per explicit
    instruction - written for someone who's never done this before,
    not for a developer. The old "not a login/session code" nuance
    line was dropped entirely (not shortened) - it was explaining a
    distinction only relevant to the manual-token path, which most
    people won't even need now that login is front and center.
    """
    user_modes[user_id] = "awaiting_deriv_token"
    login_markup, oauth_state = await build_deriv_login_button(user_id)

    sent = await bot.send_message(
        chat_id=int(user_id),
        text=(
            "🔗 <b>Connect Your Deriv Account</b>\n\n"
            "This lets Nexora show your real Deriv balance and "
            "positions right here in Telegram. (MT5 and cTrader "
            "accounts aren't supported yet - Options accounts only.)\n\n"
            "Don't have a Deriv account yet? "
            "<a href=\"https://track.deriv.com/_eBizfEiAKzC6tyDIijdDK2Nd7ZgqdRLk/1/\">"
            "Sign up here first</a>, then come back to this step.\n\n"
            + (
                "👇 Tap the button below to log in with Deriv. That's it "
                "- one tap and you're connected.\n\n"
                "Prefer to connect a different way? Scroll down for "
                "another option."
                if login_markup else
                "<b>How to connect:</b>\n"
                "1️⃣ Go to <b>developers.deriv.com</b> and log in\n"
                "2️⃣ Tap the menu (☰) in the top right, then tap "
                "<b>API tokens</b>\n"
                "3️⃣ Tap <b>Create new token</b>, and check <b>Trade</b> "
                "and <b>Account management</b>\n"
                "4️⃣ Copy the token and paste it here\n\n"
                "⚠️ <b>Real accounts only</b> — demo/virtual tokens "
                "will be rejected."
            )
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=login_markup or main_keyboard
    )
    if login_markup and oauth_state:
        update_deriv_oauth_state_message_id(oauth_state, sent.chat_id, sent.message_id)
    if login_markup:
        await bot.send_message(
            chat_id=int(user_id),
            text=(
                "<b>Or connect with an API token instead:</b>\n"
                "1️⃣ Go to <b>developers.deriv.com</b> and log in\n"
                "2️⃣ Tap the menu (☰) in the top right, then tap "
                "<b>API tokens</b>\n"
                "3️⃣ Tap <b>Create new token</b>, and check <b>Trade</b> "
                "and <b>Account management</b>\n"
                "4️⃣ Copy the token and paste it here\n\n"
                "⚠️ <b>Real accounts only</b> — demo/virtual tokens "
                "will be rejected."
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
            return "Your linked Deriv account couldn't be reached. Your token may have expired - this often happens if a short-lived login code was used instead of the permanent API token. Relink with the correct token from 🔗 Connect Deriv to resume auto trade."
        return "Your linked Deriv account couldn't be reached. Your token may have expired or been revoked - this often happens if a short-lived login code was used instead of the permanent API token. Tap 🔗 Connect Deriv and paste the correct token to relink."
    if "multiplier" in lowered:
        if auto_copy_context:
            return "This stake amount isn't supported for this index right now. It'll be retried automatically on the next signal."
        return "This stake amount isn't supported for this index right now. Try a different stake, or use 🎯 Trade This Signal again."
    if "insufficient" in lowered or "not enough" in lowered or "balance" in lowered:
        if auto_copy_context:
            return "Your account balance is too low for this stake. Top up your Deriv account to resume auto trade."
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

# ============================================
# ACCUMULATION ZONE STRATEGY (NEW)
# Direct port of SpiritFX_Accumulation_Zone_EA.mq5's core logic - per
# explicit instruction, wired for the SILENT AUTO-TRADE path on
# synthetics ONLY (see run_accumulation_zone_auto_trade below). Never
# touches the channel post, the manual /signal DM flow, or
# SYNTHETIC_STRATEGY_BANK - those are all untouched and keep using
# the existing 9-strategy bank exactly as before.
#
# CONCEPT: measures realized volatility via an EWMA of squared log
# returns, then z-scores that volatility against its own longer-run
# distribution. A sustained LOW z-score (volatility compressing)
# marks an "accumulation zone." Once the zone ends (volatility
# normalizes, confirmed over 2 bars) and it was long enough (30+
# bars) with low enough net drift (a real sideways coil, not a
# stealth trend), a fixed-range volume profile gets built over that
# zone - POC (highest-volume price) and Value Area High/Low (the 70%-
# of-volume range around it). The strategy then watches for the
# FIRST candle that closes outside the Value Area - a genuine
# breakout - and fires once, matching the EA's own "fires once, then
# stops waiting" behavior rather than re-signaling every bar price
# stays broken out.
# ============================================

ACCUM_ZONE_FAST_LEN = 20
ACCUM_ZONE_ENTER_Z = 1.25
ACCUM_ZONE_EXIT_Z = 0.50
ACCUM_ZONE_EXIT_BARS = 2
ACCUM_ZONE_MIN_BARS = 30
ACCUM_ZONE_MAX_BARS = 300
ACCUM_ZONE_MAX_DRIFT = 0.25
ACCUM_ZONE_DIST_LEN = 200
ACCUM_ZONE_ROWS = 48
ACCUM_ZONE_VA_PCT = 70.0
ACCUM_ZONE_ATR_PERIOD = 14
ACCUM_ZONE_SL_ATR_MULT = 1.5
ACCUM_ZONE_TP_ATR_MULT = 3.0


def _accum_zone_alpha(length):
    return 2.0 / (length + 1.0)


def _accum_zone_true_range(candles, i):
    high = candles[i]["high"]
    low = candles[i]["low"]
    prev_close = candles[i - 1]["close"]
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _accum_zone_atr(candles, period=ACCUM_ZONE_ATR_PERIOD):
    if len(candles) < period + 1:
        return None
    trs = [_accum_zone_true_range(candles, i) for i in range(len(candles) - period, len(candles))]
    return sum(trs) / len(trs)


def _accum_zone_build_profile(zone_bars):
    """
    Fixed-range volume profile over a finalized zone - direct port of
    the EA's FinalizeProfile() binning/POC/Value-Area logic.
    zone_bars is a list of (high, low, volume) tuples for the zone's
    candles. Returns (vah, val, poc).
    """
    highs = [b[0] for b in zone_bars]
    lows = [b[1] for b in zone_bars]
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return lo, lo, lo

    step = (hi - lo) / ACCUM_ZONE_ROWS
    if step <= 0:
        return lo, lo, lo

    bin_vol = [0.0] * ACCUM_ZONE_ROWS
    for bh, bl, bv in zone_bars:
        br = bh - bl
        if br <= step * 0.01:
            px = min(hi - step * 0.5, max(lo + step * 0.5, (bh + bl) * 0.5))
            idx = int((px - lo) / step)
            idx = max(0, min(ACCUM_ZONE_ROWS - 1, idx))
            bin_vol[idx] += bv
        else:
            for b in range(ACCUM_ZONE_ROWS):
                bin_lo = lo + b * step
                bin_hi = bin_lo + step
                overlap = max(0.0, min(bh, bin_hi) - max(bl, bin_lo))
                if overlap > 0:
                    bin_vol[b] += bv * (overlap / br)

    max_v = max(bin_vol) if bin_vol else 0
    total_v = sum(bin_vol)
    poc_idx = bin_vol.index(max_v) if max_v > 0 else 0
    poc = lo + (poc_idx + 0.5) * step

    target = total_v * (ACCUM_ZONE_VA_PCT / 100.0)
    cum = bin_vol[poc_idx] if max_v > 0 else 0.0
    left = right = poc_idx
    while cum < target and (left > 0 or right < ACCUM_ZONE_ROWS - 1):
        v_left = bin_vol[left - 1] if left > 0 else -1.0
        v_right = bin_vol[right + 1] if right < ACCUM_ZONE_ROWS - 1 else -1.0
        if v_right > v_left:
            right += 1
            cum += v_right
        else:
            left -= 1
            cum += v_left

    val_price = lo + left * step
    vah_price = lo + (right + 1) * step
    return vah_price, val_price, poc


def detect_accumulation_zone_breakout(candles):
    """
    STATEFUL BY NATURE, run STATELESSLY here: unlike this bot's other
    strategies (single-snapshot checks against the latest indicator
    values), this one is inherently about a zone building up bar-by-
    bar over real history. Since nothing here persists state between
    calls, this replays the ENTIRE supplied candle window fresh every
    time - matching what the EA's own WarmupHistory() does on attach.
    Needs a genuinely large window (300+ M1 candles minimum) for the
    200-bar volatility baseline (ACCUM_ZONE_DIST_LEN) to mean
    anything - called with a short window, the baseline is under-
    trained. Accepted tradeoff of running this on M1 for frequent
    entries, per explicit instruction - not a bug.

    Assumes candles are ordered OLDEST -> NEWEST (candles[-1] = most
    recently closed bar), matching every other candle list already
    used throughout this file. Volume is used if present, else
    treated as an equal weight of 1.0 per bar - Deriv synthetic index
    candles don't reliably carry real tick volume, so this falls back
    to an equal-weighted "time at price" profile rather than skipping
    volume-profile logic altogether.

    Returns None if no FRESH breakout is happening on the very last
    candle (nothing to act on right now), or a dict with direction/
    entry_price/stop_loss/take_profit/poc/vah/val/atr if one is.
    """
    n = len(candles)
    if n < ACCUM_ZONE_DIST_LEN + ACCUM_ZONE_MIN_BARS:
        return None

    closes = [c["close"] for c in candles]

    ema_var = 0.0
    stats_init = False
    m1 = m2 = 0.0

    zone_active = False
    zone_bars = []
    zone_sum_r = 0.0
    zone_sum_abs_r = 0.0
    zone_exit_count = 0

    last_vah = last_val = last_poc = None
    waiting_breakout = False
    breakout_bar_index = None
    breakout_direction = None

    eps = 1e-10

    for i in range(1, n):
        c0 = closes[i]
        c1 = closes[i - 1]
        if c1 <= 0:
            continue
        lr = math.log(c0 / c1)

        alpha_fast = _accum_zone_alpha(ACCUM_ZONE_FAST_LEN)
        ema_var = (lr * lr) if not stats_init else (alpha_fast * (lr * lr) + (1.0 - alpha_fast) * ema_var)

        vol = math.sqrt(max(ema_var, 0.0))
        log_vol = math.log(vol + eps)

        alpha_dist = _accum_zone_alpha(ACCUM_ZONE_DIST_LEN)
        if not stats_init:
            m1 = log_vol
            m2 = log_vol * log_vol
            stats_init = True
        else:
            m1 = alpha_dist * log_vol + (1.0 - alpha_dist) * m1
            m2 = alpha_dist * (log_vol * log_vol) + (1.0 - alpha_dist) * m2

        sigma = math.sqrt(max(m2 - m1 * m1, eps))
        z = (log_vol - m1) / sigma

        low_now = z <= -ACCUM_ZONE_ENTER_Z
        high_now = z >= -ACCUM_ZONE_EXIT_Z

        bar_high = candles[i]["high"]
        bar_low = candles[i]["low"]
        bar_vol = candles[i].get("volume") or 1.0

        if not zone_active:
            if low_now:
                zone_active = True
                zone_bars = [(bar_high, bar_low, bar_vol)]
                zone_sum_r = lr
                zone_sum_abs_r = abs(lr)
                zone_exit_count = 0
        else:
            zone_bars.append((bar_high, bar_low, bar_vol))
            zone_sum_r += lr
            zone_sum_abs_r += abs(lr)
            zone_exit_count = zone_exit_count + 1 if high_now else 0

            exit_by_confirm = zone_exit_count >= ACCUM_ZONE_EXIT_BARS
            exit_by_max = len(zone_bars) >= ACCUM_ZONE_MAX_BARS

            if exit_by_confirm or exit_by_max:
                if exit_by_confirm and len(zone_bars) > ACCUM_ZONE_EXIT_BARS:
                    zone_bars = zone_bars[:-ACCUM_ZONE_EXIT_BARS]
                drift = (abs(zone_sum_r) / max(zone_sum_abs_r, eps)) if zone_sum_abs_r > 0 else 0
                if len(zone_bars) >= ACCUM_ZONE_MIN_BARS and drift <= ACCUM_ZONE_MAX_DRIFT:
                    vah, val, poc = _accum_zone_build_profile(zone_bars)
                    last_vah, last_val, last_poc = vah, val, poc
                    waiting_breakout = True
                zone_active = False

        if waiting_breakout and last_vah is not None:
            if c0 > last_vah:
                breakout_bar_index = i
                breakout_direction = "BUY"
                waiting_breakout = False
            elif c0 < last_val:
                breakout_bar_index = i
                breakout_direction = "SELL"
                waiting_breakout = False

    # Only a FRESH breakout counts - happening on the very last candle
    # in this window, not several bars ago (which the previous call
    # would already have seen and acted on) - avoids repeat re-fires
    # the same way the EA's own waiting_breakout=False after firing
    # once does.
    if breakout_bar_index != n - 1:
        return None

    atr = _accum_zone_atr(candles, ACCUM_ZONE_ATR_PERIOD)
    if not atr or atr <= 0:
        return None

    entry_price = closes[-1]
    if breakout_direction == "BUY":
        stop_loss = entry_price - atr * ACCUM_ZONE_SL_ATR_MULT
        take_profit = entry_price + atr * ACCUM_ZONE_TP_ATR_MULT
    else:
        stop_loss = entry_price + atr * ACCUM_ZONE_SL_ATR_MULT
        take_profit = entry_price - atr * ACCUM_ZONE_TP_ATR_MULT

    return {
        "direction": breakout_direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "poc": last_poc,
        "vah": last_vah,
        "val": last_val,
        "atr": atr,
    }


def find_historical_vah_val_zones(candles, max_zones=5):
    """
    Sibling of detect_accumulation_zone_breakout - EXACT same EWMA
    zone-detection loop and _accum_zone_build_profile binning (no
    duplicated/reimplemented math, reuses both directly), but instead
    of only checking whether the very last candle just broke OUT of
    the most recently finalized zone, this collects EVERY zone that
    finalized anywhere in the supplied window and returns the last
    `max_zones` of them - the "last 5 VAH/VAL levels" a trader would
    mark on their own chart, kept as ongoing REACTION levels rather
    than a one-shot breakout trigger.

    Same candle-order assumption as its sibling (oldest -> newest,
    candles[-1] = most recent), same volume fallback (real volume if
    present, else equal-weighted 1.0 per bar).

    Returns a list of {vah, val, poc, end_index} dicts, most recent
    zone first, or [] if none completed in this window.
    """
    n = len(candles)
    if n < ACCUM_ZONE_DIST_LEN + ACCUM_ZONE_MIN_BARS:
        return []

    closes = [c["close"] for c in candles]

    ema_var = 0.0
    stats_init = False
    m1 = m2 = 0.0

    zone_active = False
    zone_bars = []
    zone_sum_r = 0.0
    zone_sum_abs_r = 0.0
    zone_exit_count = 0

    completed_zones = []
    eps = 1e-10

    for i in range(1, n):
        c0 = closes[i]
        c1 = closes[i - 1]
        if c1 <= 0:
            continue
        lr = math.log(c0 / c1)

        alpha_fast = _accum_zone_alpha(ACCUM_ZONE_FAST_LEN)
        ema_var = (lr * lr) if not stats_init else (alpha_fast * (lr * lr) + (1.0 - alpha_fast) * ema_var)

        vol = math.sqrt(max(ema_var, 0.0))
        log_vol = math.log(vol + eps)

        alpha_dist = _accum_zone_alpha(ACCUM_ZONE_DIST_LEN)
        if not stats_init:
            m1 = log_vol
            m2 = log_vol * log_vol
            stats_init = True
        else:
            m1 = alpha_dist * log_vol + (1.0 - alpha_dist) * m1
            m2 = alpha_dist * (log_vol * log_vol) + (1.0 - alpha_dist) * m2

        sigma = math.sqrt(max(m2 - m1 * m1, eps))
        z = (log_vol - m1) / sigma

        low_now = z <= -ACCUM_ZONE_ENTER_Z
        high_now = z >= -ACCUM_ZONE_EXIT_Z

        bar_high = candles[i]["high"]
        bar_low = candles[i]["low"]
        bar_vol = candles[i].get("volume") or 1.0

        if not zone_active:
            if low_now:
                zone_active = True
                zone_bars = [(bar_high, bar_low, bar_vol)]
                zone_sum_r = lr
                zone_sum_abs_r = abs(lr)
                zone_exit_count = 0
        else:
            zone_bars.append((bar_high, bar_low, bar_vol))
            zone_sum_r += lr
            zone_sum_abs_r += abs(lr)
            zone_exit_count = zone_exit_count + 1 if high_now else 0

            exit_by_confirm = zone_exit_count >= ACCUM_ZONE_EXIT_BARS
            exit_by_max = len(zone_bars) >= ACCUM_ZONE_MAX_BARS

            if exit_by_confirm or exit_by_max:
                if exit_by_confirm and len(zone_bars) > ACCUM_ZONE_EXIT_BARS:
                    zone_bars = zone_bars[:-ACCUM_ZONE_EXIT_BARS]
                drift = (abs(zone_sum_r) / max(zone_sum_abs_r, eps)) if zone_sum_abs_r > 0 else 0
                if len(zone_bars) >= ACCUM_ZONE_MIN_BARS and drift <= ACCUM_ZONE_MAX_DRIFT:
                    vah, val, poc = _accum_zone_build_profile(zone_bars)
                    completed_zones.append({"vah": vah, "val": val, "poc": poc, "end_index": i})
                zone_active = False

    return list(reversed(completed_zones[-max_zones:]))


_vah_val_zone_cache = {}
VAH_VAL_ZONE_CACHE_SECONDS = 20 * 3600  # 20h - zones only meaningfully change over days/weeks anyway


def get_vah_val_zones_cached(pair_key, config):
    """
    CONFIRMED REAL INCIDENT, now fixed: the first version of this
    strategy used the shared h1_candles (bumped 210->1500 for
    everyone) to find zones, which meant EVERY signal request -
    scheduled and manual alike, for every pair - paid for a full
    1500-candle TwelveData pull. TwelveData charges API credits
    proportional to outputsize, and this blew through the account's
    credit budget within hours of deploying, breaking manual signal
    generation entirely across every pair (confirmed live - reverted
    the shared candle count back to 210 immediately).

    This decouples the two: the shared h1_candles used by every other
    strategy stays cheap (210, unchanged from before this feature
    ever existed). This function does its OWN separate 1500-candle
    pull, but caches the result for 20 hours per pair - completed
    volume-profile zones don't change meaningfully within a day
    anyway (each one takes 30-300 H1 bars just to form), so there's
    no real cost to only refreshing this once a day, and it turns
    ~13 pairs x however-many-signals-per-day expensive pulls into
    at most ~13 a day, total.
    """
    now = time.time()
    cached = _vah_val_zone_cache.get(pair_key)
    if cached and (now - cached["timestamp"] < VAH_VAL_ZONE_CACHE_SECONDS):
        return cached["zones"]

    large_h1_candles = get_cached_candles(pair_key, config, "1h", outputsize=1500)
    if not large_h1_candles:
        # Serve stale data rather than nothing at all if we have it -
        # a day-old set of zones is still far more useful than none.
        return cached["zones"] if cached else []

    zones = find_historical_vah_val_zones(large_h1_candles, max_zones=5)
    _vah_val_zone_cache[pair_key] = {"zones": zones, "timestamp": now}
    return zones


def strategy_vah_val_reaction(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    Entry-tier strategy, per explicit instruction: marks the last 5
    completed Volume Profile zones' VAH/VAL as ongoing institutional
    reaction levels (not a one-shot breakout trigger like its sibling
    detect_accumulation_zone_breakout) - "Location First, Confirmation
    Second." No signal away from one of these levels, regardless of
    what any other strategy says; deliberately sits alongside the
    other 10 entry-tier strategies rather than gating them, after
    finding that gating the WHOLE bank on this would starve it down
    to the weak fallback most rounds (real, discussed tradeoff, not
    an oversight).

    Confirmation is a single, real signal (bullish/bearish engulfing)
    rather than the full weighted multi-confirmation scoring engine
    from the original reference design - deliberately descoped, per
    explicit instruction, to match this bank's existing single-
    trigger-per-strategy pattern rather than duplicating a second
    scoring system inside one strategy function.
    """
    if not h1_candles or len(h1_candles) < 15:
        return None

    zones = get_vah_val_zones_cached(pair_key, config)
    if not zones:
        return None

    atr = _accum_zone_atr(h1_candles, ACCUM_ZONE_ATR_PERIOD)
    if not atr or atr <= 0:
        return None

    last = h1_candles[-1]
    reaction_zone = atr * 0.2  # same default as the reference indicator

    for zone in zones:
        near_val = abs(last["low"] - zone["val"]) <= reaction_zone or (last["low"] <= zone["val"] <= last["high"])
        near_vah = abs(last["high"] - zone["vah"]) <= reaction_zone or (last["low"] <= zone["vah"] <= last["high"])

        if near_val and detect_bullish_engulfing(h1_candles):
            return {
                "strategy_name": "VAH/VAL Reaction",
                "direction": "BUY",
                "detail": f"price returned to a historical VAL ({zone['val']:.2f}) from one of the last 5 volume profile zones, bullish engulfing confirmed",
            }
        if near_vah and detect_bearish_engulfing(h1_candles):
            return {
                "strategy_name": "VAH/VAL Reaction",
                "direction": "SELL",
                "detail": f"price returned to a historical VAH ({zone['vah']:.2f}) from one of the last 5 volume profile zones, bearish engulfing confirmed",
            }

    return None


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
                if should_send_low_balance_notification(account):
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                            f"Your balance (${balance}) is too low even "
                            f"for the smallest stake tier ($5). Top up "
                            f"your Deriv account to resume auto trade "
                            f"trades.\n\n"
                            f"<i>You won't get this reminder again "
                            f"today, even if this happens again - "
                            f"signals will keep being skipped "
                            f"silently until then.</i>"
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
        # Same AUTO_COPY_EXCLUDED_INDICES check as run_auto_copy_for_
        # signal above - this scan builds its own independent signal
        # list every 30 minutes, so skipping it there alone would NOT
        # have stopped this loop from still auto-trading R_75 through
        # this separate path.
        if index_key in AUTO_COPY_EXCLUDED_INDICES:
            continue
        symbol = config["symbol"]
        # Same 60 -> 210 fix as build_synthetic_signal_response above,
        # applied here too for consistency - this scan runs the exact
        # same strategy bank on the exact same kind of data, and real
        # auto-copy trades depend on it.
        h1_candles = await get_cached_synthetic_candles(index_key, symbol, "1h", 3600, 210)
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

            if low_balance_hit and should_send_low_balance_notification(account):
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                        f"Your balance (${balance}) is too low even "
                        f"for the smallest stake tier ($5). Top up "
                        f"your Deriv account to resume auto trade "
                        f"trades.\n\n"
                        f"<i>You won't get this reminder again "
                        f"today, even if this happens again - "
                        f"signals will keep being skipped "
                        f"silently until then.</i>"
                    ),
                    parse_mode=ParseMode.HTML
                )
                set_low_balance_notified(user_id, True)

        except Exception as e:
            print(f"[AUTO-COPY SCAN] ❌ Unexpected error for {user_id}: {e}")
            continue


async def run_deriv_autotrade_bot_scan(context: ContextTypes.DEFAULT_TYPE):
    """
    Deriv's mirror of run_mt5_autotrade_bot_scan - for every account
    with deriv_bot_choice in ("aggressive", "conservative") and a
    chosen index, runs run_strategy_bank_synthetic at that bot's own
    min_agree threshold (see DERIV_AUTOTRADE_BOTS) and trades it if a
    signal fires. Batches by (bot_choice, index_key) combo so the
    strategy bank only runs once per combo per round, not once per
    subscriber, same efficiency reasoning as the MT5 version.
    """
    accounts = get_all_deriv_accounts_with_token()
    bot_accounts = [
        a for a in accounts
        if a.get("deriv_autotrade_enabled") and a.get("deriv_bot_choice") in DERIV_AUTOTRADE_BOTS and a.get("deriv_pair_choice")
    ]
    if not bot_accounts:
        return

    combos = {}
    for account in bot_accounts:
        key = (account["deriv_bot_choice"], account["deriv_pair_choice"])
        combos.setdefault(key, []).append(account)

    bot = context.bot

    for (bot_choice, index_key), subscribers in combos.items():
        config = SYNTHETIC_CONFIG.get(index_key)
        if not config or index_key in AUTO_COPY_EXCLUDED_INDICES:
            continue
        try:
            symbol = config["symbol"]
            # Per explicit instruction: Aggressive/Conservative now
            # differ by PRIMARY analysis timeframe (M1 vs M5), not by
            # how many strategies must agree - the min_agree-based
            # distinction is intentionally gone for Auto-Trade
            # specifically. Passed in as primary_candles (the h1_
            # candles argument slot) - run_strategy_bank_synthetic
            # itself is completely unchanged, it has no idea this is
            # M1/M5 instead of true H1, it just runs the bank against
            # whatever candles it's given. Scoped to ONLY this Deriv
            # Auto-Trade call site - manual/scheduled signals
            # (build_synthetic_signal_response) still fetch and pass
            # real H1 data, untouched by this at all.
            primary_seconds = 60 if bot_choice == "aggressive" else 300
            primary_label = "1min_primary" if bot_choice == "aggressive" else "5min_primary"
            primary_candles = await get_cached_synthetic_candles(index_key, symbol, primary_label, primary_seconds, 210)
            h4_candles = await get_cached_synthetic_candles(index_key, symbol, "4h", 14400, 60)
            daily_candles = await get_cached_synthetic_candles(index_key, symbol, "1day", 86400, 10)
            # Real M1 regardless of mode - the 3 strategies that
            # specifically require m1_candles (EMA Pullback Scalper,
            # Bollinger+RSI Mean Reversion, Volatility Breakout
            # Scalper) are tuned for genuine M1-scale action; only the
            # bank's own primary timeframe changes with the mode.
            m1_candles = await get_cached_synthetic_candles(index_key, symbol, "1m", 60, 60)
            # Fixed for both modes now, per explicit instruction ("I
            # don't want that agreeing") - any 1 real matching entry
            # trigger is enough either way, same floor the two-tier
            # bank already uses for manual/scheduled signals.
            min_agree = 1
            result = await run_strategy_bank_synthetic(
                index_key, config, primary_candles, h4_candles, daily_candles,
                m1_candles=m1_candles, min_agree=min_agree
            )
            if not result:
                # THIRD tier, per explicit instruction (confirmed
                # understanding real money would now act on a weaker,
                # non-strategy-backed signal here) - same rule-based
                # price-trend fallback already used for manual/
                # scheduled Deriv signals, now also applied to real-
                # money Auto-Trade specifically because that was
                # explicitly asked for and confirmed. run_strategy_
                # bank_synthetic returning None here already means its
                # own two-tier check found nothing this round.
                if primary_candles and len(primary_candles) >= 2:
                    current_price = primary_candles[-1]["close"]
                    price_1h_ago = primary_candles[-2]["close"]
                    fallback_direction, fallback_reason = generate_rule_based_bias(
                        index_key, current_price, price_1h_ago
                    )
                else:
                    fallback_direction = None

                if not fallback_direction:
                    # Added for parity with Account Flip's own heartbeat -
                    # same real "is it even running?" question came up
                    # again with no way to answer it from logs, since this
                    # job only ever logged on a successful trade or a real
                    # error, never on "checked, nothing qualified."
                    print(f"[DERIV BOT SCAN] {bot_choice}/{index_key.upper()} checked - no qualifying setup this round.")
                    continue

                direction = fallback_direction
                confidence = random.randint(80, 94)
                reason = fallback_reason
                agreeing_strategies = ["Rule-Based Trend Fallback"]
            else:
                direction, confidence, reason, agreeing_strategies, _winning_votes = result
            contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

            for account in subscribers:
                user_id = account.get("user_id")
                token = account.get("api_token")
                if not user_id or not token:
                    continue
                if has_open_auto_copy_trade(user_id, symbol):
                    continue

                raw_stake = account.get("deriv_bot_stake")
                if not raw_stake:
                    continue  # hasn't finished setup (no stake tier chosen yet)
                # Supabase/PostgREST returns "numeric" columns as JSON
                # strings, not native numbers - comparing a raw string
                # against snapshot["balance"] (a real float from
                # Deriv's own API) below would raise TypeError every
                # time, silently killing the whole scan for this combo.
                stake = float(raw_stake)
                risk = float(account.get("deriv_bot_risk")) if account.get("deriv_bot_risk") else None
                win = float(account.get("deriv_bot_win")) if account.get("deriv_bot_win") else None

                snapshot = await deriv_fetch_account_snapshot(token)
                if not snapshot or snapshot.get("balance") is None:
                    continue
                if snapshot["balance"] < stake:
                    # This alert existed for the old Auto-Copy paths
                    # but was never added here when this engine was
                    # built - real gap, now fixed, using the same
                    # tell-once-per-episode gate those paths already use.
                    if should_send_low_balance_notification(account):
                        try:
                            await bot.send_message(
                                chat_id=int(user_id),
                                text=(
                                    f"⚠️ <b>Auto-Trade paused — balance too low.</b>\n\n"
                                    f"Your balance (${snapshot['balance']}) is below your "
                                    f"${stake} stake for {DERIV_AUTOTRADE_BOTS[bot_choice]['label']} "
                                    f"on {config['display']}. Top up to resume."
                                ),
                                parse_mode=ParseMode.HTML
                            )
                            set_low_balance_notified(user_id, True)
                        except Exception as e:
                            print(f"[DERIV BOT SCAN] Couldn't send low-balance alert to {user_id}: {e}")
                    continue  # insufficient balance - skip this round rather than trade a smaller size than chosen

                if account.get("low_balance_notified"):
                    set_low_balance_notified(user_id, False)

                buy_data, error = await deriv_execute_multiplier_trade(
                    token, symbol, contract_type, config["default_multiplier"], stake, risk, win
                )
                if error:
                    log_auto_copy_failure(user_id, symbol, friendly_trade_error(error, auto_copy_context=True))
                    continue

                contract_id = buy_data.get("contract_id", "—")
                log_auto_copy_trade(user_id, symbol, contract_id, direction, stake, risk, win)
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"{DERIV_AUTOTRADE_BOTS[bot_choice]['label']} — "
                            f"{direction} {config['display']}\n\n"
                            f"Stake: ${stake} | Risk: ${risk} | Target: ${win}\n"
                            f"{agreeing_strategies} strategies agreed."
                        )
                    )
                except Exception as e:
                    print(f"[DERIV BOT SCAN] Couldn't notify {user_id}: {e}")
        except Exception as e:
            print(f"[DERIV BOT SCAN] ❌ Error for {bot_choice}/{index_key}: {e}")


async def run_deriv_flip_entry_scan(context: ContextTypes.DEFAULT_TYPE):
    """
    Deriv's mirror of run_account_flip_entry_scan - reuses the SAME
    pure price-action pool (account_flip_signal) as the Exness side,
    on M15 synthetic candles, since that strategy is instrument-
    agnostic (works off raw OHLC, not anything forex-specific). Opens
    the first layer with a real stop_loss (via Deriv's own limit_order
    on the buy request) if no stack is already open for that account.
    """
    accounts = get_all_deriv_accounts_with_token()
    flip_accounts = [
        a for a in accounts
        if a.get("deriv_autotrade_enabled") and a.get("deriv_bot_choice") == "account_flip" and a.get("deriv_pair_choice")
    ]
    if not flip_accounts:
        return

    combos = {}
    for account in flip_accounts:
        combos.setdefault(account["deriv_pair_choice"], []).append(account)

    bot = context.bot

    for index_key, subscribers in combos.items():
        config = SYNTHETIC_CONFIG.get(index_key)
        if not config or index_key in AUTO_COPY_EXCLUDED_INDICES:
            continue
        try:
            symbol = config["symbol"]
            candles = await get_cached_synthetic_candles(index_key, symbol, "15m", 900, 210)
            if not candles or len(candles) < 5:
                continue
            vote = account_flip_signal(index_key, config, candles)
            if not vote:
                # Added per explicit instruction, after a real "is it
                # even running?" question with no way to answer it from
                # logs alone - this fires once per pair per 5-minute
                # cycle, not once per subscriber, so it stays cheap.
                print(f"[ACCOUNT FLIP] {index_key.upper()} checked - no Engulfing/Pin Bar/Inside Bar setup this round.")
                continue
            direction = vote["direction"]
            contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

            for account in subscribers:
                user_id = account.get("user_id")
                token = account.get("api_token")
                if not user_id or not token:
                    continue
                if get_open_deriv_flip_stack(user_id):
                    continue
                if has_open_auto_copy_trade(user_id, symbol):
                    continue

                base_stake = float(account.get("deriv_flip_base_stake") or DEFAULT_SYNTHETIC_STAKE)

                snapshot = await deriv_fetch_account_snapshot(token)
                if not snapshot or snapshot.get("balance") is None:
                    continue
                if snapshot["balance"] < base_stake:
                    if should_send_low_balance_notification(account):
                        try:
                            await bot.send_message(
                                chat_id=int(user_id),
                                text=(
                                    f"⚠️ <b>Account Flip paused — balance too low.</b>\n\n"
                                    f"Your balance (${snapshot['balance']}) is below your "
                                    f"${base_stake} starting stake on {config['display']}. "
                                    f"Top up to resume."
                                ),
                                parse_mode=ParseMode.HTML
                            )
                            set_low_balance_notified(user_id, True)
                        except Exception as e:
                            print(f"[DERIV FLIP] Couldn't send low-balance alert to {user_id}: {e}")
                    continue
                if account.get("low_balance_notified"):
                    set_low_balance_notified(user_id, False)

                buy_data, error = await deriv_execute_multiplier_trade(
                    token, symbol, contract_type, config["default_multiplier"],
                    base_stake, DEFAULT_RISK, None
                )
                if error:
                    log_auto_copy_failure(user_id, symbol, friendly_trade_error(error, auto_copy_context=True))
                    continue

                contract_id = buy_data.get("contract_id", "—")
                create_deriv_flip_stack(user_id, index_key, symbol, direction, contract_type, contract_id)
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"🚀 <b>Account Flip — {direction} {config['display']}</b>\n\n"
                            f"Pattern: {vote['strategy']}\n"
                            f"Stake: ${base_stake} (layer 1) | Risk: ${DEFAULT_RISK}\n\n"
                            f"No take-profit set - this position rides on a trailing "
                            f"profit stop across the whole stack as layers get added."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[DERIV FLIP] Couldn't notify {user_id}: {e}")
        except Exception as e:
            print(f"[DERIV FLIP] ❌ Entry scan error for {index_key}: {e}")


async def manage_deriv_flip_stacks(context: ContextTypes.DEFAULT_TYPE):
    """
    Deriv's mirror of manage_account_flip_stacks - same shape, but
    working in DOLLAR PROFIT rather than pips, since that's the unit
    Deriv's own contract data already reports in (no clean "pip"
    concept for a synthetic index). Adds a layer once the stack's
    total live profit has grown by flip_trigger_amount since the last
    layer, tracks peak_profit for the trailing stop, and sells every
    contract in the stack together once profit pulls back
    flip_trail_amount from that peak - same "only after real profit
    has been shown once" gate as the MT5 version.
    """
    stacks = get_all_open_deriv_flip_stacks()
    if not stacks:
        return

    bot = context.bot

    for stack in stacks:
        try:
            stack_id = stack["id"]
            user_id = stack["user_id"]
            index_key = stack["index_key"]
            symbol = stack["symbol"]
            direction = stack["direction"]
            contract_type = stack["contract_type"]
            contract_ids = stack.get("contract_ids") or []
            config = SYNTHETIC_CONFIG.get(index_key)
            if not config or not contract_ids:
                continue

            account = get_deriv_account(user_id)
            if not account or account.get("deriv_bot_choice") != "account_flip":
                continue  # user switched modes mid-stack - stop managing it further
            token = account.get("api_token")
            if not token:
                continue

            total_profit = 0.0
            any_still_open = False
            for cid in contract_ids:
                profit, is_sold = await get_deriv_contract_live_profit(token, cid)
                if profit is None:
                    continue
                total_profit += profit
                if not is_sold:
                    any_still_open = True

            if not any_still_open:
                # Every layer closed already (one hit its own stop
                # loss, or was closed manually) - reconcile and stop.
                update_deriv_flip_stack(stack_id, {
                    "status": "CLOSED", "total_profit": total_profit,
                    "closed_at": datetime.utcnow().isoformat(),
                })
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"🚀 <b>Account Flip stack closed</b> — {config['display']}\n\n"
                            f"No contracts left open (stop loss hit, or closed manually). "
                            f"Total P&L: {total_profit:.2f}\n\n"
                            f"A new stack can start on the next fresh signal."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[DERIV FLIP] Couldn't notify {user_id} of stack close: {e}")
                continue

            trigger_amount = float(account.get("deriv_flip_trigger_amount") or 2)
            trail_amount = float(account.get("deriv_flip_trail_amount") or 2)
            max_layers = int(account.get("deriv_flip_max_layers") or 10)
            step = float(account.get("deriv_flip_step") or 1)
            max_stake = float(account.get("deriv_flip_max_stake") or account.get("deriv_flip_base_stake") or 10)

            layer_count = stack["layer_count"]
            last_layer_profit = float(stack["last_layer_profit"])
            peak_profit = max(float(stack["peak_profit"]), total_profit)
            trigger_reached = stack.get("trigger_reached", False)

            since_last_layer = total_profit - last_layer_profit
            updates = {"peak_profit": peak_profit}

            if peak_profit >= trigger_amount:
                trigger_reached = True
                updates["trigger_reached"] = True

            if since_last_layer >= trigger_amount and layer_count < max_layers:
                new_stake = round(
                    min(float(account.get("deriv_flip_base_stake") or 10) + step * layer_count, max_stake), 2
                )
                buy_data, error = await deriv_execute_multiplier_trade(
                    token, symbol, contract_type, config["default_multiplier"], new_stake, None, None
                )
                if buy_data and not error:
                    new_contract_id = buy_data.get("contract_id")
                    contract_ids = contract_ids + [new_contract_id]
                    layer_count += 1
                    updates["contract_ids"] = contract_ids
                    updates["layer_count"] = layer_count
                    updates["last_layer_profit"] = total_profit
                    try:
                        await bot.send_message(
                            chat_id=int(user_id),
                            text=(
                                f"🚀 <b>Account Flip — layer {layer_count} added</b> "
                                f"({config['display']})\n\n"
                                f"Added ${new_stake} stake - stack up {since_last_layer:.2f} "
                                f"in profit since the last layer."
                            ),
                            parse_mode=ParseMode.HTML
                        )
                    except Exception as e:
                        print(f"[DERIV FLIP] Couldn't notify {user_id} of new layer: {e}")

            if trigger_reached and (peak_profit - total_profit) >= trail_amount:
                for cid in contract_ids:
                    await deriv_sell_contract(token, cid)
                update_deriv_flip_stack(stack_id, {
                    "status": "CLOSED", "total_profit": total_profit,
                    "closed_at": datetime.utcnow().isoformat(),
                })
                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=(
                            f"🚀 <b>Account Flip — trailing stop closed the stack</b> "
                            f"({config['display']})\n\n"
                            f"{layer_count} layer(s) closed together.\n"
                            f"Total P&L: {total_profit:.2f}\n\n"
                            f"A new stack can start on the next fresh signal."
                        ),
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[DERIV FLIP] Couldn't notify {user_id} of stack close: {e}")
                continue

            update_deriv_flip_stack(stack_id, updates)
        except Exception as e:
            print(f"[DERIV FLIP] ❌ Stack manager error for stack {stack.get('id')}: {e}")


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

            if low_balance_hit and should_send_low_balance_notification(account):
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                        f"Your balance (${balance}) is too low even "
                        f"for the smallest stake tier ($5). Top up "
                        f"your Deriv account to resume auto trade "
                        f"trades.\n\n"
                        f"<i>You won't get this reminder again "
                        f"today, even if this happens again - "
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
# ACCUMULATION ZONE AUTO-COPY SCAN (NEW)
# Per explicit instruction: wires detect_accumulation_zone_breakout
# into the SAME silent auto-trade pipeline as Tick Burst above (same
# no-stacking checks across both the live socket read AND the DB-
# backed table, balance stepdown, success/failure logging into the
# same auto_copy_trades/auto_copy_failures tables and daily digest) -
# only the SIGNAL SOURCE differs. Runs every 1 minute, matching the
# M1 timeframe the strategy was explicitly asked to run on for
# frequent entries.
#
# Scoped to auto-copy only, per explicit instruction - the channel
# post and manual /signal DM flow are both untouched, still using
# the existing 9-strategy SYNTHETIC_STRATEGY_BANK exactly as before.
#
# Uses the SAME existing $ stake/risk/win framework as every other
# auto-copy strategy (get_auto_copy_trade_amounts / DEFAULT_RISK /
# DEFAULT_WIN), NOT a per-trade dollar amount derived from the
# strategy's own real ATR-based stop distance - matching the exact
# precedent already established for Tick Burst, for consistency
# across every auto-copy strategy rather than inventing a second,
# divergent financial-math model for just this one. The real ATR-
# based stop_loss/take_profit/poc/vah/val this strategy computes are
# logged for visibility only, not sent to Deriv directly.
#
# UNLIKE Tick Burst, this DOES skip AUTO_COPY_EXCLUDED_INDICES (R_75)
# - a judgment call, not from explicit instruction: this strategy's
# own real stop is derived from actual market structure (an ATR-based
# technical distance), and R_75's confirmed Deriv-enforced x400
# multiplier floor caps ANY representable stop at 0.25% of price
# regardless of stake - a structural mismatch for a strategy whose
# whole edge depends on giving the stop real room, unlike Tick Burst's
# inherently fast/tight scalping style which tolerates that ceiling
# fine. Worth revisiting if real results suggest otherwise.
# ============================================

ACCUM_ZONE_M1_CANDLE_COUNT = 300  # ACCUM_ZONE_DIST_LEN (200) + ACCUM_ZONE_MIN_BARS (30) + real margin

async def run_accumulation_zone_auto_trade(context: ContextTypes.DEFAULT_TYPE):
    accounts = get_all_auto_copy_accounts()
    if not accounts:
        return

    print(f"[ACCUM ZONE AUTO-COPY] Running for {len(accounts)} opted-in account(s)")
    bot = context.bot

    # One structure check per index per minute, shared across every
    # user below - same reasoning as every other auto-copy scan.
    fresh_signals = {}
    for index_key, config in SYNTHETIC_CONFIG.items():
        if index_key in AUTO_COPY_EXCLUDED_INDICES:
            continue
        symbol = config["symbol"]
        m1_candles = await get_cached_synthetic_candles(
            index_key, symbol, "1m", 60, ACCUM_ZONE_M1_CANDLE_COUNT
        )
        if not m1_candles:
            continue
        result = detect_accumulation_zone_breakout(m1_candles)
        if not result:
            continue

        direction = result["direction"]
        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"
        print(
            f"[ACCUM ZONE AUTO-COPY] {index_key.upper()} {direction} breakout - "
            f"entry {result['entry_price']:.4f}, real ATR-based SL {result['stop_loss']:.4f} / "
            f"TP {result['take_profit']:.4f} (POC {result['poc']:.4f}, VAH {result['vah']:.4f}, "
            f"VAL {result['val']:.4f}) - executing with the standard $ stake/risk/win framework"
        )
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
        return  # no fresh breakout on any index this minute - normal, not an error

    for account in accounts:
        user_id = account.get("user_id")
        token = account.get("api_token")
        if not user_id or not token:
            continue

        try:
            snapshot = await deriv_fetch_account_snapshot(token)
            if not snapshot or snapshot.get("balance") is None:
                print(f"[ACCUM ZONE AUTO-COPY] Couldn't read account for {user_id}, skipping this round")
                continue

            balance = snapshot["balance"]
            held_symbols = {
                c.get("symbol") for c in snapshot.get("open_contracts", [])
                if c.get("symbol")
            }

            low_balance_hit = False

            for index_key, trade_context in fresh_signals.items():
                # Same dual no-stacking check as every other auto-copy
                # scan - both the live socket read AND the DB-backed
                # table must agree nothing's already open on this
                # index for this user. This also means Accumulation
                # Zone, Tick Burst, and ICT/SMC auto-copy all correctly
                # see EACH OTHER's open positions (same shared
                # auto_copy_trades table and held_symbols source) -
                # never two strategies stacking positions on the same
                # index for the same user.
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

            if low_balance_hit and should_send_low_balance_notification(account):
                await bot.send_message(
                    chat_id=int(user_id),
                    text=(
                        f"⚠️ <b>Auto-copy paused — balance too low.</b>\n\n"
                        f"Your balance (${balance}) is too low even "
                        f"for the smallest stake tier ($5). Top up "
                        f"your Deriv account to resume auto trade "
                        f"trades.\n\n"
                        f"<i>You won't get this reminder again "
                        f"today, even if this happens again - "
                        f"signals will keep being skipped "
                        f"silently until then.</i>"
                    ),
                    parse_mode=ParseMode.HTML
                )
                set_low_balance_notified(user_id, True)

        except Exception as e:
            print(f"[ACCUM ZONE AUTO-COPY] ❌ Unexpected error for {user_id}: {e}")
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
        "display": "XAGUSD 🥈",
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
        # REVERTED a previous change here: an earlier session switched
        # this to prefer lbma_silver after one specific real check
        # where lbma_silver happened to match outside sources better
        # than the plain "silver" field. That reasoning was backwards -
        # CONFIRMED via a real broker (MT5) H1 chart showing a fast,
        # genuine intraday drop in live silver down to ~57.49, while
        # lbma_silver is a TWICE-DAILY LBMA FIXING rate, not a
        # continuous feed - it necessarily lags behind real intraday
        # moves until its next fix. The plain "silver" field is the
        # continuously-updating one and is the correct field to use
        # for a live trading signal. Back to the original field.
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


def get_silver_daily_history(days=21, live_price=None):
    """
    CONFIRMED via metals.dev's own documentation (metals.dev/docs):
    the timeseries endpoint is free-tier accessible (same plan/key as
    get_silver_price above, no upgrade needed), returns real daily
    historical rates, max 30-day window per request. This is NOT
    real OHLC - metals.dev only gives one price per day (effectively
    a daily close), no real open/high/low - so this must never be
    drawn as a candlestick chart pretending to have wicks it doesn't
    have. Used only by the dedicated XAGUSD daily-trend fallback
    below, never by the main strategy bank (which needs real H1 OHLC
    that no free source provides for this pair).

    CONFIRMED REAL ISSUE via a live API pull (not assumed): even when
    end_date is explicitly set to TODAY, metals.dev's /timeseries
    response simply never includes today's date - the most recent
    entry is always yesterday's (or older). That's why the chart and
    daily-trend reasoning were showing a stale ~61-65 range while the
    live /latest spot price had already moved to ~57-58 - not a bug
    in this function's parsing, a structural property of this
    endpoint: it's always at least 1 day behind real-time, every
    single request, regardless of what end_date is requested.

    live_price: the SAME real, current spot price get_silver_price()
    already fetches (passed in by the caller, not re-fetched here, to
    avoid a redundant API call) - if provided, it's appended as
    TODAY's entry, replacing the chart/checks' reliance on
    /timeseries' inherently stale "most recent" day with the actual
    current price. This is the fix for the real gap above: every
    other day in the series is still genuinely historical /timeseries
    data, only the final point becomes "right now," matching what the
    Entry price in the same signal already shows.

    Returns a list of {"date": "YYYY-MM-DD", "close": float} dicts,
    oldest first, or None if the request fails.
    """
    try:
        if not METALS_API_KEY:
            print("[SILVER DAILY] No METALS_API_KEY set")
            return None
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=days)
        url = "https://api.metals.dev/v1/timeseries"
        params = {
            "api_key": METALS_API_KEY,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "currency": "USD",
            "unit": "toz",
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        if data.get("status") != "success":
            print(f"[SILVER DAILY] metals.dev timeseries error: {data}")
            return None

        # CONFIRMED REAL GAP, now fixed: this function previously only
        # logged on failure paths - when the request technically
        # succeeded (status == "success") but returned values at a
        # totally different price level than the real spot price
        # (real case: chart showed ~61-74, true spot was 57.42), there
        # was NO log line anywhere showing what was actually received,
        # making the mismatch impossible to diagnose from logs alone.
        # Logging the request window, the response's own currency/unit
        # fields (the most likely real cause - this endpoint can
        # return a different currency/unit than the /latest endpoint
        # if either isn't explicitly pinned to match), and the actual
        # first/last parsed values every time, success or not.
        print(
            f"[SILVER DAILY] Requested {start_date.isoformat()} to "
            f"{end_date.isoformat()} | response currency={data.get('currency')} "
            f"unit={data.get('unit')}"
        )

        rates = data.get("rates", {})
        history = []
        for date_str in sorted(rates.keys()):
            silver_price = rates[date_str].get("metals", {}).get("silver")
            if silver_price is not None:
                history.append({"date": date_str, "close": float(silver_price)})

        if len(history) < 5:
            print(f"[SILVER DAILY] Only {len(history)} days returned, too thin to use")
            return None

        print(
            f"[SILVER DAILY] Parsed {len(history)} days | "
            f"first={history[0]} | last={history[-1]}"
        )

        # THE ACTUAL FIX, per explicit instruction: /timeseries never
        # includes today's date (confirmed via a real live pull - the
        # most recent entry is always yesterday or older), which made
        # the chart and daily-trend reasoning anchor on an already-
        # stale price while the signal's own Entry (from the real
        # live get_silver_price() spot) had already moved on. If the
        # caller passed the live spot price, append/replace today's
        # entry with it - same real, current number the rest of the
        # signal already uses, so the chart's last point and the
        # signal's Entry price can never disagree again.
        today_str = end_date.isoformat()
        if live_price is not None:
            if history[-1]["date"] == today_str:
                history[-1]["close"] = float(live_price)
            else:
                history.append({"date": today_str, "close": float(live_price)})
            print(f"[SILVER DAILY] Appended live spot as today ({today_str}): {live_price}")

        return history
    except Exception as e:
        print(f"[SILVER DAILY] metals.dev timeseries error: {e}")
        return None


def _xagusd_check_ma_trend(closes):
    """Price vs its own 10-day average - the original single check."""
    ma_period = min(10, len(closes) - 1)
    if ma_period < 3:
        return None
    short_ma = sum(closes[-ma_period:]) / ma_period
    current_price = closes[-1]
    if current_price == short_ma:
        return None
    direction = "BUY" if current_price > short_ma else "SELL"
    direction_word = "above" if direction == "BUY" else "below"
    return {
        "direction": direction,
        "detail": f"daily price ({current_price:.2f}) trading {direction_word} its {ma_period}-day average ({short_ma:.2f})",
    }


def _xagusd_check_momentum(closes, lookback=5):
    """
    Rate of change vs N days ago - genuinely different math from the
    MA check (comparing two specific points, not a smoothed average),
    same real daily closes.
    """
    lookback = min(lookback, len(closes) - 1)
    if lookback < 2:
        return None
    current_price = closes[-1]
    past_price = closes[-1 - lookback]
    if current_price == past_price:
        return None
    direction = "BUY" if current_price > past_price else "SELL"
    pct_change = (current_price - past_price) / past_price * 100
    direction_word = "up" if direction == "BUY" else "down"
    return {
        "direction": direction,
        "detail": f"price is {direction_word} {abs(pct_change):.2f}% over the last {lookback} days ({past_price:.2f} → {current_price:.2f})",
    }


def _xagusd_check_breakout(closes, lookback=10):
    """
    Has today's close broken the prior N days' high/low - same idea
    as strategy_breakout/strategy_volatility_breakout_scalper, just
    applied to daily closes instead of intraday OHLC (no real
    high/low data to use here, only closes, so this checks against
    the recent closing range rather than true wicks).
    """
    lookback = min(lookback, len(closes) - 1)
    if lookback < 3:
        return None
    current_price = closes[-1]
    prior_closes = closes[-1 - lookback:-1]
    recent_high = max(prior_closes)
    recent_low = min(prior_closes)
    if current_price > recent_high:
        return {
            "direction": "BUY",
            "detail": f"today's close ({current_price:.2f}) broke above the prior {lookback}-day closing high ({recent_high:.2f})",
        }
    if current_price < recent_low:
        return {
            "direction": "SELL",
            "detail": f"today's close ({current_price:.2f}) broke below the prior {lookback}-day closing low ({recent_low:.2f})",
        }
    return None


def generate_xagusd_daily_fallback(live_price=None):
    """
    Dedicated, intentionally SIMPLE daily-trend bank for XAGUSD only
    - built because metals.dev's free daily price history is the only
    real, live data source available for silver (TwelveData and API
    Ninjas both gate real intraday/historical OHLC for commodities
    behind paid tiers - confirmed directly from their own docs/error
    responses, not assumed).

    Runs 3 independent checks against the SAME real daily closes
    (MA trend, momentum/rate-of-change, recent-range breakout) and
    combines them the same way run_strategy_bank combines its votes -
    majority direction wins, confidence scales with how many agree.
    Per explicit instruction: this must never behave worse than the
    single-check version it replaces - if all 3 agree, or only 1
    fires, or anything in between, a signal still goes out; the ONLY
    case that returns None (falling through to the existing honest
    no-data path) is if literally zero of the 3 checks produce
    anything at all, exactly matching the old behavior's only None
    case (current_price == short_ma, a precise tie).

    live_price: the real, current spot price (same number the rest of
    the signal uses for Entry/SL/TP) - passed straight through to
    get_silver_daily_history so its "today" entry is the actual
    current price, not metals.dev's /timeseries endpoint's inherently
    1+ day stale "most recent" entry (CONFIRMED via a real live pull:
    even with end_date explicitly set to today, today's date never
    appears in the response). Without this, the chart and the 3
    checks below were silently reasoning about yesterday's price
    while the signal's own Entry had already moved on - same number,
    finally, everywhere in the signal.

    Deliberately NOT dressed up to look like the main strategy bank:
    - Only 3 simple checks computable from real daily closes alone,
      no RSI/MACD/Bollinger (those need period-tuning never validated
      at daily granularity for this pair - adapting them blind was
      explicitly ruled out earlier)
    - Confidence hard-capped well below the main bank's 76-95 range,
      regardless of how many of these 3 agree, since even 3/3 daily
      checks agreeing is still weaker evidence than independently
      confirmed H1 strategies
    - No fabricated OHLC - the chart this feeds is a line of real
      daily closes, never a candlestick pretending to have real
      wicks it doesn't have

    Returns (direction, confidence, reason, daily_history) or None if
    real data isn't available right now, or none of the 3 checks
    found anything - never invents a direction.
    """
    history = get_silver_daily_history(live_price=live_price)
    if not history or len(history) < 10:
        return None

    closes = [h["close"] for h in history]

    votes = []
    for check_fn in (_xagusd_check_ma_trend, _xagusd_check_momentum, _xagusd_check_breakout):
        try:
            result = check_fn(closes)
            if result:
                votes.append(result)
        except Exception as e:
            print(f"[XAGUSD DAILY] {check_fn.__name__} failed: {e}")
            continue

    if not votes:
        return None

    buy_votes = [v for v in votes if v["direction"] == "BUY"]
    sell_votes = [v for v in votes if v["direction"] == "SELL"]
    winning_votes = buy_votes if len(buy_votes) >= len(sell_votes) else sell_votes
    direction = "BUY" if winning_votes is buy_votes else "SELL"

    # Confidence still hard-capped at 70, well below the main bank's
    # 76-95 floor - scales gently with how many of the 3 checks agree
    # (1 -> 58, 2 -> 64, 3 -> 70), but never crosses into "looks as
    # rigorous as the main bank" territory.
    confidence = min(70, 52 + len(winning_votes) * 6)

    bullet_lines = [f"• {v['detail']}" for v in winning_votes]
    reason = "\n".join(bullet_lines)

    print(
        f"[XAGUSD DAILY] -> {direction} | {len(winning_votes)}/3 checks agreeing"
    )

    return direction, confidence, reason, history


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
        # FIX: this used to just return None here with zero logging -
        # TwelveData returns a normal 200 JSON response for rate-limit
        # and other API errors (not a Python exception), so the real
        # reason ("price" missing) was being silently discarded every
        # time. Confirmed live - couldn't tell a rate limit apart from
        # any other failure without this.
        print(f"[TWELVEDATA] No price in response for {symbol}: {data}")
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

def get_price_metaapi(mt5_symbol):
    """
    Live bid/ask price straight from the connected MT5 account -
    primary live-price source now, per explicit instruction, matching
    the same MetaAPI-first approach already used for candles. Unlike
    the historical candles endpoint, this one is NOT region-locked to
    new-york - MetaAPI's own docs don't have the "different hostname"
    note on this endpoint, so it uses the same london-region trading
    hostname already used for actual trade placement elsewhere in
    this file. Returns the bid/ask midpoint, matching what a single
    "price" quote conventionally means everywhere else in this file.

    Reuses the same success/failure recording as get_candles_metaapi
    (record_metaapi_candles_success/failure) - one shared health
    signal for "is MetaAPI working right now" across both candles and
    price, not two separate flags for the same underlying account
    connection.
    """
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        return None
    url = (
        f"https://mt-client-api-v1.london.agiliumtrade.ai"
        f"/users/current/accounts/{METAAPI_ACCOUNT_ID}/symbols/{mt5_symbol}/current-price"
    )
    headers = {"auth-token": METAAPI_TOKEN, "Accept": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            bid, ask = data.get("bid"), data.get("ask")
            if bid is not None and ask is not None:
                record_metaapi_candles_success()
                return (bid + ask) / 2
            print(f"[METAAPI PRICE] {mt5_symbol} response missing bid/ask: {data}")
            return None
        if response.status_code in (401, 403):
            print(f"[METAAPI PRICE] {mt5_symbol} failed {response.status_code}: {response.text[:300]}")
            record_metaapi_candles_failure()
            return None
        print(f"[METAAPI PRICE] {mt5_symbol} failed {response.status_code}: {response.text[:300]}")
        return None
    except Exception as e:
        print(f"[METAAPI PRICE] {mt5_symbol} error: {e}")
        return None


def get_live_price(symbol="XAU/USD", config=None):
    # FIX: per explicit instruction, MetaAPI is now the PRIMARY live-
    # price source for every pair that has an mt5_symbol - not just
    # candles. Confirmed live: XAUUSD's signal generation went down
    # entirely when TwelveData's free daily quota (800 credits) ran
    # out, even though real, working candle data was available via
    # MetaAPI the whole time - the live price check happens FIRST and
    # gates everything after it, so it alone being TwelveData-only was
    # a real single point of failure. Every existing fallback below is
    # completely unchanged, still runs exactly as before if MetaAPI
    # has any issue.
    if config and config.get("mt5_symbol"):
        price = get_price_metaapi(config["mt5_symbol"])
        if price is not None:
            return price
        print(f"[PRICE] MetaAPI failed for {symbol} - falling back to existing sources")

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
    "1min": 60,     # matches the actual candle length - no point caching longer than one candle's real duration
    "5min": 300,
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
        # FIX: retry once on a timeout before giving up. CONFIRMED
        # real case via live logs - a single 10s read timeout on
        # BTC/USD's 1h fetch made h1_candles None for that entire
        # signal round, which cascaded into: most of STRATEGY_BANK
        # unable to run (all need h1_candles), the signal falling
        # back to ICT/SMC's lone vote, AND generate_signal_chart
        # having no candles to draw - so the message fell back to
        # the old static BUY/SELL graphic instead of a real chart,
        # even though the signal itself still sent correctly. A
        # second attempt with a longer timeout (20s) recovers most
        # of these transient hiccups without changing anything else
        # about how candle data flows through the rest of the bot.
        try:
            response = requests.get(url, timeout=10)
        except requests.exceptions.Timeout:
            print(f"[CANDLES] {symbol} {interval} timed out at 10s, retrying once with 20s...")
            response = requests.get(url, timeout=20)

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
    Ordered candidate symbols to try for candle data.

    Oil: CONFIRMED DEAD END on this TwelveData plan, at every symbol
    tried. "CL1!" and "USOIL" both 404 with "symbol or figi parameter
    missing or invalid" (not valid symbols on this account at all).
    "WTI/USD" - TwelveData's own documented symbol for this
    instrument - was THEN tested live and got the SAME rejection
    XAGUSD already had: "available starting with the Grow or Venture
    plan". So oil has no free path on this plan at any symbol
    spelling, same root cause as silver. No further oil symbol is
    worth guessing - this needs a paid plan upgrade (TwelveData Grow/
    Venture or an alternative provider), not more code. USOIL has no
    fixed schedule slot for this reason; still reachable manually via
    DM "Signal", where it returns the honest NO_DATA_AVAILABLE message.

    Silver does NOT get a candle fallback either, for the identical
    reason - CONFIRMED via real logs that XAG/USD candle data 404s
    with "available starting with the Grow or Venture plan" - a plan-
    tier restriction, not a bad symbol. (XAGUSD's LIVE PRICE still
    works fine via metals.dev - get_silver_price/get_silver_daily_
    history - this function only covers TwelveData's candle/OHLC
    endpoint, a completely separate product from metals.dev.)
    """
    if config.get("use_oil_api"):
        return ["WTI/USD"]
    return [config.get("td_symbol", config["symbol"])]

def get_candles_metaapi(mt5_symbol, interval, outputsize):
    """
    Pulls historical candles directly from the connected MT5 account
    via MetaAPI - primary data source for XAGUSD/USOIL specifically,
    per explicit instruction: TwelveData's plan doesn't support either
    at all (confirmed live - both 404/reject at every symbol spelling
    tried, see get_candle_symbol_candidates), so this isn't replacing
    a working path, it's filling a real gap. TwelveData stays as
    fallback for these two, and as the only source for every other
    pair - testing MetaAPI on just these 2 before considering it more
    broadly, since it ties data availability to ONE broker account's
    connection staying healthy (a real tradeoff against TwelveData's
    provider-agnostic reliability, not a strict upgrade).

    IMPORTANT: this is a genuinely different hostname/region from the
    trade-execution endpoints elsewhere in this file (which use the
    london region) - MetaAPI's own docs state this specific market-
    data API is valid for the new-york region only, regardless of
    which region the account's trading endpoints use.

    tickVolume (not real "volume") is used deliberately - it's the
    one volume field guaranteed present on every MT5 candle regardless
    of broker data-feed setup, the same standard proxy already used
    for the VAH/VAL Reaction strategy's zone detection.
    """
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        print("[METAAPI CANDLES] Credentials not set")
        record_metaapi_candles_failure()
        return None

    timeframe_map = {"1h": "1h", "4h": "4h", "1day": "1d"}
    mt5_timeframe = timeframe_map.get(interval)
    if not mt5_timeframe:
        print(f"[METAAPI CANDLES] No mapping for internal timeframe '{interval}'")
        return None

    url = (
        f"https://mt-market-data-client-api-v1.new-york.agiliumtrade.ai"
        f"/users/current/accounts/{METAAPI_ACCOUNT_ID}"
        f"/historical-market-data/symbols/{mt5_symbol}/timeframes/{mt5_timeframe}/candles"
        f"?limit={min(outputsize, 1000)}"
    )
    headers = {"auth-token": METAAPI_TOKEN, "Accept": "application/json"}

    for attempt, timeout in enumerate((15, 30), start=1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 200:
                raw = response.json()
                if not raw:
                    print(f"[METAAPI CANDLES] Empty response for {mt5_symbol} {mt5_timeframe}")
                    return None
                # A real 200 with real data proves the account/
                # connection itself is healthy - clears any failure
                # flag, regardless of which specific symbol asked.
                record_metaapi_candles_success()
                print(f"[METAAPI CANDLES] ✅ {mt5_symbol} {mt5_timeframe} - {len(raw)} candles")
                # FIX: CONFIRMED via live chart evidence - candles were
                # showing newest-date-on-the-left (backwards from every
                # other pair's chart). MetaAPI's own docs describe
                # candles as "loaded in backwards direction" but that
                # describes the FETCH mechanism (starts from latest,
                # works backward to build the set), not necessarily the
                # returned array's order - the actual array order was
                # already oldest-first, matching this codebase's
                # convention, so the .reverse() call here was flipping
                # already-correct data into the wrong order. Removed.
                candles = []
                for c in raw:
                    candles.append({
                        "time": c.get("time"),
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c.get("tickVolume") or 0),
                    })
                return candles
            if response.status_code in (401, 403):
                # Genuinely systemic (account disconnected/revoked, not
                # a symbol-specific issue) - the ONLY status worth
                # flagging as a real outage. A 404 or other 4xx for one
                # specific symbol doesn't mean the whole account is
                # down, so those are deliberately NOT flagged here -
                # would cause false "MetaAPI is down" alarms for what's
                # really just one instrument's normal data gap.
                print(f"[METAAPI CANDLES] {mt5_symbol} {mt5_timeframe} failed {response.status_code}: {response.text[:300]}")
                record_metaapi_candles_failure()
                return None
            if response.status_code in (429, 502, 503, 504) and attempt == 1:
                print(f"[METAAPI CANDLES] {mt5_symbol} got {response.status_code}, retrying...")
                continue
            print(f"[METAAPI CANDLES] {mt5_symbol} {mt5_timeframe} failed {response.status_code}: {response.text[:300]}")
            return None
        except Exception as e:
            if attempt == 1:
                print(f"[METAAPI CANDLES] {mt5_symbol} attempt 1 error: {e} - retrying with longer timeout...")
                continue
            print(f"[METAAPI CANDLES] {mt5_symbol} error: {e}")
            return None
    return None


# Pairs where MetaAPI is tried FIRST, TwelveData as fallback - see
# get_candles_metaapi's docstring for why. Scoped to exactly these 2
# per explicit instruction (test before any wider rollout), not a
# blanket default for every pair.
# Pairs where MetaAPI is tried FIRST, TwelveData as fallback - see
# get_candles_metaapi's docstring for why. Per explicit instruction,
# expanded from the original XAGUSD/USOIL-only test to every pair
# currently offered - the XAGUSD/USOIL test confirmed the mechanism
# works, so this is now the real default for both scheduled and
# manual signals across the board, not a limited trial anymore.
METAAPI_FIRST_PAIRS = {
    "xauusd", "btcusd", "xagusd", "usoil",
    "gbpusd", "gbpjpy", "eurusd", "usdjpy",
    "audusd", "usdcad", "eurjpy", "usdchf", "nzdusd",
}


def get_cached_candles(pair_key, config, interval, outputsize=60):
    """
    CONFIRMED REAL BUG FIX: cache_key previously omitted outputsize
    entirely (f"{pair_key}_{interval}"), so whichever call happened
    to run FIRST for a given pair/interval silently decided what
    every later caller got, regardless of how many candles THEY
    asked for. Concretely: analyze_smc_structure (inside
    run_strategy_bank) requests outputsize=60 for h1, while
    build_signal_response's main signal generation requests
    outputsize=210 for the same pair/interval - if the 60-candle
    fetch ran first and got cached, the 210-candle request later in
    the SAME call would silently receive the cached 60-candle list
    instead, never actually fetching the deeper history it asked
    for. strategy_trend_following's MA20/MA50 (and any other
    strategy relying on a longer window) could end up running on
    less data than intended, with no error or warning anywhere -
    the bug was completely silent. Including outputsize in the cache
    key means a 60-candle cache entry and a 210-candle cache entry
    are now tracked as genuinely separate things, each fetched fresh
    when first needed at that specific size.
    """
    cache_key = f"{pair_key}_{interval}_{outputsize}"
    now = time.time()
    ttl = CANDLE_CACHE_SECONDS.get(interval, 3600)
    cached = candle_cache.get(cache_key)
    if cached and (now - cached["timestamp"] < ttl):
        return cached["candles"]

    if pair_key in METAAPI_FIRST_PAIRS and config.get("mt5_symbol"):
        candles = get_candles_metaapi(config["mt5_symbol"], interval, outputsize)
        if candles:
            candle_cache[cache_key] = {"candles": candles, "timestamp": now}
            return candles
        print(f"[CANDLES] MetaAPI failed for {pair_key} ({interval}) - falling back to TwelveData")

    candidates = get_candle_symbol_candidates(config)
    for symbol in candidates:
        candles = get_candles_twelvedata(symbol, interval, outputsize)
        if candles:
            candle_cache[cache_key] = {"candles": candles, "timestamp": now}
            return candles

    print(f"[CANDLES] All symbol candidates failed for {pair_key} ({interval}, outputsize={outputsize}): tried {candidates}")
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
    # h1 outputsize matches build_signal_response's own h1 fetch
    # (210) on purpose - CONFIRMED REAL REGRESSION: after fixing
    # get_cached_candles' cache key to include outputsize (so a
    # 60-candle cache entry and a 210-candle cache entry are never
    # silently confused for each other), this function's OWN h1
    # fetch at outputsize=60 stopped sharing a cache entry with
    # build_signal_response's h1@210 fetch - meaning every single
    # signal request now made a genuinely new extra API call for h1
    # that didn't exist before, on EVERY pair, not just oil/silver.
    # That's what pushed ordinary forex pairs (confirmed via real
    # logs: EURUSD, USDJPY, USDCAD) into the same rate-limit
    # territory oil/silver were already hitting. analyze_timeframe
    # has no upper bound on candle count (only a len>=15 floor), so
    # requesting 210 here costs nothing functionally - it just lets
    # this call and build_signal_response's collapse back into ONE
    # shared cache entry/API call instead of two.
    h1_candles = get_cached_candles(pair_key, config, "1h", outputsize=210)
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


def detect_pin_bar(candles):
    """
    Classic hammer (bullish) / shooting star (bearish) rejection
    candle. Returns "BUY", "SELL", or None. Wick must be at least 2x
    the body, and the body must sit at the opposite end of the
    candle's range from the long wick - a real rejection, not just a
    doji with a long wick on both sides.
    """
    if len(candles) < 1:
        return None
    c = candles[-1]
    body = abs(c["close"] - c["open"])
    candle_range = c["high"] - c["low"]
    if candle_range <= 0 or body <= 0:
        return None

    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]

    # Hammer: long lower wick, small body near the top -> bullish rejection
    if lower_wick >= body * 2 and upper_wick <= body * 0.5:
        return "BUY"
    # Shooting star: long upper wick, small body near the bottom -> bearish rejection
    if upper_wick >= body * 2 and lower_wick <= body * 0.5:
        return "SELL"
    return None


def detect_inside_bar_breakout(candles):
    """
    Inside bar: a candle fully contained within the prior candle's
    high/low range (a pause/coil). Signal fires on the candle AFTER
    the inside bar, once price actually breaks outside the inside
    bar's own range - momentum/continuation, not reversal. Needs 3
    candles: the "mother" bar, the inside bar, and the breakout bar.
    """
    if len(candles) < 3:
        return None
    mother, inside, breakout = candles[-3], candles[-2], candles[-1]
    is_inside = inside["high"] <= mother["high"] and inside["low"] >= mother["low"]
    if not is_inside:
        return None
    if breakout["close"] > inside["high"]:
        return "BUY"
    if breakout["close"] < inside["low"]:
        return "SELL"
    return None


def get_deriv_flip_defaults(base_stake):
    """
    Deriv's mirror of get_account_flip_defaults - same "only the
    starting size is a real choice" philosophy, but in dollar profit
    terms instead of pips (no pair-specific volatility split like
    XAUUSD/BTCUSD vs others, since every Volatility index here is
    already deliberately similar in character - all synthetic, all
    RNG-generated - so one flat rule fits everywhere: trigger/trail
    set to 20% of the starting stake, with a $1 floor so a very small
    stake doesn't produce a trigger of a few cents).
    """
    max_layers = 10
    step = round(max(base_stake * 0.2, 1), 2)
    trigger_amount = round(max(base_stake * 0.2, 1), 2)
    return {
        "flip_step": step,
        "flip_max_layers": max_layers,
        "flip_max_stake": round(base_stake * max_layers, 2),
        "flip_trigger_amount": trigger_amount,
        "flip_trail_amount": trigger_amount,
    }


def get_account_flip_defaults(pair_key, base_lot):
    """
    Everything about Account Flip except the starting lot size is a
    sensible default now, per explicit instruction (the full 6-question
    setup was too much for a layman) - only the lot size is still a
    real choice. Layer growth is exactly "one step upward" from the
    base size (0.01 base -> layers of 0.01, 0.02, 0.03, ...), pip
    spacing/trailing distance is wider for the two volatile instruments
    (gold, BTC) and tighter for everything else, and the per-layer cap
    is set to exactly cover all 10 max layers rather than an arbitrary
    number - it's a safety ceiling, not meant to bind before then.
    """
    volatile_pairs = ("xauusd", "btcusd")
    pips = 10 if pair_key in volatile_pairs else 3
    max_layers = 10
    return {
        "flip_step": base_lot,
        "flip_max_layers": max_layers,
        "flip_max_lot": round(base_lot * max_layers, 2),
        "flip_trigger_pips": pips,
        "flip_trail_pips": pips,
    }


def account_flip_signal(pair_key, pair_config, candles):
    """
    Account Flip's own dedicated, standalone entry logic - pure price
    action only, deliberately separate from the indicator-based
    strategy banks every other bot draws from. Checks all 3 patterns
    on the latest candle(s); whichever fires first wins (checked in
    this fixed order: Engulfing, Pin Bar, Inside Bar Breakout - not a
    vote, just "first real signal on this candle").

    Returns a vote dict matching the shape the rest of the codebase
    already expects ({"direction", "reasoning", "strategy"}), or None.
    Stop-loss anchor is pattern-specific (the invalidation point of
    whichever pattern fired), used only for the FIRST layer - later
    layers ride without their own broker-side stop, per the
    trailing-stop design that closes the whole stack together.
    """
    if len(candles) < 3:
        return None

    if detect_bullish_engulfing(candles):
        return {"direction": "BUY", "strategy": "Engulfing Bar Reversal",
                "reasoning": "Bullish engulfing candle - full reversal of the prior bearish candle.",
                "invalidation": candles[-1]["low"]}
    if detect_bearish_engulfing(candles):
        return {"direction": "SELL", "strategy": "Engulfing Bar Reversal",
                "reasoning": "Bearish engulfing candle - full reversal of the prior bullish candle.",
                "invalidation": candles[-1]["high"]}

    pin_bar_direction = detect_pin_bar(candles)
    if pin_bar_direction == "BUY":
        return {"direction": "BUY", "strategy": "Pin Bar",
                "reasoning": "Hammer - long lower wick rejecting lower prices.",
                "invalidation": candles[-1]["low"]}
    if pin_bar_direction == "SELL":
        return {"direction": "SELL", "strategy": "Pin Bar",
                "reasoning": "Shooting star - long upper wick rejecting higher prices.",
                "invalidation": candles[-1]["high"]}

    inside_bar_direction = detect_inside_bar_breakout(candles)
    if inside_bar_direction == "BUY":
        return {"direction": "BUY", "strategy": "Inside Bar Breakout",
                "reasoning": "Price broke above an inside bar's coiled range.",
                "invalidation": candles[-2]["low"]}
    if inside_bar_direction == "SELL":
        return {"direction": "SELL", "strategy": "Inside Bar Breakout",
                "reasoning": "Price broke below an inside bar's coiled range.",
                "invalidation": candles[-2]["high"]}

    return None

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

    EXTENSION FILTER: per explicit instruction, after a real signal
    (XAUUSD SELL, live) confirmed this exact failure mode - the raw
    alignment check fires immediately on a sharp breakaway candle,
    entering right at the most extended point of the move, which is
    exactly where a bounce back toward the average is most likely -
    that live signal got stopped out by a retracement before the
    trend resumed. Now requires price to be within 2x a recent
    volatility proxy (average true range of the last 14 candles) of
    the 20MA before firing - an already-extended move (price far from
    its own 20MA, since the MA lags and hasn't caught up yet) is
    skipped for now rather than chased, and the strategy will
    naturally fire on a later candle once price pulls back closer to
    the average or the average catches up - a genuinely safer entry,
    without duplicating EMA Pullback Scalper's own separate pattern.
    """
    if not h1_candles or len(h1_candles) < 50:
        return None

    closes = [c["close"] for c in h1_candles]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    current_price = closes[-1]

    recent = h1_candles[-14:]
    atr_proxy = sum(c["high"] - c["low"] for c in recent) / len(recent)
    distance_from_ma20 = abs(current_price - ma20)
    if atr_proxy > 0 and distance_from_ma20 > (atr_proxy * 2.0):
        return None

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

    # FIX: both checks below used to only have a LOWER bound (>= for
    # resistance, <= for support), meaning a level price had already
    # moved well past days ago could still "qualify" as a fresh
    # rejection, since nothing capped how FAR beyond the level the
    # current candle was allowed to be. Confirmed live - a real signal
    # cited a resistance level ~0.75-0.9 price away from the actual
    # entry, days after price had already broken through it. Added a
    # proximity band (0.5%) on the far side too, so this now requires
    # the candle to genuinely be AT the level, not just anywhere past
    # it.
    proximity = 0.005

    for level, touches in tested_levels.items():
        level_type = touches[0]["type"]
        near_support = level_type == "low" and level * (1 - proximity) <= last["low"] <= level * (1 + tolerance)
        near_resistance = level_type == "high" and level * (1 - tolerance) <= last["high"] <= level * (1 + proximity)

        if near_support and (last["close"] - last["low"]) / candle_range >= 0.6:
            return {
                "strategy_name": "Support/Resistance Bounce",
                "direction": "BUY",
                "level": level,
                "detail": f"price bounced off a well-tested support level (~{level:.2f})",
            }
        if near_resistance and (last["high"] - last["close"]) / candle_range >= 0.6:
            return {
                "strategy_name": "Support/Resistance Bounce",
                "direction": "SELL",
                "level": level,
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

def calculate_atr_series(candles, period=14):
    """
    Standard ATR (Average True Range) via Wilder's smoothing. Returns
    the full series aligned to candles[period:], or [] if there isn't
    enough data - needed as a series (not just the latest value) so
    callers can compare current ATR against ATR several candles ago
    to detect genuine expansion, not just read one static number.
    """
    if len(candles) < period + 1:
        return []

    true_ranges = []
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    atr_values = [sum(true_ranges[:period]) / period]
    for tr in true_ranges[period:]:
        atr_values.append((atr_values[-1] * (period - 1) + tr) / period)

    return atr_values


def calculate_supertrend(candles, period=10, multiplier=3):
    """
    Standard Supertrend indicator. Returns a list of (is_uptrend,
    supertrend_value) tuples aligned to candles[period:], or [] if
    there isn't enough data. is_uptrend flips exactly when price
    crosses the final band - that flip IS the buy/sell signal,
    checked by the strategy function below.
    """
    atr_values = calculate_atr_series(candles, period)
    if not atr_values:
        return []

    aligned_candles = candles[-len(atr_values):]
    final_upper, final_lower = None, None
    is_uptrend = True
    results = []

    for i, candle in enumerate(aligned_candles):
        atr = atr_values[i]
        hl2 = (candle["high"] + candle["low"]) / 2
        basic_upper = hl2 + multiplier * atr
        basic_lower = hl2 - multiplier * atr
        close = candle["close"]

        if i == 0:
            final_upper, final_lower = basic_upper, basic_lower
        else:
            prev_close = aligned_candles[i - 1]["close"]
            final_upper = basic_upper if (basic_upper < final_upper or prev_close > final_upper) else final_upper
            final_lower = basic_lower if (basic_lower > final_lower or prev_close < final_lower) else final_lower

        if close > final_upper:
            is_uptrend = True
        elif close < final_lower:
            is_uptrend = False
        # else: no cross this candle, is_uptrend carries over unchanged

        results.append((is_uptrend, final_lower if is_uptrend else final_upper))

    return results


def calculate_adx(candles, period=14):
    """
    Standard ADX (Average Directional Index) via Wilder's smoothing.
    Returns the latest ADX value only (a single float, 0-100), or
    None if there isn't enough data. Used as a FILTER on the whole
    strategy bank, per explicit instruction - not a voting strategy
    of its own. ADX > 25 = strong trend, ADX < 20 = choppy/no clear
    trend, and everything in between is a grey zone this filter
    deliberately doesn't touch.
    """
    if len(candles) < period * 2:
        return None

    plus_dm, minus_dm, true_ranges = [], [], []
    for i in range(1, len(candles)):
        high, low = candles[i]["high"], candles[i]["low"]
        prev_high, prev_low, prev_close = candles[i - 1]["high"], candles[i - 1]["low"], candles[i - 1]["close"]

        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    def wilder_smooth(values, period):
        smoothed = [sum(values[:period])]
        for v in values[period:]:
            smoothed.append(smoothed[-1] - (smoothed[-1] / period) + v)
        return smoothed

    smoothed_tr = wilder_smooth(true_ranges, period)
    smoothed_plus_dm = wilder_smooth(plus_dm, period)
    smoothed_minus_dm = wilder_smooth(minus_dm, period)

    dx_values = []
    for tr, pdm, mdm in zip(smoothed_tr, smoothed_plus_dm, smoothed_minus_dm):
        if tr == 0:
            continue
        plus_di = 100 * pdm / tr
        minus_di = 100 * mdm / tr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            continue
        dx_values.append(100 * abs(plus_di - minus_di) / di_sum)

    if len(dx_values) < period:
        return None

    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period

    return adx


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

def strategy_fibonacci_retracement(pair_key, config, h1_candles, h4_candles, daily_candles, m1_candles=None):
    """
    Per explicit instruction, added on all pairs (both forex/crypto
    and synthetic - note the m1_candles parameter, unused here, exists
    only so this function can be shared into SYNTHETIC_STRATEGY_BANK
    via run_strategy_bank_synthetic's dispatch, which checks for that
    parameter name to decide which candle set to pass in; this
    strategy always uses h1_candles regardless).

    Uses the most recent genuine swing high and swing low
    (find_swing_points - the same real fractal detection already used
    elsewhere in this file, e.g. detect_bos_choch, detect_premium_
    discount) to build real retracement levels, never a fabricated or
    guessed range.

    BUY: the broader swing is bullish (the low came before the high),
    price has pulled back into the 38.2%-61.8% retracement zone of
    that up-move, and the current candle shows a bullish reaction
    (closes in the upper half of its own range) - a real bounce off
    the zone, not just price sitting inside it. SELL is the mirror
    case on a bearish swing (high before low, pullback up into the
    zone, bearish reaction candle).

    Requires the two most recent swings to actually be one high and
    one low (not two highs or two lows in a row, which would mean no
    clean single impulse leg exists to retrace from) - returns None
    rather than guessing if that's not the case.
    """
    if not h1_candles or len(h1_candles) < 20:
        return None

    swings = find_swing_points(h1_candles, strength=2)
    if len(swings) < 2:
        return None

    last_two = swings[-2:]
    types = {s["type"] for s in last_two}
    if len(types) != 2:
        return None  # both the same type (two highs or two lows) - no single clean leg to retrace

    swing_low = next(s for s in last_two if s["type"] == "low")
    swing_high = next(s for s in last_two if s["type"] == "high")
    leg_range = swing_high["price"] - swing_low["price"]
    if leg_range <= 0:
        return None

    fib_618 = swing_high["price"] - (leg_range * 0.618)
    fib_382 = swing_high["price"] - (leg_range * 0.382)
    zone_low, zone_high = min(fib_618, fib_382), max(fib_618, fib_382)

    last = h1_candles[-1]
    current_price = last["close"]
    candle_range = last["high"] - last["low"]
    if candle_range == 0 or not (zone_low <= current_price <= zone_high):
        return None

    closed_upper_half = (last["close"] - last["low"]) / candle_range >= 0.5
    bullish_leg = swing_low["index"] < swing_high["index"]

    if bullish_leg and closed_upper_half:
        return {
            "strategy_name": "Fibonacci Retracement",
            "direction": "BUY",
            "detail": f"pulled back into the 38.2-61.8% retracement zone ({zone_low:.2f}-{zone_high:.2f}) of the recent up-move and bounced",
        }
    if not bullish_leg and not closed_upper_half:
        return {
            "strategy_name": "Fibonacci Retracement",
            "direction": "SELL",
            "detail": f"pulled back into the 38.2-61.8% retracement zone ({zone_low:.2f}-{zone_high:.2f}) of the recent down-move and rejected",
        }

    return None


def strategy_rsi_trend_continuation(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - genuinely different from the
    removed RSI Extreme Reversal (that traded RSI extremes as
    reversal points; this trades RSI as a CONTINUATION signal within
    an existing trend, which is a real, distinct concept, not a
    duplicate). Trend direction uses the same simple MA20/50
    alignment already established elsewhere in this bank.

    Uptrend (MA20 > MA50): RSI dipped into 40-50 on either of the
    last 2 candles, then turned up (current RSI > previous) -> BUY.
    Downtrend (MA20 < MA50): RSI rose into 50-60, then turned down
    -> SELL.
    """
    if not h1_candles or len(h1_candles) < 55:
        return None

    closes = [c["close"] for c in h1_candles]
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50

    rsi_series = calculate_rsi(h1_candles, period=14)
    if len(rsi_series) < 3:
        return None

    prev_rsi, curr_rsi = rsi_series[-2], rsi_series[-1]
    recent_dip = rsi_series[-3] if len(rsi_series) >= 3 else prev_rsi

    if ma20 > ma50:
        dipped_to_zone = 40 <= prev_rsi <= 50 or 40 <= recent_dip <= 50
        turned_up = curr_rsi > prev_rsi
        if dipped_to_zone and turned_up:
            return {
                "strategy_name": "RSI Trend Continuation",
                "direction": "BUY",
                "detail": f"RSI {prev_rsi:.1f} to {curr_rsi:.1f}, uptrend intact",
            }
    elif ma20 < ma50:
        rose_to_zone = 50 <= prev_rsi <= 60 or 50 <= recent_dip <= 60
        turned_down = curr_rsi < prev_rsi
        if rose_to_zone and turned_down:
            return {
                "strategy_name": "RSI Trend Continuation",
                "direction": "SELL",
                "detail": f"RSI {prev_rsi:.1f} to {curr_rsi:.1f}, downtrend intact",
            }

    return None


def strategy_bollinger_squeeze_breakout(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - genuinely different from the
    existing Breakout strategy (which measures raw high-low range
    compression, not actual Bollinger Band width). Squeeze = current
    band width sits in the tightest 25% of the last 20 candles' width
    history. Breakout = the latest candle both closes outside the
    (pre-breakout) band AND has a real body - at least 60% of its own
    range - not just a thin wick poking through.
    """
    if not h1_candles or len(h1_candles) < 40:
        return None

    widths = []
    for i in range(20, 0, -1):
        window = h1_candles[:len(h1_candles) - i + 1] if i > 1 else h1_candles
        upper, middle, lower = calculate_bollinger_bands(window, period=20)
        if upper is None:
            return None
        widths.append(upper - lower)

    current_width = widths[-1]
    prior_widths = sorted(widths[:-1])
    squeeze_threshold = prior_widths[len(prior_widths) // 4] if prior_widths else None
    if squeeze_threshold is None or current_width > squeeze_threshold:
        return None  # bands aren't actually tight right now - no squeeze to break out of

    upper, middle, lower = calculate_bollinger_bands(h1_candles[:-1], period=20)
    if upper is None:
        return None

    last = h1_candles[-1]
    candle_range = last["high"] - last["low"]
    if candle_range == 0:
        return None
    body_ratio = abs(last["close"] - last["open"]) / candle_range
    is_strong_candle = body_ratio >= 0.6

    if last["close"] > upper and is_strong_candle:
        return {
            "strategy_name": "Bollinger Squeeze Breakout",
            "direction": "BUY",
            "detail": f"closed above the upper band at {upper:.2f}",
        }
    if last["close"] < lower and is_strong_candle:
        return {
            "strategy_name": "Bollinger Squeeze Breakout",
            "direction": "SELL",
            "detail": f"closed below the lower band at {lower:.2f}",
        }

    return None


def strategy_atr_volatility_breakout(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - genuinely different from the
    existing Breakout/Volatility Breakout Scalper strategies (both
    measure a PRICE LEVEL breaking out of a range; this measures
    VOLATILITY ITSELF expanding, a different signal entirely).
    Requires current ATR meaningfully above its own recent average
    (genuine expansion, not just noise) AND the latest candle's range
    being unusually large relative to that same average - "large
    breakout candle" from the spec, not just any directional move.
    """
    if not h1_candles or len(h1_candles) < 30:
        return None

    atr_series = calculate_atr_series(h1_candles, period=14)
    if len(atr_series) < 10:
        return None

    current_atr = atr_series[-1]
    avg_atr = sum(atr_series[-10:-1]) / 9
    if avg_atr == 0 or current_atr < avg_atr * 1.3:
        return None  # ATR isn't genuinely expanding right now

    last = h1_candles[-1]
    prior_20 = h1_candles[-21:-1]
    candle_range = last["high"] - last["low"]
    if candle_range < avg_atr * 1.5:
        return None  # not actually a large breakout candle, just a wider-than-usual one

    recent_high = max(c["high"] for c in prior_20)
    recent_low = min(c["low"] for c in prior_20)

    if last["close"] > recent_high:
        return {
            "strategy_name": "ATR Volatility Breakout",
            "direction": "BUY",
            "detail": f"broke above {recent_high:.2f} on expanding volatility",
        }
    if last["close"] < recent_low:
        return {
            "strategy_name": "ATR Volatility Breakout",
            "direction": "SELL",
            "detail": f"broke below {recent_low:.2f} on expanding volatility",
        }

    return None


def strategy_supertrend(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - standard Supertrend (period=10,
    multiplier=3), firing only on the exact candle the trend flips,
    not on every candle while already in an established trend
    (otherwise this would fire constantly rather than at genuine
    turning points).
    """
    if not h1_candles or len(h1_candles) < 25:
        return None

    st = calculate_supertrend(h1_candles, period=10, multiplier=3)
    if len(st) < 2:
        return None

    prev_uptrend, _ = st[-2]
    curr_uptrend, curr_value = st[-1]

    if not prev_uptrend and curr_uptrend:
        return {
            "strategy_name": "Supertrend",
            "direction": "BUY",
            "detail": f"Supertrend flipped bullish at {curr_value:.2f}",
        }
    if prev_uptrend and not curr_uptrend:
        return {
            "strategy_name": "Supertrend",
            "direction": "SELL",
            "detail": f"Supertrend flipped bearish at {curr_value:.2f}",
        }

    return None


def calculate_parabolic_sar(candles, af_start=0.02, af_step=0.02, af_max=0.2):
    """
    Standard Parabolic SAR. Returns a list of (is_uptrend, sar_value)
    tuples aligned to the full candles list, or [] if there isn't
    enough data. Mirrors calculate_supertrend's return shape - a
    trend flip (is_uptrend changing between consecutive candles) IS
    the buy/sell signal, checked by the strategy function below.
    """
    if len(candles) < 5:
        return []

    is_uptrend = candles[1]["close"] >= candles[0]["close"]
    sar = candles[0]["low"] if is_uptrend else candles[0]["high"]
    ep = candles[0]["high"] if is_uptrend else candles[0]["low"]
    af = af_start
    results = [(is_uptrend, sar)]

    for i in range(1, len(candles)):
        prev_sar = sar
        sar = prev_sar + af * (ep - prev_sar)
        high, low = candles[i]["high"], candles[i]["low"]

        if is_uptrend:
            sar = min(sar, candles[i - 1]["low"], candles[i - 2]["low"] if i >= 2 else candles[i - 1]["low"])
            if low < sar:
                is_uptrend = False
                sar = ep
                ep = low
                af = af_start
            else:
                if high > ep:
                    ep = high
                    af = min(af + af_step, af_max)
        else:
            sar = max(sar, candles[i - 1]["high"], candles[i - 2]["high"] if i >= 2 else candles[i - 1]["high"])
            if high > sar:
                is_uptrend = True
                sar = ep
                ep = high
                af = af_start
            else:
                if low < ep:
                    ep = low
                    af = min(af + af_step, af_max)

        results.append((is_uptrend, sar))

    return results


def strategy_parabolic_sar(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - same "fires only on the exact flip"
    principle as Supertrend, but a genuinely different calculation
    (a trailing dot that accelerates as the trend extends, rather than
    an ATR-band cross) - not a duplicate vote of Supertrend.
    """
    if not h1_candles or len(h1_candles) < 20:
        return None

    sar = calculate_parabolic_sar(h1_candles)
    if len(sar) < 2:
        return None

    prev_uptrend, _ = sar[-2]
    curr_uptrend, curr_value = sar[-1]

    if not prev_uptrend and curr_uptrend:
        return {
            "strategy_name": "Parabolic SAR",
            "direction": "BUY",
            "detail": f"SAR flipped below price at {curr_value:.2f}, signaling a new uptrend",
        }
    if prev_uptrend and not curr_uptrend:
        return {
            "strategy_name": "Parabolic SAR",
            "direction": "SELL",
            "detail": f"SAR flipped above price at {curr_value:.2f}, signaling a new downtrend",
        }

    return None


def strategy_ichimoku_breakout(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - Ichimoku Cloud breakout. Cloud =
    the area between Senkou Span A and B, projected forward 26
    periods when drawn on a chart - but since this only needs
    "is CURRENT price above/below the cloud that applies to THIS
    candle", it uses the span values computed 26 periods ago
    directly, with no need to actually shift/plot anything.
    """
    if not h1_candles or len(h1_candles) < 78:
        return None

    def donchian_mid(candles, period):
        highs = [c["high"] for c in candles[-period:]]
        lows = [c["low"] for c in candles[-period:]]
        return (max(highs) + min(lows)) / 2

    # Cloud values as they were computed 26 candles ago - that's the
    # cloud that actually applies to today's candle when charted.
    candles_26_ago = h1_candles[:-26]
    if len(candles_26_ago) < 52:
        return None

    tenkan_then = donchian_mid(candles_26_ago, 9)
    kijun_then = donchian_mid(candles_26_ago, 26)
    span_a = (tenkan_then + kijun_then) / 2
    span_b = donchian_mid(candles_26_ago, 52)

    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)

    prev_close = h1_candles[-2]["close"]
    curr_close = h1_candles[-1]["close"]

    if prev_close <= cloud_top and curr_close > cloud_top:
        return {
            "strategy_name": "Ichimoku Breakout",
            "direction": "BUY",
            "detail": f"price broke above the cloud (top at {cloud_top:.2f})",
        }
    if prev_close >= cloud_bottom and curr_close < cloud_bottom:
        return {
            "strategy_name": "Ichimoku Breakout",
            "direction": "SELL",
            "detail": f"price broke below the cloud (bottom at {cloud_bottom:.2f})",
        }

    return None


def strategy_keltner_breakout(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - genuinely different band calculation
    from the existing Bollinger Squeeze Breakout: Keltner bands are
    ATR-based around an EMA, not standard-deviation-based around an
    SMA, so this reacts differently to the same price action and
    isn't a duplicate vote.
    """
    if not h1_candles or len(h1_candles) < 25:
        return None

    ema20_series = calculate_ema_series(h1_candles, 20)
    atr_series = calculate_atr_series(h1_candles, 10)
    if not ema20_series or not atr_series:
        return None

    ema_now = ema20_series[-1]
    atr_now = atr_series[-1]
    upper = ema_now + 2 * atr_now
    lower = ema_now - 2 * atr_now

    last = h1_candles[-1]
    if last["close"] > upper:
        return {
            "strategy_name": "Keltner Breakout",
            "direction": "BUY",
            "detail": f"price closed above the upper Keltner band ({upper:.2f})",
        }
    if last["close"] < lower:
        return {
            "strategy_name": "Keltner Breakout",
            "direction": "SELL",
            "detail": f"price closed below the lower Keltner band ({lower:.2f})",
        }

    return None


def strategy_ema_ribbon(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - a stricter, higher-conviction
    cousin of the existing Trend Following (which only checks 20 vs
    50) - requires 5 EMAs (5/10/20/50/100) to ALL stack in order,
    genuinely different confluence than a simple 2-average check.
    """
    if not h1_candles or len(h1_candles) < 105:
        return None

    periods = [5, 10, 20, 50, 100]
    emas = {}
    for p in periods:
        series = calculate_ema_series(h1_candles, p)
        if not series:
            return None
        emas[p] = series[-1]

    values = [emas[p] for p in periods]
    if all(values[i] > values[i + 1] for i in range(len(values) - 1)):
        return {
            "strategy_name": "EMA Ribbon",
            "direction": "BUY",
            "detail": "all 5 EMAs (5/10/20/50/100) stacked in bullish order",
        }
    if all(values[i] < values[i + 1] for i in range(len(values) - 1)):
        return {
            "strategy_name": "EMA Ribbon",
            "direction": "SELL",
            "detail": "all 5 EMAs (5/10/20/50/100) stacked in bearish order",
        }

    return None


def strategy_rate_of_change(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - Rate of Change: simply today's
    close vs the close N periods ago, as a %. A much simpler
    momentum measure than MACD - genuinely different math, not a
    duplicate vote.
    """
    if not h1_candles or len(h1_candles) < 13:
        return None

    period = 12
    curr_close = h1_candles[-1]["close"]
    past_close = h1_candles[-1 - period]["close"]
    if not past_close:
        return None

    roc = (curr_close - past_close) / past_close * 100
    THRESHOLD_PCT = 1.0

    if roc > THRESHOLD_PCT:
        return {
            "strategy_name": "Rate of Change",
            "direction": "BUY",
            "detail": f"price up {roc:.2f}% over the last {period} candles - strong upside momentum",
        }
    if roc < -THRESHOLD_PCT:
        return {
            "strategy_name": "Rate of Change",
            "direction": "SELL",
            "detail": f"price down {abs(roc):.2f}% over the last {period} candles - strong downside momentum",
        }

    return None


def calculate_cci(candles, period=20):
    """Standard CCI (Commodity Channel Index). Returns the latest value, or None if there isn't enough data."""
    if len(candles) < period:
        return None

    typical_prices = [(c["high"] + c["low"] + c["close"]) / 3 for c in candles[-period:]]
    sma_tp = sum(typical_prices) / period
    mean_deviation = sum(abs(tp - sma_tp) for tp in typical_prices) / period
    if mean_deviation == 0:
        return None

    current_tp = typical_prices[-1]
    return (current_tp - sma_tp) / (0.015 * mean_deviation)


def strategy_cci_breakout(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - CCI crossing beyond +/-100, the
    classic "strong momentum" reading. Same family as RSI but a
    genuinely different formula (based on typical price deviation
    from its own moving average, not up/down close comparisons).
    """
    if not h1_candles or len(h1_candles) < 21:
        return None

    cci = calculate_cci(h1_candles, period=20)
    if cci is None:
        return None

    if cci > 100:
        return {
            "strategy_name": "CCI Breakout",
            "direction": "BUY",
            "detail": f"CCI at {cci:.1f}, above +100",
        }
    if cci < -100:
        return {
            "strategy_name": "CCI Breakout",
            "direction": "SELL",
            "detail": f"CCI at {cci:.1f}, below -100",
        }

    return None


def calculate_williams_r(candles, period=14):
    """Standard Williams %R. Returns the latest value (-100 to 0), or None if there isn't enough data."""
    if len(candles) < period:
        return None
    window = candles[-period:]
    highest_high = max(c["high"] for c in window)
    lowest_low = min(c["low"] for c in window)
    if highest_high == lowest_low:
        return None
    return (highest_high - candles[-1]["close"]) / (highest_high - lowest_low) * -100


def strategy_williams_r(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - Williams %R turning back from an
    extreme (below -80, then rising = BUY; above -20, then falling =
    SELL) - same "continuation from extreme" spirit as RSI Trend
    Continuation, but a genuinely different formula (based on the
    high-low range position, not average gains/losses).
    """
    if not h1_candles or len(h1_candles) < 16:
        return None

    prev_wr = calculate_williams_r(h1_candles[:-1], period=14)
    curr_wr = calculate_williams_r(h1_candles, period=14)
    if prev_wr is None or curr_wr is None:
        return None

    if prev_wr <= -80 and curr_wr > prev_wr:
        return {
            "strategy_name": "Williams %R",
            "direction": "BUY",
            "detail": f"{prev_wr:.1f} to {curr_wr:.1f} from oversold",
        }
    if prev_wr >= -20 and curr_wr < prev_wr:
        return {
            "strategy_name": "Williams %R",
            "direction": "SELL",
            "detail": f"{prev_wr:.1f} to {curr_wr:.1f} from overbought",
        }

    return None


def calculate_heikin_ashi(candles):
    """
    Converts raw OHLC candles into Heikin-Ashi (smoothed) candles.
    Returns a list of {open, high, low, close} dicts, same length as
    input.
    """
    ha_candles = []
    for i, c in enumerate(candles):
        ha_close = (c["open"] + c["high"] + c["low"] + c["close"]) / 4
        if i == 0:
            ha_open = (c["open"] + c["close"]) / 2
        else:
            ha_open = (ha_candles[-1]["open"] + ha_candles[-1]["close"]) / 2
        ha_high = max(c["high"], ha_open, ha_close)
        ha_low = min(c["low"], ha_open, ha_close)
        ha_candles.append({"open": ha_open, "high": ha_high, "low": ha_low, "close": ha_close})
    return ha_candles


def strategy_heikin_ashi_trend(pair_key, config, h1_candles, h4_candles, daily_candles):
    """
    NEW per explicit instruction - requires 3 consecutive Heikin-Ashi
    candles in the same direction, each with little to no opposing
    wick (a clean, strong trend candle, not just barely-green/red) -
    a different way of reading trend strength than moving averages,
    using smoothed candles instead of raw price.
    """
    if not h1_candles or len(h1_candles) < 15:
        return None

    ha = calculate_heikin_ashi(h1_candles[-10:])
    last_three = ha[-3:]

    def is_clean_bullish(c):
        body = c["close"] - c["open"]
        lower_wick = c["open"] - c["low"]
        return body > 0 and lower_wick < body * 0.15

    def is_clean_bearish(c):
        body = c["open"] - c["close"]
        upper_wick = c["high"] - c["open"]
        return body > 0 and upper_wick < body * 0.15

    if all(is_clean_bullish(c) for c in last_three):
        return {
            "strategy_name": "Heikin-Ashi Trend",
            "direction": "BUY",
            "detail": "3 consecutive clean bullish Heikin-Ashi candles - strong uptrend",
        }
    if all(is_clean_bearish(c) for c in last_three):
        return {
            "strategy_name": "Heikin-Ashi Trend",
            "direction": "SELL",
            "detail": "3 consecutive clean bearish Heikin-Ashi candles - strong downtrend",
        }

    return None


STRATEGY_BANK = [
    # RESTORED per explicit instruction, after the earlier removal
    # (below) turned out to hurt real signal quality rather than help
    # it - ICT/SMC and these 3 were pulled out in a prior session,
    # then found to be genuinely missed once real losses accumulated
    # with the replacement roster. The functions themselves were never
    # deleted, just re-added to this active list.
    strategy_unicorn_model,  # ICT/SMC
    strategy_support_resistance_bounce,  # demand/supply zones
    strategy_fibonacci_retracement,
    strategy_rsi_extreme_reversal,
    strategy_previous_day_high_low_manipulation,
    # strategy_london_session_orb NOT restored - wasn't part of this
    # request, left available (function untouched) if wanted later.
    strategy_trend_following,
    strategy_breakout,
    strategy_momentum_macd,
    strategy_ema_pullback_scalper,  # per explicit instruction - already existed for synthetics only, now also runs on forex/crypto (falls back to h1_candles since m1_candles is never passed here)
    # strategy_volume_profile_poc REMOVED from this active list -
    # still exists as a function (diagnostic-only, always returns
    # None - real Point of Control needs genuine traded volume, which
    # forex/gold pairs don't reliably have), no longer occupying a
    # slot in the bank pretending to contribute a vote it never casts.
    #
    # TRIMMED from 12 new additions down to 4 kept per explicit
    # instruction - the other 8 were conceptually redundant with each
    # other (multiple trend-following variants, multiple breakout
    # variants, multiple momentum oscillators all tending to move
    # together), which inflates apparent "agreement" without adding
    # real signal diversity. One representative kept per category:
    strategy_supertrend,  # trend
    strategy_atr_volatility_breakout,  # breakout
    strategy_williams_r,  # momentum
    strategy_heikin_ashi_trend,  # candle-structure trend
    # REMOVED (redundant with the above or with restored classics):
    # strategy_rsi_trend_continuation (redundant with restored RSI
    # Extreme Reversal), strategy_bollinger_squeeze_breakout,
    # strategy_parabolic_sar, strategy_ichimoku_breakout,
    # strategy_keltner_breakout, strategy_ema_ribbon,
    # strategy_rate_of_change, strategy_cci_breakout - all still
    # exist as functions, untouched, just not in this active list.
]

# Two-tier split of STRATEGY_BANK, per explicit instruction after a
# real signal (XAUUSD, Heikin-Ashi Trend) was 100% right about
# direction but entered right at the top of a sharp impulsive leg,
# with a normal pullback immediately putting it underwater - a
# lagging trend-confirmation strategy is good at "which way," bad at
# "is THIS the moment." The split:
#
# FILTER tier - describes an ONGOING STATE (is price trending up/down
# right now), never allowed to trigger a trade on its own anymore:
STRATEGY_BANK_FILTERS = [
    strategy_trend_following,
    strategy_heikin_ashi_trend,
    strategy_supertrend,
]

# ENTRY tier - fires on a specific MOMENT/EVENT (a pullback into a
# real zone, a level bounce, a fresh cross, a breakout, a reversal at
# an extreme) - this is what's now actually allowed to trigger a
# trade, and only when it agrees with the filter tier's direction.
STRATEGY_BANK_ENTRIES = [
    strategy_support_resistance_bounce,
    strategy_fibonacci_retracement,
    strategy_rsi_extreme_reversal,
    strategy_ema_pullback_scalper,
    strategy_breakout,
    strategy_atr_volatility_breakout,
    strategy_momentum_macd,
    strategy_williams_r,
    strategy_previous_day_high_low_manipulation,
    strategy_unicorn_model,
    strategy_vah_val_reaction,
]

# Shared "which role does this strategy play" lookup, per explicit
# instruction - extends the filter/entry split beyond the forex
# STRATEGY_BANK to Exness Auto-Trade (run_mt5_autotrade_bot_scan) and
# Deriv's SYNTHETIC_STRATEGY_BANK (run_strategy_bank_synthetic, which
# covers Deriv Auto-Trade AND Deriv manual/scheduled signals - one
# dispatcher, three callers). Keyed by function NAME (not the
# function object) since Exness bots store their strategy list as
# name strings (bot_info["strategy_functions"]), while the forex/
# synthetic banks store the functions directly - this one dict works
# against both by looking up __name__ either way.
#
# Same "ongoing state vs specific triggering event" principle as
# before. Any function NOT in this dict defaults to "entry" (the
# safer default - being left out of the filter tier just means it
# can't set the trend lean, not that it's silently excluded).
STRATEGY_ROLE = {
    # FILTER - ongoing state / lagging trend confirmation
    "strategy_trend_following": "filter",
    "strategy_heikin_ashi_trend": "filter",
    "strategy_supertrend": "filter",
    "strategy_parabolic_sar": "filter",
    "strategy_ema_ribbon": "filter",
    # ENTRY - specific triggering moment/event
    "strategy_support_resistance_bounce": "entry",
    "strategy_fibonacci_retracement": "entry",
    "strategy_rsi_extreme_reversal": "entry",
    "strategy_ema_pullback_scalper": "entry",
    "strategy_breakout": "entry",
    "strategy_atr_volatility_breakout": "entry",
    "strategy_momentum_macd": "entry",
    "strategy_williams_r": "entry",
    "strategy_previous_day_high_low_manipulation": "entry",
    "strategy_unicorn_model": "entry",
    "strategy_vah_val_reaction": "entry",
    "strategy_ichimoku_breakout": "entry",
    "strategy_keltner_breakout": "entry",
    "strategy_rate_of_change": "entry",
    "strategy_cci_breakout": "entry",
    "strategy_volatility_breakout_scalper": "entry",
    "strategy_bollinger_squeeze_breakout": "entry",
    "strategy_rsi_trend_continuation": "entry",
    "strategy_bollinger_rsi_mean_reversion": "entry",
}


def split_strategies_by_role(strategy_fns):
    """
    Splits a list of strategy functions into (filter_fns, entry_fns)
    using STRATEGY_ROLE. Shared by run_mt5_autotrade_bot_scan and
    run_strategy_bank_synthetic so both apply the exact same rule.
    """
    filter_fns = [fn for fn in strategy_fns if STRATEGY_ROLE.get(fn.__name__) == "filter"]
    entry_fns = [fn for fn in strategy_fns if STRATEGY_ROLE.get(fn.__name__, "entry") == "entry"]
    return filter_fns, entry_fns

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
    # Per explicit instruction: no longer "forex strategies PLUS
    # Deriv-only extras" - this is now EXACTLY the same 13 strategies
    # as STRATEGY_BANK_FILTERS + STRATEGY_BANK_ENTRIES (forex/gold/
    # oil), nothing more. rsi_trend_continuation and the 9
    # synthetics-only extras (Bollinger+RSI Mean Reversion,
    # Volatility Breakout Scalper, Parabolic SAR, Ichimoku Breakout,
    # Keltner Breakout, EMA Ribbon, Rate of Change, CCI Breakout) are
    # REMOVED entirely, not just deprioritized - rsi_extreme_reversal
    # is back in place of rsi_trend_continuation to match exactly.
    #
    # strategy_vah_val_reaction is the one deliberate exception,
    # still excluded - it does its own internal candle fetch through
    # the forex-specific MetaAPI/TwelveData pipeline, which has no
    # working path for synthetic index symbols. Including it would be
    # fake parity (it would just silently never vote), not real
    # parity - would need its own Deriv-candle-sourced version to
    # genuinely belong here.
    strategy_trend_following,
    strategy_heikin_ashi_trend,
    strategy_supertrend,
    strategy_support_resistance_bounce,
    strategy_fibonacci_retracement,
    strategy_rsi_extreme_reversal,
    strategy_ema_pullback_scalper,
    strategy_breakout,
    strategy_atr_volatility_breakout,
    strategy_momentum_macd,
    strategy_williams_r,
    strategy_previous_day_high_low_manipulation,
    strategy_unicorn_model,
    # strategy_vah_val_reaction DELIBERATELY EXCLUDED - it does its
    # own internal candle fetch through the forex-specific MetaAPI/
    # TwelveData pipeline (get_vah_val_zones_cached -> get_cached_
    # candles), which has no real symbol/config path for a synthetic
    # index like "r75" - it would silently contribute nothing rather
    # than crash, but including it would be fake parity, not real
    # parity. Would need its own Deriv-candle-sourced version to
    # genuinely work here - a separate build, not a copy-paste.
]

# REMOVED per explicit instruction - every index used to get its own
# curated subset of strategies (matching the reference Trading App's
# per-index bot design), but now that SYNTHETIC_STRATEGY_BANK itself
# is EXACTLY the same 13 strategies as forex/Exness for every index
# uniformly, keeping 5 near-duplicate copies of that same list here
# added nothing but maintenance risk (a strategy added to one place
# and not the other). run_strategy_bank_synthetic's own lookup
# (SYNTHETIC_INDEX_STRATEGY_GROUPS.get(index_key, SYNTHETIC_STRATEGY_
# BANK)) already falls back to the full bank for any index not in
# this dict - leaving it empty means every index cleanly and
# uniformly uses SYNTHETIC_STRATEGY_BANK, which is exactly the
# intended behavior now.
SYNTHETIC_INDEX_STRATEGY_GROUPS = {}

def check_fresh_momentum_veto(pair_key, config, h1_candles, direction):
    """
    Safety check: does the CURRENT live price show strong, immediate
    momentum AGAINST the direction a strategy just voted for? Uses
    ONLY data already being fetched anyway - the raw h1_candles
    already passed into every strategy, and get_cached_price_data's
    existing 60-second price cache - so this adds ZERO new API calls.

    Why this exists: swing-confirmed structure (BOS/CHoCH, Unicorn
    Model, Fibonacci retracement, S/R bounce - anything built on
    find_swing_points) has an inherent blind spot. A swing point can
    only be confirmed once there are 2+ candles of follow-through
    AFTER it, so this kind of analysis structurally cannot "see" a
    sharp move still unfolding in just the last couple of candles.
    Confirmed live: a real SELL fired on XAUUSD with "bearish break of
    structure" as the stated reason, while the freshest candles were
    in the middle of a strong, fast rally straight into the entry
    zone - the swing data was real, just already out of date relative
    to what was actually happening.

    Returns True if the signal should be SUPPRESSED this round (fresh
    momentum contradicts it), False if it's safe to proceed.
    """
    if not h1_candles or len(h1_candles) < 3:
        return False  # not enough data to check - don't block on missing data

    try:
        symbol = config["symbol"]
        current_price, _ = get_cached_price_data(pair_key, symbol, config)
        if current_price is None:
            return False  # couldn't get a live price - don't block a signal on a fetch failure

        reference_close = h1_candles[-3]["close"]
        if not reference_close:
            return False

        pct_move = (current_price - reference_close) / reference_close * 100
        # 0.3% within the last 2 H1 candles is a genuinely fast, sharp
        # move for most pairs this bot trades - tuned to catch exactly
        # the kind of situation that prompted this, not everyday noise.
        THRESHOLD_PCT = 0.3

        if direction == "SELL" and pct_move > THRESHOLD_PCT:
            print(f"[MOMENTUM VETO] {pair_key} SELL suppressed - price up {pct_move:.2f}% in the last 2 candles, against the call")
            return True
        if direction == "BUY" and pct_move < -THRESHOLD_PCT:
            print(f"[MOMENTUM VETO] {pair_key} BUY suppressed - price down {pct_move:.2f}% in the last 2 candles, against the call")
            return True
        return False
    except Exception as e:
        print(f"[MOMENTUM VETO] check failed for {pair_key}: {e}")
        return False  # never let the veto itself become a new point of failure


def run_strategy_bank(pair_key, config, h1_candles, h4_candles, daily_candles, min_agree=2):
    """
    Two-tier dispatcher, per explicit instruction after a real signal
    (XAUUSD, Heikin-Ashi Trend) was 100% right about direction but
    entered right at the top of an already-extended move - a lagging
    trend-confirmation strategy is good at "which way," bad at "is
    THIS the moment." Previously ran all 13 strategies as one flat
    vote pool where any of them, trend-readers included, could
    directly trigger a trade alone.

    Now: STRATEGY_BANK_FILTERS only ever answers "which way is this
    trending" - it can no longer trigger a trade by itself.
    STRATEGY_BANK_ENTRIES answers "is this an actual entry moment"
    (a pullback, a level bounce, a breakout, a reversal at an
    extreme). A trade only fires when BOTH agree: the filter tier has
    a clear directional lean, AND at least one entry-tier strategy
    fires in that SAME direction. If the trend is clear but nothing
    has actually triggered yet, this now correctly waits instead of
    entering on trend confirmation alone - the exact discipline this
    was built to add.

    min_agree now applies to the ENTRY tier specifically (how many
    independent entry triggers confirm this exact moment), same
    "preferred bar, not a hard gate" philosophy as before - even 1
    entry-tier vote is sent, just at lower confidence.

    Returns (direction, confidence, reason, agreeing_strategies) or
    None if either tier has nothing to say, or the two tiers disagree.
    """
    filter_votes = []
    for strategy_fn in STRATEGY_BANK_FILTERS:
        try:
            result = strategy_fn(pair_key, config, h1_candles, h4_candles, daily_candles)
            if result:
                filter_votes.append(result)
        except Exception as e:
            print(f"[STRATEGY BANK] filter {strategy_fn.__name__} failed for {pair_key}: {e}")
            continue

    if not filter_votes:
        print(f"[STRATEGY BANK] {pair_key} - no trend filter has a read right now, skipping")
        return None

    filter_buy = [v for v in filter_votes if v["direction"] == "BUY"]
    filter_sell = [v for v in filter_votes if v["direction"] == "SELL"]
    if len(filter_buy) == len(filter_sell):
        print(f"[STRATEGY BANK] {pair_key} - trend filters split evenly, no clear lean, skipping")
        return None
    filter_direction = "BUY" if len(filter_buy) > len(filter_sell) else "SELL"
    winning_filter_votes = filter_buy if filter_direction == "BUY" else filter_sell

    entry_votes = []
    for strategy_fn in STRATEGY_BANK_ENTRIES:
        try:
            result = strategy_fn(pair_key, config, h1_candles, h4_candles, daily_candles)
            if result:
                entry_votes.append(result)
        except Exception as e:
            print(f"[STRATEGY BANK] entry {strategy_fn.__name__} failed for {pair_key}: {e}")
            continue

    matching_entries = [v for v in entry_votes if v["direction"] == filter_direction]
    if not matching_entries:
        print(
            f"[STRATEGY BANK] {pair_key} - trend filters lean {filter_direction} "
            f"({len(winning_filter_votes)} agreeing) but no entry trigger has fired "
            f"yet in that direction - waiting for an actual moment, not trading the trend alone"
        )
        return None

    direction = filter_direction
    winning_votes = matching_entries

    if len(winning_votes) < min_agree:
        print(
            f"[STRATEGY BANK] {pair_key} only {len(winning_votes)} entry trigger(s) "
            f"agreed on {direction} (below preferred {min_agree}) - sending anyway, every "
            f"strategy in this bank is independently trusted"
        )

    agreeing_names = [v["strategy_name"] for v in winning_votes]
    filter_names = [v["strategy_name"] for v in winning_filter_votes]
    # Small bonus for filter-tier agreement on top of the entry-tier
    # base, same ceiling as before - multiple confirming trend reads
    # plus a real entry trigger is genuinely higher-conviction than
    # either alone, but the entry trigger itself still does most of
    # the work in this number, matching its role as the actual reason
    # a trade fires now.
    confidence = min(95, 70 + len(winning_votes) * 6 + (len(winning_filter_votes) - 1) * 2)

    # Reasoning text now names the filter tier separately from the
    # entry trigger, so it reads as "why THIS pair is in a real
    # direction" followed by "why THIS exact moment" - not one
    # undifferentiated list where a lagging trend read and a genuine
    # entry trigger look equally weighted.
    reason = (
        f"Trend filters ({len(winning_filter_votes)}): {', '.join(filter_names)} all {direction.lower()}.\n"
        + "\n".join(f"• Entry — {v['strategy_name']}: {v['detail']}" for v in winning_votes)
    )

    print(
        f"[STRATEGY BANK] {pair_key} -> {direction} | "
        f"filters: {', '.join(filter_names)} | entries: {', '.join(agreeing_names)}"
    )

    # EXPERIMENTAL flip, per explicit instruction - see
    # EXPERIMENTAL_INVERT_SIGNALS above. Applied AFTER logging, so
    # Railway's logs still show the strategies' TRUE, un-flipped
    # consensus for later review - only what actually gets displayed/
    # traded on is inverted.
    direction = maybe_invert_direction(direction)

    return direction, confidence, reason, agreeing_names, winning_votes

async def check_fresh_momentum_veto_synthetic(index_key, config, h1_candles, direction):
    """
    Synthetic-index sibling of check_fresh_momentum_veto (used for
    forex/crypto) - same blind spot (swing-confirmed structure can't
    see a sharp move still unfolding in the last couple of candles),
    same fix, but sourcing the "live price" from Deriv's own candle
    feed instead of TwelveData, since synthetic indices aren't on
    TwelveData at all. Uses the already-short-cached (30s) 1-minute
    granularity as the freshness source - cheap, already proven safe,
    no new API load.

    Returns True if the signal should be SUPPRESSED this round (fresh
    momentum contradicts it), False if it's safe to proceed.
    """
    if not h1_candles or len(h1_candles) < 3:
        return False

    try:
        symbol = config["symbol"]
        fresh_m1 = await get_cached_synthetic_candles(index_key, symbol, "1m", 60, count=2)
        if not fresh_m1:
            return False
        current_price = fresh_m1[-1]["close"]

        reference_close = h1_candles[-3]["close"]
        if not reference_close:
            return False

        pct_move = (current_price - reference_close) / reference_close * 100
        # Same 0.3% threshold as the forex/crypto version - tuned to
        # catch a genuinely fast, sharp move, not everyday noise.
        THRESHOLD_PCT = 0.3

        if direction == "SELL" and pct_move > THRESHOLD_PCT:
            print(f"[MOMENTUM VETO SYNTH] {index_key} SELL suppressed - price up {pct_move:.2f}% in the last 2 H1 candles, against the call")
            return True
        if direction == "BUY" and pct_move < -THRESHOLD_PCT:
            print(f"[MOMENTUM VETO SYNTH] {index_key} BUY suppressed - price down {pct_move:.2f}% in the last 2 H1 candles, against the call")
            return True
        return False
    except Exception as e:
        print(f"[MOMENTUM VETO SYNTH] check failed for {index_key}: {e}")
        return False


async def run_strategy_bank_synthetic(index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=None, min_agree=2):
    """
    Async sibling of run_strategy_bank, for synthetic (Deriv)
    indices. Per explicit instruction, now uses EXACTLY the same
    strategy roster as forex/gold/oil (SYNTHETIC_STRATEGY_BANK is the
    identical 13 strategies as STRATEGY_BANK_FILTERS + STRATEGY_BANK_
    ENTRIES) - no more synthetics-only extras, no more exclusions.
    The one remaining exception is strategy_vah_val_reaction, left out
    because it does its own internal candle fetch through the forex-
    specific MetaAPI/TwelveData pipeline, which has no working path
    for a synthetic index symbol.

    Two-tier only, per explicit instruction - the flat vote-pool
    fallback that used to live here has been removed entirely.
    min_agree is enforced directly on the entry-tier match count
    (Aggressive=1 fires on any 1 matching entry trigger, Conservative=3
    needs 3+ agreeing within the entry tier) - moved here out of
    necessity when the fallback tier (its only previous home) was
    removed, otherwise Aggressive and Conservative would have become
    functionally identical. Returns None whenever the filter tier has
    no clear lean, or no entry trigger matches it (at the min_agree
    bar) - every real caller of this function has its own rule-based
    price-trend fallback for that case now (manual/scheduled signals,
    Deriv Auto-Trade); Auto-Copy is the one caller that doesn't, since
    it has no strategy of its own to begin with.
    """

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

    def _run_strategy(strategy_fn):
        sig = inspect.signature(strategy_fn)
        if "m1_candles" in sig.parameters:
            return strategy_fn(index_key, config, h1_candles, h4_candles, daily_candles, m1_candles=m1_candles)
        return strategy_fn(index_key, config, h1_candles, h4_candles, daily_candles)

    strategy_pool = SYNTHETIC_INDEX_STRATEGY_GROUPS.get(index_key, SYNTHETIC_STRATEGY_BANK)

    # Two-tier per explicit instruction, mirroring the exact filter/
    # entry split already built for channel/manual forex signals and
    # Exness Auto-Trade - a lagging trend-confirming strategy can no
    # longer trigger a trade alone; it only sets the direction lean,
    # and a real entry-tier trigger in that same direction is
    # required to actually fire. Covers Deriv Auto-Trade (via
    # min_agree), Deriv manual signals, AND Deriv scheduled signals
    # all at once, since all three call this one function.
    #
    # Per explicit instruction: the flat vote-pool fallback that used
    # to sit here (ALL strategies as one pool, min_agree-gated) has
    # been REMOVED entirely - two-tier failing now returns None
    # directly. Every caller of this function now has its own rule-
    # based price-trend fallback for that case (build_synthetic_
    # signal_response for manual/scheduled signals, the Deriv Auto-
    # Trade bot scan for real money) - Auto-Copy is the one caller
    # that doesn't, and correctly just skips the round, since it has
    # no strategy of its own to fall back from in the first place.
    filter_fns, entry_fns = split_strategies_by_role(strategy_pool)
    direction = None
    winning_votes = []

    if filter_fns and entry_fns:
        filter_votes = []
        for fn in filter_fns:
            try:
                result = _run_strategy(fn)
                if result:
                    filter_votes.append(result)
            except Exception as e:
                print(f"[STRATEGY BANK SYNTH] filter {fn.__name__} failed for {index_key}: {e}")

        f_buy = [v for v in filter_votes if v["direction"] == "BUY"]
        f_sell = [v for v in filter_votes if v["direction"] == "SELL"]
        if filter_votes and len(f_buy) != len(f_sell):
            filter_direction = "BUY" if len(f_buy) > len(f_sell) else "SELL"
            entry_votes = []
            for fn in entry_fns:
                try:
                    result = _run_strategy(fn)
                    if result:
                        entry_votes.append(result)
                except Exception as e:
                    print(f"[STRATEGY BANK SYNTH] entry {fn.__name__} failed for {index_key}: {e}")
            matching_entries = [v for v in entry_votes if v["direction"] == filter_direction]
            # min_agree moved here, per necessity - it used to only be
            # enforced in the flat-vote fallback tier, which no longer
            # exists. Without moving it, Aggressive (min_agree=1) and
            # Conservative (min_agree=3) would have become functionally
            # identical (both would fire on just 1 matching entry
            # trigger). Now Aggressive fires on any 1 matching entry,
            # Conservative needs 3+ matching entries agreeing within
            # the entry tier - same distinction, new home.
            if len(matching_entries) >= min_agree:
                direction = filter_direction
                winning_votes = matching_entries

    if not direction:
        print(f"[STRATEGY BANK SYNTH] {index_key} - trend filters found no matching entry trigger this round")
        return None

    agreeing_names = [v["strategy_name"] for v in winning_votes]
    confidence = min(95, 70 + len(winning_votes) * 6)

    print(
        f"[STRATEGY BANK SYNTH] {index_key} -> {direction} (two-tier) | "
        f"{len(winning_votes)} agreeing: {', '.join(agreeing_names)}"
    )

    # EXPERIMENTAL flip, per explicit instruction - see
    # EXPERIMENTAL_INVERT_SIGNALS above. Applied here, BEFORE the
    # reason text is built below, specifically because that text
    # names the direction inline ("...agree on {direction}: ...") -
    # flipping after would leave the reasoning text describing the
    # opposite of what's actually shown as the headline. The print()
    # just above already captured the TRUE, un-flipped consensus for
    # Railway's logs before this runs.
    direction = maybe_invert_direction(direction)

    detail_strings = [v["detail"] for v in winning_votes[:3]]
    reason = (
        f"{len(winning_votes)} independent strategy(ies) agree on {direction}: "
        + "; ".join(detail_strings)
        + ("." if len(detail_strings) == len(winning_votes) else f", and {len(winning_votes) - 3} more.")
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

# Per-pair relevance keywords for the fundamental analysis context -
# per explicit instruction, after a real confirmed bug: a cached
# Bitcoin/rate-cut article was handed to the AI as "context" for a
# GOLD signal, and instead of correctly saying FUNDAMENTAL: NONE (as
# the prompt already instructs it to when there's no real connection),
# it forced a confusing, hallucinated-feeling bridge between the two
# ("Interest rate cut expectations favoring Bitcoin are a risk to the
# XAUUSD SELL call"). Rather than trusting the LLM to always self-
# police this correctly, the article is now filtered for relevance
# BEFORE it ever reaches the prompt - an irrelevant article is treated
# exactly like "no article available", so the model is never tempted
# to force a connection that isn't genuinely there in the first place.
PAIR_RELEVANCE_KEYWORDS = {
    "xauusd": ["gold", "xau", "fed", "federal reserve", "interest rate", "rate cut", "rate hike", "inflation", "dollar", "usd", "treasury yield", "real yield"],
    "xagusd": ["silver", "xag", "gold", "fed", "federal reserve", "interest rate", "rate cut", "rate hike", "inflation", "dollar", "usd"],
    "btcusd": ["bitcoin", "btc", "crypto", "cryptocurrency"],
    "eurusd": ["euro", "eur", "ecb", "european central bank", "dollar", "usd", "fed", "federal reserve"],
    "gbpusd": ["pound", "sterling", "gbp", "bank of england", "boe", "dollar", "usd", "fed"],
    "gbpjpy": ["pound", "sterling", "gbp", "bank of england", "boe", "yen", "jpy", "bank of japan", "boj"],
    "usdjpy": ["yen", "jpy", "bank of japan", "boj", "dollar", "usd", "fed", "federal reserve"],
    "usdcad": ["loonie", "cad", "canadian dollar", "oil", "dollar", "usd", "fed"],
    "usdchf": ["franc", "chf", "swiss", "snb", "dollar", "usd", "fed"],
    "audusd": ["aussie", "aud", "australian dollar", "rba", "dollar", "usd", "fed"],
    "nzdusd": ["kiwi", "nzd", "rbnz", "dollar", "usd", "fed"],
    "usoil": ["usoil", "oil price", "oil prices", "crude oil", "crude", "opec", "wti", "brent crude", "dollar", "usd"],
}

def is_article_relevant_to_pair(article, pair_name):
    """
    True only if the article's title/description contains at least one
    keyword genuinely tied to this specific pair's currencies/asset -
    NOT a general "is this forex-related at all" check. A Bitcoin
    article is real financial news, but it has zero of gold's keywords
    in it, so it correctly returns False for an XAUUSD signal even
    though it would (correctly) return True for a BTCUSD one.
    """
    if not article or not article.get("title"):
        return False
    keywords = PAIR_RELEVANCE_KEYWORDS.get(pair_name.lower().replace("/", ""))
    if not keywords:
        return True  # no keyword list defined for this pair - don't block, fall through to the AI's own judgment
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    return any(kw in text for kw in keywords)

# Per explicit instruction, after a SECOND real confirmed instance of
# the same underlying problem: an article about "Applied Digital" (a
# single company whose business is hosting Bitcoin miners) correctly
# passed the pair-keyword check above (it genuinely does mention
# "bitcoin", and the pair IS BTCUSD) - but the article is single-
# company EQUITY/business news, not something that actually drives
# Bitcoin's real spot price the way a Fed decision, ETF flow, or
# halving event would. The AI again forced a shaky "risk" framing
# instead of recognizing this isn't genuine macro context. Keyword-
# matching the right ASSET isn't enough - this also has to exclude the
# wrong KIND of news, regardless of which pair it happens to mention.
COMPANY_SPECIFIC_INDICATORS = [
    "inc.", "inc ", " corp", "corporation", " ltd.", " ltd ",
    "shares of", "share price", "stock price", "nasdaq:", "nyse:",
    "quarterly earnings", "earnings report", "q1 earnings", "q2 earnings",
    "q3 earnings", "q4 earnings", "reliance on", "revenue from",
    "hosting business", "data center company", "ipo", "stock surged",
    "stock fell", "shares surged", "shares fell", "shares rose",
]

def is_company_specific_news(article):
    """
    True if the article reads as single-company equity/business news
    (a specific firm's stock, earnings, revenue model, hosting
    contracts, etc.) rather than genuine macro/market-moving context
    for a currency or crypto PRICE. This check runs regardless of
    which pair's keywords the article also happens to match - a
    company-specific story doesn't become valid fundamental context
    just because it mentions the right asset in passing.
    """
    if not article or not article.get("title"):
        return False
    text = f"{article.get('title', '')} {article.get('description', '')}".lower()
    return any(indicator in text for indicator in COMPANY_SPECIFIC_INDICATORS)

async def generate_fundamental_context(pair_name, direction):
    # Check what today's own news briefing already told subscribers
    # about this SAME pair, BEFORE falling back to a fresh, possibly
    # unrelated article - this is the actual fix for signals silently
    # contradicting a briefing already posted hours earlier the same
    # day. If today's briefing gave this pair an explicit direction:
    #   - it agrees with this signal -> reuse THAT exact reasoning
    #     (perfect consistency, not a freshly-generated second angle)
    #   - it disagrees -> show no fundamental at all, full stop. Per
    #     explicit instruction: a signal should never be paired with
    #     fundamental reasoning that fights its own direction, and it
    #     also should never contradict what the channel ALREADY told
    #     the same subscribers that same day. Does not fall through
    #     to the general logic below in this case - the briefing is
    #     the more specific, already-public source of truth for this
    #     pair today, so a second, different-sounding narrative from
    #     a fresh Gemini call would just create a second contradiction
    #     instead of fixing the first one.
    todays_bias = get_todays_news_bias(pair_name)
    if todays_bias:
        bias_direction = todays_bias.get("direction")
        if bias_direction == direction:
            headline_snippet = todays_bias.get("headline_text", "").strip()
            if not headline_snippet:
                headline_snippet = "see this morning's market update."
            return (
                f"Today's briefing already flagged this: {headline_snippet} "
                f"This lines up with today's news direction."
            )
        else:
            return None

    article = get_cached_news_context()
    if not is_article_relevant_to_pair(article, pair_name) or is_company_specific_news(article):
        article = None
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
{pair_name}'s actual PRICE (a macro driver like a rate decision, ETF
flow, inflation data, central bank action, or similar) AND it
genuinely SUPPORTS this {direction} call, write ONE sentence
explaining how it relates to this {direction} call, THEN end with a
short, plain closing clause stating what it means for this trade -
e.g. "This favors buyers." / "This favors sellers." A newbie should
never have to work out for themselves whether the reasoning is good
news for this specific trade - always say it plainly at the end.

IMPORTANT - per explicit instruction: the fundamental analysis must
NEVER conflict with the signal's own direction. If the information
above points the OTHER way from this {direction} call (bearish news
under a BUY call, or bullish news under a SELL call), do NOT show it
at all, even framed as a caution or risk to be aware of - respond
FUNDAMENTAL: NONE instead. A subscriber should never see a BUY
signal paired with a reason to be bearish, or vice versa - two
conflicting messages in the same signal is worse than showing none.

NOT a genuine connection - respond FUNDAMENTAL: NONE for things like:
- A single company's stock, earnings, or business model (e.g. a
  hosting/mining company's revenue risk) - this doesn't move the
  actual spot price of a currency or crypto asset.
- Any connection that only works by mentioning the same word (e.g.
  "bitcoin") without a real causal link to price direction.
Do not force a connection that isn't genuinely there, even if it
sounds plausible on the surface.

Respond in EXACTLY this format, nothing else, no markdown:
FUNDAMENTAL: [one sentence + closing clause, max 24 words] OR FUNDAMENTAL: NONE
"""
    else:
        prompt = f"""
You are a forex/macro analyst. No specific real-time news or
calendar data is available right now. The technical analysis for
{pair_name} already calls a {direction}. If you can give genuinely
useful general macro context that SUPPORTS this pair and direction,
write ONE sentence, making clear it's a general pattern rather than
a specific current event, THEN end with a short, plain closing
clause stating what it means for this trade - e.g. "This favors
buyers." / "This favors sellers." A newbie should never have to work
out for themselves whether the reasoning is good news for this
specific trade.

IMPORTANT - per explicit instruction: never provide context that
conflicts with the signal's own direction, even framed as a caution
or risk. If the only genuinely relevant context you can think of
points the OTHER way from this {direction} call, respond
FUNDAMENTAL: NONE instead - a subscriber should never see a BUY
signal paired with a reason to be bearish, or vice versa.

If you have nothing genuinely useful to add, respond with exactly:
FUNDAMENTAL: NONE

Respond in EXACTLY this format, nothing else, no markdown:
FUNDAMENTAL: [one sentence + closing clause, max 24 words] OR FUNDAMENTAL: NONE
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
            reason = "Potential buy setup spotted."
        elif current_price < price_1h_ago:
            direction = "SELL"
            reason = "Potential sell setup spotted."
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
            return prev_direction, "Potential " + ("buy" if prev_direction == "BUY" else "sell") + " setup spotted."
    direction = random.choice(["BUY", "SELL"])
    return direction, "Potential " + ("buy" if direction == "BUY" else "sell") + " setup spotted."

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


def generate_daily_line_chart(display_name, direction, daily_history, save_path, entry=None, sl=None, tp=None):
    """
    Dedicated chart for the XAGUSD daily fallback ONLY. Draws a real
    LINE chart of actual daily closes - deliberately NOT a
    candlestick chart, since metals.dev's timeseries only provides
    one price per day (no real open/high/low), and drawing fake
    wicks around a single number would misrepresent real data as
    something more detailed than it is. Per explicit instruction,
    restyled to match the candlestick charts' Entry/SL/TP line and
    label treatment exactly (same colors, same _chart_fmt_price
    helper) - the line-vs-candlestick choice stays honest, but
    everything else now looks visually consistent with every other
    pair's chart.
    """
    try:
        if not daily_history or len(daily_history) < 5:
            return False

        dates = [datetime.strptime(h["date"], "%Y-%m-%d") for h in daily_history]
        closes = [h["close"] for h in daily_history]

        fig, ax = plt.subplots(figsize=(10, 6), dpi=150)
        fig.patch.set_facecolor("#0f1115")
        ax.set_facecolor("#0f1115")

        x = mdates.date2num(dates)
        line_color = "#22c55e" if direction == "BUY" else "#ef4444"
        ax.plot(x, closes, color=line_color, linewidth=2, marker="o", markersize=4, zorder=3)
        ax.fill_between(x, closes, min(closes) - (max(closes) - min(closes)) * 0.1, color=line_color, alpha=0.08, zorder=1)

        ma_period = min(10, len(closes) - 1)
        if ma_period >= 3:
            ma_values = [None] * (ma_period - 1) + [
                sum(closes[i - ma_period + 1:i + 1]) / ma_period for i in range(ma_period - 1, len(closes))
            ]
            ax.plot(x, ma_values, color="#f59e0b", linewidth=1.4, linestyle="--", label=f"{ma_period}-day MA", zorder=4)

        # Entry/SL/TP lines + labels, same styling as the candlestick
        # charts (generate_signal_chart/_chart_finish) - same colors,
        # same label box, same _chart_fmt_price decimal-precision
        # helper, so this chart only differs from the others in
        # being a line instead of candlesticks, nothing else.
        label_box = dict(boxstyle="round,pad=0.3", facecolor="#0f1115", edgecolor="none")
        all_values = closes + [v for v in (entry, sl, tp) if v is not None]
        x_right = x[-1] + (x[-1] - x[0]) * 0.01 if len(x) > 1 else x[-1] + 1

        if entry is not None:
            ax.axhline(entry, color="#e5e7eb", linewidth=1, linestyle="--", zorder=2)
            ax.text(x_right, entry, f"Entry {_chart_fmt_price(entry, all_values)}", color="#e5e7eb", fontsize=9, va="center", bbox=label_box, zorder=5)
        if sl is not None:
            ax.axhline(sl, color="#ef4444", linewidth=1, linestyle="--", zorder=2)
            ax.text(x_right, sl, f"SL {_chart_fmt_price(sl, all_values)}", color="#ef4444", fontsize=9, va="center", bbox=label_box, zorder=5)
        if tp is not None:
            ax.axhline(tp, color="#22c55e", linewidth=1, linestyle="--", zorder=2)
            ax.text(x_right, tp, f"TP {_chart_fmt_price(tp, all_values)}", color="#22c55e", fontsize=9, va="center", bbox=label_box, zorder=5)

        if entry is not None or sl is not None or tp is not None:
            ax.set_xlim(x[0] - (x[-1] - x[0]) * 0.02 if len(x) > 1 else x[0] - 1, x_right + (x[-1] - x[0]) * 0.12 if len(x) > 1 else x_right + 2)
            y_pad = (max(all_values) - min(all_values)) * 0.08
            ax.set_ylim(min(all_values) - y_pad, max(all_values) + y_pad)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="upper left", facecolor="#0f1115", edgecolor="#2d3139", labelcolor="#e5e7eb", fontsize=9)

        ax.xaxis_date()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        plt.setp(ax.get_xticklabels(), rotation=20)
        ax.tick_params(colors="#9ca3af", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#2d3139")
        ax.set_title(
            f"{display_name} — Daily Close (no intraday data available) — {direction}",
            color="#e5e7eb", fontsize=11, fontweight="bold", loc="left", pad=12,
        )
        ax.grid(color="#1f2329", linewidth=0.5, alpha=0.5)

        plt.tight_layout()
        plt.savefig(save_path, facecolor="#0f1115")
        plt.close(fig)
        return True
    except Exception as e:
        print(f"[CHART] Failed to generate daily line chart for {display_name}: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return False


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
        # FIX: CONFIRMED REAL, LIVE BUG - H1 candles are cached for a
        # full hour (CANDLE_CACHE_SECONDS), but the live price used
        # for `entry` is always fetched fresh with no caching at all.
        # For a fast-moving instrument (BTCUSD confirmed live - a
        # 250-450 point gap), the chart's own rightmost candles could
        # be showing price from up to 59 minutes ago while Entry/SL/TP
        # reflect right now, making the chart visually CONTRADICT the
        # numbers printed next to it - not a wrong signal, a dishonest
        # picture of one. Fixed by appending one extra "live" point
        # using the real entry price as its open/high/low/close,
        # BEFORE any indicator is computed (not just before drawing) -
        # so every indicator (MA, RSI, MACD, etc.) reflects the true
        # current price too, not just the picture. The chart can now
        # never show a last candle that disagrees with the quoted
        # Entry price, regardless of how stale the underlying cache is.
        if entry and candles:
            live_point = {
                "open": entry, "high": entry, "low": entry, "close": entry,
                "volume": 0,
            }
            candles = candles + [live_point]

        if not candles or len(candles) < 5:
            # FIX: this early exit was completely silent before -
            # CONFIRMED REAL CASE via live logs: a real signal
            # (btcusd -> SELL, 1 agreeing: ICT/SMC) correctly reached
            # this function, but fell back to the static SELL banner
            # image with ZERO log output anywhere (no [CANDLES], no
            # [CHART], nothing) - meaning h1_candles was apparently
            # None or too short by the time this ran, but there was
            # no trace of that anywhere to diagnose it from. Logging
            # here now so this exact scenario is visible going
            # forward, rather than looking like nothing was even
            # attempted.
            print(f"[CHART] {display_name}/{strategy_name}: no usable candles ({len(candles) if candles else 0} given), falling back to static image")
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

        if strategy_name == "Trend Following (MA)":
            if len(candles) >= 50:
                def sma(period):
                    return [None] * (period - 1) + [
                        sum(closes[i - period + 1:i + 1]) / period for i in range(period - 1, len(closes))
                    ]
                ma20, ma50 = sma(20), sma(50)
            else:
                # DIAGNOSTIC LOGGING - per explicit instruction. Before
                # this, a strategy name could win, get titled correctly
                # at the top of the chart, and silently skip its own
                # overlay with zero trace anywhere if the candle count
                # came up short of its own threshold - the only
                # existing log fired solely on near-total candle
                # absence (<5), not this "just barely not enough" case.
                print(f"[CHART] {display_name}/{strategy_name}: only {len(candles)} candles, need 50+ - MA overlay skipped")

        elif strategy_name == "EMA Pullback Scalper":
            if len(candles) >= 55:
                ema20_raw = calculate_ema_series(candles, 20)
                ema50_raw = calculate_ema_series(candles, 50)
                ema20 = [None] * (len(candles) - len(ema20_raw)) + list(ema20_raw)
                ema50 = [None] * (len(candles) - len(ema50_raw)) + list(ema50_raw)
            else:
                print(f"[CHART] {display_name}/{strategy_name}: only {len(candles)} candles, need 55+ - EMA overlay skipped")

        elif strategy_name == "Breakout":
            if len(candles) >= 30:
                consolidation = candles[-11:-1]
                range_high = max(c["high"] for c in consolidation)
                range_low = min(c["low"] for c in consolidation)
                consolidation_box = (range_low, range_high)
            else:
                print(f"[CHART] {display_name}/{strategy_name}: only {len(candles)} candles, need 30+ - box overlay skipped")

        elif strategy_name == "Volatility Breakout Scalper":
            if len(candles) >= 11:
                prior_10 = candles[-11:-1]
                breakout_lines = (max(c["high"] for c in prior_10), min(c["low"] for c in prior_10))
            else:
                print(f"[CHART] {display_name}/{strategy_name}: only {len(candles)} candles, need 11+ - breakout lines skipped")

        elif strategy_name == "Support/Resistance Bounce":
            # FIX: this used to just plot the last candle's own high/low
            # as a placeholder - not the real tested level the strategy
            # actually detected and traded on, which could show (and
            # did, confirmed live) a completely different, misleading
            # number on the chart than the one in the signal's own
            # text. Recomputes the exact same swing/tested-level logic
            # strategy_support_resistance_bounce uses, so the chart and
            # the text can never disagree again.
            if len(candles) >= 30:
                swings = find_swing_points(candles, strength=2)
                sr_tolerance = 0.0015
                level_touches = {}
                for s in swings:
                    matched = False
                    for lvl in list(level_touches.keys()):
                        if abs(s["price"] - lvl) / lvl <= sr_tolerance:
                            level_touches[lvl].append(s)
                            matched = True
                            break
                    if not matched:
                        level_touches[s["price"]] = [s]
                tested = {lvl: t for lvl, t in level_touches.items() if len(t) >= 2}
                wanted_type = "low" if direction == "BUY" else "high"
                proximity = 0.005
                last_c = candles[-1]
                for lvl, touches in tested.items():
                    if touches[0]["type"] != wanted_type:
                        continue
                    if wanted_type == "low" and lvl * (1 - proximity) <= last_c["low"] <= lvl * (1 + sr_tolerance):
                        sr_level = lvl
                        break
                    if wanted_type == "high" and lvl * (1 - sr_tolerance) <= last_c["high"] <= lvl * (1 + proximity):
                        sr_level = lvl
                        break
            else:
                print(f"[CHART] {display_name}/{strategy_name}: only {len(candles)} candles, need 30+ - level overlay skipped")

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
        if vote["direction"] == "BUY":
            return "Price is above both the 20MA and 50MA — bullish."
        else:
            return "Price is below both the 20MA and 50MA — bearish."
    if name == "Breakout":
        return f"Broke out of its recent range: {detail}."
    if name == "Support/Resistance Bounce":
        return f"Price rejected a key level: {detail}."
    if name == "Volatility Breakout Scalper":
        return f"Clean breakout: {detail}."
    if name == "EMA Pullback Scalper":
        if vote["direction"] == "BUY":
            return "Bullish pullback bounce off the short-term average."
        else:
            return "Bearish pullback rejection off the short-term average."
    if name == "Momentum (MACD)":
        return f"MACD confirms momentum: {detail}."
    if name == "RSI Extreme Reversal":
        return f"RSI at an extreme: {detail}."
    if name == "Bollinger+RSI Mean Reversion":
        return f"Price snapping back: {detail}."
    if name == "ICT/SMC":
        return f"Structure confirms it: {detail}."
    if name == "RSI Trend Continuation":
        return f"RSI trend continuation: {detail}."
    if name == "Bollinger Squeeze Breakout":
        return f"Bollinger squeeze fired: {detail}."
    if name == "ATR Volatility Breakout":
        return f"Volatility surge: {detail}."
    if name == "Supertrend":
        return f"Supertrend flip: {detail}."
    if name == "Parabolic SAR":
        return f"SAR flip: {detail}."
    if name == "Ichimoku Breakout":
        return f"Cloud break: {detail}."
    if name == "Keltner Breakout":
        return f"Keltner break: {detail}."
    if name == "EMA Ribbon":
        return f"EMA ribbon aligned: {detail}."
    if name == "Rate of Change":
        return f"Momentum accelerating: {detail}."
    if name == "CCI Breakout":
        return f"CCI extreme: {detail}."
    if name == "Williams %R":
        return f"Williams %R turning: {detail}."
    if name == "Heikin-Ashi Trend":
        return f"Heikin-Ashi confirms trend: {detail}."
    return detail[0].upper() + detail[1:] + "."


def generate_signal_narrative(display_name, direction, winning_votes):
    """
    Returns up to 3 short bullet lines, one per agreeing strategy, per
    explicit instruction - names every agreeing strategy rather than
    just the first, each prefixed with "• " so multiple strategies
    read as a clean, scannable list instead of a run-on paragraph.
    Capped at 3 lines even if more agreed (adds a brief "+N more"
    line instead of listing every single one), since some banks now
    have 10+ strategies and listing all of them would be far too much
    text on a single signal. Confidence % always reflects the TRUE
    total agreement count regardless of this cap - only the printed
    reasons are capped, never the confidence math.
    """
    if not winning_votes:
        return f"{display_name} {direction.lower()} signal."

    lines = [f"• {_narrative_strategy_sentence(v)}" for v in winning_votes[:3]]
    if len(winning_votes) > 3:
        lines.append(f"• + {len(winning_votes) - 3} more strategy(ies) agree.")
    return "\n".join(lines)


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

    daily_fallback_history = None
    if not direction:
        # FIX: the strategy bank now genuinely runs on real MetaAPI
        # data for XAGUSD/USOIL - confirmed live, the bank correctly
        # found a trend lean with no qualifying entry yet ("waiting
        # for an actual moment"), which is a legitimate, healthy "no
        # signal this round" outcome, same as any other pair. The
        # special-cases below (XAGUSD's separate metals.dev daily
        # fallback, USOIL's hard "no data source" message) both
        # PREDATE MetaAPI and were written for a world where these 2
        # pairs had zero real data at all - confirmed live, they were
        # firing even now that real data works, misrepresenting "no
        # signal yet" as "we have no data," and showing a materially
        # worse answer (a coarse daily-close heuristic, or an outright
        # false error) than the real, live H1 data actually supports.
        # Both removed - XAGUSD/USOIL now fall through to the exact
        # same generate_rule_based_bias every other pair already
        # uses, just below.
        #
        # That fallback needs a real price_1h_ago to do anything
        # better than a coin flip, and get_price_history_1h() still
        # always returns None for these two (by design - both use a
        # spot-price-only provider with no real history endpoint) -
        # unrelated to and unfixed by the MetaAPI work, so still a
        # real gap on its own. Since h1_candles now has genuine recent
        # history for these pairs via MetaAPI, derive price_1h_ago
        # directly from it instead of that separate, still-broken
        # function - real data, not a fabricated random guess.
        if price_1h_ago is None and h1_candles and len(h1_candles) >= 2:
            price_1h_ago = h1_candles[-2]["close"]

        direction, reason = generate_rule_based_bias(
            matched_key, current_price, price_1h_ago
        )
        confidence = random.randint(80, 94)

    # AI fundamental layer (capped) - per explicit instruction, REMOVED
    # from scheduled channel signals entirely now (user_id=None means
    # scheduled - see the comment this replaced). This system has been
    # the source of several real bugs this session (a level cited in
    # the text not matching the level actually detected/plotted,
    # signals contradicting that same day's own morning briefing,
    # near-identical phrasing across unrelated pairs reading as
    # scripted) - pulling it from the highest-visibility path (every
    # subscriber sees every scheduled post) while keeping it for
    # manual DM requests (lower volume, opt-in, someone specifically
    # wants the extra context) is a deliberate risk reduction, not an
    # oversight. DM signals stay capped by the exact same per-user/
    # global limits used elsewhere in this file. AI NEVER decides or
    # contradicts the direction - it's told the technical call up
    # front and only writes a supporting/caveat sentence for the
    # separate Fundamental Analysis row below.
    fundamental_reason = None
    if user_id is not None and can_use_ai_bias(user_id):
        fundamental_reason = await generate_fundamental_context(pair_name, direction)
        if fundamental_reason:
            record_ai_bias_usage(user_id)
            used_ai_layer = True

    strength = "STRONG"

    # SL/TP pip distances, per explicit instruction with EVERY value
    # below independently verified by calculation against each pair's
    # real pip_size/pip_value (not assumed - this exact area already
    # had one real math mistake earlier, see the conversation history
    # for the GBPUSD case that caught it). Multiplier = target_pips /
    # (pip_size / pip_value):
    #   XAUUSD:  100 SL / 200 TP -> 0.2x / 0.4x   (pip_size=5.0)
    #   BTCUSD:  50 SL / 100 TP  -> 0.3025x/0.605x (pip_size=165.3,
    #            using the precise value rather than a round 0.3/0.6,
    #            since BTCUSD's unusually large pip_size makes a
    #            rounded multiplier drift further from the real
    #            target than it does on the forex pairs below)
    #   GBPJPY/EURJPY: 50 SL / 100 TP -> the EXISTING 3x/6x default
    #            already lands here (pip_size=0.1667/pip_value=0.01
    #            -> 16.67 pips/unit -> 3x=50.0, 6x=100.0, verified by
    #            calculation) - confirmed needing NO override, not
    #            assumed this time.
    #   GBPUSD/EURUSD/AUDUSD/USDCAD/USDJPY/USDCHF/NZDUSD: 30 SL / 60
    #            TP -> 1.8x/3.6x (all seven share the same ~16.67
    #            pips/unit ratio despite the JPY pair's pip_size
    #            looking different on paper - 0.1667/0.01 vs
    #            0.001667/0.0001 are the same ratio - confirmed by
    #            calculation, not assumed)
    #   USOIL/XAGUSD: 70 SL / 140 TP -> 4.2x/8.4x (both share
    #            pip_size=0.1667/pip_value=0.01, same ~16.67
    #            pips/unit ratio as the JPY pairs above). Note: USOIL
    #            currently has no working real-data fallback at all
    #            (see NO_DATA_AVAILABLE handling just below) so this
    #            setting has no visible effect today, but is set
    #            correctly in case that data-source gap is closed
    #            later. XAGUSD DOES reach this via its dedicated
    #            daily-trend fallback, so this is live for XAGUSD now.
    #   XAUUSD UPDATE: widened to 150 SL / 300 TP real pips (0.3x/0.6x),
    #   per explicit instruction - gold has been unusually volatile
    #   lately and hitting the tighter 100/200 (2x/4x) SL repeatedly,
    #   confirmed via a real live losing trade screenshot. Verified by
    #   calculation: pip_size=5.0 * 0.3 / pip_value=0.01 = 150 pips
    #   (\$15 real price move -> \$150 P&L at 0.1 lot), * 0.6 = 300 pips
    #   (\$30 -> \$300 P&L at 0.1 lot) - same 1:2 ratio as before, just
    #   a wider buffer so normal gold volatility has more room before
    #   triggering SL.
    # XAUUSD CORRECTION #2: CONFIRMED REAL BUG via a live trade
    # screenshot showing SL hit at only -$20.40 instead of the
    # intended -$150. Root cause: when this was set to "150 pips SL /
    # 300 pips TP", the multiplier (0.3/0.6) was calculated using
    # this bot's INTERNAL pip-counting convention (price_move /
    # pip_value=0.01), where each "pip" = just $0.01 of price
    # movement - NOT the same "pip" unit used when the ORIGINAL
    # $100/$200 target was set and verified (where 2x/4x = a $10
    # price move was confirmed as "100 pips" in a real trading sense,
    # i.e. 1 real pip = $0.10 price move on gold, not $0.01). 150
    # real pips in that original, correct sense = a $15 price move =
    # $150 P&L at 0.1 lot, which needs multiplier 3.0 (pip_size=5.0 *
    # 3.0 = $15), not 0.3. Verified: 3.0/6.0 -> $15/$30 price move ->
    # $150/$300 P&L at 0.1 lot, exactly matching the real target.
    if matched_key == "xauusd":
        sl_multiplier, tp_multiplier = 3.0, 6.0
    elif matched_key in ("gbpusd", "eurusd", "audusd", "usdcad", "usdjpy", "usdchf", "nzdusd"):
        sl_multiplier, tp_multiplier = 1.8, 3.6
    elif matched_key in ("usoil", "xagusd"):
        sl_multiplier, tp_multiplier = 4.2, 8.4
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
    if daily_fallback_history is not None:
        # XAGUSD daily fallback - real daily closes, no real OHLC, so
        # this uses the dedicated line-chart generator, never the
        # candlestick one (which would have to fabricate fake wicks
        # around a single daily price point). Per explicit
        # instruction, reverted back to the clean trend-only view -
        # entry/sl/tp are deliberately NOT passed here, so no price
        # target lines/labels are drawn on this chart (the message
        # text below still states real Entry/SL/TP numbers, just not
        # the chart itself).
        chart_path = os.path.join(CHART_OUTPUT_DIR, f"{matched_key}_daily_{int(time.time())}.png")
        chart_ok = generate_daily_line_chart(display, direction, daily_fallback_history, chart_path)
        if chart_ok:
            image_file_id = chart_path
    else:
        # Per explicit instruction, after a real confirmed gap: this
        # used to gate chart generation ENTIRELY behind having a real
        # winning strategy vote - if the signal came from the zero-
        # strategy fallback (generate_rule_based_bias, e.g. "Price
        # trending downward over the last hour"), chart_strategy_name
        # was always None, so the whole chart-generation block
        # (including the retry added for exactly this kind of issue)
        # never even ran - it went straight to the generic branded
        # image every time, regardless of whether a real chart could
        # have been drawn. There ARE still real candles, a real entry,
        # and real SL/TP in this case - only a NAMED strategy is
        # missing - so this now always attempts a real chart, using a
        # generic "Momentum" label (no strategy-specific overlay logic
        # matches that name, so generate_signal_chart just skips the
        # overlay step and still draws the base candlestick chart +
        # entry/SL/TP lines, exactly like every other chart).
        chart_strategy_name = winning_votes[0]["strategy_name"] if winning_votes else "Momentum"
        chart_path = os.path.join(CHART_OUTPUT_DIR, f"{matched_key}_{int(time.time())}.png")
        chart_ok = generate_signal_chart(
            display, chart_strategy_name, direction, h1_candles,
            entry_price, stop_loss, take_profit, chart_path,
        )
        if not chart_ok:
            # RETRY: per explicit instruction, after a real
            # occurrence of a DM signal falling back to the static
            # branded graphic instead of a real chart. Chart
            # generation is pure local rendering (matplotlib) on
            # real candles - no AI, no extra API cost - so a retry
            # is genuinely free aside from a short delay. Re-fetches
            # candles fresh rather than just re-running the same
            # render on the same data: get_cached_candles only
            # caches on SUCCESS, so if the first attempt's candle
            # fetch itself came back thin/empty (a transient data-
            # provider hiccup), this retry has a real chance of
            # getting a fuller set the second time, not just
            # deterministically repeating the same failure.
            print(f"[CHART] {display}/{chart_strategy_name}: first attempt failed, retrying once with a fresh candle fetch...")
            time.sleep(2)
            retry_candles = get_cached_candles(matched_key, config, "1h", outputsize=210)
            if retry_candles:
                chart_ok = generate_signal_chart(
                    display, chart_strategy_name, direction, retry_candles,
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
        # Added for per-strategy performance tracking - joined against
        # this signal's eventual TP_HIT/SL_HIT status (already tracked
        # separately), this is what makes "which strategies are
        # actually working" an answerable question with real numbers
        # instead of a guess, the next time it comes up.
        "agreeing_strategies": agreeing_strategies if bank_result else [],
        "confidence": confidence,
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

def fetch_news_gnews_pool():
    """
    Returns the full LIST of qualifying articles, not a single pick -
    per explicit instruction, after finding that gold/BTC dominated
    the daily news post far more than USD/EUR/GBP/JPY/oil ever did,
    despite all being technically allowed by NEWS_RELEVANT_KEYWORDS.
    Root cause: the old fetch_market_news tried GNews FIRST and only
    moved to TheNewsAPI/Alpha Vantage if GNews found NOTHING at all -
    a strict waterfall. GNews's own query pulls from a broad general
    "business" category, which naturally skews toward whatever's
    globally trending day-to-day (commonly gold/crypto), while Alpha
    Vantage's dedicated forex/macro query rarely got a turn since
    GNews almost always found *something*. Returning the full pool
    here (instead of picking immediately) lets fetch_market_news
    combine all three sources into ONE shared random draw, so no
    single source's own topic bias can dominate by default.
    """
    if not GNEWS_API_KEY:
        return []
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
        return [
            {
                "title": a.get("title", ""),
                "description": a.get("description", ""),
                "image": a.get("image", ""),
                "source": a.get("source", {}).get("name", "GNews"),
            }
            for a in articles
        ]
    except Exception as e:
        print(f"[GNEWS] Error: {e}")
        return []

# ============================================
# NEWS FETCHER — THENEWSAPI FALLBACK
# ============================================

def fetch_news_thenewsapi_pool():
    """Returns the full LIST of qualifying articles - see fetch_news_gnews_pool's docstring for why."""
    if not THENEWS_API_KEY:
        return []
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
        return [
            {
                "title": a.get("title", ""),
                "description": a.get("description", ""),
                "image": a.get("image_url", ""),
                "source": a.get("source", "TheNewsAPI"),
            }
            for a in articles
        ]
    except Exception as e:
        print(f"[THENEWSAPI] Error: {e}")
        return []

# ============================================
# NEWS FETCHER — ALPHA VANTAGE
# Third fallback, forex/macro/crypto topics
# ============================================

def fetch_news_alphavantage_pool():
    """Returns the full LIST of qualifying articles - see fetch_news_gnews_pool's docstring for why."""
    if not ALPHA_VANTAGE_API_KEY:
        return []
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
        return [
            {
                "title": a.get("title", ""),
                "description": a.get("summary", ""),
                "image": a.get("banner_image", ""),
                "source": a.get("source", "Alpha Vantage"),
            }
            for a in articles
        ]
    except Exception as e:
        print(f"[ALPHAVANTAGE NEWS] Error: {e}")
        return []

# ============================================
# NEWS FETCHER — COMBINED
# ============================================

def fetch_market_news():
    # Combines ALL THREE sources into one shared pool, then draws
    # randomly from that combined set - per explicit instruction,
    # after finding gold/BTC dominated the daily post far more than
    # USD/EUR/GBP/JPY/oil, even though all are technically eligible.
    # The old strict waterfall (GNews first, only fall through if it
    # found NOTHING) let GNews's own general "business" category bias
    # win by default every time it found anything at all, never
    # giving Alpha Vantage's more forex-focused query a real turn.
    # Each source still degrades gracefully to an empty list on its
    # own failure (missing key, API error), so a single source being
    # down never blocks the other two from contributing.
    combined_pool = (
        fetch_news_gnews_pool()
        + fetch_news_thenewsapi_pool()
        + fetch_news_alphavantage_pool()
    )
    if not combined_pool:
        print("[NEWS] All news APIs failed or no relevant forex/BTC/oil news today.")
        return None
    article = random.choice(combined_pool)
    print(f"[NEWS] ✅ Article selected from a combined pool of {len(combined_pool)} qualifying articles ({article.get('source', 'unknown')})")
    return article

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
            currency = event.get("country", "")
            if currency not in ["USD", "EUR", "GBP", "JPY"]:
                continue
            title = event.get("title", "")
            time_utc = event.get("date", "")
            flag = flag_map.get(currency, "🌍")

            if time_utc and "T" in time_utc:
                try:
                    # Same fix as get_todays_high_impact_events: the feed's
                    # date field embeds a US-Eastern offset, not UTC.
                    dt_parsed = datetime.fromisoformat(time_utc)
                    if dt_parsed.tzinfo is not None:
                        dt_utc = dt_parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        dt_utc = dt_parsed
                    lagos_dt = dt_utc + timedelta(hours=1)  # WAT = UTC+1 year-round, no DST
                    time_str = f"{lagos_dt.hour:02d}:{lagos_dt.minute:02d} GMT+1"
                except Exception:
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
# NEWS-DRIVEN DIRECT CALL FEATURE (NEW)
# Replaces the old Breakdown button/flow, per
# explicit instruction. Old Breakdown asked the AI
# to freely invent an entire "Technical Analysis"
# and trade idea (entry/SL/TP) from nothing but a
# live price - no real candles, no strategy bank,
# a real gap this replacement closes.
#
# Design: user taps a specific high-impact news
# event -> AI judges ONLY whether that event is
# bullish/bearish for the event's OWN currency (a
# narrow, constrained question) -> this code (not
# the AI) deterministically maps that into a BUY/
# SELL call on a specific tradeable pair, since the
# same "currency bullish" reading points different
# directions depending on whether that currency is
# the BASE or QUOTE side of the chosen pair (e.g.
# USD is the quote currency in XAU/USD, so USD
# strength means XAUUSD SELLS, not buys) - per
# explicit instruction, this mechanical inversion is
# handled in code, not left for the AI to get wrong.
# The pair's REAL existing technical strategy vote
# is then checked too (fundamentals still drive the
# call either way, per explicit instruction, but the
# technical read is surfaced transparently either
# way rather than silently blended in).
# ============================================

CURRENCY_PAIR_MAP = {
    # inverted=True means the event's currency is the QUOTE currency
    # in the mapped pair (e.g. USD in XAU/USD, JPY in USD/JPY) - the
    # naive "bullish=BUY" reading has to be flipped. inverted=False
    # means the event's currency is the BASE currency (e.g. EUR in
    # EUR/USD) - bullish maps directly to BUY, no flip needed.
    "USD": {"pair_key": "xauusd", "inverted": True},
    "EUR": {"pair_key": "eurusd", "inverted": False},
    "GBP": {"pair_key": "gbpusd", "inverted": False},
    "JPY": {"pair_key": "usdjpy", "inverted": True},
}

def get_todays_high_impact_events():
    """
    Same real Forex Factory feed as fetch_economic_calendar, but
    returns the RAW structured event list (title/currency/time/
    forecast/previous/actual) instead of pre-formatted text - needed
    so each event can be shown as its own tappable button and looked
    up again individually once tapped. Scoped to the same USD/EUR/
    GBP/JPY currencies as the daily calendar post, since those are
    the only ones with a mapped pair above.
    """
    try:
        today = datetime.utcnow()
        today_str = today.strftime("%Y-%m-%d")

        # Uses the SAME shared 1-hour cache as get_cached_calendar_data
        # (originally built for get_relevant_calendar_events) rather
        # than a fresh raw request every call - Forex Factory hard
        # rate-limits this feed to just 2 requests per 5 minutes
        # across ALL formats combined. This function used to hit the
        # feed directly on every single call (background job every 5
        # min + every manual News tap + get_next_high_impact_event_date
        # below), which blew through that cap easily and caused silent
        # empty-result failures - confirmed real, not hypothetical.
        data = get_cached_calendar_data()
        if not data:
            return []

        events = []
        for event in data:
            event_date = event.get("date", "")[:10]
            if event_date != today_str:
                continue
            if event.get("impact", "").lower() != "high":
                continue
            currency = event.get("country", "")
            if currency not in CURRENCY_PAIR_MAP:
                continue

            time_utc = event.get("date", "")
            time_str = ""
            event_dt_utc = ""
            if time_utc and "T" in time_utc:
                try:
                    # The feed's date field embeds its own offset (e.g.
                    # "...-04:00" for US Eastern) - it is NOT already UTC.
                    # fromisoformat respects that embedded offset, unlike
                    # the previous strptime-on-truncated-string approach
                    # which silently treated the raw Eastern hour as UTC.
                    dt_parsed = datetime.fromisoformat(time_utc)
                    if dt_parsed.tzinfo is not None:
                        dt_utc = dt_parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        dt_utc = dt_parsed  # no offset present - assume already UTC
                    event_dt_utc = dt_utc.strftime("%Y-%m-%dT%H:%M")
                    lagos_dt = dt_utc + timedelta(hours=1)  # WAT = UTC+1 year-round, no DST
                    time_str = f"{lagos_dt.hour:02d}:{lagos_dt.minute:02d}"
                except Exception:
                    time_str = ""

            events.append({
                "title": event.get("title", ""),
                "currency": currency,
                "time_str": time_str,
                "forecast": event.get("forecast", ""),
                "previous": event.get("previous", ""),
                "actual": event.get("actual", ""),
                "event_dt_utc": event_dt_utc,  # TRUE UTC timestamp now, for the 30-min-before notification check
                "event_key": f"{today_str}_{currency}_{event.get('title', '')}",
            })

        return events[:8]  # a reasonable cap on how many buttons to show

    except Exception as e:
        print(f"[NEWS CALL] Error fetching events: {e}")
        return []


def format_event_status(event, now_utc=None):
    """
    Returns (status_label, is_released) for a single event - "✅
    Released — Act: X" if its time has already passed (showing the
    real actual figure if the feed has posted one yet), or "🔜 in Xh
    Ym" / "🔜 in Ym" counting down to release if it's still ahead.
    Used by the News Calendar list so closed and still-open events
    are visually distinct.
    """
    now_utc = now_utc or datetime.utcnow()
    event_dt_str = event.get("event_dt_utc", "")
    if not event_dt_str:
        return "", False
    try:
        event_dt = datetime.strptime(event_dt_str, "%Y-%m-%dT%H:%M")
    except Exception:
        return "", False

    if event_dt <= now_utc:
        actual = event.get("actual", "")
        label = f"✅ Released — Act: {actual}" if actual else "✅ Released"
        return label, True

    total_minutes = int((event_dt - now_utc).total_seconds() // 60)
    hours, minutes = divmod(max(total_minutes, 0), 60)
    countdown = f"in {hours}h {minutes}m" if hours > 0 else f"in {minutes}m"
    return f"🔜 {countdown}", False


def get_next_high_impact_event_date():
    """
    Scans forward past today, within Forex Factory's "this week" feed
    (the only endpoint of theirs confirmed to actually exist and work -
    there is no working "next week" JSON feed, despite an earlier
    attempt to use one), for the next date with at least one
    USD/EUR/GBP/JPY high-impact event. Used once today's high-impact
    calendar is fully done, so a user isn't left guessing when to
    check back in. Returns None if nothing remains in the current
    week - the caller is expected to phrase that honestly (e.g. "check
    back next week") rather than implying a specific date was checked
    and confirmed empty beyond what this can actually see.
    """
    try:
        today = datetime.utcnow().date()
        candidate_dates = []

        feed_data = get_cached_calendar_data()
        if feed_data:
            for event in feed_data:
                if event.get("impact", "").lower() != "high":
                    continue
                if event.get("country", "") not in CURRENCY_PAIR_MAP:
                    continue
                date_field = event.get("date", "")
                if not date_field:
                    continue
                try:
                    dt_parsed = datetime.fromisoformat(date_field)
                    if dt_parsed.tzinfo is not None:
                        dt_utc = dt_parsed.astimezone(timezone.utc).replace(tzinfo=None)
                    else:
                        dt_utc = dt_parsed
                except Exception:
                    continue
                if dt_utc.date() > today:
                    candidate_dates.append(dt_utc.date())

        if not candidate_dates:
            return None
        return min(candidate_dates).strftime("%A, %d %B %Y")

    except Exception as e:
        print(f"[NEWS CALL] get_next_high_impact_event_date error: {e}")
        return None


# FIX: CONFIRMED REAL, PERMANENT ROOT CAUSE, now actually fixed -
# this used to be a plain in-memory dict, which meant ANY deploy/
# restart wiped it completely regardless of how good the expiry logic
# was. That's exactly what broke a live channel link during an actual
# NFP window - the fix at the time (switching count-based eviction to
# real time-based expiry) was real and correct, but incomplete: it
# couldn't survive a process restart, which happens on every code
# deploy. Now persisted to Supabase (news_events_store table) instead -
# a restart can no longer touch it at all. Retention is 24h (see
# NEWS_EVENTS_STORE_MAX_AGE_SECONDS), matching the news_event_
# reactions result-visibility window below, so the list itself is
# never the limiting factor before that separate, business-logic
# window kicks in.
NEWS_EVENTS_STORE_MAX_AGE_SECONDS = 24 * 3600


def store_news_events_batch(events):
    """Stores a batch of events under a fresh list_id and returns that id."""
    list_id = str(int(time.time() * 1000))
    try:
        url = f"{SUPABASE_URL}/rest/v1/news_events_store"
        requests.post(url, headers=sb_headers(), json={"list_id": list_id, "events": events}, timeout=10)
    except Exception as e:
        print(f"[NEWS EVENTS STORE] Error storing batch {list_id}: {e}")

    # Prune anything past the retention window - runs on every store
    # call, so no separate cleanup job is needed.
    try:
        cutoff = (datetime.utcnow() - timedelta(seconds=NEWS_EVENTS_STORE_MAX_AGE_SECONDS)).isoformat()
        prune_url = f"{SUPABASE_URL}/rest/v1/news_events_store?created_at=lt.{cutoff}"
        requests.delete(prune_url, headers=sb_headers(), timeout=10)
    except Exception as e:
        print(f"[NEWS EVENTS STORE] Error pruning old batches: {e}")

    return list_id


def get_news_events_batch(list_id):
    """
    Reads a stored batch back by list_id. Returns None if it doesn't
    exist or has aged past NEWS_EVENTS_STORE_MAX_AGE_SECONDS - the
    caller treats either case as "expired, show a fresh-list prompt".
    """
    try:
        cutoff = (datetime.utcnow() - timedelta(seconds=NEWS_EVENTS_STORE_MAX_AGE_SECONDS)).isoformat()
        url = f"{SUPABASE_URL}/rest/v1/news_events_store?list_id=eq.{list_id}&created_at=gte.{cutoff}&select=events"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        rows = response.json()
        if not rows:
            return None
        return rows[0]["events"]
    except Exception as e:
        print(f"[NEWS EVENTS STORE] Error reading batch {list_id}: {e}")
        return None


# How long a real, actual-grounded reaction stays visible when someone
# taps an event after it's released, per explicit instruction (asked
# for my view on going beyond 1h - people checking back later the same
# day or next morning to see how a call played out is a real, common
# use case, and this is just a display window on tap, not a broadcast/
# spam concern, so there's no real cost to being generous with it).
# Outside this window, tapping the event shows a plain "already
# released, direction call window has passed" notice instead.
NEWS_REACTION_VISIBLE_HOURS = 24


def record_news_event_reaction(event, direction, strength, reason):
    """
    Persists a computed post-release reaction, per explicit
    instruction - written once by check_released_high_impact_news
    right when it posts the broadcast, then read back by the tap
    handler (get_news_event_reaction) so tapping the SAME event later
    shows the identical real DIRECT CALL instead of re-running the AI,
    for up to NEWS_REACTION_VISIBLE_HOURS.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/news_event_reactions?on_conflict=event_key"
        headers = sb_headers()
        headers["Prefer"] = "resolution=merge-duplicates"
        payload = {
            "event_key": event["event_key"],
            "title": event["title"],
            "currency": event["currency"],
            "forecast": event.get("forecast", ""),
            "previous": event.get("previous", ""),
            "actual": event.get("actual", ""),
            "direction": direction,
            "strength": strength,
            "reason": reason,
            "actual_posted_at": datetime.utcnow().isoformat(),
        }
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"[NEWS EVENT REACTIONS] Error recording {event.get('event_key')}: {e}")


def get_news_event_reaction(event_key):
    """
    Looks up a persisted reaction by event_key. Returns None if none
    exists yet (actual hasn't appeared/been processed) - the tap
    handler shows the pre-release BIAS CALL in that case. If one
    exists, the caller compares actual_posted_at against
    NEWS_REACTION_VISIBLE_HOURS itself to decide between showing the
    real DIRECT CALL or a "window passed" notice.
    """
    try:
        url = f"{SUPABASE_URL}/rest/v1/news_event_reactions?event_key=eq.{event_key}&select=*"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        rows = response.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[NEWS EVENT REACTIONS] Error reading {event_key}: {e}")
        return None


# Tracks which events have already triggered their 30-minutes-before
# notification today, so the every-5-minutes check job doesn't send
# the same alert multiple times as it keeps re-scanning. Keyed by
# each event's own event_key (date+currency+title), so it naturally
# resets itself day to day without needing an explicit clear.
NOTIFIED_EVENTS_TODAY = set()

# Same idea, separate set - tracks which events have already had their
# POST-release actual-result reaction posted to channels, per explicit
# instruction. Deliberately separate from NOTIFIED_EVENTS_TODAY above -
# an event needs to fire the pre-release notification AND (later,
# independently) the post-release one, so one set marking "handled"
# for both would incorrectly suppress whichever came second.
RELEASED_NOTIFIED_EVENTS_TODAY = set()


def get_todays_calendar_events_fresh():
    """
    Same event list/shape as get_todays_high_impact_events, but
    bypasses the normal 1-hour cache (get_cached_calendar_data) -
    used ONLY by check_released_high_impact_news below, per explicit
    instruction ("post the actual result whenever we get it,
    regardless of feed delay"). A 1-hour-stale cache would mean up to
    an hour's delay on top of whatever the feed itself takes, which
    defeats the purpose of a dedicated release-detection job. This
    fetches directly AND updates the shared cache with what it gets,
    so other callers within that window opportunistically benefit
    from fresher data too, rather than spending a second, separate
    request. Runs every 5 minutes (see job registration), which is
    exactly this feed's own documented rate budget (2 requests/5min
    total, across every caller combined) - this job alone uses 1 of
    those 2, leaving headroom for everything else that touches the
    same feed.
    """
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=10)
        data = response.json()
        calendar_data_cache["data"] = data
        calendar_data_cache["timestamp"] = time.time()
    except Exception as e:
        print(f"[NEWS RELEASE CHECK] Fresh calendar fetch error: {e}")
        data = calendar_data_cache["data"] or []

    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    events = []
    for event in data:
        event_date = event.get("date", "")[:10]
        if event_date != today_str:
            continue
        if event.get("impact", "").lower() != "high":
            continue
        currency = event.get("country", "")
        if currency not in CURRENCY_PAIR_MAP:
            continue
        events.append({
            "title": event.get("title", ""),
            "currency": currency,
            "forecast": event.get("forecast", ""),
            "previous": event.get("previous", ""),
            "actual": event.get("actual", ""),
            "event_dt_utc": event.get("date", ""),
            "event_key": f"{today_str}_{currency}_{event.get('title', '')}",
        })
    return events


async def generate_pre_release_bias(event, news_context):
    """
    Pre-release channel bias, per explicit instruction: grounded in
    real Forecast/Previous numbers AND real fetched news context (a
    genuine recent article, same source the regular briefings use -
    NOT the AI's own free-associated "geopolitical awareness", which
    would just be a second version of the exact fabrication problem
    already found and fixed once). If no real article is available
    this round, news_context is empty and the AI is told to reason
    from the calendar numbers alone rather than invent context to
    fill the gap.

    Same hard rule as generate_currency_direction: this always runs
    BEFORE release, so it must never state or imply a specific actual
    figure.
    """
    forecast_line = f"Forecast: {event['forecast']}" if event.get("forecast") else ""
    previous_line = f"Previous: {event['previous']}" if event.get("previous") else ""
    data_lines = "\n".join(l for l in [forecast_line, previous_line] if l)

    news_block = (
        f"\nRELEVANT REAL NEWS CONTEXT (from a real, recently fetched article - "
        f"use only if genuinely relevant to this event/currency, ignore otherwise):\n"
        f"\"{news_context}\"\n"
        if news_context else ""
    )

    prompt = f"""
You are a forex fundamental analyst previewing a high-impact event
BEFORE it releases, for a Telegram trading channel.

EVENT: {event['title']}
CURRENCY: {event['currency']}
{data_lines}
{news_block}
This event has NOT released yet - there is no actual result. Do not
state, imply, or invent one. Judge only whether the SETUP (forecast
vs previous, plus the real news context if relevant) leans BULLISH or
BEARISH for {event['currency']}, framed as anticipation only (e.g.
"a forecast above the previous reading would signal...").

Respond in EXACTLY this format, nothing else, no markdown:
DIRECTION: BULLISH or BEARISH
REASON: [one sentence, max 22 words, plain and beginner-friendly, anticipatory framing only]
"""
    try:
        result = await ask_gemini(prompt)
        if result.strip() in KNOWN_AI_FAILURE_STRINGS:
            return None, None
        direction_match = re.search(r"DIRECTION:\s*(BULLISH|BEARISH)", result, re.IGNORECASE)
        reason_match = re.search(r"REASON:\s*(.+)", result)
        if not direction_match:
            print(f"[NEWS PRE-RELEASE] AI responded but format didn't match - raw: {result!r}")
            return None, None
        return direction_match.group(1).upper(), (reason_match.group(1).strip() if reason_match else "")
    except Exception as e:
        print(f"[NEWS PRE-RELEASE] AI call failed: {e}")
        return None, None


async def generate_actual_result_reaction(event):
    """
    Post-release channel reaction, per explicit instruction: only
    called once event['actual'] is genuinely populated (real data,
    not invented). Judges direction from the real actual vs forecast/
    previous comparison - the exact thing generate_currency_direction
    and generate_pre_release_bias are explicitly forbidden from doing,
    now safe because the number is real.
    """
    forecast_line = f"Forecast: {event['forecast']}" if event.get("forecast") else ""
    previous_line = f"Previous: {event['previous']}" if event.get("previous") else ""
    actual_line = f"Actual: {event['actual']}"
    data_lines = "\n".join(l for l in [actual_line, forecast_line, previous_line] if l)

    prompt = f"""
You are a forex fundamental analyst. This event has JUST been
released with a real actual result - judge whether it's BULLISH or
BEARISH for its own currency based on the real Actual vs Forecast/
Previous comparison below.

Also judge HOW STRONGLY the actual result favors that direction, as a
percentage between 51 and 95 - close to 51 for a narrow beat/miss,
closer to 95 for a large, unambiguous surprise vs forecast.

EVENT: {event['title']}
CURRENCY: {event['currency']}
{data_lines}

Respond in EXACTLY this format, nothing else, no markdown:
DIRECTION: BULLISH or BEARISH
STRENGTH: [a number 51-95]
REASON: [one sentence, max 25 words, explicitly citing the real Actual vs Forecast/Previous numbers above. Plain and beginner-friendly.]
"""
    try:
        result = await ask_gemini(prompt)
        if result.strip() in KNOWN_AI_FAILURE_STRINGS:
            return None, None, None
        direction_match = re.search(r"DIRECTION:\s*(BULLISH|BEARISH)", result, re.IGNORECASE)
        strength_match = re.search(r"STRENGTH:\s*(\d+)", result)
        reason_match = re.search(r"REASON:\s*(.+)", result)
        if not direction_match:
            print(f"[NEWS RELEASE REACTION] AI responded but format didn't match - raw: {result!r}")
            return None, None, None
        direction = direction_match.group(1).upper()
        strength_pct = min(95, max(51, int(strength_match.group(1)))) if strength_match else 65
        reason = reason_match.group(1).strip() if reason_match else ""
        return direction, strength_pct, reason
    except Exception as e:
        print(f"[NEWS RELEASE REACTION] AI call failed: {e}")
        return None, None, None


# Ranks well-known "headliner" events above the component data that
# routinely releases at the exact same time alongside them (e.g. NFP,
# Average Hourly Earnings, and Unemployment Rate all drop at 8:30am
# ET together every month) - per explicit instruction, used to pick
# ONE event to post a reaction for when several cluster at the same
# time and currency, instead of posting once per event for what's
# really one single market-moving moment. Lower index = higher
# priority. Matched by substring against the event's own title
# (case-insensitive), so it's tolerant of the feed's exact wording
# varying slightly. Anything not matching any entry here is treated
# as lowest priority (component/secondary data).
EVENT_HEADLINER_PRIORITY = [
    "non-farm employment change", "nonfarm payrolls", "nfp",
    "cpi", "consumer price index",
    "interest rate decision", "fed funds rate", "official bank rate", "monetary policy statement",
    "gdp",
    "core retail sales", "retail sales",
    "pce price index",
    "ism manufacturing pmi", "ism services pmi",
    "employment change",
]


def _headliner_rank(event_title):
    title_lower = event_title.lower()
    for i, keyword in enumerate(EVENT_HEADLINER_PRIORITY):
        if keyword in title_lower:
            return i
    return len(EVENT_HEADLINER_PRIORITY)  # lowest priority - no match


async def check_released_high_impact_news(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 5 minutes, per explicit instruction - posts a real,
    data-grounded BUY/SELL reaction to all 3 channels AND every known
    user's DM (so anyone who's muted or left a channel still gets it)
    the moment an event's actual result appears in the feed, whatever
    the real-world delay on that turns out to be (the feed itself,
    not this job's cadence, is the limiting factor - see
    get_todays_calendar_events_fresh's docstring).

    Groups events by (exact scheduled time, currency) first, per
    explicit instruction - several high-impact events routinely share
    one release moment (classic case: NFP, Average Hourly Earnings,
    and Unemployment Rate all at 8:30am ET) - posting a separate
    reaction for each is really 3 messages about one single moment.
    Only the highest-priority event in each group (EVENT_HEADLINER_
    PRIORITY) gets an actual reaction posted; the rest are marked
    handled alongside it without their own post, whether their own
    actual value has appeared yet or not - waiting on the group's
    headliner specifically is the whole point.
    """
    events = get_todays_calendar_events_fresh()
    if not events:
        print("[NEWS RELEASE REACTION] Checked - no high-impact USD/EUR/GBP/JPY events today")
        return

    bot = context.bot
    flag_by_currency = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵"}

    # Group by (currency, exact scheduled time) - same clustering the
    # pre-release alert already does visually by listing them
    # together, just applied here to decide what actually gets posted.
    groups = {}
    for event in events:
        group_key = (event["currency"], event.get("event_dt_utc", ""))
        groups.setdefault(group_key, []).append(event)

    for group_key, group_events in groups.items():
        # Skip a group entirely if every event in it has already been
        # handled (posted or absorbed into a prior post) - avoids
        # redoing the ranking/lookup on every single 5-minute pass
        # once a group's done.
        if all(e["event_key"] in RELEASED_NOTIFIED_EVENTS_TODAY for e in group_events):
            continue

        headliner = min(group_events, key=lambda e: _headliner_rank(e["title"]))
        if not headliner.get("actual"):
            # FIX: this used to be a silent skip with zero logging -
            # made a real diagnosis impossible when a subscriber asked
            # why NFP's reaction never posted (couldn't tell "checked,
            # actual not in feed yet" apart from "never ran at all").
            print(f"[NEWS RELEASE REACTION] {headliner['title']} ({headliner['currency']}) checked - actual not in feed yet")
            continue  # the group's headliner hasn't released yet - wait for it specifically

        # Mark EVERY event in this group as handled together, whether
        # or not this is the headliner - the non-headliner ones never
        # get their own post, regardless of whether their own actual
        # value shows up before, at the same time as, or after the
        # headliner's.
        for e in group_events:
            RELEASED_NOTIFIED_EVENTS_TODAY.add(e["event_key"])

        event = headliner
        ai_direction, ai_strength, ai_reason = await generate_actual_result_reaction(event)
        if not ai_direction:
            print(f"[NEWS RELEASE REACTION] Couldn't get a read for {event['title']} - skipping this group's post")
            continue

        mapping = CURRENCY_PAIR_MAP[event["currency"]]
        pair_key = mapping["pair_key"]
        inverted = mapping["inverted"]
        final_direction = "BUY" if (ai_direction == "BULLISH") != inverted else "SELL"
        pair_display = PAIR_CONFIG[pair_key]["display"]
        emoji = "🟢" if final_direction == "BUY" else "🔴"
        flag = flag_by_currency.get(event["currency"], "🌍")

        record_news_event_reaction(event, final_direction, ai_strength, ai_reason)

        data_parts = []
        if event.get("actual"):
            data_parts.append(f"Actual: {event['actual']}")
        if event.get("forecast"):
            data_parts.append(f"Forecast: {event['forecast']}")
        if event.get("previous"):
            data_parts.append(f"Previous: {event['previous']}")
        data_line = " | ".join(data_parts)

        # Note when this represents a cluster, so readers understand
        # why (say) Average Hourly Earnings and Unemployment Rate
        # aren't getting their own separate posts today.
        other_titles = [e["title"] for e in group_events if e is not headliner]
        cluster_note = (
            f"\n<i>Also released at the same time: {', '.join(other_titles)}</i>"
            if other_titles else ""
        )

        # No per-user personalization needed here (unlike the pre-
        # release alert) - this is an immediate "just released"
        # reaction with no future event time to convert per timezone,
        # so channel and DM copy are identical.
        text = (
            f"🚨 {flag} <b>{event['title']} — JUST RELEASED</b>\n\n"
            + (f"📊 {data_line}\n\n" if data_line else "")
            + f"{ai_reason}\n\n"
            f"{emoji} <b>DIRECT CALL: {final_direction} {pair_display}</b>\n"
            f"<b>Confidence:</b> {ai_strength}%"
            f"{cluster_note}\n\n"
            f"<i>Trade safe 💼🔥</i>"
        )

        for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
            try:
                await bot.send_message(chat_id=channel_id, text=text, parse_mode=ParseMode.HTML)
            except Exception as e:
                print(f"[NEWS RELEASE REACTION] Channel post failed for {channel_id}: {e}")

        # Per-user DMs, per explicit instruction - reuses the exact
        # same text computed above, same safe ~20/sec pacing already
        # proven elsewhere for broadcasting to potentially many users.
        user_ids = await get_all_known_user_ids()
        sent = failed = 0
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=int(uid), text=text, parse_mode=ParseMode.HTML)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)

        print(f"[NEWS RELEASE REACTION] ✅ Posted {event['title']} ({event['currency']}) -> {final_direction} {pair_display} | DMs sent {sent}, failed {failed}")


async def generate_currency_direction(event):
    """
    Constrained AI call: judges ONLY whether this specific event's
    likely result is bullish or bearish for the event's OWN currency,
    with a strength percentage and one sentence of reasoning. Does NOT
    pick a pair or a BUY/SELL call itself - see the module docstring
    above for why that mechanical step is deliberately kept in code,
    not left to the AI.
    Returns (direction, strength_pct, reasoning) where direction is
    "BULLISH" or "BEARISH" and strength_pct is an int 51-95 (how
    strongly the data favors that direction - capped below 100 since
    fundamentals are never a certainty, and above 50 since anything
    weaker wouldn't be worth calling a direction at all), or
    (None, None, None) on failure.
    """
    forecast_line = f"Forecast: {event['forecast']}" if event.get("forecast") else ""
    previous_line = f"Previous: {event['previous']}" if event.get("previous") else ""
    actual_line = f"Actual: {event['actual']}" if event.get("actual") else ""
    data_lines = "\n".join(l for l in [forecast_line, previous_line, actual_line] if l)

    # FIX: CONFIRMED REAL, SERIOUS BUG - this function is only ever
    # called on an event BEFORE it releases (send_news_direction_analysis
    # gates on is_released and shows a plain notice instead of calling
    # this for anything already out), so event['actual'] is ALWAYS
    # empty in every real, legitimate call - actual_line above is
    # effectively dead code, always "". The old prompt's REASON
    # instruction said "explicitly reference the actual Forecast/
    # Previous/Actual numbers" regardless - telling the model to cite
    # a real "Actual" figure that can NEVER exist at call time. Live
    # result, confirmed directly: the AI fabricated a specific,
    # plausible-sounding "actual result of 114K" out of nothing and
    # stated it as settled fact, for an event hours from releasing.
    # That's exactly the kind of invented-numbers-presented-as-real
    # harm this whole system is built to avoid - a real, not
    # hypothetical, instance of it reaching a subscriber.
    #
    # Fixed with an explicit, unambiguous instruction never to state
    # or imply a specific actual result, and anticipatory framing
    # instead (what a beat/miss vs forecast WOULD mean, not what
    # already happened).
    prompt = f"""
You are a forex fundamental analyst. Judge ONLY whether this news
event is likely BULLISH or BEARISH for its own currency - nothing
else, no pair, no trade call.

Also judge HOW STRONGLY the data favors that direction, as a
percentage between 51 and 95 - use a number close to 51 when the data
is only mildly one-sided (e.g. forecast barely above previous), and
closer to 95 only when the data implies a large, unambiguous move
versus previous. Never output 50 or below (that wouldn't be a
direction at all) and never output 100 (fundamentals are never a
certainty).

EVENT: {event['title']}
CURRENCY: {event['currency']}
{data_lines}

This event has NOT been released yet - there is no real "Actual"
result available, regardless of what the data above shows. You MUST
NOT state, imply, or invent any specific actual/released figure (do
not write things like "the actual result of X" or "came in at X") -
that would be fabricating a real economic data point that has not
happened. Base your read ONLY on comparing Forecast against Previous,
framed as anticipation of the release (e.g. "a forecast above the
previous reading would signal..."), never as something that has
already occurred.

Respond in EXACTLY this format, nothing else, no markdown:
DIRECTION: BULLISH or BEARISH
STRENGTH: [a number 51-95]
REASON: [one sentence, max 25 words - reference the real Forecast/Previous numbers above anticipating the release, never an actual/released figure. Plain and beginner-friendly.]
"""
    try:
        # NO outer retry loop here (there used to be one, 15s + 30s
        # waits on top of ask_gemini's own retry) - removed per
        # explicit instruction after it turned out to be the actual
        # cause of "takes forever, then still fails". ask_gemini
        # ALREADY retries Gemini once internally AND falls back to a
        # completely different provider (OpenRouter) on failure - that
        # was already real resilience against a one-off blip. Stacking
        # a third retry layer on top of that doesn't add meaningful
        # protection against a PERSISTENT problem (wrong API key,
        # exhausted quota, real outage) - it just makes the user wait
        # up to ~6 minutes worst-case for the exact same eventual
        # failure. This is a foreground call someone is actively
        # waiting on, not a background job - fail fast here matters
        # more than squeezing out one more retry.
        result = await ask_gemini(prompt)
        if result.strip() in KNOWN_AI_FAILURE_STRINGS:
            print(f"[NEWS CALL] AI direction judgment failed ('{result.strip()}')")
            return None, None, None

        direction_match = re.search(r"DIRECTION:\s*(BULLISH|BEARISH)", result, re.IGNORECASE)
        strength_match = re.search(r"STRENGTH:\s*(\d+)", result)
        reason_match = re.search(r"REASON:\s*(.+)", result)
        if not direction_match:
            # This was silently returning None before, with NO log
            # line at all - meaning a real, distinct failure mode
            # (the AI answered, just not in the exact expected format)
            # left zero trace to diagnose from. Logging the raw
            # response now so the next occurrence is actually visible
            # in Railway's logs instead of indistinguishable from a
            # hard API failure.
            print(f"[NEWS CALL] AI responded but format didn't match - raw response: {result!r}")
            return None, None, None
        direction = direction_match.group(1).upper()
        # Clamp defensively even though the prompt constrains the
        # range - never trust a model to perfectly honor a numeric
        # bound, clamp rather than reject so a slightly-out-of-range
        # answer still displays sensibly instead of failing outright.
        strength_pct = min(95, max(51, int(strength_match.group(1)))) if strength_match else 65
        reason = reason_match.group(1).strip() if reason_match else ""
        return direction, strength_pct, reason
    except Exception as e:
        print(f"[NEWS CALL] AI direction judgment failed: {e}")
        return None, None, None


# Caches a successful analysis per event_key, so re-tapping "Know the
# Direction" on the SAME event re-displays the same result instantly
# instead of re-running the AI call. Per explicit instruction: a
# repeat tap was re-running the whole analysis every time, which not
# only wasted a call re-deriving an answer that can't have changed for
# the same event, but meant a second tap could fail even after the
# FIRST one already succeeded (e.g. hitting a rate limit the second
# time around, as seen live). Not persisted across restarts, and
# naturally bounded - only ever holds today's events, and there are
# never more than a handful of high-impact events on any given day.
NEWS_DIRECTION_CACHE = {}


async def send_news_direction_analysis(bot, chat_id, event, batch_events=None):
    """
    Shared by both entry points into this feature: the private-DM
    callback button (already in a 1:1 chat, so callback_data works
    fine) AND the channel deep-link button (see start()'s newsevent_
    branch below) - same fundamentals-first BUY/SELL call either way,
    just two different doors into it.

    By design, this bot only calls a direction BEFORE a news event
    releases - once it's out, the trade window it's meant for is
    already gone, so an already-released event gets no AI call at
    all, just a plain notice. batch_events (the full list this event
    came from, if available) lets that notice also say whether
    anything else in the same list is still upcoming, or otherwise
    surface the next known high-impact date so the user isn't left
    guessing when to check back.
    """
    now_utc = datetime.utcnow()

    # FIX: replaced the old scheduled-TIME-based gate (is_released,
    # from format_event_status) with a real actual-EXISTENCE-based
    # one, per explicit instruction. The old version showed "already
    # released" the instant the scheduled time passed, even if the
    # feed hadn't actually posted a real actual value yet - exactly
    # backwards from what should happen (no actual yet = still show
    # the pre-release BIAS CALL, regardless of whether the clock time
    # has technically passed). This also now shows the REAL, actual-
    # grounded DIRECT CALL (identical to what the channel/DM broadcast
    # already posted - see record_news_event_reaction) for
    # NEWS_REACTION_VISIBLE_HOURS after it releases, instead of just a
    # plain "already released" notice with no real content at all.
    event_key = event.get("event_key")
    reaction = get_news_event_reaction(event_key) if event_key else None

    if reaction:
        posted_at = datetime.fromisoformat(reaction["actual_posted_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        hours_since = (now_utc - posted_at).total_seconds() / 3600

        if hours_since <= NEWS_REACTION_VISIBLE_HOURS:
            mapping = CURRENCY_PAIR_MAP[reaction["currency"]]
            pair_display = PAIR_CONFIG[mapping["pair_key"]]["display"]
            emoji = "🟢" if reaction["direction"] == "BUY" else "🔴"
            data_parts = []
            if reaction.get("actual"):
                data_parts.append(f"Actual: {reaction['actual']}")
            if reaction.get("forecast"):
                data_parts.append(f"Forecast: {reaction['forecast']}")
            if reaction.get("previous"):
                data_parts.append(f"Previous: {reaction['previous']}")
            data_line = " | ".join(data_parts)
            text = (
                f"📰 <b>{reaction['title']}</b> ({reaction['currency']})\n\n"
                + (f"📊 {data_line}\n\n" if data_line else "")
                + f"{reaction['reason']}\n\n"
                f"{emoji} <b>DIRECT CALL: {reaction['direction']} {pair_display}</b>\n"
                f"<b>Confidence:</b> {reaction['strength']}%\n\n"
                f"<i>Trade safe 💼🔥</i>"
            )
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
            return

        # Real result exists but the visibility window has passed.
        text = (
            f"📰 <b>{event['title']}</b> ({event['currency']})\n\n"
            f"✅ This event released more than {NEWS_REACTION_VISIBLE_HOURS} hours ago "
            f"and its call window has passed.\n\n"
        )
        has_other_upcoming = False
        if batch_events:
            has_other_upcoming = any(
                not get_news_event_reaction(e.get("event_key")) for e in batch_events if e.get("event_key")
            )
        if has_other_upcoming:
            text += "Tap another event above that hasn't released yet."
        else:
            next_date = get_next_high_impact_event_date()
            if next_date:
                text += f"No more high-impact events left today. Next one: <b>{next_date}</b>."
            else:
                text += "No more high-impact events left in this week's calendar — check back next week."
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        return

    if event_key and event_key in NEWS_DIRECTION_CACHE:
        await bot.send_message(chat_id=chat_id, text=NEWS_DIRECTION_CACHE[event_key], parse_mode=ParseMode.HTML)
        return

    wait_message = await bot.send_message(
        chat_id=chat_id,
        text="🧠 <b>Nexora AI reading the news...</b>",
        parse_mode=ParseMode.HTML
    )

    ai_direction, ai_strength, ai_reason = await generate_currency_direction(event)
    if not ai_direction:
        await wait_message.edit_text(
            "⚠️ Couldn't get a clear read on this event right now - try again shortly.",
            parse_mode=ParseMode.HTML
        )
        return

    mapping = CURRENCY_PAIR_MAP[event["currency"]]
    pair_key = mapping["pair_key"]
    inverted = mapping["inverted"]
    final_direction = "BUY" if (ai_direction == "BULLISH") != inverted else "SELL"
    pair_config = PAIR_CONFIG[pair_key]
    pair_display = pair_config["display"]

    # Real Forecast/Previous/Actual figures, per explicit instruction -
    # shows the actual data behind the call instead of just a
    # qualitative statement.
    data_parts = []
    if event.get("forecast"):
        data_parts.append(f"Forecast: {event['forecast']}")
    if event.get("previous"):
        data_parts.append(f"Previous: {event['previous']}")
    if event.get("actual"):
        data_parts.append(f"Actual: {event['actual']}")
    data_line = " | ".join(data_parts)

    # Technical cross-check REMOVED entirely, per explicit instruction -
    # it could (and did) contradict the fundamental call outright
    # ("Technicals read SELL" right under "BIAS CALL: BUY"), which
    # read as confusing and undermined the whole point of a direct
    # call. Confidence is now the AI's own fundamental strength
    # percentage (see generate_currency_direction) - one clear number,
    # not two that can disagree with each other.
    # Renamed DIRECT CALL -> BIAS CALL, per explicit instruction -
    # this fires before the event releases (no real actual figure
    # exists yet, enforced in generate_currency_direction's own
    # prompt), so "Direct Call" implied a certainty this doesn't
    # actually have. The POST-release reaction (check_released_
    # high_impact_news) correctly keeps "DIRECT CALL" - that one IS
    # grounded in a real, confirmed actual result.
    emoji = "🟢" if final_direction == "BUY" else "🔴"
    response = (
        f"📰 <b>{event['title']}</b> ({event['currency']})\n\n"
        + (f"📊 {data_line}\n\n" if data_line else "")
        + f"<b>Fundamental read:</b> {ai_reason}\n\n"
        f"{emoji} <b>BIAS CALL: {final_direction} {pair_display}</b>\n"
        f"<b>Confidence:</b> {ai_strength}%\n\n"
        f"<i>Trade safe 💼🔥</i>"
    )

    if event_key:
        NEWS_DIRECTION_CACHE[event_key] = response

    await wait_message.edit_text(response, parse_mode=ParseMode.HTML)
    schedule_auto_delete(wait_message.chat_id, wait_message.message_id)


async def check_upcoming_high_impact_news(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every 5 minutes. Fires ONE notification per day, ~30 minutes
    before the FIRST high-impact USD/EUR/GBP/JPY event, bundling
    EVERY remaining event for today into that single message - per
    explicit instruction, to cut this down to one notification a day
    instead of one per event (or one per group of same-time events,
    which is what this used to do). Each event still gets its own
    "Know the Direction" button, since each needs its own fundamental
    read - only the surrounding alert message and image are shared.
    Once this fires, every event it listed is marked notified, so none
    of them trigger anything again later that day, however far apart
    their actual times are.

    Uses a 25-30 minute WINDOW on the EARLIEST unnotified event only
    (since this job checks every 5 minutes, an exact 30 would miss
    most days entirely), and NOTIFIED_EVENTS_TODAY guards against
    re-firing as the job keeps re-scanning through that window.

    Reuses get_all_known_user_ids() + the same rate-limited send loop
    already proven safe in _run_broadcast, rather than inventing a
    new one - sending to potentially many users needs that same
    ~20/sec pacing to stay safely under Telegram's rate limits.
    """
    events = get_todays_high_impact_events()
    if not events:
        return
    list_id = store_news_events_batch(events)

    now = datetime.utcnow()
    bot = context.bot
    flag_by_currency = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵"}

    # Every event not yet notified today, with a valid parseable time.
    pending = []
    for idx, event in enumerate(events):
        if event["event_key"] in NOTIFIED_EVENTS_TODAY:
            continue
        raw_dt = event.get("event_dt_utc", "")
        if not raw_dt or "T" not in raw_dt:
            continue
        try:
            event_time = datetime.strptime(raw_dt[:16], "%Y-%m-%dT%H:%M")
        except Exception:
            continue
        pending.append((idx, event, event_time))

    if not pending:
        return

    # Only fire once the EARLIEST of these is 25-30 minutes out - at
    # that point, bundle ALL of them into one message, not just the
    # earliest one.
    pending.sort(key=lambda p: p[2])
    _, _, earliest_time = pending[0]
    minutes_until_earliest = (earliest_time - now).total_seconds() / 60
    if not (25 <= minutes_until_earliest <= 30):
        return

    group = [(idx, event) for idx, event, _ in pending]
    for _, event in group:
        NOTIFIED_EVENTS_TODAY.add(event["event_key"])

    header = "First high-impact event in ~30 minutes" if len(group) == 1 else f"First high-impact event in ~30 minutes — {len(group)} today"

    first_event = group[0][1]
    image_prompt = (
        f"professional financial news illustration: {first_event['currency']} "
        f"{first_event['title']}, cinematic digital art, dramatic lighting, high quality"
    )
    image_url = (
        f"https://image.pollinations.ai/prompt/"
        f"{requests.utils.quote(image_prompt)}"
        f"?width=800&height=450&nologo=true"
    )

    # Channel copy uses url= deep links, NOT callback_data - a
    # callback button on a channel post either fails silently for
    # anyone who hasn't DM'd the bot yet, or replies publicly into
    # the channel itself for whoever taps it, neither of which is
    # "take them privately into the bot to see their own call".
    # Opening the deep link IS the user starting/continuing their
    # own private DM, handled in start()'s newsevent_ branch.
    channel_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔍 {event['title']} ({event['currency']})",
            url=f"https://t.me/{BOT_USERNAME}?start=newsevent_{list_id}_{idx}"
        )]
        for idx, event in group
    ])
    # Per-user DM copy stays on callback_data - it's already inside
    # a private 1:1 chat, so there's no "channel spam" or "blocked
    # DM" risk to route around here.
    dm_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"🔍 {event['title']} ({event['currency']})",
            callback_data=f"newsevent_{list_id}_{idx}"
        )]
        for idx, event in group
    ])

    # Real news context, fetched ONCE for this whole batch (not per
    # event) - same source the regular briefings already use, per
    # explicit instruction ("use real life news not just AI"). A
    # single shared article is enough context for the AI to draw on
    # if genuinely relevant; the prompt itself tells it to ignore this
    # context entirely if it doesn't actually relate to a given event.
    grounding_article = fetch_market_news()
    news_context = ""
    if grounding_article:
        news_context = f"{grounding_article.get('title', '')}. {grounding_article.get('description', '')}".strip()

    # Per-event real bias - forecast/previous + real news context,
    # never a fabricated actual figure (generate_pre_release_bias
    # enforces this in its own prompt). Computed ONCE per event here,
    # then reused for both the channel post AND every user's DM below -
    # calling the AI once per recipient instead would be wasteful and
    # slow, and the bias itself doesn't vary by user, only the
    # displayed TIME does.
    event_bias_results = []  # (event, direction_or_None, reason_or_None)
    for _, event in group:
        direction, reason = await generate_pre_release_bias(event, news_context)
        event_bias_results.append((event, direction, reason))

    def _format_event_bias_line(event, direction, reason, offset_minutes, tz_label):
        flag = flag_by_currency.get(event["currency"], "🌍")
        time_str = f"{format_local_time(event.get('event_dt_utc', ''), offset_minutes)} {tz_label}"
        header_line = f"{flag} <b>{event['title']}</b> ({event['currency']}) — {time_str}"
        if direction and reason:
            lean_emoji = "🟢" if direction == "BULLISH" else "🔴"
            return f"{header_line}\n{lean_emoji} {reason}"
        # AI call failed for this one event - still show it with its
        # real time, just without a bias line, rather than dropping
        # it from the alert entirely.
        return header_line

    channel_alert_text = (
        f"⏰ <b>{header}</b>\n\n"
        + "\n\n".join(
            _format_event_bias_line(event, direction, reason, DEFAULT_UTC_OFFSET_MINUTES, "GMT+1")
            for event, direction, reason in event_bias_results
        )
        + f"\n\nWe'll post the real BUY/SELL call the moment actual results are out."
    )

    print(f"[NEWS ALERT] Notifying for {len(group)} event(s) today, first at {earliest_time}: {[e['title'] for _, e in group]}")

    # Per explicit instruction: both channels AND every known user's
    # DM, so anyone who's muted or left a channel still gets it.
    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
        try:
            await bot.send_photo(
                chat_id=channel_id, photo=image_url, caption=channel_alert_text,
                parse_mode=ParseMode.HTML, reply_markup=channel_markup
            )
        except Exception as e:
            print(f"[NEWS ALERT] Channel post failed for {channel_id}: {e}")

    # Per-user DMs, restored per explicit instruction. Each user's own
    # saved offset (falling back to WAT for anyone who never set one)
    # is looked up once here in bulk, rather than one DB round-trip
    # per recipient inside the send loop. Reuses the SAME AI results
    # computed above - only the time formatting changes per user.
    user_offsets = get_all_user_utc_offsets()
    user_ids = await get_all_known_user_ids()
    sent = failed = 0
    for uid in user_ids:
        try:
            offset_minutes = user_offsets.get(uid, DEFAULT_UTC_OFFSET_MINUTES)
            tz_label = format_gmt_label(offset_minutes)
            personal_alert_text = (
                f"⏰ <b>{header}</b>\n\n"
                + "\n\n".join(
                    _format_event_bias_line(event, direction, reason, offset_minutes, tz_label)
                    for event, direction, reason in event_bias_results
                )
                + f"\n\nWe'll post the real BUY/SELL call the moment actual results are out."
            )
            await bot.send_photo(
                chat_id=int(uid), photo=image_url, caption=personal_alert_text,
                parse_mode=ParseMode.HTML, reply_markup=dm_markup
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # ~20/sec, same safe pacing as _run_broadcast

    print(f"[NEWS ALERT] Done — sent {sent}, failed {failed}")

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
            currency = event.get("country", "")
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

def extract_and_strip_pairs_trailer(summary_text):
    """
    Pulls the internal "PAIRS: XAUUSD=SELL, USDJPY=SELL" trailer line
    off the end of a generated news summary, returning (display_text,
    pairs_dict) - display_text has the trailer removed entirely (it
    was never meant for subscribers), pairs_dict maps pair_name ->
    (direction, matching_bullet_text). matching_bullet_text is
    whichever of the (up to 2) bullet lines actually mentions that
    pair, so a later signal quotes ONLY the relevant line instead of
    the whole briefing - falls back to the full display text if no
    single bullet can be matched.

    FIX: the PAIRS: trailer was never actually showing up in practice
    (confirmed live - zero rows ever landed in daily_news_bias despite
    briefings clearly naming pairs in their bullets), and failed
    completely silently since this was pure text parsing with no
    exception to catch. Now falls back to reading the pair + direction
    straight off each bullet's own actionable-read phrase ("favors
    buying/selling X") when the trailer line isn't found - the prompt
    already requires every bullet to end with exactly that phrase, so
    this is arguably the more reliable source anyway, not just a
    backup. Logs which path produced the result (or that neither did)
    so a future miss is diagnosable from Railway logs instead of
    invisible like this one was.
    """
    if not summary_text:
        return summary_text, {}

    lines = summary_text.strip().split("\n")
    raw_pairs = {}
    kept_lines = []
    known_pairs = {
        "XAUUSD", "BTCUSD", "GBPUSD", "GBPJPY", "EURUSD",
        "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    }
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("PAIRS:") or stripped.upper().startswith("PAIRS "):
            body = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            if body.upper() != "NONE":
                for entry in body.split(","):
                    entry = entry.strip()
                    if "=" not in entry:
                        continue
                    pair, direction = entry.split("=", 1)
                    pair = pair.strip().upper()
                    direction = direction.strip().upper()
                    if pair in known_pairs and direction in ("BUY", "SELL"):
                        raw_pairs[pair] = direction
            continue  # never keep the trailer line in the displayed text
        kept_lines.append(line)

    display_text = "\n".join(kept_lines).strip()
    bullets = [b.strip() for b in display_text.split("🔹") if b.strip()]

    # Loose keyword match per pair - good enough to pick the right
    # one of only 2 bullets; falls back to the whole text if neither
    # bullet contains a recognizable mention of that pair.
    keyword_map = {
        "XAUUSD": ["gold", "xau"],
        "BTCUSD": ["btc", "bitcoin"],
        "GBPUSD": ["gbp/usd", "gbpusd", "pound"],
        "GBPJPY": ["gbp/jpy", "gbpjpy"],
        "EURUSD": ["eur/usd", "eurusd", "euro"],
        "USDJPY": ["usd/jpy", "usdjpy", "yen"],
        "AUDUSD": ["aud/usd", "audusd", "aussie"],
        "USDCAD": ["usd/cad", "usdcad", "loonie"],
        "USDCHF": ["usd/chf", "usdchf", "franc"],
        "NZDUSD": ["nzd/usd", "nzdusd", "kiwi"],
    }

    source = "trailer"
    if not raw_pairs:
        # Fallback: read directly off each bullet's own actionable
        # read - "favors buying X" / "favors selling X" (also accepts
        # "favor" without the s, and "may see upside/downside" as a
        # softer phrasing the prompt's examples also use).
        source = "bullet-fallback"
        for bullet in bullets:
            lower = bullet.lower()
            for pair, keywords in keyword_map.items():
                if not any(kw in lower for kw in keywords):
                    continue
                if "favors selling" in lower or "favor selling" in lower or "downside" in lower:
                    raw_pairs[pair] = "SELL"
                elif "favors buying" in lower or "favor buying" in lower or "upside" in lower:
                    raw_pairs[pair] = "BUY"

    pairs = {}
    for pair, direction in raw_pairs.items():
        matching_bullet = next(
            (b for b in bullets if any(kw in b.lower() for kw in keyword_map.get(pair, []))),
            display_text  # fall back to the whole thing if no single bullet matches
        )
        pairs[pair] = (direction, matching_bullet)

    if pairs:
        print(f"[NEWS BIAS] Extracted via {source}: {[(p, d) for p, (d, _) in pairs.items()]}")
    else:
        print(f"[NEWS BIAS] ⚠️ No pairs extracted from briefing (neither trailer nor bullet-fallback matched). Raw text: {summary_text[:300]!r}")

    return display_text, pairs


def save_daily_news_bias(pair_name, direction, headline_text, session_type):
    """
    Upserts today's briefing-derived bias for one pair - UNIQUE on
    (bias_date, pair_name), so a later same-day briefing mentioning
    the same pair again simply overwrites with the newer read rather
    than erroring, keeping only the most recent same-day take.
    """
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"{SUPABASE_URL}/rest/v1/daily_news_bias?on_conflict=bias_date,pair_name"
        headers = sb_headers()
        headers["Prefer"] = "resolution=merge-duplicates"
        payload = {
            "bias_date": today_str,
            "pair_name": pair_name,
            "direction": direction,
            "headline_text": headline_text,
            "session_type": session_type,
        }
        requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"[NEWS BIAS] Error saving bias for {pair_name}: {e}")


def get_todays_news_bias(pair_name):
    """
    Looks up whether TODAY's news briefing already gave a specific
    BUY/SELL read on this pair - the same-day source of truth a
    signal's own fundamental text should never contradict. Returns
    the row dict, or None if nothing was recorded for this pair today.
    """
    try:
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        url = (
            f"{SUPABASE_URL}/rest/v1/daily_news_bias"
            f"?bias_date=eq.{today_str}&pair_name=eq.{pair_name}&select=*&limit=1"
        )
        response = requests.get(url, headers=sb_headers(), timeout=10)
        rows = response.json()
        return rows[0] if rows else None
    except Exception as e:
        print(f"[NEWS BIAS] Error fetching bias for {pair_name}: {e}")
        return None


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
Write a VERY SHORT market news post for a Telegram trading channel of
forex, gold, and Bitcoin traders. They only care about instruments
they can actually trade - never a minor/exotic currency, never a
vague phrase like "global markets".

SESSION: {session_label}
NEWS HEADLINE: {title}
NEWS DETAILS: {description}
SOURCE: {source}

FORMAT EXACTLY LIKE THIS — NO EXCEPTIONS:
{session_label}

🔹 [One line news item 1] — [Bullish/Bearish] for [instrument], [direct BUY/SELL read]

🔹 [One line news item 2] — [Bullish/Bearish] for [instrument], [direct BUY/SELL read]

PAIRS: [comma-separated list, e.g. XAUUSD=SELL, USDJPY=SELL]

STRICT RULES:
- Maximum 2 bullet points ONLY
- Each bullet point MAX 25 words INCLUDING the sentiment tag and BUY/SELL read
- [instrument] MUST be one of exactly these - never anything else,
  never a minor/exotic currency (e.g. Rupee, Rand, Lira, Naira),
  never a vague phrase like "global markets" or "risk sentiment":
  the Dollar, EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD,
  NZD/USD, Gold (XAU/USD), BTC/USD
- Every bullet must end with a direct actionable read a trader can
  act on immediately, e.g. "— Bullish for the Dollar, favors selling
  Gold here" or "— Bearish for the Dollar, BTC/USD may see upside"
- State Bullish/Bearish based on what the news itself implies, not a
  guess - only use Neutral if truly no directional read is possible
  for one of the instruments above, and even then still name which
  instrument it's neutral for
- If the news doesn't clearly relate to ANY of the instruments listed
  above, respond with exactly: SKIP
- No long sentences, no paragraphs
- No markdown symbols like ** or ##
- No hashtags
- Make each point punchy, direct, and immediately actionable
- The PAIRS line is INTERNAL ONLY (stripped before posting, never
  shown to subscribers) - it exists so a later trading signal on the
  same pair, same day, can check its own direction against what this
  briefing already told subscribers, instead of contradicting it.
  Only include a pair here if a bullet gave it a real, specific,
  actionable BUY or SELL read (not just "the Dollar" alone, and not
  a Neutral read) - use ONLY these exact tickers, matching a bullet
  you actually wrote: XAUUSD, BTCUSD, GBPUSD, GBPJPY, EURUSD, USDJPY,
  AUDUSD, USDCAD, USDCHF, NZDUSD. If neither bullet maps to any of
  these specific tickers, write exactly: PAIRS: NONE
"""
    return await ask_gemini(prompt)

# ============================================
# POST NEWS — CHANNEL 1 ONLY
# ============================================

async def post_news(context: ContextTypes.DEFAULT_TYPE):

    session_type = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')
    print(f"[NEWS] Posting {session_type} news at {now}")

    # Fetched EARLY and independently of the article pipeline below -
    # per explicit instruction, after finding that high-impact calendar
    # events (e.g. FOMC) were being silently lost any time the article
    # fetch failed or the AI judged the article irrelevant, EVEN THOUGH
    # fetch_economic_calendar() itself doesn't depend on any news
    # article or AI call at all - it's a separate, independent feed.
    # The old code only ever appended calendar text onto a SUCCESSFUL
    # article summary, so a bad article day meant a bad calendar day
    # too, with zero relationship between the two failures.
    calendar = fetch_economic_calendar()

    async def post_calendar_only(reason: str):
        """
        Per explicit instruction: this used to post a standalone
        calendar recap to the channels whenever the article pipeline
        below couldn't produce a real post. Removed - it was landing
        as a separate message right around/after the same events
        subscribers already got a dedicated 30-minutes-before alert
        for (check_upcoming_high_impact_news), making it pure
        duplication with no real use of its own. Now just logs why
        the main post didn't go out, so that's still visible in
        Railway's logs, without posting anything to the channels.
        """
        print(f"[NEWS] {reason} - skipping today's post (calendar-only fallback disabled per explicit instruction).")
        return

    article = fetch_market_news()

    # FIX: retry the article fetch itself before giving up, per
    # explicit instruction after 3 separate real occurrences over 3
    # days - CONFIRMED via live logs this is a genuinely different
    # failure point than the AI-summary retry already in place
    # (which only helps once an article IS found). All 3 providers
    # (GNews, TheNewsAPI, Alpha Vantage) failed together at the exact
    # scheduled 06:00 UTC moment each time, but the SAME chain
    # succeeded again on its own just ~2 minutes later in the same
    # logs - a real, brief, simultaneous collision (likely a
    # transient network blip at that shared moment), not any single
    # provider being genuinely down. Two retries, 60s apart, mirrors
    # that real observed recovery window rather than guessing.
    article_retry_waits = [60, 60]
    for attempt_number, wait_seconds in enumerate(article_retry_waits, start=2):
        if article is not None:
            break
        print(f"[NEWS] No article found from any source - waiting {wait_seconds}s then retrying (attempt {attempt_number}/3)...")
        await asyncio.sleep(wait_seconds)
        article = fetch_market_news()

    if article is None:
        print("[NEWS] No article found from any source after 3 attempts.")
        await post_calendar_only("no article found")
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

    # FIX: up to TWO retries before giving up entirely, per explicit
    # instruction. CONFIRMED LIKELY CAUSE, not just a random outage -
    # both real failures seen in logs landed within seconds of the
    # exact same daily 06:00 UTC trigger, on two separate days. Per
    # Gemini's own published free-tier limits, Flash sits around 15
    # requests/minute (a ROLLING window, not a fixed clock minute) -
    # this bot also fires other Gemini calls around the same moments
    # (AI bias for scheduled forex/crypto signals, DM signal bias,
    # breakdowns), so a burst of concurrent activity right at the
    # scheduled slot can plausibly exhaust the RPM window, not the
    # (much later, 08:00 UTC) daily quota. ask_gemini's own existing
    # 429 retry is only a 10s gap - not always enough for a genuinely
    # busy RPM window to clear. Waiting longer between attempts here
    # (90s, then 180s) gives real additional time for that window to
    # reset, rather than repeating into the same still-busy window.
    # Still hard-capped at 2 retries (3 attempts total) - if all three
    # fail, this falls through to the existing skip-the-post behavior
    # below exactly as before, so a genuinely sustained outage still
    # fails safely rather than retrying forever.
    retry_waits = [90, 180]
    for attempt_number, wait_seconds in enumerate(retry_waits, start=2):
        if summary.strip() not in KNOWN_AI_FAILURE_STRINGS:
            break
        print(f"[NEWS] AI summary generation failed ('{summary.strip()}') - waiting {wait_seconds}s then retrying (attempt {attempt_number}/3)...")
        await asyncio.sleep(wait_seconds)
        summary = await generate_news_summary(article, session_type)
        summary = clean_text(summary)

    # Skip posting entirely if every retry above ALSO failed - a
    # failed AI call should behave the same way a failed news fetch
    # already does (see "if article is None: return" earlier), never
    # publish a placeholder/error string as if it were real market
    # commentary. Only reached after all retries above have already
    # had their chance, so this is the genuine "still broken after
    # trying three times, over several minutes" case, not a
    # first-attempt overreaction.
    if summary.strip() in KNOWN_AI_FAILURE_STRINGS:
        print(f"[NEWS] AI summary generation failed on all 3 attempts ('{summary.strip()}').")
        await post_calendar_only("AI summary failed")
        return

    # The prompt now returns exactly "SKIP" when an article passed the
    # keyword filter but still isn't genuinely about a major currency,
    # Gold, or BTC (e.g. an exotic-currency story where "usd"/"dollar"
    # only appeared incidentally) - per explicit instruction, this
    # must never get posted literally as if "SKIP" were real content.
    if summary.strip().upper() == "SKIP":
        print(f"[NEWS] AI judged article ('{article.get('title', '')}') not relevant to major FX/Gold/BTC.")
        await post_calendar_only("article judged not relevant")
        return

    # Pull out the internal PAIRS: trailer (never shown to
    # subscribers) and save each pair's read for today, so a later
    # signal on the same pair can check itself against what this
    # briefing already told the channel instead of contradicting it.
    summary, briefing_pairs = extract_and_strip_pairs_trailer(summary)
    for pair_name_key, (briefing_direction, bullet_text) in briefing_pairs.items():
        save_daily_news_bias(pair_name_key, briefing_direction, bullet_text, session_type)

    # Per explicit instruction (re-added, morning-only this time):
    # today's high-impact calendar events now get appended to the
    # MORNING briefing specifically, when there are any. This was
    # previously removed entirely ("it shouldn't post at all") - this
    # is a narrower re-add, not a full reversal: midday/afternoon
    # briefings still never include it, and mornings with no real
    # high-impact events today post exactly as before (calendar is
    # None in that case, nothing gets appended).
    if session_type == "morning" and calendar:
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

async def check_for_matching_recent_position(metaapi_account_id, mt5_symbol, comment, since_dt):
    """
    Real GET on this account's actual open positions (confirmed
    endpoint: /users/current/accounts/{id}/positions, returns
    Array<MetatraderPosition>) - looks for one matching this exact
    symbol and comment, opened at or after since_dt. Used specifically
    before retrying a timed-out trade placement, so a retry can safely
    confirm "did the prior attempt actually already succeed" instead
    of blindly resubmitting and risking a real duplicate order.

    Returns the matching position dict if found, None if the check
    ran successfully and genuinely found nothing, or the string
    "CHECK_FAILED" if the check itself couldn't run (network error,
    bad response) - per explicit instruction, the caller treats
    CHECK_FAILED as "stop, don't retry", not "assume no match and
    proceed". A duplicate real order is worse than a missed one, so
    when this safety check can't be trusted, the safe direction is to
    NOT resubmit, not to fall back to the old blind-retry behavior.
    """
    try:
        url = (
            f"https://mt-client-api-v1.london.agiliumtrade.ai"
            f"/users/current/accounts/{metaapi_account_id}/positions"
        )
        headers = {"auth-token": METAAPI_TOKEN, "Accept": "application/json"}
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"[MT5] Idempotency check failed to read positions: {response.status_code}")
            return "CHECK_FAILED"
        positions = response.json()
        target_symbol = mt5_symbol.upper()
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            if (pos.get("symbol") or "").upper() != target_symbol:
                continue
            if pos.get("comment") != comment:
                continue
            open_time_str = pos.get("time")
            if not open_time_str:
                continue
            try:
                open_time = datetime.fromisoformat(open_time_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                continue
            if open_time >= since_dt - timedelta(seconds=5):  # small buffer for clock drift
                return pos
        return None
    except Exception as e:
        print(f"[MT5] Idempotency check error: {e}")
        return "CHECK_FAILED"


async def place_mt5_trade(signal_data, signal_id=None):
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
        # Per explicit instruction (real gap found on review): a fixed
        # shared comment couldn't tell two DIFFERENT signals for the
        # SAME pair apart if they happened to fire within the same
        # few-second retry window - the idempotency check below could
        # in principle match the wrong signal's position to this one.
        # A per-signal comment closes this completely; falls back to
        # the old shared comment only if no signal_id was passed in
        # (keeps this function callable exactly as before from
        # anywhere that doesn't have one).
        trade_comment = f"NexoraAI #{signal_id}" if signal_id else "NexoraAI Signal"
        payload = {
            "symbol": mt5_symbol,
            "volume": 0.1,
            "actionType": order_type,
            "stopLoss": signal_data["stop_loss"],
            "takeProfit": signal_data["take_profit"],
            "comment": trade_comment
        }
        url = (
            f"https://mt-client-api-v1.london.agiliumtrade.ai"
            f"/users/current/accounts/{METAAPI_ACCOUNT_ID}/trade"
        )

        # FIX: confirmed live via Railway logs - a real trade silently
        # never made it to MT5 because MetaAPI returned 429 (rate
        # limited) at that exact moment, and this had zero retry -
        # one bad-timing moment meant the trade was gone for good,
        # with only a bare status code logged and no way to see why.
        # Retries transient failures (429 rate-limit, and 502/503/504
        # which are typically momentary upstream issues, not real
        # rejections) up to 2 extra times with a short backoff. A
        # genuine rejection (bad symbol, invalid volume, market
        # closed, insufficient margin, etc.) returns a 4xx that isn't
        # 429 and is NOT retried - retrying a real rejection would
        # just fail the same way 3 times instead of once.
        #
        # LONG-TERM FIX for the confirmed real duplicate-order
        # incident (3 real XAUUSD buys for one signal, seconds apart):
        # 504 was removed from the retry list entirely as an urgent
        # stopgap, since a 504 means the RESPONSE timed out, not that
        # the order failed to reach the broker - blindly resubmitting
        # risked a second/third REAL order on top of one that may have
        # already gone through. That stopgap traded resilience for
        # safety (a slow-but-genuine connectivity hiccup now just
        # fails outright, no retry at all). This replaces the stopgap
        # with the real fix: 504 is back in the retry list, but before
        # EVERY retry, check_for_matching_recent_position() confirms
        # via a fresh GET on this account's real open positions
        # whether the PRIOR attempt actually already placed this exact
        # trade (same symbol, same comment, opened within the retry
        # window) before ever resubmitting. If it's already there,
        # that position's own IDs are used directly and no duplicate
        # order is ever submitted - real resilience AND real safety,
        # not one traded for the other.
        transient_statuses = (429, 502, 503, 504)
        max_attempts = 3
        last_response = None
        idempotent_match = None
        attempt_started_at = datetime.utcnow()
        for attempt in range(1, max_attempts + 1):
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            last_response = response
            if response.status_code in (200, 201):
                break
            if response.status_code not in transient_statuses or attempt == max_attempts:
                break

            # Before resubmitting, confirm the PRIOR attempt genuinely
            # didn't already place this trade - this is what makes
            # retrying 504 safe again. Matches on trade_comment (now
            # unique per signal, not the old shared constant) so this
            # can never cross-match a DIFFERENT signal's position for
            # the same pair, even one that happened to open seconds
            # earlier.
            check_result = await check_for_matching_recent_position(
                METAAPI_ACCOUNT_ID, mt5_symbol, trade_comment, attempt_started_at
            )
            if check_result == "CHECK_FAILED":
                # Per explicit instruction: if the safety check itself
                # can't be trusted, the safe direction is to STOP, not
                # to fall back to blindly resubmitting. A missed trade
                # is recoverable; a duplicate real order is not.
                print(f"[MT5] ⚠️ Idempotency check itself failed - stopping rather than risk a duplicate order.")
                break
            if check_result:
                idempotent_match = check_result
                print(
                    f"[MT5] ✅ Prior attempt {attempt} actually succeeded (found matching "
                    f"open position {idempotent_match.get('id')}) - using it, NOT resubmitting."
                )
                break

            wait_seconds = 2 * attempt
            print(
                f"[MT5] ⚠️ Trade request got {response.status_code} "
                f"(attempt {attempt}/{max_attempts}) - retrying in {wait_seconds}s..."
            )
            await asyncio.sleep(wait_seconds)

        if idempotent_match:
            order_id = idempotent_match.get("id", "unknown")
            print(f"[MT5 PERSONAL COPY] ✅ Trade confirmed via idempotency check - Position ID: {order_id}")
            return order_id

        response = last_response
        if response.status_code in [200, 201]:
            result = response.json()
            order_id = result.get("orderId", "unknown")
            if order_id == "unknown":
                # FIX: CONFIRMED REAL ISSUE via live logs - a 200/201
                # response without a real "orderId" field was being
                # silently logged as a SUCCESS ("Trade placed - Order
                # ID: unknown"), with the actual response body
                # discarded entirely. This meant a request that
                # technically got a 2xx status but didn't actually
                # place a real trade (e.g. a different field name in
                # the response, a partial/rejected fill, or some
                # other MetaApi-side issue) looked identical to a
                # genuine success in the logs, with zero way to tell
                # the two apart after the fact. Logging the full raw
                # response now whenever this happens, so the real
                # field name/shape/error can be seen directly instead
                # of guessed at.
                print(f"[MT5 PERSONAL COPY] ⚠️ Trade response had no real orderId - full raw response: {result!r}")
            else:
                print(f"[MT5 PERSONAL COPY] ✅ Trade placed — Order ID: {order_id}")
            return order_id
        else:
            # FIX: used to only log the status code, discarding the
            # actual response body - meaning a real rejection reason
            # (bad symbol, invalid volume, market closed, insufficient
            # margin, etc.) was never visible anywhere, only ever a
            # bare number. Logging the full body now so a future
            # failure is actually diagnosable instead of another guess.
            print(f"[MT5 PERSONAL COPY] ❌ Trade failed after {max_attempts} attempt(s): {response.status_code} | {response.text}")
            return None
    except Exception as e:
        print(f"[MT5 PERSONAL COPY] ❌ Exception: {e}")
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

# Per explicit instruction, after a real confirmed issue found live:
# has_real_open_mt5_position and get_mt5_trade_outcome EACH used to
# open a brand new MetaApi client + websocket connection + fresh
# subscribe attempt on EVERY SINGLE CALL, rather than reusing one.
# Since these now run often (every scheduled/manual signal, every
# open-signal check, every weekly report), that meant many near-
# simultaneous fresh subscribe attempts hitting the SAME single
# account in a short window - confirmed directly in Railway logs as
# repeated "Failed to subscribe... not connected to broker yet" and
# MetaAPI's own TooManyRequestsException ("trying to access too many
# unexisting or undeployed trading accounts"). place_mt5_trade itself
# was NEVER affected by this - it uses a plain REST call, no
# websocket subscription at all, which is why real trades kept
# placing successfully throughout. This module-level cache holds ONE
# shared, persistent connection instead, reused across every call.
_SHARED_MT5_CONNECTION = {"connection": None}

async def get_shared_mt5_connection():
    """
    Returns the shared, persistent MetaAPI RPC connection, creating
    and subscribing it once on first use and reusing it on every
    subsequent call - rather than a fresh connect+subscribe every
    time. Returns None if it cannot be created/reused at all (caller
    treats that as "lookup unavailable right now", same fail-safe
    behavior as before).

    HARD TIMEOUT ADDED per explicit instruction, after a real
    confirmed incident: an entire day of scheduled signals and news
    posts silently failed to fire (while manual /signal requests kept
    working fine), traced back to "tp_sl_monitor... skipped: maximum
    number of running instances reached" appearing in the logs -
    proof a PREVIOUS run of a job using this exact connection was
    still stuck running when its next scheduled run came due. If
    MetaAPI's own client HANGS instead of raising promptly when an
    account is disconnected/disabled (as opposed to the TimeoutException
    it does sometimes raise), the previous code had nothing forcing
    it to give up - a hang here could tie up a scheduler worker
    indefinitely, and enough of those hanging at once (MetaAPI here,
    Deriv elsewhere) can saturate the whole job queue's worker pool,
    blocking brand new scheduled jobs from ever getting a slot to
    even start - while interactive commands, which run through a
    separate path, keep working the whole time. Wrapping the whole
    connect sequence in asyncio.wait_for guarantees this ALWAYS gives
    up within 15 seconds no matter what MetaAPI's client does
    internally, freeing the calling job promptly either way.
    """
    if _SHARED_MT5_CONNECTION["connection"] is not None:
        return _SHARED_MT5_CONNECTION["connection"]
    try:
        async def _connect():
            api = MetaApi(token=METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(
                account_id=METAAPI_ACCOUNT_ID
            )
            connection = account.get_rpc_connection()
            await connection.connect()
            await connection.wait_synchronized()
            return connection

        connection = await asyncio.wait_for(_connect(), timeout=15)
        _SHARED_MT5_CONNECTION["connection"] = connection
        print("[MT5 SHARED CONNECTION] ✅ New shared connection established and cached.")
        return connection
    except asyncio.TimeoutError:
        print("[MT5 SHARED CONNECTION] ❌ Timed out after 15s - MetaAPI account likely disconnected/disabled. Giving up for now rather than hanging.")
        return None
    except Exception as e:
        print(f"[MT5 SHARED CONNECTION] Failed to establish shared connection: {e}")
        return None


def _reset_shared_mt5_connection():
    """
    Clears the cached connection so the NEXT call rebuilds it fresh,
    rather than staying permanently stuck reusing a connection that
    just failed/went stale.
    """
    _SHARED_MT5_CONNECTION["connection"] = None


async def has_real_open_mt5_position(mt5_symbol):
    """
    True if the connected MT5 account has ANY currently open position on
    this symbol RIGHT NOW, regardless of who opened it - the bot itself,
    or a trade placed manually. Per explicit instruction, after a real
    confirmed regression: has_open_signal_for_pair only ever checks our
    OWN internal signal_log table, which only gets a row when THE BOT
    posts a signal - a manually-placed trade never touches that table
    at all, so the bot had no way of knowing one was live and posted a
    fresh scheduled signal on top of it (confirmed live: a manual BTCUSD
    BUY + the bot's own scheduled BTCUSD SELL open simultaneously).
    This checks the account's REAL live position list instead, so it
    catches a position no matter who placed it.

    Tolerant of broker symbol suffixes (e.g. mt5_symbol "BTCUSDm" vs a
    position reporting "BTCUSD" or "BTCUSDm.raw") by matching on
    whichever string is the prefix of the other, uppercased - avoids a
    silent false negative if the broker's exact suffix ever changes.

    Returns False (does NOT block) on any lookup failure - same
    fail-safe philosophy as has_open_signal_for_pair's own except
    block: an unreachable MetaAPI account should never silently stall
    the whole schedule forever.
    """
    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID or not mt5_symbol:
        return False
    try:
        connection = await get_shared_mt5_connection()
        if connection is None:
            return False

        positions = await asyncio.wait_for(connection.get_positions(), timeout=15)
        # Same dict-wrapping gotcha already confirmed once for
        # get_deals_by_position on this exact SDK - defensively
        # unwrapped here too rather than assuming it can't recur.
        if isinstance(positions, dict):
            positions = positions.get("positions", [])

        target = mt5_symbol.upper()
        for pos in positions:
            pos_symbol = (pos.get("symbol") if isinstance(pos, dict) else None) or ""
            pos_symbol = pos_symbol.upper()
            if pos_symbol and (pos_symbol.startswith(target) or target.startswith(pos_symbol)):
                return True
        return False
    except Exception as e:
        print(f"[MT5 POSITION CHECK] has_real_open_mt5_position error for {mt5_symbol}: {e}")
        _reset_shared_mt5_connection()
        return False


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
        connection = await get_shared_mt5_connection()
        if connection is None:
            return None, None

        deals = await asyncio.wait_for(
            connection.get_deals_by_position(position_id=str(position_id)),
            timeout=15
        )

        # FIX #2: CONFIRMED REAL ISSUE via live logs after the first
        # fix below - "2 unrecognized deal item(s), sample: 'deals'".
        # That sample being the literal string 'deals' is a strong,
        # specific signal: get_deals_by_position via this SDK's RPC
        # connection is returning a DICT wrapping the real list under
        # a "deals" key (e.g. {"deals": [...], "synchronizing": ...}),
        # not the bare array the REST API docs describe - a common
        # pattern for SDK wrapper methods that add metadata around the
        # raw REST response. Iterating that dict directly (the
        # previous behavior) iterates its KEYS, which is exactly why
        # the string "deals" itself showed up as a fake "deal item".
        # Unwrapping here before any per-item logic runs, while still
        # falling back to treating the response as already a plain
        # list if it isn't a dict (keeps this working either way,
        # rather than assuming one specific shape again).
        if isinstance(deals, dict):
            deals = deals.get("deals", [])

        # FIX: CONFIRMED REAL CRASH via live logs - "'str' object has
        # no attribute 'get'" repeating for every single linked order,
        # every sweep, for hours. get_deals_by_position's items were
        # being assumed to always be plain dicts and called with
        # .get() unconditionally - but at least one item in the real
        # response isn't a dict (could be a model/object instance
        # this SDK version returns instead of a plain dict, or some
        # other non-dict entry mixed into the list). This single bad
        # item was enough to throw inside the generator expression
        # below and get caught by the outer except, meaning this
        # function NEVER successfully determined an outcome for ANY
        # trade while this was happening - silently keeping every
        # signal/auto-copy-trade row stuck as OPEN regardless of
        # whether mt5_order_id was correctly saved or not. Each item
        # is now type-checked before .get() is ever called on it -
        # dicts use .get() as before, objects with an entryType
        # attribute use getattr as a fallback, anything else is
        # skipped and logged so the real shape can be seen if this
        # happens again, rather than crashing the whole lookup.
        def _deal_entry_type(d):
            if isinstance(d, dict):
                return d.get("entryType")
            return getattr(d, "entryType", None)

        def _deal_profit(d):
            if isinstance(d, dict):
                return d.get("profit", 0)
            return getattr(d, "profit", 0)

        unrecognized = [d for d in deals if not isinstance(d, dict) and not hasattr(d, "entryType")]
        if unrecognized:
            print(f"[MT5 OUTCOME] {position_id}: {len(unrecognized)} unrecognized deal item(s) AFTER unwrap, sample: {unrecognized[0]!r} | full deals type: {type(deals)!r}")

        closing_deal = next(
            (d for d in deals if _deal_entry_type(d) == "DEAL_ENTRY_OUT"),
            None
        )
        if closing_deal is None:
            return "OPEN", None

        profit = _deal_profit(closing_deal)
        print(
            f"[MT5 OUTCOME] Position {position_id} closed — "
            f"profit: {profit}"
        )
        return "CLOSED", profit

    except Exception as e:
        print(f"[MT5 OUTCOME] ❌ Lookup failed for {position_id}: {e}")
        _reset_shared_mt5_connection()
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
                raise Exception(f"RATE_LIMIT | body: {response.text[:300]}")
        if response.status_code != 200:
            raise Exception(f"GEMINI_ERROR_{response.status_code} | body: {response.text[:300]}")
        result = response.json()
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"[NEWS AI] Gemini failed ({e}), trying OpenRouter...")
        result = await ask_openrouter(prompt)
        if result.strip() in KNOWN_AI_FAILURE_STRINGS:
            print(f"[NEWS AI] OpenRouter also failed ('{result.strip()}'), trying Groq...")
            return await ask_groq(prompt)
        return result

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
        result = await ask_openrouter(prompt)
        if result.strip() in KNOWN_AI_FAILURE_STRINGS:
            print(f"[AI BIAS] OpenRouter also failed ('{result.strip()}'), trying Groq...")
            return await ask_groq(prompt)
        return result

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
        # SWITCHED per explicit instruction - "deepseek/deepseek-chat"
        # is a PAID model, which is exactly what was consuming your
        # OpenRouter credits. Confirmed via current research: DeepSeek's
        # free tier on OpenRouter was discontinued entirely as of July
        # 2026. "openrouter/free" is OpenRouter's own auto-router
        # (introduced Feb 2026) - it automatically picks whichever
        # free model is currently available, rather than pinning to
        # one specific free model that could get discontinued the
        # same way DeepSeek's just did.
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        response = requests.post(
            OPENROUTER_URL, headers=headers, json=data, timeout=30
        )
        if response.status_code != 200:
            print(f"[OPENROUTER] Failed with status {response.status_code} | body: {response.text[:300]}")
            return "⚠️ AI server busy."
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"OpenRouter Error: {e}")
        return "⚠️ AI servers unavailable."


async def ask_groq(prompt):
    """
    Third, genuinely independent free fallback, per explicit
    instruction - Groq's free tier requires no credit card and is
    consistently ranked among the most reliable free LLM tiers
    available. Uses openai/gpt-oss-120b rather than the commonly-
    cited llama-3.3-70b-versatile, since current research confirmed
    Groq has been actively RETIRING that exact model (and
    llama-3.1-8b-instant) - openai/gpt-oss-120b is their own named
    migration target, avoiding the same "pinned to a model that just
    got discontinued" mistake already found and fixed for Gemini.
    """
    if not GROQ_API_KEY:
        return "⚠️ AI service unavailable."
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "openai/gpt-oss-120b",
        "messages": [{"role": "user", "content": prompt}]
    }
    try:
        response = requests.post(
            GROQ_URL, headers=headers, json=data, timeout=30
        )
        if response.status_code != 200:
            print(f"[GROQ] Failed with status {response.status_code} | body: {response.text[:300]}")
            return "⚠️ AI server busy."
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Groq Error: {e}")
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
    await update.effective_message.reply_text(
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
        signal_key = context.args[0].replace("chantrade_", "")
        shared_context = channel_signal_context.get(signal_key)

        if not shared_context:
            sent_expired_signal = await update.message.reply_text(
                "⚠️ <b>This signal has expired.</b>\n\n"
                "Signals are only tradeable for 1 hour after they're "
                "posted. Tap <b>📊 Signal</b> below to get a fresh, "
                "live one instead.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_expired_signal.chat_id, sent_expired_signal.message_id)
            return

        pending_trades[user_id] = dict(shared_context)  # copy - each tapper gets their own independent trade
        await send_tier_selection(context.bot, user_id, pending_trades[user_id])
        return

    # Arrived here via the channel's "Know the Direction" news-alert
    # deep-link button (?start=newsevent_<list_id>_<idx>). Same fix as
    # chantrade_ above and for the same reason: a callback_data button
    # on a CHANNEL post either fails silently for anyone who hasn't
    # DM'd the bot yet, or (worse) replies publicly into the channel
    # itself instead of privately to the tapper - a url= deep link
    # opens a private DM instead, which is what "tap to see the
    # direction" is actually supposed to do. The private per-user copy
    # of this same alert still uses callback_data, since that copy is
    # already inside a private chat.
    if context.args and context.args[0].startswith("newsevent_"):
        remainder = context.args[0].replace("newsevent_", "")
        try:
            list_id, idx_str = remainder.rsplit("_", 1)
            idx = int(idx_str)
            batch = get_news_events_batch(list_id)
            event = batch[idx] if batch else None
            if event is None:
                raise IndexError
        except (ValueError, IndexError, KeyError):
            sent_expired_news = await update.message.reply_text(
                "⚠️ <b>This news list has expired.</b>\n\n"
                "Tap 📰 News below for a fresh list.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_expired_news.chat_id, sent_expired_news.message_id)
            return

        if not is_verified(user_id):
            count = increment_trial(user_id)
            if count > FREE_TRIAL_LIMIT:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
                return

        await send_news_direction_analysis(
            context.bot, update.effective_chat.id, event,
            batch_events=batch
        )

        if not is_verified(user_id):
            remaining = trial_remaining(user_id)
            if remaining <= 0:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

    # Arrived here via a broadcast's optional button
    # (?start=goto_<destination>) - works identically whether the tap
    # came from a channel post or a DM, since a url= deep link always
    # opens a private chat regardless of where it was tapped. Add a
    # new keyword here any time a new destination is needed (see
    # broadcast_command's || syntax) - this is the one place that
    # ever needs to know about it.
    if context.args and context.args[0].startswith("goto_"):
        destination = context.args[0].replace("goto_", "")
        if destination == "exness":
            await send_exness_autotrade_intro(context.bot, update.effective_chat.id)
        elif destination == "deriv":
            await send_connect_instructions(context.bot, user_id)
        elif destination == "signal":
            user_modes[user_id] = "signal"
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=(
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
                    "• USDJPY"
                ),
                parse_mode=ParseMode.HTML
            )
        elif destination == "newscalendar":
            if get_user_utc_offset_minutes(user_id) is None:
                user_modes[user_id] = "awaiting_timezone_location"
                sent_tz_prompt = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=(
                        "🌍 <b>Quick one-time setup:</b> share your location so news "
                        "and event times always show in <b>your own local time</b>, "
                        "wherever you are.\n\n"
                        "Or tap <b>Skip</b> to use West Africa Time (GMT+1) instead."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=ReplyKeyboardMarkup(
                        [[KeyboardButton("📍 Share My Location", request_location=True)], ["Skip"]],
                        resize_keyboard=True,
                        one_time_keyboard=True
                    )
                )
                schedule_auto_delete(sent_tz_prompt.chat_id, sent_tz_prompt.message_id)
            else:
                await send_news_calendar(context.bot, update.effective_chat.id, user_id)
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
        # Delete the previous welcome message first, if one exists -
        # per explicit instruction, prevents repeated taps of a
        # deep-link button (channelcta, chantrade_) or repeated manual
        # /start calls from stacking up duplicate "what would you like
        # to do today?" messages. Best-effort: if the old message was
        # already deleted by the user, or is too old for the bot to
        # delete (Telegram only allows deleting a bot's own messages,
        # but only within certain limits), this fails silently and
        # the new welcome message still sends normally either way.
        prev = last_welcome_message.get(user_id)
        if prev:
            try:
                await context.bot.delete_message(chat_id=prev[0], message_id=prev[1])
            except Exception as e:
                print(f"[START] Couldn't delete previous welcome message for {user_id}: {e}")

        sent_welcome = await update.message.reply_text(
            f"👋 <b>Welcome back, {username}!</b>\n\n"
            f"✅ You're a <b>verified Nexora AI trader.</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>What would you like to do today?</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 <b>Signal</b> — Get a live trading signal right now\n\n"
            f"📰 <b>News</b> — Get a direct call on high-impact news\n\n"
            f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
            f"signals directly, manually or fully automatic\n\n"
            f"🤖 <b>Exness Auto-Trade</b> — Subscribe to auto-trade "
            f"directly on your own Exness MT5/MT4 account\n\n"
            f"<i>All four buttons are at the bottom of your screen 👇</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        last_welcome_message[user_id] = (sent_welcome.chat_id, sent_welcome.message_id)
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
            f"📰 <b>News</b> — Get a direct call on high-impact news\n\n"
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

async def send_news_calendar(bot, chat_id, user_id):
    """
    Standalone version of the newsmenu_calendar callback's calendar-
    fetch logic, for the goto_newscalendar deep link specifically
    (used by broadcast buttons) - added rather than reusing the
    existing callback handler because that one calls
    query.message.edit_text on an EXISTING message, which doesn't
    exist in a fresh /start deep-link context. Sends a new message
    instead. Kept as its own function rather than refactoring the
    existing (already-working) callback handler to share code, to
    avoid any risk of changing live, already-tested behavior for
    something this small.
    """
    events = get_todays_high_impact_events()
    if not events:
        next_date = get_next_high_impact_event_date()
        no_news_text = (
            "📅 <b>No high-impact USD, EUR, GBP, or JPY news "
            "scheduled for today.</b>\n\n"
        )
        if next_date:
            no_news_text += f"Next one: <b>{next_date}</b>.\n\n"
        no_news_text += (
            "Try <b>News Breakdown</b> instead for a live read "
            "on any specific pair or currency you're curious about."
        )
        await bot.send_message(chat_id=chat_id, text=no_news_text, parse_mode=ParseMode.HTML)
        return

    list_id = store_news_events_batch(events)
    today_display = datetime.utcnow().strftime("%A, %d %B %Y")
    now_utc = datetime.utcnow()
    user_offset = get_user_utc_offset_minutes(user_id)
    if user_offset is None:
        user_offset = DEFAULT_UTC_OFFSET_MINUTES
    tz_label = format_gmt_label(user_offset)

    released_count = 0
    buttons = []
    for i, event in enumerate(events):
        flag = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵"}.get(event["currency"], "🌍")
        status_label, is_released = format_event_status(event, now_utc)
        if is_released:
            released_count += 1
        local_time = format_local_time(event.get("event_dt_utc", ""), user_offset)
        label = f"{flag} {event['title'][:32]}"
        if local_time:
            label += f" ({local_time})"
        if status_label:
            label += f" — {status_label}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"newsevent_{list_id}_{i}")])

    upcoming_count = len(events) - released_count
    if upcoming_count > 0:
        summary_line = f"✅ {released_count} released • 🔜 {upcoming_count} upcoming"
    else:
        summary_line = f"✅ All {released_count} released — nothing left scheduled today"

    await bot.send_message(
        chat_id=chat_id,
        text=(
            f"📅 <b>High-Impact News — {today_display}</b>\n"
            f"{summary_line}\n"
            f"<i>Times shown in your local time ({tz_label})</i>\n\n"
            f"Tap any event below for a direct BUY/SELL call, "
            f"fundamentals-first:"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def build_exness_autotrade_dashboard(user_id, account, expiry, now):
    """
    Builds the (text, reply_markup) for an already-active Exness
    Auto-Trade subscriber's status dashboard. Extracted per explicit
    instruction - this used to be inline only inside the mt5auto_start
    callback (reachable only AFTER tapping "Continue" past the generic
    intro), meaning an already-connected user had to sit through the
    new-user intro every time just to check their own balance. Now a
    single shared builder both the direct button-tap handler and the
    mt5auto_start callback call - one source of truth, so the two
    entry points can never silently drift apart from each other.
    """
    days_left = (expiry - now).days
    risk_mode = account.get("risk_mode", "lot")
    if risk_mode == "lot":
        risk_display = f"{account.get('lot_size', 0.01)} lots"
    elif risk_mode == "account_flip":
        open_stack = get_open_flip_stack(user_id)
        if open_stack:
            risk_display = (
                f"🚀 Flip — stack open, {open_stack.get('layer_count', 1)} layer(s) so far "
                f"(base {account.get('flip_base_lot', 0.01)}, +{account.get('flip_step', 0.01)}/layer, "
                f"cap {account.get('flip_max_lot', 0.01)}, trail {account.get('flip_trail_pips', 10)} pips)"
            )
        else:
            risk_display = (
                f"🚀 Flip — no open stack right now "
                f"(base {account.get('flip_base_lot', 0.01)}, +{account.get('flip_step', 0.01)}/layer, "
                f"cap {account.get('flip_max_lot', 0.01)}, every {account.get('flip_trigger_pips', 10)} "
                f"pips, max {account.get('flip_max_layers', 3)} layers)"
            )
    else:
        risk_display = f"{account.get('risk_percent', 1.0)}% risk"
    bot_choice = account.get("bot_choice", "follow_channel")
    bot_label = (
        "📡 Full Signal Coverage (Recommended)" if bot_choice == "follow_channel"
        else "🚀 Account Flip (own price-action strategy)" if bot_choice == "account_flip"
        else MT5_AUTOTRADE_BOTS.get(bot_choice, {}).get("label", bot_choice)
    )
    # Account name/server come straight from the saved row; balance is
    # a live fetch since it changes constantly and was never something
    # worth caching - per explicit instruction.
    balance = await get_client_mt5_balance(account["metaapi_account_id"])
    balance_display = f"${balance:,.2f}" if balance is not None else "unavailable right now"
    text = (
        f"🤖 <b>Exness Auto-Trade — Active</b>\n\n"
        f"Account: {account.get('account_name') or account.get('account_number', 'N/A')}\n"
        f"Server: {account.get('server', 'N/A')}\n"
        f"Balance: {balance_display}\n\n"
        f"Subscription: {days_left} day(s) left\n"
        f"Mode: {bot_label}"
        + (f" on {account.get('pair_choice', '').upper()}" if account.get("pair_choice") else "")
        + f"\nRisk: {risk_display}\n\n"
        f"Waiting on the trading strategy to be wired in before "
        f"real trades begin - infrastructure is ready."
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Change lot size / risk %", callback_data="mt5settings_change")],
    ])
    return text, markup


async def send_exness_autotrade_intro(bot, chat_id):
    """
    Shared by the normal "🤖 Exness Auto-Trade" button tap (handle_buttons
    below) AND the "Try it now" deep-link button used in broadcasts
    (start()'s exnessautotrade branch) - same intro either way, just
    two different doors into it, mirroring the same pattern already
    used for send_connect_instructions/send_tier_selection.
    """
    sent_info = await bot.send_message(
        chat_id=chat_id,
        text=(
            "🤖 <b>Exness Auto-Trade</b>\n\n"
            "Let Nexora AI trade directly on your own Exness MT5/MT4 "
            "account, automatically, using the same technical strategy "
            "already powering your signals — no need to manually "
            "watch charts or place trades yourself.\n\n"
            f"💵 <b>Price:</b> {MT5_AUTOTRADE_DISPLAY_PRICE}\n"
            f"<i>Covers your dedicated MT5 connection, server uptime, "
            f"and continuous strategy execution — running for you "
            f"around the clock, not just a one-time signal.</i>\n\n"
            "📈 <b>Why auto-trade?</b> Markets move fast, and the best "
            "setups don't always happen when you're free to act on "
            "them. Auto-trading lets you stay in the market on your "
            "own terms — your own lot size, your own risk %, executed "
            "the moment a real setup appears.\n\n"
            "⚠️ <b>Disclaimer:</b> Trading involves real risk. Past "
            "performance is not a guarantee of future results, and "
            "you can lose money. Only subscribe with funds you can "
            "afford to risk, and never with borrowed money.\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "Ready to continue?"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Continue", callback_data="mt5auto_start")]
        ])
    )
    schedule_auto_delete(sent_info.chat_id, sent_info.message_id)



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

                autotrade_on = bool(existing.get("deriv_autotrade_enabled"))
                autotrade_bot_choice = existing.get("deriv_bot_choice")
                already_configured = autotrade_bot_choice in DERIV_AUTOTRADE_BOTS or autotrade_bot_choice == "account_flip"
                if autotrade_on and autotrade_bot_choice in DERIV_AUTOTRADE_BOTS:
                    autotrade_mode_label = f"{DERIV_AUTOTRADE_BOTS[autotrade_bot_choice]['label']} Bot"
                elif autotrade_on and autotrade_bot_choice == "account_flip":
                    autotrade_mode_label = "🚀 Account Flip"
                else:
                    autotrade_mode_label = None
                autotrade_status_line = (
                    f"🎯 <b>Auto-Trade:</b> ON ({autotrade_mode_label})"
                    if autotrade_on and autotrade_mode_label else
                    "🎯 <b>Auto-Trade:</b> OFF"
                )
                manage_button = InlineKeyboardButton("⚙️ Manage Bot", callback_data="derivauto_menu")
                toggle_button = (
                    InlineKeyboardButton("🔴 Turn Bot OFF", callback_data="derivauto_turnoff")
                    if autotrade_on else
                    InlineKeyboardButton(
                        "🟢 Turn Bot ON",
                        callback_data="derivauto_turnon" if already_configured else "derivauto_menu"
                    )
                )

                await update.message.reply_text(
                    f"🔗 <b>Linked Deriv Options Account</b>\n\n"
                    f"<b>Account:</b> {snapshot['loginid']}\n"
                    f"<b>Balance:</b> {snapshot['balance']} {snapshot['currency']}\n"
                    f"<b>Open Positions:</b> {open_count}\n\n"
                    f"{autotrade_status_line}\n\n"
                    f"ℹ️ <i>This shows your Options account only. Your MT5 "
                    f"and cTrader balances aren't connected yet.</i>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                await update.message.reply_text(
                    "Want to change or set up trading?",
                    reply_markup=InlineKeyboardMarkup([[manage_button], [toggle_button]])
                )
            else:
                reconnect_markup, oauth_state = await build_deriv_login_button(user_id)
                reconnect_text = (
                    "⚠️ <b>Couldn't reach your linked Deriv account.</b>\n\n"
                    "Your saved token may have expired or been revoked. "
                    "Paste a new real-account API token below to relink."
                )
                if reconnect_markup:
                    reconnect_text = (
                        "⚠️ <b>Couldn't reach your linked Deriv account.</b>\n\n"
                        "Your saved token may have expired or been revoked. "
                        "Tap below to log in directly with Deriv and relink "
                        "instantly, or paste a new API token here instead."
                    )
                sent_unreachable = await update.message.reply_text(
                    reconnect_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reconnect_markup or main_keyboard
                )
                if reconnect_markup and oauth_state:
                    update_deriv_oauth_state_message_id(oauth_state, sent_unreachable.chat_id, sent_unreachable.message_id)
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

    if "news" in text:
        if get_user_utc_offset_minutes(user_id) is None:
            user_modes[user_id] = "awaiting_timezone_location"
            sent_tz_prompt = await update.message.reply_text(
                "🌍 <b>Quick one-time setup:</b> share your location so news "
                "and event times always show in <b>your own local time</b>, "
                "wherever you are.\n\n"
                "Or tap <b>Skip</b> to use West Africa Time (GMT+1) instead.",
                parse_mode=ParseMode.HTML,
                reply_markup=ReplyKeyboardMarkup(
                    [[KeyboardButton("📍 Share My Location", request_location=True)], ["Skip"]],
                    resize_keyboard=True,
                    one_time_keyboard=True
                )
            )
            schedule_auto_delete(sent_tz_prompt.chat_id, sent_tz_prompt.message_id)
            return

        sent_news_menu = await update.message.reply_text(
            "📰 <b>News</b>\n\n"
            "What would you like?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 News Calendar", callback_data="newsmenu_calendar")],
                [InlineKeyboardButton("📚 News Breakdown", callback_data="newsmenu_breakdown")],
            ])
        )
        schedule_auto_delete(sent_news_menu.chat_id, sent_news_menu.message_id)
        return

    if "exness auto-trade" in text:
        # FIX: CONFIRMED REAL UX BUG, per explicit instruction - an
        # already-connected, active subscriber tapping this button was
        # ALWAYS shown the generic intro/pricing message first, and
        # had to tap "Continue" before their real account dashboard
        # (balance, subscription, mode) appeared - the dashboard logic
        # itself already existed correctly, it just lived behind an
        # extra unnecessary tap that only new/unconnected users should
        # ever see. Checks the exact same "already active" condition
        # the mt5auto_start callback already uses, and goes straight
        # to the real dashboard here too when it's true.
        user_id = str(update.effective_user.id)
        account = get_mt5_autotrade_account(user_id)
        now = datetime.utcnow()
        expiry = None
        if account and account.get("subscription_expires_at"):
            try:
                expiry = datetime.fromisoformat(
                    account["subscription_expires_at"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                expiry = None
        already_active = (
            account and expiry is not None and now < expiry
            and account.get("metaapi_account_id")
        )
        if already_active:
            dash_text, dash_markup = await build_exness_autotrade_dashboard(user_id, account, expiry, now)
            await update.message.reply_text(dash_text, parse_mode=ParseMode.HTML, reply_markup=dash_markup)
            return
        await send_exness_autotrade_intro(context.bot, update.message.chat_id)
        return

# ============================================
# CALLBACK HANDLER — APPROVE / REJECT
# ============================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "show_main_menu":
        user_id = str(query.from_user.id)
        if is_verified(user_id):
            await query.message.reply_text(
                "👇 <b>What would you like to do today?</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
        else:
            await query.message.reply_text(
                "👇 <b>Tap Signal below for a free trial, or verify "
                "your Exness account for full access.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
        return

    if data == "mt5auto_start":
        user_id = str(query.from_user.id)
        account = get_mt5_autotrade_account(user_id)
        now = datetime.utcnow()
        expiry = None
        if account and account.get("subscription_expires_at"):
            try:
                expiry = datetime.fromisoformat(
                    account["subscription_expires_at"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                expiry = None
        already_active = (
            account and expiry is not None and now < expiry
            and account.get("metaapi_account_id")
        )
        if already_active:
            # Already fully set up - no need to walk through bot/pair/
            # lot-risk selection again, just show current status.
            dash_text, dash_markup = await build_exness_autotrade_dashboard(user_id, account, expiry, now)
            await query.message.edit_text(dash_text, parse_mode=ParseMode.HTML, reply_markup=dash_markup)
            return

        # REAL FIX, per explicit instruction - a confirmed case: a
        # customer paid successfully (subscription_expires_at was set
        # the moment payment cleared), but their FIRST attempt to
        # connect their MT5 account failed (wrong password), leaving
        # metaapi_account_id empty. Tapping "Exness Auto-Trade" again
        # used to fall straight through to the full bot-choice ->
        # payment flow below, as if they'd never paid at all - meaning
        # a simple retry-after-typo demanded a SECOND payment. This
        # checks for exactly that in-between state (valid, unexpired
        # subscription + no connected account yet) and resumes account
        # setup directly instead, skipping payment and bot-choice
        # entirely since both are already settled.
        already_paid_not_connected = (
            account and expiry is not None and now < expiry
            and not account.get("metaapi_account_id")
        )
        if already_paid_not_connected:
            days_left = (expiry - now).days
            user_modes[user_id] = "mt5_awaiting_account_number"
            await query.message.edit_text(
                f"✅ <b>You're already subscribed</b> ({days_left} day(s) "
                f"left) - no need to pay again. Let's just finish "
                f"connecting your account.\n\n"
                f"Send your Exness <b>account number</b>:",
                parse_mode=ParseMode.HTML
            )
            return

        mt5_signup_state[user_id] = {"flow": "presetup"}
        await query.message.edit_text(
            "🤖 <b>How should Exness Auto-Trade decide your trades?</b>\n\n"
            "📡 <b>Full Signal Coverage (Recommended)</b> — auto-copies whatever "
            "the main channel already posts (XAUUSD, GBPJPY, BTCUSD, etc).\n\n"
            "🎯 <b>Pick a Bot</b> — choose one of 4 dedicated strategy "
            "bots and one specific pair for it to trade.\n\n"
            "🚀 <b>Account Flip</b> — its own standalone price-action "
            "strategy that layers in bigger positions while winning, "
            "with a trailing stop across the whole stack. Higher risk.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📡 Full Signal Coverage (Recommended)", callback_data="mt5auto_follow_channel")],
                [InlineKeyboardButton("🎯 Pick a Bot", callback_data="mt5auto_choose_bot")],
                [InlineKeyboardButton("🚀 Account Flip", callback_data="mt5auto_account_flip")],
            ])
        )
        return

    if data == "mt5auto_follow_channel":
        user_id = str(query.from_user.id)
        if user_id not in mt5_signup_state:
            mt5_signup_state[user_id] = {"flow": "presetup"}
        mt5_signup_state[user_id]["bot_choice"] = "follow_channel"
        mt5_signup_state[user_id]["pair_choice"] = None
        await query.message.edit_text(
            "📏 <b>Choose how trades should be sized:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📏 Fixed lot size", callback_data="mt5auto_mode_lot")],
                [InlineKeyboardButton("📊 Risk %", callback_data="mt5auto_mode_risk")],
                [InlineKeyboardButton("⬅️ Back", callback_data="mt5auto_start")],
            ])
        )
        return

    if data == "mt5auto_account_flip":
        user_id = str(query.from_user.id)
        await query.message.edit_text(
            "⚠️ <b>Account Flip — Read Before Enabling</b>\n\n"
            "This mode compounds lot size while a trade is winning, adding "
            "bigger positions as price moves further your way. It is built "
            "to grow fast, not to protect capital the way Follow Channel or "
            "Choose a Bot do. A sudden reversal after several layers have "
            "stacked can hand back a large chunk of the run's gains at "
            "once. Every layer beyond the first only exists because the "
            "trade is already in profit — nothing is added while losing — "
            "but this is still meaningfully higher-risk than the other two "
            "modes and is not suitable for money you aren't prepared to see "
            "swing hard in both directions.\n\n"
            "It also runs its own standalone strategy — pure price action "
            "(Engulfing / Pin Bar / Inside Bar Breakout), separate from "
            "every other bot's strategy pool.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I Understand, Continue", callback_data="mt5auto_flip_accept")],
                [InlineKeyboardButton("⬅️ Back", callback_data="mt5auto_start")],
            ])
        )
        return

    if data == "mt5auto_flip_accept":
        user_id = str(query.from_user.id)
        if user_id not in mt5_signup_state:
            mt5_signup_state[user_id] = {"flow": "presetup"}
        mt5_signup_state[user_id]["bot_choice"] = "account_flip"
        mt5_signup_state[user_id]["risk_mode"] = "account_flip"
        mt5_signup_state[user_id]["flip_disclaimer_accepted"] = True

        buttons = [
            [InlineKeyboardButton(PAIR_CONFIG[p]["display"], callback_data=f"mt5auto_flippair_{p}")]
            for p in MT5_AUTOTRADE_PAIRS
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="mt5auto_account_flip")])
        await query.message.edit_text(
            "🚀 <b>Account Flip</b> — choose the one pair it should trade:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("mt5auto_flippair_"):
        user_id = str(query.from_user.id)
        pair_key = data.replace("mt5auto_flippair_", "")
        if pair_key not in MT5_AUTOTRADE_PAIRS:
            return
        if user_id not in mt5_signup_state:
            mt5_signup_state[user_id] = {"flow": "presetup"}
        mt5_signup_state[user_id]["pair_choice"] = pair_key
        user_modes[user_id] = "mt5_awaiting_flip_base"
        await query.message.edit_text(
            f"✅ {PAIR_CONFIG[pair_key]['display']} selected.\n\n"
            f"🚀 <b>Account Flip — last step</b>\n\n"
            f"Send your <b>starting lot size</b> (e.g. 0.01):",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5auto_choose_bot":
        user_id = str(query.from_user.id)
        buttons = [
            [InlineKeyboardButton(bot["label"], callback_data=f"mt5auto_bot_{key}")]
            for key, bot in MT5_AUTOTRADE_BOTS.items()
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="mt5auto_start")])
        await query.message.edit_text(
            "🎯 <b>Choose a bot:</b>\n\n" + "\n".join(
                f"{bot['label']} — {bot['description']}" for bot in MT5_AUTOTRADE_BOTS.values()
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("mt5auto_bot_"):
        user_id = str(query.from_user.id)
        bot_key = data.replace("mt5auto_bot_", "")
        if bot_key not in MT5_AUTOTRADE_BOTS:
            return
        if user_id not in mt5_signup_state:
            mt5_signup_state[user_id] = {"flow": "presetup"}
        mt5_signup_state[user_id]["bot_choice"] = bot_key

        buttons = [
            [InlineKeyboardButton(PAIR_CONFIG[p]["display"], callback_data=f"mt5auto_pair_{p}")]
            for p in MT5_AUTOTRADE_PAIRS
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="mt5auto_choose_bot")])
        await query.message.edit_text(
            f"✅ {MT5_AUTOTRADE_BOTS[bot_key]['label']} selected.\n\n"
            f"📌 <b>Now choose which pair to trade it on:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("mt5auto_pair_"):
        user_id = str(query.from_user.id)
        pair_key = data.replace("mt5auto_pair_", "")
        if pair_key not in MT5_AUTOTRADE_PAIRS:
            return
        if user_id not in mt5_signup_state:
            mt5_signup_state[user_id] = {"flow": "presetup"}
        mt5_signup_state[user_id]["pair_choice"] = pair_key
        bot_choice = mt5_signup_state[user_id].get("bot_choice", "follow_channel")
        back_target = "mt5auto_start" if bot_choice == "follow_channel" else f"mt5auto_bot_{bot_choice}"
        await query.message.edit_text(
            f"✅ {PAIR_CONFIG[pair_key]['display']} selected.\n\n"
            f"📏 <b>Choose how trades should be sized:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📏 Fixed lot size", callback_data="mt5auto_mode_lot")],
                [InlineKeyboardButton("📊 Risk %", callback_data="mt5auto_mode_risk")],
                [InlineKeyboardButton("⬅️ Back", callback_data=back_target)],
            ])
        )
        return

    if data == "mt5auto_mode_lot":
        user_id = str(query.from_user.id)
        user_modes[user_id] = "mt5_awaiting_lot_value"
        await query.message.edit_text(
            "📏 Send your desired <b>lot size</b> (e.g. 0.01):",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5auto_mode_risk":
        user_id = str(query.from_user.id)
        user_modes[user_id] = "mt5_awaiting_risk_value"
        await query.message.edit_text(
            "📊 Send your desired <b>risk %</b> per trade (e.g. 1 for 1%):",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5auto_continue":
        user_id = str(query.from_user.id)

        if not is_verified(user_id):
            await query.message.edit_text(
                "🔒 <b>Exness Auto-Trade requires a verified Exness "
                "account first.</b>\n\n"
                "One quick step below 👇",
                parse_mode=ParseMode.HTML
            )
            user_modes[user_id] = "awaiting_email"
            await send_verification_gate(update)
            return

        account = get_mt5_autotrade_account(user_id)
        now = datetime.utcnow()
        expiry = None
        if account and account.get("subscription_expires_at"):
            try:
                expiry = datetime.fromisoformat(
                    account["subscription_expires_at"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                expiry = None

        subscribed = expiry is not None and now < expiry

        if not subscribed:
            email = get_verified_user_email(user_id)
            reference = f"MT5AUTO-{user_id}-{int(time.time())}"
            checkout_url = await korapay_initialize_charge(user_id, email or f"{user_id}@nexoraai.temp", reference)
            if not checkout_url:
                await query.message.edit_text(
                    "⚠️ <b>Couldn't start the payment right now.</b> "
                    "Please try again shortly.",
                    parse_mode=ParseMode.HTML
                )
                return
            log_korapay_transaction(reference, user_id, MT5_AUTOTRADE_MONTHLY_FEE, MT5_AUTOTRADE_CURRENCY)

            # Persist the bot/pair/lot-risk choices NOW, before payment
            # confirms - per explicit instruction, these are collected
            # BEFORE payment, so they must survive until the KoraPay
            # webhook + process_confirmed_korapay_payments job actually
            # activates the subscription (which happens independently,
            # possibly minutes later).
            signup = mt5_signup_state.get(user_id, {})
            account_fields = {
                "bot_choice": signup.get("bot_choice", "follow_channel"),
                "pair_choice": signup.get("pair_choice"),
                "risk_mode": signup.get("risk_mode", "lot"),
                "lot_size": signup.get("lot_size", 0.01),
                "risk_percent": signup.get("risk_percent", 1.0),
            }
            if signup.get("risk_mode") == "account_flip":
                account_fields.update({
                    "flip_base_lot": signup.get("flip_base_lot", 0.01),
                    "flip_step": signup.get("flip_step"),
                    "flip_max_lot": signup.get("flip_max_lot"),
                    "flip_trigger_pips": signup.get("flip_trigger_pips"),
                    "flip_max_layers": signup.get("flip_max_layers"),
                    "flip_trail_pips": signup.get("flip_trail_pips"),
                    "flip_disclaimer_accepted": True,
                })
            upsert_mt5_autotrade_account(user_id, account_fields)

            await query.message.edit_text(
                f"🤖 <b>Exness Auto-Trade — {MT5_SUBSCRIPTION_DAYS}-day subscription</b>\n\n"
                f"Tap below to pay. Your MT5 connection will unlock "
                f"automatically once payment is confirmed.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💳 Pay Now", url=checkout_url)]])
            )
            return

        if not account.get("metaapi_account_id"):
            user_modes[user_id] = "mt5_awaiting_account_number"
            await query.message.edit_text(
                "✅ <b>Subscription active.</b>\n\n"
                "Now let's connect your Exness MT5/MT4 account.\n\n"
                "Send your <b>account number</b>:",
                parse_mode=ParseMode.HTML
            )
            return

        days_left = (expiry - now).days
        risk_mode = account.get("risk_mode", "lot")
        if risk_mode == "lot":
            risk_display = f"{account.get('lot_size', 0.01)} lots"
        elif risk_mode == "account_flip":
            open_stack = get_open_flip_stack(user_id)
            stack_note = (
                f"stack open, {open_stack.get('layer_count', 1)} layer(s)" if open_stack
                else "no open stack right now"
            )
            risk_display = (
                f"🚀 Account Flip — {stack_note} "
                f"(base {account.get('flip_base_lot', 0.01)}, +{account.get('flip_step', 0.01)}/layer, "
                f"cap {account.get('flip_max_lot', 0.01)}, every {account.get('flip_trigger_pips', 10)} pips, "
                f"max {account.get('flip_max_layers', 3)} layers, trail {account.get('flip_trail_pips', 10)} pips)"
            )
        else:
            risk_display = f"{account.get('risk_percent', 1.0)}% risk"
        await query.message.edit_text(
            f"🤖 <b>Exness Auto-Trade — Active</b>\n\n"
            f"Subscription: {days_left} day(s) left\n"
            f"MT5 account: connected\n"
            f"Mode: {risk_display}\n\n"
            f"Waiting on the trading strategy to be wired in before "
            f"real trades begin - infrastructure is ready.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Change lot size / risk %", callback_data="mt5settings_change")],
            ])
        )
        return

    if data == "mt5settings_change":
        user_id = str(query.from_user.id)
        await query.message.edit_text(
            "⚙️ <b>Choose your mode:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📏 Fixed lot size", callback_data="mt5settings_lot")],
                [InlineKeyboardButton("📊 Risk %", callback_data="mt5settings_risk")],
                [InlineKeyboardButton("🔄 Switch bot / signal mode", callback_data="mt5switch_menu")],
            ])
        )
        return

    # SWITCH MODE flow, per explicit instruction - lets an ALREADY
    # connected, already-subscribed customer change between "Follow
    # Channel Signals" and a specific bot (or switch which bot/pair)
    # at any time, without touching their account connection, lot
    # size, or payment at all. Deliberately a SEPARATE set of
    # callback_data values from the original signup flow
    # (mt5auto_follow_channel / mt5auto_choose_bot / etc.) - those
    # accumulate choices in mt5_signup_state on the way to a NEW
    # payment, which doesn't apply here; this saves directly to
    # Supabase at the final step since the subscription is already
    # active.
    if data == "mt5renew_keep":
        user_id = str(query.from_user.id)
        await query.message.edit_text(
            "✅ <b>Keeping your current account.</b> All set for the next "
            f"{MT5_SUBSCRIPTION_DAYS} days.",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5renew_new":
        user_id = str(query.from_user.id)
        account = get_mt5_autotrade_account(user_id)
        old_metaapi_id = account.get("metaapi_account_id") if account else None
        upsert_mt5_autotrade_account(user_id, {
            "account_number": None,
            "encrypted_password": None,
            "server": None,
            "account_name": None,
            "metaapi_account_id": None,
        })
        if old_metaapi_id:
            asyncio.create_task(deprovision_mt5_account(old_metaapi_id))
        user_modes[user_id] = "mt5_awaiting_account_number"
        await query.message.edit_text(
            "🔄 <b>Let's connect your new account.</b>\n\n"
            "Send your Exness <b>account number</b>:",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5switch_menu":
        user_id = str(query.from_user.id)
        await query.message.edit_text(
            "🔄 <b>Switch how Exness Auto-Trade decides your trades:</b>\n\n"
            "📡 <b>Full Signal Coverage (Recommended)</b> — auto-copies whatever "
            "the main channel already posts (XAUUSD, GBPJPY, BTCUSD, etc).\n\n"
            "🎯 <b>Pick a Bot</b> — choose one of 4 dedicated strategy "
            "bots and one specific pair for it to trade.\n\n"
            "🚀 <b>Account Flip</b> — its own standalone price-action "
            "strategy that layers in bigger positions while winning, "
            "with a trailing stop across the whole stack. Higher risk.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📡 Full Signal Coverage (Recommended)", callback_data="mt5switch_follow_channel")],
                [InlineKeyboardButton("🎯 Pick a Bot", callback_data="mt5switch_choose_bot")],
                [InlineKeyboardButton("🚀 Account Flip", callback_data="mt5switch_account_flip")],
            ])
        )
        return

    if data == "mt5switch_account_flip":
        user_id = str(query.from_user.id)
        await query.message.edit_text(
            "⚠️ <b>Account Flip — Read Before Enabling</b>\n\n"
            "This mode compounds lot size while a trade is winning, adding "
            "bigger positions as price moves further your way. It is built "
            "to grow fast, not to protect capital the way Follow Channel or "
            "Choose a Bot do. A sudden reversal after several layers have "
            "stacked can hand back a large chunk of the run's gains at "
            "once. Every layer beyond the first only exists because the "
            "trade is already in profit — nothing is added while losing — "
            "but this is still meaningfully higher-risk than the other two "
            "modes and is not suitable for money you aren't prepared to see "
            "swing hard in both directions.\n\n"
            "It also runs its own standalone strategy — pure price action "
            "(Engulfing / Pin Bar / Inside Bar Breakout), separate from "
            "every other bot's strategy pool.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I Understand, Continue", callback_data="mt5switch_flip_accept")],
                [InlineKeyboardButton("⬅️ Back", callback_data="mt5switch_menu")],
            ])
        )
        return

    if data == "mt5switch_flip_accept":
        user_id = str(query.from_user.id)
        mt5_signup_state[user_id] = {
            "flow": "switch",
            "bot_choice": "account_flip",
            "risk_mode": "account_flip",
            "flip_disclaimer_accepted": True,
        }
        buttons = [
            [InlineKeyboardButton(PAIR_CONFIG[p]["display"], callback_data=f"mt5switch_flippair_{p}")]
            for p in MT5_AUTOTRADE_PAIRS
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="mt5switch_account_flip")])
        await query.message.edit_text(
            "🚀 <b>Account Flip</b> — choose the one pair it should trade:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("mt5switch_flippair_"):
        user_id = str(query.from_user.id)
        pair_key = data.replace("mt5switch_flippair_", "")
        if pair_key not in MT5_AUTOTRADE_PAIRS:
            return
        mt5_signup_state.setdefault(user_id, {"flow": "switch"})["pair_choice"] = pair_key
        user_modes[user_id] = "mt5_awaiting_flip_base"
        await query.message.edit_text(
            f"✅ {PAIR_CONFIG[pair_key]['display']} selected.\n\n"
            f"🚀 <b>Account Flip — last step</b>\n\n"
            f"Send your <b>starting lot size</b> (e.g. 0.01):",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5switch_follow_channel":
        user_id = str(query.from_user.id)
        upsert_mt5_autotrade_account(user_id, {"bot_choice": "follow_channel", "pair_choice": None})
        await query.message.edit_text(
            "✅ <b>Switched to Following Channel Signals.</b>\n\n"
            "Tap 🤖 Exness Auto-Trade anytime to check your status or switch again.",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5switch_choose_bot":
        user_id = str(query.from_user.id)
        buttons = [
            [InlineKeyboardButton(bot["label"], callback_data=f"mt5switch_bot_{key}")]
            for key, bot in MT5_AUTOTRADE_BOTS.items()
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="mt5switch_menu")])
        await query.message.edit_text(
            "🎯 <b>Choose a bot:</b>\n\n" + "\n".join(
                f"{bot['label']} — {bot['description']}" for bot in MT5_AUTOTRADE_BOTS.values()
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("mt5switch_bot_"):
        user_id = str(query.from_user.id)
        bot_key = data.replace("mt5switch_bot_", "")
        if bot_key not in MT5_AUTOTRADE_BOTS:
            return
        buttons = [
            [InlineKeyboardButton(PAIR_CONFIG[p]["display"], callback_data=f"mt5switch_pair_{bot_key}_{p}")]
            for p in MT5_AUTOTRADE_PAIRS
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="mt5switch_choose_bot")])
        await query.message.edit_text(
            f"✅ {MT5_AUTOTRADE_BOTS[bot_key]['label']} selected.\n\n"
            f"📌 <b>Now choose which pair to trade it on:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("mt5switch_pair_"):
        user_id = str(query.from_user.id)
        remainder = data.replace("mt5switch_pair_", "")
        # bot_key values themselves contain underscores (e.g.
        # "aggressive_scalper"), so a naive split on "_" would break -
        # match against the known bot keys directly instead.
        bot_key = next((k for k in MT5_AUTOTRADE_BOTS if remainder.startswith(k + "_")), None)
        pair_key = remainder[len(bot_key) + 1:] if bot_key else None
        if not bot_key or pair_key not in MT5_AUTOTRADE_PAIRS:
            return
        upsert_mt5_autotrade_account(user_id, {"bot_choice": bot_key, "pair_choice": pair_key})
        await query.message.edit_text(
            f"✅ <b>Switched to {MT5_AUTOTRADE_BOTS[bot_key]['label']} on "
            f"{PAIR_CONFIG[pair_key]['display']}.</b>\n\n"
            f"Tap 🤖 Exness Auto-Trade anytime to check your status or switch again.",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5settings_lot":
        user_id = str(query.from_user.id)
        user_modes[user_id] = "mt5_awaiting_lot_value"
        await query.message.edit_text(
            "📏 Send your desired <b>lot size</b> (e.g. 0.01):",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "derivauto_menu":
        user_id = str(query.from_user.id)
        account = get_deriv_account(user_id)
        autotrade_on = bool(account.get("deriv_autotrade_enabled")) if account else False

        buttons = [
            [InlineKeyboardButton("🎯 Pick a Bot", callback_data="derivauto_choose_bot")],
            [InlineKeyboardButton("🚀 Account Flip", callback_data="derivauto_account_flip")],
        ]
        if autotrade_on:
            buttons.append([InlineKeyboardButton("🔴 Turn Bot OFF", callback_data="derivauto_turnoff")])

        await query.message.edit_text(
            "🎲 <b>How should Deriv Auto-Trade decide your trades?</b>\n\n"
            "🎯 <b>Pick a Bot</b> — choose Aggressive or Conservative "
            "and one Volatility index for it to trade.\n\n"
            "🚀 <b>Account Flip</b> — its own standalone price-action "
            "strategy that layers in bigger stakes while winning, with "
            "a trailing stop across the whole stack. Higher risk.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data == "derivauto_turnoff":
        user_id = str(query.from_user.id)
        update_deriv_account_fields(user_id, {"deriv_autotrade_enabled": False})
        await query.message.edit_text(
            "🛑 <b>Auto-Trade turned off.</b>\n\n"
            "Your bot/mode settings are kept, so turning it back on "
            "later won't need reconfiguring. Check status anytime from "
            "🔗 Connect Deriv.",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "derivauto_turnon":
        user_id = str(query.from_user.id)
        account = get_deriv_account(user_id)
        bot_choice = account.get("deriv_bot_choice") if account else None
        if bot_choice not in DERIV_AUTOTRADE_BOTS and bot_choice != "account_flip":
            # Never configured before - can't just flip it on, needs a mode picked first.
            await query.message.edit_text(
                "🎯 <b>Pick a mode first:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎯 Pick a Bot", callback_data="derivauto_choose_bot")],
                    [InlineKeyboardButton("🚀 Account Flip", callback_data="derivauto_account_flip")],
                ])
            )
            return
        update_deriv_account_fields(user_id, {"deriv_autotrade_enabled": True})
        mode_label = (
            f"{DERIV_AUTOTRADE_BOTS[bot_choice]['label']} Bot" if bot_choice in DERIV_AUTOTRADE_BOTS
            else "🚀 Account Flip"
        )
        await query.message.edit_text(
            f"✅ <b>Auto-Trade turned back on.</b>\n\n"
            f"Resumed with your saved settings: {mode_label}.\n\n"
            f"To change bot, tap ⚙️ Manage Bot below.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Manage Bot", callback_data="derivauto_menu")]
            ])
        )
        return

    if data == "derivauto_choose_bot":
        user_id = str(query.from_user.id)
        buttons = [
            [InlineKeyboardButton(bot["label"], callback_data=f"derivauto_bot_{key}")]
            for key, bot in DERIV_AUTOTRADE_BOTS.items()
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="derivauto_menu")])
        await query.message.edit_text(
            "🎯 <b>Choose a bot:</b>\n\n" + "\n".join(
                f"{bot['label']} — {bot['description']}" for bot in DERIV_AUTOTRADE_BOTS.values()
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("derivauto_bot_"):
        user_id = str(query.from_user.id)
        bot_key = data.replace("derivauto_bot_", "")
        if bot_key not in DERIV_AUTOTRADE_BOTS:
            return
        buttons = [
            [InlineKeyboardButton(SYNTHETIC_CONFIG[p]["display"], callback_data=f"derivauto_pair_{bot_key}_{p}")]
            for p in DERIV_AUTOTRADE_PAIRS
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="derivauto_choose_bot")])
        await query.message.edit_text(
            f"✅ {DERIV_AUTOTRADE_BOTS[bot_key]['label']} selected.\n\n"
            f"📌 <b>Now choose which index to trade it on:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("derivauto_pair_"):
        user_id = str(query.from_user.id)
        remainder = data.replace("derivauto_pair_", "")
        bot_key = next((k for k in DERIV_AUTOTRADE_BOTS if remainder.startswith(k + "_")), None)
        pair_key = remainder[len(bot_key) + 1:] if bot_key else None
        if not bot_key or pair_key not in DERIV_AUTOTRADE_PAIRS:
            return
        tier_buttons = [
            [InlineKeyboardButton(
                f"${t['stake']} | Risk ${t['risk']} → Win ${t['win']}",
                callback_data=f"derivbotstake_{bot_key}_{pair_key}_{t['stake']}"
            )]
            for t in STAKE_TIERS
        ]
        tier_buttons.append([InlineKeyboardButton("⬅️ Back", callback_data=f"derivauto_bot_{bot_key}")])
        await query.message.edit_text(
            f"✅ {DERIV_AUTOTRADE_BOTS[bot_key]['label']} on "
            f"{SYNTHETIC_CONFIG[pair_key]['display']} selected.\n\n"
            f"📌 <b>Now choose the stake to use on every trade:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(tier_buttons)
        )
        return

    if data.startswith("derivbotstake_"):
        user_id = str(query.from_user.id)
        remainder = data.replace("derivbotstake_", "")
        bot_key = next((k for k in DERIV_AUTOTRADE_BOTS if remainder.startswith(k + "_")), None)
        rest = remainder[len(bot_key) + 1:] if bot_key else None
        pair_key, stake_str = rest.rsplit("_", 1) if rest else (None, None)
        if not bot_key or pair_key not in DERIV_AUTOTRADE_PAIRS:
            return
        tier = next((t for t in STAKE_TIERS if t["stake"] == float(stake_str)), None)
        if not tier:
            return

        update_deriv_account_fields(user_id, {
            "deriv_bot_choice": bot_key,
            "deriv_pair_choice": pair_key,
            "deriv_autotrade_enabled": True,
            "deriv_bot_stake": tier["stake"],
            "deriv_bot_risk": tier["risk"],
            "deriv_bot_win": tier["win"],
        })
        await query.message.edit_text(
            f"✅ <b>Auto-Trade connected successfully.</b>\n\n"
            f"Mode: {DERIV_AUTOTRADE_BOTS[bot_key]['label']}\n"
            f"Index: {SYNTHETIC_CONFIG[pair_key]['display']}\n"
            f"Stake: ${tier['stake']} | Risk: ${tier['risk']} → Win: ${tier['win']}\n\n"
            f"Check status or switch modes anytime from 🔗 Connect Deriv.",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "derivauto_account_flip":
        user_id = str(query.from_user.id)
        await query.message.edit_text(
            "⚠️ <b>Account Flip — Read Before Enabling</b>\n\n"
            "This mode compounds stake size while a trade is winning, "
            "adding bigger positions as it moves further your way. It "
            "is built to grow fast, not to protect capital the way "
            "Pick a Bot does. A sudden reversal after several layers "
            "have stacked can hand back a large chunk of the run's "
            "gains at once. Every layer beyond the first only exists "
            "because the trade is already in profit — nothing is "
            "added while losing — but this is still meaningfully "
            "higher-risk and is not suitable for money you aren't "
            "prepared to see swing hard in both directions.\n\n"
            "It also runs its own standalone strategy — pure price "
            "action (Engulfing / Pin Bar / Inside Bar Breakout).",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ I Understand, Continue", callback_data="derivauto_flip_accept")],
                [InlineKeyboardButton("⬅️ Back", callback_data="derivauto_menu")],
            ])
        )
        return

    if data == "derivauto_flip_accept":
        user_id = str(query.from_user.id)
        deriv_flip_signup_state[user_id] = {}
        buttons = [
            [InlineKeyboardButton(SYNTHETIC_CONFIG[p]["display"], callback_data=f"derivauto_flippair_{p}")]
            for p in DERIV_AUTOTRADE_PAIRS
        ]
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="derivauto_account_flip")])
        await query.message.edit_text(
            "🚀 <b>Account Flip</b> — choose the one index it should trade:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    if data.startswith("derivauto_flippair_"):
        user_id = str(query.from_user.id)
        pair_key = data.replace("derivauto_flippair_", "")
        if pair_key not in DERIV_AUTOTRADE_PAIRS:
            return
        deriv_flip_signup_state.setdefault(user_id, {})["pair_choice"] = pair_key
        user_modes[user_id] = "deriv_awaiting_flip_base"
        await query.message.edit_text(
            f"✅ {SYNTHETIC_CONFIG[pair_key]['display']} selected.\n\n"
            f"🚀 <b>Account Flip — last step</b>\n\n"
            f"Send your <b>starting stake</b> in $ (e.g. 10):",
            parse_mode=ParseMode.HTML
        )
        return

    if data == "mt5settings_risk":
        user_id = str(query.from_user.id)
        user_modes[user_id] = "mt5_awaiting_risk_value"
        await query.message.edit_text(
            "📊 Send your desired <b>risk %</b> per trade (e.g. 1 for 1%):",
            parse_mode=ParseMode.HTML
        )
        return

    if data.startswith("newsmenu_"):
        user_id = str(query.from_user.id)
        choice = data.replace("newsmenu_", "")

        if choice == "calendar":
            await query.message.edit_text(
                "⏳ <b>Fetching today's economic calendar...</b>",
                parse_mode=ParseMode.HTML
            )

            events = get_todays_high_impact_events()
            if not events:
                next_date = get_next_high_impact_event_date()
                no_news_text = (
                    "📅 <b>No high-impact USD, EUR, GBP, or JPY news "
                    "scheduled for today.</b>\n\n"
                )
                if next_date:
                    no_news_text += f"Next one: <b>{next_date}</b>.\n\n"
                no_news_text += (
                    "Try <b>News Breakdown</b> instead for a live read "
                    "on any specific pair or currency you're curious about."
                )
                await query.message.edit_text(
                    no_news_text,
                    parse_mode=ParseMode.HTML
                )
                return

            list_id = store_news_events_batch(events)
            today_display = datetime.utcnow().strftime("%A, %d %B %Y")
            now_utc = datetime.utcnow()
            user_offset = get_user_utc_offset_minutes(user_id)
            if user_offset is None:
                user_offset = DEFAULT_UTC_OFFSET_MINUTES
            tz_label = format_gmt_label(user_offset)

            released_count = 0
            buttons = []
            for i, event in enumerate(events):
                flag = {"USD": "🇺🇸", "EUR": "🇪🇺", "GBP": "🇬🇧", "JPY": "🇯🇵"}.get(event["currency"], "🌍")
                status_label, is_released = format_event_status(event, now_utc)
                if is_released:
                    released_count += 1
                local_time = format_local_time(event.get("event_dt_utc", ""), user_offset)
                label = f"{flag} {event['title'][:32]}"
                if local_time:
                    label += f" ({local_time})"
                if status_label:
                    label += f" — {status_label}"
                buttons.append([InlineKeyboardButton(label, callback_data=f"newsevent_{list_id}_{i}")])

            upcoming_count = len(events) - released_count
            if upcoming_count > 0:
                summary_line = f"✅ {released_count} released • 🔜 {upcoming_count} upcoming"
            else:
                summary_line = f"✅ All {released_count} released — nothing left scheduled today"

            await query.message.edit_text(
                f"📅 <b>High-Impact News — {today_display}</b>\n"
                f"{summary_line}\n"
                f"<i>Times shown in your local time ({tz_label})</i>\n\n"
                f"Tap any event below for a direct BUY/SELL call, "
                f"fundamentals-first:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        elif choice == "breakdown":
            user_modes[user_id] = "breakdown"
            await query.message.edit_text(
                "📚 <b>News Breakdown Mode Activated</b>\n\n"
                "Now type your market question below.\n\n"
                "<b>Examples:</b>\n"
                "• Analyze gold market today\n"
                "• BTCUSD outlook\n"
                "• GBPJPY market analysis\n"
                "• What is happening with oil today?",
                parse_mode=ParseMode.HTML
            )
            return

    if data.startswith("newsevent_"):
        user_id = str(query.from_user.id)

        if not is_verified(user_id):
            count = increment_trial(user_id)
            if count > FREE_TRIAL_LIMIT:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
                return

        try:
            remainder = data.replace("newsevent_", "")
            list_id, idx_str = remainder.rsplit("_", 1)
            idx = int(idx_str)
            batch = get_news_events_batch(list_id)
            event = batch[idx] if batch else None
            if event is None:
                raise IndexError
        except (ValueError, IndexError, KeyError):
            await query.message.reply_text(
                "⚠️ This news list has expired - tap 📰 News again for a fresh list.",
                parse_mode=ParseMode.HTML
            )
            return

        await send_news_direction_analysis(
            context.bot, query.message.chat_id, event,
            batch_events=batch
        )

        if not is_verified(user_id):
            remaining = trial_remaining(user_id)
            if remaining <= 0:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

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
                        "📰 <b>News-Driven Calls</b> — Direct BUY/SELL calls "
                        "on any pair you ask about\n\n"
                        "📈 <b>Technical Analysis</b> — Professional "
                        "grade insights powered by AI\n\n"
                        "🤖 <b>Exness Auto-Trade</b> — Subscribe to auto-trade "
                        "directly on your own Exness MT5/MT4 account\n\n"
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
                        "📰 <b>News-Driven Calls</b> — Direct BUY/SELL calls "
                        "on any pair you ask about\n\n"
                        "📈 <b>Technical Analysis</b> — Professional "
                        "grade insights powered by AI\n\n"
                        "🤖 <b>Exness Auto-Trade</b> — Subscribe to auto-trade "
                        "directly on your own Exness MT5/MT4 account"
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
                    "📰 <b>News</b> — Get a direct call on high-impact news"
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

        signal_key = data.replace("chantrade_", "")
        tapping_user_id = str(query.from_user.id)

        shared_context = channel_signal_context.get(signal_key)
        if not shared_context:
            try:
                await send_and_auto_delete(
                    context.bot, int(tapping_user_id),
                    "⚠️ <b>This signal has expired.</b>\n\n"
                    "Signals are only tradeable for 1 hour after "
                    "they're posted. Tap 📊 Signal below to get a "
                    "fresh, live one instead.",
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
                    f"📰 <b>News</b> — Get a direct call on high-impact news\n\n"
                    f"🔗 <b>Connect Deriv</b> — Link your Deriv account to trade "
                    f"signals directly, manually or fully automatic\n\n"
                    f"🤖 <b>Exness Auto-Trade</b> — Subscribe to auto-trade "
                    f"directly on your own Exness MT5/MT4 account\n\n"
                    f"<i>All four buttons are at the bottom of your screen 👇</i>"
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
                    f"📰 <b>News</b> — Get a direct call on high-impact news\n\n"
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
                f"📰 <b>News</b> — Get a direct call on high-impact news\n\n"
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
                "want to trade it. You can switch to Auto Trade anytime "
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
                "🤖 <b>Auto Trade Setup — Step 1 of 2</b>\n\n"
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
                "🤖 <b>Auto Trade Setup — Step 2 of 2</b>\n\n"
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
                "Tap 🔗 Connect Deriv and set up Auto Trade again.",
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
                "⚠️ <b>Couldn't save your Auto Trade settings right "
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
                "✅ <b>Auto Trade is ON.</b>\n\n"
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

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Only acts when a location arrives WHILE a user is in the
    awaiting_timezone_location mode (triggered by their first "News"
    tap) - an unsolicited location share otherwise (e.g. someone just
    fooling around with the attachment menu) is ignored entirely, per
    explicit instruction that this is a one-time optional setup step,
    not a general location-tracking feature.
    """
    if update.message is None or update.message.from_user is None or update.message.location is None:
        return

    user_id = str(update.message.from_user.id)
    if user_modes.get(user_id) != "awaiting_timezone_location":
        return

    loc = update.message.location
    offset_minutes = lookup_utc_offset_from_coordinates(loc.latitude, loc.longitude)

    if offset_minutes is None:
        offset_minutes = DEFAULT_UTC_OFFSET_MINUTES
        note = (
            "⚠️ Couldn't detect your timezone automatically - defaulting "
            "to West Africa Time (GMT+1). You can retry anytime by "
            "tapping 📰 News again."
        )
    else:
        note = f"✅ Got it — your news times will now show in <b>{format_gmt_label(offset_minutes)}</b>."

    save_user_utc_offset_minutes(user_id, offset_minutes)
    user_modes[user_id] = None

    sent_confirm = await update.message.reply_text(
        note, parse_mode=ParseMode.HTML, reply_markup=main_keyboard
    )
    schedule_auto_delete(sent_confirm.chat_id, sent_confirm.message_id)

    sent_news_menu = await update.message.reply_text(
        "📰 <b>News</b>\n\nWhat would you like?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 News Calendar", callback_data="newsmenu_calendar")],
            [InlineKeyboardButton("📚 News Breakdown", callback_data="newsmenu_breakdown")],
        ])
    )
    schedule_auto_delete(sent_news_menu.chat_id, sent_news_menu.message_id)


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

    if user_modes.get(user_id) == "awaiting_timezone_location":
        # Any text here (in practice, the "Skip" button) means: use
        # the default WAT offset and stop asking - saved explicitly so
        # this prompt never repeats for this user again.
        save_user_utc_offset_minutes(user_id, DEFAULT_UTC_OFFSET_MINUTES)
        user_modes[user_id] = None
        sent_skip = await update.message.reply_text(
            "✅ Using West Africa Time (GMT+1) for your news times.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        schedule_auto_delete(sent_skip.chat_id, sent_skip.message_id)

        sent_news_menu = await update.message.reply_text(
            "📰 <b>News</b>\n\nWhat would you like?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📅 News Calendar", callback_data="newsmenu_calendar")],
                [InlineKeyboardButton("📚 News Breakdown", callback_data="newsmenu_breakdown")],
            ])
        )
        schedule_auto_delete(sent_news_menu.chat_id, sent_news_menu.message_id)
        return

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
        # connect -> trade flow stays one continuous motion. If there's
        # no pending trade (a plain, direct Connect Deriv link with no
        # signal in context), the confirmation above is the whole
        # story - nothing further to ask here.
        pending_trade = pending_trades.get(user_id)
        if pending_trade:
            await send_tier_selection(context.bot, user_id, pending_trade)
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

        # ADDED: manual/DM-generated signals were never logged at all -
        # only the scheduled auto-post path was, meaning per-strategy
        # performance tracking would have silently missed every signal
        # a user requested directly. Same log_signal call as the
        # scheduled path, just added here too for complete data.
        if signal_data is not None:
            log_signal(signal_data, source="manual")

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
                        f"📰 <b>News</b> — Get a direct call on high-impact news",
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
        elif signal == "NO_DATA_AVAILABLE":
            sent_no_data = await update.message.reply_text(
                "⚠️ <b>No live signal data available for this pair right now.</b>\n\n"
                "Our data provider doesn't currently support detailed "
                "chart history for this pair on our plan, so we can't "
                "generate a real, verified signal for it at the moment "
                "rather than guess.\n\n"
                "Try another pair from the list, or check back later.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_no_data.chat_id, sent_no_data.message_id)
        else:
            sent_fetch_failed = await update.message.reply_text(
                "⚠️ <b>Unable to fetch live market data.</b>\n"
                "Please try again shortly.",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_fetch_failed.chat_id, sent_fetch_failed.message_id)
        return

    if mode == "mt5_awaiting_account_number":
        # REDESIGNED per explicit instruction - saves to Supabase
        # IMMEDIATELY instead of only holding it in the in-memory
        # mt5_signup_state dict. A confirmed real case: a bot
        # redeploy happened while a customer was mid-signup, silently
        # wiping their in-progress state (user_modes/mt5_signup_state
        # are plain Python dicts, gone on every restart) - they typed
        # their server name into what looked like the right place,
        # but the bot had already forgotten they were mid-signup and
        # showed a generic "here's what you can do" fallback instead.
        # Saving each field the moment it's collected means this
        # survives a restart - see the recovery check further below.
        mt5_signup_state[user_id] = {"account_number": message.strip()}
        try:
            upsert_mt5_autotrade_account(user_id, {"account_number": message.strip()})
        except Exception as e:
            print(f"[MT5 SIGNUP] Couldn't persist account_number for {user_id}: {e}")
        user_modes[user_id] = "mt5_awaiting_password"
        sent_ask_password = await update.message.reply_text(
            "🔑 Now send your MT5/MT4 <b>password</b>.\n\n"
            "<i>Your message will be deleted immediately after this "
            "for your security.</i>",
            parse_mode=ParseMode.HTML
        )
        schedule_auto_delete(sent_ask_password.chat_id, sent_ask_password.message_id)
        return

    if mode == "mt5_awaiting_password":
        encrypted_pw = encrypt_credential(message.strip())
        mt5_signup_state.setdefault(user_id, {})["password"] = message.strip()
        try:
            upsert_mt5_autotrade_account(user_id, {"encrypted_password": encrypted_pw})
        except Exception as e:
            print(f"[MT5 SIGNUP] Couldn't persist password for {user_id}: {e}")
        user_modes[user_id] = "mt5_awaiting_server"
        try:
            await update.message.delete()
        except Exception as e:
            print(f"[MT5 SIGNUP] Couldn't delete password message for {user_id}: {e}")
        sent_ask_server = await update.message.reply_text(
            "🌐 Now send your <b>server</b> name (e.g. Exness-Real4):",
            parse_mode=ParseMode.HTML
        )
        schedule_auto_delete(sent_ask_server.chat_id, sent_ask_server.message_id)
        return

    if mode == "mt5_awaiting_server":
        mt5_signup_state.setdefault(user_id, {})["server"] = message.strip()
        try:
            upsert_mt5_autotrade_account(user_id, {"server": message.strip()})
        except Exception as e:
            print(f"[MT5 SIGNUP] Couldn't persist server for {user_id}: {e}")
        user_modes[user_id] = "mt5_awaiting_name"
        sent_ask_name = await update.message.reply_text(
            "📝 Send an account <b>nickname</b> (e.g. \"My Exness\"):",
            parse_mode=ParseMode.HTML
        )
        schedule_auto_delete(sent_ask_name.chat_id, sent_ask_name.message_id)
        return

    if mode == "mt5_awaiting_name":
        # Nickname is now COMPULSORY, per explicit instruction - reject
        # empty input and ask again, rather than silently accepting
        # blank/skip like before.
        account_name = message.strip()
        if not account_name or account_name.lower() == "skip":
            sent_name_required = await update.message.reply_text(
                "📝 An account nickname is required now - send one "
                "(e.g. \"My Exness\"):",
                parse_mode=ParseMode.HTML
            )
            schedule_auto_delete(sent_name_required.chat_id, sent_name_required.message_id)
            return

        # Pulls account_number/password/server fresh from Supabase
        # rather than the in-memory mt5_signup_state dict - per
        # explicit instruction, since that dict may be empty here if
        # a restart happened between an earlier step and this one,
        # even though every field was already safely persisted above.
        user_modes[user_id] = None
        saved_account = get_mt5_autotrade_account(user_id) or {}
        account_number = saved_account.get("account_number") or mt5_signup_state.get(user_id, {}).get("account_number")
        server = saved_account.get("server") or mt5_signup_state.get(user_id, {}).get("server")
        raw_password = mt5_signup_state.get(user_id, {}).get("password")
        if not raw_password and saved_account.get("encrypted_password"):
            try:
                raw_password = decrypt_credential(saved_account["encrypted_password"])
            except Exception as e:
                print(f"[MT5 SIGNUP] Couldn't decrypt saved password for {user_id}: {e}")

        if not account_number or not raw_password or not server:
            await update.message.reply_text(
                "⚠️ <b>Something's missing to finish connecting.</b>\n\n"
                "Tap 🤖 Exness Auto-Trade to pick up right where you left off.",
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )
            return

        mt5_signup_state.pop(user_id, None)

        wait_connect = await update.message.reply_text(
            "🔗 <b>Connecting your MT5/MT4 account...</b> this can take up to a minute.",
            parse_mode=ParseMode.HTML
        )

        account_id, error = await provision_mt5_account(
            account_number, raw_password, server,
            account_name=account_name
        )

        if error:
            await wait_connect.edit_text(
                f"⚠️ <b>Couldn't connect that account.</b>\n\n{error}\n\n"
                f"Double-check your login, password, and server name, "
                f"then tap 🤖 Exness Auto-Trade to try again.",
                parse_mode=ParseMode.HTML
            )
            return

        upsert_mt5_autotrade_account(user_id, {
            "account_number": account_number,
            "encrypted_password": encrypt_credential(raw_password),
            "server": server,
            "account_name": account_name,
            "metaapi_account_id": account_id,
            "is_active": True,
        })

        await wait_connect.edit_text(
            "✅ <b>MT5/MT4 account connected!</b>\n\n"
            "Default mode: 0.01 lots per trade. Tap 🤖 Exness Auto-Trade "
            "anytime to change your lot size or switch to risk %.\n\n"
            "Auto-trading itself is still waiting on the trading "
            "strategy to be wired in - you'll be notified the moment "
            "it goes live.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

    if mode == "mt5_awaiting_lot_value":
        try:
            lot_size = float(message.strip())
        except ValueError:
            sent_bad_lot = await update.message.reply_text("⚠️ Please send a number, e.g. 0.01")
            schedule_auto_delete(sent_bad_lot.chat_id, sent_bad_lot.message_id)
            return
        user_modes[user_id] = None

        if mt5_signup_state.get(user_id, {}).get("flow") == "presetup":
            # Pre-payment setup flow - store the choice and move to the
            # final "ready to pay" step, rather than saving to the DB
            # yet (the account may not even exist for this user until
            # payment succeeds).
            mt5_signup_state[user_id]["risk_mode"] = "lot"
            mt5_signup_state[user_id]["lot_size"] = lot_size
            signup = mt5_signup_state[user_id]
            bot_choice = signup.get("bot_choice", "follow_channel")
            summary = (
                "📡 Following Channel Signals" if bot_choice == "follow_channel"
                else f"{MT5_AUTOTRADE_BOTS[bot_choice]['label']} on {PAIR_CONFIG[signup['pair_choice']]['display']}"
            )
            sent_ready = await update.message.reply_text(
                f"✅ <b>Setup complete:</b>\n\n"
                f"{summary}\n"
                f"Lot size: {lot_size}\n\n"
                f"Tap below to pay and unlock your MT5 connection:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ Continue to Payment", callback_data="mt5auto_continue")]
                ])
            )
            schedule_auto_delete(sent_ready.chat_id, sent_ready.message_id)
            return

        upsert_mt5_autotrade_account(user_id, {"risk_mode": "lot", "lot_size": lot_size})
        sent_lot_saved = await update.message.reply_text(
            f"✅ Lot size set to <b>{lot_size}</b> per trade.",
            parse_mode=ParseMode.HTML, reply_markup=main_keyboard
        )
        schedule_auto_delete(sent_lot_saved.chat_id, sent_lot_saved.message_id)
        return

    if mode == "mt5_awaiting_flip_base":
        signup = mt5_signup_state.setdefault(user_id, {})
        try:
            flip_base = float(message.strip())
            if flip_base <= 0:
                raise ValueError
        except ValueError:
            sent_bad = await update.message.reply_text("⚠️ Please send a positive number, e.g. 0.01")
            schedule_auto_delete(sent_bad.chat_id, sent_bad.message_id)
            return

        user_modes[user_id] = None
        pair_key = signup.get("pair_choice")
        defaults = get_account_flip_defaults(pair_key, flip_base)
        signup["flip_base_lot"] = flip_base
        signup.update(defaults)
        signup["risk_mode"] = "account_flip"
        signup["bot_choice"] = "account_flip"

        summary = (
            f"Pair: {PAIR_CONFIG.get(pair_key, {}).get('display', '—')}\n"
            f"Start: {flip_base} lots | +{defaults['flip_step']}/layer "
            f"(caps at {defaults['flip_max_lot']} after {defaults['flip_max_layers']} layers)\n"
            f"New layer every {defaults['flip_trigger_pips']} pips in profit\n"
            f"Trailing stop: {defaults['flip_trail_pips']} pips across the whole stack"
        )

        if signup.get("flow") == "switch":
            upsert_mt5_autotrade_account(user_id, {
                "bot_choice": "account_flip",
                "pair_choice": pair_key,
                "risk_mode": "account_flip",
                "flip_base_lot": flip_base,
                "flip_step": defaults["flip_step"],
                "flip_max_lot": defaults["flip_max_lot"],
                "flip_trigger_pips": defaults["flip_trigger_pips"],
                "flip_max_layers": defaults["flip_max_layers"],
                "flip_trail_pips": defaults["flip_trail_pips"],
                "flip_disclaimer_accepted": True,
            })
            sent_done = await update.message.reply_text(
                f"✅ <b>Switched to Account Flip.</b>\n\n{summary}\n\n"
                f"Tap 🤖 Exness Auto-Trade anytime to check status or switch again.",
                parse_mode=ParseMode.HTML, reply_markup=main_keyboard
            )
            schedule_auto_delete(sent_done.chat_id, sent_done.message_id)
            return

        # presetup flow - store and move to the "ready to pay" step,
        # same pattern as the lot/risk % paths above.
        sent_ready = await update.message.reply_text(
            f"✅ <b>Setup complete:</b>\n\n🚀 Account Flip\n{summary}\n\n"
            f"Tap below to pay and unlock your MT5 connection:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➡️ Continue to Payment", callback_data="mt5auto_continue")]
            ])
        )
        schedule_auto_delete(sent_ready.chat_id, sent_ready.message_id)
        return

    if mode == "deriv_awaiting_flip_base":
        signup = deriv_flip_signup_state.setdefault(user_id, {})
        try:
            flip_base = float(message.strip())
            if flip_base <= 0:
                raise ValueError
        except ValueError:
            sent_bad = await update.message.reply_text("⚠️ Please send a positive number, e.g. 10")
            schedule_auto_delete(sent_bad.chat_id, sent_bad.message_id)
            return

        user_modes[user_id] = None
        pair_key = signup.get("pair_choice")
        defaults = get_deriv_flip_defaults(flip_base)

        update_deriv_account_fields(user_id, {
            "deriv_bot_choice": "account_flip",
            "deriv_pair_choice": pair_key,
            "deriv_autotrade_enabled": True,
            "deriv_flip_base_stake": flip_base,
            "deriv_flip_step": defaults["flip_step"],
            "deriv_flip_max_stake": defaults["flip_max_stake"],
            "deriv_flip_trigger_amount": defaults["flip_trigger_amount"],
            "deriv_flip_max_layers": defaults["flip_max_layers"],
            "deriv_flip_trail_amount": defaults["flip_trail_amount"],
            "deriv_flip_disclaimer_accepted": True,
        })

        summary = (
            f"Index: {SYNTHETIC_CONFIG.get(pair_key, {}).get('display', '—')}\n"
            f"Start: ${flip_base} | +${defaults['flip_step']}/layer "
            f"(caps at ${defaults['flip_max_stake']} after {defaults['flip_max_layers']} layers)\n"
            f"New layer every ${defaults['flip_trigger_amount']} profit\n"
            f"Trailing stop: ${defaults['flip_trail_amount']} across the whole stack"
        )
        sent_done = await update.message.reply_text(
            f"✅ <b>Auto-Trade connected successfully.</b>\n\n🚀 Account Flip\n{summary}\n\n"
            f"Check status or switch modes anytime from 🔗 Connect Deriv.",
            parse_mode=ParseMode.HTML, reply_markup=main_keyboard
        )
        schedule_auto_delete(sent_done.chat_id, sent_done.message_id)
        return

    if mode == "mt5_awaiting_risk_value":
        try:
            risk_percent = float(message.strip())
        except ValueError:
            sent_bad_risk = await update.message.reply_text("⚠️ Please send a number, e.g. 1 for 1%")
            schedule_auto_delete(sent_bad_risk.chat_id, sent_bad_risk.message_id)
            return
        user_modes[user_id] = None

        if mt5_signup_state.get(user_id, {}).get("flow") == "presetup":
            mt5_signup_state[user_id]["risk_mode"] = "percent"
            mt5_signup_state[user_id]["risk_percent"] = risk_percent
            signup = mt5_signup_state[user_id]
            bot_choice = signup.get("bot_choice", "follow_channel")
            summary = (
                "📡 Following Channel Signals" if bot_choice == "follow_channel"
                else f"{MT5_AUTOTRADE_BOTS[bot_choice]['label']} on {PAIR_CONFIG[signup['pair_choice']]['display']}"
            )
            sent_ready = await update.message.reply_text(
                f"✅ <b>Setup complete:</b>\n\n"
                f"{summary}\n"
                f"Risk: {risk_percent}%\n\n"
                f"Tap below to pay and unlock your MT5 connection:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("➡️ Continue to Payment", callback_data="mt5auto_continue")]
                ])
            )
            schedule_auto_delete(sent_ready.chat_id, sent_ready.message_id)
            return

        upsert_mt5_autotrade_account(user_id, {"risk_mode": "percent", "risk_percent": risk_percent})
        sent_risk_saved = await update.message.reply_text(
            f"✅ Risk set to <b>{risk_percent}%</b> per trade.",
            parse_mode=ParseMode.HTML, reply_markup=main_keyboard
        )
        schedule_auto_delete(sent_risk_saved.chat_id, sent_risk_saved.message_id)
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

        # NO outer retry loop here (there used to be one, 15s + 30s
        # waits on top of ask_gemini's own retry) - removed per
        # explicit instruction, same fix as generate_currency_direction
        # above: ask_gemini already retries Gemini internally AND
        # falls back to a different provider (OpenRouter). Stacking a
        # third retry layer on top just made a real, waiting user sit
        # through up to ~6 minutes for the same eventual failure
        # instead of finding out quickly.
        if response.strip() in KNOWN_AI_FAILURE_STRINGS:
            print(f"[BREAKDOWN] AI generation failed ('{response.strip()}')")

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
                    f"📰 <b>News</b> — Get a direct call on high-impact news",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
                schedule_auto_delete(sent_trial_notice.chat_id, sent_trial_notice.message_id)
            else:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

    # RECOVERY CHECK, per explicit instruction - catches exactly the
    # case that just happened for real: a customer's user_modes/
    # mt5_signup_state got wiped by a bot restart mid-signup (both are
    # in-memory only), so their next message (meant to answer the
    # bot's last question) matched no known mode and was about to
    # silently fall through to the generic "here's what you can do"
    # message below. If they have an active, unexpired subscription
    # with an incomplete MT5 connection, resume them at whichever
    # field is genuinely still missing - checked against Supabase
    # (persisted, survives restarts), not the volatile in-memory dicts.
    account = get_mt5_autotrade_account(user_id)
    if account and not account.get("metaapi_account_id"):
        expiry = None
        if account.get("subscription_expires_at"):
            try:
                expiry = datetime.fromisoformat(
                    account["subscription_expires_at"].replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                expiry = None
        if expiry is not None and datetime.utcnow() < expiry:
            if not account.get("account_number"):
                user_modes[user_id] = "mt5_awaiting_account_number"
                sent_recover = await update.message.reply_text(
                    "👋 <b>Let's pick up where you left off.</b>\n\n"
                    "Send your Exness <b>account number</b>:",
                    parse_mode=ParseMode.HTML
                )
            elif not account.get("encrypted_password"):
                user_modes[user_id] = "mt5_awaiting_password"
                sent_recover = await update.message.reply_text(
                    "👋 <b>Let's pick up where you left off.</b>\n\n"
                    "🔑 Now send your MT5/MT4 <b>password</b>.\n\n"
                    "<i>Your message will be deleted immediately after "
                    "this for your security.</i>",
                    parse_mode=ParseMode.HTML
                )
            elif not account.get("server"):
                user_modes[user_id] = "mt5_awaiting_server"
                sent_recover = await update.message.reply_text(
                    "👋 <b>Let's pick up where you left off.</b>\n\n"
                    "🌐 Now send your <b>server</b> name (e.g. Exness-Real4):",
                    parse_mode=ParseMode.HTML
                )
            else:
                user_modes[user_id] = "mt5_awaiting_name"
                sent_recover = await update.message.reply_text(
                    "👋 <b>Let's pick up where you left off.</b>\n\n"
                    "📝 Send an account <b>nickname</b> (e.g. \"My Exness\"):",
                    parse_mode=ParseMode.HTML
                )
            schedule_auto_delete(sent_recover.chat_id, sent_recover.message_id)
            return

    sent_fallback = await update.message.reply_text(
        "👇 <b>Here's what you can do:</b>\n\n"
        "📊 <b>Signal</b> — Get a live trading signal right now\n\n"
        "📰 <b>News</b> — Get a direct call on high-impact news\n\n"
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

    try:
        pair_name = PAIR_CONFIG.get(pair_keyword, {}).get("pair_name", pair_keyword.upper())
        if has_open_signal_for_pair(pair_name):
            print(
                f"[AUTO SIGNAL] ⏸️ {pair_keyword.upper()} skipped — a "
                f"previous {pair_name} signal hasn't closed yet."
            )
            return

        # Catches a position REGARDLESS of who opened it - the bot's own
        # prior signal (already covered above via signal_log) OR a trade
        # placed manually, which never touches signal_log at all. Per
        # explicit instruction, after a real confirmed regression where a
        # manually-placed BTCUSD trade didn't stop the bot's own scheduled
        # BTCUSD signal from firing on top of it.
        mt5_symbol = PAIR_CONFIG.get(pair_keyword, {}).get("mt5_symbol")
        if mt5_symbol and await has_real_open_mt5_position(mt5_symbol):
            print(
                f"[AUTO SIGNAL] ⏸️ {pair_keyword.upper()} skipped — the "
                f"account already has a live {mt5_symbol} position open "
                f"(placed manually or otherwise), regardless of signal_log."
            )
            return

        image_file_id, direction, signal, signal_data = (
            await build_signal_response(pair_keyword, user_id=None)
        )

        if signal_data is None:
            if signal == "MARKET_CLOSED":
                print(f"[AUTO SIGNAL] ⏸️ {pair_keyword.upper()} skipped — forex market closed.")
            elif signal == "NO_DATA_AVAILABLE":
                print(f"[AUTO SIGNAL] ⏸️ {pair_keyword.upper()} skipped — no real data source available (should not be scheduled).")
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

                sent_msg = await bot.send_photo(
                    chat_id=channel_id,
                    photo=image_file_id,
                    caption=signal,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )
                log_channel_message(signal_id, sent_msg.chat_id, sent_msg.message_id)
                print(
                    f"[AUTO SIGNAL] ✅ {pair_keyword.upper()} "
                    f"posted to {channel_id}"
                )

            except Exception as e:
                print(f"[AUTO SIGNAL] ❌ Failed for {channel_id}: {e}")
    except Exception as e:
        # CATCH-ALL: per explicit instruction, after a real incident
        # where a screenshot of this exact job's logs cut off right
        # after the "firing at..." line with no way to tell what
        # happened next. Every ANTICIPATED skip reason above already
        # had clear logging - what was missing was a guarantee for
        # the UNANTICIPATED case: if anything inside build_signal_
        # response (or anywhere else in this block) throws for a
        # reason not already handled, this now prints ONE unmistakable
        # final line naming the pair and the real exception, instead
        # of relying on the job scheduler's own generic traceback dump
        # (which is exactly the kind of thing that's easy to scroll
        # past or crop out of a screenshot). This does not change
        # WHETHER a crash can happen - only guarantees it is never
        # silent or ambiguous when it does.
        print(f"[AUTO SIGNAL] ❌ CRASHED for {pair_keyword.upper()}: {e}")

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

async def post_midday_signal(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every day at 13:00 UTC (2PM Lagos). Looks up today's pair
    from MIDDAY_PAIR_BY_WEEKDAY - a different forex major each
    weekday (EURUSD Monday, GBPUSD Tuesday, GBPJPY Wednesday), no
    post at all on Thursday (a volatility/synthetic index posts
    instead that day - see SYNTHETIC_SCHEDULE's thursday_only entry)
    or Friday (no midday post that day, per explicit instruction),
    and BTCUSD on weekends since forex is closed.
    """
    weekday = datetime.utcnow().weekday()  # 0=Monday ... 6=Sunday
    pair_keyword = MIDDAY_PAIR_BY_WEEKDAY.get(weekday)
    if not pair_keyword:
        return
    await _post_signal_for_pair(context.bot, pair_keyword)

async def post_evening_signal(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every day at 19:00 UTC (8PM Lagos) - moved from the previous
    17:00 UTC (6PM Lagos) per explicit instruction, deliberately
    spaced apart from the new 13:00 UTC (2PM Lagos) midday slot so the
    two don't feel clustered together ("too much noise"). Looks up
    today's pair from EVENING_PAIR_BY_WEEKDAY - None on Sat/Sun, since
    those days only get the volatility-index slot in the evening, not
    a forex/crypto signal.
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

    # Unique per-signal key, per explicit instruction - CONFIRMED REAL
    # BUG (real user report + screenshot): the old bare index_key
    # approach let every new signal for the same index silently
    # overwrite the previous one's stored data, so an old message's
    # "Trade This Signal" button (from hours or days earlier) could
    # execute a completely different, newer signal's numbers without
    # anyone knowing. This timestamp suffix gives every signal its
    # own permanent, never-overwritten row - see ChannelSignalContext
    # Store's docstring above for the full explanation.
    signal_key = f"{index_key}_{int(time.time())}"
    trade_context["index_key"] = index_key
    channel_signal_context[signal_key] = trade_context

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
                        url=f"https://t.me/{BOT_USERNAME}?start=chantrade_{signal_key}"
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
    #
    # AUTO_COPY_EXCLUDED_INDICES check - per explicit instruction,
    # R_75's Deriv-enforced x400 multiplier floor caps the widest
    # possible stop distance at 0.25% of price no matter the stake or
    # dollar risk chosen, which real trade history confirmed as the
    # actual driver of auto-copy's poor win rate. This ONLY skips
    # automatic execution - the channel post above and the manual
    # "Trade This Signal" button are both unaffected, so anyone who
    # wants to accept that risk themselves still can.
    if index_key not in AUTO_COPY_EXCLUDED_INDICES:
        asyncio.create_task(run_auto_copy_for_signal(bot, trade_context))
    else:
        print(f"[AUTO-COPY] Skipping auto-copy for {index_key.upper()} - excluded (Deriv multiplier floor too tight for a survivable stop)")

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

async def _run_broadcast(bot, message_text, admin_chat_id, button_label=None, destination="exness", photo_file_ids=None):
    user_ids = await get_all_known_user_ids()
    total = len(user_ids)
    print(f"[BROADCAST] Starting send to {total} users")

    sent = 0
    failed = 0

    # A button and the persistent reply keyboard can't both attach to
    # the same message (Telegram only allows one reply_markup type) -
    # when a button's requested, this message uses that instead of
    # refreshing the keyboard, per explicit instruction to support a
    # "Try it now"-style CTA on broadcasts, for whichever destination
    # the admin picked (see start()'s goto_ branch for the full list).
    markup = main_keyboard
    if button_label:
        # "nexora" is a plain link straight into the bot - no deep-link
        # payload, no specific feature - just "go to Nexora AI" itself.
        button_url = (
            f"https://t.me/{BOT_USERNAME}"
            if destination == "nexora"
            else f"https://t.me/{BOT_USERNAME}?start=goto_{destination}"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(button_label, url=button_url)
        ]])

    # Per explicit instruction: two genuinely different paths now,
    # picked automatically based on whether an explicit button was
    # requested (checking button_label specifically, not markup -
    # markup defaults to the persistent keyboard refresh even with no
    # button asked for, and that default refresh is a nice-to-have
    # worth skipping for a true unified album, not a reason to force
    # the split - it'll refresh again on the user's next interaction
    # regardless). Previously this ALWAYS split into "first photo
    # separate + trailing group" no matter what, which is why removing
    # a button alone didn't change anything (confirmed real gap).
    #
    # NO explicit button + multiple photos: one single, TRUE unified
    # send_media_group call, caption on the first item - Telegram
    # shows that as one shared caption for the whole album, a genuine
    # single post with every photo together.
    #
    # Explicit button requested (any photo count) OR only 1 photo:
    # unavoidable split - Telegram's send_media_group has zero
    # reply_markup support at all (confirmed directly from Telegram's
    # own bot-api issue tracker), so a button can only ever attach to
    # a single, standalone photo message.
    first_photo = photo_file_ids[0] if photo_file_ids else None
    extra_photos = photo_file_ids[1:] if photo_file_ids and len(photo_file_ids) > 1 else []
    use_unified_album = photo_file_ids and len(photo_file_ids) > 1 and not button_label

    for uid in user_ids:
        try:
            if use_unified_album:
                media = [InputMediaPhoto(photo_file_ids[0], caption=message_text, parse_mode=ParseMode.HTML)]
                media += [InputMediaPhoto(fid) for fid in photo_file_ids[1:]]
                await bot.send_media_group(chat_id=int(uid), media=media)
            elif first_photo:
                await bot.send_photo(
                    chat_id=int(uid),
                    photo=first_photo,
                    caption=message_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )
                if extra_photos:
                    await bot.send_media_group(
                        chat_id=int(uid),
                        media=[InputMediaPhoto(fid) for fid in extra_photos]
                    )
            else:
                await bot.send_message(
                    chat_id=int(uid),
                    text=message_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup
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

async def discoversymbols_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    One-time diagnostic, per explicit instruction - before adding the
    1-second Volatility indices or Step Index to SYNTHETIC_CONFIG
    (which controls REAL auto-trading), get their ACTUAL symbol codes
    directly from Deriv's own active_symbols API instead of guessing.
    This is a read-only call - zero trading risk, just a lookup.
    Reuses the exact same DERIV_SERVICE_TOKEN connection pattern
    already proven for candle fetching (get_synthetic_candles, etc.).
    Admin-only, remove once the real codes are confirmed and added.
    """
    user_id = str(update.message.from_user.id)
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return

    if not DERIV_SERVICE_TOKEN:
        await update.message.reply_text("No DERIV_SERVICE_TOKEN set - can't run this.")
        return

    await update.message.reply_text("Looking up real symbol codes from Deriv...")

    try:
        accounts_data = await deriv_get_options_accounts(DERIV_SERVICE_TOKEN)
        if not accounts_data:
            await update.message.reply_text("Couldn't reach Deriv accounts.")
            return
        accounts_list = accounts_data.get("data")
        if not isinstance(accounts_list, list):
            accounts_list = accounts_data.get("accounts")
        if not accounts_list:
            await update.message.reply_text("Couldn't read the accounts list.")
            return

        account_id = (
            accounts_list[0].get("account_id")
            or accounts_list[0].get("loginid")
            or accounts_list[0].get("id")
        )
        ws_url = await deriv_get_otp_url(DERIV_SERVICE_TOKEN, account_id)
        if not ws_url:
            await update.message.reply_text("Couldn't establish a connection to Deriv.")
            return

        async with websockets.connect(ws_url, open_timeout=10, close_timeout=5) as ws:
            await ws.send(json.dumps({"active_symbols": "brief", "product_type": "basic"}))
            response = json.loads(await ws.recv())

        symbols = response.get("active_symbols", [])
        if not symbols:
            await update.message.reply_text(f"Unexpected response: {response}")
            return

        matches = [
            s for s in symbols
            if "1s" in s.get("display_name", "").lower().replace(" ", "")
            or "step" in s.get("display_name", "").lower()
        ]

        if not matches:
            await update.message.reply_text("No matching symbols found - check the raw log output.")
            print(f"[DISCOVER SYMBOLS] Full active_symbols response: {symbols}")
            return

        lines = [f"{m['display_name']}: {m['symbol']}" for m in matches]
        await update.message.reply_text(
            "✅ Confirmed real symbol codes:\n\n" + "\n".join(lines)
        )
    except Exception as e:
        await update.message.reply_text(f"Failed: {e}")
        print(f"[DISCOVER SYMBOLS] error: {e}")


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


async def testsignal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Forex/crypto equivalent of /testsynth above - same admin-only
    gate, same pattern. Lets the admin fire one real signal through
    _post_signal_for_pair (the actual function the channel_messages
    logging fix lives in) on demand, rather than waiting for the
    scheduled morning/evening job - per explicit instruction, so this
    can be verified BEFORE today's schedule posts anything on its own.
    """
    user_id = str(update.message.from_user.id)

    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /testsignal xauusd | btcusd | usoil | xagusd | eurusd | etc."
        )
        return

    pair_keyword = args[0].lower()
    if pair_keyword not in PAIR_CONFIG:
        await update.message.reply_text(
            f"Unknown pair '{pair_keyword}'. Use one of: "
            f"{', '.join(PAIR_CONFIG.keys())}"
        )
        return

    pair_name = PAIR_CONFIG[pair_keyword].get("pair_name", pair_keyword.upper())
    if has_open_signal_for_pair(pair_name):
        await update.message.reply_text(
            f"⚠️ {pair_name} already has an OPEN signal logged - "
            f"_post_signal_for_pair will skip posting, nothing will "
            f"appear in the channel. Close/clear that signal_log row "
            f"first, or test a different pair."
        )
        return

    await update.message.reply_text(f"Firing a fresh {pair_keyword.upper()} signal now...")
    await _post_signal_for_pair(context.bot, pair_keyword)
    await update.message.reply_text(
        "Done - check the channel, tap Get Your Own Signal, and confirm "
        "it opens the bot directly."
    )

# ============================================
# WELCOME (admin only)
# Posts the standard "Welcome to Nexora AI"
# message to all 3 channels on demand, with the
# same "Get Your Own Signal" deep-link button
# (get_channel_button()) used elsewhere. Same
# admin-only gate as the other test/admin
# commands above.
# ============================================

WELCOME_CHANNEL_MESSAGE = (
    "🚀 <b>Welcome to Nexora AI</b>\n\n"
    "Your <b>FREE</b> AI-powered trading assistant, available 24/7.\n\n"
    "✅ Instant Trading Signals\n"
    "📊 Smart Market Analysis\n"
    "📈 Forex, Gold, Crypto & Indices\n"
    "🔗 Connect Deriv account for Auto & Manual Trades\n"
    "🧠 AI-Powered Trade Insights\n"
    "⚡ Fast & Accurate Responses\n\n"
    "Whether you’re a beginner or an experienced trader, Nexora AI is "
    "here to help you make better trading decisions.\n\n"
    "👇 Tap the button below to start chatting with Nexora AI."
)


async def welcome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)

    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    sent, failed = 0, 0
    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=WELCOME_CHANNEL_MESSAGE,
                parse_mode=ParseMode.HTML,
                reply_markup=get_channel_button(),
            )
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[WELCOME] Failed for {channel_id}: {e}")

    await update.message.reply_text(
        f"Welcome message posted to {sent}/3 channels."
        + (f" {failed} failed - check logs." if failed else "")
    )


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
            response = requests.patch(
                url, headers=sb_headers(),
                json={"last_digest_message_id": None}, timeout=10
            )
            if response.status_code not in (200, 204):
                print(f"[PURGE DIGESTS] Clear got {response.status_code} for {target_user_id}: {response.text}")
        except Exception as e:
            print(f"[PURGE DIGESTS] Couldn't clear saved id for {target_user_id}: {e}")

    await update.message.reply_text(
        f"Done. Deleted: {deleted} | Failed: {failed} "
        f"(likely already deleted, or message older than Telegram's "
        f"48h bot-delete window)."
    )

async def mt5revenue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only report on THIS bot's MT5 Auto-Trade revenue
    specifically (from korapay_transactions) - has no visibility into
    any other business using the same KoraPay account, since that
    data lives in systems this bot doesn't touch.
    """
    user_id = str(update.message.from_user.id)
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return

    try:
        url = f"{SUPABASE_URL}/rest/v1/korapay_transactions?status=eq.success&select=*&order=confirmed_at.desc"
        response = requests.get(url, headers=sb_headers(), timeout=10)
        transactions = response.json()
    except Exception as e:
        await update.message.reply_text(f"⚠️ Couldn't fetch revenue data: {e}")
        return

    if not transactions:
        await update.message.reply_text("📊 No confirmed MT5 Auto-Trade payments yet.")
        return

    total = sum(float(t.get("amount", 0)) for t in transactions)
    currency = transactions[0].get("currency", MT5_AUTOTRADE_CURRENCY)
    count = len(transactions)

    recent_lines = []
    for t in transactions[:10]:
        confirmed = (t.get("confirmed_at") or "")[:16].replace("T", " ")
        recent_lines.append(f"• {t.get('user_id')} — {t.get('amount')} {t.get('currency')} ({confirmed})")

    await update.message.reply_text(
        f"📊 <b>MT5 Auto-Trade Revenue</b>\n\n"
        f"Total collected: <b>{total:,.2f} {currency}</b>\n"
        f"Confirmed payments: <b>{count}</b>\n\n"
        f"<b>Most recent 10:</b>\n" + "\n".join(recent_lines) + "\n\n"
        f"<i>This covers MT5 Auto-Trade only - no visibility into any "
        f"other business on the same KoraPay account.</i>",
        parse_mode=ParseMode.HTML
    )


def parse_broadcast_button_syntax(message_text):
    """
    Parses the (message, button_label, destination) out of a broadcast
    command's raw text. Uses plain keyword LINES ("BUTTON:" / "LINK:")
    rather than special characters like the old || delimiter - a
    real broadcast came through with the button missing because a
    phone's keyboard autocorrect silently mangled the || sequence
    before the message was even sent (confirmed by the visible
    capitalization change alongside it). Plain English words on their
    own line are not something autocorrect meaningfully alters, so
    this is the actual fix, not just a workaround.

    Returns (message_text, button_label_or_None, destination_string).
    destination defaults to "exness" when a button is present but no
    LINK: line was given.
    """
    lines = message_text.split("\n")
    button_label = None
    destination = "exness"
    message_lines = []
    skip_next_link_check = False

    # Per explicit instruction: LINK: is meant to be typed as a plain
    # human-readable phrase, not the literal goto_ keyword or a full
    # URL - "News Calendar" is easier to type correctly from a phone
    # than "newscalendar" every time, especially under autocorrect.
    # Add more aliases here as new destinations get added.
    DESTINATION_ALIASES = {
        "news calendar": "newscalendar",
        "newscalendar": "newscalendar",
    }

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("button:"):
            button_label = stripped[len("button:"):].strip()
            skip_next_link_check = True
            continue
        if skip_next_link_check and stripped.lower().startswith("link:"):
            raw_destination = stripped[len("link:"):].strip().lower()
            destination = DESTINATION_ALIASES.get(raw_destination, raw_destination.replace(" ", ""))
            continue
        message_lines.append(line)

    return "\n".join(message_lines).strip(), button_label, destination


async def _handle_broadcast_request(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str, photo_file_ids=None):
    """
    Shared core for /broadcast - used by BOTH the plain-text
    CommandHandler below AND the new photo-caption handler
    (broadcast_photo_handler). Confirmed directly from Telegram's own
    docs: CommandHandler can NEVER see a command typed as a photo's
    caption, in any version of the library - that's why a broadcast
    sent as "photo + /broadcast... caption" silently did nothing
    before. This shared core is what makes both paths actually work
    the same way, rather than duplicating the parsing/sending logic.

    photo_file_ids is a LIST now, per explicit instruction (confirmed
    real bug - sending multiple photos as one Telegram album used to
    silently drop everything after the first) - see broadcast_photo_
    handler's media-group buffering for how multi-photo albums get
    collected into this list before this function ever runs.
    """
    user_id = str(update.effective_user.id)
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    message_text = raw_text.replace("/broadcast", "", 1).strip()
    if not message_text:
        await update.effective_message.reply_text(
            "Usage: /broadcast <message>\n\n"
            "With a button, add these on their own lines at the end:\n"
            "BUTTON: <button label>\n"
            "LINK: <destination>\n\n"
            "Destinations: exness (default), deriv, signal, news calendar, nexora (just opens the bot directly).\n\n"
            "Attach a photo (with this as its caption) to send it as "
            "an image with caption instead of plain text.\n\n"
            "Sends to every known user, with the current keyboard "
            "attached so everyone's buttons refresh too (unless a "
            "button is included, which takes its place on that "
            "message only). HTML formatting tags (<b>, <i>, etc.) "
            "are supported."
        )
        return

    message_text, button_label, destination = parse_broadcast_button_syntax(message_text)

    user_count = len(await get_all_known_user_ids())
    await update.effective_message.reply_text(
        f"📡 <b>Broadcasting to {user_count} users in the background...</b>\n"
        f"I'll message you here when it's done.",
        parse_mode=ParseMode.HTML
    )

    asyncio.create_task(
        _run_broadcast(context.bot, message_text, update.effective_chat.id, button_label, destination, photo_file_ids)
    )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_broadcast_request(update, context, update.message.text)


async def _handle_broadcastchannels_request(update: Update, context: ContextTypes.DEFAULT_TYPE, raw_text: str, photo_file_ids=None):
    """
    Shared core for /broadcastchannels - same reasoning as
    _handle_broadcast_request above. Only 3 channels, so this still
    runs inline rather than as a background task.

    photo_file_ids is a LIST now, per explicit instruction (confirmed
    real bug - sending multiple photos as one Telegram album used to
    silently drop everything after the first, since the caption/
    command only ever attaches to ONE message in a Telegram album,
    and that was the only photo this function ever saw).
    """
    user_id = str(update.effective_user.id)
    if not ADMIN_USER_ID or user_id != str(ADMIN_USER_ID):
        return  # silently ignore - don't reveal this command to anyone else

    message_text = raw_text.replace("/broadcastchannels", "", 1).strip()
    if not message_text:
        await update.effective_message.reply_text(
            "Usage: /broadcastchannels <message>\n\n"
            "With a button, add these on their own lines at the end:\n"
            "BUTTON: <button label>\n"
            "LINK: <destination>\n\n"
            "Destinations: exness (default), deriv, signal, news calendar, nexora (just opens the bot directly).\n\n"
            "Attach a photo (with this as its caption) to post it as "
            "an image with caption instead of plain text - attach "
            "multiple photos as one album and they'll all post "
            "together (caption and button only show on the first).\n\n"
            "Posts to all 3 channels at once. Channels have no "
            "persistent keyboard, so a button is the only way to make "
            "the post tappable. HTML formatting tags (<b>, <i>, etc.) "
            "are supported."
        )
        return

    message_text, button_label, destination = parse_broadcast_button_syntax(message_text)

    markup = None
    if button_label:
        button_url = (
            f"https://t.me/{BOT_USERNAME}"
            if destination == "nexora"
            else f"https://t.me/{BOT_USERNAME}?start=goto_{destination}"
        )
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton(button_label, url=button_url)
        ]])

    # Per explicit instruction: two genuinely different paths now,
    # picked automatically based on whether a button is present -
    # previously this ALWAYS split into "first photo separate +
    # trailing group" regardless of button, which is why removing the
    # button alone didn't change anything (confirmed real gap - the
    # conditional below never actually existed until now).
    #
    # NO button + multiple photos: one single, TRUE unified
    # send_media_group call, with the caption on the first item -
    # Telegram displays that as one shared caption for the whole
    # album, so this is a genuine single post, all photos together.
    #
    # Button present (any photo count) OR only 1 photo: unavoidable
    # split - Telegram's send_media_group has zero reply_markup
    # support at all (confirmed directly from Telegram's own bot-api
    # issue tracker), so a button can only ever attach to a single,
    # standalone photo message. First photo carries caption+button;
    # any additional photos follow as a separate uncaptioned album
    # instead of being silently dropped.
    first_photo = photo_file_ids[0] if photo_file_ids else None
    extra_photos = photo_file_ids[1:] if photo_file_ids and len(photo_file_ids) > 1 else []
    use_unified_album = photo_file_ids and len(photo_file_ids) > 1 and not markup

    sent, failed = [], []
    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
        try:
            if use_unified_album:
                media = [InputMediaPhoto(photo_file_ids[0], caption=message_text, parse_mode=ParseMode.HTML)]
                media += [InputMediaPhoto(fid) for fid in photo_file_ids[1:]]
                await context.bot.send_media_group(chat_id=channel_id, media=media)
            elif first_photo:
                await context.bot.send_photo(
                    chat_id=channel_id, photo=first_photo, caption=message_text,
                    parse_mode=ParseMode.HTML, reply_markup=markup
                )
                if extra_photos:
                    await context.bot.send_media_group(
                        chat_id=channel_id,
                        media=[InputMediaPhoto(fid) for fid in extra_photos]
                    )
            else:
                await context.bot.send_message(
                    chat_id=channel_id, text=message_text, parse_mode=ParseMode.HTML,
                    reply_markup=markup
                )
            sent.append(channel_id)
        except Exception as e:
            failed.append(channel_id)
            print(f"[BROADCAST CHANNELS] Failed for {channel_id}: {e}")

    result_text = f"✅ Posted to {len(sent)}/3 channels."
    if failed:
        result_text += f"\n⚠️ Failed: {', '.join(failed)} - check Railway logs for why."
    await update.effective_message.reply_text(result_text)


async def broadcastchannels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle_broadcastchannels_request(update, context, update.message.text)


# Buffers photos from a Telegram album (multiple photos sent as one
# message group) until all of them have arrived, keyed by Telegram's
# own media_group_id - per explicit instruction, confirmed real bug:
# an album's caption/command only ever attaches to ONE of its photo
# messages, so without this, only that single photo was ever seen and
# every other photo in the album silently vanished. Not meant to
# persist across restarts - an in-flight album mid-send during a
# restart is a rare enough edge case that losing it is an acceptable
# tradeoff for not needing real persistence here.
MEDIA_GROUP_BUFFER = {}
MEDIA_GROUP_DEBOUNCE_SECONDS = 1.5


async def _process_media_group_after_debounce(media_group_id):
    """
    Waits for the album to stop growing before processing it - photos
    in a Telegram album arrive as separate, near-simultaneous updates,
    not all at once, so there's no single moment that's reliably "the
    last one". Each new photo re-triggers this with a fresh timer;
    only the LAST-scheduled call (the one where no newer photo showed
    up during its wait) actually processes and clears the buffer.
    """
    await asyncio.sleep(MEDIA_GROUP_DEBOUNCE_SECONDS)
    entry = MEDIA_GROUP_BUFFER.get(media_group_id)
    if not entry:
        return  # already processed by another call, or never existed
    if time.time() - entry["last_update_at"] < MEDIA_GROUP_DEBOUNCE_SECONDS - 0.1:
        return  # a newer photo arrived since this call started waiting - a fresher call will handle it

    MEDIA_GROUP_BUFFER.pop(media_group_id, None)
    caption = entry.get("caption")
    if not caption:
        return  # no /broadcast or /broadcastchannels caption found anywhere in this album - nothing to do

    file_ids = entry["file_ids"]
    update = entry["update"]
    context = entry["context"]
    if caption.startswith("/broadcastchannels"):
        await _handle_broadcastchannels_request(update, context, caption, file_ids)
    elif caption.startswith("/broadcast"):
        await _handle_broadcast_request(update, context, caption, file_ids)


async def broadcast_photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Catches /broadcast or /broadcastchannels sent as a PHOTO's
    caption - the exact scenario that silently failed before.
    CommandHandler can never see these (confirmed directly from
    Telegram's own docs, true in every library version), so this is
    a separate MessageHandler specifically watching for a photo whose
    caption starts with one of these commands. Checks
    "/broadcastchannels" before the bare "/broadcast" prefix, since
    the latter is a prefix of the former and would otherwise catch
    it by mistake.

    Multiple photos sent as one album (per explicit instruction,
    confirmed real bug) arrive as SEPARATE messages sharing a
    media_group_id, with the caption on only ONE of them - buffered
    via MEDIA_GROUP_BUFFER and processed together once the album stops
    growing, rather than only ever seeing the single captioned photo.
    A single photo (no media_group_id) is unaffected and still
    processes immediately, exactly as before.
    """
    if not update.message.photo:
        return
    photo_file_id = update.message.photo[-1].file_id  # highest resolution Telegram sent
    caption = update.message.caption or ""
    media_group_id = update.message.media_group_id

    if not media_group_id:
        if caption.startswith("/broadcastchannels"):
            await _handle_broadcastchannels_request(update, context, caption, [photo_file_id])
        elif caption.startswith("/broadcast"):
            await _handle_broadcast_request(update, context, caption, [photo_file_id])
        return

    entry = MEDIA_GROUP_BUFFER.setdefault(media_group_id, {
        "file_ids": [], "caption": None, "update": update, "context": context
    })
    entry["file_ids"].append(photo_file_id)
    if caption:
        entry["caption"] = caption
        entry["update"] = update  # the message that actually carries the command/caption
    entry["context"] = context
    entry["last_update_at"] = time.time()
    asyncio.create_task(_process_media_group_after_debounce(media_group_id))

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
    # No custom menu button / commands list - left as Telegram's default
    # so the native full-width blue "Start" button shows automatically
    # whenever a chat with the bot has zero messages (e.g. right after
    # a client clears their chat history), same as reference bots.
    try:
        await app.bot.set_my_commands([])
        await app.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        print("[STARTUP] ✅ Menu button reset to Telegram default.")
    except Exception as e:
        print(f"[STARTUP] ⚠️ Couldn't reset menu button: {e}")

    try:
        await app.bot.set_my_description(
            description=(
                "📊 Free real-time forex & gold signals ⚡ AI chart analysis "
                "🤖 MT5 & Deriv AutoCopy — join thousands of traders"
            )
        )
        print("[STARTUP] ✅ Bot description set.")
    except Exception as e:
        print(f"[STARTUP] ⚠️ Couldn't set bot description: {e}")

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
# MT5 AUTO-TRADE CLOSE MONITOR (NEW)
# Deriv equivalent is check_open_auto_copy_trades below - this is the
# MT5 side, which never existed before (see log_mt5_autotrade_order
# above). Runs every 15 minutes: for each order still marked OPEN,
# asks MetaAPI directly whether it has actually closed yet (real
# profit/loss via get_mt5_trade_outcome, ground truth from the real
# trade), and DMs the client immediately once it has - per explicit
# instruction, so a bad run is visible in real time instead of
# discovered too late.
# ============================================

async def check_mt5_autotrade_closed_orders(context: ContextTypes.DEFAULT_TYPE):
    open_orders = get_open_mt5_autotrade_orders()
    if not open_orders:
        return

    for order in open_orders:
        order_id = order.get("order_id")
        user_id = order.get("user_id")
        if not order_id or not user_id:
            continue

        outcome, profit = await get_mt5_trade_outcome(order_id)
        if outcome != "CLOSED" or profit is None:
            continue  # still open, or lookup failed - retry next sweep

        mark_mt5_autotrade_order_closed(order_id, profit)
        try:
            await update_flip_lot_after_close(user_id, profit)
        except Exception as e:
            print(f"[MT5 AUTOTRADE] Account Flip lot update failed for {user_id}: {e}")

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

    # Fixed: this used to build its account lookup from
    # get_all_auto_copy_accounts(), which only returns accounts with
    # the legacy Auto-Copy flag on - any trade placed by the newer
    # Pick-a-Bot/Account-Flip engines would never find its account
    # here and could stay stuck OPEN forever, exactly the bug already
    # found in the two scan jobs themselves.
    accounts_by_user = {a["user_id"]: a for a in get_all_deriv_accounts_with_token()}

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
        response = requests.patch(
            url, headers=sb_headers(),
            json={"last_digest_message_id": message_id}, timeout=10
        )
        if response.status_code not in (200, 204):
            print(f"[AUTO-COPY DIGEST] save got {response.status_code}: {response.text}")
    except Exception as e:
        print(f"[AUTO-COPY DIGEST] save_last_digest_message_id error: {e}")

def save_mt5_digest_message_id(user_id, message_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/mt5_auto_trade_accounts?user_id=eq.{user_id}"
        requests.patch(
            url, headers=sb_headers(),
            json={"last_digest_message_id": message_id}, timeout=10
        )
    except Exception as e:
        print(f"[MT5 AUTOTRADE DIGEST] save_mt5_digest_message_id error: {e}")

# ============================================
# MT5 AUTO-TRADE DAILY DIGEST (NEW)
# Exness/MT5 equivalent of send_auto_copy_daily_digest above - same
# once-a-day, no-real-time-pings approach, per explicit instruction.
# Summarizes today's trades, real $ P&L (from mt5_autotrade_orders,
# via check_mt5_autotrade_closed_orders' silent close-detection), and
# current balance.
#
# REQUIRED: add this column in Supabase before deploying -
#   alter table mt5_auto_trade_accounts add column last_digest_message_id text;
# ============================================

async def send_mt5_autotrade_daily_digest(context: ContextTypes.DEFAULT_TYPE):
    accounts = get_all_active_mt5_autotrade_accounts()
    if not accounts:
        return

    bot = context.bot

    for account in accounts:
        user_id = account.get("user_id")
        metaapi_account_id = account.get("metaapi_account_id")
        if not user_id:
            continue

        orders_today = get_todays_mt5_autotrade_orders(user_id)
        if not orders_today:
            continue  # nothing happened for this user today - no digest at all

        # Compact summary ONLY, per explicit instruction - matches the
        # same fix applied to the Deriv Auto-Copy digest above.
        closed_today = [o for o in orders_today if o.get("status") == "CLOSED" and o.get("profit") is not None]
        wins = [o for o in closed_today if o["profit"] >= 0]
        losses = [o for o in closed_today if o["profit"] < 0]
        total_profit = sum(o["profit"] for o in closed_today)
        still_open = len(orders_today) - len(closed_today)

        digest = "🤖 <b>Exness Auto-Trade — today's summary</b>\n\n"
        digest += f"Trades placed: {len(orders_today)}\n"
        if closed_today:
            digest += f"✅ {len(wins)} win{'s' if len(wins) != 1 else ''} • ❌ {len(losses)} loss{'es' if len(losses) != 1 else ''}"
            if still_open:
                digest += f" • ⏳ {still_open} still open"
            digest += "\n"
            sign = "+" if total_profit >= 0 else "-"
            emoji = "📈" if total_profit >= 0 else "📉"
            digest += f"{emoji} <b>Today's P&L: {sign}${abs(total_profit):.2f}</b>\n"
        else:
            digest += f"⏳ All still open - nothing closed yet today.\n"

        if metaapi_account_id:
            balance = await get_client_mt5_balance(metaapi_account_id)
            if balance is not None:
                digest += f"💰 <b>Current balance:</b> ${balance:.2f}\n"

        try:
            old_message_id = account.get("last_digest_message_id")
            if old_message_id:
                try:
                    await bot.delete_message(chat_id=int(user_id), message_id=int(old_message_id))
                except Exception as e:
                    print(f"[MT5 AUTOTRADE DIGEST] Couldn't delete yesterday's digest for {user_id}: {e}")

            sent = await bot.send_message(
                chat_id=int(user_id),
                text=digest,
                parse_mode=ParseMode.HTML
            )
            save_mt5_digest_message_id(user_id, sent.message_id)
        except Exception as e:
            print(f"[MT5 AUTOTRADE DIGEST] ❌ Couldn't send digest to {user_id}: {e}")

async def send_auto_copy_daily_digest(context: ContextTypes.DEFAULT_TYPE):
    accounts = get_all_auto_copy_accounts()
    if not accounts:
        return

    bot = context.bot

    for account in accounts:
        user_id = account.get("user_id")
        token = account.get("api_token")
        if not user_id:
            continue

        trades_today = get_todays_auto_copy_trades(user_id)
        failure_count = get_todays_auto_copy_failure_count(user_id)

        if not trades_today and not failure_count:
            continue  # nothing happened for this user today - no digest at all

        # Compact summary ONLY, per explicit instruction - a wall of
        # one line per individual trade (the previous version) is
        # exactly the kind of message flooding this bot has been
        # steered away from throughout. One digest, one screenful,
        # every trade folded into counts instead of listed out.
        closed_today = [t for t in trades_today if t.get("status") == "CLOSED" and t.get("profit") is not None]
        wins = [t for t in closed_today if t["profit"] >= 0]
        losses = [t for t in closed_today if t["profit"] < 0]
        total_profit = sum(t["profit"] for t in closed_today)
        still_open = len(trades_today) - len(closed_today)

        digest = "🤖 <b>Auto Trade — today's summary</b>\n\n"
        digest += f"Trades placed: {len(trades_today)}\n"
        if closed_today:
            digest += f"✅ {len(wins)} win{'s' if len(wins) != 1 else ''} • ❌ {len(losses)} loss{'es' if len(losses) != 1 else ''}"
            if still_open:
                digest += f" • ⏳ {still_open} still open"
            digest += "\n"
            sign = "+" if total_profit >= 0 else "-"
            emoji = "📈" if total_profit >= 0 else "📉"
            digest += f"{emoji} <b>Today's P&L: {sign}${abs(total_profit):.2f}</b>\n"
        elif trades_today:
            digest += f"⏳ All still open - nothing closed yet today.\n"

        if token:
            snapshot = await deriv_fetch_account_snapshot(token)
            balance = snapshot.get("balance") if snapshot else None
            if balance is not None:
                digest += f"💰 <b>Current balance:</b> ${balance:.2f}\n"

        if failure_count:
            digest += (
                f"\n<i>{failure_count} other signal"
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
# WEEKLY PERFORMANCE REPORT
# Runs every Sunday at 23:00 UTC, covering the
# full Monday 00:00 UTC -> Sunday 23:00 UTC week
# (including weekend BTCUSD activity).
#
# REWRITE, per explicit instruction: a pure win-
# rate count (TP hits / closed signals) can make a
# genuinely PROFITABLE week look bad, since it
# ignores position sizing entirely - e.g. 4 wins/6
# losses on XAUUSD's real 1:2 risk:reward ($150
# SL / $300 TP at 0.1 lot) is a real +$300 net week
# (4*300 - 6*150 = 300), but the old win-rate-only
# report would have shown that as a discouraging
# 40% win rate with no indication it was actually
# profitable.
#
# Now calculates REAL dollar P&L using live MT5
# data (get_mt5_trade_outcome, the exact same
# function the 15-min TP/SL monitor already uses)
# for each individual signal that week, rather than
# a fixed formula - this is accurate to the cent,
# including for GBPJPY where the real USD value
# depends on the live JPY exchange rate at the
# moment each trade closed (a fixed formula could
# never get that number exactly right).
#
# Scoped ONLY to the 5 pairs that actually appear
# on the scheduled daily/weekday calendar (XAUUSD,
# EURUSD, GBPUSD, GBPJPY, BTCUSD - confirmed
# directly against MORNING_PAIR_BY_WEEKDAY /
# MIDDAY_PAIR_BY_WEEKDAY / EVENING_PAIR_BY_WEEKDAY),
# per explicit instruction - synthetics excluded
# (traded far less often, and not auto-traded on
# MT5 at all, so there's no MT5 P&L to pull for
# them in the first place).
#
# Relies on mt5_order_id already being saved on
# every scheduled signal (see attach_mt5_order_id /
# place_and_link_mt5_trade in _post_signal_for_pair)
# - a signal with no linked order (e.g. MT5
# placement itself failed that round) is counted in
# the win/loss totals from its status field, but
# can't contribute a real dollar figure and is
# called out separately in the report rather than
# silently treated as $0.
# ============================================

SCHEDULED_DAILY_PAIRS = ("XAUUSD", "XAGUSD", "USOIL", "BTCUSD")

# Fixed SL/TP pip distances per pair, per explicit instruction (added
# alongside the dollar P&L above so pip-focused traders can see both).
# Unlike dollar P&L, pip distance doesn't need live MT5 data - it's a
# fixed property of each pair's current SL/TP multiplier config (see
# the sl_multiplier/tp_multiplier block in build_signal_response),
# confirmed by calculation, not MT5 lookup: BTCUSD's 495.9/991.8 are
# NOT round numbers - they're the exact real values of pip_size=165.3
# * 3/6 / pip_value=1.0, kept precise rather than rounded to "496/992"
# so this always matches the real, current live SL/TP setting exactly.
# XAGUSD/USOIL added the same way: pip_size=0.1667 * 4.2/8.4
# multipliers / pip_value=0.01 = 70.014/140.028 (both pairs share the
# identical pip_size, pip_value, and multiplier bucket, so their pip
# distances are identical too - not a copy-paste mistake).
SCHEDULED_PAIR_PIPS = {
    "XAUUSD": (150, 300),
    "EURUSD": (30, 60),
    "GBPUSD": (30, 60),
    "GBPJPY": (50, 100),
    "BTCUSD": (495.9, 991.8),
    "XAGUSD": (70.014, 140.028),
    "USOIL": (70.014, 140.028),
}

def get_week_start():
    now = datetime.utcnow()
    monday = now - timedelta(days=now.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)

async def post_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    week_start = get_week_start()
    now = datetime.utcnow()

    all_signals = get_signals_since(week_start)
    signals = [
        s for s in all_signals
        if s.get("pair_name") in SCHEDULED_DAILY_PAIRS
        # FIX: CONFIRMED REAL BUG - neither report filtered by
        # source before, so today's 47 manual test signals would
        # have swamped a report that only actually had 1 real
        # scheduled signal. source IS NULL is treated as scheduled
        # too, for any row logged before this column existed.
        and s.get("source") in ("scheduled", None)
    ]

    total = len(signals)
    tp_hit = sum(1 for s in signals if s.get("status") == "TP_HIT")
    sl_hit = sum(1 for s in signals if s.get("status") == "SL_HIT")
    still_open = sum(1 for s in signals if s.get("status") == "OPEN")
    closed = tp_hit + sl_hit
    win_rate = round((tp_hit / closed) * 100) if closed > 0 else 0

    total_profit_usd = 0.0
    missing_pnl_count = 0
    per_pair_pnl = {pair: 0.0 for pair in SCHEDULED_DAILY_PAIRS}

    total_pips_won = 0.0
    total_pips_lost = 0.0

    for sig in signals:
        pair_name = sig.get("pair_name")
        status = sig.get("status")

        # Pips: computed directly from status + pair_name alone - a
        # TP_HIT or SL_HIT always means the FULL known pip distance
        # for that pair, by definition, so this doesn't need MT5 data
        # the way the dollar P&L above does (that needed a live MT5
        # lookup specifically because GBPJPY's real USD value depends
        # on the JPY rate at close time - pips have no such ambiguity).
        if pair_name in SCHEDULED_PAIR_PIPS:
            sl_pips, tp_pips = SCHEDULED_PAIR_PIPS[pair_name]
            if status == "TP_HIT":
                total_pips_won += tp_pips
            elif status == "SL_HIT":
                total_pips_lost += sl_pips

        if status not in ("TP_HIT", "SL_HIT"):
            continue  # still-open signals have no realized P&L yet
        order_id = sig.get("mt5_order_id")
        if not order_id:
            missing_pnl_count += 1
            continue
        outcome, profit = await get_mt5_trade_outcome(order_id)
        if outcome != "CLOSED" or profit is None:
            missing_pnl_count += 1
            continue
        total_profit_usd += profit
        if pair_name in per_pair_pnl:
            per_pair_pnl[pair_name] += profit

    net_pips = total_pips_won - total_pips_lost

    date_range = f"{week_start.strftime('%d %b')} – {now.strftime('%d %b %Y')}"
    # Sign placed BEFORE the $ (e.g. "-$218.96", "+$218.96") instead of
    # between $ and the digits - per explicit instruction, the old
    # "${total_profit_usd:.2f}" let Python's own negative formatting
    # land the minus sign after the $ (e.g. "$-218.96"), which read
    # as ambiguous/misleading to subscribers. abs() + explicit sign
    # in front of $ removes that ambiguity entirely.
    profit_sign = "-" if total_profit_usd < 0 else "+"
    profit_abs = abs(total_profit_usd)
    profit_emoji = "📈" if total_profit_usd >= 0 else "📉"
    net_pips_sign = "+" if net_pips >= 0 else ""

    # Was a hardcoded, disconnected string ("XAUUSD, EURUSD, GBPUSD,
    # GBPJPY, BTCUSD") left over from before SCHEDULED_DAILY_PAIRS was
    # narrowed to just the 3 actually-traded pairs - the underlying
    # numbers were already correctly filtered, but this label never
    # got updated to match, so it kept showing 2 pairs that aren't
    # actually traded. Now built directly from the same constant the
    # filtering itself uses, so the two can never diverge again.
    traded_pairs_label = ", ".join(SCHEDULED_DAILY_PAIRS)

    report = (
        f"📊 <b>WEEKLY PERFORMANCE REPORT</b>\n"
        f"<i>#NexoraAI — {date_range}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Total Signals Issued:</b> {total} "
        f"<i>({traded_pairs_label})</i>\n\n"
        f"✅ <b>Take Profit Hit:</b> {tp_hit}\n"
        f"❌ <b>Stop Loss Hit:</b> {sl_hit}\n"
        f"⏳ <b>Still Running:</b> {still_open}\n\n"
        f"🎯 <b>Win Rate:</b> {win_rate}% ({tp_hit}/{closed} closed signals)\n\n"
        f"📐 <b>Pips Won:</b> +{total_pips_won:.1f}\n"
        f"📐 <b>Pips Lost:</b> -{total_pips_lost:.1f}\n\n"
        # Net Pips REMOVED per explicit instruction - it could show a
        # negative figure in the same report as a positive dollar P&L
        # (different pairs carry very different $-per-pip values, so
        # the two totals don't move together), which read as
        # confusing/contradictory to beginners. Pips Won/Lost above
        # are kept as-is; only the combined net figure is gone.
        f"{profit_emoji} <b>Real Net P&L (0.1 lot):</b> {profit_sign}${profit_abs:.2f}\n\n"
    )
    # NOTE: the missing_pnl_count warning ("N closed signal(s) couldn't
    # be matched to a real MT5 trade...") is deliberately NOT included
    # in the public report anymore - per explicit instruction, that's
    # internal diagnostic info subscribers shouldn't see. It's still
    # logged to console below so the info isn't lost, just not public.
    if missing_pnl_count:
        print(
            f"[WEEKLY REPORT] ⚠️ {missing_pnl_count} closed signal(s) couldn't be "
            f"matched to a real MT5 trade this week (e.g. auto-trade "
            f"placement failed that round) - excluded from P&L total, "
            f"not shown in public report."
        )
    report += (
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


def get_day_start():
    now = datetime.utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def post_daily_report(context: ContextTypes.DEFAULT_TYPE):
    """
    New, per explicit instruction: a same-day summary of today's
    scheduled signals, sent at 9PM Lagos (20:00 UTC) - separate from
    and BEFORE the existing weekly report, which still runs unchanged
    at its own Sunday slot. Reuses the exact same TP/SL/pip/P&L
    calculation post_weekly_report uses, just scoped to today
    (midnight UTC -> now), and lists each of today's signals
    individually by pair - the weekly report only ever shows
    aggregate totals, never itemizes, but with up to 3 (or fewer on
    weekends) signals a day, per-signal detail is the more useful
    view, per explicit instruction.

    A signal still OPEN at send time isn't a bug or a missed report -
    it's noted individually as still running, excluded from today's
    P&L math (which only covers what's actually closed), and folded
    into the following weekend's report once it does close, exactly
    like the existing weekly logic already handles it - this doesn't
    change how the weekly report counts anything, only adds a same-
    day preview on top of it.
    """
    day_start = get_day_start()
    now = datetime.utcnow()
    date_label = now.strftime("%d %b")

    all_signals = get_signals_since(day_start)
    signals = [
        s for s in all_signals
        if s.get("pair_name") in SCHEDULED_DAILY_PAIRS
        # FIX: CONFIRMED REAL BUG - neither report filtered by
        # source before, so today's 47 manual test signals would
        # have swamped a report that only actually had 1 real
        # scheduled signal. source IS NULL is treated as scheduled
        # too, for any row logged before this column existed.
        and s.get("source") in ("scheduled", None)
    ]

    if not signals:
        report = f"📊 <b>DAILY REPORT — {date_label}</b>\n\nNo scheduled signals today. <i>Trade safe 🔥</i>"
        for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
            try:
                await context.bot.send_message(chat_id=channel_id, text=report, parse_mode=ParseMode.HTML)
                print(f"[DAILY REPORT] ✅ Posted (no signals) to {channel_id}")
            except Exception as e:
                print(f"[DAILY REPORT] ❌ Failed for {channel_id}: {e}")
        return

    total_pips_won = 0.0
    total_pips_lost = 0.0
    any_still_running = False
    line_items = []

    for sig in signals:
        pair_name = sig.get("pair_name")
        status = sig.get("status")
        direction = sig.get("direction", "")
        sl_pips, tp_pips = SCHEDULED_PAIR_PIPS.get(pair_name, (0, 0))

        if status == "TP_HIT":
            total_pips_won += tp_pips
            line_items.append(f"✅ {pair_name} {direction} +{tp_pips:.0f} pips")
        elif status == "SL_HIT":
            total_pips_lost += sl_pips
            line_items.append(f"❌ {pair_name} {direction} -{sl_pips:.0f} pips")
        else:
            # Still running - excluded from today's pip math, picked
            # up by the weekend report once it actually closes.
            any_still_running = True
            line_items.append(f"⏳ {pair_name} running")

    # Per explicit instruction: dropped the dollar P&L entirely - it
    # was computed at a fixed 0.1 lot, but real subscribers trade
    # different lot sizes, so a single dollar figure was never
    # actually accurate for anyone reading it. Pips are lot-size-
    # independent, so they're the fair number to show.
    #
    # Per explicit instruction: "+300 / -70" read as confusing (looks
    # like two separate figures, not obviously related) - now shows
    # the actual arithmetic so the net result is unambiguous at a
    # glance. One-sided days still show just that one number, no
    # pointless "+0" or "= " on a single figure.
    if total_pips_won and total_pips_lost:
        net = total_pips_won - total_pips_lost
        net_str = f"+{net:.0f}" if net >= 0 else f"{net:.0f}"
        pips_summary = f"+{total_pips_won:.0f}, -{total_pips_lost:.0f} = {net_str} pips"
    elif total_pips_won:
        pips_summary = f"+{total_pips_won:.0f} pips"
    elif total_pips_lost:
        pips_summary = f"-{total_pips_lost:.0f} pips"
    else:
        pips_summary = "0 pips"

    report = (
        f"📊 <b>DAILY REPORT — {date_label}</b>\n\n"
        + "\n".join(line_items) + "\n\n"
        f"<b>{pips_summary}</b> | <i>Trade safe 🔥</i>"
    )
    if any_still_running:
        report += "\n<i>⏳ Running signals will be included in the weekend report once closed.</i>"

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID, CHANNEL_3_ID]:
        try:
            await context.bot.send_message(
                chat_id=channel_id,
                text=report,
                parse_mode=ParseMode.HTML
            )
            print(f"[DAILY REPORT] ✅ Posted to {channel_id}")
        except Exception as e:
            print(f"[DAILY REPORT] ❌ Failed for {channel_id}: {e}")

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
    app.add_handler(CommandHandler("broadcastchannels", broadcastchannels_command))
    app.add_handler(MessageHandler(filters.PHOTO, broadcast_photo_handler))
    app.add_handler(CommandHandler("mt5revenue", mt5revenue_command))
    app.add_handler(CommandHandler("testsynth", testsynth_command))
    app.add_handler(CommandHandler("discoversymbols", discoversymbols_command))
    app.add_handler(CommandHandler("testsignal", testsignal_command))
    app.add_handler(CommandHandler("welcome", welcome_command))
    app.add_handler(CommandHandler("purgedigests", purgedigests_command))
    app.add_handler(CallbackQueryHandler(handle_callback))

    app.add_handler(
        MessageHandler(
            filters.Regex(
                "^(📊 Signal|📰 News|🔗 Connect Deriv|🤖 Exness Auto-Trade|signal|news|connect deriv|exness auto-trade)$"
            ),
            handle_buttons
        )
    )

    app.add_handler(
        MessageHandler(
            filters.LOCATION,
            handle_location
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
    SATURDAY_ONLY = (6,)             # cron-style: 6=Saturday
    SUNDAY_ONLY = (0,)               # cron-style: 0=Sunday

    for i, (utc_time, post_type, data) in enumerate(DAILY_SCHEDULE):
        if post_type == "news":
            job_queue.run_daily(
                post_news,
                time=parse_time(utc_time),
                name=f"news_{i}_{data}",
                data=data,
                days=WEEKDAYS_ONLY,  # per explicit instruction - no news post Sat/Sun
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
        post_midday_signal,
        time=parse_time("11:00"),  # 12PM Lagos, per explicit instruction (schedule rebuilt 2026-07-08)
        name="midday_signal",
        days=EVERY_DAY,
        job_kwargs={"misfire_grace_time": 300}
    )
    job_queue.run_daily(
        post_evening_signal,
        time=parse_time("17:00"),  # 6PM Lagos, per explicit instruction
        name="evening_signal",
        days=EVERY_DAY,
        job_kwargs={"misfire_grace_time": 300}
    )

    for i, (utc_time, schedule_type, slot_number) in enumerate(SYNTHETIC_SCHEDULE):
        if schedule_type == "sunday_only":
            days = SUNDAY_ONLY
        elif schedule_type == "saturday_only":
            days = SATURDAY_ONLY
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

    # MT5 auto-trade close monitor - detects closed orders every 15
    # minutes and logs the real profit/loss silently, feeding the
    # daily digest below. No per-trade DM, per explicit instruction -
    # consolidated into one daily summary instead of real-time pings.
    job_queue.run_repeating(
        check_mt5_autotrade_closed_orders,
        interval=900,
        first=90,
        name="mt5_autotrade_close_monitor"
    )

    # Account Flip entry scan - checks each subscriber's one chosen
    # pair on M15 for a fresh price-action signal (Engulfing/Pin Bar/
    # Inside Bar Breakout) and opens the first layer of a new stack.
    # 5 minutes matches the M15 candle-close cadence closely enough
    # without re-checking on every tick.
    job_queue.run_repeating(
        run_account_flip_entry_scan,
        interval=300,
        first=100,
        name="account_flip_entry_scan"
    )

    # Account Flip stack manager - runs every 60 seconds (much tighter
    # than the entry scan) since this is what adds layers on profit
    # triggers and runs the trailing stop close - both need to react
    # to live floating price, not just candle closes.
    job_queue.run_repeating(
        manage_account_flip_stacks,
        interval=60,
        first=50,
        name="account_flip_stack_manager"
    )

    # Deriv Aggressive/Conservative bot scan - mirrors the MT5 bot
    # scan, on synthetic indices via run_strategy_bank_synthetic.
    job_queue.run_repeating(
        run_deriv_autotrade_bot_scan,
        interval=300,
        first=110,
        name="deriv_autotrade_bot_scan"
    )

    # Deriv Account Flip entry scan - same M15 price-action signal as
    # the Exness side, executed as a Deriv multiplier contract instead
    # of an MT5 trade.
    job_queue.run_repeating(
        run_deriv_flip_entry_scan,
        interval=300,
        first=120,
        name="deriv_flip_entry_scan"
    )

    # Deriv Account Flip stack manager - dollar-profit-based layering/
    # trailing-stop, checked every 60 seconds same as the MT5 version.
    job_queue.run_repeating(
        manage_deriv_flip_stacks,
        interval=60,
        first=55,
        name="deriv_flip_stack_manager"
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

    # High-impact news alert - checks every 5 minutes for any USD/
    # EUR/GBP/JPY high-impact event landing in ~30 minutes, notifying
    # all 3 channels with a real pre-release bias per event. Per
    # explicit instruction.
    job_queue.run_repeating(
        check_upcoming_high_impact_news,
        interval=300,
        first=30,
        name="high_impact_news_alert"
    )

    # Post-release reaction - checks every 5 minutes for any today's
    # high-impact event whose actual result has now appeared in the
    # feed, posting a real, data-grounded BUY/SELL call to all 3
    # channels the moment it does. Per explicit instruction - separate
    # job from the pre-release one above (different trigger condition
    # entirely: event time vs actual-result-appearing), offset by 150s
    # so the two don't both hit the shared calendar feed at the exact
    # same moment every cycle.
    job_queue.run_repeating(
        check_released_high_impact_news,
        interval=300,
        first=180,
        name="high_impact_news_release_reaction"
    )

    # MT5 auto-trade: payment processing (picks up what the KoraPay
    # webhook already confirmed in the database, activates the
    # subscription, notifies the user - runs on the bot's own event
    # loop, unlike the webhook handler itself).
    job_queue.run_repeating(
        process_confirmed_korapay_payments,
        interval=20,
        first=15,
        name="mt5_autotrade_payment_processing"
    )

    # Deriv OAuth: picks up what deriv_oauth_callback_handler already
    # resolved and wrote to the database, saves the account, notifies
    # the user - same pattern as the KoraPay job above.
    #
    # FIX: interval was 20s, meaning the confirmation message in
    # Telegram could lag up to ~18s behind the page's own redirect
    # back into the app - a real, reported gap where the user lands
    # back in Telegram to nothing yet. This check is cheap (usually
    # an empty result), so 3s is safe and closes that gap to
    # something that reads as immediate.
    job_queue.run_repeating(
        process_pending_deriv_oauth_connections,
        interval=3,
        first=3,
        name="deriv_oauth_connection_processing"
    )

    # DERIV_SERVICE_TOKEN health check - added after a real, hours-long
    # silent outage (401 invalid/expired token, zero price data for
    # every Deriv synthetic index) that only got noticed because the
    # user happened to ask why nothing was trading. Checks every 15
    # minutes: alerts the admin if the token is currently failing
    # (max once per 2 hours while it stays broken), and separately
    # alerts once a day starting 3 days before its tracked expiry.
    job_queue.run_repeating(
        check_deriv_service_token_health,
        interval=900,
        first=60,
        name="deriv_service_token_health_check"
    )

    # MT5 auto-trade: daily expiry check - clients automatically
    # removed once their subscription expires, per explicit instruction.
    job_queue.run_daily(
        check_mt5_autotrade_expiry,
        time=parse_time("00:05"),
        name="mt5_autotrade_expiry_check"
    )

    # MT5 auto-trade: live execution for the 4 bot presets - checks
    # every 5 minutes for a fresh signal per unique (bot, pair)
    # combination actually in use, copies to every subscriber on it.
    job_queue.run_repeating(
        run_mt5_autotrade_bot_scan,
        interval=300,
        first=60,
        name="mt5_autotrade_bot_scan"
    )

    # MT5 auto-trade: live execution for "Full Signal Coverage"
    # subscribers - copies whatever the channel itself already posts,
    # checked every 2 minutes.
    job_queue.run_repeating(
        run_mt5_autotrade_follow_channel_scan,
        interval=120,
        first=60,
        name="mt5_autotrade_follow_channel_scan"
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

    # Accumulation Zone auto-copy scan (NEW) - LEFT DISABLED for now,
    # pending explicit confirmation. Built exactly per instruction
    # (M1 timeframe, auto-copy only), but reusing the SAME $ stake/
    # risk/win execution framework as Tick Burst above - the strategy
    # that was disabled after causing real account losses. Not
    # necessarily the same outcome (this strategy's own logic is
    # unrelated to Tick Burst's), but the EXECUTION MODEL is
    # identical, and that's worth a real go-ahead before real money
    # touches it, not an assumption this job should just start
    # running because the code compiles cleanly.
    #
    # job_queue.run_repeating(
    #     run_accumulation_zone_auto_trade,
    #     interval=60,
    #     first=30,
    #     name="accumulation_zone_auto_trade"
    # )

    # Auto-copy daily digest - RE-ENABLED, now with real $ P&L and
    # current balance included (previously just listed trades placed).
    # Per explicit instruction: real-time per-trade/low-balance pings
    # were too noisy - this one daily summary per platform is the
    # agreed replacement, not an addition on top of them.
    job_queue.run_daily(
        send_auto_copy_daily_digest,
        time=parse_time("23:59"),
        name="auto_copy_daily_digest",
        days=EVERY_DAY,
        job_kwargs={"misfire_grace_time": 300}
    )

    # MT5/Exness auto-trade daily digest - same schedule and same
    # once-a-day approach as the Deriv digest above.
    job_queue.run_daily(
        send_mt5_autotrade_daily_digest,
        time=parse_time("23:59"),
        name="mt5_autotrade_daily_digest",
        days=EVERY_DAY,
        job_kwargs={"misfire_grace_time": 300}
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
        days=(0,),
        job_kwargs={"misfire_grace_time": 300}
    )

    # Daily signal report - weekdays only (Mon-Fri) at 20:00 UTC (9PM
    # Lagos), per explicit instruction - both weekend days now
    # excluded. Saturday only ever has the single evening BTCUSD slot
    # (often still running at post time, as confirmed live), and
    # Sunday has no scheduled signals at all - both were consistently
    # near-empty or literally "No scheduled signals today" every
    # single week. Both now fold entirely into Sunday's weekly report
    # instead of their own thin/empty same-day posts. NOTE: PTB v20+
    # uses cron-style day indexing for run_daily's `days` param
    # (0=Sunday...6=Saturday), NOT Python's datetime.weekday()
    # convention - days=(1,2,3,4,5) is Monday through Friday,
    # correctly excluding both Sunday (0) and Saturday (6).
    job_queue.run_daily(
        post_daily_report,
        time=parse_time("20:00"),
        name="daily_report",
        days=(1, 2, 3, 4, 5),
        job_kwargs={"misfire_grace_time": 300}
    )

    print("Nexora AI Running...")
    print("Daily schedule (UTC):")
    for utc_time, post_type, data in DAILY_SCHEDULE:
        emoji = "📰" if post_type == "news" else "📊"
        weekend_note = "" if data == "btcusd" else " (weekdays only)"
        print(f"  {emoji} {utc_time} UTC — {data.upper()}{weekend_note}")
    for utc_time, schedule_type, slot_number in SYNTHETIC_SCHEDULE:
        note = {
            "sunday_only": "Sundays only",
            "saturday_only": "Saturdays only",
            "weekend": "weekends only",
            "weekday": "weekdays only",
        }.get(schedule_type, schedule_type)
        print(f"  ⚡ {utc_time} UTC — SYNTHETIC ROTATION ({note}, slot {slot_number})")
    print("  🔁 TP/SL monitor — every 15 minutes")
    print("  📊 20:00 UTC daily — DAILY SIGNAL REPORT")
    print("  📊 23:00 UTC Sunday — WEEKLY REPORT")
    print(f"Channel 1 (Public): {CHANNEL_1_ID}")
    print(f"Channel 2 (Inner Circle): {CHANNEL_2_ID}")
    print(f"Verify Group: {VERIFY_GROUP_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    # KoraPay webhook server - own thread, own event loop, deliberately
    # decoupled from the bot's own polling loop below (see
    # run_korapay_webhook_server's docstring for why). daemon=True so
    # it doesn't block the process from exiting on shutdown.
    threading.Thread(target=run_korapay_webhook_server, daemon=True).start()

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
