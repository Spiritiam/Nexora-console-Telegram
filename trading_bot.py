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
    ("06:00", "news",   "morning"),   # 7:00 AM Lagos
    ("07:00", "signal", "xauusd"),    # 8:00 AM Lagos
    ("09:00", "signal", "btcusd"),    # 10:00 AM Lagos
    ("11:00", "news",   "midday"),    # 12:00 PM Lagos
    ("13:00", "signal", "xagusd"),    # 2:00 PM Lagos
    ("15:00", "signal", "usoil"),     # 4:00 PM Lagos
    ("17:00", "news",   "afternoon"), # 6:00 PM Lagos
    ("19:00", "signal", "gbpusd"),    # 8:00 PM Lagos
    ("21:00", "signal", "gbpjpy"),    # 10:00 PM Lagos
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
# USER MODES & VERIFIED USERS
# ============================================

user_modes = {}

VERIFIED_FILE = "verified_users.json"

def load_verified():
    if Path(VERIFIED_FILE).exists():
        with open(VERIFIED_FILE, "r") as f:
            return json.load(f)
    return {}

def save_verified(data):
    with open(VERIFIED_FILE, "w") as f:
        json.dump(data, f)

verified_users = load_verified()

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
# START COMMAND
# ============================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = str(update.message.from_user.id)
    username = update.message.from_user.username or "Trader"

    if user_id in verified_users:
        await update.message.reply_text(
            f"👋 Welcome back, {username}!\n\n"
            f"✅ You're a verified Nexora AI trader.\n\n"
            f"Choose an option below to get started.",
            reply_markup=main_keyboard
        )
        return

    await update.message.reply_text(
        f"👋 Hello {username}, welcome to Nexora AI! 🤖\n\n"
        f"I am your personal AI trading assistant — I deliver "
        f"FREE unlimited professional trading signals, live market "
        f"analysis and AI-powered breakdowns straight to you.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🔓 HOW TO GET FREE ACCESS\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"To unlock full access, there is just ONE requirement:\n\n"
        f"📌 You must have a trading account with our official "
        f"broker partner — Exness — registered through our unique link.\n\n"
        f"That's it. No payment. No subscription. Completely FREE.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 CHOOSE YOUR SITUATION BELOW:\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"✅ SITUATION 1 — I have already registered on Exness "
        f"using the Nexora AI link in the past:\n"
        f"👉 Simply type the email address you used to register "
        f"on Exness below and we will verify you instantly.\n\n"
        f"📝 SITUATION 2 — I have NOT registered on Exness yet "
        f"or I registered without using our link:\n"
        f"👉 Click the button below to create your FREE Exness "
        f"account using our official link. Once done, come back "
        f"here and type the email you registered with.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 Already registered? Type your Exness email now 👇",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📝 I'm New — Register on Exness FREE 👆",
                url=EXNESS_LINK
            )]
        ])
    )

    user_modes[user_id] = "awaiting_email"

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
            f"?category=business"
            f"&language=en"
            f"&pageSize=10"
            f"&apiKey={NEWS_API_KEY}"
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
- Use emojis naturally (not excessively)
- Make traders feel informed and ready to trade
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

IMPORTANT:
Use the REAL LIVE PRICE provided below.
Do NOT invent fake prices.

PAIR: {pair_name}
LIVE PRICE: {live_price_text}
SESSION: {session}

