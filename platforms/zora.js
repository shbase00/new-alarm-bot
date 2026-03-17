'use strict';

const { scrapeZora } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'Zora',
  domains: ['zora.co'],

  async scrape(url) {
    const raw = await scrapeZora(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'Zora' }, url);
  },
};
