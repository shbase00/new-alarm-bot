'use strict';

const { scrapeMagicEden } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'MagicEden',
  domains: ['magiceden.io'],

  async scrape(url) {
    const raw = await scrapeMagicEden(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'MagicEden' }, url);
  },
};
