'use strict';

/**
 * Platform detection and routing.
 *
 * Each platform module exports:
 *   { name, domains, scrape }
 *
 * The router matches a URL against domain arrays (most specific first),
 * calls the platform's scrape() function, and returns normalized MintData.
 *
 * Supported platforms:
 *   OpenSea, Manifold, Highlight, LaunchMyNFT, Foundation,
 *   MagicEden, Zora, Sound.xyz, Scatter.art, Custom (browser fallback)
 */

const opensea    = require('./opensea');
const manifold   = require('./manifold');
const highlight  = require('./highlight');
const launchmynft = require('./launchmynft');
const foundation = require('./foundation');
const magiceden  = require('./magiceden');
const zora       = require('./zora');
const sound      = require('./sound');
const scatter    = require('./scatter');
const custom     = require('./custom');

// Ordered list of platforms — more specific domains first
const PLATFORMS = [
  opensea,
  manifold,
  highlight,
  launchmynft,
  foundation,
  magiceden,
  zora,
  sound,
  scatter,
  // custom is the fallback and is not in this list (scrape returns null)
];

/**
 * Detect which platform a URL belongs to.
 *
 * @param {string} url
 * @returns {string} Platform name, or 'Custom' if none matched.
 */
function detectPlatform(url) {
  if (!url) return 'Custom';
  const lower = url.toLowerCase();
  for (const p of PLATFORMS) {
    if (p.domains.some(d => lower.includes(d))) return p.name;
  }
  return custom.name;
}

/**
 * Run the platform-specific API scraper for the given URL.
 *
 * Returns normalized MintData (from utils/model.js) or null.
 * Returns null for 'Custom' platforms — browser scraper handles those.
 *
 * Individual scraper failures are caught here so the detection pipeline
 * never crashes due to a single platform's API being unavailable.
 *
 * @param {string} url
 * @returns {Promise<object|null>}
 */
async function scrapeByPlatform(url) {
  if (!url) return null;
  const lower = url.toLowerCase();
  for (const p of PLATFORMS) {
    if (p.domains.some(d => lower.includes(d))) {
      try {
        return await p.scrape(url);
      } catch {
        // Scraper threw unexpectedly — return null so pipeline falls through
        return null;
      }
    }
  }
  return null;
}

module.exports = {
  detectPlatform,
  scrapeByPlatform,
  PLATFORMS,
};
