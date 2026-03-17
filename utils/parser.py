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
    "scatter.art": "Ethereum",
}

CHAIN_NORMALIZE = {
    "ethereum": "Ethereum", "eth": "Ethereum",
    "base": "Base", "blast": "Blast", "arbitrum": "Arbitrum",
    "polygon": "Polygon", "optimism": "Optimism", "zora": "Zora",
    "solana": "Solana", "sol": "Solana", "bitcoin": "Bitcoin", "btc": "Bitcoin",
    "megaeth": "MegaETH", "mega": "MegaETH",
    "abstract": "Abstract", "abs": "Abstract",
    "linea": "Linea", "scroll": "Scroll", "starknet": "Starknet",
    "bnb": "BNB", "bsc": "BNB", "avalanche": "Avalanche", "avax": "Avalanche",
}

CHAIN_OS_SLUG = {
    'Ethereum': 'ethereum', 'Base': 'base', 'Blast': 'blast',
    'Arbitrum': 'arbitrum', 'Polygon': 'matic', 'Optimism': 'optimism', 'Zora': 'zora',
    'Abstract': 'abstract',
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
              ' - Collection | OpenSea', ' Collection | OpenSea',
              ' | Highlight', ' | Scatter', ' - Scatter', ' - Highlight'):
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

        # Extract contract from drops response
        contract = (
            data.get('contract_address') or
            data.get('contract') or
            (data.get('drop') or {}).get('contract_address') or
            (data.get('drop') or {}).get('contract') or ''
        )
        # Also check contracts array
        contracts = data.get('contracts') or []
        if not contract and contracts and isinstance(contracts, list):
            contract = contracts[0].get('address', '')

        if contract:
            logger.info(f"[API:drops] contract={contract}")

        return {'phases': phases, 'success': True, 'total_minted': int(total_minted), 'contract': contract}

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

    # Always scrape the /overview page for OpenSea collections — it has the drop schedule
    scrape_url = url
    if 'opensea.io/collection/' in url:
        slug = _opensea_slug(url)
        if slug:
            scrape_url = f"https://opensea.io/collection/{slug}/overview"
    try:
        headers = {'Content-Type': 'application/json'}
        if api_key:
            headers['X-API-Key'] = api_key
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{pc_url}/scrape",
                json={'url': scrape_url},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
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

            # Run both collections + drops API in parallel to get contract/chain/metadata
            col_r, drops_r = await asyncio.gather(
                _run_with_timeout(_detect_via_collections_api(slug), 10, "collections_api"),
                _run_with_timeout(_detect_via_drops_api(slug),       10, "drops_api"),
            )

            result = {
                'name':               pc_r.get('name', ''),
                'chain':              'Ethereum',
                'contract':           '',
                'x_link':             pc_r.get('twitter', ''),
                'discord_link':       pc_r.get('discord', ''),
                'total_supply':       0,
                'minted':             pc_r.get('minted', 0),
                'phases':             pc_r['phases'],
                'success':            True,
                'needs_manual':       False,
                'countdown_detected': False,
            }

            # Merge collections API metadata (name, chain, contract, social)
            if col_r:
                if col_r.get('name'):         result['name']         = col_r['name']
                if col_r.get('chain'):        result['chain']        = col_r['chain']
                if col_r.get('contract'):     result['contract']     = col_r['contract']
                if col_r.get('x_link'):       result['x_link']       = col_r['x_link']
                if col_r.get('discord_link'): result['discord_link'] = col_r['discord_link']

            # Merge drops API — has contract for upcoming drops + supply
            if drops_r:
                if drops_r.get('contract') and not result['contract']:
                    result['contract'] = drops_r['contract']

            logger.info(f"[parallel] contract={result['contract'] or 'NOT FOUND'} chain={result['chain']}")
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

# ═══════════════════════════════════════════════
# MULTI-PLATFORM DETECTION ENGINE
# ═══════════════════════════════════════════════

PLATFORM_PATTERNS = {
    'opensea':     ['opensea.io'],
    'manifold':    ['manifold.xyz', 'app.manifold.xyz'],
    'highlight':   ['highlight.xyz'],
    'scatter':     ['scatter.art'],
    'launchmynft': ['launchmynft.io'],
}

def identify_platform(url: str) -> str:
    """Identify which NFT platform a URL belongs to."""
    domain = urlparse(url).netloc.lower().replace('www.', '')
    for platform, patterns in PLATFORM_PATTERNS.items():
        for pat in patterns:
            if pat in domain:
                return platform
    return 'generic'


