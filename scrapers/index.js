'use strict';

/**
 * Multi-layer detection pipeline for NFT mint data.
 *
 * Layer 1: Launchpad-specific APIs (Foundation, Highlight, Manifold, MagicEden, Zora, Sound, LaunchMyNFT)
 * Layer 2: OpenSea Drops API (phases & supply)
 * Layer 3: OpenSea Collections API (metadata & floor)
 * Layer 4: Stealth browser scraping (JS-rendered pages)
 * Layer 5: Smart contract monitoring (totalSupply via ethers.js)
 * Layer 6: Fallback / manual
 */

const { fetchCollectionData, fetchDropData, buildMarketLinks } = require('./opensea');
const { scrapeByPlatform } = require('./launchpad');
const { scrapeUrl, scrapeWithCallback } = require('./browser');
const { extractOpenSeaSlug, extractPlatform, detectChainFromUrl, detectChainFromText, parseTime, normalizePriceStr } = require('../utils/parser');
const logger = require('../utils/logger');

/**
 * Run all detection layers in parallel and merge results.
 * Returns a mint data object ready for createMint().
 */
async function detectMint(mintUrl) {
  logger.info(`Starting multi-layer detection for ${mintUrl}`);

  const slug = extractOpenSeaSlug(mintUrl);
  const platform = extractPlatform(mintUrl);

  // Run all layers concurrently
  const [launchpadResult, osDropResult, osCollectionResult, browserResult] = await Promise.allSettled([
    // Layer 1: Platform-specific launchpad API
    scrapeByPlatform(mintUrl),

    // Layer 2: OpenSea Drops API
    slug ? fetchDropData(slug) : Promise.resolve(null),

    // Layer 3: OpenSea Collections API
    slug ? fetchCollectionData(slug) : Promise.resolve(null),

    // Layer 4: Stealth browser scraping
    scrapeBrowserPhases(mintUrl),
  ]);

  const launchpad = launchpadResult.status === 'fulfilled' ? launchpadResult.value : null;
  const osDrop = osDropResult.status === 'fulfilled' ? osDropResult.value : null;
  const osCollection = osCollectionResult.status === 'fulfilled' ? osCollectionResult.value : null;
  const browser = browserResult.status === 'fulfilled' ? browserResult.value : null;

  logger.info(`Detection results - Launchpad: ${!!launchpad}, OSDrop: ${!!osDrop}, OSCollection: ${!!osCollection}, Browser: ${!!browser}`);

  // Merge all results, prioritizing the most complete source
  const merged = mergeDetectionResults({ launchpad, osDrop, osCollection, browser, mintUrl });

  // Build market links if we have a contract
  if (merged.contract && merged.chain) {
    merged.market_links = buildMarketLinks(merged.contract, merged.chain, slug);
    if (slug && !merged.os_link) {
      merged.os_link = `https://opensea.io/collection/${slug}`;
    }
  }

  return merged;
}

/**
 * Merge detection layer results with priority:
 * launchpad > osDrop > browser > osCollection (for phases)
 * osCollection > osDrop > launchpad (for metadata)
 */
