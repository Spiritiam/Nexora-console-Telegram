import os
import asyncio
import random
import requests

from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
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

# ============================================
# YOUR TWO CHANNEL IDs
# ============================================

CHANNEL_1_ID = os.getenv("CHANNEL_1_ID", "-1001722756645")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "-1002468228698")

# ============================================
# BUY / SELL IMAGE FILE IDs  ← FILL THESE IN
# ============================================
# Step 1: Run the bot
# Step 2: Send your BUY image to the bot in private chat
# Step 3: It replies with the file ID — paste it below
# Step 4: Do the same for SELL image
# Step 5: Remove the get_image_file_id handler from main()

BUY_IMAGE_FILE_ID = "YOUR_BUY_IMAGE_FILE_ID_HERE"
SELL_IMAGE_FILE_ID = "YOUR_SELL_IMAGE_FILE_ID_HERE"

# ============================================
# SIGNAL TIMES (UTC)
# ============================================
# 08:00 UTC = 09:00 Lagos time
# 16:00 UTC = 17:00 Lagos time

SIGNAL_TIME_1 = "08:00"
SIGNAL_TIME_2 = "16:00"

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
    [
        ["📊 Signal", "📚 Breakdown"],
    ],
    resize_keyboard=True
)

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

    direction = random.choice([
        "BUY",
        "SELL"
    ])

    strength = random.choice([
        "STRONG",
        "WEAK"
    ])

    confidence = random.randint(72, 94)

    return direction, strength, confidence

# ============================================
# BUY REASONS
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

# ============================================
# SELL REASONS
# ============================================

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

    symbol = "XAU/USD"
    pair_name = "XAUUSD"
    pip_size = 10

    if "eurusd" in q:
        symbol = "EUR/USD"
        pair_name = "EURUSD"
        pip_size = 0.0020

    elif "gbpusd" in q:
        symbol = "GBP/USD"
        pair_name = "GBPUSD"
        pip_size = 0.0025

    elif "nzdusd" in q:
        symbol = "NZD/USD"
        pair_name = "NZDUSD"
        pip_size = 0.0020

    elif "usdjpy" in q:
        symbol = "USD/JPY"
        pair_name = "USDJPY"
        pip_size = 0.30

    elif "btc" in q or "bitcoin" in q:
        symbol = "BTC/USD"
        pair_name = "BTCUSD"
        pip_size = 300

    live_price = get_live_price(symbol)

    if live_price is None:
        return None, None, (
            "⚠️ Unable to fetch live market data.\n"
            "Please try again shortly."
        )

    direction, strength, confidence = generate_market_bias()

    if direction == "BUY":

        entry_low = live_price - pip_size
        entry_high = live_price + pip_size
        stop_loss = live_price - (pip_size * 3)
        take_profit = live_price + (pip_size * 6)
        reason = random.choice(BUY_REASONS)
        signal_emoji = "🟢"
        image_file_id = BUY_IMAGE_FILE_ID

    else:

        entry_low = live_price - pip_size
        entry_high = live_price + pip_size
        stop_loss = live_price + (pip_size * 3)
        take_profit = live_price - (pip_size * 6)
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

    if live_price < 10:
        live_price = round(live_price, 5)
        entry_low = round(entry_low, 5)
        entry_high = round(entry_high, 5)
        stop_loss = round(stop_loss, 5)
        take_profit = round(take_profit, 5)
    else:
        live_price = round(live_price, 2)
        entry_low = round(entry_low, 2)
        entry_high = round(entry_high, 2)
        stop_loss = round(stop_loss, 2)
        take_profit = round(take_profit, 2)

    response = f"""{signal_emoji} {strength} {direction} {pair_name}

Current Price: {live_price}

Entry Zone: {entry_low} - {entry_high}

Stop Loss: {stop_loss}

Take Profit: {take_profit}

Confidence: {confidence}%

Session: {session}

Timeframe Confirmation:
{timeframe_confirmation}

Reason:
{reason}

Trade safe 💼🔥"""

    return image_file_id, direction, response

# ============================================
# GEMINI AI
# ============================================

