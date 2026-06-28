import os
import asyncio
import sqlite3
from datetime import datetime
from typing import Optional

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIG - Render / VPS safe
# =========================
# Required Environment Variables on Render:
# BOT_TOKEN=your_new_telegram_bot_token
# Optional Environment Variables:
# ADMIN_IDS=8908955171,5446536002
# PAYMENT_BINANCE_ID=850566283
# DB_PATH=/var/data/bot.db   # use this only if you attach a Render persistent disk
# FORCE_JOIN_TARGETS=@channel|Title|https://t.me/channel|true;@group|Group Title|https://t.me/group|true

TOKEN = os.getenv("BOT_TOKEN", "").strip()


def parse_admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS") or os.getenv("ADMIN_ID") or "8908955171,5446536002"
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            print(f"Invalid ADMIN_IDS value skipped: {part!r}")
    return ids or {8908955171}


ADMIN_IDS = parse_admin_ids()
ADMIN_ID = next(iter(ADMIN_IDS))


def parse_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_force_join_targets():
    raw = os.getenv("FORCE_JOIN_TARGETS", "").strip()
    targets = []
    if raw:
        # Format: chat|title|url|check;chat|title|url|check
        for item in raw.split(";"):
            parts = [x.strip() for x in item.split("|")]
            if len(parts) < 3:
                continue
            chat, title, url = parts[:3]
            check = parse_bool(parts[3], True) if len(parts) >= 4 else True
            targets.append({"chat": chat, "title": title, "url": url, "check": check})
    if targets:
        return targets
    return [
        {"chat": "@gemini_pixel_vr", "title": "Gemini Pixel VR", "url": "https://t.me/gemini_pixel_vr", "check": True},
        {"chat": "@free_internet_config_bd", "title": "Free Internet Config BD", "url": "https://t.me/free_internet_config_bd", "check": True},
        {"chat": "@gemini_vr_Chat", "title": "Gemini VR Chat", "url": "https://t.me/gemini_vr_Chat", "check": True},
    ]


FORCE_JOIN_TARGETS = parse_force_join_targets()
REF_BONUS = int(os.getenv("REF_BONUS", "1"))
ORDER_COST = int(os.getenv("ORDER_COST", "10"))
PAYMENT_BINANCE_ID = os.getenv("PAYMENT_BINANCE_ID", "850566283")

DB_PATH = os.getenv("DB_PATH", "bot.db")
DB_DIR = os.path.dirname(os.path.abspath(DB_PATH))
if DB_DIR:
    os.makedirs(DB_DIR, exist_ok=True)

conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
conn.execute("PRAGMA busy_timeout = 30000")
conn.execute("PRAGMA journal_mode = WAL")
conn.execute("PRAGMA synchronous = NORMAL")
cur = conn.cursor()

# =========================
# DATABASE
# =========================
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        coins INTEGER DEFAULT 0,
        language TEXT DEFAULT 'en',
        referred_by INTEGER DEFAULT NULL,
        referral_count INTEGER DEFAULT 0,
        joined_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        details TEXT,
        status TEXT,
        result TEXT DEFAULT '',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS payments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        package_coins INTEGER,
        trx_id TEXT UNIQUE,
        screenshot_file_id TEXT DEFAULT '',
        status TEXT DEFAULT 'Pending',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
conn.commit()

cur.execute(
    """
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """
)
conn.commit()


def get_setting(key: str, default: str) -> str:
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str):
    cur.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def get_order_cost() -> int:
    try:
        return int(get_setting("order_cost", str(ORDER_COST)))
    except Exception:
        return ORDER_COST


def get_ref_bonus() -> int:
    try:
        return int(get_setting("ref_bonus", str(REF_BONUS)))
    except Exception:
        return REF_BONUS


def is_referral_enabled() -> bool:
    return get_setting("referral_enabled", "1") == "1"


def has_approved_payment(user_id: int) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM payments WHERE user_id = ? AND status = 'Approved'",
        (user_id,),
    )
    return cur.fetchone()[0] > 0

# Safe migration for older bot.db
for sql in [
    "ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en'",
    "ALTER TABLE users ADD COLUMN referred_by INTEGER DEFAULT NULL",
    "ALTER TABLE users ADD COLUMN referral_count INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN joined_at TEXT DEFAULT CURRENT_TIMESTAMP",
    "ALTER TABLE orders ADD COLUMN result TEXT DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP",
    "ALTER TABLE payments ADD COLUMN screenshot_file_id TEXT DEFAULT ''",
    "ALTER TABLE payments ADD COLUMN created_at TEXT DEFAULT CURRENT_TIMESTAMP",
]:
    try:
        cur.execute(sql)
        conn.commit()
    except Exception:
        pass

# Existing users' saved language is preserved. New users default to English.

# =========================
# LANGUAGE TEXT
# =========================
LANGS = {
    "en": "English",
    "bn": "বাংলা",
    "ur": "اردو",
    "hi": "हिन्दी",
}

