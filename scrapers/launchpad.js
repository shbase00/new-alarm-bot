'use strict';

const axios = require('axios');
const { normalizePriceStr, parseTime } = require('../utils/parser');
const logger = require('../utils/logger');

// ── Foundation.app ─────────────────────────────────────────────────────────────

async function scrapeFoundation(url) {
  try {
    // Extract slug from foundation URL
    const match = url.match(/foundation\.app\/@([^/]+)\/([^/?#]+)/);
    if (!match) return null;
    const slug = match[2];

    // Foundation GraphQL API
    const query = `{
      artwork(tokenId: "${slug}") {
        name
        mintStartDate
        mintPrice
        maxTokenId
        totalSold
        nftCount
        creator { name }
      }
    }`;

    const resp = await axios.post('https://api.foundation.app/graphql', { query }, {
      timeout: 10000,
      headers: { 'Content-Type': 'application/json' },
    });

    const art = resp.data?.data?.artwork;
    if (!art) return null;

    return {
      name: art.name,
      chain: 'Ethereum',
      phases: art.mintStartDate ? [{
        name: 'Public Mint',
        time: art.mintStartDate,
        price: normalizePriceStr(art.mintPrice),
        limit: null,
      }] : [],
      total_supply: art.nftCount || art.maxTokenId,
      minted: art.totalSold || 0,
    };
  } catch (err) {
    logger.debug(`Foundation scrape failed: ${err.message}`);
    return null;
  }
}

// ── Highlight.xyz ──────────────────────────────────────────────────────────────

async function scrapeHighlight(url) {
  try {
    const match = url.match(/highlight\.xyz\/mint\/([^/?#]+)/);
    if (!match) return null;
    const id = match[1];

    const resp = await axios.get(`https://api.highlight.xyz/v1/collections/${id}`, {
      timeout: 10000,
    });
    const d = resp.data;
    if (!d) return null;

    const phases = [];
    if (d.vectorMintConfig || d.mintConfig) {
      const cfg = d.vectorMintConfig || d.mintConfig;
      phases.push({
        name: 'Public Mint',
        time: cfg.startTimestamp ? new Date(cfg.startTimestamp * 1000).toISOString() : null,
        price: normalizePriceStr(cfg.pricePerToken || cfg.price),
        limit: cfg.maxUserClaimable || null,
      });
    }

    return {
      name: d.name || d.collectionName,
      chain: normalizeChain(d.chainId),
      contract: d.contractAddress,
      total_supply: d.size || d.maxSupply,
      phases,
    };
  } catch (err) {
    logger.debug(`Highlight scrape failed: ${err.message}`);
    return null;
  }
}

// ── Manifold.xyz ───────────────────────────────────────────────────────────────

async function scrapeManifold(url) {
  try {
    // Extract claim ID from URL
    const match = url.match(/app\.manifold\.xyz\/c\/([^/?#]+)/);
    if (!match) return null;
    const slug = match[1];

    const resp = await axios.get(`https://apps.api.manifoldxyz.dev/public/instance?appId=1`, {
      params: { instanceId: slug },
      timeout: 10000,
    });
    const d = resp.data;
    if (!d) return null;

    const claim = d.publicData;
    return {
      name: d.name || 'Manifold Drop',
      chain: 'Ethereum',
      contract: claim?.contractAddress,
      total_supply: claim?.total,
      minted: claim?.totalMinted,
      phases: claim?.startDate ? [{
        name: 'Public Mint',
        time: new Date(claim.startDate * 1000).toISOString(),
        price: normalizePriceStr(claim.cost),
        limit: claim.walletMax || null,
      }] : [],
    };
  } catch (err) {
    logger.debug(`Manifold scrape failed: ${err.message}`);
    return null;
  }
}

// ── MagicEden (Solana/EVM) ────────────────────────────────────────────────────

async function scrapeMagicEden(url) {
  try {
    const match = url.match(/magiceden\.io\/(?:launchpad|mint-terminal|collections?)\/([^/?#]+)/i);
    if (!match) return null;
    const slug = match[1];

    // Try Solana launchpad API first
    const resp = await axios.get(`https://api-mainnet.magiceden.dev/v2/launchpad/collection/${slug}`, {
      timeout: 10000,
      headers: { 'User-Agent': 'Mozilla/5.0' },
    });
    const d = resp.data;
    if (!d) return null;

    const phases = [];
    if (d.mintStart) {
      phases.push({
        name: 'Public Mint',
        time: new Date(d.mintStart).toISOString(),
        price: normalizePriceStr(d.price ? `${d.price} SOL` : null),
        limit: d.maxMintPerWallet || null,
      });
    }
    if (d.wlMintStart) {
      phases.unshift({
        name: 'Whitelist',
        time: new Date(d.wlMintStart).toISOString(),
        price: normalizePriceStr(d.wlPrice ? `${d.wlPrice} SOL` : null),
        limit: d.maxWlMintPerWallet || null,
      });
    }

    return {
      name: d.name,
      chain: 'Solana',
      total_supply: d.size,
      minted: d.itemsAvailable - (d.itemsRemaining || d.itemsAvailable),
      phases,
      x_link: d.twitter ? `https://x.com/${d.twitter}` : null,
      discord_link: d.discord || null,
    };
  } catch (err) {
    logger.debug(`MagicEden scrape failed: ${err.message}`);
    return null;
  }
}

// ── Zora.co ───────────────────────────────────────────────────────────────────

async function scrapeZora(url) {
  try {
    const match = url.match(/zora\.co\/collect\/([^/?#]+)/);
    if (!match) return null;
    const id = match[1];

    // Zora API
    const resp = await axios.get(`https://api.zora.co/discover/tokens/${id}`, {
      timeout: 10000,
    });
    const d = resp.data;
    if (!d) return null;

    return {
      name: d.name || d.token?.name,
      chain: 'Zora',
      contract: d.address || d.token?.address,
      total_supply: d.totalSupply || d.token?.totalSupply,
      phases: [],
    };
  } catch (err) {
    logger.debug(`Zora scrape failed: ${err.message}`);
    return null;
  }
}

// ── Sound.xyz ─────────────────────────────────────────────────────────────────

async function scrapeSound(url) {
  try {
    const match = url.match(/sound\.xyz\/([^/]+)\/([^/?#]+)/);
    if (!match) return null;

    const query = `query { audioBySlug(slug: "${match[2]}", artistSlug: "${match[1]}") {
      title
      sale { mintStartTime price maxMintable totalMinted }
    }}`;

    const resp = await axios.post('https://api.sound.xyz/graphql', { query }, {
      timeout: 10000,
      headers: { 'Content-Type': 'application/json' },
    });
    const audio = resp.data?.data?.audioBySlug;
    if (!audio) return null;

    const sale = audio.sale;
    return {
      name: audio.title,
      chain: 'Ethereum',
      total_supply: sale?.maxMintable,
      minted: sale?.totalMinted || 0,
      phases: sale?.mintStartTime ? [{
        name: 'Public Mint',
        time: new Date(sale.mintStartTime).toISOString(),
        price: normalizePriceStr(sale.price),
        limit: null,
      }] : [],
    };
  } catch (err) {
    logger.debug(`Sound.xyz scrape failed: ${err.message}`);
    return null;
  }
}

// ── LaunchMyNFT ───────────────────────────────────────────────────────────────

async function scrapeLaunchMyNFT(url) {
  try {
    const match = url.match(/launchmynft\.io\/collections\/([^/?#]+)/);
    if (!match) return null;

    const resp = await axios.get(`https://api.launchmynft.io/collection/${match[1]}`, {
      timeout: 10000,
    });
    const d = resp.data;
    if (!d) return null;

    const phases = [];
    if (d.whitelistSaleStart) {
      phases.push({
        name: 'Whitelist',
        time: new Date(d.whitelistSaleStart).toISOString(),
        price: normalizePriceStr(d.whitelistSalePrice),
        limit: d.walletLimitWl || null,
      });
    }
    if (d.publicSaleStart) {
      phases.push({
        name: 'Public',
        time: new Date(d.publicSaleStart).toISOString(),
        price: normalizePriceStr(d.publicSalePrice),
        limit: d.walletLimitPublic || null,
      });
    }

    return {
      name: d.name,
      chain: normalizeChain(d.chain),
      contract: d.contractAddress,
      total_supply: d.maxSupply,
      minted: d.totalMinted || 0,
      phases,
    };
  } catch (err) {
    logger.debug(`LaunchMyNFT scrape failed: ${err.message}`);
    return null;
  }
}

// ── helpers ────────────────────────────────────────────────────────────────────

function normalizeChain(chainId) {
  if (!chainId) return 'Ethereum';
  const map = {
    1: 'Ethereum', 137: 'Polygon', 8453: 'Base', 42161: 'Arbitrum',
    10: 'Optimism', 81457: 'Blast', 7777777: 'Zora', 56: 'BNB',
  };
  return map[Number(chainId)] || String(chainId);
}

/**
 * Try all known launchpad scrapers for a URL.
 */
async function scrapeByPlatform(url) {
  const lower = url.toLowerCase();

  if (lower.includes('foundation.app')) return scrapeFoundation(url);
  if (lower.includes('highlight.xyz')) return scrapeHighlight(url);
  if (lower.includes('manifold.xyz') || lower.includes('app.manifold')) return scrapeManifold(url);
  if (lower.includes('magiceden.io')) return scrapeMagicEden(url);
  if (lower.includes('zora.co')) return scrapeZora(url);
  if (lower.includes('sound.xyz')) return scrapeSound(url);
  if (lower.includes('launchmynft.io')) return scrapeLaunchMyNFT(url);

  return null;
}

module.exports = {
  scrapeByPlatform,
  scrapeFoundation,
  scrapeHighlight,
  scrapeManifold,
  scrapeMagicEden,
  scrapeZora,
  scrapeSound,
  scrapeLaunchMyNFT,
};
