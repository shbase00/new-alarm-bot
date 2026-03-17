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

    if (isOpenSea) {
      return scrapeOpenSeaPage(url);
    }

    // Generic page scrape for phase data
    const { text } = await scrapeUrl(url, {
      waitMs: 4000,
      timeout: 40000,
    });
    return parseGenericPageText(text);
  } catch (err) {
    logger.debug(`Browser scrape failed for ${url}: ${err.message}`);
    return null;
  }
}

/**
 * Scrape OpenSea collection/drop page for phase schedule.
 */
async function scrapeOpenSeaPage(url) {
  return scrapeWithCallback(url, async (page) => {
    // Wait for mint schedule section or page to settle
    const timeout = 30000;
    const settled = await Promise.race([
      page.waitForSelector('[data-testid="drop-details"], .MintSchedule, [class*="MintSchedule"]', { timeout }).catch(() => null),
      page.waitForFunction(
        () => document.body.innerText.match(/January|February|March|April|May|June|July|August|September|October|November|December/),
        { timeout }
      ).catch(() => null),
      new Promise(r => setTimeout(r, timeout)),
    ]);

    const text = await page.evaluate(() => document.body?.innerText || '');
    const result = parseOpenSeaPageText(text);
    result.text = text;
    return result;
  });
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
