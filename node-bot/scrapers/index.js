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
  // Text-based chain detection as final fallback
  if (result.chain === 'Ethereum' && browser?.text) {
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
    if (isOpenSea) return scrapeOpenSeaPage(url);

    // Generic page scrape for phase data
    const { text } = await scrapeUrl(url, { waitMs: 4000, timeout: 40000 });
    return parseGenericPageText(text);
  } catch (err) {
    logger.debug(`Browser scrape failed for ${url}: ${err.message}`);
    return null;
  }
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

    // Navigate here (not in scrapeWithCallback) so interception is already active
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 });

    // ── Pass 1: __NEXT_DATA__ ──────────────────────────────────────────────
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

    // ── Pass 2: wait up to 8 s more for XHR responses then check them ─────
    await new Promise(r => setTimeout(r, 8000));

    for (const body of intercepted) {
      const result = parseOpenSeaApiBody(body);
      if (result && result.phases.length > 0) {
        logger.info(`OpenSea XHR intercept found ${result.phases.length} phase(s) for ${url}`);
        return result;
      }
    }

    // ── Pass 3: innerText fallback ─────────────────────────────────────────
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

  // Walk the props tree looking for drop/mint stage structures
  function walk(obj, depth = 0) {
    if (!obj || typeof obj !== 'object' || depth > 12) return;

    // Direct mint_stages / stages array
    const stageList = obj.mint_stages || obj.mintStages || obj.stages ||
                      obj.dropStages || obj.mintPhases || obj.phases;
    if (Array.isArray(stageList) && stageList.length > 0) {
      for (const s of stageList) {
        const time = s.startTime || s.start_time || s.startTimestamp || s.mintStartTime;
        const endTime = s.endTime || s.end_time;
        const price = s.price?.amount ?? s.mintPrice ?? s.price ?? s.cost;
        const limit = s.limit ?? s.walletLimit ?? s.maxPerWallet ?? s.limitPerWallet;
        phases.push({
          name: s.name || s.stageName || s.label || 'Mint',
          time: time ? new Date(typeof time === 'number' ? time * 1000 : time).toISOString() : null,
          end_time: endTime ? new Date(typeof endTime === 'number' ? endTime * 1000 : endTime).toISOString() : null,
          price: normalizePriceStr(price),
          limit: limit ?? null,
        });
      }
      return; // found what we need
    }

    // Grab collection metadata opportunistically
    if (!name && (obj.name || obj.collectionName)) name = obj.name || obj.collectionName;
    if (!contract && obj.contractAddress) contract = obj.contractAddress;
    if (!total_supply && obj.totalSupply) total_supply = Number(obj.totalSupply);
    if (!minted && obj.mintedItemCount) minted = Number(obj.mintedItemCount);
    if (!chain && obj.chain) chain = obj.chain;

    for (const v of Object.values(obj)) {
      if (v && typeof v === 'object') walk(v, depth + 1);
    }
  }

  walk(data?.props ?? data);

  if (phases.length === 0) return null;
  return { phases, name, chain, contract, total_supply, minted };
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
 * Parse phase schedule from OpenSea page text.
 */
function parseOpenSeaPageText(text) {
  const phases = [];

  if (!text) return { phases };

  // Look for countdown pattern
  const countdownMatch = text.match(/[Mm]inting\s+in\s+(\d+)\s*(?:days?|d)?\s*(?:(\d+)\s*(?:hours?|h))?\s*(?:(\d+)\s*(?:min(?:utes?)?|m))?/);
  if (countdownMatch) {
    const d = parseInt(countdownMatch[1] || 0);
    const h = parseInt(countdownMatch[2] || 0);
    const m = parseInt(countdownMatch[3] || 0);
    const time = new Date(Date.now() + (d * 86400 + h * 3600 + m * 60) * 1000).toISOString();
    phases.push({ name: 'Public Mint', time, price: 'TBD', limit: null });
    return { phases, countdown_detected: true };
  }

  // Parse schedule list items
  // Typical format: "Allowlist\nMarch 15 at 3:00 PM UTC\n0.05 ETH"
  const lines = text.split('\n').map(l => l.trim()).filter(Boolean);
  const months = ['january','february','march','april','may','june','july','august','september','october','november','december'];

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    // Check if next line is a date
    if (i + 1 < lines.length) {
      const dateLine = lines[i + 1];
      const isDate = months.some(m => dateLine.toLowerCase().includes(m));

      if (isDate) {
        const phase = { name: line, time: null, price: 'TBD', limit: null };

        // Parse the date line
        const parsed = parseTime(dateLine);
        if (parsed) phase.time = parsed.toISOString();

        // Check for price in subsequent lines
        if (i + 2 < lines.length) {
          const priceLine = lines[i + 2];
          const priceMatch = priceLine.match(/(\d+\.?\d*)\s*(ETH|SOL|MATIC|BNB|AVAX)/i);
          if (priceMatch) {
            phase.price = `${priceMatch[1]} ${priceMatch[2].toUpperCase()}`;
            i += 3;
          } else if (priceLine.toLowerCase().includes('free')) {
            phase.price = 'Free';
            i += 3;
          } else {
            i += 2;
          }
        } else {
          i += 2;
        }

        phases.push(phase);
        continue;
      }
    }
    i++;
  }

  // Fallback: look for price patterns
  if (phases.length === 0) {
    const priceMatch = text.match(/(\d+\.?\d*)\s*(ETH|SOL|MATIC|BNB)/i);
    if (priceMatch) {
      phases.push({
        name: 'Public Mint',
        time: null,
        price: `${priceMatch[1]} ${priceMatch[2].toUpperCase()}`,
        limit: null,
      });
    }
  }

  return { phases };
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
