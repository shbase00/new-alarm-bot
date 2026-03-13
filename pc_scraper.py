"""
PC Mint Scraper
---------------
Paste any mint URL and this script will:
  1. OpenSea links  → scrape phases on YOUR PC (avoids Railway 403 blocks)
  2. Other links    → try to scrape on your PC first, then fall back to Railway
  3. If both fail   → prompt you to enter phases manually

Requirements:
  pip install aiohttp playwright
  playwright install chromium

Usage:
  python pc_scraper.py
  python pc_scraper.py https://opensea.io/collection/my-collection
"""
import asyncio
import re
import sys
from datetime import datetime, timezone

# ── CONFIG ───────────────────────────────────────────────────
BOT_API_URL    = "https://mint-alarm-production.up.railway.app"
API_SECRET_KEY = "9fA7K2xQ4Lm8TzR6Wb1H"
# ─────────────────────────────────────────────────────────────

MONTHS = {
    'january':1,'february':2,'march':3,'april':4,'may':5,'june':6,
    'july':7,'august':8,'september':9,'october':10,'november':11,'december':12,
    'jan':1,'feb':2,'mar':3,'apr':4,'jun':6,'jul':7,'aug':8,
    'sep':9,'oct':10,'nov':11,'dec':12,
}

# ── HELPERS ──────────────────────────────────────────────────

def _parse_time(raw: str) -> str:
    """Parse a human-readable or ISO time string to 'YYYY-MM-DD HH:MM' UTC."""
    if not raw:
        return ""
    raw = raw.strip()

    # ISO: 2024-03-14T18:00:00Z
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})', raw)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)} {m.group(4)}:{m.group(5)}"

    # Unix timestamp
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
                if utc_hour < 0:   day_off = -1; utc_hour %= 24
                elif utc_hour >= 24: day_off = 1; utc_hour %= 24
                dt = datetime(year, month, day + day_off, utc_hour, minute)
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    return ""


def _parse_li_items(items: list) -> list:
    KNOWN = ['TEAM','GTD','FCFS','PUBLIC','WHITELIST','HOLDER','OG','ALLOWLIST','AL','WL','GUARANTEED','FREE CLAIM']
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


# ── SCRAPERS ─────────────────────────────────────────────────

async def scrape_with_playwright(url: str) -> dict:
    """Scrape any mint page using Playwright on this PC."""
    result = {'name': '', 'chain': 'Ethereum', 'phases': [], 'total_supply': 0,
              'twitter': '', 'discord': '', 'success': False, 'error': None}
    try:
        from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    except ImportError:
        result['error'] = 'playwright_not_installed'
        print("  ⚠  Playwright not installed. Run: pip install playwright && playwright install chromium")
        return result

    print(f"  🌐 Opening browser for: {url}")
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=['--no-sandbox','--disable-setuid-sandbox','--disable-dev-shm-usage',
                      '--disable-gpu','--disable-extensions','--no-first-run','--mute-audio'],
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
            # Skip images/fonts to speed things up
            await page.route("**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,ttf,mp4,mp3}",
                             lambda r: r.abort())

            await page.goto(url, wait_until='domcontentloaded', timeout=60000)

            # ── Collection name ──
            try:
                el = await page.wait_for_selector('h1', timeout=8000)
                if el:
                    result['name'] = ' '.join((await el.inner_text()).strip().split())
            except Exception:
                pass

            # ── Wait for schedule ──
            for selector in ['text=Mint schedule','text=Mint Schedule','ol li',
                             'text=Starts:','text=TEAM','text=GTD','text=FCFS',
                             'text=Public stage','text=Whitelist']:
                try:
                    await page.wait_for_selector(selector, timeout=8000)
                    print(f"  ✅ Schedule found")
                    break
                except PWTimeout:
                    continue

            # Wait for month names (JS-rendered times)
            for month in ['January','February','March','April','May','June',
                          'July','August','September','October','November','December']:
                try:
                    await page.wait_for_selector(f'text={month}', timeout=5000)
                    print(f"  ✅ Times loaded")
                    break
                except PWTimeout:
                    continue
            else:
                await page.wait_for_timeout(3000)

            page_text = await page.evaluate(
                "function(){ return document.body ? document.body.innerText : ''; }"
            )

            # Try structured li extraction first
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

            # ── Extract supply ──
            supply_m = re.search(r'(\d[\d,]+)\s+(?:items?|supply|total)', page_text, re.IGNORECASE)
            if supply_m:
                result['total_supply'] = int(supply_m.group(1).replace(',', ''))

            # ── Extract social links ──
            links = await page.evaluate("""function(){
                var hrefs = [];
                document.querySelectorAll('a[href]').forEach(function(a){ hrefs.push(a.href); });
                return hrefs;
            }""")
            for link in links:
                if 'twitter.com' in link or 'x.com' in link:
                    if not result['twitter'] and '/status/' not in link:
                        result['twitter'] = link
                elif 'discord.gg' in link or 'discord.com/invite' in link:
                    if not result['discord']:
                        result['discord'] = link

            await browser.close()

            # Parse phases
            phases = _parse_li_items(schedule_data) if schedule_data else []
            if not phases:
                phases = _parse_page_text(page_text)

            if phases:
                result['phases']  = phases
                result['success'] = True
                print(f"  ✅ Found {len(phases)} phase(s): {[p['name'] for p in phases]}")
            else:
                result['error'] = 'no_phases_found'
                print("  ⚠  Page loaded but no phases detected")

    except Exception as e:
        result['error'] = str(e)
        print(f"  ❌ Browser error: {e}")

    return result