async def parse_mint_url(url: str) -> dict:
    platform = identify_platform(url)
    logger.info(f"[Platform Detection] {platform.capitalize()} detected for {url}")

    if platform == 'opensea':
        slug = _opensea_slug(url)
        if slug:
            result = await detect_opensea_parallel(url, slug)
        else:
            result = {'name': '', 'chain': 'Ethereum', 'phases': [],
                      'success': False, 'needs_manual': True}
    elif platform == 'manifold':
        result = await _detect_manifold(url)
    elif platform == 'highlight':
        result = await _detect_highlight(url)
    elif platform == 'scatter':
        result = await _detect_scatter(url)
    elif platform == 'launchmynft':
        result = await _detect_launchmynft(url)
    else:
        result = await _detect_generic(url)

    result['mint_link'] = url

    # Set os_link for OpenSea
    if platform == 'opensea':
        slug = _opensea_slug(url)
        if slug:
            result['os_link'] = f"https://opensea.io/collection/{slug}"

    # Defaults
    for k in ('x_link', 'os_link', 'discord_link', 'contract'):
        if not result.get(k): result[k] = ''
    if not result.get('total_supply'): result['total_supply'] = 0

    # Market links
    if result.get('contract'):
        slug = _opensea_slug(url) if platform == 'opensea' else ''
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
# PLATFORM: Manifold
# ─────────────────────────────────────────────

def _extract_next_data(html: str) -> dict:
    """Extract __NEXT_DATA__ JSON from a Next.js page."""
    m = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    return {}

def _extract_json_ld(html: str) -> list:
    """Extract all JSON-LD script blocks from HTML."""
    results = []
    for m in re.finditer(r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            results.append(json.loads(m.group(1)))
        except Exception:
            pass
    return results


async def _detect_manifold(url: str) -> dict:
    """Detect mint data from manifold.xyz claim/edition pages."""
    result = _empty_result('Ethereum')
    logger.info(f"[Manifold] Fetching {url}")

    html = await _get(url, timeout=15)
    if not html:
        logger.warning(f"[Manifold] Could not fetch page")
        return result

    # Extract name from og:title or page title
    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        for prop in ['og:title', 'twitter:title']:
            tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if tag and tag.get('content'):
                result['name'] = _clean_name(tag['content'])
                break
        if not result['name']:
            h1 = soup.find('h1')
            if h1:
                result['name'] = _clean_name(h1.get_text(strip=True))
        if not result['name'] and soup.title:
            result['name'] = _clean_name(soup.title.string or '')

        # Extract social links
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('twitter.com/' in href or 'x.com/' in href) and '/status/' not in href:
                if not result['x_link']:
                    result['x_link'] = href
            elif 'discord.gg' in href or 'discord.com/invite' in href:
                if not result['discord_link']:
                    result['discord_link'] = href

    # Try __NEXT_DATA__ for structured data
    next_data = _extract_next_data(html)
    if next_data:
        props = next_data.get('props', {}).get('pageProps', {})
        claim = props.get('claim') or props.get('instance') or props.get('edition') or {}
        if isinstance(claim, dict) and claim:
            logger.info(f"[Manifold] Found claim data in __NEXT_DATA__")
            result = _parse_manifold_claim(claim, result)

    # Try scanning page for embedded contract addresses and mint data
    if not result.get('contract'):
        # Look for 0x addresses in page
        contracts = re.findall(r'0x[a-fA-F0-9]{40}', html)
        if contracts:
            result['contract'] = contracts[0]
            logger.info(f"[Manifold] Found contract: {result['contract']}")

    # Try extracting price/supply from page text
    if not result.get('phases'):
        result = _parse_manifold_from_text(html, result)

    # Detect chain from URL path or page content
    if '/base/' in url.lower() or 'base' in html.lower()[:3000]:
        result['chain'] = 'Base'
    elif '/optimism/' in url.lower():
        result['chain'] = 'Optimism'

    if result.get('phases'):
        result['success'] = True
        logger.info(f"[Manifold] Detected: {result['name']} — {len(result['phases'])} phase(s)")
    else:
        result['needs_manual'] = True
        logger.info(f"[Manifold] No phases found — needs manual entry")

    return result


def _parse_manifold_claim(claim: dict, result: dict) -> dict:
    """Parse Manifold claim/edition object into our standard format."""
    if claim.get('name'):
        result['name'] = claim['name']
    if claim.get('contract') or claim.get('contractAddress'):
        result['contract'] = claim.get('contract') or claim.get('contractAddress', '')

    # Supply
    supply = claim.get('totalMax') or claim.get('maxSupply') or claim.get('total') or 0
    try:
        result['total_supply'] = int(supply)
    except (ValueError, TypeError):
        pass

    # Build phase from claim data
    price_raw = claim.get('cost') or claim.get('price') or claim.get('mintPrice') or 0
    price_str = 'Free'
    try:
        if isinstance(price_raw, dict):
            price_str = _format_eth_price(price_raw)
        else:
            val = float(price_raw)
            if val > 1e15:  # Wei value
                val = val / 1e18
            price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
    except (ValueError, TypeError):
        price_str = str(price_raw) if price_raw else 'Free'

    start_time = parse_any_time(
        claim.get('startDate') or claim.get('start_date') or
        claim.get('startTime') or claim.get('saleStart') or ''
    )
    end_time = parse_any_time(
        claim.get('endDate') or claim.get('end_date') or
        claim.get('endTime') or claim.get('saleEnd') or ''
    )

    phase_name = claim.get('stageName') or claim.get('phaseName') or 'Mint'
    result['phases'] = [{
        'name': phase_name,
        'time': start_time,
        'end_time': end_time,
        'price': price_str,
        'limit': str(claim.get('walletMax') or claim.get('maxPerWallet') or 'N/A'),
    }]
    return result


def _parse_manifold_from_text(html: str, result: dict) -> dict:
    """Fallback: extract mint info from raw page text using keyword scanning."""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)

    # Price patterns
    price_m = re.search(r'([\d.]+)\s*(ETH|eth|Ξ)', text)
    price_str = 'Free'
    if price_m:
        val = float(price_m.group(1))
        price_str = 'Free' if val == 0 else f"{price_m.group(1)} ETH"
    elif re.search(r'(?i)\bfree\b.*\bmint\b|\bmint\b.*\bfree\b', text):
        price_str = 'Free'

    # Supply
    supply_m = re.search(r'(?i)(?:supply|edition|max)\s*[:\s]*(\d{1,6})', text)
    if supply_m and not result.get('total_supply'):
        try:
            result['total_supply'] = int(supply_m.group(1))
        except ValueError:
            pass

    # If we found a price, create a basic phase
    if price_str or re.search(r'(?i)mint\s+now|claim\s+now|mint\s+live', text):
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        result['phases'] = [{
            'name': 'Mint',
            'time': now_str,
            'end_time': '',
            'price': price_str,
            'limit': 'N/A',
        }]

    return result


