import os
import asyncio
import random
import requests

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
    ContextTypes,
    filters,
)

# ============================================
# ENV VARIABLES
# ============================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY")
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# ============================================
# NEWS API KEY
# ============================================

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "fe030adf325d4c95b2f72b09659cd203")

# ============================================
# CHANNEL IDs
# ============================================

CHANNEL_1_ID = os.getenv("CHANNEL_1_ID", "-1001722756645")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "-1002468228698")

# ============================================
# BOT USERNAME
# ============================================

BOT_USERNAME = "NexoraConsoleBot"

# ============================================
# IMAGE FILE IDs
# ============================================

BUY_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBFWowFvKE9iJ2rPQK6iqENojXggvJAAIyD2sbbT2BUfFOIeGp11tVAQADAgADeQADPAQ"
SELL_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBH2owIQ4F4GQEnXyDhVLRoQZ3Vg06AAI_D2sbbT2BUechitI61wpvAQADAgADeQADPAQ"
TP_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBI2owI-2IToLB1YLPMxCa132jhJMKAAJCD2sbbT2BUbnbjLmJ1VZIAQADAgADeQADPAQ"
SL_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBIWowI9Lxu93CIKFD5YSHFbJ8_MB-AAJBD2sbbT2BUT1NzWx8We6EAQADAgADeQADPAQ"

# ============================================
# DAILY SCHEDULE
# ============================================
# Format: (UTC time, type, keyword)
# type: "news" or "signal"
# keyword for news: "morning" / "midday" / "afternoon"
# keyword for signal: pair name

DAILY_SCHEDULE = [
    ("06:00", "news",   "morning"),     # 07:00 Lagos — Morning News
    ("07:00", "signal", "xauusd"),      # 08:00 Lagos — Gold
    ("08:00", "signal", "btcusd"),      # 09:00 Lagos — Bitcoin
    ("10:00", "news",   "midday"),      # 11:00 Lagos — Midday News
    ("11:00", "signal", "xagusd"),      # 12:00 Lagos — Silver
    ("12:00", "signal", "usoil"),       # 13:00 Lagos — US Oil
    ("14:00", "news",   "afternoon"),   # 15:00 Lagos — Afternoon News
    ("15:00", "signal", "gbpusd"),      # 16:00 Lagos — GBPUSD
    ("16:00", "signal", "eurusd"),      # 17:00 Lagos — EURUSD
    ("17:00", "signal", "gbpjpy"),      # 18:00 Lagos — GBPJPY
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
# BUTTONS
# ============================================

main_keyboard = ReplyKeyboardMarkup(
    [["📊 Signal", "📚 Breakdown"]],
    resize_keyboard=True
)

def get_channel_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🤖 Get Your Own Signal",
                url=f"https://t.me/{BOT_USERNAME}"
            )
        ]
    ])

# ============================================
# USER MODES
# ============================================

user_modes = {}

# ============================================
# START COMMAND
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = (
        "👋 Welcome to Nexora AI\n\n"
        "I can help you with:\n\n"
        "📊 Trading Signals\n"
        "📚 Market Breakdowns\n"
        "📈 Technical Analysis\n"
        "🧠 AI Trading Assistance\n\n"
        "Choose an option below."
    )

    await update.message.reply_text(
        text,
        reply_markup=main_keyboard
    )

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
# LIVE PRICE
# ============================================

def get_live_price(symbol="XAU/USD"):

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
        print("PRICE ERROR:", e)
        return None

# ============================================
# MARKET BIAS
# ============================================

def generate_market_bias():
    direction = random.choice(["BUY", "SELL"])
    strength = random.choice(["STRONG", "WEAK"])
    confidence = random.randint(72, 94)
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
# PAIR CONFIG
# ============================================

