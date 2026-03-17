'use strict';

const axios = require('axios');
const { parseTime, normalizePriceStr } = require('../utils/parser');
const logger = require('../utils/logger');

const OS_API_BASE = 'https://api.opensea.io/api/v2';

function osHeaders() {
  const key = process.env.OPENSEA_API_KEY;
  return key ? { 'X-API-KEY': key } : {};
}

/**
 * Fetch collection metadata from OpenSea Collections API.
 */
async function fetchCollectionData(slug) {
  try {
    const resp = await axios.get(`${OS_API_BASE}/collections/${slug}`, {
      headers: osHeaders(),
      timeout: 10000,
    });
    const d = resp.data;
    if (!d) return null;

    const result = {
      name: d.name,
      chain: normalizeChain(d.contracts?.[0]?.chain || d.chain),
      contract: d.contracts?.[0]?.address,
      total_supply: d.total_supply || null,
      os_link: `https://opensea.io/collection/${slug}`,
      x_link: d.twitter_username ? `https://x.com/${d.twitter_username}` : null,
      discord_link: d.discord_url || null,
      floor_price: d.stats?.floor_price || null,
    };
    logger.debug(`OpenSea Collection API OK for ${slug}: name="${result.name}" chain=${result.chain} contract=${result.contract}`);
    return result;
  } catch (err) {
    const status = err.response?.status;
    if (status !== 404) {
      logger.warn(`OpenSea Collection API error for ${slug}: HTTP ${status ?? 'network'} — ${err.message}`);
    } else {
      logger.debug(`OpenSea Collection API: ${slug} not found (404)`);
    }
    return null;
  }
}

/**
 * Parse a raw drop/collection data object into our normalized shape.
 */
function parseDropData(data, slug) {
  if (!data) return null;
  if (data.drop) data = data.drop; // some responses nest under 'drop'

  const phases = [];
  const phaseList = data.mint_stages || data.stages || data.phases || [];

  for (const stage of phaseList) {
    const time    = stage.start_time || stage.startTime || stage.time;
    const endTime = stage.end_time   || stage.endTime;
    const price   = stage.price?.amount || stage.price || stage.mint_price;
    const limit   = stage.limit_per_wallet || stage.limit || stage.max_per_wallet;

    phases.push({
      name: stage.name || stage.stage_name || 'Mint',
      time: time    ? new Date(typeof time    === 'number' ? time    * 1000 : time).toISOString()    : null,
      end_time: endTime ? new Date(typeof endTime === 'number' ? endTime * 1000 : endTime).toISOString() : null,
      price: normalizePriceStr(price),
      limit: limit || null,
    });
  }

  return {
    name:         data.collection_name || data.name || null,
    chain:        normalizeChain(data.chain),
    contract:     data.contract_address || data.contracts?.[0]?.address || null,
    total_supply: data.total_supply || data.supply?.total || null,
    minted:       data.minted_item_count || data.minted || 0,
    phases,
  };
}

/**
 * Fetch drop/mint phase data from OpenSea Drops API.
 *
 * Tries two endpoints in order:
 *   1. GET /drops/{slug}          — launchpad-style drop
 *   2. GET /drops?collection_slug={slug} — collection-linked drop
 */
async function fetchDropData(slug) {
  // ── Attempt 1: direct drop slug endpoint ──────────────────────────────
  try {
    const resp = await axios.get(`${OS_API_BASE}/drops/${slug}`, {
      headers: osHeaders(),
      timeout: 10000,
    });
    const result = parseDropData(resp.data, slug);
    if (result) {
      logger.debug(`OpenSea Drops API (direct) OK for ${slug}: ${result.phases.length} phase(s)`);
      return result;
    }
  } catch (err) {
    const status = err.response?.status;
    if (status === 404) {
      logger.debug(`OpenSea Drops API: ${slug} not in drops system (404) — trying list endpoint`);
    } else {
      logger.warn(`OpenSea Drops API error for ${slug}: HTTP ${status ?? 'network'} — ${err.message}`);
    }
  }

  // ── Attempt 2: collection_slug query param ─────────────────────────────
  try {
    const resp = await axios.get(`${OS_API_BASE}/drops`, {
      headers: osHeaders(),
      params: { collection_slug: slug, limit: 1 },
      timeout: 10000,
    });
    const drops = resp.data?.drops || resp.data?.results || [];
    const match = drops.find(d => (d.collection_slug || d.slug) === slug) || drops[0];
    if (match) {
      const result = parseDropData(match, slug);
      if (result) {
        logger.debug(`OpenSea Drops API (list) found ${slug}: ${result.phases.length} phase(s)`);
        return result;
      }
    }
  } catch (err) {
    const status = err.response?.status;
    if (status !== 404) {
      logger.warn(`OpenSea Drops list API error for ${slug}: HTTP ${status ?? 'network'} — ${err.message}`);
    }
  }

  return null;
}

