'use strict';

/**
 * Custom / unknown platform module.
 *
 * Acts as a marker for URLs that didn't match any known platform.
 * Actual scraping for custom URLs is handled by the generic browser
 * scraper in scrapers/index.js (Layer 4).
 *
 * This module exists so platform detection always returns a structured
 * result rather than null, and so the platform field in the database
 * is populated meaningfully.
 */

module.exports = {
  name: 'Custom',
  domains: [], // matches nothing — only used as a fallback label

  // No API scraper for custom platforms; browser Layer 4 handles them
  async scrape(_url) {
    return null;
  },
};
