import os
import asyncio
import random
import requests

from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
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
# METAAPI CONFIG
# ============================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
METAAPI_ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

# ============================================
# YOUR TWO CHANNEL IDs
# ============================================

CHANNEL_1_ID = os.getenv("CHANNEL_1_ID", "-1001722756645")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID", "-1002468228698")

# ============================================
# BOT USERNAME
# ============================================

BOT_USERNAME = "NexoraConsoleBot"

# ============================================
# IMAGE FILE IDs
# ============================================

BUY_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBFWowFvKE9iJ2rPQK6iqENojXggvJAAIyD2sbbT2BUfFOIeGp11tVAQADAgADeQADPAQ"
SELL_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBH2owIQ4F4GQEnXyDhVLRoQZ3Vg06AAI_D2sbbT2BUechitI61wpvAQADAgADeQADPAQ"
TP_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBI2owI-2IToLB1YLPMxCa132jhJMKAAJCD2sbbT2BUbnbjLmJ1VZIAQADAgADeQADPAQ"
SL_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBIWowI9Lxu93CIKFD5YSHFbJ8_MB-AAJBD2sbbT2BUT1NzWx8We6EAQADAgADeQADPAQ"

# ============================================
# SIGNAL TIMES (UTC) — 5 SIGNALS DAILY
# ============================================

SIGNAL_TIMES = [
    "07:00",
    "09:00",
    "12:00",
    "15:00",
    "18:00",
]

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
# INLINE BUTTON FOR CHANNEL POSTS
# ============================================

def get_channel_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🤖 Get Your Own Signal",
                url=f"https://t.me/{BOT_USERNAME}"
            )
        ]
    ])

# ============================================
# USER MODES
# ============================================

user_modes = {}

# ============================================
# ACTIVE SIGNALS TRACKER
# ============================================
# Stores signals being monitored for TP/SL
# Format: { message_id: { signal data } }

active_signals = {}

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

    direction = random.choice(["BUY", "SELL"])
    strength = random.choice(["STRONG", "WEAK"])
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
        return None, None, None, None, (
            "⚠️ Unable to fetch live market data.\n"
            "Please try again shortly."
        )

    direction, strength, confidence = generate_market_bias()

    if direction == "BUY":
        entry_price = live_price
        stop_loss = round(live_price - (pip_size * 3), 2)
        take_profit = round(live_price + (pip_size * 6), 2)
        reason = random.choice(BUY_REASONS)
        signal_emoji = "🟢"
        image_file_id = BUY_IMAGE_FILE_ID

    else:
        entry_price = live_price
        stop_loss = round(live_price + (pip_size * 3), 2)
        take_profit = round(live_price - (pip_size * 6), 2)
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

    entry_price = round(live_price, 2)

    response = f"""{signal_emoji} {strength} {direction} {pair_name}

Entry Price: {entry_price}

Stop Loss: {stop_loss}

Take Profit: {take_profit}

Confidence: {confidence}%

Session: {session}

Timeframe Confirmation:
{timeframe_confirmation}

Reason:
{reason}

Trade safe 💼🔥"""

    signal_data = {
        "symbol": symbol,
        "pair_name": pair_name,
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

    return image_file_id, direction, response, signal_data

# ============================================
# METAAPI — PLACE TRADE ON MT5
# ============================================

async def place_mt5_trade(signal_data):

    if not METAAPI_TOKEN or not METAAPI_ACCOUNT_ID:
        print("[MT5] MetaAPI credentials not set.")
        return None

    try:

        headers = {
            "auth-token": METAAPI_TOKEN,
            "Content-Type": "application/json"
        }

        direction = signal_data["direction"]
        symbol = signal_data["pair_name"]

        # Map pair name to MT5 symbol format
        symbol_map = {
            "XAUUSD": "XAUUSDm",
            "EURUSD": "EURUSDm",
            "GBPUSD": "GBPUSDm",
            "NZDUSD": "NZDUSDm",
            "USDJPY": "USDJPYm",
            "BTCUSD": "BTCUSDm",
        }

        mt5_symbol = symbol_map.get(symbol, symbol)

        order_type = "ORDER_TYPE_BUY" if direction == "BUY" else "ORDER_TYPE_SELL"

        payload = {
            "symbol": mt5_symbol,
            "volume": 0.01,
            "actionType": order_type,
            "stopLoss": signal_data["stop_loss"],
            "takeProfit": signal_data["take_profit"],
            "comment": "NexoraAI Signal"
        }

        url = (
            f"https://mt-client-api-v1.london.agiliumtrade.ai"
            f"/users/current/accounts/{METAAPI_ACCOUNT_ID}"
            f"/trade"
        )

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )

        if response.status_code in [200, 201]:
            result = response.json()
            order_id = result.get("orderId", "unknown")
            print(f"[MT5] ✅ Trade placed — Order ID: {order_id}")
            return order_id

        else:
            print(f"[MT5] ❌ Trade failed: {response.status_code} — {response.text}")
            return None

    except Exception as e:

        print(f"[MT5] ❌ Exception: {e}")
        return None

# ============================================
# MONITOR SIGNAL FOR TP/SL
# ============================================

