"""
Telegram bot for V2IpLimit — monitoring only.

Commands:
    /start      — show help
    /status     — show currently active connections
    /add_admin  — add a bot admin by chat ID
    /remove_admin — remove an admin
    /admins_list  — list all admins
    /backup     — send config.json as a file
    /setup      — (re)configure panel domain, username, password
"""

import asyncio
import os
import sys
from collections import Counter

try:
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        ConversationHandler,
        MessageHandler,
        filters,
    )
except ImportError:
    print(
        "Module 'python-telegram-bot' is not installed. "
        "Run: pip install python-telegram-bot"
    )
    sys.exit()

from telegram_bot.utils import (
    add_admin_to_config,
    add_base_information,
    check_admin,
    read_json_file,
    remove_admin_from_config,
    write_country_code_json,
)
from utils.read_config import read_config

# ── Conversation states ─────────────────────────────────────────────────────

(
    GET_DOMAIN,
    GET_USERNAME,
    GET_PASSWORD,
    GET_CONFIRMATION,
    GET_CHAT_ID,
    GET_CHAT_ID_TO_REMOVE,
    SET_COUNTRY_CODE,
) = range(7)

# ── Bot setup ────────────────────────────────────────────────────────────────

data = asyncio.run(read_config())
bot_token = data.get("BOT_TOKEN") or ""

# Telegram monitoring is optional. With no BOT_TOKEN the service runs headless
# (device counter + IP enforcement only). A no-op stub keeps the module-level
# `application.add_handler(...)` / `application.bot.sendMessage(...)` calls inert.
TELEGRAM_ENABLED = bool(bot_token)


class _NoopApplication:
    """Inert stand-in for telegram Application when the bot is disabled."""

    class _Bot:
        async def sendMessage(self, *args, **kwargs):  # noqa: N802
            return None

    bot = _Bot()

    def add_handler(self, *args, **kwargs):
        return None


if TELEGRAM_ENABLED:
    application = ApplicationBuilder().token(bot_token).build()
else:
    application = _NoopApplication()

# ── Help text ────────────────────────────────────────────────────────────────

START_MESSAGE = (
    "🔭 <b>V2IpLimit Monitor</b>\n\n"
    "<b>/status</b>\n"
    "<code>Show currently active connections and IPs</code>\n\n"
    "<b>/setup</b>\n"
    "<code>Configure panel domain, username and password</code>\n\n"
    "<b>/country_code</b>\n"
    "<code>Set country filter for IP accuracy</code>\n\n"
    "<b>/add_admin</b>\n"
    "<code>Grant bot access to another chat ID</code>\n\n"
    "<b>/admins_list</b>\n"
    "<code>Show all current bot admins</code>\n\n"
    "<b>/remove_admin</b>\n"
    "<code>Revoke a bot admin's access</code>\n\n"
    "<b>/backup</b>\n"
    "<code>Download config.json</code>"
)

# ── Helpers ──────────────────────────────────────────────────────────────────


async def check_admin_privilege(update: Update):
    """Return ConversationHandler.END if the caller is not an admin, else None."""
    admins = await check_admin()
    if not admins:
        await add_admin_to_config(update.effective_chat.id)
    admins = await check_admin()
    if update.effective_chat.id not in admins:
        await update.message.reply_html(
            "⛔ Sorry, you don't have permission to use this bot."
        )
        return ConversationHandler.END
    return None


# ── /start ───────────────────────────────────────────────────────────────────


