import os
import asyncio
import random
import requests
import json

from datetime import datetime
from pathlib import Path

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
    CallbackQueryHandler,
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
NEWS_API_KEY = os.getenv("NEWS_API_KEY")

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
# VERIFICATION GROUP ID
# ============================================

VERIFY_GROUP_ID = "-1002400215654"

# ============================================
# EXNESS AFFILIATE LINK
# ============================================

EXNESS_LINK = "https://www.exness.com/boarding/sign-up/a/vlnafmua"

# ============================================
# BOT USERNAME
# ============================================

BOT_USERNAME = "NexoraConsoleBot"

# ============================================
# FREE TRIAL LIMIT
# ============================================

FREE_TRIAL_LIMIT = 3

# ============================================
# IMAGE FILE IDs
# ============================================

BUY_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBFWowFvKE9iJ2rPQK6iqENojXggvJAAIyD2sbbT2BUfFOIeGp11tVAQADAgADeQADPAQ"
SELL_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBH2owIQ4F4GQEnXyDhVLRoQZ3Vg06AAI_D2sbbT2BUechitI61wpvAQADAgADeQADPAQ"
TP_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBI2owI-2IToLB1YLPMxCa132jhJMKAAJCD2sbbT2BUbnbjLmJ1VZIAQADAgADeQADPAQ"
SL_HIT_IMAGE_FILE_ID = "AgACAgQAAxkBAAIBIWowI9Lxu93CIKFD5YSHFbJ8_MB-AAJBD2sbbT2BUT1NzWx8We6EAQADAgADeQADPAQ"

# ============================================
# DAILY SCHEDULE (UTC)
# ============================================

DAILY_SCHEDULE = [
    ("06:00", "news",   "morning"),
    ("07:00", "signal", "xauusd"),
    ("09:00", "signal", "btcusd"),
    ("11:00", "news",   "midday"),
    ("13:00", "signal", "xagusd"),
    ("15:00", "signal", "usoil"),
    ("17:00", "news",   "afternoon"),
    ("19:00", "signal", "gbpusd"),
    ("21:00", "signal", "gbpjpy"),
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
# KEYBOARDS
# ============================================

main_keyboard = ReplyKeyboardMarkup(
    [["📊 Signal", "📚 Breakdown"]],
    resize_keyboard=True
)

# ============================================
# INLINE BUTTON FOR CHANNEL POSTS
# ============================================

def get_channel_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🤖 Get Your Own Signal",
            url=f"https://t.me/{BOT_USERNAME}"
        )]
    ])

# ============================================
# USER DATA STORAGE
# ============================================

user_modes = {}

VERIFIED_FILE = "verified_users.json"
TRIAL_FILE = "trial_users.json"

def load_json(filename):
    if Path(filename).exists():
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f)

verified_users = load_json(VERIFIED_FILE)
trial_users = load_json(TRIAL_FILE)
pending_verifications = {}

# ============================================
# PAIR CONFIG
# ============================================

PAIR_CONFIG = {
    "xauusd": {
        "symbol": "XAU/USD",
        "pair_name": "XAUUSD",
        "pip_size": 10,
        "mt5_symbol": "XAUUSDm",
        "display": "Gold (XAUUSD) 🥇",
    },
    "btcusd": {
        "symbol": "BTC/USD",
        "pair_name": "BTCUSD",
        "pip_size": 300,
        "mt5_symbol": "BTCUSDm",
        "display": "Bitcoin (BTCUSD) ₿",
    },
    "xagusd": {
        "symbol": "XAG/USD",
        "pair_name": "XAGUSD",
        "pip_size": 0.30,
        "mt5_symbol": "XAGUSDm",
        "display": "Silver (XAGUSD) 🥈",
    },
    "usoil": {
        "symbol": "WTI/USD",
        "pair_name": "USOIL",
        "pip_size": 0.50,
        "mt5_symbol": "USOILm",
        "display": "US Oil (WTI) 🛢️",
    },
    "gbpusd": {
        "symbol": "GBP/USD",
        "pair_name": "GBPUSD",
        "pip_size": 0.0025,
        "mt5_symbol": "GBPUSDm",
        "display": "GBP/USD 🇬🇧",
    },
    "gbpjpy": {
        "symbol": "GBP/JPY",
        "pair_name": "GBPJPY",
        "pip_size": 0.30,
        "mt5_symbol": "GBPJPYm",
        "display": "GBP/JPY 🇯🇵",
    },
}

