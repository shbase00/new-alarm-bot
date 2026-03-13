"""
PC Scrape Server
----------------
Runs on your PC and exposes a /scrape endpoint.
Railway calls this when OpenSea blocks it (403).

Setup:
  1. pip install aiohttp playwright
  2. playwright install chromium
  3. Install ngrok: https://ngrok.com/download
  4. Run this script: python pc_server.py
  5. In a second terminal: ngrok http 7842
  6. Copy the ngrok URL (e.g. https://abc123.ngrok-free.app)
  7. Set PC_SCRAPER_URL=https://abc123.ngrok-free.app in Railway env vars
  8. That's it — your bot will auto-call this server when needed

Security: requests must include X-API-Key matching API_SECRET_KEY below.
"""
import asyncio
import re
import logging
from datetime import datetime, timezone
from aiohttp import web

# ── CONFIG — must match Railway env vars ─────────────────────
API_SECRET_KEY = "9fA7K2xQ4Lm8TzR6Wb1H"
PORT           = 7842
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

MONTHS = {
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,
    'sep':9,'oct':10,'nov':11,'dec':12,
}

# ── TIME PARSER ───────────────────────────────────────────────

def _parse_time(raw: str) -> str:
    if not raw:
        return ""
    raw = raw.strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"
    if re.match(r'^\d{10,13}$', raw):
        ts = int(raw[:10])
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M")
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
        month_s = m.group(1).lower()[:3]
        day     = int(m.group(2))
        year    = int(m.group(3)) if m.group(3) else datetime.now(timezone.utc).year
        tpart   = m.group(4)
        ampm    = (m.group(5) or '').upper()
        month   = MONTHS.get(month_s)
        if month:
            hour, minute = map(int, tpart.split(':'))
            if ampm == 'PM' and hour != 12: hour += 12
            elif ampm == 'AM' and hour == 12: hour = 0
            try:
                utc_hour = hour - tz_offset
                day_off  = 0
                if utc_hour < 0:    day_off = -1; utc_hour %= 24
                elif utc_hour >= 24: day_off = 1;  utc_hour %= 24
                dt = datetime(year, month, day + day_off, utc_hour, minute)
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    return ""


# ── PHASE PARSERS ─────────────────────────────────────────────

def _parse_li_items(items: list) -> list:
    KNOWN = ['TEAM','GTD','FCFS','PUBLIC','WHITELIST','HOLDER','OG',
             'ALLOWLIST','AL','WL','GUARANTEED','FREE CLAIM']
    phases = []
    for item in items:
        if not item or not item.strip():
            continue
        text  = item.replace('\xa0', ' ').strip()
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines:
            continue
        phase = {'name': 'Phase', 'time': '', 'price': 'Free', 'limit': 'N/A'}
        first = lines[0]
        si = first.lower().find('starts:')
        if si >= 0:
            phase['name'] = first[:si].strip() or 'Phase'
            phase['time'] = _parse_time(first[si+7:].strip())
        else:
            phase['name'] = first
        name_up = phase['name'].upper()
        for k in KNOWN:
            if k in name_up:
                phase['name'] = k.title() if k not in ('GTD','FCFS','OG','WL','AL') else k
                break
        price_num = None
        for line in lines[1:]:
            ll = line.lower()
            if ll.startswith('starts:'):
                phase['time'] = _parse_time(line[7:].strip())
            elif re.match(r'^[\d.]+\s*(eth|sol|matic|bnb)', ll):
                mp = re.match(r'^([\d.]+)\s*(\w+)', ll)
                if mp:
                    val = float(mp.group(1))
                    phase['price'] = 'Free' if val == 0 else f"{mp.group(1)} {mp.group(2).upper()}"
            elif re.match(r'^[\d.]+$', line.strip()):
                price_num = line.strip()
            elif ('eth' in ll or 'sol' in ll) and price_num is not None:
                val = float(price_num) if price_num else 0
                phase['price'] = 'Free' if val == 0 else f"{price_num} {'SOL' if 'sol' in ll else 'ETH'}"
                price_num = None
            elif 'free' in ll:
                phase['price'] = 'Free'
            else:
                mm = re.search(r'limit\s+(\d+)\s+per', line, re.IGNORECASE)
                if mm: phase['limit'] = mm.group(1)
        phases.append(phase)
    return phases