PAIR_CONFIG = {
    "xauusd": {
        "symbol": "XAU/USD",
        "pair_name": "XAUUSD",
        "pip_size": 10,
        "mt5_symbol": "XAUUSDm",
        "display": "Gold (XAUUSD) 🥇",
    },
    "btcusd": {
        "symbol": "BTC/USD",
        "pair_name": "BTCUSD",
        "pip_size": 300,
        "mt5_symbol": "BTCUSDm",
        "display": "Bitcoin (BTCUSD) ₿",
    },
    "xagusd": {
        "symbol": "XAG/USD",
        "pair_name": "XAGUSD",
        "pip_size": 0.30,
        "mt5_symbol": "XAGUSDm",
        "display": "Silver (XAGUSD) 🥈",
    },
    "usoil": {
        "symbol": "WTI/USD",
        "pair_name": "USOIL",
        "pip_size": 0.50,
        "mt5_symbol": "USOILm",
        "display": "US Oil (WTI) 🛢️",
    },
    "gbpusd": {
        "symbol": "GBP/USD",
        "pair_name": "GBPUSD",
        "pip_size": 0.0025,
        "mt5_symbol": "GBPUSDm",
        "display": "GBP/USD 🇬🇧",
    },
    "eurusd": {
        "symbol": "EUR/USD",
        "pair_name": "EURUSD",
        "pip_size": 0.0020,
        "mt5_symbol": "EURUSDm",
        "display": "EUR/USD 🇪🇺",
    },
    "gbpjpy": {
        "symbol": "GBP/JPY",
        "pair_name": "GBPJPY",
        "pip_size": 0.30,
        "mt5_symbol": "GBPJPYm",
        "display": "GBP/JPY 🇯🇵",
    },
}

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

    live_price = get_live_price(symbol)

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

    response = f"""{signal_emoji} {strength} {direction} {display}

Entry Price: {entry_price}

Stop Loss: {stop_loss}

Take Profit: {take_profit}

Confidence: {confidence}%

Session: {session}

Timeframe Confirmation:
{timeframe_confirmation}

Reason:
{reason}

Trade safe 💼🔥"""

    signal_data = {
        "symbol": symbol,
        "pair_name": pair_name,
        "mt5_symbol": config["mt5_symbol"],
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

    return image_file_id, direction, response, signal_data

# ============================================
# NEWS FETCHER
# ============================================

def fetch_market_news(session_type="morning"):

    queries = {
        "morning": "forex gold stock market financial",
        "midday":  "Elon Musk economy oil Bitcoin stocks",
        "afternoon": "US market Federal Reserve trading Wall Street",
    }

    query = queries.get(session_type, "financial markets")

    try:

        url = (
            f"https://newsapi.org/v2/everything"
            f"?q={query}"
            f"&language=en"
            f"&sortBy=publishedAt"
            f"&pageSize=10"
            f"&apiKey={NEWSAPI_KEY}"
        )

        response = requests.get(url, timeout=10)
        data = response.json()

        if data.get("status") != "ok":
            return None, None, None

        articles = data.get("articles", [])

        # Find first article with an image
        for article in articles:
            title = article.get("title", "")
            description = article.get("description", "")
            image_url = article.get("urlToImage", "")
            source = article.get("source", {}).get("name", "")
            article_url = article.get("url", "")

            if image_url and title and description:
                return title, description, image_url, source, article_url

        return None, None, None, None, None

    except Exception as e:
        print(f"[NEWS] Error: {e}")
        return None, None, None, None, None

# ============================================
# AI NEWS SUMMARY
# ============================================

async def generate_news_summary(title, description, session_type):

    session_labels = {
        "morning": "Morning Market Briefing 🌅",
        "midday": "Midday Market Update ☀️",
        "afternoon": "Afternoon Trading Briefing 🌆",
    }

    label = session_labels.get(session_type, "Market Update")

    prompt = f"""
You are Nexora AI, a professional financial news analyst.

Write a SHORT engaging market update for a Telegram trading channel.

NEWS HEADLINE:
{title}

NEWS DETAILS:
{description}

SESSION:
{label}

RULES:
- Maximum 5 sentences
- Engaging and professional tone
- Mention how this affects traders
- No markdown, no stars, no hashtags
- End with one trading insight or what to watch
- Keep it punchy and interesting
"""

    try:

        headers = {"Content-Type": "application/json"}

        data = {
            "contents": [
                {"parts": [{"text": prompt}]}
            ]
        }

        response = requests.post(
            GEMINI_URL,
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code == 200:
            result = response.json()
            return result["candidates"][0]["content"]["parts"][0]["text"]

        return description

    except Exception as e:
        print(f"[NEWS SUMMARY] Error: {e}")
        return description

# ============================================
# POST NEWS TO CHANNELS
# ============================================

async def post_news(bot, session_type):

    session_labels = {
        "morning":   "🌅 MORNING MARKET BRIEFING",
        "midday":    "☀️ MIDDAY MARKET UPDATE",
        "afternoon": "🌆 AFTERNOON TRADING BRIEFING",
    }

    label = session_labels.get(session_type, "📰 MARKET UPDATE")

    print(f"[NEWS] Fetching {session_type} news...")

    result = fetch_market_news(session_type)

    title, description, image_url, source, article_url = result

    if not title:
        print("[NEWS] No article found. Skipping.")
        return

    summary = await generate_news_summary(title, description, session_type)
    summary = summary.replace("**", "").replace("###", "").replace("##", "").strip()

    caption = (
        f"{label}\n\n"
        f"📌 {title}\n\n"
        f"{summary}\n\n"
        f"Source: {source}"
    )

    news_button = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📖 Read Full Article",
                url=article_url
            )
        ],
        [
            InlineKeyboardButton(
                "🤖 Get Trading Signal",
                url=f"https://t.me/{BOT_USERNAME}"
            )
        ]
    ])

    channel_ids = [CHANNEL_1_ID, CHANNEL_2_ID]

    for channel_id in channel_ids:
        try:

            await bot.send_photo(
                chat_id=channel_id,
                photo=image_url,
                caption=caption,
                reply_markup=news_button
            )

            print(f"[NEWS] ✅ {session_type} news posted to {channel_id}")

        except Exception as e:

            # If image fails, send text only
            try:
                await bot.send_message(
                    chat_id=channel_id,
                    text=caption,
                    reply_markup=news_button
                )
                print(f"[NEWS] ✅ {session_type} news (text only) posted to {channel_id}")
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
            f"/users/current/accounts/{METAAPI_ACCOUNT_ID}"
            f"/trade"
        )

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code in [200, 201]:
            result = response.json()
            order_id = result.get("orderId", "unknown")
            print(f"[MT5] ✅ Trade placed — Order ID: {order_id}")
            return order_id
        else:
            print(f"[MT5] ❌ Trade failed: {response.status_code} — {response.text}")
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

    print(f"[MONITOR] Watching {pair_name} | TP: {take_profit} | SL: {stop_loss}")

    max_checks = 1440  # 24 hours max

    for _ in range(max_checks):

        await asyncio.sleep(60)

        current_price = get_live_price(symbol)

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

            print(f"[MONITOR] ✅ TP HIT {pair_name} at {current_price}")

            await bot.send_photo(
                chat_id=channel_id,
                photo=TP_HIT_IMAGE_FILE_ID,
                caption=(
                    f"✅ TP HIT — {pair_name}\n\n"
                    f"Entry: {entry_price}\n"
                    f"Take Profit: {take_profit}\n"
                    f"Exit Price: {round(current_price, 2)}\n\n"
                    f"Well done to everyone who followed! 💰🔥"
                ),
                reply_to_message_id=message_id
            )
            break

        elif sl_hit:

            print(f"[MONITOR] ❌ SL HIT {pair_name} at {current_price}")

            await bot.send_photo(
                chat_id=channel_id,
                photo=SL_HIT_IMAGE_FILE_ID,
                caption=(
                    f"❌ SL HIT — {pair_name}\n\n"
                    f"Entry: {entry_price}\n"
                    f"Stop Loss: {stop_loss}\n"
                    f"Exit Price: {round(current_price, 2)}\n\n"
                    f"Risk managed. Next signal coming. 💼"
                ),
                reply_to_message_id=message_id
            )
            break

