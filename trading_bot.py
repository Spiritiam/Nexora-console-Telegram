import os
import asyncio
import random
import requests
import threading
import schedule
import time

from datetime import datetime

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
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# ============================================
# CHANNEL IDs
# ============================================

CHANNEL_1_ID = os.getenv("CHANNEL_1_ID", "-1001722756645")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "-1002468228698")
VERIFY_GROUP_ID = "-1002400215654"

# ============================================
# CONSTANTS
# ============================================

EXNESS_LINK = "https://www.exness.com/boarding/sign-up/a/vlnafmua"
BOT_USERNAME = "NexoraConsoleBot"
FREE_TRIAL_LIMIT = 3

# ============================================
# IMAGE FILE IDs
# ============================================

BUY_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBFWowFvKE9iJ2rPQK6iqENojXggvJAAIyD2sbbT2BUfFOIeGp11tVAQADAgADeQADPAQ"
SELL_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBH2owIQ4F4GQEnXyDhVLRoQZ3Vg06AAI_D2sbbT2BUechitI61wpvAQADAgADeQADPAQ"

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

def get_channel_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤖 Get Your Own Signal",
            url=f"https://t.me/{BOT_USERNAME}"
        )]
    ])

# ============================================
# USER STATE
# ============================================

user_modes = {}
pending_verifications = {}

# Global bot reference for scheduler
bot_app = None

# ============================================
# SUPABASE
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
        r = requests.get(url, headers=sb_headers(), timeout=10)
        return len(r.json()) > 0
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
        print(f"[DB] ✅ Verified: {user_id}")
    except Exception as e:
        print(f"[DB] add_verified_user error: {e}")

def get_trial_count(user_id):
    try:
        url = (
            f"{SUPABASE_URL}/rest/v1/trial_users"
            f"?user_id=eq.{user_id}&select=count"
        )
        r = requests.get(url, headers=sb_headers(), timeout=10)
        data = r.json()
        return data[0].get("count", 0) if data else 0
    except Exception as e:
        print(f"[DB] get_trial_count error: {e}")
        return 0

def increment_trial(user_id):
    try:
        current = get_trial_count(user_id)
        new_count = current + 1
        url = f"{SUPABASE_URL}/rest/v1/trial_users"
        payload = {"user_id": str(user_id), "count": new_count}
        headers = {**sb_headers(), "Prefer": "resolution=merge-duplicates"}
        requests.post(url, headers=headers, json=payload, timeout=10)
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
        "mt5_symbol": "XAUUSDm",
        "display": "Gold (XAUUSD) 🥇",
        "av_symbol": "XAU",
        "av_type": "forex",
    },
    "btcusd": {
        "symbol": "BTC/USD",
        "pair_name": "BTCUSD",
        "pip_size": 300,
        "mt5_symbol": "BTCUSDm",
        "display": "Bitcoin (BTCUSD) ₿",
        "av_symbol": "BTC",
        "av_type": "crypto",
    },
    "xagusd": {
        "symbol": "XAG/USD",
        "pair_name": "XAGUSD",
        "pip_size": 0.30,
        "mt5_symbol": "XAGUSDm",
        "display": "Silver (XAGUSD) 🥈",
        "av_symbol": "XAG",
        "av_type": "forex",
    },
    "usoil": {
        "symbol": "USOIL",
        "pair_name": "USOIL",
        "pip_size": 0.50,
        "mt5_symbol": "USOILm",
        "display": "US Oil (WTI) 🛢️",
        "av_symbol": "WTI",
        "av_type": "commodity",
    },
    "gbpusd": {
        "symbol": "GBP/USD",
        "pair_name": "GBPUSD",
        "pip_size": 0.0025,
        "mt5_symbol": "GBPUSDm",
        "display": "GBP/USD 🇬🇧",
        "av_symbol": "GBP",
        "av_type": "forex",
    },
    "gbpjpy": {
        "symbol": "GBP/JPY",
        "pair_name": "GBPJPY",
        "pip_size": 0.30,
        "mt5_symbol": "GBPJPYm",
        "display": "GBP/JPY 🇯🇵",
        "av_symbol": "GBPJPY",
        "av_type": "forex",
    },
}

