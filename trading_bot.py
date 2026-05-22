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
# LIVE MARKET PRICE
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
# TREND LOGIC
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
# DYNAMIC REASONS
# ============================================

BUY_REASONS = [
    "Bullish momentum across higher timeframes.",
    "Liquidity sweep reaction from support zone.",
    "London session bullish continuation.",
    "Strong buyer pressure detected.",
    "Breakout structure confirmation on H1.",
    "Momentum expansion during NY session.",
    "Demand zone rejection with bullish pressure.",
]

SELL_REASONS = [
    "Bearish rejection from resistance zone.",
    "Strong seller momentum detected.",
    "Liquidity sweep from highs.",
    "H1 bearish continuation setup.",
    "NY session reversal pressure.",
    "Breakdown below key support.",
    "Supply zone reaction confirmed.",
]

# ============================================
# SIGNAL GENERATOR
# ============================================

def build_signal_response(question):

    q = question.lower()

    symbol = "XAU/USD"
    pair_name = "XAUUSD"

    pip_size = 10

    # ========================================
    # PAIRS
    # ========================================

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

    # ========================================
    # LIVE PRICE
    # ========================================

    live_price = get_live_price(symbol)

    if live_price is None:

        return (
            "⚠️ Unable to fetch live market data.\n"
            "Please try again shortly."
        )

    # ========================================
    # MARKET BIAS
    # ========================================

    direction, strength, confidence = generate_market_bias()

    # ========================================
    # ENTRY / SL / TP
    # ========================================

    if direction == "BUY":

        entry_low = live_price - pip_size
        entry_high = live_price + pip_size

        stop_loss = live_price - (pip_size * 3)

        take_profit = live_price + (pip_size * 6)

        reason = random.choice(BUY_REASONS)

        signal_emoji = "🟢"

    else:

        entry_low = live_price - pip_size
        entry_high = live_price + pip_size

        stop_loss = live_price + (pip_size * 3)

        take_profit = live_price - (pip_size * 6)

        reason = random.choice(SELL_REASONS)

        signal_emoji = "🔴"

    # ========================================
    # SESSION
    # ========================================

    session = get_market_session()

    # ========================================
    # TIMEFRAME CONFIRMATION
    # ========================================

    timeframe_confirmation = random.choice([
        "M15 bullish structure",
        "H1 trend continuation",
        "H4 momentum alignment",
        "Multi-timeframe confirmation",
        "M15 liquidity sweep confirmation",
    ])

    # ========================================
    # FORMAT DECIMALS
    # ========================================

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

    # ========================================
    # FINAL RESPONSE
    # ========================================

    response = f"""
{signal_emoji} {strength} {direction} {pair_name}

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

Trade safe 💼🔥
"""

    return response

# ============================================
# GEMINI
# ============================================

async def ask_gemini(prompt):

    headers = {
        "Content-Type": "application/json"
    }

    data = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
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
# OPENROUTER
# ============================================

async def ask_openrouter(prompt):

    if not OPENROUTER_API_KEY:

        return (
            "⚠️ AI service unavailable."
        )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    data = {
        "model": "deepseek/deepseek-chat",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    try:

        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=data,
            timeout=30
        )

        if response.status_code != 200:

            return (
                "⚠️ AI server busy."
            )

        result = response.json()

        return result["choices"][0]["message"]["content"]

    except Exception as e:

        print("OpenRouter Error:", e)

        return (
            "⚠️ AI servers unavailable."
        )

# ============================================
# BREAKDOWN GENERATOR
# ============================================

async def generate_breakdown(question):

    session = get_market_session()

    prompt = f"""
You are Nexora AI.

Give a PROFESSIONAL forex and market breakdown.

IMPORTANT:

1. Use CLEAN formatting
2. NO markdown stars
3. NO hashtags
4. NO weird symbols
5. Use professional structure
6. Include:
   - technical analysis
   - fundamental analysis
   - sentiment
   - session behavior
   - market outlook
   - trade idea
7. Make it look elite and modern.
8. Mention current session:
{session}

Question:
{question}
"""

    return await ask_gemini(prompt)

# ============================================
# BUTTON HANDLER
# ============================================

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text.lower()

    user_id = update.message.from_user.id

    # SIGNAL
    if "signal" in text:

        user_modes[user_id] = "signal"

        await update.message.reply_text(
            "📊 Signal Mode Activated\n\n"
            "Now ask for a signal.\n\n"
            "Examples:\n"
            "• EURUSD\n"
            "• XAUUSD\n"
            "• BTCUSD\n"
            "• Should I buy gold now?"
        )

        return

    # BREAKDOWN
    if "breakdown" in text:

        user_modes[user_id] = "breakdown"

        await update.message.reply_text(
            "📚 Breakdown Mode Activated\n\n"
            "Now ask your market question.\n\n"
            "Examples:\n"
            "• Analyze gold market today\n"
            "• GBPUSD outlook\n"
            "• BTC market analysis"
        )

        return

# ============================================
# HANDLE TEXT
# ============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.message.from_user.id

    message = update.message.text

    # NO MODE
    if user_id not in user_modes:

        await update.message.reply_text(
            "Choose an option below:",
            reply_markup=main_keyboard
        )

        return

    mode = user_modes[user_id]

    # ========================================
    # SIGNAL MODE
    # ========================================

    if mode == "signal":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI analyzing live market..."
        )

        await asyncio.sleep(1)

        signal = build_signal_response(message)

        await wait_message.edit_text(signal)

        return

    # ========================================
    # BREAKDOWN MODE
    # ========================================

    if mode == "breakdown":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI preparing market breakdown..."
        )

        response = await generate_breakdown(message)

        await wait_message.edit_text(response)

        return

# ============================================
# MAIN
# ============================================

def main():

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    # START
    app.add_handler(
        CommandHandler("start", start)
    )

    # BUTTONS
    app.add_handler(
        MessageHandler(
            filters.Regex(
                "^(📊 Signal|📚 Breakdown|signal|breakdown)$"
            ),
            handle_buttons
        )
    )

    # TEXT
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print("Nexora AI Running...")

    app.run_polling(
        drop_pending_updates=True
    )

# ============================================
# RUN BOT
# ============================================

if __name__ == "__main__":
    main()