async def scrape_via_railway(url: str) -> dict:
    """Ask the Railway bot to scrape the URL for us (non-OpenSea fallback)."""
    import aiohttp
    result = {'name': '', 'chain': 'Ethereum', 'phases': [], 'success': False, 'error': None}
    try:
        headers = {"Content-Type": "application/json"}
        if API_SECRET_KEY:
            headers["X-API-Key"] = API_SECRET_KEY
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BOT_API_URL}/api/scrape",
                json={"url": url},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('phases'):
                        result.update(data)
                        result['success'] = True
                        print(f"  ✅ Railway scraped {len(data['phases'])} phase(s)")
                    else:
                        result['error'] = 'no_phases'
                        print("  ⚠  Railway returned no phases")
                else:
                    result['error'] = f"HTTP {resp.status}"
                    print(f"  ⚠  Railway scrape endpoint returned {resp.status}")
    except Exception as e:
        result['error'] = str(e)
        print(f"  ⚠  Railway scrape attempt failed: {e}")
    return result


# ── MANUAL ENTRY ─────────────────────────────────────────────

def manual_entry(url: str) -> dict:
    """Interactively prompt the user to enter mint details."""
    print("\n📝 Manual entry mode — please fill in the details:")
    print("   (Press Enter to skip optional fields)\n")

    name    = input("  Collection name: ").strip()
    chain   = input("  Chain (Ethereum/Solana/Base) [Ethereum]: ").strip() or "Ethereum"
    supply  = input("  Total supply [0]: ").strip()
    twitter = input("  Twitter URL (optional): ").strip()
    discord = input("  Discord URL (optional): ").strip()

    phases = []
    print("\n  Enter phases (leave name blank when done):")
    while True:
        print(f"\n  Phase {len(phases)+1}:")
        pname = input("    Name (e.g. WL / Public): ").strip()
        if not pname:
            if not phases:
                print("  ⚠  At least one phase is required.")
                continue
            break
        ptime = input("    Start time UTC (YYYY-MM-DD HH:MM): ").strip()
        # Accept natural format too
        if ptime and not re.match(r'^\d{4}-\d{2}-\d{2}', ptime):
            ptime = _parse_time(ptime)
        pprice = input("    Price (e.g. 0.08 ETH / Free) [Free]: ").strip() or "Free"
        plimit = input("    Limit per wallet [N/A]: ").strip() or "N/A"
        phases.append({'name': pname, 'time': ptime, 'price': pprice, 'limit': plimit})
        print(f"    ✅ Phase added: {pname} @ {ptime or 'TBA'} — {pprice}")

    return {
        'name':         name or 'Unknown Collection',
        'chain':        chain,
        'mint_link':    url,
        'twitter':      twitter,
        'discord':      discord,
        'total_supply': int(supply) if supply.isdigit() else 0,
        'minted':       0,
        'phases':       phases,
        'success':      True,
    }


