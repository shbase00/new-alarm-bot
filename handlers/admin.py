"""
Admin Dashboard - Interactive Telegram bot dashboard with buttons
Smart phase builder: auto-detects phases from page, falls back to guided input
"""
import re
import logging
import json
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_USER_IDS, ALERT_MINUTES_BEFORE
from database import (
    get_all_mints, get_mint, add_mint, update_mint, delete_mint,
    get_channels, add_channel, remove_channel, init_db, get_conn
)
from utils.parser import parse_mint_url, parse_multiple_urls, get_market_links
from utils.formatter import format_mint_card, format_mint_list

logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────
WAITING_LINK         = 1
WAITING_FIRST_TIME   = 2
WAITING_PHASE_NAMES  = 3
WAITING_INTERVAL     = 4
WAITING_PRICES       = 5
WAITING_LIMITS       = 6
WAITING_EDIT_VALUE   = 7
WAITING_CHANNEL      = 8
WAITING_SUPPLY       = 9   # asking for supply after mint saved
# Phase builder states
PB_FIRST_NAME        = 10
PB_FIRST_TIME        = 11
PB_NEXT_INTERVAL     = 12
PB_NEXT_NAME         = 13
PB_PRICE             = 14   # price for current phase being added
EDIT_PHASE_VAL       = 20   # editing a specific phase field
WAITING_CONTRACT     = 21   # waiting for contract address

# ── AUTH ─────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_USER_IDS

# ── KEYBOARDS ────────────────────────────────────────────────

def reply_kb():
    """Persistent bottom keyboard — always visible."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("➕ Add Mint"),    KeyboardButton("📋 All Mints")],
            [KeyboardButton("📅 Today's Mints"), KeyboardButton("📢 Channels")],
            [KeyboardButton("🎛 Dashboard"),   KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
    )

def main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Mint",    callback_data="add_mint"),
         InlineKeyboardButton("📋 All Mints",  callback_data="view_mints")],
        [InlineKeyboardButton("📅 Today's Mints", callback_data="todays_mints"),
         InlineKeyboardButton("📢 Channels",   callback_data="manage_channels")],
        [InlineKeyboardButton("📨 Send Summary Now", callback_data="send_summary_now"),
         InlineKeyboardButton("ℹ️ Help",        callback_data="help")],
    ])

def mint_list_kb(mints):
    rows = []
    for m in mints:
        icon = "⏸" if m.get('paused') else "✅"
        rows.append([InlineKeyboardButton(
            f"{icon} {m['name']} (#{m['id']})",
            callback_data=f"view_mint_{m['id']}"
        )])
    rows.append([InlineKeyboardButton("🔙 Dashboard", callback_data="dashboard")])
    return InlineKeyboardMarkup(rows)

def mint_detail_kb(mint_id, paused):
    pause_label = "▶️ Resume" if paused else "⏸ Pause"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit",   callback_data=f"edit_mint_{mint_id}"),
         InlineKeyboardButton("🗑 Delete", callback_data=f"delete_mint_{mint_id}")],
        [InlineKeyboardButton(pause_label, callback_data=f"toggle_pause_{mint_id}"),
         InlineKeyboardButton("📢 Channels", callback_data=f"set_channels_{mint_id}")],
        [InlineKeyboardButton("🔙 All Mints", callback_data="view_mints")],
    ])

def edit_field_kb(mint_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📛 Name",      callback_data=f"ef_{mint_id}_name"),
         InlineKeyboardButton("⛓ Chain",     callback_data=f"ef_{mint_id}_chain")],
        [InlineKeyboardButton("🔗 Mint Link", callback_data=f"ef_{mint_id}_mint_link"),
         InlineKeyboardButton("📋 Phases",   callback_data=f"ef_{mint_id}_phases")],
        [InlineKeyboardButton("⏱ Rebuild Phases", callback_data=f"rebuild_phases_{mint_id}"),
         InlineKeyboardButton("🏪 Get Market Links", callback_data=f"get_markets_{mint_id}")],
        [InlineKeyboardButton("🐦 X Link",   callback_data=f"ef_{mint_id}_x_link"),
         InlineKeyboardButton("💬 Discord",  callback_data=f"ef_{mint_id}_discord_link")],
        [InlineKeyboardButton("🌊 OS Market", callback_data=f"ef_{mint_id}_os_link"),
         InlineKeyboardButton("📦 Supply",   callback_data=f"ef_{mint_id}_total_supply")],
        [InlineKeyboardButton("📊 Status",   callback_data=f"status_pick_{mint_id}")],
        [InlineKeyboardButton("📝 Notes",    callback_data=f"ef_{mint_id}_notes")],
        [InlineKeyboardButton("🔙 Cancel",   callback_data=f"view_mint_{mint_id}")],
    ])

def status_pick_kb(mint_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ Upcoming",  callback_data=f"set_status_{mint_id}_upcoming"),
         InlineKeyboardButton("🟢 Live",      callback_data=f"set_status_{mint_id}_live")],
        [InlineKeyboardButton("🔴 Sold Out",  callback_data=f"set_status_{mint_id}_sold_out"),
         InlineKeyboardButton("⚫ Ended",     callback_data=f"set_status_{mint_id}_ended")],
        [InlineKeyboardButton("🔙 Back",      callback_data=f"edit_mint_{mint_id}")],
    ])

def confirm_delete_kb(mint_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{mint_id}"),
        InlineKeyboardButton("❌ Cancel",      callback_data=f"view_mint_{mint_id}"),
    ]])

def channels_kb():
    channels = get_channels()
    rows = []
    for ch in channels:
        a    = "🔔" if ch.get('receive_alerts') else "🔕"
        s    = "📅" if ch.get('receive_summary') else "🚫"
        name = ch.get('channel_name') or ch['channel_id']
        rows.append([
            InlineKeyboardButton(f"{a}{s} {name}", callback_data=f"ch_toggle_{ch['channel_id']}"),
            InlineKeyboardButton("🗑", callback_data=f"ch_remove_{ch['channel_id']}"),
        ])
    rows.append([InlineKeyboardButton("➕ Add Channel", callback_data="add_channel")])
    rows.append([InlineKeyboardButton("🔙 Dashboard",   callback_data="dashboard")])
    return InlineKeyboardMarkup(rows)

# ── HELPERS ───────────────────────────────────────────────────

def parse_datetime(text: str) -> datetime | None:
    """Parse user-entered date/time — flexible formats including today/tomorrow."""
    text = text.strip()
    now  = datetime.utcnow()
    # Handle "HH:MM today" / "HH:MM tomorrow"
    low = text.lower()
    modifier = None
    if low.endswith(' today'):
        modifier = 'today'
        text = text[:-6].strip()
    elif low.endswith(' tomorrow'):
        modifier = 'tomorrow'
        text = text[:-9].strip()
    formats = [
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M",
        "%Y/%m/%d %H:%M",
        "%d/%m %H:%M",
        "%H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                dt = now.replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
                if modifier == 'tomorrow':
                    dt = dt + timedelta(days=1)
            elif fmt == "%d/%m %H:%M":
                dt = dt.replace(year=now.year)
            return dt
        except ValueError:
            continue
    # Month name formats: "Mar 14 18:00" / "14 Mar 18:00" / "March 14 2026 18:00"
    MONTHS = {
        'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
        'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12,
        'january':1,'february':2,'march':3,'april':4,'june':6,
        'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    }
    for pat in [
        r'([A-Za-z]+)\s+(\d{1,2})(?:,?\s*(\d{4}))?\s+(\d{1,2}:\d{2})',
        r'(\d{1,2})\s+([A-Za-z]+)(?:,?\s*(\d{4}))?\s+(\d{1,2}:\d{2})',
    ]:
        m = re.match(pat, text)
        if m:
            g = m.groups()
            # First pattern: month_str day [year] time
            # Second pattern: day month_str [year] time
            if g[0].isdigit():
                day_s, mon_s, yr_s, t_s = g[0], g[1], g[2], g[3]
            else:
                mon_s, day_s, yr_s, t_s = g[0], g[1], g[2], g[3]
            month_num = MONTHS.get(mon_s.lower()[:3])
            if month_num:
                try:
                    year  = int(yr_s) if yr_s else now.year
                    h, mn = map(int, t_s.split(':'))
                    return datetime(year, month_num, int(day_s), h, mn)
                except Exception:
                    pass
    return None

def format_phases_preview(phases: list) -> str:
    lines = ["📋 *Phase Preview:*\n"]
    for p in phases:
        lines.append(
            f"🎯 *{p['name']}*\n"
            f"   🕐 {p['time']} UTC\n"
            f"   💰 {p['price']}\n"
            f"   🔒 Limit: {p['limit']}\n"
        )
    return "\n".join(lines)

# ── COMMANDS ─────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    init_db()
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ You are not authorized.")
        return
    await update.message.reply_text(
        "🚀 *NFT Mint Alarm Bot*\n\nButtons are ready below 👇",
        parse_mode='Markdown',
        reply_markup=reply_kb()
    )
    await update.message.reply_text(
        "🎛 *Dashboard*", parse_mode='Markdown', reply_markup=main_kb()
    )

async def dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🎛 *Dashboard*", parse_mode='Markdown', reply_markup=main_kb())


async def handle_reply_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle the persistent bottom reply keyboard buttons."""
    if not is_admin(update.effective_user.id):
        return
    text = update.message.text.strip()

    if text == "➕ Add Mint":
        ctx.user_data.clear()
        await update.message.reply_text(
            "➕ *Add New Mint*\n\n"
            "Paste the mint page URL and send it here.\n\n"
            "Supported:\n"
            "• opensea.io — full auto detect ✅\n"
            "• any other link — manual phases ✏️\n\n"
            "⚠️ *Type or paste the URL and press Send.*\n"
            "_Send /cancel to go back._",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data="dashboard")
            ]])
        )
        return WAITING_LINK

    elif text == "📋 All Mints":
        mints = get_all_mints()
        def _first_time(m):
            for p in (m.get('phases') or []):
                t = p.get('time','')
                if t:
                    try: return datetime.strptime(t, "%Y-%m-%d %H:%M")
                    except: pass
            return datetime(9999,1,1)
        mints = sorted(mints, key=_first_time)
        await update.message.reply_text(
            format_mint_list(mints), parse_mode='HTML', reply_markup=mint_list_kb(mints)
        )

    elif text == "📅 Today's Mints":
        from database import get_todays_mints
        from utils.formatter import format_daily_summary
        todays = get_todays_mints()
        def _phase_time(mp):
            try: return datetime.strptime(mp[1].get('time',''), "%Y-%m-%d %H:%M")
            except: return datetime(9999,1,1)
        todays = sorted(todays, key=_phase_time)
        msg = format_daily_summary(todays)
        await update.message.reply_text(msg, parse_mode='HTML', disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="dashboard")]]))

    elif text == "📢 Channels":
        channels = get_channels()
        info = f"{len(channels)} channel(s) configured." if channels else "No channels added yet."
        await update.message.reply_text(
            f"📢 *Channel Management*\n\n{info}\n\n"
            "🔔 = alerts  |  📅 = daily summary\n"
            "Tap a channel to toggle alerts on/off.\nTap 🗑 to remove it.",
            parse_mode='Markdown', reply_markup=channels_kb()
        )

    elif text == "🎛 Dashboard":
        await update.message.reply_text("🎛 *Dashboard*", parse_mode='Markdown', reply_markup=main_kb())

    elif text == "ℹ️ Help":
        await update.message.reply_text(
            "ℹ️ *Help*\n\n"
            "*Add a mint:*\n"
            "➕ Add Mint → paste any URL\n\n"
            "🤖 *Auto-detect (OpenSea links):*\n"
            "Phases, times, prices & limits are detected automatically.\n\n"
            "✏️ *Manual entry (all other sites):*\n"
            "Bot saves name + chain, then you enter phases step by step.\n\n"
            "*Supported auto-detect:*\n"
            "• opensea.io ✅ full auto\n"
            "• all other sites ✏️ manual\n\n"
            "*Time entry formats:*\n"
            "`2026-03-25 18:00` • `18:00` _(today)_ • `+60` _(+60 min)_\n\n"
            "Use the buttons below to manage everything! 👇",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="dashboard")]])
        )