# ============================================
# LIVE PRICE
# ============================================

def get_price_twelvedata(symbol):
    try:
        url = (
            f"https://api.twelvedata.com/price"
            f"?symbol={symbol}&apikey={TWELVEDATA_API_KEY}"
        )
        r = requests.get(url, timeout=10)
        data = r.json()
        if "price" in data:
            return float(data["price"])
        return None
    except Exception as e:
        print(f"[TWELVEDATA] Error: {e}")
        return None

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
                f"&from_currency={av_symbol}&to_currency=USD"
                f"&apikey={ALPHA_VANTAGE_API_KEY}"
            )
            r = requests.get(url, timeout=10)
            rate = r.json().get(
                "Realtime Currency Exchange Rate", {}
            ).get("5. Exchange Rate")
            if rate:
                return float(rate)
        return None
    except Exception as e:
        print(f"[ALPHAVANTAGE] Error: {e}")
        return None

def get_live_price(symbol="XAU/USD", config=None):
    price = get_price_twelvedata(symbol)
    if price is not None:
        return price
    if config:
        price = get_price_alphavantage(config)
        if price is not None:
            return price
    print(f"[PRICE] Both APIs failed for {symbol}")
    return None

# ============================================
# SESSION
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
# MARKET BIAS
# ============================================

def generate_market_bias():
    direction = random.choice(["BUY", "SELL"])
    confidence = random.randint(80, 94)
    return direction, "STRONG", confidence

# ============================================
# REASONS
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
    pip_size = config["pip_size"]
    display = config["display"]

    live_price = get_live_price(symbol, config=config)
    if live_price is None:
        print(f"[SIGNAL] ❌ No price for {config['pair_name']}")
        return None, None, None, None

    direction, strength, confidence = generate_market_bias()

    if direction == "BUY":
        entry = round(live_price, 2)
        sl = round(live_price - (pip_size * 3), 2)
        tp = round(live_price + (pip_size * 6), 2)
        reason = random.choice(BUY_REASONS)
        emoji = "🟢"
        image = BUY_IMAGE_FILE_ID
    else:
        entry = round(live_price, 2)
        sl = round(live_price + (pip_size * 3), 2)
        tp = round(live_price - (pip_size * 6), 2)
        reason = random.choice(SELL_REASONS)
        emoji = "🔴"
        image = SELL_IMAGE_FILE_ID

    session = get_market_session()
    tf = random.choice([
        "M15 bullish structure confirmation",
        "H1 trend continuation active",
        "H4 momentum alignment confirmed",
        "Multi-timeframe confirmation detected",
        "Liquidity sweep confirmation on M15",
        "London session continuation setup",
        "New York volatility expansion detected",
    ])

    text = (
        f"{emoji} <b>{strength} {direction} {display}</b>\n\n"
        f"<b>Entry Price:</b> {entry}\n\n"
        f"<b>Stop Loss:</b> {sl}\n\n"
        f"<b>Take Profit:</b> {tp}\n\n"
        f"<b>Confidence:</b> {confidence}%\n\n"
        f"<b>Session:</b> {session}\n\n"
        f"<b>Timeframe Confirmation:</b>\n{tf}\n\n"
        f"<b>Reason:</b>\n{reason}\n\n"
        f"<i>Trade safe 💼🔥</i>"
    )

    signal_data = {
        "symbol": symbol,
        "pair_name": config["pair_name"],
        "mt5_symbol": config["mt5_symbol"],
        "direction": direction,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "config": config,
    }

    print(f"[SIGNAL] ✅ {config['pair_name']} | {direction} @ {entry}")
    return image, direction, text, signal_data

# ============================================
# FORMAT BREAKDOWN
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
        for e in emojis:
            text = text.replace(f"{e} {header}", f"{e} <b>{header}</b>")
        text = text.replace(f"\n{header}\n", f"\n<b>{header}</b>\n")
        text = text.replace(f"\n{header}:", f"\n<b>{header}:</b>")
    return text

