"""
Message Formatter
- format_mint_card / format_mint_list / format_phases_preview → Markdown (used by admin dashboard)
- format_daily_summary → HTML (used by alerts scheduler and admin summary)
All alert messages are formatted directly in handlers/alerts.py using HTML.
"""
from datetime import datetime

CHAIN_EMOJIS = {
    'ethereum': '⟠', 'base': '🔵', 'blast': '💥', 'arbitrum': '🔷',
    'polygon': '🟣', 'optimism': '🔴', 'zora': '🟡', 'solana': '◎',
    'avalanche': '🔺', 'bnb': '🟡', 'unknown': '⛓',
    'megaeth': '⚡', 'abstract': '🌀', 'linea': '🟢', 'scroll': '📜',
    'starknet': '🌟', 'sonic': '🔵', 'berachain': '🐻', 'bera': '🐻',
    'apechain': '🦧', 'mantle': '🟩', 'taiko': '🥁',
    'unichain': '🦄', 'worldchain': '🌍', 'world': '🌍',
    'celo': '🟡', 'gnosis': '🦉', 'moonbeam': '🌙', 'opbnb': '🟡',
}

STATUS_EMOJIS = {
    'upcoming': '⏳', 'live': '🟢', 'sold_out': '🔴', 'ended': '⚫',
}

NUMBER_EMOJIS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']