# ============================================
# TRIAL HELPERS
# ============================================

def get_trial_count(user_id):
    return trial_users.get(str(user_id), {}).get("count", 0)

def increment_trial(user_id):
    uid = str(user_id)
    if uid not in trial_users:
        trial_users[uid] = {"count": 0}
    trial_users[uid]["count"] += 1
    save_json(TRIAL_FILE, trial_users)
    return trial_users[uid]["count"]

def is_verified(user_id):
    return str(user_id) in verified_users

def trial_remaining(user_id):
    return max(0, FREE_TRIAL_LIMIT - get_trial_count(user_id))

# ============================================
# VERIFICATION GATE MESSAGE
# ============================================

async def send_verification_gate(update):
    await update.message.reply_text(
        "🔐 You've used your 3 FREE trial signals!\n\n"
        "Hope you loved what you saw! 🔥\n\n"
        "To continue enjoying UNLIMITED FREE signals, "
        "live market analysis and AI breakdowns — "
        "you just need ONE simple step:\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔓 HOW TO UNLOCK FULL ACCESS\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "📌 Register a FREE trading account with our official "
        "broker partner — Exness — using our unique link.\n\n"
        "No payment. No subscription. Completely FREE.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👇 CHOOSE YOUR SITUATION:\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ SITUATION 1 — Already registered on Exness "
        "using our link before:\n"
        "👉 Type the email you used to register on Exness "
        "and we will verify you instantly.\n\n"
        "📝 SITUATION 2 — Not yet registered or registered "
        "without our link:\n"
        "👉 Click the button below to create your FREE Exness "
        "account. Once done, come back and type your email.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📧 Already registered? Type your Exness email now 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📝 I'm New — Register on Exness FREE 👆",
                url=EXNESS_LINK
            )]
        ])
    )

# ============================================
# START COMMAND
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"

    if is_verified(user_id):
        await update.message.reply_text(
            f"👋 Welcome back, {username}!\n\n"
            f"✅ You're a verified Nexora AI trader.\n\n"
            f"Choose an option below to get started.",
            reply_markup=main_keyboard
        )
        return

    remaining = trial_remaining(user_id)

    if remaining > 0:
        await update.message.reply_text(
            f"👋 Hello {username}, welcome to Nexora AI! 🤖\n\n"
            f"I am your personal AI trading assistant — delivering "
            f"professional trading signals, live market analysis "
            f"and AI-powered breakdowns.\n\n"
            f"🎁 You have {remaining} FREE trial signal(s) to use!\n\n"
            f"Choose an option below to get started 👇",
            reply_markup=main_keyboard
        )
        return

    user_modes[user_id] = "awaiting_email"
    await send_verification_gate(update)

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
# BUY / SELL REASONS
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
    config = PAIR_CONFIG["xauusd"]

    for key in PAIR_CONFIG:
        if key in q:
            config = PAIR_CONFIG[key]
            break

    symbol = config["symbol"]
    pair_name = config["pair_name"]
    pip_size = config["pip_size"]
    display = config["display"]

    live_price = get_live_price(symbol)

    if live_price is None:
        return None, None, None, None

    direction, strength, confidence = generate_market_bias()

    if direction == "BUY":
        entry_price = round(live_price, 2)
        stop_loss = round(live_price - (pip_size * 3), 2)
        take_profit = round(live_price + (pip_size * 6), 2)
        reason = random.choice(BUY_REASONS)
        signal_emoji = "🟢"
        image_file_id = BUY_IMAGE_FILE_ID
    else:
        entry_price = round(live_price, 2)
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

    response = f"""{signal_emoji} {strength} {direction} {display}

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
        "mt5_symbol": config["mt5_symbol"],
        "direction": direction,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }

    return image_file_id, direction, response, signal_data

# ============================================
# NEWS FETCHER
# ============================================

def fetch_market_news():
    if not NEWS_API_KEY:
        return None
    try:
        url = (
            f"https://newsapi.org/v2/top-headlines"
            f"?category=business&language=en"
            f"&pageSize=10&apiKey={NEWS_API_KEY}"
        )
        response = requests.get(url, timeout=10)
        data = response.json()
        if data.get("status") != "ok":
            return None
        articles = [
            a for a in data.get("articles", [])
            if a.get("urlToImage") and a.get("title") and a.get("description")
        ]
        if not articles:
            return None
        return random.choice(articles)
    except Exception as e:
        print(f"[NEWS] Fetch error: {e}")
        return None

# ============================================
# NEWS SUMMARY GENERATOR
# ============================================

async def generate_news_summary(article, session_type):

    title = article.get("title", "")
    description = article.get("description", "")
    source = article.get("source", {}).get("name", "")

    if session_type == "morning":
        session_label = "Morning Market Briefing 🌅"
        prompt_context = "morning trading session opening"
    elif session_type == "midday":
        session_label = "Midday Market Update ☀️"
        prompt_context = "midday trading activity"
    else:
        session_label = "Afternoon Market Briefing 🌆"
        prompt_context = "afternoon and closing session"

    prompt = f"""