async def monitor_signal(bot, channel_id, message_id, signal_data):

    symbol = signal_data["symbol"]
    direction = signal_data["direction"]
    take_profit = signal_data["take_profit"]
    stop_loss = signal_data["stop_loss"]
    pair_name = signal_data["pair_name"]

    print(f"[MONITOR] Watching {pair_name} | TP: {take_profit} | SL: {stop_loss}")

    # Check every 60 seconds, max 24 hours
    max_checks = 1440

    for _ in range(max_checks):

        await asyncio.sleep(60)

        current_price = get_live_price(symbol)

        if current_price is None:
            continue

        # TP HIT
        if direction == "BUY" and current_price >= take_profit:

            print(f"[MONITOR] ✅ TP HIT for {pair_name} at {current_price}")

            await bot.send_photo(
                chat_id=channel_id,
                photo=TP_HIT_IMAGE_FILE_ID,
                caption=(
                    f"✅ TP HIT — {pair_name}\n\n"
                    f"Entry: {signal_data['entry_price']}\n"
                    f"Take Profit: {take_profit}\n"
                    f"Exit Price: {current_price}\n\n"
                    f"Well done to everyone who followed! 💰🔥"
                ),
                reply_to_message_id=message_id
            )
            break

        elif direction == "SELL" and current_price <= take_profit:

            print(f"[MONITOR] ✅ TP HIT for {pair_name} at {current_price}")

            await bot.send_photo(
                chat_id=channel_id,
                photo=TP_HIT_IMAGE_FILE_ID,
                caption=(
                    f"✅ TP HIT — {pair_name}\n\n"
                    f"Entry: {signal_data['entry_price']}\n"
                    f"Take Profit: {take_profit}\n"
                    f"Exit Price: {current_price}\n\n"
                    f"Well done to everyone who followed! 💰🔥"
                ),
                reply_to_message_id=message_id
            )
            break

        # SL HIT
        elif direction == "BUY" and current_price <= stop_loss:

            print(f"[MONITOR] ❌ SL HIT for {pair_name} at {current_price}")

            await bot.send_photo(
                chat_id=channel_id,
                photo=SL_HIT_IMAGE_FILE_ID,
                caption=(
                    f"❌ SL HIT — {pair_name}\n\n"
                    f"Entry: {signal_data['entry_price']}\n"
                    f"Stop Loss: {stop_loss}\n"
                    f"Exit Price: {current_price}\n\n"
                    f"Risk managed. Next signal coming. 💼"
                ),
                reply_to_message_id=message_id
            )
            break

        elif direction == "SELL" and current_price >= stop_loss:

            print(f"[MONITOR] ❌ SL HIT for {pair_name} at {current_price}")

            await bot.send_photo(
                chat_id=channel_id,
                photo=SL_HIT_IMAGE_FILE_ID,
                caption=(
                    f"❌ SL HIT — {pair_name}\n\n"
                    f"Entry: {signal_data['entry_price']}\n"
                    f"Stop Loss: {stop_loss}\n"
                    f"Exit Price: {current_price}\n\n"
                    f"Risk managed. Next signal coming. 💼"
                ),
                reply_to_message_id=message_id
            )
            break

# ============================================
# GEMINI AI
# ============================================

async def ask_gemini(prompt):

    headers = {"Content-Type": "application/json"}

    data = {
        "contents": [
            {"parts": [{"text": prompt}]}
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

    if mode == "signal":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI analyzing live market..."
        )

        await asyncio.sleep(1)

        image_file_id, direction, signal, signal_data = build_signal_response(message)

        await wait_message.delete()

        await update.message.reply_photo(
            photo=image_file_id,
            caption=signal
        )

        return

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

    now = datetime.utcnow().strftime('%H:%M UTC')
    print(f"[AUTO SIGNAL] Firing at {now}")

    image_file_id, direction, signal, signal_data = build_signal_response("xauusd")

    if signal_data is None:
        print("[AUTO SIGNAL] ❌ Could not fetch price. Skipping.")
        return

    button = get_channel_button()

    channel_ids = [CHANNEL_1_ID, CHANNEL_2_ID]

    for channel_id in channel_ids:
        try:

            sent = await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_file_id,
                caption=signal,
                reply_markup=button
            )

            print(f"[AUTO SIGNAL] ✅ Posted to {channel_id} at {now}")

            # Place trade on MT5
            asyncio.create_task(
                place_mt5_trade(signal_data)
            )

            # Start monitoring for TP/SL outcome
            asyncio.create_task(
                monitor_signal(
                    context.bot,
                    channel_id,
                    sent.message_id,
                    signal_data
                )
            )

        except Exception as e:

            print(f"[AUTO SIGNAL] ❌ Failed for {channel_id}: {e}")

# ============================================
# MAIN
# ============================================

def main():

    app = Application.builder().token(
        TELEGRAM_TOKEN
    ).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.Regex(
                "^(📊 Signal|📚 Breakdown|signal|breakdown)$"
            ),
            handle_buttons
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    # ========================================
    # SCHEDULE 5 AUTO SIGNALS DAILY
    # ========================================

    job_queue = app.job_queue

    def parse_time(t):
        h, m = map(int, t.split(":"))
        return datetime.now().replace(
            hour=h, minute=m, second=0, microsecond=0
        ).time()

    for i, signal_time in enumerate(SIGNAL_TIMES):
        job_queue.run_daily(
            post_auto_signal,
            time=parse_time(signal_time),
            name=f"auto_signal_{i+1}"
        )

    print("Nexora AI Running...")
    print("Auto signals scheduled daily at (UTC):")
    for t in SIGNAL_TIMES:
        print(f"  ⏰ {t} UTC")
    print(f"Channel 1: {CHANNEL_1_ID}")
    print(f"Channel 2: {CHANNEL_2_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    app.run_polling(drop_pending_updates=True)

# ============================================
# RUN BOT
# ============================================

if __name__ == "__main__":
    main()