# ── SEND TO BOT ──────────────────────────────────────────────

async def send_to_bot(mint_data: dict) -> bool:
    import aiohttp
    payload = {
        "name":         mint_data.get('name', 'Unknown'),
        "chain":        mint_data.get('chain', 'Ethereum'),
        "mint_link":    mint_data.get('mint_link', ''),
        "twitter":      mint_data.get('twitter', ''),
        "discord":      mint_data.get('discord', ''),
        "supply":       mint_data.get('total_supply', 0),
        "minted":       mint_data.get('minted', 0),
        "phases":       mint_data.get('phases', []),
    }
    headers = {"Content-Type": "application/json"}
    if API_SECRET_KEY:
        headers["X-API-Key"] = API_SECRET_KEY

    print(f"\n  📡 Sending to bot...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{BOT_API_URL}/api/mint",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                body = await resp.text()
                if resp.status == 200:
                    print(f"  ✅ Sent! Bot response: {body}")
                    return True
                else:
                    print(f"  ❌ Bot returned {resp.status}: {body}")
                    return False
    except Exception as e:
        print(f"  ❌ Failed to send to bot: {e}")
        return False


# ── MAIN ─────────────────────────────────────────────────────

async def process_url(url: str):
    is_opensea = 'opensea.io' in url.lower()
    mint_data  = None

    print(f"\n{'='*55}")
    print(f"  URL: {url}")
    print(f"{'='*55}")

    if is_opensea:
        # OpenSea: always use PC scraper
        print("  🔵 OpenSea link → scraping on your PC...")
        result = await scrape_with_playwright(url)
        if result['success']:
            result['mint_link'] = url
            mint_data = result
        else:
            print(f"  ❌ PC scrape failed: {result.get('error')}")

    else:
        # Other links: try PC first, then Railway
        print("  🟡 Non-OpenSea link → trying your PC first...")
        result = await scrape_with_playwright(url)
        if result['success']:
            result['mint_link'] = url
            mint_data = result
        else:
            print(f"  ⚠  PC scrape failed — trying Railway...")
            result = await scrape_via_railway(url)
            if result['success']:
                result.setdefault('mint_link', url)
                mint_data = result
            else:
                print("  ❌ Railway also failed")

    # Both failed → manual entry
    if not mint_data:
        print("\n  ⚠  Automatic detection failed.")
        choice = input("  Enter details manually? (y/n): ").strip().lower()
        if choice == 'y':
            mint_data = manual_entry(url)
        else:
            print("  Skipping this URL.")
            return

    # Preview what we're about to send
    print(f"\n  📋 Preview:")
    print(f"     Name:    {mint_data.get('name')}")
    print(f"     Chain:   {mint_data.get('chain')}")
    print(f"     Supply:  {mint_data.get('total_supply', 0)}")
    print(f"     Twitter: {mint_data.get('twitter', '—')}")
    print(f"     Discord: {mint_data.get('discord', '—')}")
    for i, p in enumerate(mint_data.get('phases', []), 1):
        print(f"     Phase {i}: {p['name']} | {p.get('time','TBA')} | {p.get('price','Free')} | Limit: {p.get('limit','N/A')}")

    confirm = input("\n  Send to bot? (y/n) [y]: ").strip().lower()
    if confirm in ('', 'y'):
        await send_to_bot(mint_data)
    else:
        print("  Cancelled.")


async def main():
    print("╔═══════════════════════════════════════════════╗")
    print("║         NFT Mint PC Scraper                   ║")
    print("╚═══════════════════════════════════════════════╝")

    # URL passed as command-line argument
    if len(sys.argv) > 1:
        urls = sys.argv[1:]
    else:
        print("\nPaste mint URL(s) below. Press Enter twice when done.\n")
        urls = []
        while True:
            line = input("  URL: ").strip()
            if not line:
                break
            urls.append(line)

    if not urls:
        print("No URLs provided. Exiting.")
        return

    for url in urls:
        if url:
            await process_url(url)

    print("\n✅ Done.")


if __name__ == "__main__":
    asyncio.run(main())
