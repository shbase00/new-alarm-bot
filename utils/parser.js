'use strict';

const dayjs = require('dayjs');
const customParseFormat = require('dayjs/plugin/customParseFormat');
const utc = require('dayjs/plugin/utc');
const timezone = require('dayjs/plugin/timezone');
const advancedFormat = require('dayjs/plugin/advancedFormat');

dayjs.extend(customParseFormat);
dayjs.extend(utc);
dayjs.extend(timezone);
dayjs.extend(advancedFormat);

// ── Chain detection ────────────────────────────────────────────────────────────

const PLATFORM_CHAIN_MAP = {
  'foundation.app': 'Ethereum',
  'manifold.xyz': 'Ethereum',
  'highlight.xyz': 'Ethereum',
  'magiceden.io': 'Solana',
  'sound.xyz': 'Ethereum',
  'mint.fun': 'Ethereum',
  'launchmynft.io': 'Ethereum',
  'ordzaar.com': 'Bitcoin',
  'zora.co': 'Zora',
  'opensea.io': 'Ethereum',
  'niftygateway.com': 'Ethereum',
  'superrare.com': 'Ethereum',
  'rarible.com': 'Ethereum',
  'async.art': 'Ethereum',
  'x2y2.io': 'Ethereum',
  'blur.io': 'Ethereum',
};

const CHAIN_KEYWORDS = {
  ethereum: 'Ethereum', eth: 'Ethereum',
  base: 'Base',
  arbitrum: 'Arbitrum', arb: 'Arbitrum',
  optimism: 'Optimism', op: 'Optimism',
  polygon: 'Polygon', matic: 'Polygon',
  blast: 'Blast',
  zora: 'Zora',
  linea: 'Linea',
  scroll: 'Scroll',
  abstract: 'Abstract',
  mantle: 'Mantle',
  taiko: 'Taiko',
  unichain: 'UniChain',
  opbnb: 'opBNB',
  bnb: 'BNB', bsc: 'BNB',
  solana: 'Solana', sol: 'Solana',
  bitcoin: 'Bitcoin', btc: 'Bitcoin',
  avalanche: 'Avalanche', avax: 'Avalanche',
  fantom: 'Fantom', ftm: 'Fantom',
  berachain: 'Berachain', bera: 'Berachain',
  apechain: 'ApeChain', ape: 'ApeChain',
  worldchain: 'WorldChain',
  celo: 'Celo',
  megaeth: 'MegaETH',
  starknet: 'Starknet',
  sonic: 'Sonic',
};

const CHAIN_EMOJIS = {
  Ethereum: '⬡', Base: '🔵', Arbitrum: '🔷', Optimism: '🔴',
  Polygon: '🟣', Blast: '💛', Zora: '🟢', Linea: '⬛',
  Solana: '☀️', Bitcoin: '₿', BNB: '🟡', Avalanche: '🔺',
  Fantom: '👻', Berachain: '🐻', ApeChain: '🐵', Scroll: '📜',
  Abstract: '🎨', Mantle: '🌀', MegaETH: '⚡', Starknet: '⭐',
  Sonic: '💨', UniChain: '🦄', WorldChain: '🌍', Celo: '🍃',
  opBNB: '🟡', Taiko: '🎵',
};

function detectChainFromUrl(url) {
  const lower = url.toLowerCase();
  for (const [domain, chain] of Object.entries(PLATFORM_CHAIN_MAP)) {
    if (lower.includes(domain)) return chain;
  }
  return null;
}

function detectChainFromText(text) {
  const lower = text.toLowerCase();
  for (const [keyword, chain] of Object.entries(CHAIN_KEYWORDS)) {
    if (lower.includes(keyword)) return chain;
  }
  return null;
}

function getChainEmoji(chain) {
  return CHAIN_EMOJIS[chain] || '🔗';
}

// ── URL helpers ────────────────────────────────────────────────────────────────