function mergeDetectionResults({ launchpad, osDrop, osCollection, browser, mintUrl }) {
  const result = {
    name: null,
    chain: detectChainFromUrl(mintUrl) || 'Ethereum',
    mint_link: mintUrl,
    phases: [],
    contract: null,
    total_supply: null,
    minted: 0,
    x_link: null,
    discord_link: null,
    os_link: null,
    market_links: {},
    needs_manual: false,
  };

  // Collect all sources for merging
  const sources = [osCollection, osDrop, launchpad, browser].filter(Boolean);

  // Name: first non-null from priority sources
  for (const s of [osCollection, osDrop, launchpad, browser]) {
    if (s?.name) { result.name = s.name; break; }
  }

  // Chain: launchpad/drop knows best for non-OpenSea platforms
  for (const s of [launchpad, osDrop, osCollection, browser]) {
    if (s?.chain) { result.chain = s.chain; break; }
  }
  // Text-based chain detection ONLY when no API source returned a chain
  // (avoids overriding a correct "Ethereum" with unrelated page mentions of other chains)
  const hasApiChain = [launchpad, osDrop, osCollection].some(s => s?.chain);
  if (!hasApiChain && browser?.text) {
    result.chain = detectChainFromText(browser.text) || result.chain;
  }

  // Contract: any source that has it
  for (const s of sources) {
    if (s?.contract) { result.contract = s.contract; break; }
  }

  // Supply
  for (const s of [osDrop, launchpad, osCollection]) {
    if (s?.total_supply) { result.total_supply = s.total_supply; break; }
  }

  // Minted count
  for (const s of [osDrop, launchpad]) {
    if (s?.minted) { result.minted = s.minted; break; }
  }

  // Phases: prioritize most detailed source
  const phaseSources = [launchpad, osDrop, browser].filter(s => s?.phases?.length > 0);
  if (phaseSources.length > 0) {
    // Pick the source with the most phase data
    result.phases = phaseSources.reduce((best, s) =>
      s.phases.length >= best.phases.length ? s : best
    ).phases;
  }

  // Socials
  for (const s of sources) {
    if (s?.x_link) { result.x_link = s.x_link; break; }
  }
  for (const s of sources) {
    if (s?.discord_link) { result.discord_link = s.discord_link; break; }
  }
  for (const s of sources) {
    if (s?.os_link) { result.os_link = s.os_link; break; }
  }

  // Flag if no usable data was found
  if (!result.name && result.phases.length === 0) {
    result.needs_manual = true;
    result.name = result.name || 'Unknown Collection';
  }

  return result;
}

/**
 * Layer 4: Stealth browser scraping for OpenSea and other JS-heavy pages.
 */
async function scrapeBrowserPhases(url) {
  try {
    const isOpenSea = url.toLowerCase().includes('opensea.io');
    if (isOpenSea) {
      // Always scrape the /overview subpage — that's where OpenSea renders
      // the full mint schedule with all phases and times
      const overviewUrl = toOpenSeaOverviewUrl(url);
      return scrapeOpenSeaPage(overviewUrl);
    }

    // Generic page scrape for phase data
    const { text } = await scrapeUrl(url, { waitMs: 4000, timeout: 40000 });
    return parseGenericPageText(text);
  } catch (err) {
    logger.debug(`Browser scrape failed for ${url}: ${err.message}`);
    return null;
  }
}

/**
 * Normalize any opensea.io/collection/<slug>[/*] URL to the /overview subpage.
 */
