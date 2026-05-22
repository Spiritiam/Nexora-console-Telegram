import logging
import os
import time
import asyncio
import httpx

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================
# LOGGING
# =========================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# =========================================
# ENV VARIABLES
# =========================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# =========================================
# GEMINI SETTINGS
# =========================================

GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN environment variable")

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY environment variable")

# =========================================
# MEMORY + COOLDOWN
# =========================================

user_memory = {}
user_last_request = {}

COOLDOWN_SECONDS = 10

# =========================================
# SYSTEM PROMPT
# =========================================

SYSTEM_PROMPT = """
You are Nexora AI.

You are a smart and professional AI assistant.

You specialize in:
- Forex trading
- Gold (XAUUSD)
- BTCUSD
- Trading psychology
- Supply and demand
- Risk management
- Financial education

Always respond clearly and professionally.
"""

# =========================================
# START COMMAND
# =========================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🚀 Welcome to Nexora AI\n\n"
        "Ask me anything about forex, crypto, trading, business, or general questions."
    )

# =========================================
# GEMINI REQUEST
# =========================================

async def ask_gemini_text(prompt: str):

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

    async with httpx.AsyncClient(timeout=60) as client:

        for attempt in range(3):

            try:

                response = await client.post(
                    GEMINI_URL,
                    json=payload,
                )

                # =====================================
                # RATE LIMIT HANDLING
                # =====================================

                if response.status_code == 429:

                    logger.warning("429 RATE LIMIT HIT")

                    if attempt < 2:
                        await asyncio.sleep(5)
                        continue

                    return (
                        "⚠️ AI server is busy right now.\n"
                        "Please wait a few seconds and try again."
                    )

                # =====================================
                # OTHER ERRORS
                # =====================================

                if response.status_code != 200:

                    logger.error(
                        f"Gemini Error: {response.status_code}"
                    )

                    return (
                        f"❌ Gemini Error: "
                        f"{response.status_code}"
                    )

                data = response.json()

                return data["candidates"][0]["content"]["parts"][0]["text"]

            except Exception as e:

                logger.error(f"GEMINI EXCEPTION: {e}")

                return (
                    "⚠️ AI temporarily unavailable.\n"
                    "Please try again later."
                )

# =========================================
# TEXT HANDLER
# =========================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    chat_id = str(update.effective_chat.id)

    current_time = time.time()

    # =====================================
    # COOLDOWN SYSTEM
    # =====================================

    if user_id in user_last_request:

        last_time = user_last_request[user_id]

        if current_time - last_time < COOLDOWN_SECONDS:

            await update.message.reply_text(
                "⏳ Please wait a few seconds before sending another request."
            )

            return

    user_last_request[user_id] = current_time

    user_message = update.message.text

    await update.message.reply_text(
        "🧠 Analyzing... please wait ⏳"
    )

    # =====================================
    # MEMORY SYSTEM
    # =====================================

    if chat_id not in user_memory:
        user_memory[chat_id] = []

    user_memory[chat_id].append({
        "role": "user",
        "text": user_message
    })

    # KEEP LAST 10 MESSAGES
    history = user_memory[chat_id][-10:]

    conversation_text = SYSTEM_PROMPT + "\n\n"

    for msg in history:

        conversation_text += (
            f"{msg['role']}: {msg['text']}\n"
        )

    # =====================================
    # ASK AI
    # =====================================

    ai_reply = await ask_gemini_text(conversation_text)

    # SAVE MEMORY
    user_memory[chat_id].append({
        "role": "assistant",
        "text": ai_reply
    })

    # SEND REPLY
    await update.message.reply_text(ai_reply)

# =========================================
# MAIN
# =========================================

def main():

    app = ApplicationBuilder().token(
        TELEGRAM_TOKEN
    ).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    logger.info("Nexora AI Bot Started")

    app.run_polling()

# =========================================
# RUN BOT
# =========================================

if __name__ == "__main__":
    main()
