'use strict';

/**
 * OpenSea platform module.
 *
 * Coordinates both the OpenSea REST APIs (Drops + Collections) and returns
 * merged data. Browser scraping of /overview pages is handled separately in
 * scrapers/index.js (Layer 4) since it requires puppeteer infrastructure.
 */

const { fetchDropData, fetchCollectionData } = require('../scrapers/opensea');
const { extractOpenSeaSlug } = require('../utils/parser');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'OpenSea',
  domains: ['opensea.io'],

  async scrape(url) {
    const slug = extractOpenSeaSlug(url);
    if (!slug) return null;

    // Run both APIs concurrently — Drops has phases, Collections has metadata
    const [dropResult, collectionResult] = await Promise.allSettled([
      fetchDropData(slug),
      fetchCollectionData(slug),
    ]);

    const drop = dropResult.status === 'fulfilled' ? dropResult.value : null;
    const collection = collectionResult.status === 'fulfilled' ? collectionResult.value : null;

    if (!drop && !collection) return null;

    const merged = {
      name: collection?.name || drop?.name,
      chain: collection?.chain || drop?.chain,
      contract: collection?.contract || drop?.contract,
      total_supply: drop?.total_supply || collection?.total_supply,
      minted: drop?.minted || 0,
      phases: drop?.phases || [],
      x_link: collection?.x_link || null,
      discord_link: collection?.discord_link || null,
      os_link: `https://opensea.io/collection/${slug}`,
      platform: 'OpenSea',
    };

    return normalizeMintData(merged, url);
  },
};