# ─────────────────────────────────────────────
# PLATFORM: Highlight
# ─────────────────────────────────────────────

async def _detect_highlight(url: str) -> dict:
    """Detect mint data from highlight.xyz pages."""
    result = _empty_result('Ethereum')
    logger.info(f"[Highlight] Fetching {url}")

    html = await _get(url, timeout=15)
    if not html:
        logger.warning(f"[Highlight] Could not fetch page")
        return result

    # Name from meta tags
    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        for prop in ['og:title', 'twitter:title']:
            tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if tag and tag.get('content'):
                result['name'] = _clean_name(tag['content'])
                break
        if not result['name'] and soup.title:
            result['name'] = _clean_name(soup.title.string or '')

        # Social links
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('twitter.com/' in href or 'x.com/' in href) and '/status/' not in href:
                if not result['x_link']:
                    result['x_link'] = href
            elif 'discord.gg' in href or 'discord.com/invite' in href:
                if not result['discord_link']:
                    result['discord_link'] = href

    # Try __NEXT_DATA__
    next_data = _extract_next_data(html)
    if next_data:
        props = next_data.get('props', {}).get('pageProps', {})
        # Highlight stores project data in various keys
        project = (props.get('collection') or props.get('project') or
                   props.get('drop') or props.get('mintPage') or {})
        if isinstance(project, dict) and project:
            logger.info(f"[Highlight] Found project data in __NEXT_DATA__")
            result = _parse_highlight_project(project, result)

    # Detect chain from URL or content
    path = urlparse(url).path.lower()
    for chain_kw, chain_name in CHAIN_NORMALIZE.items():
        if f'/{chain_kw}/' in path or f'/{chain_kw}' == path.rstrip('/').rsplit('/', 1)[-1] if '/' in path else False:
            result['chain'] = chain_name
            break

    # Contract from page
    if not result.get('contract'):
        contracts = re.findall(r'0x[a-fA-F0-9]{40}', html)
        if contracts:
            result['contract'] = contracts[0]

    # Fallback: parse from text
    if not result.get('phases'):
        result = _parse_generic_text_phases(html, result)

    if result.get('phases'):
        result['success'] = True
        logger.info(f"[Highlight] Detected: {result['name']} — {len(result['phases'])} phase(s)")
    else:
        result['needs_manual'] = True
        logger.info(f"[Highlight] No phases found — needs manual entry")

    return result