# ── MAIN CALLBACK ROUTER ──────────────────────────────────────

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("⛔ Not authorized.")
        return

    data = query.data

    if data == "dashboard":
        await query.edit_message_text("🎛 *Dashboard*", parse_mode='Markdown', reply_markup=main_kb())

    elif data == "view_mints":
        mints = get_all_mints()
        # Sort by first phase time (soonest first, no-time mints at end)
        def _first_time(m):
            for p in (m.get('phases') or []):
                t = p.get('time','')
                if t:
                    try: return datetime.strptime(t, "%Y-%m-%d %H:%M")
                    except: pass
            return datetime(9999,1,1)
        mints = sorted(mints, key=_first_time)
        await query.edit_message_text(
            format_mint_list(mints), parse_mode='HTML', reply_markup=mint_list_kb(mints)
        )

    elif data.startswith("view_mint_"):
        mint_id = int(data.replace("view_mint_", ""))
        mint = get_mint(mint_id)
        if not mint:
            await query.edit_message_text("❌ Mint not found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="view_mints")]]))
            return
        await query.edit_message_text(
            format_mint_card(mint), parse_mode='HTML',
            reply_markup=mint_detail_kb(mint_id, bool(mint.get('paused'))),
            disable_web_page_preview=True
        )

    elif data == "todays_mints":
        from database import get_todays_mints
        from utils.formatter import format_daily_summary
        todays = get_todays_mints()
        # Sort by phase start time
        def _phase_time(mp):
            try: return datetime.strptime(mp[1].get('time',''), "%Y-%m-%d %H:%M")
            except: return datetime(9999,1,1)
        todays = sorted(todays, key=_phase_time)
        msg = format_daily_summary(todays)
        await query.edit_message_text(msg, parse_mode='HTML', disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="dashboard")]]))

    elif data == "send_summary_now":
        from handlers.alerts import send_daily_summary
        await send_daily_summary()
        await query.edit_message_text("✅ Daily summary sent!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="dashboard")]]))

    elif data.startswith("status_pick_"):
        mint_id = int(data.replace("status_pick_", ""))
        mint = get_mint(mint_id)
        if not mint:
            await query.edit_message_text("❌ Mint not found.")
            return
        current = mint.get('status', 'upcoming')
        await query.edit_message_text(
            f"📊 <b>Set Status: {mint['name']}</b>\n\nCurrent: <b>{current.upper()}</b>\n\nChoose new status:",
            parse_mode='HTML', reply_markup=status_pick_kb(mint_id)
        )

    elif data.startswith("set_status_"):
        # set_status_{mint_id}_{status}
        parts   = data.split("_", 3)  # ['set', 'status', mint_id, status_value]
        mint_id = int(parts[2])
        new_status = parts[3]
        mint = get_mint(mint_id)
        if not mint:
            await query.edit_message_text("❌ Mint not found.")
            return
        update_mint(mint_id, status=new_status)
        mint = get_mint(mint_id)
        status_labels = {'upcoming': '⏳', 'live': '🟢', 'sold_out': '🔴', 'ended': '⚫'}
        emoji = status_labels.get(new_status, '📊')
        await query.edit_message_text(
            f"{emoji} Status updated to <b>{new_status.upper()}</b>\n\n" + format_mint_card(mint),
            parse_mode='HTML',
            reply_markup=mint_detail_kb(mint_id, bool(mint.get('paused'))),
            disable_web_page_preview=True
        )

    elif data.startswith("edit_mint_"):
        mint_id = int(data.replace("edit_mint_", ""))
        mint = get_mint(mint_id)
        if not mint:
            await query.edit_message_text("❌ Mint not found.")
            return
        await query.edit_message_text(
            f"✏️ <b>Edit: {mint['name']}</b>\n\nChoose what to edit:",
            parse_mode='HTML', reply_markup=edit_field_kb(mint_id)
        )

    elif data.startswith("ef_"):
        # ef_{mint_id}_{field}
        parts = data.split("_", 2)
        mint_id = int(parts[1])
        field   = parts[2]
        ctx.user_data['editing_mint_id'] = mint_id
        ctx.user_data['editing_field']   = field
        mint    = get_mint(mint_id)
        current = mint.get(field, '') if mint else ''
        current_display = json.dumps(current, indent=2) if isinstance(current, list) else str(current)

        # Phases get a special button-based editor
        if field == 'phases':
            mint    = get_mint(mint_id)
            phases  = mint.get('phases') or []
            if not phases:
                await query.edit_message_text(
                    f"📋 *No phases yet for {mint['name']}*\n\nAdd phases using the phase builder.",
                    parse_mode='Markdown',
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⏱ Build Phases", callback_data=f"rebuild_phases_{mint_id}")],
                        [InlineKeyboardButton("🔙 Back",         callback_data=f"edit_mint_{mint_id}")],
                        [InlineKeyboardButton("🎛 Dashboard",    callback_data="dashboard")],
                    ])
                )
                return
            await query.edit_message_text(
                f"📋 *Edit Phases: {mint['name']}*\n\nTap a phase to edit it:",
                parse_mode='Markdown',
                reply_markup=phases_list_kb(mint_id, phases)
            )
            return

        hints = {
            'name':      'Enter the new collection name:',
            'chain':     'Enter chain:\nEthereum / Base / Blast / Arbitrum / Polygon / Solana',
            'mint_link': 'Enter the new mint URL:',
            'x_link':    'Enter the X (Twitter) link:\nExample: `https://x.com/collection_name`',
            'os_link':   'Enter the OpenSea market link:\nExample: `https://opensea.io/collection/name`',
            'status':    'Enter: upcoming / live / sold_out / ended',
            'notes':     'Enter any notes:',
        }
        await query.edit_message_text(
            f"✏️ *Editing: {field.replace('_',' ').title()}*\n\n"
            f"Current: `{current_display[:200]}`\n\n"
            f"{hints.get(field, 'Enter new value:')}",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")]])
        )
        return WAITING_EDIT_VALUE

    elif data.startswith("ep_list_"):
        mint_id = int(data.replace("ep_list_", ""))
        mint    = get_mint(mint_id)
        phases  = mint.get('phases') or []
        await query.edit_message_text(
            f"📋 *Edit Phases: {mint['name']}*\n\nTap a phase to edit it:",
            parse_mode='Markdown',
            reply_markup=phases_list_kb(mint_id, phases)
        )

    elif data.startswith("ep_select_"):
        # ep_select_{mint_id}_{phase_idx}
        parts     = data.split("_")
        mint_id   = int(parts[2])
        phase_idx = int(parts[3])
        mint      = get_mint(mint_id)
        phases    = mint.get('phases') or []
        if phase_idx >= len(phases):
            await query.edit_message_text("❌ Phase not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"ep_list_{mint_id}")]]))
            return
        p = phases[phase_idx]
        await query.edit_message_text(
            f"✏️ *Phase {phase_idx+1}: {p.get('name','?')}*\n\n"
            f"🕐 Time: `{p.get('time','TBA')}`\n"
            f"💰 Price: `{p.get('price','TBA')}`\n"
            f"🔒 Limit: `{p.get('limit','N/A')}`\n\n"
            f"What do you want to edit?",
            parse_mode='Markdown',
            reply_markup=phase_field_kb(mint_id, phase_idx)
        )

    elif data.startswith("ep_field_"):
        # ep_field_{mint_id}_{phase_idx}_{field}
        parts     = data.split("_", 4)
        mint_id   = int(parts[2])
        phase_idx = int(parts[3])
        field     = parts[4]
        mint      = get_mint(mint_id)
        phases    = mint.get('phases') or []
        p         = phases[phase_idx] if phase_idx < len(phases) else {}
        current   = p.get(field, '')

        field_hints = {
            'name':  'Enter the new phase name:\nExample: `OG` / `Whitelist` / `Public`',
            'time':  'Enter the new start time (UTC):\nExample: `2026-03-25 18:00`',
            'price': 'Enter the new price:\nExample: `Free` / `0.05 ETH`',
            'limit': 'Enter the new wallet limit:\nExample: `2` / `unlimited`',
        }
        ctx.user_data['ep_mint_id']   = mint_id
        ctx.user_data['ep_phase_idx'] = phase_idx
        ctx.user_data['ep_field']     = field
        await query.edit_message_text(
            f"✏️ *Phase {phase_idx+1} — {field.title()}*\n\n"
            f"Current: `{current}`\n\n"
            f"{field_hints.get(field, 'Enter new value:')}\n\n"
            f"_/cancel to go back._",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")]])
        )
        return EDIT_PHASE_VAL

    elif data.startswith("ep_del_"):
        # ep_del_{mint_id}_{phase_idx}
        parts     = data.split("_")
        mint_id   = int(parts[2])
        phase_idx = int(parts[3])
        mint      = get_mint(mint_id)
        phases    = list(mint.get('phases') or [])
        if phase_idx < len(phases):
            removed = phases.pop(phase_idx)
            update_mint(mint_id, phases=phases)
        mint   = get_mint(mint_id)
        phases = mint.get('phases') or []
        await query.edit_message_text(
            f"🗑 Phase deleted.\n\n📋 *Edit Phases: {mint['name']}*\n\nTap a phase to edit it:",
            parse_mode='Markdown',
            reply_markup=phases_list_kb(mint_id, phases) if phases else InlineKeyboardMarkup([
                [InlineKeyboardButton("⏱ Build Phases", callback_data=f"rebuild_phases_{mint_id}")],
                [InlineKeyboardButton("🔙 Back",         callback_data=f"edit_mint_{mint_id}")],
                [InlineKeyboardButton("🎛 Dashboard",    callback_data="dashboard")],
            ])
        )

    elif data.startswith("get_markets_"):
        mint_id = int(data.replace("get_markets_", ""))
        mint = get_mint(mint_id)
        await query.edit_message_text(
            f"🏪 *Get Market Links*\n\n"
            f"Send the contract address for <b>{mint['name']}</b>\n\n"
            f"Example: `0xAbCd1234...`\n\n"
            f"_/cancel to go back._",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"edit_mint_{mint_id}")]])
        )
        ctx.user_data['getting_markets_for'] = mint_id
        return WAITING_CONTRACT

    elif data.startswith("rebuild_phases_"):
        mint_id = int(data.replace("rebuild_phases_", ""))
        mint    = get_mint(mint_id)
        ctx.user_data['building_phases_for'] = mint_id

        # Try to auto-detect time from the stored mint link
        mint_link = mint.get('mint_link', '')
        detected_dt = None
        if mint_link:
            await query.edit_message_text(f"⏱ *Rebuilding phases for: {mint['name']}*\n\n⏳ Trying to auto-detect time from mint page...")
            mint_data = await parse_mint_url(mint_link)
            detected_dt = mint_data.get('first_phase_time')

        if detected_dt:
            ctx.user_data['first_phase_time'] = detected_dt.strftime("%Y-%m-%d %H:%M")
            await query.edit_message_text(
                f"⏱ *Rebuild Phases: {mint['name']}*\n\n"
                f"🕐 First phase time detected: *{detected_dt.strftime('%Y-%m-%d %H:%M')} UTC* ✨\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"*Step 1/3 — Phase Names*\n\n"
                f"Enter all phase names separated by commas:\n\n"
                f"• `OG, WL, Public`\n"
                f"• `Public` _(single phase)_\n\n"
                f"_Send /cancel to abort._",
                parse_mode='Markdown'
            )
            return WAITING_PHASE_NAMES
        else:
            await query.edit_message_text(
                f"⏱ *Rebuild Phases: {mint['name']}*\n\n"
                f"Could not auto-detect time from page.\n\n"
                f"*Step 1/4 — First Phase Date & Time (UTC)*\n\n"
                f"Examples:\n"
                f"• `2024-12-25 18:00`\n"
                f"• `25/12/2024 18:00`\n"
                f"• `18:00` _(today)_\n\n"
                f"_Send /cancel to abort._",
                parse_mode='Markdown'
            )
            return WAITING_FIRST_TIME

    elif data.startswith("delete_mint_"):
        mint_id = int(data.replace("delete_mint_", ""))
        mint = get_mint(mint_id)
        if not mint:
            await query.edit_message_text("❌ Mint not found.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="view_mints")]]))
            return
        await query.edit_message_text(
            f"🗑 *Delete: {mint['name']}?*\n\n"
            f"⚠️ This will *permanently* remove this mint and ALL its data:\n"
            f"• Mint info & phases\n"
            f"• All sent alerts history\n"
            f"• Floor price history\n"
            f"• Sweep events\n\n"
            f"🚫 *This cannot be undone!*",
            parse_mode='Markdown', reply_markup=confirm_delete_kb(mint_id)
        )

    elif data.startswith("confirm_delete_"):
        mint_id = int(data.replace("confirm_delete_", ""))
        mint    = get_mint(mint_id)
        name    = mint['name'] if mint else "Mint"
        delete_mint(mint_id)
        mints   = get_all_mints()
        await query.edit_message_text(
            f"🗑 <b>{name}</b> permanently deleted — all data removed.\n\n" + format_mint_list(mints),
            parse_mode='HTML', reply_markup=mint_list_kb(mints)
        )

    elif data.startswith("toggle_pause_"):
        mint_id   = int(data.replace("toggle_pause_", ""))
        mint      = get_mint(mint_id)
        if not mint: return
        new_paused = 0 if mint.get('paused') else 1
        update_mint(mint_id, paused=new_paused)
        mint  = get_mint(mint_id)
        label = "⏸ Paused" if new_paused else "▶️ Resumed"
        await query.edit_message_text(
            f"{label} alerts for <b>{mint['name']}</b>\n\n" + format_mint_card(mint),
            parse_mode='HTML',
            reply_markup=mint_detail_kb(mint_id, bool(new_paused)),
            disable_web_page_preview=True
        )

    elif data == "manage_channels":
        channels = get_channels()
        info = f"{len(channels)} channel(s) configured." if channels else "No channels added yet."
        await query.edit_message_text(
            f"📢 *Channel Management*\n\n{info}\n\n"
            "🔔 = alerts  |  📅 = daily summary\n"
            "Tap a channel to toggle alerts on/off.\nTap 🗑 to remove it.",
            parse_mode='Markdown', reply_markup=channels_kb()
        )

    elif data.startswith("ch_toggle_"):
        ch_id    = data.replace("ch_toggle_", "")
        channels = get_channels()
        ch       = next((c for c in channels if c['channel_id'] == ch_id), None)
        if ch:
            new_alerts = 0 if ch['receive_alerts'] else 1
            conn = get_conn()
            conn.execute("UPDATE channels SET receive_alerts=? WHERE channel_id=?", (new_alerts, ch_id))
            conn.commit(); conn.close()
            await query.answer("🔔 Alerts ON" if new_alerts else "🔕 Alerts OFF")
        await query.edit_message_text(
            "📢 *Channel Management*", parse_mode='Markdown', reply_markup=channels_kb()
        )

    elif data.startswith("ch_remove_"):
        ch_id = data.replace("ch_remove_", "")
        remove_channel(ch_id)
        await query.edit_message_text(
            "✅ Channel removed.\n\n📢 *Channel Management*",
            parse_mode='Markdown', reply_markup=channels_kb()
        )

    elif data.startswith("set_channels_"):
        mint_id  = int(data.replace("set_channels_", ""))
        mint     = get_mint(mint_id)
        channels = get_channels()
        if not channels:
            await query.edit_message_text(
                "❌ No channels added yet.\nGo to 📢 Channels first.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📢 Channels", callback_data="manage_channels"),
                    InlineKeyboardButton("🔙 Back", callback_data=f"view_mint_{mint_id}"),
                ]])
            )
            return
        mint_channels = mint.get('alert_channels', [])
        rows = []
        for ch in channels:
            checked = "✅" if ch['channel_id'] in mint_channels else "⬜"
            name    = ch.get('channel_name') or ch['channel_id']
            rows.append([InlineKeyboardButton(f"{checked} {name}",
                callback_data=f"mint_ch_{mint_id}_{ch['channel_id']}")])
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"view_mint_{mint_id}")])
        await query.edit_message_text(
            f"📢 *Channels for: {mint['name']}*\n\nTap to toggle:",
            parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data.startswith("mint_ch_"):
        parts   = data.split("_", 3)
        mint_id = int(parts[2])
        ch_id   = parts[3]
        mint    = get_mint(mint_id)
        if mint:
            current = mint.get('alert_channels', [])
            if ch_id in current: current.remove(ch_id)
            else: current.append(ch_id)
            update_mint(mint_id, alert_channels=current)
        mint     = get_mint(mint_id)
        channels = get_channels()
        mint_channels = mint.get('alert_channels', [])
        rows = []
        for ch in channels:
            checked = "✅" if ch['channel_id'] in mint_channels else "⬜"
            name    = ch.get('channel_name') or ch['channel_id']
            rows.append([InlineKeyboardButton(f"{checked} {name}",
                callback_data=f"mint_ch_{mint_id}_{ch['channel_id']}")])
        rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"view_mint_{mint_id}")])
        await query.edit_message_text(
            f"📢 *Channels for: {mint['name']}*\n\nTap to toggle:",
            parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(rows)
        )

    elif data == "help":
        await query.edit_message_text(
            "ℹ️ *Help*\n\n"
            "*Add a mint:*\n"
            "➕ Add Mint → paste any URL\n\n"
            "🤖 *Auto-detect (OpenSea links):*\n"
            "Phases, times, prices & limits are detected automatically.\n\n"
            "✏️ *Manual entry (all other sites):*\n"
            "Bot saves name + chain, then you enter phases step by step.\n\n"
            "*Supported auto-detect:*\n"
            "• opensea.io ✅ full auto\n"
            "• all other sites ✏️ manual\n\n"
            "*Time entry formats:*\n"
            "`2026-03-25 18:00` • `18:00` _(today)_ • `+60` _(+60 min)_\n\n"
            "Use the buttons below to manage everything! 👇",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data="dashboard")]])
        )

    elif data.startswith("confirm_phases_"):
        mint_id = int(data.replace("confirm_phases_", ""))
        phases  = ctx.user_data.get('built_phases', [])
        if not phases:
            await query.edit_message_text("❌ No phases found. Please try again.")
            return
        update_mint(mint_id, phases=phases)
        ctx.user_data.clear()
        mint = get_mint(mint_id)
        await query.edit_message_text(
            f"✅ <b>Phases saved!</b> ({len(phases)} phases)\n\n" + format_mint_card(mint),
            parse_mode='HTML',
            reply_markup=mint_detail_kb(mint_id, bool(mint.get('paused'))),
            disable_web_page_preview=True
        )

    elif data.startswith("redo_phases_"):
        mint_id = int(data.replace("redo_phases_", ""))
        mint    = get_mint(mint_id)
        ctx.user_data['building_phases_for'] = mint_id
        ctx.user_data.pop('built_phases', None)
        await query.edit_message_text(
            f"⏱ *Rebuild Phases: {mint['name']}*\n\n"
            f"*Step 1/4 — First Phase Date & Time (UTC)*\n\n"
            f"Examples:\n"
            f"• `2024-12-25 18:00`\n"
            f"• `25/12 18:00`\n"
            f"• `18:00` _(today)_\n\n"
            f"_Send /cancel to abort._",
            parse_mode='Markdown'
        )
        return WAITING_FIRST_TIME

