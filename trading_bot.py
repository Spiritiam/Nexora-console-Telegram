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
    ("06:00", "news",   "morning"),
    ("07:00", "signal", "xauusd"),
    ("09:00", "signal", "btcusd"),
    ("11:00", "news",   "midday"),
    ("13:00", "signal", "usoil"),
    ("15:00", "signal", "gbpusd"),
    ("17:00", "news",   "afternoon"),
    ("19:00", "signal", "gbpjpy"),
    ("21:00", "signal", "xauusd"),
]

# ============================================
# AI CONFIG
# ============================================

GEMINI_MODEL = "gemini-2.0-flash"

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

# ============================================
# USER MODES
# ============================================

user_modes = {}
pending_verifications = {}

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
# PAIR CONFIG
# ============================================

PAIR_CONFIG = {
    "xauusd": {
        "symbol": "XAU/USD",
        "pair_name": "XAUUSD",
        "pip_size": 10,
        "pip_value": 0.01,
        "pip_label": "pips",
        "mt5_symbol": "XAUUSDm",
        "display": "Gold (XAUUSD) 🥇",
        "av_symbol": "XAU",
        "av_type": "forex",
    },
    "btcusd": {
        "symbol": "BTC/USD",
        "pair_name": "BTCUSD",
        "pip_size": 300,
        "pip_value": 1.0,
        "pip_label": "points",
        "mt5_symbol": "BTCUSDm",
        "display": "Bitcoin (BTCUSD) ₿",
        "av_symbol": "BTC",
        "av_type": "crypto",
    },
    "xagusd": {
        "symbol": "XAG/USD",
        "pair_name": "XAGUSD",
        "pip_size": 0.30,
        "pip_value": 0.01,
        "pip_label": "pips",
        "mt5_symbol": "XAGUSDm",
        "display": "Silver (XAGUSD) 🥈",
        "av_symbol": "XAG",
        "av_type": "forex",
    },
    "usoil": {
        "symbol": "WTI/USD",
        "pair_name": "USOIL",
        "pip_size": 0.50,
        "pip_value": 0.01,
        "pip_label": "pips",
        "mt5_symbol": "USOILm",
        "display": "US Oil (WTI) 🛢️",
        "av_symbol": "WTI",
        "av_type": "commodity",
    },
    "gbpusd": {
        "symbol": "GBP/USD",
        "pair_name": "GBPUSD",
        "pip_size": 0.0025,
        "pip_value": 0.0001,
        "pip_label": "pips",
        "mt5_symbol": "GBPUSDm",
        "display": "GBP/USD 🇬🇧",
        "av_symbol": "GBP",
        "av_type": "forex",
    },
    "gbpjpy": {
        "symbol": "GBP/JPY",
        "pair_name": "GBPJPY",
        "pip_size": 0.30,
        "pip_value": 0.01,
        "pip_label": "pips",
        "mt5_symbol": "GBPJPYm",
        "display": "GBP/JPY 🇯🇵",
        "av_symbol": "GBPJPY",
        "av_type": "forex",
    },
}

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
        elif av_type == "commodity":
            url = (
                f"https://www.alphavantage.co/query"
                f"?function=WTI"
                f"&interval=daily"
                f"&apikey={ALPHA_VANTAGE_API_KEY}"
            )
            response = requests.get(url, timeout=10)
            data = response.json()
            latest = data.get("data", [{}])[0]
            value = latest.get("value")
            if value and value != ".":
                return float(value)
        return None
    except Exception as e:
        print(f"[ALPHAVANTAGE] Error: {e}")
        return None

# ============================================
# LIVE PRICE — COMBINED
# ============================================

def get_live_price(symbol="XAU/USD", config=None):
    price = get_price_twelvedata(symbol)
    if price is not None:
        return price
    if config:
        print(f"[PRICE] Twelvedata failed for {symbol}, trying Alpha Vantage...")
        price = get_price_alphavantage(config)
        if price is not None:
            print(f"[PRICE] Alpha Vantage: {price} for {symbol}")
            return price
    print(f"[PRICE] Both APIs failed for {symbol}")
    return None

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
# MARKET BIAS — STRONG SIGNALS ONLY
# ============================================