async def start(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Show the command list."""
    check = await check_admin_privilege(update)
    if check is not None:
        return check
    await update.message.reply_html(START_MESSAGE)


# ── /status ──────────────────────────────────────────────────────────────────


async def status(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Show a live snapshot of active connections from ACTIVE_USERS."""
    check = await check_admin_privilege(update)
    if check is not None:
        return check

    # Import here to avoid circular imports at module level
    from utils.check_usage import ACTIVE_USERS  # pylint: disable=import-outside-toplevel

    if not ACTIVE_USERS:
        await update.message.reply_html("📊 No active connections right now.")
        return

    import time as _time
    lines = []
    total = 0
    _cutoff = _time.monotonic() - 60
    for username, user_data in sorted(
        ACTIVE_USERS.items(), key=lambda x: len(x[1].ips), reverse=True
    ):
        ips = [ip for ip, ts in user_data.ips.items() if ts >= _cutoff]
        if ips:
            ip_list = "\n  • ".join(f"<code>{ip}</code>" for ip in ips)
            lines.append(
                f"👤 <code>{username}</code> — <b>{len(ips)}</b> IP(s)\n  • {ip_list}"
            )
            total += len(ips)

    if not lines:
        await update.message.reply_html("📊 No active connections right now.")
        return

    header = f"📡 <b>Active Connections</b> — {total} IP(s) total\n\n"
    body = "\n\n".join(lines)
    full_msg = header + body

    # Telegram message limit is 4096 chars
    if len(full_msg) <= 4096:
        await update.message.reply_html(full_msg)
    else:
        chunks = [full_msg[i : i + 4096] for i in range(0, len(full_msg), 4096)]
        for chunk in chunks:
            await update.message.reply_html(chunk)


# ── /setup (panel config) ─────────────────────────────────────────────────────


async def setup(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    """Start the panel configuration conversation."""
    check = await check_admin_privilege(update)
    if check is not None:
        return check

    if os.path.exists("config.json"):
        json_data = await read_json_file()
        domain = json_data.get("PANEL_DOMAIN")
        username = json_data.get("PANEL_USERNAME")
        password = json_data.get("PANEL_PASSWORD")
        if domain and username and password:
            await update.message.reply_html(
                "⚙️ <b>Current panel configuration:</b>\n"
                f"  Domain:   <code>{domain}</code>\n"
                f"  Username: <code>{username}</code>\n"
                f"  Password: <code>{password}</code>\n\n"
                "Send <code>yes</code> to change, anything else to cancel."
            )
            return GET_CONFIRMATION

    await update.message.reply_html(
        "🌐 Send your panel address (domain or IP with port).\n"
        "Example: <code>sub.domain.com:8333</code> or <code>95.12.153.87:443</code>\n"
        "<b>Do not</b> include <code>https://</code> or <code>http://</code>."
    )
    return GET_DOMAIN


async def get_confirmation(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip().lower() in ("yes", "y"):
        await update.message.reply_html(
            "🌐 Send your panel address:\n"
            "<code>sub.domain.com:8333</code> or <code>95.12.153.87:443</code>"
        )
        return GET_DOMAIN
    await update.message.reply_html("Cancelled. Use /setup whenever you want to change.")
    return ConversationHandler.END


async def get_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["domain"] = update.message.text.strip()
    await update.message.reply_html("👤 Send your panel <b>username</b>:")
    return GET_USERNAME


async def get_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username"] = update.message.text.strip()
    await update.message.reply_html("🔑 Send your panel <b>password</b>:")
    return GET_PASSWORD


async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["password"] = update.message.text.strip()
    await update.message.reply_html("⏳ Verifying credentials…")
    try:
        await add_base_information(
            context.user_data["domain"],
            context.user_data["password"],
            context.user_data["username"],
        )
        await update.message.reply_html("✅ Config saved! <b>Restart the bot</b> to apply.")
    except ValueError:
        await update.message.reply_html(
            "❌ <b>Could not connect to the panel.</b> Check your details and retry.\n\n"
            f"Domain:   <code>{context.user_data['domain']}</code>\n"
            f"Username: <code>{context.user_data['username']}</code>\n"
            f"Password: <code>{context.user_data['password']}</code>\n\n"
            "Try again: /setup"
        )
    return ConversationHandler.END


# ── /country_code ─────────────────────────────────────────────────────────────


async def set_country_code(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    check = await check_admin_privilege(update)
    if check is not None:
        return check
    await update.message.reply_html(
        "🌍 Choose your country code (send the number):\n\n"
        "1 — <code>IR</code> (Iran)\n"
        "2 — <code>RU</code> (Russia)\n"
        "3 — <code>CN</code> (China)\n"
        "4 — <code>None</code> (no location filter)"
    )
    return SET_COUNTRY_CODE


async def write_country_code(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    country_codes = {"1": "IR", "2": "RU", "3": "CN", "4": "None"}
    selected = country_codes.get(update.message.text.strip(), "None")
    await write_country_code_json(selected)
    await update.message.reply_html(f"✅ Country code set to <code>{selected}</code>.")
    return ConversationHandler.END


# ── /add_admin ────────────────────────────────────────────────────────────────


async def add_admin(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    check = await check_admin_privilege(update)
    if check is not None:
        return check
    if len(await check_admin()) >= 5:
        await update.message.reply_html(
            "⚠️ Maximum 5 admins allowed.\n"
            "Remove one with /remove_admin first."
        )
        return ConversationHandler.END
    await update.message.reply_html("Send the <b>chat ID</b> to add as admin:")
    return GET_CHAT_ID


async def get_chat_id(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    new_id = update.message.text.strip()
    try:
        if await add_admin_to_config(new_id):
            await update.message.reply_html(f"✅ Admin <code>{new_id}</code> added.")
        else:
            await update.message.reply_html(f"ℹ️ <code>{new_id}</code> is already an admin.")
    except ValueError:
        await update.message.reply_html(
            f"❌ Invalid input: <code>{new_id}</code>. Try /add_admin again."
        )
    return ConversationHandler.END


# ── /remove_admin ─────────────────────────────────────────────────────────────


async def remove_admin(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    check = await check_admin_privilege(update)
    if check is not None:
        return check
    if len(await check_admin()) == 1:
        await update.message.reply_html(
            "⚠️ Only <b>1</b> admin remains. If removed, the next person to /start "
            "becomes admin automatically."
        )
    await update.message.reply_html("Send the <b>chat ID</b> to remove:")
    return GET_CHAT_ID_TO_REMOVE


async def get_chat_id_to_remove(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    try:
        admin_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_html(
            f"❌ Invalid input: <code>{update.message.text.strip()}</code>. Try /remove_admin again."
        )
        return ConversationHandler.END
    if await remove_admin_from_config(admin_id):
        await update.message.reply_html(f"✅ Admin <code>{admin_id}</code> removed.")
    else:
        await update.message.reply_html(f"❌ Admin <code>{admin_id}</code> not found.")
    return ConversationHandler.END


# ── /admins_list ──────────────────────────────────────────────────────────────


async def admins_list(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    check = await check_admin_privilege(update)
    if check is not None:
        return check
    admins = await check_admin()
    if admins:
        text = "👥 <b>Admins:</b>\n• " + "\n• ".join(
            f"<code>{a}</code>" for a in admins
        )
    else:
        text = "No admins found."
    await update.message.reply_html(text)


# ── /backup ───────────────────────────────────────────────────────────────────


async def send_backup(update: Update, _context: ContextTypes.DEFAULT_TYPE):
    check = await check_admin_privilege(update)
    if check is not None:
        return check
    await update.message.reply_document(
        document=open("config.json", "r", encoding="utf8"),  # pylint: disable=consider-using-with
        caption="📦 config.json backup",
    )


# ── Register handlers ─────────────────────────────────────────────────────────

application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("status", status))
application.add_handler(CommandHandler("admins_list", admins_list))

application.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("setup", setup)],
        states={
            GET_CONFIRMATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_confirmation)],
            GET_DOMAIN:       [MessageHandler(filters.TEXT & ~filters.COMMAND, get_domain)],
            GET_USERNAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_username)],
            GET_PASSWORD:     [MessageHandler(filters.TEXT & ~filters.COMMAND, get_password)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
)
application.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("country_code", set_country_code)],
        states={
            SET_COUNTRY_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, write_country_code)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
)
application.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("add_admin", add_admin)],
        states={
            GET_CHAT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_chat_id)],
        },
        fallbacks=[CommandHandler("start", start)],
    )
)
application.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("remove_admin", remove_admin)],
        states={
            GET_CHAT_ID_TO_REMOVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_chat_id_to_remove)
            ],
        },
        fallbacks=[CommandHandler("start", start)],
    )
)
application.add_handler(
    ConversationHandler(
        entry_points=[CommandHandler("backup", send_backup)],
        states={},
        fallbacks=[],
    )
)

# Catch-all: any unknown text or command → show start
application.add_handler(MessageHandler(filters.TEXT, start))
application.add_handler(MessageHandler(filters.COMMAND, start))
