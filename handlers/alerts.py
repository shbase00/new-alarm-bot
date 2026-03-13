"""
Alerts Handler
- Pre-mint alerts (15 minutes before)  
- Live alerts (when mint starts)
- Sold-out detection (minted >= max_supply) → stops future phase alerts
- Floor pump detection (50%+ increase)
- Big sweep detection (10 NFTs in 60 seconds)
- Daily summary at 10:00 UTC
- HTML parse mode for all alerts
"""
import asyncio
import logging
import datetime
from telegram.ext import Application

from config import (
    ALERT_MINUTES_BEFORE, DAILY_SUMMARY_HOUR, DAILY_SUMMARY_MINUTE,
    STATUS_CHECK_INTERVAL, FLOOR_CHECK_INTERVAL, FLOOR_PUMP_THRESHOLD,
    SWEEP_COUNT_THRESHOLD, SWEEP_WINDOW_SECONDS
)
from database import (
    get_all_mints, get_todays_mints, alert_already_sent,
    mark_alert_sent, get_channels, update_mint,
    record_floor_price, get_last_floor_price,
    record_sweep_event, count_recent_sweeps, cleanup_old_sweep_events
)

logger = logging.getLogger(__name__)
_app: Application = None


async def setup_scheduler(app: Application):
    global _app
    _app = app
    app.job_queue.run_repeating(
        callback=_alert_job, interval=STATUS_CHECK_INTERVAL, first=10, name="alert_loop"
    )
    app.job_queue.run_repeating(
        callback=_floor_monitor_job, interval=FLOOR_CHECK_INTERVAL, first=30, name="floor_monitor"
    )
    app.job_queue.run_daily(
        callback=_summary_job,
        time=datetime.time(hour=DAILY_SUMMARY_HOUR, minute=DAILY_SUMMARY_MINUTE,
                           tzinfo=datetime.timezone.utc),
        name="daily_summary"
    )
    logger.info("Scheduler started")


async def _alert_job(context):
    try:
        await check_and_send_alerts()
    except Exception as e:
        logger.error(f"Alert job error: {e}")


async def _floor_monitor_job(context):
    try:
        await check_floor_and_sweeps()
    except Exception as e:
        logger.error(f"Floor monitor job error: {e}")


async def _summary_job(context):
    try:
        await send_daily_summary()
    except Exception as e:
        logger.error(f"Daily summary job error: {e}")


# ── CHAIN EMOJI ──────────────────────────────────────────────

def _chain_emoji(chain: str) -> str:
    emojis = {
        'ethereum': '⟠', 'base': '🔵', 'blast': '💥', 'arbitrum': '🔷',
        'polygon': '🟣', 'solana': '◎', 'bitcoin': '₿', 'zora': '🟡',
        'optimism': '🔴', 'avalanche': '🔺', 'bnb': '🟡',
    }
    return emojis.get((chain or '').lower(), '⛓')


def _esc(text: str) -> str:
    return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


# ── ALERT FORMATTERS ─────────────────────────────────────────

def _format_mint_alert(mint: dict, alert_type: str = 'pre') -> str:
    """
    Format:
    🚨 MINT ALERT
    Collection Name
    ⟠ Network
    Supply: minted / total_supply
    Phase Name
    Price
    Time
    ...
    Mint | Twitter | Discord
    """
    name = _esc(mint.get('name', 'Unknown'))
    chain = mint.get('chain', 'Unknown')
    chain_emoji = _chain_emoji(chain)
    minted = mint.get('minted', 0) or 0
    total_supply = mint.get('total_supply', 0) or 0

    if alert_type == 'live':
        header = '🚨 MINT ALERT'
    else:
        header = '🚨 MINT ALERT'

    lines = [f'<b>{header}</b>', '']
    lines.append(f'<b>{name}</b>')
    lines.append(f'{chain_emoji} {_esc(chain)}')

    if total_supply:
        lines.append(f'Supply: {minted} / {total_supply:,}')

    lines.append('')

    for phase in mint.get('phases', []):
        phase_name = _esc(phase.get('name', 'Phase'))
        price = _esc(phase.get('price', 'TBA'))
        time_str = _esc(phase.get('time', 'TBA') or 'TBA')
        lines.append(f'<b>{phase_name}</b>')
        lines.append(price)
        lines.append(f'🕐 {time_str} UTC')
        lines.append('')

    links = _build_links_html(mint)
    if links:
        lines.append(links)

    return '\n'.join(lines).strip()


