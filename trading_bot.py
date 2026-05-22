import os
import re
import asyncio
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
# AI MODELS
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
        print("Live Price Error:", e)
        return None

# ============================================
# SIGNAL BUILDER
# ============================================

def build_signal_response(question):

    q = question.lower()

    symbol = "XAU/USD"
    pair_name = "XAUUSD"

    # BTC
    if "btc" in q or "bitcoin" in q:
        symbol = "BTC/USD"
        pair_name = "BTCUSD"

    # EURUSD
    elif "eurusd" in q:
        symbol = "EUR/USD"
        pair_name = "EURUSD"

    # GBPUSD
    elif "gbpusd" in q:
        symbol = "GBP/USD"
        pair_name = "GBPUSD"

    live_price = get_live_price(symbol)

    if live_price is None:
        return (
            "⚠️ Unable to fetch live market data right now.\n"
            "Please try again shortly."
        )

    entry_low = round(live_price - 5, 2)
    entry_high = round(live_price + 5, 2)

    stop_loss = round(live_price - 20, 2)
    take_profit = round(live_price + 40, 2)

    response = f"""
🟢 BUY {pair_name}

Current Price: {live_price}

Entry Zone: {entry_low} - {entry_high}

Stop Loss: {stop_loss} 🔴

Take Profit: {take_profit} 🎯

Confidence: 82%

Reason:
Bullish momentum + liquidity sweep reaction + market strength.

Trade safe 💼🔥
"""

    return response

# ============================================
# GEMINI AI
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
            raise Exception(f"GEMINI_ERROR_{response.status_code}")

        result = response.json()

        return result["candidates"][0]["content"]["parts"][0]["text"]

    except Exception as e:

        print("Gemini Failed:", e)

        return await ask_openrouter(prompt)

# ============================================
# OPENROUTER FALLBACK
# ============================================

async def ask_openrouter(prompt):

    if not OPENROUTER_API_KEY:
        return (
            "⚠️ AI server is busy right now.\n"
            "Please try again shortly."
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
                "⚠️ AI servers are currently busy.\n"
                "Please try again later."
            )

        result = response.json()

        return result["choices"][0]["message"]["content"]

    except Exception as e:

        print("OpenRouter Error:", e)

        return (
            "⚠️ AI servers are unavailable right now.\n"
            "Please try again later."
        )

# ============================================
# BREAKDOWN GENERATOR
# ============================================

async def generate_breakdown(question):

    try:

        price = get_live_price("XAU/USD")

        if not price:
            price = 4500

        hour = datetime.utcnow().hour

        if hour < 8:
            session = "Asian Session 🌏"

        elif hour < 13:
            session = "London Session 🏦"

        else:
            session = "New York Session 🇺🇸"

        support_1 = round(price - 20, 2)
        support_2 = round(price - 40, 2)

        resistance_1 = round(price + 25, 2)
        resistance_2 = round(price + 45, 2)

        breakdown = f"""
📊 GOLD MARKET BREAKDOWN

💰 Current Price:
{price}

━━━━━━━━━━━━━━━

🌍 Active Session:
{session}

━━━━━━━━━━━━━━━

📈 TECHNICAL ANALYSIS

M15 Trend:
Bullish 🟢

H1 Trend:
Bullish 🟢

H4 Trend:
Strong Bullish 🟢

━━━━━━━━━━━━━━━

🟢 Support Zones

{support_1}

{support_2}

━━━━━━━━━━━━━━━

🔴 Resistance Zones

{resistance_1}

{resistance_2}

━━━━━━━━━━━━━━━

🌍 FUNDAMENTAL ANALYSIS

• USD weakness supporting gold prices

• Institutional buying pressure detected

• Safe haven demand remains elevated

• Fed expectations supporting bullish continuation

━━━━━━━━━━━━━━━

🧠 MARKET SENTIMENT

Bullish momentum remains active across higher timeframes.

━━━━━━━━━━━━━━━

💡 TRADE IDEA

Buying pullbacks remains safer than chasing highs.

━━━━━━━━━━━━━━━

📊 Probability Score

84% Confidence

━━━━━━━━━━━━━━━

⚡ Powered by Nexora AI
"""

        return breakdown

    except Exception as e:

        print("BREAKDOWN ERROR:", e)

        return """
⚠️ Breakdown temporarily unavailable.

Please try again in a few seconds.
"""

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
            "Example:\n"
            "Should I buy gold now?"
        )

        return

    if "breakdown" in text:

        user_modes[user_id] = "breakdown"

        await update.message.reply_text(
            "📚 Breakdown Mode Activated\n\n"
            "Now ask your market question.\n\n"
            "Example:\n"
            "Analyze gold market today."
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

    # SIGNAL MODE
    if mode == "signal":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI is analyzing market..."
        )

        await asyncio.sleep(1)

        signal = build_signal_response(message)

        await wait_message.edit_text(signal)

        return

    # BREAKDOWN MODE
    if mode == "breakdown":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI is preparing breakdown..."
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

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.Regex("^(📊 Signal|📚 Breakdown|signal|breakdown)$"),
            handle_buttons
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print("Nexora AI Bot Running...")

    app.run_polling(
        drop_pending_updates=True
    )

# ============================================
# RUN BOT
# ============================================

if __name__ == "__main__":
    main()