T = {
    "choose_lang": {
        "en": "🌐 Please choose your language:",
        "bn": "🌐 আপনার ভাষা নির্বাচন করুন:",
        "ur": "🌐 اپنی زبان منتخب کریں:",
        "hi": "🌐 अपनी भाषा चुनें:",
    },
    "welcome": {
        "en": "Welcome ✅\nSelect an option from the menu.",
        "bn": "স্বাগতম ✅\nMenu থেকে option select করুন।",
        "ur": "خوش آمدید ✅\nMenu سے option منتخب کریں۔",
        "hi": "स्वागत है ✅\nMenu से option select करें।",
    },
    "force_join": {
        "en": "🔒 To use this bot, please join all required channels/groups first, then press Check Joined.",
        "bn": "🔒 Bot ব্যবহার করতে আগে সব required channel/group join করুন, তারপর Check Joined চাপুন।",
        "ur": "🔒 Bot استعمال کرنے کے لیے پہلے تمام required channel/group join کریں، پھر Check Joined دبائیں۔",
        "hi": "🔒 Bot use करने के लिए पहले सभी required channel/group join करें, फिर Check Joined दबाएं।",
    },
    "joined_ok": {"en": "✅ Verified!", "bn": "✅ Verified!", "ur": "✅ Verified!", "hi": "✅ Verified!"},
    "not_joined": {
        "en": "❌ You have not joined yet.",
        "bn": "❌ আপনি এখনো join করেননি।",
        "ur": "❌ آپ نے ابھی join نہیں کیا۔",
        "hi": "❌ आपने अभी join नहीं किया।",
    },
    "balance": {
        "en": "💰 Your balance: {coins} coins",
        "bn": "💰 আপনার balance: {coins} coins",
        "ur": "💰 آپ کا balance: {coins} coins",
        "hi": "💰 आपका balance: {coins} coins",
    },
    "low_balance": {
        "en": "❌ Low balance. Please buy coins first.",
        "bn": "❌ আপনার balance কম। আগে coins কিনুন।",
        "ur": "❌ Balance کم ہے۔ پہلے coins خریدیں۔",
        "hi": "❌ Balance कम है। पहले coins खरीदें।",
    },
    "buy_coins": {
        "en": "💳 Buy Coins\n\n🟡 Binance ID: {binance}\n\nPackages:\n10 Coins = 1 USDT\n50 Coins = 5 USDT\n100 Coins = 10 USDT\n\nNow choose package below.",
        "bn": "💳 Coins কিনতে payment করুন\n\n🟡 Binance ID: {binance}\n\nPackages:\n10 Coins = 1 USDT\n50 Coins = 5 USDT\n100 Coins = 10 USDT\n\nএখন নিচ থেকে package select করুন।",
        "ur": "💳 Coins خریدیں\n\n🟡 Binance ID: {binance}\n\nPackages:\n10 Coins = 1 USDT\n50 Coins = 5 USDT\n100 Coins = 10 USDT\n\nاب نیچے سے package منتخب کریں۔",
        "hi": "💳 Coins खरीदें\n\n🟡 Binance ID: {binance}\n\nPackages:\n10 Coins = 1 USDT\n50 Coins = 5 USDT\n100 Coins = 10 USDT\n\nअब नीचे से package select करें।",
    },
    "send_trx": {
        "en": "✅ Package selected: {coins} coins\n\nNow send only your Order ID.\nScreenshot optional: you can send screenshot after Order ID.",
        "bn": "✅ Package selected: {coins} coins\n\nএখন শুধু Order ID পাঠান।\nScreenshot optional: Order ID দেওয়ার পর screenshot পাঠাতে পারেন।",
        "ur": "✅ Package selected: {coins} coins\n\nاب Order ID بھیجیں۔\nScreenshot optional ہے۔",
        "hi": "✅ Package selected: {coins} coins\n\nअब Order ID भेजें।\nScreenshot optional है।",
    },
    "payment_pending": {
        "en": "✅ Payment request submitted. Admin will verify Order ID.",
        "bn": "✅ Payment request submitted. Admin Order ID verify করবে।",
        "ur": "✅ Payment request submitted. Admin Order ID verify کرے گا۔",
        "hi": "✅ Payment request submitted. Admin Order ID verify करेगा।",
    },
    "new_order_prompt": {
        "en": "📦 New Order\n💰 Cost: {cost} coins\n\nSend all details together:\n📧 Email:\n🔑 Password:\n🔐 2FA Code:",
        "bn": "📦 New Order\n💰 Cost: {cost} coins\n\nনিচের তথ্যগুলো একসাথে পাঠান:\n📧 Email:\n🔑 Password:\n🔐 2FA Code:",
        "ur": "📦 New Order\n💰 Cost: {cost} coins\n\nتمام details ایک ساتھ بھیجیں:\n📧 Email:\n🔑 Password:\n🔐 2FA Code:",
        "hi": "📦 New Order\n💰 Cost: {cost} coins\n\nसारी details एक साथ भेजें:\n📧 Email:\n🔑 Password:\n🔐 2FA Code:",
    },
    "ref": {
        "en": "👥 Your referral link:\n{link}\n\nBonus: {bonus} coins per valid referral.\nTotal referrals: {count}",
        "bn": "👥 আপনার referral link:\n{link}\n\nBonus: প্রতি valid referral এ {bonus} coins।\nTotal referrals: {count}",
        "ur": "👥 آپ کا referral link:\n{link}\n\nBonus: ہر valid referral پر {bonus} coins۔\nTotal referrals: {count}",
        "hi": "👥 आपका referral link:\n{link}\n\nBonus: हर valid referral पर {bonus} coins।\nTotal referrals: {count}",
    },
}

MENU_TEXTS = {
    "balance": {"en": "💰 Balance", "bn": "💰 Balance", "ur": "💰 Balance", "hi": "💰 Balance"},
    "buy": {"en": "💳 Buy Coins", "bn": "💳 Buy Coins", "ur": "💳 Buy Coins", "hi": "💳 Buy Coins"},
    "order": {"en": "📦 New Order", "bn": "📦 New Order", "ur": "📦 New Order", "hi": "📦 New Order"},
    "myorders": {"en": "📜 My Orders", "bn": "📜 My Orders", "ur": "📜 My Orders", "hi": "📜 My Orders"},
    "referral": {"en": "👥 Referral", "bn": "👥 Referral", "ur": "👥 Referral", "hi": "👥 Referral"},
    "language": {"en": "🌐 Language", "bn": "🌐 Language", "ur": "🌐 Language", "hi": "🌐 Language"},
    "admin": {"en": "🛠 Admin Panel", "bn": "🛠 Admin Panel", "ur": "🛠 Admin Panel", "hi": "🛠 Admin Panel"},
}


def tr(key: str, lang: str = "en", **kwargs) -> str:
    lang = lang if lang in LANGS else "en"
    return T[key][lang].format(**kwargs)