function extractOpenSeaSlug(url) {
  const match = url.match(/opensea\.io\/(?:collection|drops)\/([^/?#]+)/i);
  return match ? match[1] : null;
}

function extractPlatform(url) {
  try {
    const { hostname } = new URL(url);
    return hostname.replace(/^www\./, '');
  } catch {
    return null;
  }
}

function isEVMChain(chain) {
  const nonEVM = new Set(['Solana', 'Bitcoin', 'Starknet']);
  return !nonEVM.has(chain);
}

// ── Time parsing ───────────────────────────────────────────────────────────────

// Strict dayjs formats (must match exactly)
const TIME_FORMATS_STRICT = [
  'YYYY-MM-DDTHH:mm:ssZ',
  'YYYY-MM-DDTHH:mm:ss.SSSZ',
  'YYYY-MM-DD HH:mm:ss',
  'YYYY-MM-DD HH:mm',
  'MM/DD/YYYY HH:mm',
  'MMMM D, YYYY [at] h:mm A',
  'MMM D, YYYY h:mm A',
];

// Regex-based fallbacks for formats dayjs strict mode rejects
// Covers OpenSea's "March 18 at 3:00 PM UTC" and "March 18, 2026 at 3:00 PM UTC"
const MONTHS = {
  january:1, february:2, march:3, april:4, may:5, june:6,
  july:7, august:8, september:9, october:10, november:11, december:12,
  jan:1, feb:2, mar:3, apr:4, jun:6, jul:7, aug:8, sep:9, oct:10, nov:11, dec:12,
};

function parseOpenSeaDateStr(s) {
  // "March 18 at 3:00 PM UTC" or "March 18, 2026 at 3:00 PM UTC"
  const m = s.match(
    /^(\w+)\s+(\d{1,2})(?:,\s*(\d{4}))?\s+at\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?\s*(UTC|GMT)?/i
  );
  if (!m) return null;

  const month = MONTHS[m[1].toLowerCase()];
  if (!month) return null;

  const day  = parseInt(m[2]);
  const year = m[3] ? parseInt(m[3]) : new Date().getUTCFullYear();
  let   hour = parseInt(m[4]);
  const min  = parseInt(m[5]);
  const sec  = m[6] ? parseInt(m[6]) : 0;
  const ampm = (m[7] || '').toUpperCase();

  if (ampm === 'PM' && hour < 12) hour += 12;
  if (ampm === 'AM' && hour === 12) hour = 0;

  const d = new Date(Date.UTC(year, month - 1, day, hour, min, sec));

  // If no year was given and the date is already in the past, bump to next year
  if (!m[3] && d < new Date()) {
    d.setUTCFullYear(d.getUTCFullYear() + 1);
  }

  return isNaN(d) ? null : d;
}

function parseTime(input) {
  if (!input) return null;

  // Unix timestamp
  const num = Number(input);
  if (!isNaN(num) && num > 1e9) {
    return new Date(num < 1e12 ? num * 1000 : num);
  }

  // Native Date (handles ISO 8601 and RFC 2822)
  const iso = new Date(input);
  if (!isNaN(iso.getTime())) return iso;

  // Relative: "in 1 day 3 hours 20 minutes"
  const relMatch = input.match(/in\s+(?:(\d+)\s+days?)?\s*(?:(\d+)\s+hours?)?\s*(?:(\d+)\s+min(?:utes?)?)?/i);
  if (relMatch && (relMatch[1] || relMatch[2] || relMatch[3])) {
    const d = parseInt(relMatch[1] || 0);
    const h = parseInt(relMatch[2] || 0);
    const m = parseInt(relMatch[3] || 0);
    return new Date(Date.now() + (d * 86400 + h * 3600 + m * 60) * 1000);
  }

  // OpenSea human format: "March 18 at 3:00 PM UTC"
  const osDate = parseOpenSeaDateStr(input.trim());
  if (osDate) return osDate;

  // dayjs strict formats
  for (const fmt of TIME_FORMATS_STRICT) {
    const parsed = dayjs.utc(input, fmt, true);
    if (parsed.isValid()) return parsed.toDate();
  }

  return null;
}

function formatTimeUTC(date) {
  if (!date) return 'TBD';
  return dayjs(date).utc().format('MMM D, HH:mm [UTC]');
}

function timeUntil(date) {
  if (!date) return '';
  const diff = date.getTime() - Date.now();
  if (diff <= 0) return 'now';

  const days = Math.floor(diff / 86400000);
  const hours = Math.floor((diff % 86400000) / 3600000);
  const mins = Math.floor((diff % 3600000) / 60000);

  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

// ── Price normalization ────────────────────────────────────────────────────────

function normalizePriceStr(price) {
  if (!price) return 'Free';
  const s = String(price).trim();
  if (s === '0' || s === '0.0' || s.toLowerCase() === 'free') return 'Free';
  if (/^\d/.test(s) && !/ /.test(s)) return `${s} ETH`;
  return s;
}

// ── Phase helpers ──────────────────────────────────────────────────────────────

function sortPhases(phases) {
  return [...phases].sort((a, b) => {
    const ta = parseTime(a.time);
    const tb = parseTime(b.time);
    if (!ta && !tb) return 0;
    if (!ta) return 1;
    if (!tb) return -1;
    return ta - tb;
  });
}

function getNextPhase(phases) {
  const now = Date.now();
  const sorted = sortPhases(phases);
  for (const p of sorted) {
    const t = parseTime(p.time);
    if (t && t.getTime() > now) return p;
  }
  return null;
}

function getCurrentPhase(phases) {
  const now = Date.now();
  const sorted = sortPhases(phases);
  let current = null;
  for (const p of sorted) {
    const t = parseTime(p.time);
    if (t && t.getTime() <= now) current = p;
    else break;
  }
  return current;
}

module.exports = {
  detectChainFromUrl,
  detectChainFromText,
  getChainEmoji,
  extractOpenSeaSlug,
  extractPlatform,
  isEVMChain,
  parseTime,
  formatTimeUTC,
  timeUntil,
  normalizePriceStr,
  sortPhases,
  getNextPhase,
  getCurrentPhase,
  CHAIN_EMOJIS,
};
