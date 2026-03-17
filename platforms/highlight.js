'use strict';

const { scrapeHighlight } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'Highlight',
  domains: ['highlight.xyz'],

  async scrape(url) {
    const raw = await scrapeHighlight(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'Highlight' }, url);
  },
};