def _esc_html(text: str) -> str:
    """Escape HTML special characters."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _esc_md(text: str) -> str:
    """Escape Markdown special characters to prevent parse errors."""
    text = str(text)
    for ch in ['\\', '*', '_', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']:
        text = text.replace(ch, f'\\{ch}')
    return text


def get_chain_emoji(chain: str) -> str:
    return CHAIN_EMOJIS.get((chain or '').lower(), '⛓')


def get_status_emoji(status: str) -> str:
    return STATUS_EMOJIS.get(status, '⏳')


# ── ADMIN DASHBOARD FUNCTIONS (HTML) ──────────────────────────

def format_mint_card(mint: dict) -> str:
    """Format a single mint as a detailed info card. Returns HTML."""
    chain        = mint.get('chain', 'Unknown')
    chain_emoji  = get_chain_emoji(chain)
    status       = mint.get('status', 'upcoming')
    status_emoji = get_status_emoji(status)
    paused       = "⏸ <b>PAUSED</b>\n" if mint.get('paused') else ""
    name         = _esc_html(mint.get('name', 'Unknown'))

    lines = [
        f"{paused}📛 <b>{name}</b>",
        f"{chain_emoji} {_esc_html(chain)}  {status_emoji} {status.upper()}",
        f"🔗 {_esc_html(mint.get('mint_link', 'No link'))}",
        "",
    ]

    if mint.get('total_supply'):
        minted = mint.get('minted', 0) or 0
        lines.append(f"📦 Supply: {minted:,} / {mint['total_supply']:,}")

    phases = mint.get('phases', [])
    if phases:
        lines.append(f"\n📋 <b>{len(phases)} Phase(s):</b>\n")
        for i, p in enumerate(phases, 1):
            time_str = p.get('time', 'TBA') or 'TBA'
            end_time = p.get('end_time', '')
            if end_time:
                time_display = f"{time_str} – {end_time} UTC"
            else:
                time_display = f"{time_str} UTC" if time_str != 'TBA' else 'TBA'
            lines.append(
                f"<b>Phase {i}: {_esc_html(p.get('name', '?'))}</b>\n"
                f"  🕐 {_esc_html(time_display)}\n"
                f"  💰 {_esc_html(p.get('price', 'TBA'))}\n"
            )
    else:
        lines.append("📋 No phases set yet.")

    if mint.get('x_link'):
        lines.append(f"🐦 {_esc_html(mint['x_link'])}")
    if mint.get('discord_link'):
        lines.append(f"💬 {_esc_html(mint['discord_link'])}")
    if mint.get('os_link'):
        lines.append(f"🌊 {_esc_html(mint['os_link'])}")

    if mint.get('notes'):
        lines.append(f"\n<i>{_esc_html(mint['notes'])}</i>")

    return "\n".join(lines)


def format_mint_list(mints: list) -> str:
    """Format list of all mints for dashboard. Returns HTML."""
    if not mints:
        return "No mints added yet. Use ➕ Add Mint to get started!"

    lines = ["📋 <b>All Tracked Mints</b>\n"]
    for m in mints:
        status_emoji = get_status_emoji(m.get('status', 'upcoming'))
        paused       = "⏸" if m.get('paused') else ""
        chain_emoji  = get_chain_emoji(m.get('chain', 'Unknown'))
        phases       = m.get('phases', [])
        time_hint    = ''
        for p in phases:
            t = p.get('time', '')
            if t:
                time_hint = f" — {_esc_html(t)} UTC"
                break
        lines.append(
            f"{status_emoji}{paused} <b>{_esc_html(m['name'])}</b> {chain_emoji} "
            f"(#{m['id']}){time_hint}"
        )

    return "\n".join(lines)


def format_phases_preview(phases: list) -> str:
    """Compact phase preview for confirmations. Returns Markdown."""
    lines = []
    for i, p in enumerate(phases, 1):
        time_str = p.get('time', '') or 'TBA'
        end_time = p.get('end_time', '')
        if end_time:
            time_display = f"{time_str} – {end_time} UTC"
        else:
            time_display = f"{time_str} UTC" if time_str != 'TBA' else 'TBA'
        lines.append(
            f"*Phase {i}:*\n"
            f"{p.get('name', '?')}\n"
            f"🕐 Time: {time_display}\n"
            f"💰 Price: {p.get('price', 'TBA')}"
        )
    return "\n\n".join(lines)


# ── DAILY SUMMARY (HTML — used by scheduler and admin) ─────────

def format_daily_summary(mints_today: list) -> str:
    """
    Format the daily 📅 TODAY'S MINTS message.
    Returns HTML (parse_mode='HTML').

    New format:
    📅 TODAY'S MINTS

    1️⃣ ZOB ⟠ Ethereum
    ┣ ZOB_TREASURY — Free — 🕐 2026-03-16 13:30 UTC
    next phase in 30 minutes
    🔗 Mint Link

    Updated: 2026-03-16 07:27 UTC
    """
    if not mints_today:
        return "📅 <b>TODAY'S MINTS</b>\n\nNo mints scheduled for today!"

    now = datetime.utcnow()
    lines = ["\n📅 <b>TODAY'S MINTS</b>\n"]

    # Group by mint (mints_today may have duplicate mints with different phases)
    seen_ids = {}
    grouped = []
    for mint, phase in mints_today:
        mid = mint.get('id')
        if mid not in seen_ids:
            seen_ids[mid] = len(grouped)
            grouped.append((mint, [phase]))
        else:
            grouped[seen_ids[mid]][1].append(phase)

    for idx, (mint, phases) in enumerate(grouped):
        num_emoji   = NUMBER_EMOJIS[idx] if idx < len(NUMBER_EMOJIS) else f"{idx + 1}."
        chain       = mint.get('chain', 'Unknown')
        chain_emoji = get_chain_emoji(chain)
        name        = _esc_html(mint.get('name', 'Unknown'))
        mint_link   = mint.get('mint_link', '')
        os_link     = mint.get('os_link', '')
        display_link = mint_link or os_link

        # Title line: number + name + chain emoji + chain
        lines.append(f"{num_emoji} <b>{name}</b> {chain_emoji} {_esc_html(chain)}")

        # Use all phases from the mint, not just today's first phase
        all_phases = mint.get('phases') or phases

        for p in all_phases:
            p_name  = _esc_html(p.get('name', 'Phase'))
            p_price = _esc_html(p.get('price', 'TBA'))
            p_time  = p.get('time', 'TBA') or 'TBA'

            # Phase detail line: ┣ NAME — PRICE — 🕐 TIME UTC
            lines.append(f"┣ {p_name} — {p_price} — 🕐 {_esc_html(p_time)} UTC")

            # Countdown line
            if p_time and p_time != 'TBA':
                try:
                    phase_dt = datetime.strptime(p_time, "%Y-%m-%d %H:%M")
                    diff_minutes = int((phase_dt - now).total_seconds() / 60)
                    if diff_minutes > 0:
                        if diff_minutes >= 60:
                            hours = diff_minutes // 60
                            mins  = diff_minutes % 60
                            if mins > 0:
                                lines.append(f"next phase in {hours}h {mins}m")
                            else:
                                lines.append(f"next phase in {hours}h")
                        else:
                            lines.append(f"next phase in {diff_minutes} minutes")
                    elif diff_minutes == 0:
                        lines.append("🟢 LIVE NOW")
                    else:
                        lines.append("🟢 Already started")
                except (ValueError, TypeError):
                    pass

        # Mint link line
        if display_link:
            link_html = f'<a href="{display_link}">Mint Link</a>'
            lines.append(f"🔗 {link_html}")

        lines.append("")  # blank line between mints

    now_str = now.strftime('%Y-%m-%d %H:%M')
    lines.append(f"Updated: {now_str} UTC")
    return "\n".join(lines)
