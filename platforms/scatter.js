'use strict';

/**
 * Scatter.art platform module.
 *
 * Scatter.art is an NFT launch platform supporting Ethereum and Base.
 * URL patterns:
 *   https://scatter.art/drop/SLUG
 *   https://scatter.art/USERNAME/COLLECTION
 *
 * The public API endpoint is attempted first; if it returns nothing the
 * browser scraper (Layer 4) will pick it up as a generic page.
 */

const axios = require('axios');
const { normalizeMintData } = require('../utils/model');
const { normalizePriceStr } = require('../utils/parser');
const logger = require('../utils/logger');

// Chain ID → name map for Scatter's chain field
const CHAIN_MAP = {
  1: 'Ethereum',
  8453: 'Base',
  137: 'Polygon',
  42161: 'Arbitrum',
  10: 'Optimism',
};

function resolveChain(raw) {
  if (!raw) return 'Ethereum';
  const n = Number(raw);
  if (!isNaN(n)) return CHAIN_MAP[n] || `Chain ${n}`;
  const s = String(raw).toLowerCase();
  if (s === 'ethereum' || s === 'eth') return 'Ethereum';
  if (s === 'base') return 'Base';
  if (s === 'polygon' || s === 'matic') return 'Polygon';
  if (s === 'arbitrum' || s === 'arb') return 'Arbitrum';
  if (s === 'optimism' || s === 'op') return 'Optimism';
  return String(raw);
}

module.exports = {
  name: 'Scatter',
  domains: ['scatter.art'],

  async scrape(url) {
    try {
      // Extract slug from URL formats:
      //   scatter.art/drop/SLUG  →  match[2] = SLUG
      //   scatter.art/USER/SLUG  →  match[1]/match[2]
      const m = url.match(/scatter\.art\/(?:drop\/)?([^/?#]+)(?:\/([^/?#]+))?/i);
      if (!m) return null;
      const slug = m[2] || m[1];
      if (!slug || slug === 'drop') return null;

      // Try Scatter API (undocumented but publicly accessible)
      const resp = await axios.get(`https://scatter.art/api/v1/drops/${slug}`, {
        timeout: 10000,
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
          'Accept': 'application/json',
        },
      });

      const d = resp.data?.drop || resp.data;
      if (!d) return null;

      const phases = [];

      // Allowlist / whitelist phase
      if (d.allowlistSaleStartTime || d.wlStartTime || d.presaleStartTime) {
        phases.push({
          name: 'Allowlist',
          time: new Date(
            d.allowlistSaleStartTime || d.wlStartTime || d.presaleStartTime
          ).toISOString(),
          price: normalizePriceStr(
            d.allowlistPrice ?? d.wlPrice ?? d.presalePrice ?? null
          ),
          limit: d.allowlistMaxPerWallet ?? d.wlMaxPerWallet ?? null,
        });
      }

      // Public phase
      if (d.publicSaleStartTime || d.saleStartTime || d.startTime) {
        phases.push({
          name: 'Public',
          time: new Date(
            d.publicSaleStartTime || d.saleStartTime || d.startTime
          ).toISOString(),
          price: normalizePriceStr(
            d.publicPrice ?? d.salePrice ?? d.price ?? null
          ),
          limit: d.publicMaxPerWallet ?? d.maxPerWallet ?? null,
        });
      }

      return normalizeMintData({
        name: d.name || d.collectionName || d.title,
        chain: resolveChain(d.chainId || d.chain),
        contract: d.contractAddress || d.contract,
        total_supply: d.maxSupply || d.totalSupply,
        minted: d.totalMinted || d.minted || 0,
        phases,
        x_link: d.twitter ? `https://x.com/${d.twitter}` : (d.twitterUrl || null),
        discord_link: d.discord || d.discordUrl || null,
        platform: 'Scatter',
      }, url);
    } catch (err) {
      // 404 = slug not found via API; browser scraper (Layer 4) will attempt
      if (err.response?.status !== 404) {
        logger.debug(`Scatter scrape failed for ${url}: ${err.message}`);
      }
      return null;
    }
  },
};