def clean_text(text):
    text = text.replace("###", "").replace("##", "")
    text = text.replace("**", "").replace("---", "").replace("__", "")
    return text.strip()

# ============================================
# NEWS FETCHERS
# ============================================

def fetch_news_gnews():
    if not GNEWS_API_KEY:
        return None
    try:
        url = (
            f"https://gnews.io/api/v4/top-headlines"
            f"?category=business&lang=en&max=10&apikey={GNEWS_API_KEY}"
        )
        r = requests.get(url, timeout=10)
        articles = [
            a for a in r.json().get("articles", [])
            if a.get("image") and a.get("title") and a.get("description")
        ]
        if not articles:
            return None
        a = random.choice(articles)
        return {
            "title": a.get("title", ""),
            "description": a.get("description", ""),
            "image": a.get("image", ""),
            "source": a.get("source", {}).get("name", "GNews"),
        }
    except Exception as e:
        print(f"[GNEWS] Error: {e}")
        return None

def fetch_news_thenewsapi():
    if not THENEWS_API_KEY:
        return None
    try:
        url = (
            f"https://api.thenewsapi.com/v1/news/top"
            f"?api_token={THENEWS_API_KEY}"
            f"&categories=business,finance&language=en&limit=10"
        )
        r = requests.get(url, timeout=10)
        articles = [
            a for a in r.json().get("data", [])
            if a.get("image_url") and a.get("title") and a.get("description")
        ]
        if not articles:
            return None
        a = random.choice(articles)
        return {
            "title": a.get("title", ""),
            "description": a.get("description", ""),
            "image": a.get("image_url", ""),
            "source": a.get("source", "TheNewsAPI"),
        }
    except Exception as e:
        print(f"[THENEWSAPI] Error: {e}")
        return None

def fetch_market_news():
    article = fetch_news_gnews()
    if article:
        return article
    article = fetch_news_thenewsapi()
    if article:
        return article
    return None

# ============================================
# ECONOMIC CALENDAR
# ============================================

def fetch_economic_calendar():
    try:
        today = datetime.utcnow()
        date_str = today.strftime("%d.%m.%Y")
        today_str = today.strftime("%Y-%m-%d")

        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        r = requests.get(url, timeout=10)
        data = r.json()

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
            if event.get("date", "")[:10] != today_str:
                continue
            if event.get("impact", "").lower() != "high":
                continue

            title = event.get("title", "")
            currency = event.get("currency", "")
            time_utc = event.get("date", "")
            flag = flag_map.get(currency, "🌍")

            time_str = ""
            if time_utc and "T" in time_utc:
                try:
                    dt = datetime.strptime(time_utc[:16], "%Y-%m-%dT%H:%M")
                    lagos_hour = (dt.hour + 1) % 24
                    time_str = f"{lagos_hour:02d}:{dt.minute:02d} GMT+1"
                except:
                    pass

            line = f"{flag} {title}"
            if time_str:
                line += f" — {time_str}"
            calendar_text += line + "\n"
            count += 1
            if count >= 5:
                break

        return calendar_text if count > 0 else None

    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        return None

# ============================================
# AI FUNCTIONS
# ============================================

async def ask_gemini(prompt):
    headers = {"Content-Type": "application/json"}
    data = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        r = requests.post(GEMINI_URL, headers=headers, json=data, timeout=30)
        if r.status_code == 429:
            await asyncio.sleep(10)
            r = requests.post(GEMINI_URL, headers=headers, json=data, timeout=30)
            if r.status_code == 429:
                raise Exception("RATE_LIMIT")
        if r.status_code != 200:
            raise Exception("GEMINI_ERROR")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini Error: {e}")
        return await ask_openrouter(prompt)

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
        r = requests.post(OPENROUTER_URL, headers=headers, json=data, timeout=30)
        if r.status_code != 200:
            return "⚠️ AI server busy."
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"OpenRouter Error: {e}")
        return "⚠️ AI servers unavailable."

# ============================================
# NEWS SUMMARY
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

FORMAT EXACTLY LIKE THIS:
{session_label}

🔹 [One line news item 1]

