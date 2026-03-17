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

    return {
      name: d.name,
      chain: normalizeChain(d.contracts?.[0]?.chain || d.chain),
      contract: d.contracts?.[0]?.address,
      total_supply: d.total_supply || null,
      os_link: `https://opensea.io/collection/${slug}`,
      x_link: d.twitter_username ? `https://x.com/${d.twitter_username}` : null,
      discord_link: d.discord_url || null,
      floor_price: d.stats?.floor_price || null,
    };
  } catch (err) {
    if (err.response?.status !== 404) {
      logger.debug(`OpenSea collection API error for ${slug}: ${err.message}`);
    }
    return null;
  }
}

/**
 * Fetch drop/mint phase data from OpenSea Drops API.
 */
async function fetchDropData(slug) {
  try {
    const resp = await axios.get(`${OS_API_BASE}/drops/${slug}`, {
      headers: osHeaders(),
      timeout: 10000,
    });

    let data = resp.data;
    if (!data) return null;
    if (data.drop) data = data.drop; // some responses nest under 'drop'

    const phases = [];

    // Try different phase shapes
    const phaseList = data.mint_stages || data.stages || data.phases || [];

    for (const stage of phaseList) {
      const time = stage.start_time || stage.startTime || stage.time;
      const endTime = stage.end_time || stage.endTime;
      const price = stage.price?.amount || stage.price || stage.mint_price;
      const limit = stage.limit_per_wallet || stage.limit || stage.max_per_wallet;

      phases.push({
        name: stage.name || stage.stage_name || 'Mint',
        time: time ? new Date(typeof time === 'number' ? time * 1000 : time).toISOString() : null,
        end_time: endTime ? new Date(typeof endTime === 'number' ? endTime * 1000 : endTime).toISOString() : null,
        price: normalizePriceStr(price),
        limit: limit || null,
      });
    }

    return {
      name: data.collection_name || data.name,
      chain: normalizeChain(data.chain),
      contract: data.contract_address || data.contracts?.[0]?.address,
      total_supply: data.total_supply || data.supply?.total || null,
      minted: data.minted_item_count || data.minted || 0,
      phases,
    };
  } catch (err) {
    if (err.response?.status !== 404) {
      logger.debug(`OpenSea drops API error for ${slug}: ${err.message}`);
    }
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
  fetchFloorPrice,
  fetchRecentSales,
  searchCollection,
  buildMarketLinks,
  normalizeChain,
};