def get_lang(user_id: int) -> str:
    cur.execute("SELECT language FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    return (row[0] if row and row[0] else "en")


def main_menu(user_id: int) -> ReplyKeyboardMarkup:
    lang = get_lang(user_id)
    rows = [
        [MENU_TEXTS["balance"][lang], MENU_TEXTS["buy"][lang]],
        [MENU_TEXTS["order"][lang], MENU_TEXTS["myorders"][lang]],
        [MENU_TEXTS["referral"][lang], MENU_TEXTS["language"][lang]],
    ]
    if user_id in ADMIN_IDS:
        rows.append([MENU_TEXTS["admin"][lang]])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def lang_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("English", callback_data="lang_en"), InlineKeyboardButton("বাংলা", callback_data="lang_bn")],
        [InlineKeyboardButton("اردو", callback_data="lang_ur"), InlineKeyboardButton("हिन्दी", callback_data="lang_hi")],
    ])


def force_join_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for target in FORCE_JOIN_TARGETS:
        rows.append([InlineKeyboardButton(f"📢 Join {target['title']}", url=target["url"])])
    rows.append([InlineKeyboardButton("✅ Check Joined", callback_data="check_joined")])
    return InlineKeyboardMarkup(rows)


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Change Order Cost", callback_data="admin_change_cost")],
        [InlineKeyboardButton("📊 All Users Coins", callback_data="admin_allcoins")],
        [InlineKeyboardButton("➕ Add Coin", callback_data="admin_add_coin"), InlineKeyboardButton("➖ Remove Coin", callback_data="admin_remove_coin")],
        [InlineKeyboardButton("🎁 Referral ON/OFF", callback_data="admin_ref_toggle"), InlineKeyboardButton("📤 Export CSV", callback_data="admin_export")],
        [InlineKeyboardButton("📊 Stats", callback_data="admin_stats"), InlineKeyboardButton("💳 Pending Payments", callback_data="admin_payments")],
        [InlineKeyboardButton("📦 Pending Orders", callback_data="admin_orders")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="user_back")],
    ])


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="user_back")]
    ])


def admin_coin_action_keyboard(action: str) -> InlineKeyboardMarkup:
    title = "➕ Add Coin" if action == "add" else "➖ Remove Coin"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(title, callback_data=f"admin_{action}_coin")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]
    ])


def admin_ref_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Referral ON", callback_data="admin_ref_on"), InlineKeyboardButton("❌ Referral OFF", callback_data="admin_ref_off")],
        [InlineKeyboardButton("⬅️ Back", callback_data="admin_back")]
    ])


def hide_reply_keyboard():
    return ReplyKeyboardRemove()


def user_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back", callback_data="user_back")]
    ])


def buy_coins_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Select Coin Package", callback_data="user_buy_packages")],
        [InlineKeyboardButton("⬅️ Back", callback_data="user_back")]
    ])


def order_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 Start New Order", callback_data="user_start_order")],
        [InlineKeyboardButton("⬅️ Back", callback_data="user_back")]
    ])


def referral_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Show Referral Link", callback_data="user_show_referral")],
        [InlineKeyboardButton("⬅️ Back", callback_data="user_back")]
    ])


def language_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("English", callback_data="lang_en"), InlineKeyboardButton("বাংলা", callback_data="lang_bn")],
        [InlineKeyboardButton("اردو", callback_data="lang_ur"), InlineKeyboardButton("हिन्दी", callback_data="lang_hi")],
        [InlineKeyboardButton("⬅️ Back", callback_data="user_back")],
    ])


def payment_package_keyboard() -> InlineKeyboardMarkup:
    amounts = list(range(10, 111, 10))
    rows = []
    for i in range(0, len(amounts), 3):
        rows.append([
            InlineKeyboardButton(f"{coin} Coins", callback_data=f"pkg_{coin}")
            for coin in amounts[i:i+3]
        ])
    return InlineKeyboardMarkup(rows)


def register_user(user, referred_by: Optional[int] = None) -> bool:
    cur.execute("SELECT user_id FROM users WHERE user_id = ?", (user.id,))
    exists = cur.fetchone()
    if exists:
        cur.execute("UPDATE users SET username = ? WHERE user_id = ?", (user.username or "No username", user.id))
        conn.commit()
        return False

    valid_ref = referred_by if referred_by and referred_by != user.id else None
    cur.execute(
        "INSERT INTO users (user_id, username, coins, language, referred_by) VALUES (?, ?, 0, 'en', ?)",
        (user.id, user.username or "No username", valid_ref),
    )
    if valid_ref and is_referral_enabled():
        cur.execute("UPDATE users SET coins = coins + ?, referral_count = referral_count + 1 WHERE user_id = ?", (get_ref_bonus(), valid_ref))
    conn.commit()
    return True