def _parse_highlight_project(project: dict, result: dict) -> dict:
    """Parse Highlight project data into standard format."""
    if project.get('name') or project.get('title'):
        result['name'] = project.get('name') or project.get('title', '')

    if project.get('contractAddress') or project.get('address'):
        result['contract'] = project.get('contractAddress') or project.get('address', '')

    # Chain
    chain_raw = project.get('chainId') or project.get('chain') or ''
    chain_map = {'1': 'Ethereum', '8453': 'Base', '42161': 'Arbitrum',
                 '10': 'Optimism', '137': 'Polygon', '7777777': 'Zora'}
    if str(chain_raw) in chain_map:
        result['chain'] = chain_map[str(chain_raw)]
    elif isinstance(chain_raw, str) and chain_raw.lower() in CHAIN_NORMALIZE:
        result['chain'] = CHAIN_NORMALIZE[chain_raw.lower()]

    # Supply
    supply = project.get('size') or project.get('maxSupply') or project.get('supply') or 0
    try:
        result['total_supply'] = int(supply)
    except (ValueError, TypeError):
        pass

    # Phases from mintConfigs or stages
    configs = project.get('mintConfigs') or project.get('stages') or project.get('phases') or []
    if isinstance(configs, list) and configs:
        phases = []
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            price_raw = cfg.get('price') or cfg.get('mintPrice') or cfg.get('pricePerToken') or 0
            price_str = _format_eth_price(price_raw) if isinstance(price_raw, dict) else 'Free'
            if not isinstance(price_raw, dict):
                try:
                    val = float(price_raw)
                    if val > 1e15:
                        val = val / 1e18
                    price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
                except (ValueError, TypeError):
                    price_str = str(price_raw) if price_raw else 'Free'

            start = parse_any_time(cfg.get('startTime') or cfg.get('start') or cfg.get('startDate') or '')
            end = parse_any_time(cfg.get('endTime') or cfg.get('end') or cfg.get('endDate') or '')

            phases.append({
                'name': cfg.get('name') or cfg.get('stage') or 'Mint',
                'time': start,
                'end_time': end,
                'price': price_str,
                'limit': str(cfg.get('maxPerWallet') or cfg.get('limit') or 'N/A'),
            })
        if phases:
            result['phases'] = phases
    elif project.get('mintPrice') or project.get('price'):
        # Single phase
        price_raw = project.get('mintPrice') or project.get('price') or 0
        price_str = 'Free'
        try:
            val = float(price_raw)
            if val > 1e15: val = val / 1e18
            price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
        except (ValueError, TypeError):
            price_str = str(price_raw) if price_raw else 'Free'

        start = parse_any_time(project.get('startTime') or project.get('startDate') or '')
        result['phases'] = [{
            'name': 'Mint',
            'time': start,
            'end_time': '',
            'price': price_str,
            'limit': str(project.get('maxPerWallet') or 'N/A'),
        }]

    # Social links
    if project.get('twitter') or project.get('twitterUrl'):
        result['x_link'] = project.get('twitter') or project.get('twitterUrl', '')
    if project.get('discord') or project.get('discordUrl'):
        result['discord_link'] = project.get('discord') or project.get('discordUrl', '')

    return result


# ─────────────────────────────────────────────
# PLATFORM: Scatter
# ─────────────────────────────────────────────

def _scatter_slug(url: str) -> str:
    """Extract collection slug from scatter.art URL."""
    path = urlparse(url).path.rstrip('/')
    # /collection/slug or /collection/slug/mint
    m = re.search(r'/collection/([^/?#]+)', path)
    if m:
        return m.group(1)
    # Direct slug in path: scatter.art/slug
    parts = [p for p in path.split('/') if p]
    if parts and parts[0] not in ('collection', 'explore', 'create', 'profile', 'settings'):
        return parts[0]
    return ''


async def _detect_scatter(url: str) -> dict:
    """Detect mint data from scatter.art using their public API."""
    result = _empty_result('Ethereum')
    slug = _scatter_slug(url)
    if not slug:
        logger.warning(f"[Scatter] Could not extract slug from {url}")
        # Fallback to generic HTML parsing
        return await _detect_generic(url)

    logger.info(f"[Scatter] Fetching collection data for slug={slug}")

    # Fetch collection info via Scatter API
    col_data = await _get(
        f"https://api.scatter.art/v1/collection/{slug}",
        as_json=True, timeout=10
    )

    if not col_data or not isinstance(col_data, dict):
        logger.warning(f"[Scatter] API returned no data for {slug}")
        return await _detect_generic(url)

    # Parse collection data
    result['name'] = col_data.get('name') or slug.replace('-', ' ').title()

    chain_id = str(col_data.get('chainId') or '1')
    chain_map = {'1': 'Ethereum', '8453': 'Base', '42161': 'Arbitrum',
                 '10': 'Optimism', '137': 'Polygon', '56': 'BNB',
                 '43114': 'Avalanche', '81457': 'Blast'}
    result['chain'] = chain_map.get(chain_id, 'Ethereum')

    result['contract'] = col_data.get('address') or col_data.get('contractAddress') or ''

    supply = col_data.get('maxSupply') or col_data.get('supply') or 0
    try:
        result['total_supply'] = int(supply)
    except (ValueError, TypeError):
        pass

    if col_data.get('twitter'):
        result['x_link'] = col_data['twitter']
    if col_data.get('discord'):
        result['discord_link'] = col_data['discord']

    # Fetch mint lists (phases) — public lists available without wallet
    lists_data = await _get(
        f"https://api.scatter.art/v1/collection/{slug}/eligible-invite-lists",
        as_json=True, timeout=10
    )

    if lists_data and isinstance(lists_data, list):
        phases = []
        for lst in lists_data:
            if not isinstance(lst, dict):
                continue
            price_raw = lst.get('price') or lst.get('mintPrice') or 0
            price_str = 'Free'
            try:
                val = float(price_raw)
                if val > 1e15: val = val / 1e18
                price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
            except (ValueError, TypeError):
                price_str = str(price_raw) if price_raw else 'Free'

            start = parse_any_time(lst.get('startTime') or lst.get('start') or '')
            end = parse_any_time(lst.get('endTime') or lst.get('end') or '')

            phases.append({
                'name': lst.get('name') or lst.get('listName') or 'Mint',
                'time': start,
                'end_time': end,
                'price': price_str,
                'limit': str(lst.get('maxPerWallet') or lst.get('limit') or 'N/A'),
            })
        if phases:
            result['phases'] = phases

    # If no phases from lists, try getting basic price as single phase
    if not result.get('phases'):
        price = col_data.get('tokenPrice') or col_data.get('price') or 0
        price_str = 'Free'
        try:
            val = float(price)
            if val > 1e15: val = val / 1e18
            price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
        except (ValueError, TypeError):
            pass
        if price_str:
            result['phases'] = [{
                'name': 'Public',
                'time': '',
                'end_time': '',
                'price': price_str,
                'limit': 'N/A',
            }]

    if result.get('phases'):
        result['success'] = True
        logger.info(f"[Scatter] Detected: {result['name']} — {len(result['phases'])} phase(s)")
    else:
        result['needs_manual'] = True
        logger.info(f"[Scatter] No phases found — needs manual entry")

    return result