# ── CONVERSATION: ADD MINT ────────────────────────────────────

async def add_mint_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    await query.edit_message_text(
        "➕ *Add New Mint*\n\n"
        "Paste the mint page URL and send it here.\n\n"
        "Supported:\n"
        "• opensea.io — full auto detect ✅\n"
        "• any other link — manual phases ✏️\n\n"
        "⚠️ *Type or paste the URL and press Send.*\n"
        "_/cancel to go back._",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="dashboard")
        ]])
    )
    return WAITING_LINK

async def add_mint_link(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    # Extract all https URLs from message
    urls = re.findall(r'https?://[^\s]+', text)
    # Filter to valid URLs (has a domain)
    urls = [u for u in urls if '.' in u]

    if not urls:
        await update.message.reply_text(
            "❌ No valid URL found.\n"
            "Please paste a URL starting with https://\n\n"
            "Or /cancel to go back."
        )
        return WAITING_LINK

    urls = list(dict.fromkeys(urls))[:5]  # dedupe, max 5

    loading = await update.message.reply_text(
        f"⏳ Detecting {'mint' if len(urls)==1 else str(len(urls))+' mints'}... this may take ~30s"
    )

    if len(urls) == 1:
        mint_data = await parse_mint_url(urls[0])
        all_data  = [mint_data]
    else:
        all_data = await parse_multiple_urls(urls)

    await loading.delete()

    # Save all mints to DB
    saved_ids = []
    for d in all_data:
        url     = d.get('mint_link', '')
        name    = d.get('name') or 'Unknown Project'
        chain   = d.get('chain', 'Unknown')
        phases  = d.get('phases', [])
        mint_id = add_mint(name=name, chain=chain, mint_link=url, phases=phases)
        saved_ids.append(mint_id)
        # Save extra fields
        extras = {}
        if d.get('x_link'):       extras['x_link']       = d['x_link']
        if d.get('discord_link'): extras['discord_link'] = d['discord_link']
        if d.get('contract'):     extras['contract']     = d['contract']
        if d.get('total_supply'): extras['total_supply'] = d['total_supply']
        if d.get('minted'):       extras['minted']       = d['minted']
        ml = d.get('market_links')
        if ml:
            # Pass dict directly — update_mint handles JSON serialization
            extras['market_links'] = ml
        os_link = d.get('os_link', '')
        if not os_link and 'opensea.io' in url:
            m = re.search(r'(opensea\.io/collection/[^/?#]+)', url)
            if m: os_link = 'https://' + m.group(1)
        if os_link: extras['os_link'] = os_link
        if extras:
            update_mint(mint_id, **extras)

    ctx.user_data['pending_mint_ids'] = saved_ids
    ctx.user_data['pending_mint_idx'] = 0

    # Ask for supply before showing summary
    if len(saved_ids) == 1:
        mint_id   = saved_ids[0]
        mint_data = all_data[0]
        ctx.user_data['building_phases_for'] = mint_id
        ctx.user_data['pending_mint_data']   = mint_data

        minted = mint_data.get('minted', 0)
        minted_txt = f"\n📊 *Minted so far:* {minted:,}" if minted else ""

        await update.message.reply_text(
            f"✅ *{mint_data.get('name','Mint')}* detected!\n"
            f"{minted_txt}\n\n"
            f"📦 *What's the total supply?*\n"
            f"Reply with a number (e.g. `5000`) or type `skip` to set later.",
            parse_mode='Markdown',
        )
        return WAITING_SUPPLY
    else:
        # Multi-link: show summary of all, confirm all at once
        msg = f"✅ *Detected {len(saved_ids)} collections*\n\n"
        for i, (d, mid) in enumerate(zip(all_data, saved_ids), 1):
            name   = d.get('name', 'Unknown')
            chain  = d.get('chain', '')
            phases = d.get('phases', [])
            msg += f"*{i}️⃣ {name}*"
            if chain: msg += f" — {chain}"
            msg += "\n"
            if phases:
                for p in phases[:3]:
                    t = p.get('time','TBA')
                    pr = p.get('price','TBA')
                    msg += f"  • {p.get('name','Phase')}: {t} UTC | {pr}\n"
            else:
                msg += "  ⚠️ No phases detected — add manually\n"
            msg += "\n"
        msg += "Alerts created for all. Tap a mint to edit phases."
        rows = []
        for _i in range(len(saved_ids)):
            _btn_name = (all_data[_i].get('name') or 'Mint')[:22]
            rows.append([InlineKeyboardButton(
                f"✏️ {_btn_name}", callback_data=f"view_mint_{saved_ids[_i]}"
            )])
        rows.append([InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")])
        kb = InlineKeyboardMarkup(rows)
        await update.message.reply_text(msg, parse_mode='Markdown',
                                        reply_markup=kb, disable_web_page_preview=True)
        return ConversationHandler.END


async def waiting_supply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle supply input after mint is saved."""
    text      = update.message.text.strip()
    mint_id   = ctx.user_data.get('building_phases_for')
    mint_data = ctx.user_data.get('pending_mint_data', {})

    if not mint_id:
        return ConversationHandler.END

    if text.lower() != 'skip':
        try:
            supply = int(text.replace(',', '').replace('.', '').strip())
            if supply > 0:
                update_mint(mint_id, total_supply=supply)
                mint_data['total_supply'] = supply
        except ValueError:
            await update.message.reply_text(
                "❌ Please enter a valid number (e.g. `5000`) or type `skip`.",
                parse_mode='Markdown',
            )
            return WAITING_SUPPLY

    # Trigger immediate minted count check after supply is set
    if mint_data.get('total_supply'):
        try:
            from utils.parser import get_minted_count
            from database import get_mint as db_get_mint
            fresh_mint = db_get_mint(mint_id) or mint_data
            minted = await get_minted_count(fresh_mint)
            if minted and minted > 0:
                update_mint(mint_id, minted=minted)
                mint_data['minted'] = minted
                logger.info(f"[supply] Immediate minted check: {minted:,}/{mint_data['total_supply']:,}")
        except Exception as e:
            logger.debug(f"[supply] Immediate minted check error: {e}")

    await _show_single_summary(update, ctx, mint_data, mint_id)
    return _decide_next_state(mint_data)


async def _show_single_summary(update, ctx, mint_data: dict, mint_id: int):
    """Show full confirmation summary for a single mint."""
    name     = mint_data.get('name', 'Unknown')
    chain    = mint_data.get('chain', 'Unknown')
    phases   = mint_data.get('phases', [])
    contract = mint_data.get('contract', '')
    x_link   = mint_data.get('x_link', '')
    discord  = mint_data.get('discord_link', '')
    supply   = mint_data.get('total_supply', 0)
    mlinks   = mint_data.get('market_links', {})
    if isinstance(mlinks, str):
        try: mlinks = json.loads(mlinks)
        except: mlinks = {}
    os_link   = mint_data.get('os_link', '')
    countdown = mint_data.get('countdown_detected', False)

    msg = f"✅ *{name}*\n"
    msg += f"⛓ Chain: {chain}\n"
    if supply:  msg += f"📦 Supply: {supply:,}\n"
    if contract: msg += f"📄 Contract: `{contract[:10]}...{contract[-6:]}`\n"
    msg += "\n"

    if phases:
        for i, p in enumerate(phases, 1):
            t = p.get('time', 'TBA')
            msg += f"*Phase {i} — {p.get('name','Phase')}*\n"
            msg += f"  🕐 {t} UTC"
            if countdown and i == 1: msg += " _(approx)_"
            msg += "\n"
            msg += f"  💰 {p.get('price','TBA')}  |  🔒 {p.get('limit','N/A')}/wallet\n"
    else:
        msg += "⚠️ _No phases detected — add manually_\n"

    msg += "\n"
    if x_link:  msg += f"🐦 {x_link}\n"
    if discord: msg += f"💬 {discord}\n"
    if os_link: msg += f"🌊 {os_link}\n"

    if mlinks:
        mparts = []
        for mname, murl in list(mlinks.items())[:3]:
            mparts.append(f"[{mname}]({murl})")
        if mparts: msg += "🏪 " + " · ".join(mparts) + "\n"

    buttons = []
    if phases and all(p.get('time') for p in phases):
        buttons.append([
            InlineKeyboardButton("✅ Looks Good", callback_data=f"view_mint_{mint_id}"),
            InlineKeyboardButton("✏️ Edit Phases", callback_data=f"ef_{mint_id}_phases"),
        ])
    else:
        buttons.append([
            InlineKeyboardButton("⏱ Add Phases", callback_data=f"rebuild_phases_{mint_id}"),
            InlineKeyboardButton("✅ Save As-Is",  callback_data=f"view_mint_{mint_id}"),
        ])
    buttons.append([InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")])

    await update.message.reply_text(msg, parse_mode='Markdown',
                                    reply_markup=InlineKeyboardMarkup(buttons),
                                    disable_web_page_preview=True)


def _decide_next_state(mint_data: dict):
    """Return conversation state based on what was detected."""
    phases = mint_data.get('phases', [])
    if phases and all(p.get('time') for p in phases):
        return ConversationHandler.END
    if phases and not all(p.get('time') for p in phases):
        return PB_FIRST_TIME
    if mint_data.get('first_phase_time'):
        return PB_FIRST_NAME
    # No phases, no time detected — ask user for time first
    return PB_FIRST_TIME



# ══════════════════════════════════════════════════════════════
# PHASE BUILDER
# Flow: PB_FIRST_TIME? → PB_FIRST_NAME → PB_NEXT_INTERVAL →
#       PB_NEXT_NAME → PB_NEXT_INTERVAL → ... → PB_PRICE → done
# ══════════════════════════════════════════════════════════════

def _pb_dashboard_kb():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard"),
    ]])

def phases_list_kb(mint_id, phases):
    """Show each phase as a button — tap to edit it."""
    rows = []
    for i, p in enumerate(phases):
        rows.append([InlineKeyboardButton(
            f"Phase {i+1}: {p.get('name','?')} | {p.get('time','TBA')} | {p.get('price','TBA')}",
            callback_data=f"ep_select_{mint_id}_{i}"
        )])
    rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"edit_mint_{mint_id}")])
    rows.append([InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")])
    return InlineKeyboardMarkup(rows)

def phase_field_kb(mint_id, phase_idx):
    """Choose which field of a phase to edit."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 Name",   callback_data=f"ep_field_{mint_id}_{phase_idx}_name"),
         InlineKeyboardButton("🕐 Time",   callback_data=f"ep_field_{mint_id}_{phase_idx}_time")],
        [InlineKeyboardButton("💰 Price",  callback_data=f"ep_field_{mint_id}_{phase_idx}_price"),
         InlineKeyboardButton("🔒 Limit",  callback_data=f"ep_field_{mint_id}_{phase_idx}_limit")],
        [InlineKeyboardButton("🗑 Delete This Phase", callback_data=f"ep_del_{mint_id}_{phase_idx}")],
        [InlineKeyboardButton("🔙 Back to Phases",    callback_data=f"ep_list_{mint_id}")],
        [InlineKeyboardButton("🎛 Dashboard",          callback_data="dashboard")],
    ])