You are Nexora AI, a professional financial news analyst.
Write a SHORT, ENGAGING market news post for a Telegram trading channel.

SESSION: {session_label}
CONTEXT: {prompt_context}
NEWS HEADLINE: {title}
NEWS DETAILS: {description}
SOURCE: {source}

RULES:
- Maximum 150 words
- Start with a punchy opening line
- Include what this means for traders
- Mention impact on gold, forex, or crypto if relevant
- End with a motivational trading line
- No markdown symbols like ** or ##
- No hashtags
- Professional but exciting tone
- Use emojis naturally
"""

    return await ask_gemini(prompt)

# ============================================
# POST NEWS TO CHANNELS
# ============================================

async def post_news(context: ContextTypes.DEFAULT_TYPE):

    session_type = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')
    print(f"[NEWS] Posting {session_type} news at {now}")

    article = fetch_market_news()
    if article is None:
        print("[NEWS] No article found. Skipping.")
        return

    image_url = article.get("urlToImage")
    summary = await generate_news_summary(article, session_type)
    summary = clean_text(summary)

    button = get_channel_button()
    channel_ids = [CHANNEL_1_ID, CHANNEL_2_ID]

    for channel_id in channel_ids:
        try:
            if image_url:
                await context.bot.send_photo(
                    chat_id=channel_id,
                    photo=image_url,
                    caption=summary,
                    reply_markup=button
                )
            else:
                await context.bot.send_message(
                    chat_id=channel_id,
                    text=summary,
                    reply_markup=button
                )
            print(f"[NEWS] ✅ {session_type} posted to {channel_id}")
        except Exception as e:
            print(f"[NEWS] ❌ Failed for {channel_id}: {e}")

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
        mt5_symbol = signal_data["mt5_symbol"]
        order_type = (
            "ORDER_TYPE_BUY" if direction == "BUY"
            else "ORDER_TYPE_SELL"
        )
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
            f"/users/current/accounts/{METAAPI_ACCOUNT_ID}/trade"
        )
        response = requests.post(
            url, headers=headers, json=payload, timeout=30
        )
        if response.status_code in [200, 201]:
            result = response.json()
            order_id = result.get("orderId", "unknown")
            print(f"[MT5] ✅ Trade placed — Order ID: {order_id}")
            return order_id
        else:
            print(f"[MT5] ❌ Trade failed: {response.status_code}")
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
    entry_price = signal_data["entry_price"]

    print(f"[MONITOR] Watching {pair_name} | TP: {take_profit} | SL: {stop_loss}")

    max_checks = 1440

    for _ in range(max_checks):
        await asyncio.sleep(60)
        current_price = get_live_price(symbol)

        if current_price is None:
            continue

        tp_hit = (
            (direction == "BUY" and current_price >= take_profit) or
            (direction == "SELL" and current_price <= take_profit)
        )
        sl_hit = (
            (direction == "BUY" and current_price <= stop_loss) or
            (direction == "SELL" and current_price >= stop_loss)
        )

        if tp_hit:
            print(f"[MONITOR] ✅ TP HIT {pair_name} at {current_price}")
            await bot.send_photo(
                chat_id=channel_id,
                photo=TP_HIT_IMAGE_FILE_ID,
                caption=(
                    f"✅ TP HIT — {pair_name}\n\n"
                    f"Entry: {entry_price}\n"
                    f"Take Profit: {take_profit}\n"
                    f"Exit Price: {round(current_price, 2)}\n\n"
                    f"Well done to everyone who followed! 💰🔥"
                ),
                reply_to_message_id=message_id
            )
            break

        elif sl_hit:
            print(f"[MONITOR] ❌ SL HIT {pair_name} at {current_price}")
            await bot.send_photo(
                chat_id=channel_id,
                photo=SL_HIT_IMAGE_FILE_ID,
                caption=(
                    f"❌ SL HIT — {pair_name}\n\n"
                    f"Entry: {entry_price}\n"
                    f"Stop Loss: {stop_loss}\n"
                    f"Exit Price: {round(current_price, 2)}\n\n"
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
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    try:
        response = requests.post(
            GEMINI_URL, headers=headers, json=data, timeout=30
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
            OPENROUTER_URL, headers=headers, json=data, timeout=30
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

    for key, config in PAIR_CONFIG.items():
        if key in q:
            symbol = config["symbol"]
            pair_name = config["display"]
            break

    live_price = get_live_price(symbol)
    live_price_text = (
        str(round(live_price, 4)) if live_price
        else "Live price unavailable"
    )

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
IMPORTANT: Use the REAL LIVE PRICE. Do NOT invent fake prices.

PAIR: {pair_name}
LIVE PRICE: {live_price_text}
SESSION: {session}

RULES:
- Clean formatting, no markdown, no hashtags, no stars
- Beginner friendly, professional tone
- Include technical and fundamental analysis
- Include sentiment and trade idea
- Modern clean formatting

QUESTION: {question}
"""

    return await ask_gemini(prompt)