# ─────────────────────────────────────────────
# PLATFORM: LaunchMyNFT
# ─────────────────────────────────────────────

async def _detect_launchmynft(url: str) -> dict:
    """Detect mint data from launchmynft.io pages."""
    result = _empty_result('Ethereum')
    logger.info(f"[LaunchMyNFT] Fetching {url}")

    html = await _get(url, timeout=15)
    if not html:
        logger.warning(f"[LaunchMyNFT] Could not fetch page")
        return result

    # Name from meta tags
    if HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')
        for prop in ['og:title', 'twitter:title']:
            tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if tag and tag.get('content'):
                result['name'] = _clean_name(tag['content'])
                break
        if not result['name'] and soup.title:
            result['name'] = _clean_name(soup.title.string or '')

    # Try __NEXT_DATA__
    next_data = _extract_next_data(html)
    if next_data:
        props = next_data.get('props', {}).get('pageProps', {})
        collection = props.get('collection') or props.get('project') or props.get('mint') or {}
        if isinstance(collection, dict) and collection:
            logger.info(f"[LaunchMyNFT] Found collection data in __NEXT_DATA__")
            if collection.get('name'):
                result['name'] = collection['name']
            if collection.get('contractAddress') or collection.get('address'):
                result['contract'] = collection.get('contractAddress') or collection.get('address', '')

            supply = collection.get('maxSupply') or collection.get('supply') or collection.get('size') or 0
            try:
                result['total_supply'] = int(supply)
            except (ValueError, TypeError):
                pass

            # Chain detection
            chain_raw = collection.get('blockchain') or collection.get('chain') or collection.get('network') or ''
            if isinstance(chain_raw, str) and chain_raw.lower() in CHAIN_NORMALIZE:
                result['chain'] = CHAIN_NORMALIZE[chain_raw.lower()]

            # Price
            price_raw = collection.get('price') or collection.get('mintPrice') or collection.get('publicPrice') or 0
            price_str = 'Free'
            try:
                val = float(price_raw)
                if val > 1e15: val = val / 1e18
                price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
            except (ValueError, TypeError):
                price_str = str(price_raw) if price_raw else 'Free'

            start = parse_any_time(collection.get('launchDate') or collection.get('startDate') or
                                   collection.get('saleStart') or '')

            result['phases'] = [{
                'name': 'Mint',
                'time': start,
                'end_time': '',
                'price': price_str,
                'limit': str(collection.get('maxPerWallet') or collection.get('maxMint') or 'N/A'),
            }]

    # Fallback: scan embedded JSON in page
    if not result.get('phases'):
        # LaunchMyNFT embeds config in script tags
        for m in re.finditer(r'<script[^>]*>(.*?)</script>', html, re.DOTALL):
            script = m.group(1)
            # Look for JSON with mint-related keys
            json_m = re.search(r'\{[^{}]*"(?:mintPrice|price|maxSupply)"[^{}]*\}', script)
            if json_m:
                try:
                    obj = json.loads(json_m.group(0))
                    price = obj.get('mintPrice') or obj.get('price') or 0
                    try:
                        val = float(price)
                        if val > 1e15: val = val / 1e18
                        p_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
                    except (ValueError, TypeError):
                        p_str = 'Free'

                    result['phases'] = [{'name': 'Mint', 'time': '', 'end_time': '',
                                         'price': p_str, 'limit': 'N/A'}]
                    if obj.get('maxSupply'):
                        result['total_supply'] = int(obj['maxSupply'])
                    break
                except (json.JSONDecodeError, ValueError):
                    pass

    # Contract from page
    if not result.get('contract'):
        contracts = re.findall(r'0x[a-fA-F0-9]{40}', html)
        if contracts:
            result['contract'] = contracts[0]

    if not result.get('phases'):
        result = _parse_generic_text_phases(html, result)

    if result.get('phases'):
        result['success'] = True
        logger.info(f"[LaunchMyNFT] Detected: {result['name']} — {len(result['phases'])} phase(s)")
    else:
        result['needs_manual'] = True
        logger.info(f"[LaunchMyNFT] No phases found — needs manual entry")

    return result


