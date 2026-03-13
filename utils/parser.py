"""
Mint Parser — OpenSea focused
- Parallel detection: API + Playwright + HTML run concurrently
- Full data extraction: name, chain, contract, phases, twitter, discord, supply
- Handles all OpenSea API v2 response shapes (drops + collections)
- Countdown timer fallback for phase times
"""
import re
import json
import asyncio
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote

logger = logging.getLogger(__name__)

try:
    import aiohttp
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

CHAIN_FROM_DOMAIN = {
    "opensea.io": "Ethereum", "zora.co": "Zora",
    "foundation.app": "Ethereum", "manifold.xyz": "Ethereum",
    "highlight.xyz": "Ethereum", "magiceden.io": "Solana",
    "sound.xyz": "Ethereum", "mint.fun": "Ethereum",
    "launchmynft.io": "Ethereum", "ordzaar.com": "Bitcoin",
}

CHAIN_NORMALIZE = {
    "ethereum": "Ethereum", "eth": "Ethereum",
    "base": "Base", "blast": "Blast", "arbitrum": "Arbitrum",
    "polygon": "Polygon", "optimism": "Optimism", "zora": "Zora",
    "solana": "Solana", "sol": "Solana", "bitcoin": "Bitcoin", "btc": "Bitcoin",
}

CHAIN_OS_SLUG = {
    'Ethereum': 'ethereum', 'Base': 'base', 'Blast': 'blast',
    'Arbitrum': 'arbitrum', 'Polygon': 'matic', 'Optimism': 'optimism', 'Zora': 'zora',
}

# ─────────────────────────────────────────────
# HTTP helper
# ─────────────────────────────────────────────

