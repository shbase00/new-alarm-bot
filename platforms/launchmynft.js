'use strict';

const { scrapeLaunchMyNFT } = require('../scrapers/launchpad');
const { normalizeMintData } = require('../utils/model');

module.exports = {
  name: 'LaunchMyNFT',
  domains: ['launchmynft.io'],

  async scrape(url) {
    const raw = await scrapeLaunchMyNFT(url);
    if (!raw) return null;
    return normalizeMintData({ ...raw, platform: 'LaunchMyNFT' }, url);
  },
};