def _format_sold_out_alert(mint: dict) -> str:
    name = _esc(mint.get('name', 'Unknown'))
    total_supply = mint.get('total_supply', 0) or 0
    supply_str = f'{total_supply:,} / {total_supply:,}' if total_supply else 'N/A'
    return (
        f'🔥 <b>SOLD OUT</b>\n\n'
        f'<b>{name}</b>\n'
        f'Supply: {supply_str}'
    )


def _format_floor_pump_alert(mint: dict, old_floor: float, new_floor: float) -> str:
    name = _esc(mint.get('name', 'Unknown'))
    pct = int(((new_floor - old_floor) / old_floor) * 100) if old_floor else 0
    return (
        f'🚀 <b>FLOOR PUMP</b>\n\n'
        f'<b>{name}</b>\n'
        f'Old Floor: {old_floor:.4f} ETH\n'
        f'New Floor: {new_floor:.4f} ETH\n'
        f'+{pct}%'
    )


def _format_sweep_alert(mint: dict, count: int) -> str:
    name = _esc(mint.get('name', 'Unknown'))
    return (
        f'⚡ <b>BIG SWEEP</b>\n\n'
        f'<b>{name}</b>\n'
        f'{count} NFTs bought in 1 minute'
    )


def _build_links_html(mint: dict) -> str:
    parts = []
    mint_link = mint.get('mint_link', '')
    twitter = mint.get('x_link', '')
    discord = mint.get('discord_link', '')
    if mint_link:
        parts.append(f'<a href="{mint_link}">Mint</a>')
    if twitter:
        parts.append(f'<a href="{twitter}">Twitter</a>')
    if discord:
        parts.append(f'<a href="{discord}">Discord</a>')
    return ' | '.join(parts)


# ── MAIN ALERT CHECKER ───────────────────────────────────────

async def check_and_send_alerts():
    now = datetime.datetime.utcnow()
    mints = get_all_mints()

    from collections import defaultdict
    pre_buckets = defaultdict(list)
    live_buckets = defaultdict(list)

    for mint in mints:
        if mint.get('paused'):
            continue
        if mint.get('status') == 'sold_out':
            continue

        for phase in mint.get('phases', []):
            phase_time_str = phase.get('time', '')
            if not phase_time_str:
                continue
            phase_time = parse_phase_time(phase_time_str)
            if not phase_time:
                continue

            phase_name = phase.get('name', 'Phase')
            time_diff = (phase_time - now).total_seconds() / 60

            # 15-min pre-alert
            if 0 < time_diff <= ALERT_MINUTES_BEFORE:
                alert_key = f"pre_{ALERT_MINUTES_BEFORE}min"
                if not alert_already_sent(mint['id'], phase_name, alert_key):
                    for ch_id in get_alert_channels_for_mint(mint):
                        pre_buckets[ch_id].append(mint)
                    mark_alert_sent(mint['id'], phase_name, alert_key)

            # Live alert
            if -2 <= time_diff <= 2:
                if not alert_already_sent(mint['id'], phase_name, 'live'):
                    for ch_id in get_alert_channels_for_mint(mint):
                        live_buckets[ch_id].append(mint)
                    mark_alert_sent(mint['id'], phase_name, 'live')
                    update_mint(mint['id'], status='live')

            # Sold-out check
            if time_diff < -5:
                if not alert_already_sent(mint['id'], phase_name, 'soldout_check'):
                    mark_alert_sent(mint['id'], phase_name, 'soldout_check')
                    asyncio.ensure_future(_check_sold_out(mint, phase))

    for ch_id, mints_list in pre_buckets.items():
        seen = set()
        unique = [m for m in mints_list if not (m['id'] in seen or seen.add(m['id']))]
        for mint in unique:
            try:
                msg = _format_mint_alert(mint, alert_type='pre')
                await _app.bot.send_message(
                    chat_id=ch_id, text=msg, parse_mode='HTML',
                    disable_web_page_preview=True
                )
                logger.info(f"Pre-mint alert sent to {ch_id} for {mint['name']}")
            except Exception as e:
                logger.error(f"Pre alert send error {ch_id}: {e}")

    for ch_id, mints_list in live_buckets.items():
        seen = set()
        unique = [m for m in mints_list if not (m['id'] in seen or seen.add(m['id']))]
        for mint in unique:
            try:
                msg = _format_mint_alert(mint, alert_type='live')
                await _app.bot.send_message(
                    chat_id=ch_id, text=msg, parse_mode='HTML',
                    disable_web_page_preview=True
                )
                logger.info(f"Live alert sent to {ch_id} for {mint['name']}")
            except Exception as e:
                logger.error(f"Live alert send error {ch_id}: {e}")