/**
 * Fetch the OpenSea /overview page as plain HTML (no JavaScript) and extract
 * __NEXT_DATA__ or any embedded JSON that contains phase data.
 *
 * Used as a fallback when the puppeteer browser gets a 403 from OpenSea,
 * because a plain HTTP request lacks the headless-browser fingerprint.
 *
 * Returns the raw parsed JSON object (NOT a normalized MintData) so that
 * the caller (scrapers/index.js::parseNextData) can walk it.
 * Returns null if the request fails or no usable JSON is found.
 */
async function fetchOverviewNextData(slug) {
  const url = `https://opensea.io/collection/${slug}/overview`;
  try {
    const resp = await axios.get(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Upgrade-Insecure-Requests': '1',
        'Cache-Control': 'no-cache',
      },
      timeout: 15000,
    });

    const html = typeof resp.data === 'string' ? resp.data : '';
    if (!html) return null;

    // Extract __NEXT_DATA__ embedded in <script id="__NEXT_DATA__">
    const nextDataMatch = html.match(/<script[^>]+id=["']__NEXT_DATA__["'][^>]*>([^<]+)<\/script>/i);
    if (nextDataMatch) {
      try {
        const parsed = JSON.parse(nextDataMatch[1]);
        logger.info(`OpenSea HTTP fallback: __NEXT_DATA__ extracted for ${slug}`);
        return parsed;
      } catch {
        logger.debug(`OpenSea HTTP fallback: __NEXT_DATA__ parse failed for ${slug}`);
      }
    }

    // Also scan for any <script type="application/json"> blocks
    const jsonScripts = [...html.matchAll(/<script[^>]+type=["']application\/json["'][^>]*>([^<]+)<\/script>/gi)];
    for (const m of jsonScripts) {
      try {
        const parsed = JSON.parse(m[1]);
        if (parsed && typeof parsed === 'object') {
          logger.info(`OpenSea HTTP fallback: JSON script block found for ${slug}`);
          return parsed;
        }
      } catch {}
    }

    logger.warn(`OpenSea HTTP fallback: no embedded JSON found for ${slug} (HTTP ${resp.status})`);
    return null;
  } catch (err) {
    const status = err.response?.status;
    logger.warn(`OpenSea HTTP fallback failed for ${slug}: HTTP ${status ?? 'network'} — ${err.message}`);
    return null;
  }
}

/**
 * Fetch floor price for a collection.
 */
async function fetchFloorPrice(slug) {
  try {
    const resp = await axios.get(`${OS_API_BASE}/collections/${slug}/stats`, {
      headers: osHeaders(),
      timeout: 8000,
    });
    const stats = resp.data?.stats;
    return stats?.floor_price != null ? parseFloat(stats.floor_price) : null;
  } catch {
    return null;
  }
}

/**
 * Fetch recent sale events (for sweep detection).
 */
async function fetchRecentSales(slug, limit = 50) {
  try {
    const resp = await axios.get(`${OS_API_BASE}/events/collection/${slug}`, {
      headers: osHeaders(),
      params: { event_type: 'sale', limit },
      timeout: 10000,
    });
    return resp.data?.asset_events || [];
  } catch {
    return [];
  }
}

/**
 * Search for a collection by name to find its slug.
 */
async function searchCollection(query) {
  try {
    const resp = await axios.get(`${OS_API_BASE}/collections`, {
      headers: osHeaders(),
      params: { limit: 5, include_hidden: false, collection_slug: query },
      timeout: 8000,
    });
    return resp.data?.collections || [];
  } catch {
    return [];
  }
}

function normalizeChain(chain) {
  if (!chain) return 'Ethereum';
  const map = {
    ethereum: 'Ethereum', base: 'Base', arbitrum: 'Arbitrum', optimism: 'Optimism',
    polygon: 'Polygon', blast: 'Blast', zora: 'Zora', solana: 'Solana',
    matic: 'Polygon', klaytn: 'Klaytn', avalanche: 'Avalanche', bsc: 'BNB',
    linea: 'Linea', abstract: 'Abstract', apechain: 'ApeChain',
  };
  return map[chain.toLowerCase()] || chain;
}

/**
 * Build market trading links for an EVM collection.
 */
function buildMarketLinks(contract, chain, slug) {
  if (!contract) return {};

  const chainSlugMap = {
    Ethereum: 'ethereum', Base: 'base', Arbitrum: 'arbitrum', Optimism: 'optimism',
    Polygon: 'matic', Blast: 'blast', Zora: 'zora', Linea: 'linea',
    Abstract: 'abstract', ApeChain: 'apechain',
  };
  const chainSlug = chainSlugMap[chain] || chain.toLowerCase();

  return {
    opensea: `https://opensea.io/assets/${chainSlug}/${contract}`,
    blur: chain === 'Ethereum' ? `https://blur.io/collection/${contract}` : null,
    magiceden: ['Ethereum','Base','Polygon'].includes(chain)
      ? `https://magiceden.io/collections/${chainSlug}/${contract}`
      : null,
  };
}

module.exports = {
  fetchCollectionData,
  fetchDropData,
  fetchOverviewNextData,
  fetchFloorPrice,
  fetchRecentSales,
  searchCollection,
  buildMarketLinks,
  normalizeChain,
};
