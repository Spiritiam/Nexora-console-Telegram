
#!/usr/bin/env python3
"""
Nexora AI Trading Bot
Fixed & Improved Version
"""

import logging
import base64
import httpx
import os

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ==============================
# ENV VARIABLES
# ==============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN environment variable")

if not GEMINI_API_KEY:
    raise ValueError("Missing GEMINI_API_KEY environment variable")


# ==============================
# SYSTEM PROMPT
# ==============================

SYSTEM_PROMPT = """
You are Nexora AI, a professional trading analyst.

You analyze:
- Forex
- Gold (XAUUSD)
- Crypto

Always give:
- BUY or SELL bias
- Entry
- Stop Loss
- Take Profit
- Short explanation
- Risk warning

Be professional, clear, and concise.
"""


# ==============================
# GEMINI FUNCTIONS
# ==============================

async def ask_gemini_text(user_message: str) -> str:

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"{SYSTEM_PROMPT}\n\nUser Question: {user_message}"
                    }
                ]
            }
        ]
    }

    async with httpx.AsyncClient(timeout=60) as client:

        response = await client.post(
            GEMINI_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        response.raise_for_status()

        data = response.json()

        if "candidates" not in data:
            return f"Gemini Error: {data}"

        return data["candidates"][0]["content"]["parts"][0]["text"]


async def ask_gemini_image(image_bytes: bytes, mime_type: str, caption: str = "") -> str:

    image_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": f"{SYSTEM_PROMPT}\n\nAnalyze this chart. {caption}"
                    },
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_b64,
                        }
                    }
                ]
            }
        ]
    }

    async with httpx.AsyncClient(timeout=90) as client:

        response = await client.post(
            GEMINI_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        response.raise_for_status()

        data = response.json()

        if "candidates" not in data:
            return f"Gemini Error: {data}"

        return data["candidates"][0]["content"]["parts"][0]["text"]


# ==============================
# TELEGRAM COMMANDS
# ==============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = """
👋 Welcome to Nexora AI

I can:
• Analyze Gold, Forex & Crypto
• Give BUY/SELL signals
• Analyze chart screenshots
• Answer trading questions

Send a message or chart screenshot to begin.
"""

    await update.message.reply_text(text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "Send any trading question or chart screenshot."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_message = update.message.text

    await update.message.reply_text("🧠 Analyzing... please wait ⏳")

    try:

        result = await ask_gemini_text(user_message)

        if len(result) > 4000:
            for i in range(0, len(result), 4000):
                await update.message.reply_text(result[i:i+4000])
        else:
            await update.message.reply_text(result)

    except Exception as e:

        logging.exception("TEXT ERROR")

        await update.message.reply_text(
            f"❌ Error:\n{str(e)}"
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "📸 Chart received. Analyzing..."
    )

    try:

        photo = update.message.photo[-1]

        file = await context.bot.get_file(photo.file_id)

        image_bytes = await file.download_as_bytearray()

        caption = update.message.caption or ""

        result = await ask_gemini_image(
            bytes(image_bytes),
            "image/jpeg",
            caption
        )

        if len(result) > 4000:
            for i in range(0, len(result), 4000):
                await update.message.reply_text(result[i:i+4000])
        else:
            await update.message.reply_text(result)

    except Exception as e:

        logging.exception("IMAGE ERROR")

        await update.message.reply_text(
            f"❌ Image Error:\n{str(e)}"
        )


# ==============================
# MAIN
# ==============================

def main():

    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))

    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )

    app.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )

    print("✅ Nexora AI Bot is LIVE")

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
