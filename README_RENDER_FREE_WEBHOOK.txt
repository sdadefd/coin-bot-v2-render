Coin Bot v2.1 - Render Free Web Service Setup
=============================================

Use this version when Render Background Worker asks for card/payment.
This version runs as a Web Service using Telegram webhook.

Upload these files to GitHub:
1) coin_bot_v2_1_render_free_webhook.py
2) requirements.txt   (use the webhook requirements content)
3) Procfile           (use the webhook Procfile content)
4) .env.example
5) README_RENDER_FREE_WEBHOOK.txt

Render settings:
- New -> Web Service
- Runtime: Python 3
- Branch: main
- Root Directory: empty
- Build Command: pip install -r requirements.txt
- Start Command: python coin_bot_v2_1_render_free_webhook.py

Environment Variables:
BOT_TOKEN=your_new_telegram_bot_token
ADMIN_IDS=8908955171,5446536002
PAYMENT_BINANCE_ID=850566283
DB_PATH=bot.db
WEBHOOK_PATH=telegram-webhook

After creating Web Service, Render will give a URL like:
https://coin-bot-v2-render.onrender.com

Then add this Environment Variable:
WEBHOOK_URL=https://coin-bot-v2-render.onrender.com/telegram-webhook

Then click Manual Deploy -> Deploy latest commit.

Important:
- Stop local CMD bot before Render deploy.
- Do not upload bot.db, bot.db-shm, bot.db-wal.
- Do not upload real .env with BOT_TOKEN.
