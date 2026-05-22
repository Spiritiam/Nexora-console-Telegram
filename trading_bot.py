import os
import asyncio
import requests

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

# =====================================
# ENV VARIABLES
# =====================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# =====================================
# GEMINI SETTINGS
# =====================================

GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# =====================================
# OPENROUTER SETTINGS
# =====================================

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# =====================================
# AI PROMPTS
# =====================================

SIGNAL_PROMPT = """
You are Nexora AI.

You are a professional forex and crypto trading assistant.

When giving signals:
- Give direction
- Give entry
- Give stop loss
- Give take profit
- Give confidence level

Keep response:
- Clean
- Professional
- Short
- Easy to read

Use premium formatting.

Example format:

📊 XAUUSD BUY SIGNAL

Entry: 3345
SL: 3335
TP1: 3360
TP2: 3375

Confidence: High ✅
"""

BREAKDOWN_PROMPT = """
You are Nexora AI.

You are a professional market analyst.

Provide:
- Trend direction
- Momentum
- Bias
- Key support/resistance
- Risk warning

Keep response:
- Beautiful
- Premium
- Easy to read
- Short but useful

Use clean formatting and emojis professionally.
"""

# =====================================
# GEMINI REQUEST
# =====================================

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

        # RATE LIMIT
        if response.status_code == 429:
            return None

        # OTHER ERRORS
        if response.status_code != 200:
            return None

        result = response.json()

        return result["candidates"][0]["content"]["parts"][0]["text"]

    except:
        return None

# =====================================
# OPENROUTER REQUEST
# =====================================

async def ask_openrouter(prompt):

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
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
            return None

        result = response.json()

        return result["choices"][0]["message"]["content"]

    except:
        return None

# =====================================
# MAIN AI ENGINE
# =====================================

async def ask_ai(user_text, mode="signal"):

    if mode == "signal":
        final_prompt = SIGNAL_PROMPT + "\n\nUser Request:\n" + user_text
    else:
        final_prompt = BREAKDOWN_PROMPT + "\n\nUser Request:\n" + user_text

    # TRY GEMINI FIRST
    gemini_response = await ask_gemini(final_prompt)

    if gemini_response:
        return gemini_response

    # FALLBACK TO OPENROUTER
    openrouter_response = await ask_openrouter(final_prompt)

    if openrouter_response:
        return openrouter_response

    return (
        "⚠️ AI servers are busy right now.\n"
        "Please wait a few seconds and try again."
    )

# =====================================
# START COMMAND
# =====================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    welcome_text = """
🚀 Welcome to Nexora AI

Your intelligent trading assistant.

You can ask:

• Should I buy gold now?
• Analyze BTCUSD
• Give me a scalp signal
• Market breakdown for EURUSD

Nexora AI helps with:

📊 Trading Signals
📈 Market Analysis
📚 Trade Breakdowns
⚠️ Risk Awareness
"""

    await update.message.reply_text(welcome_text)

# =====================================
# NORMAL USER MESSAGE
# =====================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_message = update.message.text

    # IGNORE BUTTON MESSAGES
    if user_message in ["📊 Signal", "📚 Breakdown"]:
        return

    # SAVE USER QUESTION
    context.user_data["last_question"] = user_message

    await update.message.reply_text(
        "🧠 Nexora AI is analyzing the market..."
    )

    await asyncio.sleep(1)

    keyboard = [
        ["📊 Signal", "📚 Breakdown"]
    ]

    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True
    )

    await update.message.reply_text(
        "Choose analysis mode:",
        reply_markup=reply_markup
    )

# =====================================
# BUTTON HANDLER
# =====================================

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text

    question = context.user_data.get("last_question", "")

    # =========================
    # SIGNAL
    # =========================

    if text == "📊 Signal":

        await update.message.reply_text(
            "📡 Generating high probability signal..."
        )

        await asyncio.sleep(2)

        response = await ask_ai(
            question,
            mode="signal"
        )

        formatted = f"""
📊 NEXORA AI SIGNAL

{response}

⚠️ Trade responsibly.
"""

        await update.message.reply_text(formatted)

    # =========================
    # BREAKDOWN
    # =========================

    elif text == "📚 Breakdown":

        await update.message.reply_text(
            "📈 Preparing market breakdown..."
        )

        await asyncio.sleep(2)

        response = await ask_ai(
            question,
            mode="breakdown"
        )

        formatted = f"""
📚 MARKET BREAKDOWN

{response}
"""

        await update.message.reply_text(formatted)

# =====================================
# MAIN
# =====================================

def main():

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    # START COMMAND
    app.add_handler(
        CommandHandler("start", start)
    )

    # BUTTON HANDLER
    app.add_handler(
        MessageHandler(
            filters.Regex("📊 Signal|📚 Breakdown"),
            handle_buttons
        )
    )

    # NORMAL TEXT HANDLER
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

# =====================================
# RUN BOT
# =====================================

if __name__ == "__main__":
    main()
