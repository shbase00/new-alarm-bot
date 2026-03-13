"""
OpenSea Playwright Scraper
Loads page as real browser, waits for JS to fill in the mint schedule times,
then extracts phases. Falls back to static HTML parse if JS times not found.
"""
import re
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

MONTHS = {
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,
    'sep':9,'oct':10,'nov':11,'dec':12,
}

def _parse_opensea_time(raw: str) -> str:
    if not raw: return ""
    raw = raw.strip()
    # Handle ISO strings directly (e.g. "2024-03-14T18:00:00Z")
    if re.match(r'^\d{4}-\d{2}-\d{2}T', raw):
        from utils.parser import iso_to_str
        result = iso_to_str(raw)
        if result: return result
    # Handle unix timestamps
    if re.match(r'^\d{10,13}$', raw):
        from utils.parser import ts_to_str
        result = ts_to_str(int(raw))
        if result: return result
    tz_offset = 0
    tz_match = re.search(r'(?:GMT|UTC)([+-]\d+)', raw, re.IGNORECASE)
    if tz_match:
        tz_offset = int(tz_match.group(1))
    clean = re.sub(r'\s*(?:GMT|UTC)[+-]\d+', '', raw, flags=re.IGNORECASE).strip()
    m = re.search(
        r'([A-Za-z]+)\s+(\d{1,2})(?:,?\s*(\d{4}))?\s+(?:at\s+)?(\d{1,2}:\d{2})\s*(AM|PM)?',
        clean, re.IGNORECASE
    )
    if m:
        month_str = m.group(1).lower()[:3]
        day   = int(m.group(2))
        year  = int(m.group(3)) if m.group(3) else datetime.utcnow().year
        tpart = m.group(4)
        ampm  = (m.group(5) or '').upper()
        month = MONTHS.get(month_str)
        if month:
            hour, minute = map(int, tpart.split(':'))
            if ampm == 'PM' and hour != 12: hour += 12
            elif ampm == 'AM' and hour == 12: hour = 0
            try:
                dt = datetime(year, month, day, hour, minute)
                utc_hour = dt.hour - tz_offset
                if utc_hour < 0:
                    dt = dt - timedelta(days=1)
                    dt = dt.replace(hour=(utc_hour % 24))
                elif utc_hour >= 24:
                    dt = dt + timedelta(days=1)
                    dt = dt.replace(hour=(utc_hour % 24))
                else:
                    dt = dt.replace(hour=utc_hour)
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    return ""