# ============================================
# CLEAN AI RESPONSE
# ============================================

def clean_text(text):
    text = text.replace("###", "").replace("##", "")
    text = text.replace("**", "").replace("---", "").replace("__", "")
    return text.strip()

# ============================================
# HANDLE BUTTONS
# ============================================

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    text = update.message.text.lower()

    if not is_verified(user_id) and get_trial_count(user_id) >= FREE_TRIAL_LIMIT:
        user_modes[user_id] = "awaiting_email"
        await send_verification_gate(update)
        return

    if "signal" in text:
        user_modes[user_id] = "signal"
        await update.message.reply_text(
            "📊 Signal Mode Activated\n\n"
            "Now ask for a signal.\n\n"
            "Examples:\n"
            "• XAUUSD\n• BTCUSD\n• XAGUSD\n• USOIL\n• GBPUSD\n• GBPJPY"
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
            "• GBPJPY market analysis"
        )
        return

# ============================================
# CALLBACK HANDLER — APPROVE / REJECT BUTTONS
# ============================================

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    data = query.data

    # ── APPROVE ───────────────────────────────────
    if data.startswith("approve_"):

        target_id = data.replace("approve_", "")
        email = pending_verifications.get(target_id, "unknown")

        verified_users[target_id] = {
            "email": email,
            "verified_at": str(datetime.utcnow())
        }
        save_json(VERIFIED_FILE, verified_users)

        if target_id in pending_verifications:
            del pending_verifications[target_id]

        # Notify the user
        try:
            await context.bot.send_message(
                chat_id=int(target_id),
                text=(
                    "🎉 Congratulations! You're now a verified "
                    "Nexora AI trader!\n\n"
                    "✅ Full access unlocked — unlimited signals, "
                    "live market breakdowns and AI analysis "
                    "are all yours.\n\n"
                    "Welcome to the winning side. "
                    "Let's get to work! 💼🔥"
                ),
                reply_markup=main_keyboard
            )
        except Exception as e:
            print(f"[APPROVE] Could not message user: {e}")

        # Update the group message
        await query.edit_message_text(
            text=(
                f"✅ APPROVED\n\n"
                f"🆔 User ID: {target_id}\n"
                f"📧 Email: {email}\n\n"
                f"User has been notified and granted full access."
            )
        )

    # ── REJECT ────────────────────────────────────
    elif data.startswith("reject_"):

        target_id = data.replace("reject_", "")
        email = pending_verifications.get(target_id, "unknown")

        if target_id in pending_verifications:
            del pending_verifications[target_id]

        # Notify the user
        try:
            await context.bot.send_message(
                chat_id=int(target_id),
                text=(
                    "❌ Verification Failed.\n\n"
                    "Unfortunately, we could not find an Exness account "
                    "linked to your email that was registered through "
                    "our official link.\n\n"
                    "This could mean:\n"
                    "• You registered on Exness without using our link\n"
                    "• You used a different email address\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ HOW TO FIX THIS:\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Click the link below to create a NEW Exness account "
                    "using our official link. It's completely FREE and "
                    "takes less than 2 minutes.\n\n"
                    f"🔗 {EXNESS_LINK}\n\n"
                    "Once done, come back here and type your new "
                    "email address to get verified instantly. 🚀"
                )
            )
        except Exception as e:
            print(f"[REJECT] Could not message user: {e}")

        # Update the group message
        await query.edit_message_text(
            text=(
                f"❌ REJECTED\n\n"
                f"🆔 User ID: {target_id}\n"
                f"📧 Email: {email}\n\n"
                f"User has been notified to register via the correct link."
            )
        )

