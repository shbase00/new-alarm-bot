'use strict';

const { scrapeManifold } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'Manifold',
  domains: ['manifold.xyz', 'app.manifold'],

  async scrape(url) {
    const raw = await scrapeManifold(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'Manifold' }, url);
  },
};
