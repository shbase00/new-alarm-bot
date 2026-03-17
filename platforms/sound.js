'use strict';

const { scrapeSound } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'Sound',
  domains: ['sound.xyz'],

  async scrape(url) {
    const raw = await scrapeSound(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'Sound' }, url);
  },
};