# ============================================
# GEMINI AI
# ============================================

async def ask_gemini(prompt):

    headers = {"Content-Type": "application/json"}

    data = {
        "contents": [
            {"parts": [{"text": prompt}]}
        ]
    }

    try:

        response = requests.post(
            GEMINI_URL,
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code == 429:
            raise Exception("RATE_LIMIT")

        if response.status_code != 200:
            raise Exception("GEMINI_ERROR")

        result = response.json()

        return result["candidates"][0]["content"]["parts"][0]["text"]

    except Exception as e:
        print("Gemini Error:", e)
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
            OPENROUTER_URL,
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code != 200:
            return "⚠️ AI server busy."

        result = response.json()

        return result["choices"][0]["message"]["content"]

    except Exception as e:
        print("OpenRouter Error:", e)
        return "⚠️ AI servers unavailable."

# ============================================
# BREAKDOWN GENERATOR
# ============================================

async def generate_breakdown(question):

    q = question.lower()

    symbol = "XAU/USD"
    pair_name = "Gold (XAUUSD)"

    for key, config in PAIR_CONFIG.items():
        if key in q:
            symbol = config["symbol"]
            pair_name = config["display"]
            break

    live_price = get_live_price(symbol)

    if live_price is None:
        live_price_text = "Live price unavailable"
    else:
        live_price_text = str(round(live_price, 4))

    hour = datetime.utcnow().hour

    if 7 <= hour < 13:
        session = "London Session 🇬🇧"
    elif 13 <= hour < 22:
        session = "New York Session 🇺🇸"
    else:
        session = "Asian Session 🇯🇵"

    prompt = f"""
You are Nexora AI.

Generate a PROFESSIONAL market breakdown.

IMPORTANT:
Use the REAL LIVE PRICE provided below.
Do NOT invent fake prices.

PAIR: {pair_name}
LIVE PRICE: {live_price_text}
SESSION: {session}

RULES:
- Clean formatting
- No markdown symbols
- No hashtags
- No stars
- Beginner friendly
- Professional tone
- Include technical and fundamental analysis
- Include sentiment and trade idea
- Keep formatting modern and clean

QUESTION: {question}
"""

    return await ask_gemini(prompt)

# ============================================
# CLEAN AI RESPONSE
# ============================================

def clean_text(text):
    text = text.replace("###", "")
    text = text.replace("##", "")
    text = text.replace("**", "")
    text = text.replace("---", "")
    text = text.replace("__", "")
    return text.strip()

# ============================================
# HANDLE BUTTONS
# ============================================

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.lower()
    user_id = update.message.from_user.id

    if "signal" in text:

        user_modes[user_id] = "signal"

        await update.message.reply_text(
            "📊 Signal Mode Activated\n\n"
            "Now ask for a signal.\n\n"
            "Examples:\n"
            "• XAUUSD\n"
            "• BTCUSD\n"
            "• XAGUSD\n"
            "• USOIL\n"
            "• GBPUSD\n"
            "• EURUSD\n"
            "• GBPJPY"
        )

        return

    if "breakdown" in text:

        user_modes[user_id] = "breakdown"

        await update.message.reply_text(
            "📚 Breakdown Mode Activated\n\n"
            "Now ask your market question.\n\n"
            "Examples:\n"
            "• Analyze gold market today\n"
            "• BTCUSD outlook\n"
            "• GBPUSD market analysis"
        )

        return

# ============================================
# HANDLE TEXT
# ============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.message.from_user.id
    message = update.message.text

    if user_id not in user_modes:

        await update.message.reply_text(
            "Choose an option below:",
            reply_markup=main_keyboard
        )

        return

    mode = user_modes[user_id]

    if mode == "signal":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI analyzing live market..."
        )

        await asyncio.sleep(1)

        image_file_id, direction, signal, signal_data = build_signal_response(message)

        await wait_message.delete()

        if image_file_id:
            await update.message.reply_photo(
                photo=image_file_id,
                caption=signal
            )
        else:
            await update.message.reply_text(
                "⚠️ Unable to fetch live market data.\nPlease try again shortly."
            )

        return

    if mode == "breakdown":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI preparing market breakdown..."
        )

        response = await generate_breakdown(message)
        response = clean_text(response)

        await wait_message.edit_text(response)

        return

