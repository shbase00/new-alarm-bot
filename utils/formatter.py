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
}

STATUS_EMOJIS = {
    'upcoming': '⏳', 'live': '🟢', 'sold_out': '🔴', 'ended': '⚫',
}

NUMBER_EMOJIS = ['1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']


def _esc_html(text: str) -> str:
    """Escape HTML special characters."""
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def get_chain_emoji(chain: str) -> str:
    return CHAIN_EMOJIS.get((chain or '').lower(), '⛓')


def get_status_emoji(status: str) -> str:
    return STATUS_EMOJIS.get(status, '⏳')


# ── ADMIN DASHBOARD FUNCTIONS (Markdown) ──────────────────────

def format_mint_card(mint: dict) -> str:
    """Format a single mint as a detailed info card. Returns Markdown."""
    chain        = mint.get('chain', 'Unknown')
    chain_emoji  = get_chain_emoji(chain)
    status       = mint.get('status', 'upcoming')
    status_emoji = get_status_emoji(status)
    paused       = "⏸ *PAUSED*\n" if mint.get('paused') else ""

    lines = [
        f"{paused}📛 *{mint['name']}*",
        f"{chain_emoji} {chain}  {status_emoji} {status.upper()}",
        f"🔗 {mint.get('mint_link', 'No link')}",
        "",
    ]

    if mint.get('total_supply'):
        minted = mint.get('minted', 0) or 0
        lines.append(f"📦 Supply: {minted} / {mint['total_supply']:,}")

    phases = mint.get('phases', [])
    if phases:
        lines.append(f"\n📋 *{len(phases)} Phase(s):*\n")
        for i, p in enumerate(phases, 1):
            time_str = p.get('time', 'TBA') or 'TBA'
            end_time = p.get('end_time', '')
            if end_time:
                time_display = f"{time_str} – {end_time} UTC"
            else:
                time_display = f"{time_str} UTC" if time_str != 'TBA' else 'TBA'
            lines.append(
                f"*Phase {i}: {p.get('name', '?')}*\n"
                f"  🕐 {time_display}\n"
                f"  💰 {p.get('price', 'TBA')}\n"
            )
    else:
        lines.append("📋 No phases set yet.")

    if mint.get('x_link'):
        lines.append(f"🐦 {mint['x_link']}")
    if mint.get('discord_link'):
        lines.append(f"💬 {mint['discord_link']}")
    if mint.get('os_link'):
        lines.append(f"🌊 {mint['os_link']}")

    if mint.get('notes'):
        lines.append(f"\n_{mint['notes']}_")

    return "\n".join(lines)


def format_mint_list(mints: list) -> str:
    """Format list of all mints for dashboard. Returns Markdown."""
    if not mints:
        return "No mints added yet. Use ➕ Add Mint to get started!"

    lines = ["📋 *All Tracked Mints*\n"]
    for m in mints:
        status_emoji = get_status_emoji(m.get('status', 'upcoming'))
        paused       = "⏸" if m.get('paused') else ""
        chain_emoji  = get_chain_emoji(m.get('chain', 'Unknown'))
        phases = m.get('phases', [])
        time_hint = ''
        for p in phases:
            t = p.get('time', '')
            if t:
                time_hint = f" — {t} UTC"
                break
        lines.append(
            f"{status_emoji}{paused} *{m['name']}* {chain_emoji} "
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

    Format:
    📅 TODAY'S MINTS

    1️⃣ Bittys
       🕐 WL: 2026-03-12 17:00
       ⟠ Ethereum
       💰 0.005 ETH
       🔗 <a href="...">Mint Link</a>

    Updated: 2026-03-12 15:06 UTC
    """
    if not mints_today:
        return "📅 <b>TODAY'S MINTS</b>\n\nNo mints scheduled for today!"

    lines = ["📅 <b>TODAY'S MINTS</b>\n"]

    for idx, (mint, phase) in enumerate(mints_today):
        num_emoji   = NUMBER_EMOJIS[idx] if idx < len(NUMBER_EMOJIS) else f"{idx + 1}."
        chain       = mint.get('chain', 'Unknown')
        chain_emoji = get_chain_emoji(chain)
        price       = _esc_html(phase.get('price', 'TBA'))
        time_str    = _esc_html(phase.get('time', 'TBA') or 'TBA')
        phase_name  = _esc_html(phase.get('name', 'Phase'))
        name        = _esc_html(mint.get('name', 'Unknown'))
        mint_link   = mint.get('mint_link', '')
        link_html   = f'<a href="{mint_link}">Mint Link</a>' if mint_link else 'No link'

        lines.append(
            f"{num_emoji} <b>{name}</b>\n"
            f"   🕐 {phase_name}: {time_str}\n"
            f"   {chain_emoji} {_esc_html(chain)}\n"
            f"   💰 {price}\n"
            f"   🔗 {link_html}"
        )
        lines.append("")

    now_str = datetime.utcnow().strftime('%Y-%m-%d %H:%M')
    lines.append(f"Updated: {now_str} UTC")
    return "\n".join(lines)