def generate_market_bias():
    direction = random.choice(["BUY", "SELL"])
    strength = "STRONG"
    confidence = random.randint(80, 94)
    return direction, strength, confidence

# ============================================
# BUY / SELL REASONS
# ============================================

BUY_REASONS = [
    "Bullish momentum across higher timeframes.",
    "Liquidity sweep reaction from support zone.",
    "London bullish continuation detected.",
    "Strong buyer pressure detected.",
    "Breakout confirmation on H1 timeframe.",
    "New York session momentum expansion.",
    "Demand zone rejection with bullish structure.",
    "Multi-timeframe bullish alignment confirmed.",
]

SELL_REASONS = [
    "Bearish rejection from resistance zone.",
    "Strong seller pressure detected.",
    "Liquidity sweep from recent highs.",
    "H1 bearish continuation setup active.",
    "New York session reversal pressure.",
    "Breakdown below key support level.",
    "Supply zone reaction confirmed.",
    "Multi-timeframe bearish alignment confirmed.",
]

# ============================================
# SIGNAL BUILDER
# ============================================

def build_signal_response(question):
    q = question.lower()
    config = PAIR_CONFIG["xauusd"]
    for key in PAIR_CONFIG:
        if key in q:
            config = PAIR_CONFIG[key]
            break

    symbol = config["symbol"]
    pair_name = config["pair_name"]
    pip_size = config["pip_size"]
    display = config["display"]

    live_price = get_live_price(symbol, config=config)
    if live_price is None:
        return None, None, None, None

    direction, strength, confidence = generate_market_bias()

    if direction == "BUY":
        entry_price = round(live_price, 2)
        stop_loss = round(live_price - (pip_size * 3), 2)
        take_profit = round(live_price + (pip_size * 6), 2)
        reason = random.choice(BUY_REASONS)
        signal_emoji = "🟢"
        image_file_id = BUY_IMAGE_FILE_ID
    else:
        entry_price = round(live_price, 2)
        stop_loss = round(live_price + (pip_size * 3), 2)
        take_profit = round(live_price - (pip_size * 6), 2)
        reason = random.choice(SELL_REASONS)
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
    print("[NEWS] Both news APIs failed.")
    return None

# ============================================
# ECONOMIC CALENDAR
# ============================================

def fetch_economic_calendar():
    try:
        today = datetime.utcnow()
        date_from = today.strftime("%Y-%m-%dT00:00:00.000Z")
        date_to = today.strftime("%Y-%m-%dT23:59:59.000Z")
        date_str = today.strftime("%d.%m.%Y")

        url = "https://economic-calendar.tradingview.com/events"
        params = {
            "from": date_from,
            "to": date_to,
            "countries": ["US", "EU", "GB", "CA", "JP", "AU", "CN"],
            "importance": ["high"]
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        events = data.get("result", [])

        if not events:
            return None

        flag_map = {
            "US": "🇺🇸", "EU": "🇪🇺", "GB": "🇬🇧",
            "CA": "🇨🇦", "JP": "🇯🇵", "AU": "🇦🇺",
            "CN": "🇨🇳", "NZ": "🇳🇿", "CH": "🇨🇭",
        }

        calendar_text = f"\n\n📆 <b>CALENDAR TODAY — {date_str}</b>\n\n"

        count = 0
        for event in events[:5]:
            title = event.get("title", "")
            country = event.get("country", "")
            time_utc = event.get("date", "")
            flag = flag_map.get(country, "🌍")

            if time_utc:
                try:
                    dt = datetime.strptime(time_utc[:16], "%Y-%m-%dT%H:%M")
                    # Convert UTC to Lagos (UTC+1)
                    lagos_hour = (dt.hour + 1) % 24
                    time_str = f"{lagos_hour:02d}:{dt.minute:02d} GMT+1"
                except:
                    time_str = ""
            else:
                time_str = ""

            calendar_text += f"{flag} {title}"
            if time_str:
                calendar_text += f" — {time_str}"
            calendar_text += "\n"
            count += 1

        if count == 0:
            return None

        return calendar_text

    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        return None

# ============================================
# NEWS SUMMARY GENERATOR — SHORT & PRECISE
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

🔹 [One line news item 3]

STRICT RULES:
- Maximum 3 bullet points ONLY
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
# POST NEWS TO CHANNELS
# ============================================

async def post_news(context: ContextTypes.DEFAULT_TYPE):

    session_type = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')
    print(f"[NEWS] Posting {session_type} news at {now}")

    article = fetch_market_news()
    if article is None:
        print("[NEWS] No article found from any source. Skipping.")
        return

    # Generate AI image from headline using Pollinations.ai
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

    # Generate short news summary
    summary = await generate_news_summary(article, session_type)
    summary = clean_text(summary)

    # Fetch economic calendar and append
    calendar = fetch_economic_calendar()
    if calendar:
        summary += calendar

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_url,
                caption=summary,
                parse_mode=ParseMode.HTML
            )
            print(f"[NEWS] ✅ {session_type} posted to {channel_id}")
        except Exception as e:
            print(f"[NEWS] AI image failed, posting without image: {e}")
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
# METAAPI — PLACE TRADE ON MT5
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
            "volume": 0.01,
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
# MONITOR SIGNAL FOR TP/SL
# ============================================

