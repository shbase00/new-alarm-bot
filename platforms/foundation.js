'use strict';

const { scrapeFoundation } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'Foundation',
  domains: ['foundation.app'],

  async scrape(url) {
    const raw = await scrapeFoundation(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'Foundation' }, url);
  },
};