def _pb_done_kb(mint_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Another Phase", callback_data=f"pb_add_{mint_id}"),
         InlineKeyboardButton("✅ Done",               callback_data=f"pb_done_{mint_id}")],
        [InlineKeyboardButton("🎛 Dashboard",          callback_data="dashboard")],
    ])

def _pb_summary(phases: list) -> str:
    if not phases:
        return ""
    lines = []
    for i, p in enumerate(phases, 1):
        lines.append(f"  *Phase {i}: {p['name']}*  🕐 {p.get('time','TBA')}  💰 {p.get('price','TBA')}")
    return "\n".join(lines)

def _normalize_price(text: str) -> str:
    t = text.strip()
    if re.match(r"^0\.0+$", t) or t.lower() in ("free", "0", "0 eth", "0.0 eth", "0.00 eth"):
        return "Free"
    if re.match(r"^[\d.]+$", t):
        return f"{t} ETH"
    return t

async def pb_first_time(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User enters Phase 1 start time manually (when auto-detect had no time)."""
    dt = parse_datetime(update.message.text.strip())
    if not dt:
        await update.message.reply_text(
            "❌ Could not read that time.\n\n"
            "• `2026-03-25 18:00`\n• `18:00` _(today)_\n\nTry again or /cancel.",
            parse_mode='Markdown', reply_markup=_pb_dashboard_kb()
        )
        return PB_FIRST_TIME

    ctx.user_data['pb_first_time'] = dt.strftime("%Y-%m-%d %H:%M")
    await update.message.reply_text(
        f"🕐 Phase 1 start: *{dt.strftime('%Y-%m-%d %H:%M')} UTC*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📝 *What is Phase 1 called?*\n"
        f"Examples: `OG` / `Team` / `Public`\n\n"
        f"_/cancel to stop._",
        parse_mode='Markdown', reply_markup=_pb_dashboard_kb()
    )
    return PB_FIRST_NAME

async def pb_first_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User enters name for Phase 1. Saves phase 1 and asks for phase 2 interval."""
    pname   = update.message.text.strip()
    mint_id = ctx.user_data.get('building_phases_for')
    time1   = ctx.user_data.get('pb_first_time', '')

    if not pname:
        await update.message.reply_text("❌ Please enter a name.\nExample: `OG` or `Public`\n\n_/cancel to stop._", parse_mode='Markdown')
        return PB_FIRST_NAME

    # Save phase 1 (price TBA for now, will update at PB_PRICE)
    phases = ctx.user_data.get('smart_phases', [])
    phases.append({'name': pname, 'time': time1, 'price': 'TBA', 'limit': 'N/A'})
    ctx.user_data['smart_phases'] = phases

    await update.message.reply_text(
        f"✅ *Phase 1: {pname}*  🕐 {time1} UTC\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Price for Phase 1: {pname}?*\n\n"
        f"• `Free`\n• `0.05 ETH`\n• `0.0022 ETH`",
        parse_mode='Markdown', reply_markup=_pb_dashboard_kb()
    )
    return PB_PRICE

async def pb_next_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User enters interval in minutes between last phase and new one."""
    text   = update.message.text.strip()
    phases = ctx.user_data.get('smart_phases', [])

    try:
        minutes = int(re.sub(r"[^\d]", "", text))
        if minutes <= 0:
            raise ValueError
    except Exception:
        await update.message.reply_text(
            "❌ Please enter a number of minutes.\n"
            "Example: `30` _(30 minutes after previous phase)_\n\nTry again or /cancel.",
            parse_mode='Markdown', reply_markup=_pb_dashboard_kb()
        )
        return PB_NEXT_INTERVAL

    # Calculate new phase time
    prev_time = datetime.strptime(phases[-1]['time'], "%Y-%m-%d %H:%M")
    new_time  = prev_time + timedelta(minutes=minutes)
    ctx.user_data['pb_next_time'] = new_time.strftime("%Y-%m-%d %H:%M")

    phase_num = len(phases) + 1
    await update.message.reply_text(
        f"✅ +{minutes} min → 🕐 *{new_time.strftime('%Y-%m-%d %H:%M')} UTC*\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📝 *What is Phase {phase_num} called?*\n"
        f"Examples: `GTD` / `FCFS` / `Whitelist` / `Public`\n\n"
        f"_/cancel to stop._",
        parse_mode='Markdown', reply_markup=_pb_dashboard_kb()
    )
    return PB_NEXT_NAME

async def pb_next_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User enters name for next phase. Saves it and asks for price or more phases."""
    pname   = update.message.text.strip()
    mint_id = ctx.user_data.get('building_phases_for')

    if not pname:
        await update.message.reply_text("❌ Please enter a name.\n_/cancel to stop._", parse_mode='Markdown')
        return PB_NEXT_NAME

    phases = ctx.user_data.get('smart_phases', [])
    phases.append({
        'name':  pname,
        'time':  ctx.user_data.get('pb_next_time', ''),
        'price': 'TBA',
        'limit': 'N/A',
    })
    ctx.user_data['smart_phases'] = phases
    phase_num = len(phases)

    await update.message.reply_text(
        f"✅ *Phase {phase_num}: {pname}*  🕐 {phases[-1]['time']} UTC\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Price for Phase {phase_num}: {pname}?*\n\n"
        f"• `Free`\n• `0.05 ETH`\n• `0.0022 ETH`",
        parse_mode='Markdown', reply_markup=_pb_dashboard_kb()
    )
    return PB_PRICE

async def pb_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Save price for the last added phase, then offer add more / done."""
    text    = update.message.text.strip()
    mint_id = ctx.user_data.get('building_phases_for')
    phases  = ctx.user_data.get('smart_phases', [])

    price = _normalize_price(text)
    if phases:
        phases[-1]['price'] = price   # set price on the phase we just named
    ctx.user_data['smart_phases'] = phases

    phase_num  = len(phases)
    last_phase = phases[-1]
    summary    = _pb_summary(phases)

    await update.message.reply_text(
        f"✅ *Phase {phase_num}: {last_phase['name']}*  💰 {price}\n\n"
        f"{summary}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Add another phase or finish?",
        parse_mode='Markdown',
        reply_markup=_pb_done_kb(mint_id)
    )
    return ConversationHandler.END

async def pb_add_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Button: ➕ Add Another Phase — asks for interval."""
    query   = update.callback_query
    await query.answer()
    mint_id = int(query.data.replace("pb_add_", ""))
    ctx.user_data['building_phases_for'] = mint_id
    phases  = ctx.user_data.get('smart_phases', [])
    prev    = phases[-1] if phases else None
    prev_str = f"\n_(previous phase: *{prev['name']}* at {prev['time']} UTC)_" if prev else ""

    await query.edit_message_text(
        f"*Phase {len(phases)+1} — Interval*{prev_str}\n\n"
        f"⏱ How many minutes after the previous phase?\n\n"
        f"• `30`\n• `60`\n• `90`\n\n"
        f"_/cancel to stop._",
        parse_mode='Markdown',
        reply_markup=_pb_dashboard_kb()
    )
    return PB_NEXT_INTERVAL

async def pb_done_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Button: ✅ Done — save all phases and show result."""
    query   = update.callback_query
    await query.answer()
    mint_id = int(query.data.replace("pb_done_", ""))
    ctx.user_data['building_phases_for'] = mint_id
    phases  = ctx.user_data.get('smart_phases', [])

    if not phases:
        await query.edit_message_text(
            "❌ No phases added.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")
            ]])
        )
        return ConversationHandler.END

    update_mint(mint_id, phases=phases)
    mint    = get_mint(mint_id)
    preview = format_phases_preview(phases)
    ctx.user_data.clear()

    await query.edit_message_text(
        f"🎉 *All phases saved!*\n\n"
        f"📛 <b>{mint['name']}</b>\n\n"
        f"{preview}",
        parse_mode='Markdown',
        reply_markup=mint_detail_kb(mint_id, bool(mint.get('paused')))
    )
    return ConversationHandler.END

# ── Legacy stubs used by rebuild_conv in bot.py ───────────────

async def step_first_time(update, ctx):   return await pb_first_time(update, ctx)
async def step_phase_names(update, ctx):  return await pb_first_name(update, ctx)
async def step_interval(update, ctx):     return await pb_next_interval(update, ctx)
async def step_prices(update, ctx):       return await pb_price(update, ctx)
async def step_limits(update, ctx):       return ConversationHandler.END


# ── CONVERSATION: EDIT FIELD ─────────────────────────────────

async def handle_text_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    action = ctx.user_data.get('action')

    # Adding a channel
    if action == 'add_channel':
        ch_input = update.message.text.strip()
        ctx.user_data.clear()
        try:
            chat    = await ctx.bot.get_chat(ch_input)
            ch_name = chat.title or chat.username or str(chat.id)
            add_channel(str(chat.id), ch_name)
            await update.message.reply_text(
                f"✅ *Channel added successfully!*\n\n"
                f"📢 Name: *{ch_name}*\n"
                f"🆔 ID: `{chat.id}`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📢 Manage Channels", callback_data="manage_channels"),
                    InlineKeyboardButton("🎛 Dashboard",       callback_data="dashboard"),
                ]])
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ *Could not add channel.*\n\nError: `{e}`\n\n"
                f"Make sure:\n"
                f"1. Bot is added as *Admin* to the channel\n"
                f"2. Username/ID is correct\n\n"
                f"Example: `@yourchannel` or `-1001234567890`",
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Channels", callback_data="manage_channels")
                ]])
            )
        return ConversationHandler.END

    # ── Contract address for market links ──
    if ctx.user_data.get('getting_markets_for'):
        mint_id  = ctx.user_data.pop('getting_markets_for')
        contract = update.message.text.strip()
        mint     = get_mint(mint_id)

        if not contract.startswith('0x') or len(contract) < 40:
            await update.message.reply_text(
                "❌ Invalid contract address. Must start with `0x` and be 42 characters.\n\nTry again or /cancel.",
                parse_mode='Markdown'
            )
            ctx.user_data['getting_markets_for'] = mint_id
            return WAITING_CONTRACT

        loading = await update.message.reply_text("⏳ Fetching market links...")
        links = await get_market_links(contract, mint.get('chain', 'Ethereum'))
        await loading.delete()

        if not links:
            await update.message.reply_text(
                "❌ Could not generate market links for that contract.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"edit_mint_{mint_id}")]]),
            )
            return ConversationHandler.END

        # Save OpenSea link automatically
        if links.get('OpenSea') and not mint.get('os_link'):
            update_mint(mint_id, os_link=links['OpenSea'])

        # Format all links
        lines = [f"🏪 *Market Links — {mint['name']}*\n"]
        for name, url in links.items():
            lines.append(f"• *{name}:* {url}")
        lines.append(f"\n✅ OpenSea link saved automatically.")

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode='Markdown',
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back to Edit", callback_data=f"edit_mint_{mint_id}"),
                InlineKeyboardButton("🎛 Dashboard",    callback_data="dashboard"),
            ]])
        )
        return ConversationHandler.END

    # ── Editing a specific phase field ──
    if ctx.user_data.get('ep_field'):
        ep_mint_id   = ctx.user_data.get('ep_mint_id')
        ep_phase_idx = ctx.user_data.get('ep_phase_idx')
        ep_field     = ctx.user_data.get('ep_field')
        new_val      = update.message.text.strip()

        mint   = get_mint(ep_mint_id)
        phases = list(mint.get('phases') or [])
        if ep_phase_idx < len(phases):
            if ep_field == 'time':
                dt = parse_datetime(new_val)
                if not dt:
                    await update.message.reply_text(
                        "❌ Could not read that time.\nFormat: `2026-03-25 18:00`\n\nTry again or /cancel.",
                        parse_mode='Markdown'
                    )
                    return EDIT_PHASE_VAL
                phases[ep_phase_idx]['time'] = dt.strftime("%Y-%m-%d %H:%M")
            elif ep_field == 'price':
                phases[ep_phase_idx]['price'] = _normalize_price(new_val)
            else:
                phases[ep_phase_idx][ep_field] = new_val
            update_mint(ep_mint_id, phases=phases)

        ctx.user_data.pop('ep_field', None)
        ctx.user_data.pop('ep_mint_id', None)
        ctx.user_data.pop('ep_phase_idx', None)

        mint = get_mint(ep_mint_id)
        await update.message.reply_text(
            f"✅ *Phase {ep_phase_idx+1} {ep_field} updated!*",
            parse_mode='Markdown',
            reply_markup=phases_list_kb(ep_mint_id, mint.get('phases') or [])
        )
        return ConversationHandler.END

    # Editing a mint field
    mint_id = ctx.user_data.get('editing_mint_id')
    field   = ctx.user_data.get('editing_field')

    if not mint_id or not field:
        await update.message.reply_text(
            "❓ Nothing to edit. Use the dashboard.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")
            ]])
        )
        ctx.user_data.clear()
        return ConversationHandler.END

    new_value = update.message.text.strip()

    if field == 'phases':
        try:
            clean  = new_value.replace('```json','').replace('```','').strip()
            phases = json.loads(clean)
            if not isinstance(phases, list):
                raise ValueError("Must be a JSON list")
            update_mint(mint_id, phases=phases)
            success_msg = f"✅ <b>Phases updated!</b> ({len(phases)} phases)"
        except Exception as e:
            await update.message.reply_text(
                f"❌ *Invalid format.*\nError: `{e}`\n\n"
                "Example:\n"
                '`[{"name":"OG","time":"2024-12-25 18:00","price":"0.02 ETH","limit":"2"}]`\n\n'
                "Try again or /cancel.",
                parse_mode='Markdown'
            )
            return WAITING_EDIT_VALUE
    elif field == 'total_supply':
        try:
            supply = int(new_value.replace(',', '').strip())
            update_mint(mint_id, total_supply=supply)
            success_msg = f"✅ <b>Supply</b> updated to {supply:,}!"
        except ValueError:
            await update.message.reply_text(
                "❌ Supply must be a number (e.g. `5000`)\n\nTry again or /cancel.",
                parse_mode='Markdown'
            )
            return WAITING_EDIT_VALUE
    else:
        update_mint(mint_id, **{field: new_value})
        success_msg = f"✅ <b>{field.replace('_',' ').title()}</b> updated!"

    ctx.user_data.clear()
    mint = get_mint(mint_id)
    await update.message.reply_text(
        f"{success_msg}\n\n" + format_mint_card(mint),
        parse_mode='HTML',
        reply_markup=mint_detail_kb(mint_id, bool(mint.get('paused'))),
        disable_web_page_preview=True
    )
    return ConversationHandler.END

# ── CONVERSATION: ADD CHANNEL ─────────────────────────────────

async def add_channel_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data.clear()
    ctx.user_data['action'] = 'add_channel'
    await query.edit_message_text(
        "📢 *Add Channel*\n\n"
        "Enter the channel username or numeric ID:\n\n"
        "• `@yourchannel`\n"
        "• `-1001234567890`\n\n"
        "⚠️ Make sure the bot is already added as *Admin* to the channel!\n\n"
        "_Send /cancel to go back._",
        parse_mode='Markdown'
    )
    return WAITING_CHANNEL

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text(
        "❌ *Cancelled.*",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎛 Dashboard", callback_data="dashboard")
        ]])
    )
    return ConversationHandler.END
