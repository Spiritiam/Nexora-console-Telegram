import os
import asyncio
import requests

from telegram import ReplyKeyboardMarkup, Update
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

# GEMINI
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.0-flash"

# OPENROUTER
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# ============================================
# CHECK VARIABLES
# ============================================

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")

# ============================================
# BUTTONS
# ============================================

reply_keyboard = [
    ["📊 Signal", "📚 Breakdown"]
]

markup = ReplyKeyboardMarkup(
    reply_keyboard,
    resize_keyboard=True
)

# ============================================
# USER MEMORY
# ============================================

user_mode = {}

# ============================================
# START
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    welcome_text = """
👋 Welcome to Nexora AI

I can help you with:

📊 Trading Signals
📚 Market Breakdowns
📈 Technical Analysis
🧠 AI Trading Assistance

Choose an option below.
"""

    await update.message.reply_text(
        welcome_text,
        reply_markup=markup
    )

# ============================================
# GEMINI REQUEST
# ============================================

async def ask_gemini(prompt):

    if not GEMINI_API_KEY:
        return None

    try:

        gemini_url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        )

        payload = {
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

        response = requests.post(
            gemini_url,
            json=payload,
            timeout=60
        )

        # ====================================
        # RATE LIMIT HANDLING
        # ====================================

        if response.status_code == 429:
            print("Gemini Rate Limited")
            return None

        # ====================================
        # OTHER ERRORS
        # ====================================

        if response.status_code != 200:
            print(f"Gemini Error: {response.status_code}")
            return None

        data = response.json()

        text = (
            data["candidates"][0]
            ["content"]["parts"][0]["text"]
        )

        return text

    except Exception as e:
        print("Gemini Exception:", e)
        return None

# ============================================
# OPENROUTER FALLBACK
# ============================================

async def ask_openrouter(prompt):

    if not OPENROUTER_API_KEY:
        return """
⚠️ AI server busy right now.

Please try again later.
"""

    try:

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek/deepseek-chat",
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            },
            timeout=60
        )

        if response.status_code != 200:
            print("OpenRouter Error:", response.text)

            return """
⚠️ AI server busy right now.

Please wait a few seconds and try again.
"""

        data = response.json()

        text = (
            data["choices"][0]
            ["message"]["content"]
        )

        return text

    except Exception as e:

        print("OpenRouter Exception:", e)

        return """
⚠️ AI server busy right now.

Please try again later.
"""

# ============================================
# MAIN AI LOGIC
# ============================================

async def generate_ai_response(user_text, mode):

    # ====================================
    # SIGNAL MODE
    # ====================================

    if mode == "signal":

        ai_prompt = f"""
You are Nexora AI, a professional forex signal provider.

User asked:
{user_text}

Give:
- Bias (BUY or SELL)
- Entry Zone
- Stop Loss
- Take Profit
- Confidence Level
- Short reason

Make response VERY clean.
Use emojis.
Keep it short and premium.
"""

    # ====================================
    # BREAKDOWN MODE
    # ====================================

    else:

        ai_prompt = f"""
You are Nexora AI.

User asked:
{user_text}

Give:
- professional breakdown
- technical analysis
- fundamental explanation
- trading insight

Make it clean and easy to read.
Use spacing properly.
Avoid huge paragraphs.
"""

    # ====================================
    # TRY GEMINI FIRST
    # ====================================

    response = await ask_gemini(ai_prompt)

    if response:
        return response

    # ====================================
    # FALLBACK TO OPENROUTER
    # ====================================

    print("Switching to OpenRouter...")

    response = await ask_openrouter(ai_prompt)

    return response

# ============================================
# BUTTON HANDLER
# ============================================

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = update.message.text
    user_id = update.effective_user.id

    # ====================================
    # SIGNAL MODE
    # ====================================

    if text == "📊 Signal":

        user_mode[user_id] = "signal"

        await update.message.reply_text(
            """
📊 Signal Mode Activated

Now send:
- pair
- market
- chart question

Example:
Should I buy gold now?
"""
        )

    # ====================================
    # BREAKDOWN MODE
    # ====================================

    elif text == "📚 Breakdown":

        user_mode[user_id] = "breakdown"

        await update.message.reply_text(
            """
📚 Breakdown Mode Activated

Send your market question.

Example:
Why is gold bullish today?
"""
        )

# ============================================
# TEXT HANDLER
# ============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    user_text = update.message.text

    # ====================================
    # DEFAULT MODE
    # ====================================

    mode = user_mode.get(user_id)

    if not mode:

        await update.message.reply_text(
            """
Choose a mode first:

📊 Signal
or
📚 Breakdown
""",
            reply_markup=markup
        )

        return

    # ====================================
    # TYPING EFFECT
    # ====================================

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    thinking_message = await update.message.reply_text(
        "🧠 Nexora AI is thinking..."
    )

    # ====================================
    # GENERATE RESPONSE
    # ====================================

    response = await generate_ai_response(
        user_text,
        mode
    )

    # ====================================
    # DELETE THINKING MESSAGE
    # ====================================

    try:
        await thinking_message.delete()
    except:
        pass

    # ====================================
    # SEND RESPONSE
    # ====================================

    await update.message.reply_text(response)

# ============================================
# MAIN
# ============================================

def main():

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # ====================================
    # START COMMAND
    # ====================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # ====================================
    # BUTTON HANDLER
    # ====================================

    app.add_handler(
        MessageHandler(
            filters.Regex("📊 Signal|📚 Breakdown"),
            handle_buttons
        )
    )

    # ====================================
    # TEXT HANDLER
    # ====================================

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    print("Nexora AI Bot Running...")

    # ====================================
    # RUN BOT
    # ====================================

    app.run_polling(
        drop_pending_updates=True,
        close_loop=False,
        allowed_updates=Update.ALL_TYPES
    )

# ============================================
# START BOT
# ============================================

if __name__ == "__main__":
    main()