# ── SOLD OUT ─────────────────────────────────────────────────

async def _check_sold_out(mint: dict, phase: dict):
    try:
        minted = mint.get('minted', 0) or 0
        total_supply = mint.get('total_supply', 0) or 0
        if total_supply and minted >= total_supply:
            await _trigger_sold_out(mint, phase)
            return

        from utils.parser import check_mint_status
        status = await check_mint_status(mint)
        if status.get('sold_out'):
            # Update minted count in DB so the alert shows correct supply
            actual_minted = status.get('minted', 0)
            actual_supply = status.get('total', total_supply)
            if actual_minted:
                update_mint(mint['id'], minted=actual_minted)
                mint = dict(mint)
                mint['minted'] = actual_minted
                mint['total_supply'] = actual_supply
            await _trigger_sold_out(mint, phase)
    except Exception as e:
        logger.debug(f"Sold-out check failed for {mint.get('name')}: {e}")


async def _trigger_sold_out(mint: dict, phase: dict):
    if alert_already_sent(mint['id'], phase.get('name', ''), 'sold_out'):
        return
    mark_alert_sent(mint['id'], phase.get('name', ''), 'sold_out')
    update_mint(mint['id'], status='sold_out')
    msg = _format_sold_out_alert(mint)
    for ch_id in get_alert_channels_for_mint(mint):
        try:
            await _app.bot.send_message(
                chat_id=ch_id, text=msg, parse_mode='HTML',
                disable_web_page_preview=True
            )
            logger.info(f"Sold-out alert sent to {ch_id} for {mint['name']}")
        except Exception as e:
            logger.error(f"Sold-out alert error {ch_id}: {e}")


async def send_sold_out_alert_manual(mint_id: int):
    from database import get_mint
    mint = get_mint(mint_id)
    if not mint:
        return
    phase = (mint.get('phases') or [{}])[0]
    await _trigger_sold_out(mint, phase)


# ── FLOOR MONITOR + SWEEP DETECTION ─────────────────────────

async def check_floor_and_sweeps():
    mints = get_all_mints()
    cleanup_old_sweep_events(SWEEP_WINDOW_SECONDS * 10)

    for mint in mints:
        if mint.get('paused'):
            continue
        # Monitor floor/sweeps for live and sold_out mints
        if mint.get('status') not in ('live', 'sold_out'):
            continue

        try:
            await _check_floor(mint)
        except Exception as e:
            logger.debug(f"Floor check error for {mint.get('name')}: {e}")

        try:
            await _check_sweep(mint)
        except Exception as e:
            logger.debug(f"Sweep check error for {mint.get('name')}: {e}")


async def _check_floor(mint: dict):
    try:
        from utils.parser import get_floor_price
        floor = await get_floor_price(mint)
        if floor is None:
            return

        floor = float(floor)
        old_floor = get_last_floor_price(mint['id'])
        record_floor_price(mint['id'], floor)

        if old_floor is None:
            return

        if old_floor > 0 and (floor - old_floor) / old_floor >= FLOOR_PUMP_THRESHOLD:
            alert_key = f"floor_pump_{floor:.6f}"
            if not alert_already_sent(mint['id'], 'floor', alert_key):
                mark_alert_sent(mint['id'], 'floor', alert_key)
                msg = _format_floor_pump_alert(mint, old_floor, floor)
                for ch_id in get_alert_channels_for_mint(mint):
                    try:
                        await _app.bot.send_message(
                            chat_id=ch_id, text=msg, parse_mode='HTML',
                            disable_web_page_preview=True
                        )
                        logger.info(f"Floor pump alert sent for {mint['name']}")
                    except Exception as e:
                        logger.error(f"Floor alert error {ch_id}: {e}")
    except Exception as e:
        logger.debug(f"Floor price fetch error for {mint.get('name')}: {e}")