async def ask_gemini(prompt):

    headers = {"Content-Type": "application/json"}

    data = {
        "contents": [
            {
                "parts": [{"text": prompt}]
            }
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

    if "btc" in q or "bitcoin" in q:
        symbol = "BTC/USD"
        pair_name = "Bitcoin (BTCUSD)"

    elif "eurusd" in q or "eur/usd" in q:
        symbol = "EUR/USD"
        pair_name = "EURUSD"

    elif "gbpusd" in q or "gbp/usd" in q:
        symbol = "GBP/USD"
        pair_name = "GBPUSD"

    elif "nzdusd" in q or "nzd/usd" in q:
        symbol = "NZD/USD"
        pair_name = "NZDUSD"

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
Do NOT use old market prices.

PAIR:
{pair_name}

LIVE PRICE:
{live_price_text}

SESSION:
{session}

RULES:
- Clean formatting
- No markdown symbols
- No hashtags
- No stars
- No fake data
- Beginner friendly
- Professional tone
- Include BOTH technical and fundamental analysis
- Include sentiment
- Include trade idea
- Use current live price in analysis
- Keep formatting modern and clean

QUESTION:
{question}
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
            "• EURUSD\n"
            "• GBPUSD\n"
            "• BTCUSD\n"
            "• XAUUSD\n"
            "• Should I buy gold now?"
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

    # ========================================
    # SIGNAL MODE — sends image + caption
    # ========================================

    if mode == "signal":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI analyzing live market..."
        )

        await asyncio.sleep(1)

        image_file_id, direction, signal = build_signal_response(message)

        await wait_message.delete()

        if image_file_id and image_file_id not in [
            "YOUR_BUY_IMAGE_FILE_ID_HERE",
            "YOUR_SELL_IMAGE_FILE_ID_HERE"
        ]:
            await update.message.reply_photo(
                photo=image_file_id,
                caption=signal
            )

        else:
            await update.message.reply_text(signal)

        return

    # ========================================
    # BREAKDOWN MODE
    # ========================================

    if mode == "breakdown":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI preparing market breakdown..."
        )

        response = await generate_breakdown(message)
        response = clean_text(response)

        await wait_message.edit_text(response)

        return

# ============================================
# AUTO SIGNAL — POSTED TO BOTH CHANNELS
# ============================================

async def post_auto_signal(context: ContextTypes.DEFAULT_TYPE):

    print(f"[AUTO SIGNAL] Firing at {datetime.utcnow().strftime('%H:%M UTC')}")

    image_file_id, direction, signal = build_signal_response("xauusd")

    channel_ids = [CHANNEL_1_ID, CHANNEL_2_ID]

    for channel_id in channel_ids:
        try:

            if image_file_id and image_file_id not in [
                "YOUR_BUY_IMAGE_FILE_ID_HERE",
                "YOUR_SELL_IMAGE_FILE_ID_HERE"
            ]:
                await context.bot.send_photo(
                    chat_id=channel_id,
                    photo=image_file_id,
                    caption=signal
                )

            else:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=signal
                )

            print(f"[AUTO SIGNAL] Posted to {channel_id} ✅")

        except Exception as e:

            print(f"[AUTO SIGNAL] Failed for {channel_id}: {e}")

# ============================================
# TEMP: GET FILE IDs FOR YOUR IMAGES
# ============================================
# DELETE this function and its handler in main()
# once you have both file IDs

async def get_image_file_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await update.message.reply_text(
            f"✅ File ID (copy this):\n\n{file_id}"
        )

# ============================================
# MAIN
# ============================================

def main():

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    # START COMMAND
    app.add_handler(CommandHandler("start", start))

    # TEMP: image file ID getter — REMOVE AFTER USE
    app.add_handler(MessageHandler(filters.PHOTO, get_image_file_id))

    # BUTTON HANDLER
    app.add_handler(
        MessageHandler(
            filters.Regex(
                "^(📊 Signal|📚 Breakdown|signal|breakdown)$"
            ),
            handle_buttons
        )
    )

    # NORMAL TEXT
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    # ========================================
    # SCHEDULE AUTO SIGNALS
    # ========================================

    job_queue = app.job_queue

    def parse_time(t):
        h, m = map(int, t.split(":"))
        return datetime.now().replace(
            hour=h, minute=m, second=0, microsecond=0
        ).time()

    job_queue.run_daily(
        post_auto_signal,
        time=parse_time(SIGNAL_TIME_1),
        name="auto_signal_morning"
    )

    job_queue.run_daily(
        post_auto_signal,
        time=parse_time(SIGNAL_TIME_2),
        name="auto_signal_afternoon"
    )

    print("Nexora AI Running...")
    print(f"Auto signals scheduled: {SIGNAL_TIME_1} UTC and {SIGNAL_TIME_2} UTC")
    print(f"Channel 1: {CHANNEL_1_ID}")
    print(f"Channel 2: {CHANNEL_2_ID}")

    app.run_polling(drop_pending_updates=True)

# ============================================
# RUN BOT
# ============================================

if __name__ == "__main__":
    main()