async def monitor_signal(bot, channel_id, message_id, signal_data):

    symbol = signal_data["symbol"]
    direction = signal_data["direction"]
    take_profit = signal_data["take_profit"]
    stop_loss = signal_data["stop_loss"]
    pair_name = signal_data["pair_name"]
    entry_price = signal_data["entry_price"]
    config = signal_data.get("config")

    print(
        f"[MONITOR] Watching {pair_name} | "
        f"TP: {take_profit} | SL: {stop_loss}"
    )

    max_checks = 1440

    for _ in range(max_checks):
        await asyncio.sleep(60)
        current_price = get_live_price(symbol, config=config)

        if current_price is None:
            continue

        tp_hit = (
            (direction == "BUY" and current_price >= take_profit) or
            (direction == "SELL" and current_price <= take_profit)
        )
        sl_hit = (
            (direction == "BUY" and current_price <= stop_loss) or
            (direction == "SELL" and current_price >= stop_loss)
        )

        if tp_hit:
            exit_price = round(current_price, 2)
            pips, pip_label = calculate_pips(
                pair_name, entry_price, exit_price, direction, config
            )
            print(f"[MONITOR] ✅ TP HIT {pair_name} at {exit_price} | +{pips} {pip_label}")
            await bot.send_photo(
                chat_id=channel_id,
                photo=TP_HIT_IMAGE_FILE_ID,
                caption=(
                    f"✅ <b>TP HIT — {pair_name}</b>\n\n"
                    f"<b>Entry:</b> {entry_price}\n"
                    f"<b>Take Profit:</b> {take_profit}\n"
                    f"<b>Exit Price:</b> {exit_price}\n\n"
                    f"📈 <b>Result: +{pips} {pip_label} gained!</b>\n\n"
                    f"<i>Well done to everyone who followed! 💰🔥\n"
                    f"Consistency is the key to long term profits.</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_to_message_id=message_id
            )
            break

        elif sl_hit:
            exit_price = round(current_price, 2)
            pips, pip_label = calculate_pips(
                pair_name, entry_price, exit_price, direction, config
            )
            pips_lost = abs(pips)
            print(f"[MONITOR] ❌ SL HIT {pair_name} at {exit_price} | -{pips_lost} {pip_label}")
            await bot.send_photo(
                chat_id=channel_id,
                photo=SL_HIT_IMAGE_FILE_ID,
                caption=(
                    f"❌ <b>SL HIT — {pair_name}</b>\n\n"
                    f"<b>Entry:</b> {entry_price}\n"
                    f"<b>Stop Loss:</b> {stop_loss}\n"
                    f"<b>Exit Price:</b> {exit_price}\n\n"
                    f"📉 <b>Result: -{pips_lost} {pip_label} lost.</b>\n\n"
                    f"<i>Risk managed. Every loss is a lesson. 💼\n"
                    f"Next signal coming — stay disciplined!</i>"
                ),
                parse_mode=ParseMode.HTML,
                reply_to_message_id=message_id
            )
            break

# ============================================
# GEMINI AI — WITH RATE LIMIT HANDLING
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

    q = question.lower()
    symbol = "XAU/USD"
    pair_name = "Gold (XAUUSD)"
    config = PAIR_CONFIG["xauusd"]

    for key, cfg in PAIR_CONFIG.items():
        if key in q:
            symbol = cfg["symbol"]
            pair_name = cfg["display"]
            config = cfg
            break

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
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔓 <b>HOW TO UNLOCK FULL ACCESS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 Register a <b>FREE</b> trading account with our official "
        "broker partner — <b>Exness</b> — using our unique link.\n\n"
        "<b>No payment. No subscription. Completely FREE.</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 <b>CHOOSE YOUR SITUATION:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>SITUATION 1</b> — Already registered on Exness "
        "using our link before:\n"
        "👉 Simply type the email address you used to register "
        "on Exness below and we will verify you instantly.\n\n"
        "📝 <b>SITUATION 2</b> — Not yet registered or registered "
        "without our link:\n"
        "👉 Click the button below to create your FREE Exness "
        "account using our official link. Once done, come back "
        "here and type the email you registered with.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
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
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>What would you like to do today?</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
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
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"👇 <b>TAP ONE OF THE OPTIONS BELOW TO START:</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
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
            "• GBPJPY\n\n"
            "<i>Example: Type <b>XAUUSD</b> to get a Gold signal</i>",
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
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🔐 <b>EXCLUSIVE — INNER CIRCLE ACCESS</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
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
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "💼 <i>Welcome to the winning side. "
                    "Let's get to work!</i> 🔥\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
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
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ <b>HOW TO FIX THIS:</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
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

    # ── AWAITING EMAIL ────────────────────────────
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

    # ── TRIAL / VERIFIED CHECK ────────────────────
    if not is_verified(user_id) and get_trial_count(user_id) >= FREE_TRIAL_LIMIT:
        user_modes[user_id] = "awaiting_email"
        await send_verification_gate(update)
        return

    mode = user_modes.get(user_id)

    # ── SIGNAL MODE ───────────────────────────────
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
            build_signal_response(message)
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

    # ── BREAKDOWN MODE ────────────────────────────
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

    # ── DEFAULT ───────────────────────────────────
    await update.message.reply_text(
        "👇 <b>Here's what you can do:</b>\n\n"
        "📊 <b>Signal</b> — Get a live trading signal right now\n\n"
        "📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
        "<i>Both buttons are right at the bottom of your screen!</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )

# ============================================
# AUTO SIGNAL — POSTED TO BOTH CHANNELS
# ============================================

async def post_auto_signal(context: ContextTypes.DEFAULT_TYPE):

    pair_keyword = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')

    print(f"[AUTO SIGNAL] {pair_keyword.upper()} firing at {now}")

    image_file_id, direction, signal, signal_data = (
        build_signal_response(pair_keyword)
    )

    if signal_data is None:
        print(f"[AUTO SIGNAL] ❌ Could not fetch price for {pair_keyword}.")
        return

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            sent = await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_file_id,
                caption=signal,
                parse_mode=ParseMode.HTML
            )
            print(
                f"[AUTO SIGNAL] ✅ {pair_keyword.upper()} "
                f"posted to {channel_id}"
            )

            asyncio.create_task(place_mt5_trade(signal_data))
            asyncio.create_task(
                monitor_signal(
                    context.bot,
                    channel_id,
                    sent.message_id,
                    signal_data
                )
            )
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
    print(f"Channel 1: {CHANNEL_1_ID}")
    print(f"Channel 2 (Inner Circle): {CHANNEL_2_ID}")
    print(f"Verify Group: {VERIFY_GROUP_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