# ─────────────────────────────────────────────
# PLATFORM: Generic (any mint site)
# ─────────────────────────────────────────────

async def _detect_generic(url: str) -> dict:
    """Generic mint site detection — HTML parsing + keyword scanning + Playwright fallback."""
    result = _empty_result(detect_chain(url))
    logger.info(f"[Generic] Fetching {url}")

    html = await _get(url, timeout=10)
    if html and HAS_BS4:
        soup = BeautifulSoup(html, 'html.parser')

        # Name extraction
        for prop in ['og:title', 'twitter:title']:
            tag = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            if tag and tag.get('content'):
                result['name'] = _clean_name(tag['content'])
                break
        if not result['name']:
            h1 = soup.find('h1')
            if h1:
                result['name'] = _clean_name(h1.get_text(strip=True))
        if not result['name'] and soup.title:
            result['name'] = _clean_name(soup.title.string or '')

        # Social links
        for a in soup.find_all('a', href=True):
            href = a['href']
            if ('twitter.com/' in href or 'x.com/' in href) and '/status/' not in href:
                parts = href.rstrip('/').split('/')
                username = parts[-1] if parts else ''
                if username and username not in ('twitter','x','share','intent','home','search',''):
                    result['x_link'] = href if href.startswith('http') else f'https://x.com/{username}'
                    break

        # Contract from page
        if not result.get('contract'):
            contracts = re.findall(r'0x[a-fA-F0-9]{40}', html)
            if contracts:
                result['contract'] = contracts[0]

        # Try __NEXT_DATA__
        next_data = _extract_next_data(html)
        if next_data:
            props = next_data.get('props', {}).get('pageProps', {})
            # Generic deep scan for mint-related data
            result = _scan_dict_for_mint_data(props, result)

        # Keyword-based phase extraction from text
        if not result.get('phases') and html:
            result = _parse_generic_text_phases(html, result)

    # If still no phases — try Playwright as last resort
    if not result.get('phases'):
        logger.info(f"[Generic] No phases from HTML — trying Playwright fallback")
        pw_result = await _run_with_timeout(_detect_via_playwright(url), 50, "playwright_generic")
        if pw_result and pw_result.get('phases'):
            result['phases'] = pw_result['phases']
            result['success'] = True
            if pw_result.get('name') and not result['name']:
                result['name'] = pw_result['name']
            logger.info(f"[Generic] Playwright found {len(result['phases'])} phase(s)")
            return result

    if result.get('phases'):
        result['success'] = True
        logger.info(f"[Generic] Detected: {result['name']} — {len(result['phases'])} phase(s)")
    else:
        result['needs_manual'] = True
        logger.info(f"[Generic] No phases found — needs manual entry")

    return result


def _empty_result(chain: str = 'Ethereum') -> dict:
    """Create an empty result dict with sensible defaults."""
    return {
        'name': '', 'chain': chain, 'contract': '',
        'x_link': '', 'discord_link': '', 'total_supply': 0,
        'phases': [], 'success': False, 'needs_manual': True,
    }