async def is_joined(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True only if user joined every checkable force-join target."""
    if user_id in ADMIN_IDS:
        return True

    for target in FORCE_JOIN_TARGETS:
        if not target.get("check"):
            # Invite-link-only private groups cannot be verified without numeric chat_id.
            continue

        chat_id = target.get("chat")
        if not chat_id:
            continue

        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status not in ("member", "administrator", "creator"):
                return False
        except (BadRequest, Forbidden):
            return False
        except Exception:
            return False

    return True


async def require_join(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    lang = get_lang(user_id)
    if await is_joined(user_id, context):
        return True
    text = tr("force_join", lang)
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=force_join_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=force_join_keyboard())
    return False


async def send_order_result_to_user(order_id: int, result_text: str, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        return False, "Order not found."

    user_id = row[0]
    cur.execute("UPDATE orders SET status = ?, result = ? WHERE id = ?", ("Done", result_text, order_id))
    conn.commit()

    parts = result_text.split("|", 1)
    main_result = parts[0].strip()
    link = parts[1].strip() if len(parts) > 1 else ""

    msg = f"✅ Your Order #{order_id} completed.\n\n📄 Result:\n{main_result}"
    if link:
        msg += f"\n\n🔗 Copy Link:\n{link}"

    await context.bot.send_message(user_id, msg)
    return True, "Result sent."


async def notify_admins(context: ContextTypes.DEFAULT_TYPE, text: str = "", reply_markup=None, photo: str = None, caption: str = None):
    for admin_id in ADMIN_IDS:
        try:
            if photo:
                await context.bot.send_photo(admin_id, photo=photo, caption=caption or text, reply_markup=reply_markup)
            else:
                await context.bot.send_message(admin_id, text, reply_markup=reply_markup)
        except Exception as e:
            print(f"ADMIN NOTIFY ERROR {admin_id}:", repr(e))


def clear_user_waiting(context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "waiting_trx",
        "payment_package",
        "last_payment_id",
        "waiting_payment_screenshot",
        "waiting_order",
    ]:
        context.user_data.pop(key, None)


def clear_admin_waiting(context: ContextTypes.DEFAULT_TYPE):
    for key in [
        "waiting_admin_coin_action",
        "waiting_new_order_cost",
        "waiting_result_order_id",
    ]:
        context.user_data.pop(key, None)


# =========================
# COMMANDS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    referred_by = None
    if context.args:
        try:
            if context.args[0].startswith("ref_"):
                referred_by = int(context.args[0].split("_", 1)[1])
        except Exception:
            referred_by = None

    is_new = register_user(update.effective_user, referred_by)
    lang = get_lang(update.effective_user.id)

    if is_new and referred_by and is_referral_enabled():
        try:
            await context.bot.send_message(referred_by, f"🎉 New referral joined! +{get_ref_bonus()} coins added.")
        except Exception:
            pass

    if not await require_join(update, context):
        return

    await update.message.reply_text(tr("choose_lang", lang), reply_markup=lang_keyboard())
    await update.message.reply_text(tr("welcome", lang), reply_markup=main_menu(update.effective_user.id))


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram ID: {update.effective_user.id}")


async def addcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
    except Exception:
        await update.message.reply_text("Use: /addcoin user_id amount")
        return

    cur.execute("INSERT OR IGNORE INTO users (user_id, username, coins, language) VALUES (?, ?, 0, 'bn')", (user_id, "Unknown"))
    cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    await update.message.reply_text(f"✅ {amount} coins added to {user_id}.")
    try:
        await context.bot.send_message(user_id, f"✅ Your account received {amount} coins.")
    except Exception:
        pass


async def checkcoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("Use: /checkcoin user_id")
        return

    cur.execute("SELECT username, coins FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ User not found.")
        return

    username, coins = row
    await update.message.reply_text(f"👤 User ID: {user_id}\nUsername: @{username}\n💰 Coins: {coins}")


async def allcoins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        page = int(context.args[0]) if context.args else 1
        if page < 1:
            page = 1
    except Exception:
        page = 1

    per_page = 30
    offset = (page - 1) * per_page

    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(coins), 0) FROM users")
    total_coins = cur.fetchone()[0]

    cur.execute(
        "SELECT user_id, username, coins FROM users ORDER BY coins DESC, joined_at DESC LIMIT ? OFFSET ?",
        (per_page, offset),
    )
    rows = cur.fetchall()

    if not rows:
        await update.message.reply_text("No users found.")
        return

    total_pages = (total_users + per_page - 1) // per_page

    msg = f"👥 All Users Coin List\nPage: {page}/{total_pages}\nTotal Users: {total_users}\nTotal Coins: {total_coins}\n\n"

    for i, (uid, username, coins) in enumerate(rows, start=offset + 1):
        uname = f"@{username}" if username and username != "No username" else "No username"
        msg += f"{i}. {uid} | {uname} | {coins} coins\n"

    if total_pages > 1:
        if page < total_pages:
            msg += f"\nUse: /allcoins {page + 1} for next page"
        else:
            msg += "\nEnd of list."

    if len(msg) > 3900:
        msg = msg[:3900] + "\n\nMessage too long. Use page system: /allcoins 2"

    await update.message.reply_text(msg)


async def setcost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        amount = int(context.args[0])
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Use: /setcost amount\nExample: /setcost 15")
        return

    set_setting("order_cost", str(amount))
    await update.message.reply_text(f"✅ Order cost updated to {amount} coins.")


async def getcost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(f"💰 Current Order Cost: {get_order_cost()} coins")


async def setref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        amount = int(context.args[0])
        if amount < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Use: /setref amount\nExample: /setref 1")
        return

    set_setting("ref_bonus", str(amount))
    await update.message.reply_text(f"✅ Referral bonus updated to {amount} coins.")


async def getref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(f"🎁 Current Referral Bonus: {get_ref_bonus()} coins")


async def refon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    set_setting("referral_enabled", "1")
    await update.message.reply_text("✅ Referral system ON.")


async def refoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    set_setting("referral_enabled", "0")
    await update.message.reply_text("✅ Referral system OFF.")


async def setusercoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if amount < 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Use: /setusercoin user_id amount")
        return

    cur.execute("INSERT OR IGNORE INTO users (user_id, username, coins, language) VALUES (?, ?, 0, 'en')", (user_id, "Unknown"))
    cur.execute("UPDATE users SET coins = ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    await update.message.reply_text(f"✅ User {user_id} coin balance set to {amount}.")


async def exportusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        import csv
        from pathlib import Path
        filename = "users_coin_export.csv"
        path = Path(filename)

        cur.execute("SELECT user_id, username, coins, referral_count, referred_by, joined_at FROM users ORDER BY coins DESC")
        rows = cur.fetchall()

        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["user_id", "username", "coins", "referral_count", "referred_by", "joined_at"])
            writer.writerows(rows)

        with path.open("rb") as f:
            await update.message.reply_document(document=f, filename=filename, caption="📊 Users coin export")
    except Exception as e:
        await update.message.reply_text(f"❌ Export failed: {e}")


async def removecoin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text("Use: /removecoin user_id amount")
        return

    cur.execute("SELECT coins FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text("❌ User not found.")
        return

    current = row[0]
    new_balance = max(0, current - amount)
    cur.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_balance, user_id))
    conn.commit()

    await update.message.reply_text(f"✅ Removed {amount} coins from {user_id}.\nOld: {current}\nNew: {new_balance}")
    try:
        await context.bot.send_message(user_id, f"⚠️ {amount} coins removed by admin.\nCurrent balance: {new_balance} coins.")
    except Exception:
        pass


async def result(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    try:
        order_id = int(context.args[0])
        result_text = " ".join(context.args[1:]).strip()
        if not result_text:
            raise ValueError
    except Exception:
        await update.message.reply_text("Use: /result order_id result_text|link\nExample: /result 5 Success|https://example.com")
        return

    ok, message = await send_order_result_to_user(order_id, result_text, context)
    await update.message.reply_text(f"✅ Result sent to Order #{order_id}" if ok else f"❌ {message}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    msg = " ".join(context.args).strip()
    if not msg:
        await update.message.reply_text("Use: /broadcast message")
        return
    cur.execute("SELECT user_id FROM users")
    users = [r[0] for r in cur.fetchall()]
    ok = fail = 0
    for uid in users:
        try:
            await context.bot.send_message(uid, msg)
            ok += 1
        except Exception:
            fail += 1
    await update.message.reply_text(f"✅ Broadcast done. Sent: {ok}, Failed: {fail}")


async def pendingpayments(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    cur.execute("SELECT id, user_id, username, package_coins, trx_id FROM payments WHERE status = 'Pending' ORDER BY id DESC LIMIT 20")
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("No pending payments.")
        return

    for pid, uid, uname, coins, trx in rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Approve {coins}", callback_data=f"payapprove_{pid}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"payreject_{pid}")]
        ])
        await update.message.reply_text(
            f"💳 Payment #{pid}\nUser: {uid} @{uname}\nPackage: {coins} coins\nOrder ID: {trx}",
            reply_markup=kb
        )


# =========================
# MESSAGE HANDLERS
# =========================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    register_user(user)
    lang = get_lang(user.id)
    main_menu_texts_current_lang = {v[lang] for v in MENU_TEXTS.values()}

    # Admin is adding/removing coins from Admin Panel
    if user.id in ADMIN_IDS and context.user_data.get("waiting_admin_coin_action"):
        admin_text = MENU_TEXTS["admin"].get(lang, "🛠 Admin Panel")
        if text == admin_text:
            clear_admin_waiting(context)
            await update.message.reply_text("🔽 Menu hidden. Use buttons below.", reply_markup=hide_reply_keyboard())
            await update.message.reply_text("🛠 Admin Panel", reply_markup=admin_panel_keyboard())
            return

        action_type = context.user_data.get("waiting_admin_coin_action")
        try:
            target_user_id, amount = map(int, text.split()[:2])
            if amount <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_text(
                "❌ Invalid format. Send only like this:\n"
                "user_id amount\n\n"
                "Example:\n"
                "123456789 10",
                reply_markup=admin_back_keyboard(),
            )
            return

        cur.execute("INSERT OR IGNORE INTO users (user_id, username, coins, language) VALUES (?, ?, 0, 'en')", (target_user_id, "Unknown"))

        if action_type == "add":
            cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (amount, target_user_id))
            msg = f"✅ Added {amount} coins to {target_user_id}."
        else:
            cur.execute("SELECT coins FROM users WHERE user_id = ?", (target_user_id,))
            row = cur.fetchone()
            current = row[0] if row else 0
            new_balance = max(0, current - amount)
            cur.execute("UPDATE users SET coins = ? WHERE user_id = ?", (new_balance, target_user_id))
            msg = f"✅ Removed {amount} coins from {target_user_id}. New balance: {new_balance}."

        conn.commit()
        clear_admin_waiting(context)
        await update.message.reply_text(msg, reply_markup=admin_panel_keyboard())
        return

    # Admin is changing order cost from Admin Panel
    if user.id in ADMIN_IDS and context.user_data.get("waiting_new_order_cost"):
        try:
            new_cost = int(text.strip())
            if new_cost <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_text("❌ Invalid amount. Send only number. Example: 15", reply_markup=admin_back_keyboard())
            return
        set_setting("order_cost", str(new_cost))
        clear_admin_waiting(context)
        await update.message.reply_text(f"✅ Order cost updated to {new_cost} coins.", reply_markup=admin_panel_keyboard())
        return

    # Admin is sending result/link after pressing 🔗 Send Result/Link
    if user.id in ADMIN_IDS and context.user_data.get("waiting_result_order_id"):
        order_id = int(context.user_data["waiting_result_order_id"])
        result_text = text.strip()
        if not result_text:
            await update.message.reply_text("Send result text. Optional link format: Result text|https://link.com")
            return
        ok, message = await send_order_result_to_user(order_id, result_text, context)
        context.user_data["waiting_result_order_id"] = None
        await update.message.reply_text(f"✅ Result/link sent to Order #{order_id}" if ok else f"❌ {message}")
        return

    if not await require_join(update, context):
        return

    # Payment Order ID waiting
    if context.user_data.get("waiting_trx"):
        if text in main_menu_texts_current_lang:
            clear_user_waiting(context)
            await update.message.reply_text("✅ Cancelled. Choose an option from menu.", reply_markup=main_menu(user.id))
            return
        package_coins = int(context.user_data.get("payment_package", 0))
        trx_id = text.strip()
        if len(trx_id) < 4:
            await update.message.reply_text("❌ Order ID is too short. Please send valid ID.")
            return
        try:
            cur.execute(
                "INSERT INTO payments (user_id, username, package_coins, trx_id, status) VALUES (?, ?, ?, ?, 'Pending')",
                (user.id, user.username or "No username", package_coins, trx_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ This Order ID already submitted.")
            return

        payment_id = cur.lastrowid
        context.user_data["last_payment_id"] = payment_id
        context.user_data["waiting_trx"] = False
        context.user_data["waiting_payment_screenshot"] = True

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Approve {package_coins}", callback_data=f"payapprove_{payment_id}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"payreject_{payment_id}")],
        ])
        await update.message.reply_text(tr("payment_pending", lang) + "\n\n📸 Screenshot optional: send now if you want.")

        admin_msg = (
            f"💳 New Payment Request #{payment_id}\n"
            f"User ID: {user.id}\n"
            f"Username: @{user.username or 'No username'}\n"
            f"Package: {package_coins} coins\n"
            f"Order ID: {trx_id}\n"
            f"Status: Pending"
        )
        try:
            await notify_admins(context, admin_msg, reply_markup=buttons)
        except Exception as e:
            print("ADMIN PAYMENT NOTIFY ERROR:", repr(e))
            await update.message.reply_text("⚠️ Payment saved, but admin notification failed. Please contact admin.")

        return

    # New order waiting
    if context.user_data.get("waiting_order"):
        if text in main_menu_texts_current_lang:
            clear_user_waiting(context)
            await update.message.reply_text("✅ Cancelled. Choose an option from menu.", reply_markup=main_menu(user.id))
            return
        current_cost = get_order_cost()

        # First order lock: user must have at least one approved payment before using referral coins
        if not has_approved_payment(user.id):
            await update.message.reply_text(
                "⚠️ First order করার আগে আপনাকে একবার payment করতে হবে.\n\n"
                "Please buy coins first. Referral coins can be used after your first approved payment."
            )
            context.user_data["waiting_order"] = False
            return

        cur.execute("SELECT coins FROM users WHERE user_id = ?", (user.id,))
        coins = cur.fetchone()[0]
        if coins < current_cost:
            await update.message.reply_text(tr("low_balance", lang))
            context.user_data["waiting_order"] = False
            return

        cur.execute("UPDATE users SET coins = coins - ? WHERE user_id = ?", (current_cost, user.id))
        cur.execute(
            "INSERT INTO orders (user_id, username, details, status) VALUES (?, ?, ?, ?)",
            (user.id, user.username or "No username", text, "Pending"),
        )
        conn.commit()
        order_id = cur.lastrowid
        context.user_data["waiting_order"] = False

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Send Result/Link", callback_data=f"sendresult_{order_id}")],
            [InlineKeyboardButton("✅ Done Only", callback_data=f"done_{order_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_order_{order_id}")]
        ])
        await update.message.reply_text(f"✅ Order placed!\nOrder ID: {order_id}\nStatus: Pending")
        await notify_admins(
            context,
            f"📦 New Order #{order_id}\nUser ID: {user.id}\nUsername: @{user.username}\nCost: {current_cost} coins\n\nDetails:\n{text}",
            reply_markup=buttons,
        )
        return

    all_menu_values = {v[lang]: k for k, v in MENU_TEXTS.items()}
    action = all_menu_values.get(text)

    if action == "balance":
        cur.execute("SELECT coins FROM users WHERE user_id = ?", (user.id,))
        coins = cur.fetchone()[0]
        await update.message.reply_text(tr("balance", lang, coins=coins))

    elif action == "buy":
        clear_user_waiting(context)
        await update.message.reply_text("🔽 Menu hidden. Use buttons below.", reply_markup=hide_reply_keyboard())
        await update.message.reply_text(
            tr("buy_coins", lang, binance=PAYMENT_BINANCE_ID),
            reply_markup=buy_coins_menu_keyboard()
        )

    elif action == "order":
        clear_user_waiting(context)
        await update.message.reply_text("🔽 Menu hidden. Use buttons below.", reply_markup=hide_reply_keyboard())
        await update.message.reply_text(
            f"📦 New Order\n💰 Current Cost: {get_order_cost()} coins\n\nPress Start New Order to continue.",
            reply_markup=order_menu_keyboard()
        )

    elif action == "myorders":
        cur.execute("SELECT id, status, result FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user.id,))
        orders = cur.fetchall()
        if not orders:
            await update.message.reply_text("No orders found.")
        else:
            msg = "📜 Your Orders:\n\n"
            for oid, status, res in orders:
                msg += f"Order #{oid} - {status}\n"
                if res:
                    msg += f"Result: {res}\n"
                msg += "\n"
            await update.message.reply_text(msg)

    elif action == "referral":
        clear_user_waiting(context)
        await update.message.reply_text("🔽 Menu hidden. Use buttons below.", reply_markup=hide_reply_keyboard())
        await update.message.reply_text(
            "👥 Referral Center\n\nPress the button below to see your referral link.",
            reply_markup=referral_menu_keyboard()
        )

    elif action == "language":
        clear_user_waiting(context)
        await update.message.reply_text("🔽 Menu hidden. Use buttons below.", reply_markup=hide_reply_keyboard())
        await update.message.reply_text(tr("choose_lang", lang), reply_markup=language_menu_keyboard())

    elif action == "admin" and user.id in ADMIN_IDS:
        clear_admin_waiting(context)
        await update.message.reply_text("🛠 Admin Panel", reply_markup=admin_panel_keyboard())

    else:
        await update.message.reply_text("Please choose an option from menu.", reply_markup=main_menu(user.id))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    register_user(user)
    lang = get_lang(user.id)

    if not await require_join(update, context):
        return

    file_id = update.message.photo[-1].file_id
    payment_id = context.user_data.get("last_payment_id")

    if context.user_data.get("waiting_payment_screenshot") and payment_id:
        cur.execute("UPDATE payments SET screenshot_file_id = ? WHERE id = ? AND user_id = ?", (file_id, payment_id, user.id))
        conn.commit()
        cur.execute("SELECT package_coins, trx_id FROM payments WHERE id = ?", (payment_id,))
        row = cur.fetchone()
        coins, trx_id = row if row else (0, "Unknown")
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"✅ Approve {coins}", callback_data=f"payapprove_{payment_id}")],
            [InlineKeyboardButton("❌ Reject", callback_data=f"payreject_{payment_id}")],
        ])
        await update.message.reply_text("✅ Screenshot received. Admin will verify.")
        await notify_admins(
            context,
            photo=file_id,
            caption=f"💳 Payment Screenshot #{payment_id}\nUser ID: {user.id}\nUsername: @{user.username}\nPackage: {coins} coins\nOrder ID: {trx_id}",
            reply_markup=buttons,
        )
        context.user_data["waiting_payment_screenshot"] = False
        return

    await update.message.reply_text("📸 Screenshot received, but please select Buy Coins package and submit Order ID first.")

# =========================
# CALLBACKS
# =========================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data.startswith("lang_"):
        lang = data.split("_", 1)[1]
        if lang in LANGS:
            cur.execute("UPDATE users SET language = ? WHERE user_id = ?", (lang, user_id))
            conn.commit()
            await query.message.reply_text(f"✅ Language set: {LANGS[lang]}", reply_markup=main_menu(user_id))
        return

    if data == "check_joined":
        lang = get_lang(user_id)
        if await is_joined(user_id, context):
            await query.message.reply_text(tr("joined_ok", lang), reply_markup=main_menu(user_id))
        else:
            await query.message.reply_text(tr("not_joined", lang), reply_markup=force_join_keyboard())
        return

    if data.startswith("pkg_"):
        if not await require_join(update, context):
            return
        coins = int(data.split("_", 1)[1])
        context.user_data["payment_package"] = coins
        context.user_data["waiting_trx"] = True
        lang = get_lang(user_id)
        await query.message.reply_text(tr("send_trx", lang, coins=coins), reply_markup=user_back_keyboard())
        return

    if data.startswith("sendresult_"):
        if user_id not in ADMIN_IDS:
            await query.message.reply_text("আপনি admin নন।")
            return
        order_id = int(data.split("_", 1)[1])
        context.user_data["waiting_result_order_id"] = order_id
        await query.message.reply_text(
            f"🔗 Send result/link for Order #{order_id}\n\n"
            "Format:\n"
            "Result text|https://your-link.com\n\n"
            "Example:\n"
            "Success|https://example.com"
        )
        return

    if data == "user_back":
        clear_user_waiting(context)
        clear_admin_waiting(context)
        await query.message.reply_text("✅ Main menu opened.", reply_markup=main_menu(user_id))
        return

    if data == "user_buy_packages":
        if not await require_join(update, context):
            return
        await query.message.reply_text("💰 Choose coin package:", reply_markup=payment_package_keyboard())
        return

    if data == "user_start_order":
        if not await require_join(update, context):
            return
        lang = get_lang(user_id)
        await query.message.reply_text(tr("new_order_prompt", lang, cost=get_order_cost()), reply_markup=user_back_keyboard())
        context.user_data["waiting_order"] = True
        return

    if data == "user_show_referral":
        if not await require_join(update, context):
            return
        bot_username = (await context.bot.get_me()).username
        cur.execute("SELECT referral_count FROM users WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        count = row[0] if row else 0
        link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        lang = get_lang(user_id)
        await query.message.reply_text(tr("ref", lang, link=link, bonus=get_ref_bonus(), count=count), reply_markup=user_back_keyboard())
        return

    # Admin-only callbacks below
    if user_id not in ADMIN_IDS:
        await query.message.reply_text("আপনি admin নন।")
        return

    if data == "admin_back":
        clear_admin_waiting(context)
        await query.message.reply_text("🛠 Admin Panel", reply_markup=admin_panel_keyboard())
        return


    if data == "admin_change_cost":
        clear_admin_waiting(context)
        context.user_data["waiting_new_order_cost"] = True
        await query.message.reply_text(
            f"💰 Change Order Cost\n\nCurrent Order Cost: {get_order_cost()} coins\n\n"
            "Send new order cost amount.\nExample: 15",
            reply_markup=admin_back_keyboard()
        )
        return

    if data == "admin_allcoins":
        fake_update = update
        # Reuse /allcoins logic by sending page 1 list directly
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        cur.execute("SELECT COALESCE(SUM(coins), 0) FROM users")
        total_coins = cur.fetchone()[0]
        cur.execute("SELECT user_id, username, coins FROM users ORDER BY coins DESC, joined_at DESC LIMIT 30")
        rows = cur.fetchall()
        if not rows:
            await query.message.reply_text("No users found.")
            return
        msg = f"👥 All Users Coin List\nPage: 1\nTotal Users: {total_users}\nTotal Coins: {total_coins}\n\n"
        for i, (uid, username, coins) in enumerate(rows, start=1):
            uname = f"@{username}" if username and username != "No username" else "No username"
            msg += f"{i}. {uid} | {uname} | {coins} coins\n"
        if total_users > 30:
            msg += "\nUse /allcoins 2 for next page."
        await query.message.reply_text(msg)
        return

    if data == "admin_add_coin":
        clear_admin_waiting(context)
        context.user_data["waiting_admin_coin_action"] = "add"
        await query.message.reply_text(
            "➕ Add Coin\n\nSend user ID and coin amount.\n\nExample:\n123456789 20",
            reply_markup=admin_back_keyboard()
        )
        return

    if data == "admin_remove_coin":
        clear_admin_waiting(context)
        context.user_data["waiting_admin_coin_action"] = "remove"
        await query.message.reply_text(
            "➖ Remove Coin\n\nSend user ID and coin amount.\n\nExample:\n123456789 10",
            reply_markup=admin_back_keyboard()
        )
        return

    if data == "admin_ref_toggle":
        clear_admin_waiting(context)
        current = "ON" if is_referral_enabled() else "OFF"
        await query.message.reply_text(f"🎁 Referral system is currently: {current}", reply_markup=admin_ref_keyboard())
        return

    if data == "admin_ref_on":
        set_setting("referral_enabled", "1")
        await query.message.reply_text("✅ Referral system ON.", reply_markup=admin_panel_keyboard())
        return

    if data == "admin_ref_off":
        set_setting("referral_enabled", "0")
        await query.message.reply_text("✅ Referral system OFF.", reply_markup=admin_panel_keyboard())
        return

    if data == "admin_export":
        await query.message.reply_text("📤 Use /exportusers to export all users coin list as CSV.")
        return

    if data == "admin_stats":
        cur.execute("SELECT COUNT(*), COALESCE(SUM(coins), 0), COALESCE(SUM(referral_count), 0) FROM users")
        total_users, total_coins, total_refs = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM orders WHERE status = 'Pending'")
        pending_orders = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM payments WHERE status = 'Pending'")
        pending_payments = cur.fetchone()[0]
        await query.message.reply_text(
            f"📊 Bot Stats\nUsers: {total_users}\nTotal Coins: {total_coins}\nTotal Referrals: {total_refs}\nPending Orders: {pending_orders}\nPending Payments: {pending_payments}"
        )
        return

    if data == "admin_payments":
        cur.execute("SELECT id, user_id, username, package_coins, trx_id FROM payments WHERE status = 'Pending' ORDER BY id DESC LIMIT 10")
        rows = cur.fetchall()
        if not rows:
            await query.message.reply_text("No pending payments.")
        for pid, uid, uname, coins, trx in rows:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Approve {coins}", callback_data=f"payapprove_{pid}"), InlineKeyboardButton("❌ Reject", callback_data=f"payreject_{pid}")]])
            await query.message.reply_text(f"💳 Payment #{pid}\nUser: {uid} @{uname}\nPackage: {coins}\nOrder ID: {trx}", reply_markup=kb)
        return

    if data == "admin_orders":
        cur.execute("SELECT id, user_id, username, details FROM orders WHERE status = 'Pending' ORDER BY id DESC LIMIT 10")
        rows = cur.fetchall()
        if not rows:
            await query.message.reply_text("No pending orders.")
        for oid, uid, uname, details in rows:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 Send Result/Link", callback_data=f"sendresult_{oid}")],
                [InlineKeyboardButton("✅ Done Only", callback_data=f"done_{oid}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_order_{oid}")]
            ])
            await query.message.reply_text(f"📦 Order #{oid}\nUser: {uid} @{uname}\nDetails:\n{details}", reply_markup=kb)
        return

    if data.startswith("payapprove_"):
        payment_id = int(data.split("_", 1)[1])
        cur.execute("SELECT user_id, package_coins, status FROM payments WHERE id = ?", (payment_id,))
        row = cur.fetchone()
        if not row:
            await query.message.reply_text("Payment not found.")
            return
        uid, coins, status = row
        if status != "Pending":
            await query.message.reply_text(f"Already {status}.")
            return
        cur.execute("UPDATE payments SET status = 'Approved' WHERE id = ?", (payment_id,))
        cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (coins, uid))
        conn.commit()
        await context.bot.send_message(uid, f"✅ Payment approved!\n{coins} coins added.")
        await query.message.reply_text(f"✅ Payment #{payment_id} approved. {coins} coins added to {uid}.")
        return

    if data.startswith("payreject_"):
        payment_id = int(data.split("_", 1)[1])
        cur.execute("SELECT user_id, status FROM payments WHERE id = ?", (payment_id,))
        row = cur.fetchone()
        if not row:
            await query.message.reply_text("Payment not found.")
            return
        uid, status = row
        if status != "Pending":
            await query.message.reply_text(f"Already {status}.")
            return
        cur.execute("UPDATE payments SET status = 'Rejected' WHERE id = ?", (payment_id,))
        conn.commit()
        await context.bot.send_message(uid, "❌ Payment rejected.")
        await query.message.reply_text(f"❌ Payment #{payment_id} rejected.")
        return

    if data.startswith("done_"):
        order_id = int(data.split("_")[1])
        cur.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            await query.message.reply_text("Order not found.")
            return
        uid = row[0]
        cur.execute("UPDATE orders SET status = ? WHERE id = ?", ("Done", order_id))
        conn.commit()
        await context.bot.send_message(uid, f"✅ Your Order #{order_id} completed.")
        await query.message.reply_text(f"✅ Order #{order_id} marked as Done.")
        return

    if data.startswith("reject_order_"):
        order_id = int(data.split("_")[2])
        cur.execute("SELECT user_id FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            await query.message.reply_text("Order not found.")
            return
        uid = row[0]
        cur.execute("UPDATE orders SET status = ? WHERE id = ?", ("Rejected", order_id))
        cur.execute("UPDATE users SET coins = coins + ? WHERE user_id = ?", (get_order_cost(), uid))
        conn.commit()
        await context.bot.send_message(uid, f"❌ Your Order #{order_id} rejected.\n{get_order_cost()} coins refunded.")
        await query.message.reply_text(f"❌ Order #{order_id} rejected and coins refunded.")
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("ERROR:", context.error)


def main():
    if not TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing. Set BOT_TOKEN in Render Environment Variables.")

    app = (
        Application.builder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(30)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("addcoin", addcoin))
    app.add_handler(CommandHandler("checkcoin", checkcoin))
    app.add_handler(CommandHandler("allcoins", allcoins))
    app.add_handler(CommandHandler("setcost", setcost))
    app.add_handler(CommandHandler("getcost", getcost))
    app.add_handler(CommandHandler("setref", setref))
    app.add_handler(CommandHandler("getref", getref))
    app.add_handler(CommandHandler("removecoin", removecoin))
    app.add_handler(CommandHandler("exportusers", exportusers))
    app.add_handler(CommandHandler("setusercoin", setusercoin))
    app.add_handler(CommandHandler("refoff", refoff))
    app.add_handler(CommandHandler("refon", refon))
    app.add_handler(CommandHandler("result", result))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("pendingpayments", pendingpayments))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_error_handler(error_handler)

    print(f"Custom Bot Started ADMIN PANEL... ADMIN_IDS={ADMIN_IDS}, REF_BONUS={get_ref_bonus()}, ORDER_COST={get_order_cost()}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    # Python 3.14/3.15 compatibility fix for python-telegram-bot run_polling()
    # Some new Python versions do not create a default event loop automatically.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
