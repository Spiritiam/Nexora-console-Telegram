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

Provide:
- Market Direction
- Entry Price
- Stop Loss
- Take Profit
- Confidence Level

Keep response:
- SHORT
- CLEAN
- PREMIUM
- EASY TO READ

Use emojis professionally.
"""

BREAKDOWN_PROMPT = """
You are Nexora AI.

You are a professional trading analyst.

Provide:
- Trend Direction
- Market Structure
- Support & Resistance
- Bullish/Bearish Bias
- Risk Warning

Keep response:
- CLEAN
- BEAUTIFUL
- EASY TO READ

Avoid overly long explanations.
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

        # TOO MANY REQUESTS
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
🚀 *Welcome to Nexora AI*

Your intelligent trading assistant.

You can ask things like:

• Should I buy gold now?
• Analyze BTCUSD
• Give me a scalp signal
• Market breakdown for EURUSD

Nexora AI will help you with:
📊 Signals
📚 Analysis
📈 Market direction
⚠️ Risk awareness
"""

    await update.message.reply_text(
        welcome_text,
        parse_mode="Markdown"
    )

# =====================================
# USER QUESTION HANDLER
# =====================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_message = update.message.text

    # SAVE QUESTION
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

    # GET LAST QUESTION
    question = context.user_data.get("last_question", "")

    # =========================
    # SIGNAL MODE
    # =========================

    if "Signal" in text:

        await update.message.reply_text(
            "📡 Generating high probability signal..."
        )

        await asyncio.sleep(2)

        response = await ask_ai(
            question,
            mode="signal"
        )

        formatted = f"""
📊 *NEXORA AI SIGNAL*

{response}

⚠️ Trade responsibly.
"""

        await update.message.reply_text(
            formatted,
            parse_mode="Markdown"
        )

    # =========================
    # BREAKDOWN MODE
    # =========================

    elif "Breakdown" in text:

        await update.message.reply_text(
            "📈 Preparing market breakdown..."
        )

        await asyncio.sleep(2)

        response = await ask_ai(
            question,
            mode="breakdown"
        )

        formatted = f"""
📚 *MARKET BREAKDOWN*

{response}
"""

        await update.message.reply_text(
            formatted,
            parse_mode="Markdown"
        )

# =====================================
# MAIN
# =====================================

def main():

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    # COMMANDS
    app.add_handler(
        CommandHandler("start", start)
    )

    # BUTTON HANDLER
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_buttons
        )
    )

    # TEXT HANDLER
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print("Nexora AI Bot Running...")

    app.run_polling()

# =====================================
# RUN BOT
# =====================================

if __name__ == "__main__":
    main()