RULES:
- Clean formatting
- No markdown symbols
- No hashtags
- No stars
- Beginner friendly
- Professional tone
- Include BOTH technical and fundamental analysis
- Include sentiment and trade idea
- Keep formatting modern and clean

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

    if user_id not in verified_users:
        await update.message.reply_text(
            "🔐 You need to verify your Exness account first.\n\n"
            "Type /start to begin verification."
        )
        return

    if "signal" in text:
        user_modes[user_id] = "signal"
        await update.message.reply_text(
            "📊 Signal Mode Activated\n\n"
            "Now ask for a signal.\n\n"
            "Examples:\n"
            "• XAUUSD\n"
            "• BTCUSD\n"
            "• XAGUSD\n"
            "• USOIL\n"
            "• GBPUSD\n"
            "• GBPJPY"
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
# APPROVE COMMAND
# ============================================

async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if str(update.message.chat_id) != VERIFY_GROUP_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /approve <user_id>")
        return

    target_id = context.args[0]
    email = pending_verifications.get(target_id, "unknown")

    verified_users[target_id] = {
        "email": email,
        "verified_at": str(datetime.utcnow())
    }
    save_verified(verified_users)

    if target_id in pending_verifications:
        del pending_verifications[target_id]

    try:
        await context.bot.send_message(
            chat_id=int(target_id),
            text=(
                "🎉 Congratulations! You're now a verified Nexora AI trader!\n\n"
                "✅ Full access unlocked — unlimited signals, live market "
                "breakdowns and AI analysis are all yours.\n\n"
                "Welcome to the winning side. Let's get to work! 💼🔥"
            ),
            reply_markup=main_keyboard
        )
    except Exception as e:
        print(f"[APPROVE] Could not message user: {e}")

    await update.message.reply_text(
        f"✅ User {target_id} ({email}) approved!"
    )

# ============================================
# REJECT COMMAND
# ============================================

async def reject_user(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if str(update.message.chat_id) != VERIFY_GROUP_ID:
        return

    if not context.args:
        await update.message.reply_text("Usage: /reject <user_id>")
        return

    target_id = context.args[0]

    if target_id in pending_verifications:
        del pending_verifications[target_id]

    try:
        await context.bot.send_message(
            chat_id=int(target_id),
            text=(
                "❌ Verification failed.\n\n"
                "We could not find your email linked to an Exness "
                "account registered through our link.\n\n"
                "Please register using our official link and try again:\n\n"
                f"🔗 {EXNESS_LINK}\n\n"
                "Once registered, come back and type your email to verify."
            )
        )
    except Exception as e:
        print(f"[REJECT] Could not message user: {e}")

    await update.message.reply_text(
        f"❌ User {target_id} rejected."
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
                "Please enter the email you used to register on Exness 👇"
            )
            return

        pending_verifications[user_id] = email

        await update.message.reply_text(
            "⏳ Got it! Your verification request has been submitted.\n\n"
            "Our team is reviewing your details right now. "
            "You'll receive a confirmation message shortly.\n\n"
            "Sit tight — greatness is loading! 🚀"
        )

        await context.bot.send_message(
            chat_id=VERIFY_GROUP_ID,
            text=(
                f"🔔 NEW VERIFICATION REQUEST\n\n"
                f"👤 User: @{username}\n"
                f"🆔 ID: {user_id}\n"
                f"📧 Email: {email}\n\n"
                f"✅ Approve: /approve {user_id}\n"
                f"❌ Reject: /reject {user_id}"
            )
        )

        user_modes[user_id] = None
        return

    # ── NOT VERIFIED ──────────────────────────────
    if user_id not in verified_users:
        await update.message.reply_text(
            "🔐 Please verify your account first.\n\n"
            "Type /start to begin."
        )
        return

    mode = user_modes.get(user_id)

    # ── SIGNAL MODE ───────────────────────────────
    if mode == "signal":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI analyzing live market..."
        )

        await asyncio.sleep(1)

        image_file_id, direction, signal, signal_data = build_signal_response(message)

        await wait_message.delete()

        if image_file_id:
            await update.message.reply_photo(
                photo=image_file_id,
                caption=signal
            )
        else:
            await update.message.reply_text(
                "⚠️ Unable to fetch live market data.\n"
                "Please try again shortly."
            )
        return

    # ── BREAKDOWN MODE ────────────────────────────
    if mode == "breakdown":

        wait_message = await update.message.reply_text(
            "🧠 Nexora AI preparing market breakdown..."
        )

        response = await generate_breakdown(message)
        response = clean_text(response)

        await wait_message.edit_text(response)
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

    image_file_id, direction, signal, signal_data = build_signal_response(pair_keyword)

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

            print(f"[AUTO SIGNAL] ✅ {pair_keyword.upper()} posted to {channel_id}")

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

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("approve", approve_user))
    app.add_handler(CommandHandler("reject", reject_user))

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

    # ── SCHEDULE ALL 9 DAILY POSTS ────────────────
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

# ============================================
# RUN BOT
# ============================================

if __name__ == "__main__":
    main()