# ============================================
# HANDLE TEXT
# ============================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"
    message = update.message.text.strip()

    # ── AWAITING EMAIL ────────────────────────────
    if user_modes.get(user_id) == "awaiting_email":

        email = message.strip().lower()

        if "@" not in email or "." not in email:
            await update.message.reply_text(
                "⚠️ That doesn't look like a valid email address.\n\n"
                "Please enter the email you used to "
                "register on Exness 👇"
            )
            return

        pending_verifications[user_id] = email

        await update.message.reply_text(
            "⏳ Got it! Your verification request has been submitted.\n\n"
            "Our team is reviewing your details right now. "
            "You'll receive a confirmation message shortly.\n\n"
            "Sit tight — greatness is loading! 🚀"
        )

        # Send to verification group with tap buttons
        try:
            await context.bot.send_message(
                chat_id=VERIFY_GROUP_ID,
                text=(
                    f"🔔 NEW VERIFICATION REQUEST\n\n"
                    f"👤 User: @{username}\n"
                    f"🆔 ID: {user_id}\n"
                    f"📧 Email: {email}\n\n"
                    f"Tap a button below to approve or reject:"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✅ Approve",
                            callback_data=f"approve_{user_id}"
                        ),
                        InlineKeyboardButton(
                            "❌ Reject",
                            callback_data=f"reject_{user_id}"
                        )
                    ]
                ])
            )
        except Exception as e:
            print(f"[VERIFY] Failed to send to group: {e}")

        user_modes[user_id] = None
        return

    # ── TRIAL / VERIFIED CHECK ────────────────────
    if not is_verified(user_id) and get_trial_count(user_id) >= FREE_TRIAL_LIMIT:
        user_modes[user_id] = "awaiting_email"
        await send_verification_gate(update)
        return

    mode = user_modes.get(user_id)

    # ── SIGNAL MODE ───────────────────────────────
    if mode == "signal":

        if not is_verified(user_id):
            count = increment_trial(user_id)
            if count > FREE_TRIAL_LIMIT:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
                return

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI analyzing live market..."
        )

        await asyncio.sleep(1)

        image_file_id, direction, signal, signal_data = (
            build_signal_response(message)
        )

        await wait_message.delete()

        if image_file_id:
            await update.message.reply_photo(
                photo=image_file_id,
                caption=signal
            )

            if not is_verified(user_id):
                remaining = trial_remaining(user_id)
                if remaining > 0:
                    await update.message.reply_text(
                        f"⚡ You have {remaining} free trial "
                        f"signal(s) remaining.\n"
                        f"Verify your Exness account for "
                        f"unlimited access!"
                    )
                else:
                    user_modes[user_id] = "awaiting_email"
                    await send_verification_gate(update)
        else:
            await update.message.reply_text(
                "⚠️ Unable to fetch live market data.\n"
                "Please try again shortly."
            )
        return

    # ── BREAKDOWN MODE ────────────────────────────
    if mode == "breakdown":

        if not is_verified(user_id):
            count = increment_trial(user_id)
            if count > FREE_TRIAL_LIMIT:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
                return

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI preparing market breakdown..."
        )

        response = await generate_breakdown(message)
        response = clean_text(response)

        await wait_message.edit_text(response)

        if not is_verified(user_id):
            remaining = trial_remaining(user_id)
            if remaining > 0:
                await update.message.reply_text(
                    f"⚡ You have {remaining} free trial "
                    f"signal(s) remaining.\n"
                    f"Verify your Exness account for "
                    f"unlimited access!"
                )
            else:
                user_modes[user_id] = "awaiting_email"
                await send_verification_gate(update)
        return

    # ── DEFAULT ───────────────────────────────────
    await update.message.reply_text(
        "Choose an option below:",
        reply_markup=main_keyboard
    )