async def _check_sweep(mint: dict):
    try:
        from utils.parser import get_recent_sales_count
        count = await get_recent_sales_count(mint, SWEEP_WINDOW_SECONDS)
        if count is None:
            return

        if count >= SWEEP_COUNT_THRESHOLD:
            now_bucket = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            alert_key = f"sweep_{now_bucket}"
            if not alert_already_sent(mint['id'], 'sweep', alert_key):
                mark_alert_sent(mint['id'], 'sweep', alert_key)
                msg = _format_sweep_alert(mint, count)
                for ch_id in get_alert_channels_for_mint(mint):
                    try:
                        await _app.bot.send_message(
                            chat_id=ch_id, text=msg, parse_mode='HTML',
                            disable_web_page_preview=True
                        )
                        logger.info(f"Big sweep alert sent for {mint['name']}")
                    except Exception as e:
                        logger.error(f"Sweep alert error {ch_id}: {e}")
    except Exception as e:
        logger.debug(f"Sweep check error for {mint.get('name')}: {e}")


# ── DAILY SUMMARY ────────────────────────────────────────────

async def send_daily_summary():
    if not _app:
        return
    from utils.formatter import format_daily_summary
    mints_today = get_todays_mints()

    def _pt(mp):
        try:
            return datetime.datetime.strptime(mp[1].get('time', ''), "%Y-%m-%d %H:%M")
        except Exception:
            return datetime.datetime(9999, 1, 1)

    mints_today = sorted(mints_today, key=_pt)
    msg = format_daily_summary(mints_today)

    for ch in get_channels():
        if ch.get('receive_summary'):
            try:
                await _app.bot.send_message(
                    chat_id=ch['channel_id'], text=msg,
                    parse_mode='HTML', disable_web_page_preview=True
                )
            except Exception as e:
                logger.error(f"Daily summary failed for {ch['channel_id']}: {e}")


# ── HELPERS ──────────────────────────────────────────────────

def get_alert_channels_for_mint(mint: dict) -> list:
    mint_channels = mint.get('alert_channels', [])
    if mint_channels:
        return mint_channels
    return [ch['channel_id'] for ch in get_channels() if ch.get('receive_alerts')]


def parse_phase_time(time_str: str):
    import re
    formats = [
        "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M"
    ]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(time_str.strip(), fmt)
        except ValueError:
            continue
    m = re.match(r'^(\d{1,2}):(\d{2})$', time_str.strip())
    if m:
        now = datetime.datetime.utcnow()
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)),
                           second=0, microsecond=0)
    return None


# ── API TRIGGER ──────────────────────────────────────────────

async def trigger_mint_alert_from_api(mint: dict):
    """Called by the API endpoint when PC scraper POSTs mint data."""
    if not _app:
        logger.warning("_app not ready for API-triggered alert")
        return

    now = datetime.datetime.utcnow()
    channels = get_alert_channels_for_mint(mint)

    # Determine alert type from first phase time
    alert_type = 'pre'
    for phase in mint.get('phases', []):
        phase_time = parse_phase_time(phase.get('time', ''))
        if phase_time:
            time_diff = (phase_time - now).total_seconds() / 60
            if time_diff <= 2:
                alert_type = 'live'
            break

    msg = _format_mint_alert(mint, alert_type=alert_type)
    for ch_id in channels:
        try:
            await _app.bot.send_message(
                chat_id=ch_id, text=msg, parse_mode='HTML',
                disable_web_page_preview=True
            )
            logger.info(f"[api] Alert sent to {ch_id} for {mint.get('name')}")
        except Exception as e:
            logger.error(f"[api] Alert error {ch_id}: {e}")