def _scan_dict_for_mint_data(data: dict, result: dict, depth: int = 0) -> dict:
    """Recursively scan a dict for mint-related fields (price, supply, startTime)."""
    if depth > 5 or not isinstance(data, dict):
        return result
    # Check if this dict has mint-related keys
    mint_keys = {'mintPrice', 'price', 'maxSupply', 'supply', 'startTime', 'startDate',
                 'saleStart', 'mint_price', 'start_time', 'total_supply'}
    found_keys = set(data.keys()) & mint_keys
    if len(found_keys) >= 2:
        price_raw = data.get('mintPrice') or data.get('price') or data.get('mint_price') or 0
        price_str = 'Free'
        try:
            val = float(price_raw)
            if val > 1e15: val = val / 1e18
            price_str = 'Free' if val == 0 else f"{val:.6f}".rstrip('0').rstrip('.') + ' ETH'
        except (ValueError, TypeError):
            pass

        start = parse_any_time(data.get('startTime') or data.get('startDate') or
                               data.get('saleStart') or data.get('start_time') or '')

        supply = data.get('maxSupply') or data.get('supply') or data.get('total_supply') or 0
        try:
            result['total_supply'] = int(supply)
        except (ValueError, TypeError):
            pass

        if data.get('name') and not result['name']:
            result['name'] = str(data['name'])
        if data.get('contractAddress') and not result.get('contract'):
            result['contract'] = data['contractAddress']

        result['phases'] = [{
            'name': str(data.get('stageName') or data.get('phaseName') or 'Mint'),
            'time': start,
            'end_time': '',
            'price': price_str,
            'limit': str(data.get('maxPerWallet') or 'N/A'),
        }]
        return result

    # Recurse into child dicts
    for v in data.values():
        if isinstance(v, dict):
            result = _scan_dict_for_mint_data(v, result, depth + 1)
            if result.get('phases'):
                return result
        elif isinstance(v, list):
            for item in v[:5]:
                if isinstance(item, dict):
                    result = _scan_dict_for_mint_data(item, result, depth + 1)
                    if result.get('phases'):
                        return result
    return result


def _parse_generic_text_phases(html: str, result: dict) -> dict:
    """Extract mint phases from raw HTML text using keyword scanning."""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = re.sub(r'\s+', ' ', text)

    # Check for mint-related keywords
    mint_keywords = ['mint', 'public sale', 'whitelist', 'presale', 'allowlist',
                     'claim', 'price', 'supply']
    has_mint_content = any(kw in text.lower() for kw in mint_keywords)
    if not has_mint_content:
        return result

    # Price extraction
    price_m = re.search(r'(?:mint\s+)?(?:price|cost)[:\s]*([\d.]+)\s*(ETH|eth|Ξ|SOL|sol)', text, re.IGNORECASE)
    if not price_m:
        price_m = re.search(r'([\d.]+)\s*(ETH|eth|Ξ)\s*(?:per|each|/)', text)
    price_str = 'Free'
    if price_m:
        val = float(price_m.group(1))
        cur = price_m.group(2).upper().replace('Ξ', 'ETH')
        price_str = 'Free' if val == 0 else f"{price_m.group(1)} {cur}"
    elif re.search(r'(?i)\bfree\s+mint\b|\bmint\s+free\b|\bfree\s+claim\b', text):
        price_str = 'Free'

    # Supply
    supply_m = re.search(r'(?i)(?:total\s+)?supply[:\s]*(\d{1,6})', text)
    if supply_m and not result.get('total_supply'):
        try:
            result['total_supply'] = int(supply_m.group(1))
        except ValueError:
            pass

    # Time extraction
    time_str = ''
    # Look for explicit date patterns near mint keywords
    time_m = re.search(
        r'(?:mint|sale|launch|starts?)[:\s]*(\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2})',
        text, re.IGNORECASE
    )
    if time_m:
        time_str = iso_to_str(time_m.group(1))

    # Build phase if we have enough info
    if price_str or time_str:
        result['phases'] = [{
            'name': 'Mint',
            'time': time_str,
            'end_time': '',
            'price': price_str,
            'limit': 'N/A',
        }]

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

ETHERSCAN_CHAIN_IDS = {
    # ── Major L1s ──
    'ethereum':  '1',
    'eth':       '1',
    'bnb':       '56',
    'bsc':       '56',
    'polygon':   '137',
    'matic':     '137',
    'avalanche': '43114',
    'avax':      '43114',
    'sonic':     '146',
    'fantom':    '146',       # Sonic replaced Fantom
    # ── L2s ──
    'base':      '8453',
    'arbitrum':  '42161',
    'optimism':  '10',
    'blast':     '81457',
    'linea':     '59144',
    'scroll':    '534352',
    'abstract':  '2741',      # now on Etherscan V2 (was separate abscan)
    'mantle':    '5000',
    'taiko':     '167000',
    'unichain':  '130',
    'opbnb':     '204',
    # ── App-chains ──
    'apechain':  '33139',
    'berachain': '80094',
    'bera':      '80094',
    'worldchain':'480',
    'world':     '480',
    'celo':      '42220',
    'gnosis':    '100',
    'moonbeam':  '1284',
    'moonriver': '1285',
    # MegaETH mainnet — chain ID 4326, uses direct RPC (not on Etherscan V2 yet)
    # Solana — uses Helius/public RPC (not EVM)
}
ETHERSCAN_V2_URL = 'https://api.etherscan.io/v2/api'

