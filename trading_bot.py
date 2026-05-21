#!/usr/bin/env python3
"""
AI Trading Signals Bot
Powered by Google Gemini Flash + Telegram
Covers: Gold, Forex, Crypto
"""

import logging
import base64
import httpx
import json
import re
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─────────────────────────────────────────────
#  CONFIG  (paste your keys here)
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = "8630120864:AAEh433QwQIdRnAxoBFJl1E5EJqMQhs_sIY"
GEMINI_API_KEY = "AIzaSyCICHbuEipB33WtjSxNXMXhSK3lbtKtFik"
GEMINI_MODEL   = "gemini-2.0-flash"
GEMINI_URL     = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

# ─────────────────────────────────────────────
#  SYSTEM PROMPT  – the AI's personality
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You are an elite professional trading analyst with 20+ years of experience in Forex, Gold (XAUUSD), and Cryptocurrency markets. You specialize in technical analysis, price action, and smart money concepts (SMC).

When a user asks a trading question OR shares a chart screenshot, you MUST respond with a complete structured signal in this EXACT format:

━━━━━━━━━━━━━━━━━━━━━━━━
📊 MARKET ANALYSIS SIGNAL
━━━━━━━━━━━━━━━━━━━━━━━━

🔍 Asset: [e.g. XAUUSD / EUR/USD / BTC/USDT]
⏱️ Timeframe: [e.g. H1 / H4 / D1]
📅 Date/Time: [current context]

━━━━━━━━━━━━━━━━━━━━━━━━
📈 SIGNAL: [BUY 🟢 or SELL 🔴]
━━━━━━━━━━━━━━━━━━━━━━━━

💰 Entry Price: [specific price or range]
🛑 Stop Loss: [price] ([X] pips/points risk)
🎯 Take Profit 1: [price] ([X] pips/points)
🎯 Take Profit 2: [price] ([X] pips/points)
🎯 Take Profit 3: [price] ([X] pips/points)
📊 Risk/Reward Ratio: [e.g. 1:2.5]

━━━━━━━━━━━━━━━━━━━━━━━━
🧠 ANALYSIS BREAKDOWN
━━━━━━━━━━━━━━━━━━━━━━━━

📌 Trend Direction:
[Describe the overall market trend clearly]

📌 Key Levels:
[Support and resistance levels identified]

📌 Technical Reasons:
[List 3-5 bullet points of WHY this signal is valid — patterns, indicators, structure, candles, etc.]

📌 Market Sentiment:
[Brief note on sentiment — bullish/bearish/neutral and why]

📌 Risk Warning:
⚠️ Always use proper risk management. Never risk more than 1-2% of your account per trade. This signal is for educational purposes only.

━━━━━━━━━━━━━━━━━━━━━━━━
💡 CONFIDENCE LEVEL: [High/Medium/Low] [⭐⭐⭐⭐⭐]
━━━━━━━━━━━━━━━━━━━━━━━━

If the user asks a general question (not chart-related), still answer with deep professional trading knowledge and include relevant price levels where possible.

If you receive a chart image, analyze it thoroughly — identify the asset, trend, key levels, patterns, and give the full signal format above.