async def _get(url: str, headers: dict = None, as_json: bool = False, timeout: int = 15):
    if not HAS_AIOHTTP:
        return None
    h = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept': 'application/json' if as_json else 'text/html,application/xhtml+xml,*/*;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'Cache-Control': 'no-cache',
    }
    if headers:
        h.update(headers)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=h, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                if r.status == 200:
                    if as_json:
                        return await r.json(content_type=None)
                    return await r.text(errors='ignore')
                logger.warning(f"HTTP {r.status} for {url}")
                return None
    except Exception as e:
        logger.warning(f"Fetch error {url}: {e}")
    return None

# ─────────────────────────────────────────────
# Time helpers
# ─────────────────────────────────────────────

def ts_to_str(ts) -> str:
    try:
        ts = int(float(ts))
        if ts > 1e12: ts //= 1000
        if ts <= 0: return ""
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""

def iso_to_str(s: str) -> str:
    """Convert any ISO/datetime string to 'YYYY-MM-DD HH:MM' UTC."""
    if not s: return ""
    s = str(s).strip()
    # Try various ISO formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]:
        try:
            return datetime.strptime(s[:len(fmt)], fmt).strftime("%Y-%m-%d %H:%M")
        except Exception:
            pass
    # Try just the first 16 chars
    try:
        return datetime.strptime(s[:16], "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return ""

def parse_any_time(val) -> str:
    """Parse timestamp (int/float/ISO string) → 'YYYY-MM-DD HH:MM' UTC."""
    if not val: return ""
    if isinstance(val, (int, float)): return ts_to_str(val)
    s = str(val).strip()
    # If it looks like a unix timestamp string
    if re.match(r'^\d{10,13}$', s):
        return ts_to_str(int(s))
    return iso_to_str(s)

def _format_eth_price(price_obj) -> str:
    if not price_obj: return "Free"
    try:
        # price_obj = {'currency': 'ETH', 'value': '80000000000000000', 'decimals': 18}
        value    = price_obj.get("value") or price_obj.get("amount") or 0
        decimals = int(price_obj.get("decimals", 18))
        currency = price_obj.get("currency", "ETH")
        val      = float(value) / (10 ** decimals)
        return "Free" if val == 0 else f"{val:.6f}".rstrip("0").rstrip(".") + f" {currency}"
    except Exception:
        try:
            val = float(price_obj)
            return "Free" if val == 0 else f"{val} ETH"
        except Exception:
            return str(price_obj)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def detect_chain(url: str, text: str = "") -> str:
    domain = urlparse(url).netloc.lower().replace("www.", "")
    path   = urlparse(url).path.lower()
    for kw, chain in CHAIN_NORMALIZE.items():
        if f"/{kw}/" in path or path.endswith(f"/{kw}"):
            return chain
    for known, chain in CHAIN_FROM_DOMAIN.items():
        if known in domain:
            return chain
    for kw, chain in CHAIN_NORMALIZE.items():
        if kw in text.lower():
            return chain
    return "Unknown"

def _clean_name(name: str) -> str:
    if not name: return ""
    for s in (' | OpenSea', ' - OpenSea', ' | LaunchMyNFT', ' - Collection',
              ' | Ordzaar', ' | Foundation', ' | Manifold', ' | Zora',
              ' - Collection | OpenSea', ' Collection | OpenSea'):
        name = name.replace(s, '')
    return name.strip()

def _opensea_slug(url: str) -> str:
    try:
        path = unquote(urlparse(url).path)
        m = re.search(r'/collection/([^/?#]+)', path)
        if m: return m.group(1)
    except Exception:
        pass
    return ""

async def _os_api_key() -> str:
    try:
        from config import OPENSEA_API_KEY
        return OPENSEA_API_KEY or ""
    except Exception:
        return ""

# ─────────────────────────────────────────────
# Drops API stage extraction
# Handles ALL known OpenSea v2 response shapes
# ─────────────────────────────────────────────

def _extract_stages(data: dict) -> list:
    """
    Extract the list of mint stages from any OpenSea drops API response shape.
    Known shapes:
      1. data['mint_stages'] = [...]
      2. data['stages'] = [...]
      3. data['drop']['stages'] = [...]
      4. data['results'][0]['stages'] = [...]
      5. data['results'][0]['mint_stages'] = [...]
    """
    if not data or not isinstance(data, dict):
        return []

    # Direct keys
    for key in ('mint_stages', 'stages', 'mintStages'):
        v = data.get(key)
        if isinstance(v, list) and v:
            return v

    # Nested under 'drop'
    drop = data.get('drop') or {}
    if isinstance(drop, dict):
        for key in ('stages', 'mint_stages', 'mintStages'):
            v = drop.get(key)
            if isinstance(v, list) and v:
                return v

    # Nested under 'results'
    results = data.get('results') or []
    if isinstance(results, list) and results:
        first = results[0] if isinstance(results[0], dict) else {}
        for key in ('stages', 'mint_stages', 'mintStages'):
            v = first.get(key)
            if isinstance(v, list) and v:
                return v

    return []

def _parse_stage(s: dict) -> dict:
    """
    Parse one stage dict into our internal phase format.
    Handles both snake_case (old) and camelCase (new) field names.
    """
    # Name: 'stage', 'name', 'stageName'
    name = (s.get('stage') or s.get('name') or s.get('stageName') or 'Phase').strip()
    if name.lower() in ('public', 'publicsale', 'public_sale', 'public sale'):
        name = 'Public'
    elif name.lower() in ('presale', 'pre_sale', 'whitelist', 'wl'):
        name = 'Whitelist'
    elif name.lower() in ('team', 'teamonly'):
        name = 'Team'

    # Start time: 'start_time', 'startTime', 'start'
    start_raw = (s.get('start_time') or s.get('startTime') or s.get('start') or
                 s.get('startTimestamp') or s.get('start_timestamp') or '')
    time_str = parse_any_time(start_raw)

    # End time
    end_raw = (s.get('end_time') or s.get('endTime') or s.get('end') or
               s.get('endTimestamp') or s.get('end_timestamp') or '')
    end_str = parse_any_time(end_raw)

    # Price: 'price', 'mintPrice', 'mint_price'
    price_raw = (s.get('price') or s.get('mintPrice') or s.get('mint_price') or
                 s.get('pricePerToken') or None)
    price_str = _format_eth_price(price_raw)

    # Limit: 'limit_per_wallet', 'maxTokensPerWallet', 'max_per_wallet', 'maxPerWallet'
    limit = (s.get('limit_per_wallet') or s.get('maxTokensPerWallet') or
             s.get('max_per_wallet') or s.get('maxPerWallet') or
             s.get('limitPerWallet') or 'N/A')
    limit_str = str(limit) if limit else 'N/A'

    return {
        'name':     name,
        'time':     time_str,
        'end_time': end_str,
        'price':    price_str,
        'limit':    limit_str,
    }

# ─────────────────────────────────────────────
# DETECTOR 1: OpenSea Collections API
# ─────────────────────────────────────────────

async def _detect_via_collections_api(slug: str) -> dict:
    api_key = await _os_api_key()
    if not api_key:
        return {}

    hdrs = {'Accept': 'application/json', 'x-api-key': api_key}
    data = await _get(f"https://api.opensea.io/api/v2/collections/{slug}",
                      headers=hdrs, as_json=True, timeout=8)

    if not data or not isinstance(data, dict):
        logger.warning(f"[API:collections] No data for {slug}")
        return {}

    # Contract address
    contract  = ''
    contracts = data.get('contracts') or []
    if contracts and isinstance(contracts, list):
        contract = contracts[0].get('address', '')
    if not contract:
        contract = data.get('primary_contract', '') or data.get('contract_address', '')

    # Chain
    chain_raw = ''
    if contracts and isinstance(contracts, list):
        chain_raw = contracts[0].get('chain', '')
    if not chain_raw:
        chain_raw = data.get('chain', 'ethereum')
    chain = CHAIN_NORMALIZE.get(chain_raw.lower(), 'Ethereum')

    # Social links
    links   = data.get('links') or {}
    twitter = links.get('twitter') or data.get('twitter_username', '')
    discord = links.get('discord') or data.get('discord_url', '')
    if twitter and not twitter.startswith('http'):
        twitter = f"https://x.com/{twitter.lstrip('@')}"
    if discord and not discord.startswith('http'):
        discord = f"https://discord.gg/{discord}"

    total_supply = int(data.get('total_supply') or 0)
    name         = _clean_name(data.get('name', '') or slug.replace('-', ' ').title())

    logger.info(f"[API:collections] {name} | chain={chain} | supply={total_supply}")

    return {
        'name':         name,
        'chain':        chain,
        'contract':     contract,
        'x_link':       twitter,
        'discord_link': discord,
        'total_supply': total_supply,
        'phases':       [],
        'success':      False,
    }

# ─────────────────────────────────────────────
# DETECTOR 2: OpenSea Drops API
# ─────────────────────────────────────────────

async def _detect_via_drops_api(slug: str) -> dict:
    api_key = await _os_api_key()
    if not api_key:
        return {}

    hdrs = {'Accept': 'application/json', 'x-api-key': api_key}
    data = await _get(f"https://api.opensea.io/api/v2/drops/{slug}",
                      headers=hdrs, as_json=True, timeout=8)

    if not data or not isinstance(data, dict):
        logger.info(f"[API:drops] No drop registered for {slug}")
        return {}

    logger.info(f"[API:drops] Raw keys: {list(data.keys())}")

    # Extract stages using flexible extractor
    raw_stages = _extract_stages(data)

    if not raw_stages:
        logger.info(f"[API:drops] No stages found in response for {slug}")
        logger.debug(f"[API:drops] Full response: {json.dumps(data)[:500]}")
        return {}

    phases = [_parse_stage(s) for s in raw_stages]
    # Filter out completely empty phases
    phases = [p for p in phases if p.get('name')]

    # total_minted — check multiple locations
    total_minted = (data.get('total_minted') or
                    data.get('totalMinted') or
                    (data.get('drop') or {}).get('total_minted') or 0)

    if phases:
        logger.info(f"[API:drops] {len(phases)} phases for {slug}: {[p['name'] for p in phases]}")
        return {'phases': phases, 'success': True, 'total_minted': int(total_minted)}

    return {}

# ─────────────────────────────────────────────
# DETECTOR 3: Playwright
# ─────────────────────────────────────────────

async def _detect_via_playwright(url: str) -> dict:
    try:
        from utils.opensea_scraper import scrape_opensea_phases
        result = await scrape_opensea_phases(url)
        if result.get('success') and result.get('phases'):
            logger.info(f"[Playwright] {len(result['phases'])} phases extracted")
            return result
        err = result.get('error', '')
        if err and err != 'playwright_not_installed':
            logger.warning(f"[Playwright] {err}")
        if result.get('name'):
            return {'name': result['name'], 'chain': result.get('chain', 'Ethereum'),
                    'phases': [], 'success': False}
    except Exception as e:
        logger.warning(f"[Playwright] exception: {e}")
    return {}

# ─────────────────────────────────────────────
# DETECTOR 4: Countdown timer fallback
# ─────────────────────────────────────────────

async def _detect_countdown_timer(url: str) -> dict:
    html = await _get(url, timeout=8)
    if not html:
        return {}

    m = re.search(
        r'[Mm]inting\s+in\s+'
        r'(?:(\d+)\s*days?\s*)?'
        r'(?:(\d+)\s*hours?\s*)?'
        r'(?:(\d+)\s*min(?:s|utes?)?\s*)?'
        r'(?:(\d+)\s*sec(?:s|onds?)?\s*)?',
        html
    )
    if m and any(m.groups()):
        days    = int(m.group(1) or 0)
        hours   = int(m.group(2) or 0)
        minutes = int(m.group(3) or 0)
        total_s = days*86400 + hours*3600 + minutes*60
        if total_s > 0:
            mint_time = datetime.utcnow() + timedelta(seconds=total_s)
            time_str  = mint_time.strftime("%Y-%m-%d %H:%M")
            logger.info(f"[Countdown] {days}d {hours}h {minutes}m -> {time_str} UTC")
            return {
                'phases': [{'name': 'Mint', 'time': time_str, 'end_time': '',
                            'price': 'TBA', 'limit': 'N/A'}],
                'success': True, 'countdown_detected': True,
            }
    return {}

# ─────────────────────────────────────────────
# PARALLEL DETECTION ENGINE
# ─────────────────────────────────────────────

async def _run_with_timeout(coro, timeout: float, name: str):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(f"[parallel] {name} timed out after {timeout}s")
        return {}
    except Exception as e:
        logger.warning(f"[parallel] {name} error: {e}")
        return {}

async def _detect_via_pc_scraper(url: str) -> dict | None:
    """Call the PC scraper server if PC_SCRAPER_URL is configured."""
    import os, aiohttp
    pc_url = os.environ.get('PC_SCRAPER_URL', '').rstrip('/')
    if not pc_url:
        return None
    api_key = os.environ.get('API_SECRET_KEY', '')
    try:
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['X-API-Key'] = api_key
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{pc_url}/scrape",
                json={'url': url},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=70),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get('phases'):
                        logger.info(f"[pc_scraper] Got {len(data['phases'])} phases from PC")
                        return data
                    logger.info(f"[pc_scraper] PC returned no phases")
                else:
                    logger.warning(f"[pc_scraper] PC server returned {resp.status}")
    except Exception as e:
        logger.warning(f"[pc_scraper] PC server unreachable: {e}")
    return None


async def detect_opensea_parallel(url: str, slug: str) -> dict:
    logger.info(f"[parallel] Starting for slug={slug}")
    t0 = datetime.utcnow()

    # If PC scraper is configured, try it first — avoids Railway 403 blocks
    import os as _os
    if _os.environ.get('PC_SCRAPER_URL'):
        logger.info(f"[parallel] Trying PC scraper first...")
        pc_r = await _detect_via_pc_scraper(url)
        if pc_r and pc_r.get('phases'):
            elapsed = (datetime.utcnow() - t0).total_seconds()
            logger.info(f"[parallel] PC scraper succeeded in {elapsed:.1f}s")
            col_r = await _run_with_timeout(_detect_via_collections_api(slug), 10, "collections_api")
            result = {
                'name':               pc_r.get('name', ''),
                'chain':              'Ethereum',
                'contract':           '',
                'x_link':             pc_r.get('twitter', ''),
                'discord_link':       pc_r.get('discord', ''),
                'total_supply':       pc_r.get('total_supply', 0),
                'phases':             pc_r['phases'],
                'success':            True,
                'needs_manual':       False,
                'countdown_detected': False,
            }
            if col_r:
                if col_r.get('name'):         result['name']         = col_r['name']
                if col_r.get('chain'):        result['chain']        = col_r['chain']
                if col_r.get('contract'):     result['contract']     = col_r['contract']
                if col_r.get('x_link'):       result['x_link']       = col_r['x_link']
                if col_r.get('discord_link'): result['discord_link'] = col_r['discord_link']
                if col_r.get('total_supply'): result['total_supply'] = col_r['total_supply']
            if not result['name']:
                result['name'] = slug.replace('-', ' ').title()
            return result
        logger.info(f"[parallel] PC scraper failed/offline — falling back to Railway scrapers")

    col_r, drops_r, pw_r, cd_r = await asyncio.gather(
        _run_with_timeout(_detect_via_collections_api(slug), 10, "collections_api"),
        _run_with_timeout(_detect_via_drops_api(slug),       10, "drops_api"),
        _run_with_timeout(_detect_via_playwright(url),       50, "playwright"),
        _run_with_timeout(_detect_countdown_timer(url),      10, "countdown"),
    )

    elapsed = (datetime.utcnow() - t0).total_seconds()
    logger.info(f"[parallel] Done in {elapsed:.1f}s | drops={bool(drops_r and drops_r.get('phases'))} | pw={bool(pw_r and pw_r.get('phases'))} | cd={bool(cd_r and cd_r.get('phases'))}")

    result = {
        'name': '', 'chain': 'Ethereum', 'contract': '',
        'x_link': '', 'discord_link': '', 'total_supply': 0,
        'phases': [], 'success': False, 'needs_manual': False,
        'countdown_detected': False,
    }

    # Metadata from collections API (most reliable source)
    if col_r:
        result.update({
            'name':         col_r.get('name', ''),
            'chain':        col_r.get('chain', 'Ethereum'),
            'contract':     col_r.get('contract', ''),
            'x_link':       col_r.get('x_link', ''),
            'discord_link': col_r.get('discord_link', ''),
            'total_supply': col_r.get('total_supply', 0),
        })

    # Phases — priority: drops_api > playwright > countdown
    if drops_r and drops_r.get('phases'):
        result['phases']  = drops_r['phases']
        result['success'] = True
        logger.info(f"[merge] Using drops API -> {len(result['phases'])} phases")
    elif pw_r and pw_r.get('phases'):
        result['phases']  = pw_r['phases']
        result['success'] = True
        if not result['name'] and pw_r.get('name'):
            result['name'] = pw_r['name']
        logger.info(f"[merge] Using Playwright -> {len(result['phases'])} phases")
    elif cd_r and cd_r.get('phases'):
        result['phases']           = cd_r['phases']
        result['success']          = True
        result['countdown_detected'] = True
        logger.info(f"[merge] Using countdown fallback -> {len(result['phases'])} phases")
    else:
        result['needs_manual'] = True
        logger.info(f"[merge] No phases detected - needs manual entry")

    # Name fallback chain
    if not result['name']:
        if pw_r and pw_r.get('name'):
            result['name'] = pw_r['name']
        else:
            result['name'] = slug.replace('-', ' ').title()

    return result

# ─────────────────────────────────────────────
# MAIN ENTRY POINTS
# ─────────────────────────────────────────────

async def parse_mint_url(url: str) -> dict:
    domain = urlparse(url).netloc.lower()

    if 'opensea.io' in domain:
        slug = _opensea_slug(url)
        if slug:
            result = await detect_opensea_parallel(url, slug)
        else:
            result = {'name': '', 'chain': 'Ethereum', 'phases': [],
                      'success': False, 'needs_manual': True}
    else:
        result = await _parse_generic(url)

    result['mint_link'] = url

    # Set os_link
    if 'opensea.io' in domain:
        slug = _opensea_slug(url)
        if slug:
            result['os_link'] = f"https://opensea.io/collection/{slug}"

    # Defaults
    for k in ('x_link', 'os_link', 'discord_link', 'contract'):
        if not result.get(k): result[k] = ''
    if not result.get('total_supply'): result['total_supply'] = 0

    # Market links
    if result.get('contract'):
        slug = _opensea_slug(url) if 'opensea.io' in domain else ''
        result['market_links'] = _build_market_links(result['contract'], result.get('chain','Ethereum'), slug)
    else:
        result['market_links'] = {}

    # first_phase_time
    result['first_phase_time'] = None
    for p in result.get('phases', []):
        if p.get('time'):
            try:
                result['first_phase_time'] = datetime.strptime(p['time'], "%Y-%m-%d %H:%M")
                break
            except Exception:
                pass

    return result


async def parse_multiple_urls(urls: list) -> list:
    urls = list(dict.fromkeys(urls))[:5]
    logger.info(f"[multi] Processing {len(urls)} URLs concurrently")
    results = await asyncio.gather(*[parse_mint_url(u) for u in urls], return_exceptions=True)
    out = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            logger.error(f"[multi] URL {i} failed: {r}")
            out.append({'name': urls[i], 'error': str(r), 'phases': [], 'success': False,
                        'mint_link': urls[i]})
        else:
            out.append(r)
    return out

# ─────────────────────────────────────────────
# Generic (non-OpenSea)
# ─────────────────────────────────────────────

async def _parse_generic(url: str) -> dict:
    result = {'name': '', 'chain': detect_chain(url), 'phases': [],
              'success': False, 'needs_manual': True}
    html = await _get(url, timeout=10)
    if not html or not HAS_BS4:
        return result
    soup = BeautifulSoup(html, 'html.parser')
    for prop in ['og:title', 'twitter:title']:
        tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
        if tag and tag.get('content'):
            result['name'] = _clean_name(tag['content'])
            break
    if not result['name']:
        h1 = soup.find('h1')
        if h1: result['name'] = _clean_name(h1.get_text(strip=True))
    if not result['name'] and soup.title:
        result['name'] = _clean_name(soup.title.string or '')
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'twitter.com/' in href or 'x.com/' in href:
            parts    = href.rstrip('/').split('/')
            username = parts[-1] if parts else ''
            if username and username not in ('twitter','x','share','intent','home','search',''):
                result['x_link'] = href if href.startswith('http') else f'https://x.com/{username}'
                break
    return result

# ─────────────────────────────────────────────
# Market links
# ─────────────────────────────────────────────

def _build_market_links(contract: str, chain: str = 'Ethereum', os_slug: str = '') -> dict:
    if not contract or not contract.startswith('0x'):
        return {}
    chain_os = CHAIN_OS_SLUG.get(chain, 'ethereum')
    links = {}
    if os_slug:
        links['OpenSea'] = f'https://opensea.io/collection/{os_slug}'
    else:
        links['OpenSea'] = f'https://opensea.io/assets/{chain_os}/{contract}'
    if chain in ('Ethereum', 'Base', 'Blast', 'Arbitrum', 'Polygon', 'Optimism'):
        links['Blur']      = f'https://blur.io/collection/{contract}'
        links['MagicEden'] = f'https://magiceden.io/collections/ethereum/{contract}'
    return links

async def get_market_links(contract: str, chain: str = 'Ethereum') -> dict:
    return _build_market_links(contract, chain)

# ─────────────────────────────────────────────
# Mint status monitoring
# ─────────────────────────────────────────────

async def check_mint_status(mint: dict) -> dict:
    mint_link = (mint.get('mint_link') or mint.get('os_link') or '').strip()
    if not mint_link:
        return {'sold_out': False, 'fast_mint': False}
    slug = _opensea_slug(mint_link)
    if not slug:
        return {'sold_out': False, 'fast_mint': False}
    api_key = await _os_api_key()
    if not api_key:
        return {'sold_out': False, 'fast_mint': False}
    hdrs = {'x-api-key': api_key, 'Accept': 'application/json'}
    try:
        col_data = await _get(f"https://api.opensea.io/api/v2/collections/{slug}",
                              headers=hdrs, as_json=True, timeout=8)
        if not col_data:
            return {'sold_out': False, 'fast_mint': False}
        total_supply = int(col_data.get('total_supply') or 0)
        stats        = col_data.get('stats') or {}
        floor_price  = float(stats.get('floor_price') or col_data.get('floor_price') or 0)
        minted       = 0
        drop_data    = await _get(f"https://api.opensea.io/api/v2/drops/{slug}",
                                  headers=hdrs, as_json=True, timeout=8)
        if drop_data:
            minted = int(drop_data.get('total_minted') or
                         drop_data.get('totalMinted') or
                         (drop_data.get('drop') or {}).get('total_minted') or 0)
        sold_out  = bool(total_supply and minted >= total_supply)
        fast_mint = bool(total_supply > 0 and minted > 0 and (minted / total_supply) >= 0.5)
        pct       = round(minted / total_supply * 100) if total_supply else 0
        floor_str = f"{floor_price:.4f}".rstrip('0').rstrip('.') if floor_price else '0'
        logger.info(f"[status] {slug}: {minted}/{total_supply} ({pct}%) floor={floor_str}")
        return {'sold_out': sold_out, 'fast_mint': fast_mint,
                'minted': minted, 'total': total_supply,
                'floor_price': floor_str, 'pct': pct}
    except Exception as e:
        logger.warning(f"[status] {slug}: {e}")
        return {'sold_out': False, 'fast_mint': False}

def _find_stages(obj, depth=0) -> list:
    if depth > 8: return []
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            first = obj[0]
            if any(k in first for k in ('start_time','startTime','start','stage','mint_price','mintPrice')):
                return obj
        for item in obj:
            r = _find_stages(item, depth+1)
            if r: return r
    elif isinstance(obj, dict):
        for key in ('mint_stages','mintStages','stages','phases','drops','schedule','tiers'):
            if key in obj and isinstance(obj[key], list) and obj[key]:
                return obj[key]
        for v in obj.values():
            r = _find_stages(v, depth+1)
            if r: return r
    return []

# ─────────────────────────────────────────────
# Floor Price + Sales (for monitor)
# ─────────────────────────────────────────────

async def get_floor_price(mint: dict) -> float | None:
    """
    Fetch current floor price for a mint (via OpenSea API).
    Returns float (ETH) or None if unavailable.
    """
    mint_link = (mint.get('os_link') or mint.get('mint_link') or '').strip()
    slug = _opensea_slug(mint_link)
    if not slug:
        return None
    api_key = await _os_api_key()
    if not api_key:
        return None
    hdrs = {'x-api-key': api_key, 'Accept': 'application/json'}
    try:
        data = await _get(
            f"https://api.opensea.io/api/v2/collections/{slug}",
            headers=hdrs, as_json=True, timeout=8
        )
        if not data:
            return None
        # Try stats.floor_price first, then top-level floor_price
        stats = data.get('stats') or {}
        fp = (
            stats.get('floor_price')
            or data.get('floor_price')
            or data.get('floorPrice')
        )
        if fp is not None:
            return float(fp)
    except Exception as e:
        logger.debug(f"get_floor_price error for {slug}: {e}")
    return None


async def get_recent_sales_count(mint: dict, window_seconds: int = 60) -> int | None:
    """
    Get number of NFT sales in the last `window_seconds` via OpenSea events API.
    Returns int or None if unavailable.
    """
    mint_link = (mint.get('os_link') or mint.get('mint_link') or '').strip()
    slug = _opensea_slug(mint_link)
    if not slug:
        return None
    api_key = await _os_api_key()
    if not api_key:
        return None
    hdrs = {'x-api-key': api_key, 'Accept': 'application/json'}
    try:
        from datetime import timezone
        cutoff = int(
            (datetime.utcnow().replace(tzinfo=timezone.utc).timestamp()) - window_seconds
        )
        data = await _get(
            f"https://api.opensea.io/api/v2/events/collection/{slug}"
            f"?event_type=sale&limit=50&after={cutoff}",
            headers=hdrs, as_json=True, timeout=8
        )
        if not data:
            return None
        events = data.get('asset_events') or data.get('events') or []
        return len(events)
    except Exception as e:
        logger.debug(f"get_recent_sales_count error for {slug}: {e}")
    return None


# ─────────────────────────────────────────────
# Minted Count Tracker (Etherscan + OpenSea)
# ─────────────────────────────────────────────

ETHERSCAN_URLS = {
    'ethereum': 'https://api.etherscan.io/api',
    'eth':      'https://api.etherscan.io/api',
    'base':     'https://api.basescan.org/api',
    'polygon':  'https://api.polygonscan.com/api',
    'matic':    'https://api.polygonscan.com/api',
}

async def get_minted_count(mint: dict) -> int | None:
    """
    Get current minted count for a mint.
    Priority: Etherscan contract call → OpenSea drops API → None
    Returns int or None if unavailable.
    """
    import os

    contract = (mint.get('contract') or '').strip()
    chain    = (mint.get('chain') or 'Ethereum').lower()
    etherscan_key = os.environ.get('ETHERSCAN_API_KEY', '')

    # ── 1. Etherscan: call totalSupply() on the contract ──
    if contract and etherscan_key:
        base_url = ETHERSCAN_URLS.get(chain)
        if base_url:
            try:
                # totalSupply() function selector = 0x18160ddd
                url = (
                    f"{base_url}?module=proxy&action=eth_call"
                    f"&to={contract}&data=0x18160ddd&tag=latest"
                    f"&apikey={etherscan_key}"
                )
                data = await _get(url, as_json=True, timeout=8)
                if data and data.get('result') and data['result'] != '0x':
                    count = int(data['result'], 16)
                    logger.info(f"[minted] {mint.get('name')}: {count} via Etherscan")
                    return count
            except Exception as e:
                logger.debug(f"[minted] Etherscan error for {mint.get('name')}: {e}")

    # ── 2. OpenSea drops API fallback ──
    mint_link = (mint.get('os_link') or mint.get('mint_link') or '').strip()
    slug = _opensea_slug(mint_link)
    if slug:
        api_key = await _os_api_key()
        if api_key:
            hdrs = {'x-api-key': api_key, 'Accept': 'application/json'}
            try:
                data = await _get(
                    f"https://api.opensea.io/api/v2/drops/{slug}",
                    headers=hdrs, as_json=True, timeout=8
                )
                if data:
                    minted = int(
                        data.get('total_minted') or
                        data.get('totalMinted') or
                        (data.get('drop') or {}).get('total_minted') or 0
                    )
                    if minted > 0:
                        logger.info(f"[minted] {mint.get('name')}: {minted} via OpenSea drops")
                        return minted
            except Exception as e:
                logger.debug(f"[minted] OpenSea drops error for {mint.get('name')}: {e}")

    return None


async def fetch_contract_address(mint: dict) -> str | None:
    """
    Fetch contract address from OpenSea if not already set.
    Returns contract address string or None.
    """
    mint_link = (mint.get('os_link') or mint.get('mint_link') or '').strip()
    slug = _opensea_slug(mint_link)
    if not slug:
        return None
    api_key = await _os_api_key()
    if not api_key:
        return None
    hdrs = {'x-api-key': api_key, 'Accept': 'application/json'}
    try:
        data = await _get(
            f"https://api.opensea.io/api/v2/collections/{slug}",
            headers=hdrs, as_json=True, timeout=8
        )
        if not data:
            return None
        # Try contracts array first
        contracts = data.get('contracts') or []
        if contracts and isinstance(contracts, list):
            addr = contracts[0].get('address') or contracts[0].get('contract_address')
            if addr:
                return addr
        # Fallback to top-level contract field
        return data.get('contract') or data.get('contract_address') or None
    except Exception as e:
        logger.debug(f"[contract] fetch error for {slug}: {e}")
    return None