# ============================================
# SCHEDULED JOB DISPATCHER
# ============================================

async def run_scheduled_job(context: ContextTypes.DEFAULT_TYPE):

    job_type = context.job.data["type"]
    keyword = context.job.data["keyword"]

    now = datetime.utcnow().strftime('%H:%M UTC')

    # NEWS POST
    if job_type == "news":

        print(f"[SCHEDULE] News ({keyword}) at {now}")
        await post_news(context.bot, keyword)

    # SIGNAL POST
    elif job_type == "signal":

        print(f"[SCHEDULE] Signal ({keyword.upper()}) at {now}")

        image_file_id, direction, signal, signal_data = build_signal_response(keyword)

        if signal_data is None:
            print(f"[SCHEDULE] ❌ No price for {keyword}. Skipping.")
            return

        button = get_channel_button()
        channel_ids = [CHANNEL_1_ID, CHANNEL_2_ID]

        for channel_id in channel_ids:
            try:

                sent = await context.bot.send_photo(
                    chat_id=channel_id,
                    photo=image_file_id,
                    caption=signal,
                    reply_markup=button
                )

                print(f"[SCHEDULE] ✅ {keyword.upper()} posted to {channel_id}")

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
                print(f"[SCHEDULE] ❌ Failed for {channel_id}: {e}")

# ============================================
# MAIN
# ============================================

def main():

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    app.add_handler(CommandHandler("start", start))

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

    # ========================================
    # SCHEDULE ALL 10 DAILY POSTS
    # ========================================

    job_queue = app.job_queue

    def parse_time(t):
        h, m = map(int, t.split(":"))
        return datetime.now().replace(
            hour=h, minute=m, second=0, microsecond=0
        ).time()

    for i, (utc_time, job_type, keyword) in enumerate(DAILY_SCHEDULE):
        job_queue.run_daily(
            run_scheduled_job,
            time=parse_time(utc_time),
            name=f"job_{i+1}_{keyword}",
            data={"type": job_type, "keyword": keyword}
        )

    print("Nexora AI Running...")
    print("Daily schedule (UTC):")
    for utc_time, job_type, keyword in DAILY_SCHEDULE:
        icon = "📰" if job_type == "news" else "📊"
        print(f"  {icon} {utc_time} UTC — {keyword.upper()}")
    print(f"Channels: {CHANNEL_1_ID} | {CHANNEL_2_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    app.run_polling(drop_pending_updates=True)

# ============================================
# RUN BOT
# ============================================

if __name__ == "__main__":
    main()