def _parse_page_text(text: str) -> list:
    if not text:
        return []
    for marker in ['Mint schedule', 'MINT SCHEDULE', 'Mint Schedule']:
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx:idx+3000]
            break
    lines  = [l.strip() for l in text.split('\n') if l.strip()]
    phases = []
    for i, line in enumerate(lines):
        m = re.search(r'(.+?)Starts:\s*(.*)', line, re.IGNORECASE)
        if m:
            phase = {
                'name':  m.group(1).strip() or 'Phase',
                'time':  _parse_time(m.group(2).strip()),
                'price': 'Free',
                'limit': 'N/A',
            }
            price_num = None
            for j in range(i+1, min(i+8, len(lines))):
                l  = lines[j]
                ll = l.lower()
                if re.search(r'starts:', ll): break
                if re.match(r'^[\d.]+\s*(eth|sol|matic)', ll):
                    mp = re.match(r'^([\d.]+)\s*(\w+)', ll)
                    if mp:
                        val = float(mp.group(1))
                        phase['price'] = 'Free' if val == 0 else f"{mp.group(1)} {mp.group(2).upper()}"
                elif re.match(r'^[\d.]+$', l): price_num = l
                elif ('eth' in ll or 'sol' in ll) and price_num is not None:
                    val = float(price_num) if price_num else 0
                    phase['price'] = 'Free' if val == 0 else f"{price_num} {'SOL' if 'sol' in ll else 'ETH'}"
                    price_num = None
                else:
                    mm = re.search(r'limit\s+(\d+)\s+per', l, re.IGNORECASE)
                    if mm: phase['limit'] = mm.group(1)
            phases.append(phase)
    return phases


# ── PLAYWRIGHT SCRAPER ────────────────────────────────────────

