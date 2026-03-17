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

const TIME_FORMATS = [
  'YYYY-MM-DDTHH:mm:ssZ',
  'YYYY-MM-DDTHH:mm:ss.SSSZ',
  'YYYY-MM-DD HH:mm:ss',
  'YYYY-MM-DD HH:mm',
  'MM/DD/YYYY HH:mm',
  'MMMM D, YYYY [at] h:mm A',
  'MMMM D [at] h:mm A',
  'MMM D, YYYY h:mm A',
];

function parseTime(input) {
  if (!input) return null;

  // Unix timestamp (number or numeric string)
  const num = Number(input);
  if (!isNaN(num) && num > 1e9) {
    return new Date(num * (num < 1e12 ? 1000 : 1));
  }

  // ISO 8601
  const iso = new Date(input);
  if (!isNaN(iso.getTime())) return iso;

  // Relative countdown: "in X days Y hours Z minutes"
  const relMatch = input.match(/in\s+(?:(\d+)\s+days?)?\s*(?:(\d+)\s+hours?)?\s*(?:(\d+)\s+min(?:utes?)?)?/i);
  if (relMatch) {
    const d = parseInt(relMatch[1] || 0);
    const h = parseInt(relMatch[2] || 0);
    const m = parseInt(relMatch[3] || 0);
    return new Date(Date.now() + (d * 86400 + h * 3600 + m * 60) * 1000);
  }

  // dayjs multi-format
  for (const fmt of TIME_FORMATS) {
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