🔹 [One line news item 2]

🔹 [One line news item 3]

STRICT RULES:
- Maximum 3 bullet points ONLY
- Each bullet point MAX 15 words
- No markdown symbols like ** or ##
- No hashtags
"""
    return await ask_gemini(prompt)

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
    live_price_text = str(round(live_price, 4)) if live_price else "unavailable"

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

PAIR: {pair_name}
LIVE PRICE: {live_price_text}
SESSION: {session}

FORMAT EXACTLY LIKE THIS:
📊 Technical Analysis
[analysis here]

📰 Fundamental Analysis
[analysis here]

💡 Market Sentiment
[sentiment here]

🎯 Trade Idea
[trade idea with entry, TP, SL]

RULES:
- Use the LIVE PRICE
- Maximum 250 words
- No markdown symbols like ** or ##
- No hashtags

QUESTION: {question}
"""
    return await ask_gemini(prompt)

# ============================================
# SCHEDULED TASKS
# ============================================

async def scheduled_news():
    global bot_app
    if bot_app is None:
        return

    print(f"[SCHEDULER] Posting morning news")

    article = fetch_market_news()
    if article is None:
        print("[NEWS] No article. Skipping.")
        return

    headline = article.get("title", "financial market news")
    image_prompt = (
        f"professional financial news illustration: {headline}, "
        f"cinematic digital art, dramatic lighting, high quality"
    )
    image_url = (
        f"https://image.pollinations.ai/prompt/"
        f"{requests.utils.quote(image_prompt)}"
        f"?width=800&height=450&nologo=true"
    )

    summary = await generate_news_summary(article, "morning")
    summary = clean_text(summary)

    calendar = fetch_economic_calendar()
    if calendar:
        summary += calendar

    try:
        await bot_app.bot.send_photo(
            chat_id=CHANNEL_1_ID,
            photo=image_url,
            caption=summary,
            parse_mode=ParseMode.HTML
        )
        print("[NEWS] ✅ Posted to Channel 1")
    except Exception as e:
        try:
            await bot_app.bot.send_message(
                chat_id=CHANNEL_1_ID,
                text=summary,
                parse_mode=ParseMode.HTML
            )
        except Exception as e2:
            print(f"[NEWS] ❌ Failed: {e2}")


async def scheduled_signal(pair_keyword):
    global bot_app
    if bot_app is None:
        return

    print(f"[SCHEDULER] Posting {pair_keyword.upper()} signal")

    image_file_id, direction, signal, signal_data = (
        build_signal_response(pair_keyword)
    )

    if signal_data is None:
        print(f"[SCHEDULER] ❌ No price for {pair_keyword}")
        return

    for channel_id in [CHANNEL_1_ID, CHANNEL_2_ID]:
        try:
            markup = (
                get_channel_button()
                if channel_id == CHANNEL_1_ID
                else None
            )
            await bot_app.bot.send_photo(
                chat_id=channel_id,
                photo=image_file_id,
                caption=signal,
                parse_mode=ParseMode.HTML,
                reply_markup=markup
            )
            print(f"[SCHEDULER] ✅ {pair_keyword.upper()} → {channel_id}")
        except Exception as e:
            print(f"[SCHEDULER] ❌ Failed: {e}")