async def _scrape(url: str) -> dict:
    result = {
        'name': '', 'chain': 'Ethereum', 'phases': [],
        'total_supply': 0, 'twitter': '', 'discord': '',
        'success': False, 'error': None,
    }
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        result['error'] = 'playwright_not_installed'
        return result

    logger.info(f"Scraping: {url}")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=['--no-sandbox','--disable-setuid-sandbox',
                      '--disable-dev-shm-usage','--disable-gpu',
                      '--disable-extensions','--no-first-run','--mute-audio'],
            )
            context = await browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                ),
                viewport={'width': 1280, 'height': 900},
                locale='en-US',
            )
            page = await context.new_page()
            await page.route(
                "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,mp4,mp3}",
                lambda r: r.abort()
            )
            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            # Collection name
            try:
                el = await page.wait_for_selector('h1', timeout=8000)
                if el:
                    result['name'] = ' '.join((await el.inner_text()).strip().split())
            except Exception:
                pass

            # Wait for schedule section
            for selector in [
                'text=Mint schedule','text=Mint Schedule','ol li',
                'text=Starts:','text=TEAM','text=GTD','text=FCFS',
                'text=Public stage','text=Whitelist',
            ]:
                try:
                    await page.wait_for_selector(selector, timeout=8000)
                    logger.info(f"Schedule found via: {selector}")
                    break
                except PWTimeout:
                    continue

            # Wait for JS-rendered times
            for month in ['January','February','March','April','May','June',
                          'July','August','September','October','November','December']:
                try:
                    await page.wait_for_selector(f'text={month}', timeout=5000)
                    logger.info(f"Times loaded (month: {month})")
                    break
                except PWTimeout:
                    continue
            else:
                await page.wait_for_timeout(3000)

            page_text = await page.evaluate(
                "function(){ return document.body ? document.body.innerText : ''; }"
            )

            # Structured li extraction
            schedule_data = await page.evaluate("""function() {
                var results = [];
                var headers = document.querySelectorAll('*');
                var scheduleEl = null;
                for (var i = 0; i < headers.length; i++) {
                    var t = (headers[i].textContent || '').trim();
                    if (t === 'Mint schedule' || t === 'MINT SCHEDULE') {
                        scheduleEl = headers[i]; break;
                    }
                }
                if (scheduleEl) {
                    var parent = scheduleEl.parentElement;
                    for (var up = 0; up < 5; up++) {
                        if (!parent) break;
                        var ol = parent.querySelector('ol');
                        if (ol) {
                            var items = ol.querySelectorAll('li');
                            for (var j = 0; j < items.length; j++)
                                results.push(items[j].innerText || items[j].textContent || '');
                            break;
                        }
                        parent = parent.parentElement;
                    }
                }
                if (results.length === 0) {
                    var allLi = document.querySelectorAll('li');
                    for (var k = 0; k < allLi.length; k++) {
                        var txt = allLi[k].innerText || '';
                        if (txt.indexOf('Starts:') >= 0 || txt.indexOf('ETH') >= 0)
                            results.push(txt);
                    }
                }
                return results;
            }""")

            # Supply
            supply_m = re.search(r'(\d[\d,]+)\s+(?:items?|supply|total)', page_text, re.IGNORECASE)
            if supply_m:
                result['total_supply'] = int(supply_m.group(1).replace(',', ''))

            # Social links
            links = await page.evaluate("""function(){
                var hrefs = [];
                document.querySelectorAll('a[href]').forEach(function(a){ hrefs.push(a.href); });
                return hrefs;
            }""")
            for link in links:
                if ('twitter.com' in link or 'x.com' in link) and '/status/' not in link:
                    if not result['twitter']: result['twitter'] = link
                elif 'discord.gg' in link or 'discord.com/invite' in link:
                    if not result['discord']: result['discord'] = link

            await browser.close()

            phases = _parse_li_items(schedule_data) if schedule_data else []
            if not phases:
                phases = _parse_page_text(page_text)

            if phases:
                result['phases']  = phases
                result['success'] = True
                logger.info(f"Done: {result['name']} — {len(phases)} phase(s)")
            else:
                result['error'] = 'no_phases_found'
                logger.warning(f"No phases found for: {url}")

    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Scrape error: {e}")

    return result


# ── HTTP HANDLERS ─────────────────────────────────────────────

async def handle_scrape(request: web.Request) -> web.Response:
    # Auth check
    if API_SECRET_KEY:
        if request.headers.get('X-API-Key', '') != API_SECRET_KEY:
            return web.json_response({'error': 'unauthorized'}, status=401)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({'error': 'invalid JSON'}, status=400)

    url = body.get('url', '').strip()
    if not url:
        return web.json_response({'error': 'missing url'}, status=400)

    result = await _scrape(url)
    return web.json_response(result)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({'status': 'ok', 'service': 'pc-scraper'})


# ── STARTUP ───────────────────────────────────────────────────

async def main():
    app = web.Application()
    app.router.add_post('/scrape', handle_scrape)
    app.router.add_get('/health', handle_health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    logger.info(f"")
    logger.info(f"  ✅ PC Scraper Server running on port {PORT}")
    logger.info(f"")
    logger.info(f"  Next steps:")
    logger.info(f"  1. Open a NEW terminal and run: ngrok http {PORT}")
    logger.info(f"  2. Copy the ngrok URL (e.g. https://abc123.ngrok-free.app)")
    logger.info(f"  3. In Railway → Variables → add:")
    logger.info(f"     PC_SCRAPER_URL = https://abc123.ngrok-free.app")
    logger.info(f"  4. Redeploy Railway — done!")
    logger.info(f"")
    logger.info(f"  Waiting for requests from Railway bot...")
    logger.info(f"")

    # Keep running
    while True:
        await asyncio.sleep(3600)


if __name__ == '__main__':
    asyncio.run(main())
