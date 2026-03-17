"""
General Commands Handler
"""
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import ADMIN_USER_IDS, ALERT_MINUTES_BEFORE, DAILY_SUMMARY_HOUR
from database import get_all_mints, get_channels

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *NFT Mint Alarm Bot*\n\n"
        "Use the buttons below to manage everything! 👇\n\n"
        "➕ Add Mint — add a new collection\n"
        "📋 All Mints — view & edit tracked mints\n"
        "📅 Today's Mints — see today's schedule\n"
        "📢 Channels — manage alert channels\n"
        "🎛 Dashboard — main menu\n\n"
        "/cancel — cancel current action",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")
        ]])
    )

async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        return
    mints    = get_all_mints()
    channels = get_channels()
    active   = sum(1 for m in mints if not m.get('paused'))
    paused   = sum(1 for m in mints if m.get('paused'))
    now      = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')
    await update.message.reply_text(
        f"📊 *Bot Status*\n\n"
        f"🕐 `{now}`\n\n"
        f"📋 *Mints:* {len(mints)} total | {active} active | {paused} paused\n"
        f"📢 *Channels:* {len(channels)}\n\n"
        f"⚙️ Alert: {ALERT_MINUTES_BEFORE} min before mint\n"
        f"📅 Daily summary: {DAILY_SUMMARY_HOUR:02d}:00 UTC\n\n"
        f"✅ Bot is running!",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")
        ]])
    )