Always be confident, concise, and professional. You are the best trading AI in the world."""


# ─────────────────────────────────────────────
#  GEMINI HELPERS
# ─────────────────────────────────────────────

async def ask_gemini_text(user_message: str) -> str:
    """Send a plain text question to Gemini."""
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {"role": "user", "parts": [{"text": user_message}]}
        ],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1500},
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(GEMINI_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def ask_gemini_image(image_bytes: bytes, mime_type: str, caption: str = "") -> str:
    """Send a chart image + optional caption to Gemini vision."""
    b64 = base64.b64encode(image_bytes).decode()
    parts = [
        {"inline_data": {"mime_type": mime_type, "data": b64}},
        {"text": caption if caption else "Analyze this trading chart and give me a full signal."},
    ]
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1500},
    }
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(GEMINI_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ─────────────────────────────────────────────
#  TELEGRAM HANDLERS
# ─────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 *Welcome to AI Trading Signals Bot!*\n\n"
        "I'm your professional AI trading analyst powered by cutting-edge AI.\n\n"
        "📌 *What I can do:*\n"
        "• Analyze Gold (XAUUSD), Forex & Crypto\n"
        "• Give Buy/Sell signals with Entry, SL & TP\n"
        "• Analyze chart screenshots you send me\n"
        "• Answer any trading question\n\n"
        "💬 *Just ask me anything like:*\n"
        "_\"Should I buy gold now?\"_\n"
        "_\"Analyze EURUSD for me\"_\n"
        "_\"What's Bitcoin doing?\"_\n"
        "Or simply *send a chart screenshot!* 📸\n\n"
        "⚠️ _Always trade with proper risk management._\n\n"
        "Let's make pips! 💰"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 *AI Trading Signals Bot — Help*\n\n"
        "*Commands:*\n"
        "/start — Welcome message\n"
        "/help — This help menu\n"
        "/gold — Quick Gold analysis\n"
        "/forex — Top Forex pairs overview\n"
        "/crypto — Crypto market overview\n\n"
        "*How to use:*\n"
        "• Type any question about a market\n"
        "• Send a screenshot of any chart\n"
        "• Ask for specific pairs or assets\n\n"
        "📊 I'll give you Entry, Stop Loss & Take Profit every time!"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_gold(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analyzing Gold (XAUUSD)... please wait ⏳")
    try:
        result = await ask_gemini_text(
            "Give me a full current analysis and trading signal for Gold (XAUUSD) right now. "
            "Include entry price, stop loss, and three take profit targets."
        )
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_forex(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analyzing top Forex pairs... please wait ⏳")
    try:
        result = await ask_gemini_text(
            "Give me a quick overview and the best trading opportunity right now among "
            "EURUSD, GBPUSD, USDJPY, and AUDUSD. Pick the best one and give a full signal."
        )
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analyzing crypto markets... please wait ⏳")
    try:
        result = await ask_gemini_text(
            "Give me the best crypto trading opportunity right now among BTC, ETH, and BNB. "
            "Pick the strongest setup and give a full trading signal with entry, SL, and TP."
        )
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    await update.message.reply_text("🧠 Analyzing... please wait ⏳")
    try:
        result = await ask_gemini_text(user_msg)
        # Telegram max message length is 4096 chars
        if len(result) > 4000:
            for i in range(0, len(result), 4000):
                await update.message.reply_text(result[i:i+4000])
        else:
            await update.message.reply_text(result)
    except Exception as e:
        logging.exception("Gemini text error")
        await update.message.reply_text(
            "❌ Something went wrong. Please try again in a moment."
        )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 Received your chart! Analyzing... please wait ⏳")
    try:
        # Get highest-res photo
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()
        caption = update.message.caption or ""
        result = await ask_gemini_image(bytes(image_bytes), "image/jpeg", caption)
        if len(result) > 4000:
            for i in range(0, len(result), 4000):
                await update.message.reply_text(result[i:i+4000])
        else:
            await update.message.reply_text(result)
    except Exception as e:
        logging.exception("Gemini image error")
        await update.message.reply_text(
            "❌ Could not analyze the image. Please try again."
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle images sent as documents (uncompressed)."""
    doc = update.message.document
    if doc.mime_type and doc.mime_type.startswith("image/"):
        await update.message.reply_text("📸 Received your chart! Analyzing... please wait ⏳")
        try:
            file = await context.bot.get_file(doc.file_id)
            image_bytes = await file.download_as_bytearray()
            caption = update.message.caption or ""
            result = await ask_gemini_image(bytes(image_bytes), doc.mime_type, caption)
            if len(result) > 4000:
                for i in range(0, len(result), 4000):
                    await update.message.reply_text(result[i:i+4000])
            else:
                await update.message.reply_text(result)
        except Exception as e:
            logging.exception("Gemini document image error")
            await update.message.reply_text("❌ Could not analyze the image. Please try again.")
    else:
        await update.message.reply_text(
            "📎 I can only analyze image files. Please send a chart screenshot!"
        )


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO,
    )

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("gold",   cmd_gold))
    app.add_handler(CommandHandler("forex",  cmd_forex))
    app.add_handler(CommandHandler("crypto", cmd_crypto))

    # Messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    print("🤖 Trading Signals Bot is LIVE! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
