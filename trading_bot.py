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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# =========================================
# GEMINI SETTINGS
# =========================================

GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# =========================================
# CHECK VARIABLES
# =========================================

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN")

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY")

if not OPENROUTER_API_KEY:
    raise ValueError("Missing OPENROUTER_API_KEY")

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
        "Multi-AI system activated.\n\n"
        "You can ask about:\n"
        "- Forex\n"
        "- Crypto\n"
        "- Trading\n"
        "- Business\n"
        "- Finance\n"
        "- General AI questions"
    )

# =========================================
# GEMINI + FALLBACK SYSTEM
# =========================================

async def ask_ai(prompt: str):

    # =====================================
    # TRY GEMINI FIRST
    # =====================================

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

    try:

        async with httpx.AsyncClient(timeout=60) as client:

            response = await client.post(
                GEMINI_URL,
                json=payload,
            )

            # SUCCESS
            if response.status_code == 200:

                data = response.json()

                logger.info("Gemini Success")

                return data["candidates"][0]["content"]["parts"][0]["text"]

            logger.warning(
                f"Gemini Failed: {response.status_code}"
            )

    except Exception as e:

        logger.warning(f"Gemini Exception: {e}")

    # =====================================
    # OPENROUTER FALLBACK
    # =====================================

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    fallback_models = [

        # DeepSeek
        "deepseek/deepseek-chat",

        # Claude
        "anthropic/claude-3-haiku",

        # Llama
        "meta-llama/llama-3-8b-instruct",
    ]

    async with httpx.AsyncClient(timeout=60) as client:

        for model in fallback_models:

            try:

                logger.info(
                    f"Trying fallback model: {model}"
                )

                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ]
                }

                response = await client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers=headers,
                    json=payload,
                )

                if response.status_code == 200:

                    data = response.json()

                    logger.info(
                        f"Fallback Success: {model}"
                    )

                    return data["choices"][0]["message"]["content"]

                logger.warning(
                    f"Fallback Failed: {model}"
                )

            except Exception as model_error:

                logger.warning(
                    f"{model} Exception: {model_error}"
                )

                continue

    # =====================================
    # TOTAL FAILURE
    # =====================================

    return (
        "⚠️ All AI systems are currently busy.\n"
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
        "🧠 Nexora AI is thinking..."
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

    ai_reply = await ask_ai(conversation_text)

    # SAVE AI MEMORY
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

    logger.info("🚀 Nexora AI Bot Started")

    app.run_polling()

# =========================================
# RUN BOT
# =========================================

if __name__ == "__main__":
    main()