def run_scheduled_task(coro):
    """Run async task from sync scheduler thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)
    loop.close()


def setup_scheduler():
    """Set up all scheduled tasks using the schedule library."""

    # 7:00 AM Lagos = 06:00 UTC — Morning News
    schedule.every().day.at("06:00").do(
        lambda: threading.Thread(
            target=run_scheduled_task,
            args=(scheduled_news(),)
        ).start()
    )

    # 8:00 AM Lagos = 07:00 UTC — Gold Signal
    schedule.every().day.at("07:00").do(
        lambda: threading.Thread(
            target=run_scheduled_task,
            args=(scheduled_signal("xauusd"),)
        ).start()
    )

    # 2:00 PM Lagos = 13:00 UTC — GBPJPY Signal
    schedule.every().day.at("13:00").do(
        lambda: threading.Thread(
            target=run_scheduled_task,
            args=(scheduled_signal("gbpjpy"),)
        ).start()
    )

    # 8:00 PM Lagos = 19:00 UTC — Bitcoin Signal
    schedule.every().day.at("19:00").do(
        lambda: threading.Thread(
            target=run_scheduled_task,
            args=(scheduled_signal("btcusd"),)
        ).start()
    )

    print("[SCHEDULER] ✅ All tasks scheduled")
    print("  📰 06:00 UTC — Morning News (Channel 1)")
    print("  📊 07:00 UTC — XAUUSD Gold (Both Channels)")
    print("  📊 13:00 UTC — GBPJPY (Both Channels)")
    print("  📊 19:00 UTC — BTCUSD Bitcoin (Both Channels)")

    # Run scheduler in background thread
    def run_forever():
        while True:
            schedule.run_pending()
            time.sleep(30)

    thread = threading.Thread(target=run_forever, daemon=True)
    thread.start()

# ============================================
# VERIFICATION GATE
# ============================================

async def send_verification_gate(update):
    await update.message.reply_text(
        "🔐 <b>You've used your 3 FREE trial signals!</b>\n\n"
        "Hope you loved what you saw! 🔥\n\n"
        "To continue enjoying <b>UNLIMITED FREE signals</b> — "
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
        "✅ <b>SITUATION 1</b> — Already registered on Exness:\n"
        "👉 Type the email you used to register below.\n\n"
        "📝 <b>SITUATION 2</b> — Not yet registered:\n"
        "👉 Click the button below to create your FREE Exness "
        "account. Once done, come back and type your email.\n\n"
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
# START
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"

    if is_verified(user_id):
        await update.message.reply_text(
            f"👋 <b>Welcome back, {username}!</b>\n\n"
            f"✅ You're a <b>verified Nexora AI trader.</b>\n\n"
            f"📊 <b>Signal</b> — Get a live trading signal\n\n"
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
            f"Your personal AI trading assistant.\n\n"
            f"🎁 <b>You have {remaining} FREE trial signal(s) to use!</b>\n\n"
            f"📊 <b>Signal</b> — Get a live trading signal\n\n"
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
            "Type the pair you want:\n\n"
            "• XAUUSD — Gold\n"
            "• BTCUSD — Bitcoin\n"
            "• GBPJPY\n"
            "• GBPUSD\n"
            "• XAGUSD — Silver\n"
            "• USOIL — US Oil\n\n"
            "<i>Example: Type <b>XAUUSD</b></i>",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

    if "breakdown" in text:
        user_modes[user_id] = "breakdown"
        await update.message.reply_text(
            "📚 <b>Breakdown Mode Activated</b>\n\n"
            "Type your market question:\n\n"
            "• Analyze gold market today\n"
            "• BTCUSD outlook\n"
            "• GBPJPY analysis",
            parse_mode=ParseMode.HTML,
            reply_markup=main_keyboard
        )
        return

# ============================================
# CALLBACK HANDLER
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
        except Exception as e:
            print(f"[INVITE] Error: {e}")

        try:
            if inner_circle_link:
                await context.bot.send_message(
                    chat_id=int(target_id),
                    text=(
                        "🎉 <b>Congratulations! You're now a verified "
                        "Nexora AI trader!</b>\n\n"
                        "✅ <b>Full access unlocked!</b>\n\n"
                        "📊 Live signals — Gold, Bitcoin, Forex and more\n\n"
                        "📚 AI market breakdowns — any pair\n\n"
                        "📈 Professional grade insights\n\n"
                        "━━━━━━━━━━━━━━━━━━━━\n"
                        "🔐 <b>INNER CIRCLE ACCESS</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━\n\n"
                        "👇 <b>Your personal invite — works once:</b>"
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
                        "🎉 <b>Congratulations! You're now verified!</b>\n\n"
                        "✅ Full access to all signals and breakdowns unlocked!"
                    ),
                    parse_mode=ParseMode.HTML,
                )

            await context.bot.send_message(
                chat_id=int(target_id),
                text=(
                    "💼 <i>Welcome to the winning side. "
                    "Let's get to work!</i> 🔥\n\n"
                    "📊 <b>Signal</b> — Get a live trading signal\n\n"
                    "📚 <b>Breakdown</b> — Get a full market analysis"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=main_keyboard
            )

        except Exception as e:
            print(f"[APPROVE] Error: {e}")

        await query.edit_message_text(
            text=(
                f"✅ <b>APPROVED</b>\n\n"
                f"🆔 {target_id}\n📧 {email}\n\n"
                f"<i>Verified and Inner Circle invite sent.</i>"
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
                    "We could not find your Exness account "
                    "registered through our link.\n\n"
                    "Register a NEW account using our link below:\n\n"
                    f"🔗 {EXNESS_LINK}\n\n"
                    "Then come back and type your new email. 🚀"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[REJECT] Error: {e}")

        await query.edit_message_text(
            text=f"❌ <b>REJECTED</b>\n\n🆔 {target_id}\n📧 {email}",
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
                "⚠️ Please enter a valid email address 👇\n\n"
                "<i>Example: yourname@gmail.com</i>",
                parse_mode=ParseMode.HTML
            )
            return

        pending_verifications[user_id] = email

        await update.message.reply_text(
            "⏳ <b>Verification request submitted!</b>\n\n"
            "Our team is reviewing your details shortly.\n\n"
            "<i>Sit tight! 🚀</i>",
            parse_mode=ParseMode.HTML
        )

        try:
            await context.bot.send_message(
                chat_id=VERIFY_GROUP_ID,
                text=(
                    f"🔔 <b>NEW VERIFICATION REQUEST</b>\n\n"
                    f"👤 @{username}\n"
                    f"🆔 {user_id}\n"
                    f"📧 {email}\n\n"
                    f"<i>Tap to approve or reject:</i>"
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
            print(f"[VERIFY] Failed: {e}")

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

        wait_msg = await update.message.reply_text(
            "🧠 <b>Nexora AI analyzing live market...</b>",
            parse_mode=ParseMode.HTML
        )
        await asyncio.sleep(1)

        image_file_id, direction, signal, signal_data = (
            build_signal_response(message)
        )
        await wait_msg.delete()

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
                        f"<b>unlimited access!</b>",
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

        wait_msg = await update.message.reply_text(
            "🧠 <b>Nexora AI preparing market breakdown...</b>",
            parse_mode=ParseMode.HTML
        )

        response = await generate_breakdown(message)
        response = clean_text(response)
        response = format_breakdown(response)

        await wait_msg.edit_text(response, parse_mode=ParseMode.HTML)

        if not is_verified(user_id):
            remaining = trial_remaining(user_id)
            if remaining > 0:
                await update.message.reply_text(
                    f"⚡ <b>You have {remaining} free trial "
                    f"signal(s) remaining.</b>\n\n"
                    f"Verify your Exness account for "
                    f"<b>unlimited access!</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=main_keyboard
                )
            else:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

    await update.message.reply_text(
        "👇 <b>Here's what you can do:</b>\n\n"
        "📊 <b>Signal</b> — Get a live trading signal\n\n"
        "📚 <b>Breakdown</b> — Get a full AI market analysis\n\n"
        "<i>Both buttons are at the bottom of your screen!</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard
    )

# ============================================
# MAIN
# ============================================

def main():
    global bot_app

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("Nexora AI Starting...")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Build app WITHOUT job queue dependency
    bot_app = Application.builder().token(TELEGRAM_TOKEN).build()

    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CallbackQueryHandler(handle_callback))
    bot_app.add_handler(
        MessageHandler(
            filters.Regex("^(📊 Signal|📚 Breakdown|signal|breakdown)$"),
            handle_buttons
        )
    )
    bot_app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    # Start scheduler in background
    setup_scheduler()

    print(f"Channel 1 (Public):       {CHANNEL_1_ID}")
    print(f"Channel 2 (Inner Circle): {CHANNEL_2_ID}")
    print(f"Bot: @{BOT_USERNAME}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[BOT] ✅ Nexora AI is LIVE!")

    bot_app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
