Coin Bot v2.0 Render Foundation - Setup

Files included:
1) coin_bot_v2_render.py      -> Main bot file for Render/VPS
2) requirements.txt           -> Python dependency list
3) Procfile                   -> Render start command
4) .env.example               -> Environment variable template

Render deploy steps:
1) Upload these files to a GitHub repo.
2) On Render, create a Background Worker service.
3) Build command: pip install -r requirements.txt
4) Start command: python coin_bot_v2_render.py
5) Add Environment Variables:
   BOT_TOKEN=your_new_telegram_bot_token
   ADMIN_IDS=8908955171,5446536002
   PAYMENT_BINANCE_ID=850566283
   ORDER_COST=10
   REF_BONUS=1
   DB_PATH=bot.db

Important database note:
- Without a persistent disk, Render may reset bot.db when the service redeploys/restarts.
- For production, attach a persistent disk and set DB_PATH=/var/data/bot.db or your disk path.

Force Join format:
FORCE_JOIN_TARGETS=@channel|Title|https://t.me/channel|true;@group|Group Title|https://t.me/group|true

Private channel/group note:
Telegram cannot verify private invite links directly. Add the bot to the private channel/group and use the numeric chat ID in FORCE_JOIN_TARGETS.

Security note:
The old uploaded file contained a hardcoded bot token. Revoke that token from BotFather and use a fresh token in Render environment variables only.