async def fetch_supply(mint: dict) -> int | None:
    """
    Fetch MAX supply (edition size) from OpenSea drops API.
    NOTE: OpenSea collections API 'total_supply' = items minted so far (NOT max supply).
          The correct max supply field is in the drops API as 'supply' or 'max_supply'.
    Returns int or None.
    """
    mint_link = (mint.get('os_link') or mint.get('mint_link') or '').strip()
    slug = _opensea_slug(mint_link)
    if not slug:
        return None
    api_key = await _os_api_key()
    if not api_key:
        return None
    hdrs = {'x-api-key': api_key, 'Accept': 'application/json'}

    # ── Try drops API first — has the correct max supply ──
    try:
        data = await _get(
            f"https://api.opensea.io/api/v2/drops/{slug}",
            headers=hdrs, as_json=True, timeout=8
        )
        if data:
            supply = int(
                data.get('supply') or
                data.get('max_supply') or
                data.get('total_supply') or
                (data.get('drop') or {}).get('supply') or
                (data.get('drop') or {}).get('max_supply') or 0
            )
            if supply > 0:
                logger.info(f"[supply] {mint.get('name')}: {supply:,} via OpenSea drops")
                return supply
    except Exception as e:
        logger.debug(f"[supply] drops API error for {slug}: {e}")

    return None


async def get_minted_count(mint: dict) -> int | None:
    """
    Get current minted count for a mint.
    Priority:
      1. Etherscan V2 (all EVM chains — ETH, Base, Arbitrum, Abstract, Bera, etc.)
      2. Abscan fallback (Abstract chain — if no Etherscan key)
      3. MegaETH direct RPC
      4. Solana Helius/public RPC
      5. OpenSea drops API fallback (any chain on OpenSea)
    """
    import os

    contract      = (mint.get('contract') or '').strip()
    chain         = (mint.get('chain') or 'Ethereum').lower()
    etherscan_key = os.environ.get('ETHERSCAN_API_KEY', '')

    # ── 1. Etherscan V2 (EVM chains) ──
    if contract and etherscan_key:
        chain_id = ETHERSCAN_CHAIN_IDS.get(chain)
        if chain_id:
            try:
                url = (
                    f"{ETHERSCAN_V2_URL}?chainid={chain_id}"
                    f"&module=proxy&action=eth_call"
                    f"&to={contract}&data=0x18160ddd&tag=latest"
                    f"&apikey={etherscan_key}"
                )
                data = await _get(url, as_json=True, timeout=8)
                result = (data or {}).get('result', '')
                if result and result not in ('0x', '0x0', '', None):
                    count = int(result, 16)
                    if count > 0:
                        logger.info(f"[minted] {mint.get('name')}: {count:,} via Etherscan")
                        return count
            except Exception as e:
                logger.warning(f"[minted] Etherscan error for {mint.get('name')}: {e}")

    # ── 2. Abscan (Abstract chain) ──
    if contract and chain == 'abstract':
        abscan_key = os.environ.get('ABSCAN_API_KEY', '')
        if abscan_key:
            try:
                url = (
                    f"https://api.abscan.org/api"
                    f"?module=proxy&action=eth_call"
                    f"&to={contract}&data=0x18160ddd&tag=latest"
                    f"&apikey={abscan_key}"
                )
                data = await _get(url, as_json=True, timeout=8)
                result = (data or {}).get('result', '')
                if result and result not in ('0x', '0x0', '', None):
                    count = int(result, 16)
                    if count > 0:
                        logger.info(f"[minted] {mint.get('name')}: {count:,} via Abscan")
                        return count
            except Exception as e:
                logger.warning(f"[minted] Abscan error for {mint.get('name')}: {e}")

    # ── 3. MegaETH — direct public RPC (chain ID 4326) ──
    if contract and chain == 'megaeth':
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post('https://mainnet.megaeth.com/rpc', json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "eth_call",
                    "params": [{"to": contract, "data": "0x18160ddd"}, "latest"]
                }, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        result = (data or {}).get('result', '')
                        if result and result not in ('0x', '0x0', '', None):
                            count = int(result, 16)
                            if count > 0:
                                logger.info(f"[minted] {mint.get('name')}: {count:,} via MegaETH RPC")
                                return count
        except Exception as e:
            logger.warning(f"[minted] MegaETH RPC error for {mint.get('name')}: {e}")

    # ── 4. Solana — getTokenSupply via Helius RPC ──
    if contract and chain == 'solana':
        helius_key = os.environ.get('HELIUS_API_KEY', '')
        rpc_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}" if helius_key else "https://api.mainnet-beta.solana.com"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "getTokenSupply",
                    "params": [contract]
                }, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        amount = ((data or {}).get('result') or {}).get('value', {}).get('uiAmount')
                        if amount and int(amount) > 0:
                            count = int(amount)
                            logger.info(f"[minted] {mint.get('name')}: {count:,} via Solana RPC")
                            return count
        except Exception as e:
            logger.warning(f"[minted] Solana RPC error for {mint.get('name')}: {e}")

    # ── 5. OpenSea drops API fallback ──
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
                        logger.info(f"[minted] {mint.get('name')}: {minted:,} via OpenSea drops")
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
        logger.warning(f"[contract] fetch error for {slug}: {e}")
    return None