function toOpenSeaOverviewUrl(url) {
  // Strip existing subpaths after the slug, then append /overview
  const m = url.match(/^(https?:\/\/opensea\.io\/collection\/[^/?#]+)/i);
  if (m) return `${m[1]}/overview`;
  return url;
}

/**
 * Scrape OpenSea collection/drop page.
 *
 * Three-pass strategy (most reliable → least):
 *   1. window.__NEXT_DATA__  — Next.js SSR payload, available immediately
 *   2. Intercepted XHR/fetch — OpenSea's own internal API calls
 *   3. innerText parsing     — last resort, requires visible text
 */
async function scrapeOpenSeaPage(url) {
  return scrapeWithCallback(url, async (page) => {
    // Collect intercepted API responses that contain drop/stage data
    const intercepted = [];

    // Must set up interception BEFORE goto
    await page.setRequestInterception(true);
    page.on('request', req => req.continue().catch(() => {}));
    page.on('response', async resp => {
      try {
        const respUrl = resp.url();
        if (
          respUrl.includes('api.opensea.io') ||
          respUrl.includes('/graphql') ||
          respUrl.includes('/drops/') ||
          respUrl.includes('/collections/')
        ) {
          const ct = resp.headers()['content-type'] || '';
          if (ct.includes('json')) {
            const body = await resp.json().catch(() => null);
            if (body) intercepted.push(body);
          }
        }
      } catch {}
    });

    // Navigate — interception already active
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });

    // ── Pass 1: __NEXT_DATA__ (available right after DOMContentLoaded) ─────
    const nextData = await page.evaluate(() => {
      try {
        const el = document.getElementById('__NEXT_DATA__');
        return el ? JSON.parse(el.textContent) : null;
      } catch { return null; }
    }).catch(() => null);

    if (nextData) {
      const result = parseNextData(nextData);
      if (result && result.phases.length > 0) {
        logger.info(`OpenSea __NEXT_DATA__ found ${result.phases.length} phase(s) for ${url}`);
        return result;
      }
    }

    // ── Pass 2: poll for XHR data and DOM content up to 18 s ──────────────
    // Phase data is loaded by client-side fetches after hydration.
    // Poll every 2 s so we exit as soon as data arrives.
    const MONTH_RE = /January|February|March|April|May|June|July|August|September|October|November|December/;
    const PRICE_RE = /\d+\.?\d*\s*ETH|\bfree\b/i; // also match free mints
    const deadline = Date.now() + 18000;

    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 2000));

      // Check intercepted XHR responses first
      for (const body of intercepted) {
        const r = parseOpenSeaApiBody(body);
        if (r && r.phases.length > 0) {
          logger.info(`OpenSea XHR intercept found ${r.phases.length} phase(s) for ${url}`);
          return r;
        }
      }

      // Check all embedded JSON script tags (Next.js injects more after hydration)
      const scriptResult = await page.evaluate(() => {
        const scripts = [...document.querySelectorAll('script[type="application/json"], script#__NEXT_DATA__')];
        return scripts.map(s => { try { return JSON.parse(s.textContent); } catch { return null; } }).filter(Boolean);
      }).catch(() => []);

      for (const blob of scriptResult) {
        const r = parseNextData(blob);
        if (r && r.phases.length > 0) {
          logger.info(`OpenSea script tag found ${r.phases.length} phase(s) for ${url}`);
          return r;
        }
      }

      // Early exit: page text has dates and prices — text parser can handle it
      const bodyText = await page.evaluate(() => document.body?.innerText || '').catch(() => '');
      if (MONTH_RE.test(bodyText) && PRICE_RE.test(bodyText)) {
        const r = parseOpenSeaPageText(bodyText);
        if (r.phases.length > 0) {
          logger.info(`OpenSea text parse found ${r.phases.length} phase(s) for ${url}`);
          r.text = bodyText;
          return r;
        }
      }
    }

    // ── Pass 3: final innerText attempt ───────────────────────────────────
    const text = await page.evaluate(() => document.body?.innerText || '').catch(() => '');
    const result = parseOpenSeaPageText(text);
    result.text = text;
    if (result.phases.length > 0) {
      logger.info(`OpenSea text parse found ${result.phases.length} phase(s) for ${url}`);
    } else {
      logger.debug(`OpenSea scrape: no phases found for ${url}`);
    }
    return result;

  }, 35000, { navigate: false });
}

/**
 * Extract phase data from window.__NEXT_DATA__.
 *
 * OpenSea embeds the drop/collection state in the Next.js SSR props under
 * several possible key paths depending on page type.
 */