async def scrape_opensea_phases(url: str) -> dict:
    result = {'name': '', 'chain': 'Ethereum', 'phases': [], 'success': False, 'error': None}

    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        result['error'] = 'playwright_not_installed'
        return result

    try:
        import os as _os
        _proxy_url = _os.environ.get('PLAYWRIGHT_PROXY', '')

        async with async_playwright() as pw:
            _launch_opts = dict(
                headless=True,
                args=[
                    '--no-sandbox', '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage', '--disable-gpu',
                    '--disable-software-rasterizer', '--disable-extensions',
                    '--single-process', '--no-zygote',
                    '--disable-background-networking', '--disable-default-apps',
                    '--disable-sync', '--mute-audio', '--no-first-run',
                ],
            )
            if _proxy_url:
                _launch_opts['proxy'] = {'server': _proxy_url}
                logger.info(f"Using proxy: {_proxy_url[:30]}...")

            browser  = await pw.chromium.launch(**_launch_opts)
            context  = await browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                ),
                viewport={'width': 1280, 'height': 900},
                locale='en-US',
            )
            page = await context.new_page()
            await page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,mp4,mp3}",
                             lambda r: r.abort())

            logger.info(f"Playwright: loading {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            # ── Get collection name ──
            try:
                name_el = await page.wait_for_selector('h1', timeout=8000)
                if name_el:
                    raw_name = (await name_el.inner_text()).strip()
                    result['name'] = ' '.join(raw_name.split())
            except Exception:
                pass

            # ── Wait for Mint schedule section ──
            schedule_found = False
            for selector in [
                'text=Mint schedule', 'text=Mint Schedule', 'text=MINT SCHEDULE',
                'ol li', 'text=Starts:', 'text=TEAM', 'text=GTD', 'text=FCFS',
                'text=Public stage', 'text=Whitelist',
            ]:
                try:
                    await page.wait_for_selector(selector, timeout=8000)
                    schedule_found = True
                    logger.info(f"Schedule marker found: {selector}")
                    break
                except PWTimeout:
                    continue

            # Wait for times to load — look for a month name in the schedule
            time_loaded = False
            for month in ['January','February','March','April','May','June',
                          'July','August','September','October','November','December']:
                try:
                    await page.wait_for_selector(f'text={month}', timeout=6000)
                    time_loaded = True
                    logger.info(f"Times loaded (found month: {month})")
                    break
                except PWTimeout:
                    continue
            if not time_loaded:
                logger.warning("Times not loaded via JS — will parse without times")
                await page.wait_for_timeout(3000)

            # ── Extract page text and parse ──
            page_text = await page.evaluate("function(){ return document.body ? document.body.innerText : ''; }")

            # ── Also try to get structured data from the page via JS ──
            schedule_data = await page.evaluate("""function() {
                var results = [];
                // Find the ordered list inside Mint schedule
                var headers = document.querySelectorAll('*');
                var scheduleEl = null;
                for (var i = 0; i < headers.length; i++) {
                    var t = (headers[i].textContent || '').trim();
                    if (t === 'Mint schedule' || t === 'MINT SCHEDULE') {
                        scheduleEl = headers[i];
                        break;
                    }
                }
                if (scheduleEl) {
                    var parent = scheduleEl.parentElement;
                    for (var up = 0; up < 5; up++) {
                        if (!parent) break;
                        var ol = parent.querySelector('ol');
                        if (ol) {
                            var items = ol.querySelectorAll('li');
                            for (var j = 0; j < items.length; j++) {
                                results.push(items[j].innerText || items[j].textContent || '');
                            }
                            break;
                        }
                        parent = parent.parentElement;
                    }
                }
                // Fallback: grab all li elements that contain "Starts:"
                if (results.length === 0) {
                    var allLi = document.querySelectorAll('li');
                    for (var k = 0; k < allLi.length; k++) {
                        var txt = allLi[k].innerText || '';
                        if (txt.indexOf('Starts:') >= 0 || txt.indexOf('0.00') >= 0 || txt.indexOf('ETH') >= 0) {
                            results.push(txt);
                        }
                    }
                }
                return results;
            }""")

            await browser.close()

            # ── Parse phases ──
            phases = []

            # Try structured li data first
            if schedule_data:
                phases = _parse_li_items(schedule_data)

            # Fall back to full page text
            if not phases:
                phases = _parse_page_text(page_text)

            if phases:
                result['phases']  = phases
                result['success'] = True
                logger.info(f"Playwright extracted {len(phases)} phases")
            else:
                result['error'] = 'no_phases_found'
                logger.warning("Playwright: page loaded but no phases found")

    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Playwright error: {e}")

    return result


def _parse_li_items(items: list) -> list:
    """
    Parse li items from OpenSea mint schedule.
    Format: "TEAMStarts: March 14 at 1:00 PM GMT+2\n0.00 ETH\n| Limit 500 per wallet"
    OR just: "TEAMStarts: \n0.00 ETH\n| Limit 500 per wallet" (times not loaded yet)
    """
    phases = []
    # Known phase name prefixes OpenSea uses
    KNOWN_PHASES = ['TEAM', 'GTD', 'FCFS', 'PUBLIC', 'WHITELIST', 'HOLDER',
                    'OG', 'ALLOWLIST', 'AL', 'WL', 'GUARANTEED', 'FREE CLAIM']

    for item in items:
        if not item or not item.strip():
            continue

        text  = item.replace('\xa0', ' ').strip()
        lines = [l.strip() for l in text.replace('\n', '\n').split('\n') if l.strip()]
        if not lines:
            continue

        phase = {'name': '', 'time': '', 'end_time': '', 'price': 'Free', 'limit': 'N/A'}

        # The first line is usually "NAMEStarts: date" joined together
        # e.g. "TEAMStarts: March 14 at 1:00 PM GMT+2"
        first = lines[0]

        # Extract name — it's the part before "Starts:"
        starts_idx = first.lower().find('starts:')
        if starts_idx >= 0:
            phase['name'] = first[:starts_idx].strip()
            time_raw = first[starts_idx+7:].strip()
            phase['time'] = _parse_opensea_time(time_raw)
        else:
            # Name is the whole first line if no "Starts:" yet
            phase['name'] = first

        # Clean up name
        if not phase['name']:
            phase['name'] = 'Phase'

        # Normalize name
        name_up = phase['name'].upper()
        for known in KNOWN_PHASES:
            if known in name_up:
                phase['name'] = known.title() if known not in ('GTD','FCFS','OG','WL','AL') else known
                break

        # Parse remaining lines
        price_num = None
        for line in lines[1:]:
            ll = line.lower()
            if ll.startswith('starts:'):
                phase['time'] = _parse_opensea_time(line[7:].strip())
            elif ll.startswith('ends:'):
                phase['end_time'] = _parse_opensea_time(line[5:].strip())
            # Handle "0.08 ETH" or "1.5 SOL" on a single line
            elif re.match(r'^[\d.]+\s*(eth|sol|matic|bnb)', ll):
                m_price = re.match(r'^([\d.]+)\s*(\w+)', ll)
                if m_price:
                    val = float(m_price.group(1))
                    cur = m_price.group(2).upper()
                    phase['price'] = 'Free' if val == 0 else f"{m_price.group(1)} {cur}"
            # Handle price split across two lines: "0.08" then "ETH"
            elif re.match(r'^[\d.]+$', line.strip()):
                price_num = line.strip()
            elif ('eth' in ll or 'sol' in ll) and price_num is not None:
                val = float(price_num) if price_num else 0
                cur = 'SOL' if 'sol' in ll else 'ETH'
                phase['price'] = 'Free' if val == 0 else f"{price_num} {cur}"
                price_num = None
            elif 'free' in ll:
                phase['price'] = 'Free'
            else:
                m = re.search(r'limit\s+(\d+)\s+per', line, re.IGNORECASE)
                if m:
                    phase['limit'] = m.group(1)

        phases.append(phase)

    return phases


def _parse_page_text(text: str) -> list:
    """Parse full page text — find Mint schedule section."""
    if not text:
        return []

    # Find schedule section
    for marker in ['Mint schedule', 'MINT SCHEDULE', 'Mint Schedule']:
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx:idx+3000]
            break

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    phases = []
    i = 0

    while i < len(lines):
        line = lines[i]
        # Detect "NAMEStarts:" pattern
        starts_m = re.search(r'(.+?)Starts:\s*(.*)', line, re.IGNORECASE)
        if starts_m:
            name     = starts_m.group(1).strip()
            time_raw = starts_m.group(2).strip()
            phase    = {'name': name or 'Phase', 'time': _parse_opensea_time(time_raw),
                        'end_time': '', 'price': 'Free', 'limit': 'N/A'}
            price_num = None
            for j in range(i+1, min(i+8, len(lines))):
                l  = lines[j]
                ll = l.lower()
                if re.search(r'starts:', ll): break
                if re.match(r'^[\d.]+\s*(eth|sol|matic)', ll):
                    m_p = re.match(r'^([\d.]+)\s*(\w+)', ll)
                    if m_p:
                        val = float(m_p.group(1))
                        cur = m_p.group(2).upper()
                        phase['price'] = 'Free' if val == 0 else f"{m_p.group(1)} {cur}"
                elif re.match(r'^[\d.]+$', l): price_num = l
                elif ('eth' in ll or 'sol' in ll) and price_num is not None:
                    val = float(price_num) if price_num else 0
                    cur = 'SOL' if 'sol' in ll else 'ETH'
                    phase['price'] = 'Free' if val == 0 else f"{price_num} {cur}"
                    price_num = None
                elif 'ends:' in ll:
                    phase['end_time'] = _parse_opensea_time(l.split('Ends:',1)[-1].strip())
                else:
                    m = re.search(r'limit\s+(\d+)\s+per', l, re.IGNORECASE)
                    if m: phase['limit'] = m.group(1)
            phases.append(phase)
        i += 1

    return phases
