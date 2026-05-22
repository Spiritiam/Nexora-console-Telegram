# 🤖 AI Trading Signals Bot — Setup Guide

## What You Need
- A computer or a cheap VPS server (to keep it running 24/7)
- Python 3.10 or newer installed

---

## Step 1 — Install Python (if not installed)
Download from: https://www.python.org/downloads/
✅ During install, check "Add Python to PATH"

---

## Step 2 — Install Required Libraries
Open Terminal (Mac/Linux) or Command Prompt (Windows) and run:

```
pip install python-telegram-bot httpx
```

---

## Step 3 — Run the Bot
1. Save the file `trading_bot.py` to your computer
2. Open Terminal / Command Prompt in that folder
3. Run:

```
python trading_bot.py
```

You should see:
```
🤖 Trading Signals Bot is LIVE! Press Ctrl+C to stop.
```

4. Open Telegram, search for your bot, and send /start

---

## Step 4 — Keep it Running 24/7 (Important!)
Your bot stops when you close your computer. To keep it always online, use a FREE cloud server:

### Option A — Railway.app (Recommended, Free)
1. Go to https://railway.app
2. Sign up with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Upload your bot file and deploy
5. It runs 24/7 for free

### Option B — Render.com (Free)
1. Go to https://render.com
2. Create a new "Background Worker"
3. Upload trading_bot.py
4. Set Start Command: `python trading_bot.py`

### Option C — Run on your PC always-on
Use a process manager like PM2 or simply leave the terminal open.

---

## Bot Commands Available
| Command | What it does |
|---------|-------------|
| /start  | Welcome message |
| /help   | Help menu |
| /gold   | Instant Gold (XAUUSD) analysis |
| /forex  | Best Forex pair signal |
| /crypto | Best Crypto signal |

Plus — users can:
- Type ANY question in plain text
- Send ANY chart screenshot for full analysis

---

## Free Usage Limits (Gemini)
- 1,500 requests per day
- 15 requests per minute
- Completely FREE — no credit card needed

---

## Troubleshooting
- **Bot not responding?** Make sure the terminal is still running
- **Error about packages?** Re-run: `pip install python-telegram-bot httpx`
- **Image not analyzing?** Make sure you send it as a photo, not a file

---

## Your Keys (Already in the bot file)
- Telegram Token: ✅ configured
- Gemini API Key: ✅ configured

---

🚀 You're all set! Your AI trading signals bot is ready to serve your community.