function parseNextData(data) {
  const phases = [];
  let name = null, chain = null, contract = null, total_supply = null, minted = 0;

  function toIso(val) {
    if (!val) return null;
    const n = Number(val);
    if (!isNaN(n) && n > 1e9) return new Date(n < 1e12 ? n * 1000 : n).toISOString();
    const d = new Date(val);
    return isNaN(d) ? null : d.toISOString();
  }

  function extractStage(s) {
    // Cover all field names seen across OpenSea page types and API versions
    const time =
      s.startTime ?? s.start_time ?? s.startTimestamp ?? s.mintStartTime ??
      s.start ?? s.begins_at ?? s.scheduled_start_time ?? s.opensAt;
    const endTime =
      s.endTime ?? s.end_time ?? s.endTimestamp ?? s.mintEndTime ??
      s.end ?? s.ends_at ?? s.scheduled_end_time ?? s.closesAt;
    const price =
      s.price?.amount ?? s.price?.value ?? s.mintPrice ?? s.cost ??
      s.price ?? s.pricePerToken ?? s.unit_price;
    const limit =
      s.limit ?? s.walletLimit ?? s.maxPerWallet ?? s.limitPerWallet ??
      s.max_per_wallet ?? s.wallet_limit;

    return {
      name: s.name ?? s.stageName ?? s.stage_name ?? s.label ?? s.type ?? 'Mint',
      time: toIso(time),
      end_time: toIso(endTime),
      price: normalizePriceStr(price),
      limit: limit ?? null,
    };
  }

  // Walk the props tree looking for drop/mint stage structures
  function walk(obj, depth = 0) {
    if (!obj || typeof obj !== 'object' || depth > 14) return;

    // All known stage-list field names across OpenSea page types
    const stageList =
      obj.mint_stages ?? obj.mintStages ?? obj.stages ?? obj.dropStages ??
      obj.mintPhases ?? obj.phases ?? obj.drop_stages ?? obj.saleStages ??
      obj.mintSchedule ?? obj.schedule ?? obj.allowlist_stages ??
      obj.public_stages ?? obj.presale_stages;

    if (Array.isArray(stageList) && stageList.length > 0) {
      for (const s of stageList) {
        phases.push(extractStage(s));
      }
      // Don't return — keep walking for metadata
    }

    // Grab collection metadata opportunistically
    if (!name) name = obj.name ?? obj.collectionName ?? obj.collection_name ?? null;
    if (!contract) contract = obj.contractAddress ?? obj.contract_address ?? obj.address ?? null;
    if (!total_supply && (obj.totalSupply || obj.total_supply))
      total_supply = Number(obj.totalSupply ?? obj.total_supply);
    if (!minted && (obj.mintedItemCount || obj.minted_count || obj.totalMinted))
      minted = Number(obj.mintedItemCount ?? obj.minted_count ?? obj.totalMinted);
    if (!chain) chain = obj.chain ?? obj.network ?? null;

    for (const v of Object.values(obj)) {
      if (v && typeof v === 'object') walk(v, depth + 1);
    }
  }

  walk(data?.props ?? data);

  // Deduplicate phases by (name + time)
  const seen = new Set();
  const unique = phases.filter(p => {
    const key = `${p.name}|${p.time}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  if (unique.length === 0) return null;
  return { phases: unique, name, chain, contract, total_supply, minted };
}

/**
 * Parse a captured OpenSea API/GraphQL JSON body for phase data.
 */
function parseOpenSeaApiBody(body) {
  if (!body) return null;

  // Try the same recursive walk used for __NEXT_DATA__
  const result = parseNextData(body);
  if (result) return result;

  // GraphQL response shape: { data: { drop: { ... } } }
  const drop = body?.data?.drop || body?.data?.collection || body?.drop || body?.collection;
  if (drop) return parseNextData(drop);

  return null;
}

/**
 * Parse phase schedule from OpenSea /overview page text.
 *
 * OpenSea overview renders phases in blocks like:
 *
 *   Allowlist
 *   March 18 at 3:00 PM UTC
 *   March 18 at 5:00 PM UTC        ← end time (optional)
 *   0.05 ETH · max 2
 *
 *   Public
 *   March 18 at 5:00 PM UTC
 *   0.08 ETH · max 5
 */
function parseOpenSeaPageText(text) {
  if (!text) return { phases: [] };

  // Countdown: "Minting in 1 day 3 hours 20 minutes"
  const countdownMatch = text.match(
    /[Mm]inting\s+in\s+(?:(\d+)\s*days?)?\s*(?:(\d+)\s*hours?)?\s*(?:(\d+)\s*min(?:utes?)?)?/
  );
  if (countdownMatch && (countdownMatch[1] || countdownMatch[2] || countdownMatch[3])) {
    const d = parseInt(countdownMatch[1] || 0);
    const h = parseInt(countdownMatch[2] || 0);
    const m = parseInt(countdownMatch[3] || 0);
    const time = new Date(Date.now() + (d * 86400 + h * 3600 + m * 60) * 1000).toISOString();
    return { phases: [{ name: 'Public Mint', time, price: 'TBD', limit: null }], countdown_detected: true };
  }

  const MONTH_RE = /^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d/i;
  const PRICE_RE = /^(\d+\.?\d*)\s*(ETH|SOL|MATIC|BNB|AVAX)/i;
  const FREE_RE  = /^free\b/i;
  const LIMIT_RE = /max\s+(\d+)/i;

  // Standalone currency/chain labels OpenSea renders between phase name and date
  const SKIP_LINE = new Set([
    'eth', 'ethereum', 'sol', 'solana', 'matic', 'polygon', 'base', 'bnb',
    'avax', 'avalanche', 'arb', 'arbitrum', 'op', 'optimism', 'blast',
    'zora', 'linea', 'abstract', 'apechain', 'starknet', 'btc', 'bitcoin',
    'usdc', 'usdt', 'weth',
  ]);

  function extractPrice(line) {
    const m = line.match(PRICE_RE);
    if (m) {
      const lim = line.match(LIMIT_RE);
      return { price: `${m[1]} ${m[2].toUpperCase()}`, limit: lim ? lim[1] : null };
    }
    if (FREE_RE.test(line)) {
      const lim = line.match(LIMIT_RE);
      return { price: 'Free', limit: lim ? lim[1] : null };
    }
    return null;
  }

  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);

  // ── Date-anchored strategy ─────────────────────────────────────────────
  // Handles all observed OpenSea /overview layout variants:
  //   A) phase-name → [ETH] → date → [end-date] → price
  //   B) phase-name → date → price
  //   C) price/Free → date (price appears before date)
  //   D) [ETH] → date (no explicit phase name)

  const phases = [];
  const usedAsEndTime = new Set();

  for (let di = 0; di < lines.length; di++) {
    if (!MONTH_RE.test(lines[di])) continue;
    if (usedAsEndTime.has(di)) continue; // already consumed as end-time of previous phase

    const startParsed = parseTime(lines[di]);
    if (!startParsed) continue;

    const phase = { name: 'Public Mint', time: startParsed.toISOString(), end_time: null, price: 'TBD', limit: null };

    // Look back up to 4 lines for phase name and/or price-before-date
    for (let back = di - 1; back >= Math.max(0, di - 4); back--) {
      const bl = lines[back];
      if (!bl || MONTH_RE.test(bl)) break; // hit another date block
      if (SKIP_LINE.has(bl.toLowerCase())) continue;
      const priceInfo = extractPrice(bl);
      if (priceInfo) {
        // Price sits before the date (e.g. "Free\nMarch 19 at 2:00 PM UTC")
        if (phase.price === 'TBD') { phase.price = priceInfo.price; phase.limit = priceInfo.limit; }
        continue;
      }
      // Non-date, non-price, non-skip → it's the phase name
      phase.name = bl;
      break;
    }

    // Look forward for optional end-time then price
    let j = di + 1;
    if (j < lines.length && MONTH_RE.test(lines[j])) {
      const endParsed = parseTime(lines[j]);
      if (endParsed) { phase.end_time = endParsed.toISOString(); usedAsEndTime.add(j); j++; }
    }
    // Skip currency labels before price
    while (j < lines.length && SKIP_LINE.has(lines[j].toLowerCase())) j++;
    if (j < lines.length && phase.price === 'TBD') {
      const priceInfo = extractPrice(lines[j]);
      if (priceInfo) { phase.price = priceInfo.price; phase.limit = priceInfo.limit; }
    }

    phases.push(phase);
  }

  // Deduplicate by (name + time)
  const seen = new Set();
  const unique = phases.filter(p => {
    const key = `${p.name}|${p.time}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Fallback: no dates found but page has a price → create a TBD phase
  if (unique.length === 0) {
    const priceMatch = text.match(/(\d+\.?\d*)\s*(ETH|SOL|MATIC|BNB)/i);
    if (priceMatch) {
      unique.push({ name: 'Public Mint', time: null, price: `${priceMatch[1]} ${priceMatch[2].toUpperCase()}`, limit: null });
    }
  }

  return { phases: unique };
}

/**
 * Parse generic page text for phase data.
 */
function parseGenericPageText(text) {
  if (!text) return { phases: [] };
  const osResult = parseOpenSeaPageText(text);
  return { phases: osResult.phases, text };
}

module.exports = {
  detectMint,
  mergeDetectionResults,
  scrapeBrowserPhases,
  scrapeOpenSeaPage,
  parseOpenSeaPageText,
};