# ============================================
# AUTO SIGNAL — POSTED TO BOTH CHANNELS
# ============================================

async def post_auto_signal(context: ContextTypes.DEFAULT_TYPE):

    pair_keyword = context.job.data
    now = datetime.utcnow().strftime('%H:%M UTC')

    print(f"[AUTO SIGNAL] {pair_keyword.upper()} firing at {now}")

    image_file_id, direction, signal, signal_data = (
        build_signal_response(pair_keyword)
    )

    if signal_data is None:
        print(f"[AUTO SIGNAL] ❌ Could not fetch price for {pair_keyword}.")
        return

    channel_ids = [CHANNEL_1_ID, CHANNEL_2_ID]

    for channel_id in channel_ids:
        try:
            sent = await context.bot.send_photo(
                chat_id=channel_id,
                photo=image_file_id,
                caption=signal,
                reply_markup=get_channel_button()
            )
            print(
                f"[AUTO SIGNAL] ✅ {pair_keyword.upper()} "
                f"posted to {channel_id}"
            )

            asyncio.create_task(place_mt5_trade(signal_data))
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

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))

    # Inline button callback — approve/reject
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Button handler
    app.add_handler(
        MessageHandler(
            filters.Regex("^(📊 Signal|📚 Breakdown|signal|breakdown)$"),
            handle_buttons
        )
    )

    # Text handler
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_text
        )
    )

    # Schedule
    job_queue = app.job_queue

    def parse_time(t):
        h, m = map(int, t.split(":"))
        return datetime.now().replace(
            hour=h, minute=m, second=0, microsecond=0
        ).time()

    for i, (utc_time, post_type, data) in enumerate(DAILY_SCHEDULE):
        if post_type == "news":
            job_queue.run_daily(
                post_news,
                time=parse_time(utc_time),
                name=f"news_{i}_{data}",
                data=data
            )
        elif post_type == "signal":
            job_queue.run_daily(
                post_auto_signal,
                time=parse_time(utc_time),
                name=f"signal_{i}_{data}",
                data=data
            )

    print("Nexora AI Running...")
    print("Daily schedule (UTC):")
    for utc_time, post_type, data in DAILY_SCHEDULE:
        emoji = "📰" if post_type == "news" else "📊"
        print(f"  {emoji} {utc_time} UTC — {data.upper()}")
    print(f"Channel 1: {CHANNEL_1_ID}")
    print(f"Channel 2: {CHANNEL_2_ID}")
    print(f"Verify Group: {VERIFY_GROUP_ID}")
    print(f"Bot: @{BOT_USERNAME}")

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
