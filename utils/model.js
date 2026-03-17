'use strict';

/**
 * Unified data model for NFT mint information.
 *
 * All detection layers (platform APIs, scrapers, blockchain) must normalize
 * their output to these canonical shapes before entering the merge pipeline.
 * This prevents different parts of the system from breaking each other when
 * field names or value formats differ across sources.
 */

const { normalizePriceStr } = require('./parser');

// ── Canonical field names ───────────────────────────────────────────────────────

/**
 * Canonical Phase shape:
 * {
 *   name:      string       — label e.g. "Allowlist", "Public Mint"
 *   time:      string|null  — ISO 8601 start time (UTC)
 *   end_time:  string|null  — ISO 8601 end time (UTC)
 *   price:     string       — normalized price e.g. "0.05 ETH", "Free"
 *   limit:     string|number|null — max per wallet
 * }
 */

/**
 * Canonical MintData shape:
 * {
 *   name:         string|null
 *   chain:        string        — "Ethereum", "Base", "Solana", etc.
 *   mint_link:    string        — original mint URL
 *   contract:     string|null   — checksummed contract address
 *   total_supply: number|null
 *   minted:       number        — items minted so far
 *   phases:       Phase[]       — normalized, sorted phases
 *   platform:     string|null   — detected platform name
 *   x_link:       string|null
 *   discord_link: string|null
 *   os_link:      string|null
 *   market_links: object
 * }
 */

// ── Phase normalization ─────────────────────────────────────────────────────────

/**
 * Map any raw phase object (from any source) to the canonical Phase shape.
 * Handles field name variants seen across OpenSea, launchpads, and blockchain.
 *
 * @param {object} raw  — raw phase data from any scraper
 * @returns {object|null}
 */
function normalizePhase(raw) {
  if (!raw || typeof raw !== 'object') return null;

  const name = String(
    raw.name ?? raw.phase_name ?? raw.stageName ?? raw.stage_name ??
    raw.label ?? raw.type ?? 'Public Mint'
  ).trim() || 'Public Mint';

  // Start time — many possible field names across platforms/APIs
  const time =
    raw.time ?? raw.start_time ?? raw.startTime ?? raw.begins_at ??
    raw.startTimestamp ?? raw.opensAt ?? raw.mintStartTime ?? raw.scheduled_start_time ?? null;

  // End time
  const end_time =
    raw.end_time ?? raw.endTime ?? raw.end ?? raw.ends_at ??
    raw.endTimestamp ?? raw.closesAt ?? raw.mintEndTime ?? null;

  // Price — normalize all variants
  const rawPrice =
    raw.price ?? raw.mint_price ?? raw.mintPrice ?? raw.cost ??
    raw.pricePerToken ?? raw.unit_price ?? raw.salePrice ?? null;

  // Wallet limit
  const limit =
    raw.limit ?? raw.max_per_wallet ?? raw.walletLimit ?? raw.maxPerWallet ??
    raw.limit_per_wallet ?? raw.wallet_limit ?? raw.maxMintPerWallet ?? null;

  return {
    name,
    time: time != null ? String(time) : null,
    end_time: end_time != null ? String(end_time) : null,
    price: normalizePriceStr(rawPrice),
    limit: limit ?? null,
  };
}

// ── MintData normalization ──────────────────────────────────────────────────────

/**
 * Normalize raw scraper output to the canonical MintData shape.
 * Safe to call on any object from any detection layer.
 *
 * @param {object|null} raw     — raw data from a scraper/API/blockchain
 * @param {string}      mintUrl — original URL (fallback for mint_link)
 * @returns {object|null}
 */
function normalizeMintData(raw, mintUrl) {
  if (!raw) return null;

  const phases = (raw.phases || [])
    .map(normalizePhase)
    .filter(Boolean);

  // Total supply — coerce to number
  let total_supply = raw.total_supply ?? raw.supply ?? raw.maxSupply ?? raw.totalSupply ?? null;
  if (total_supply != null) total_supply = Number(total_supply) || null;

  // Minted count — coerce to number
  const minted = Number(raw.minted ?? raw.totalMinted ?? raw.minted_count ?? 0) || 0;

  return {
    name: raw.name || null,
    chain: raw.chain || raw.network || null,
    mint_link: raw.mint_link || mintUrl || null,
    contract: raw.contract || raw.contractAddress || null,
    total_supply,
    minted,
    phases,
    platform: raw.platform || null,
    x_link: raw.x_link || null,
    discord_link: raw.discord_link || null,
    os_link: raw.os_link || null,
    market_links: raw.market_links || {},
  };
}

// ── Deduplication ───────────────────────────────────────────────────────────────

/**
 * Deduplicate an array of phases by (name + time).
 * Keeps the first occurrence of each unique pair.
 */
function deduplicatePhases(phases) {
  const seen = new Set();
  return phases.filter(p => {
    const key = `${p.name}|${p.time}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

module.exports = { normalizePhase, normalizeMintData, deduplicatePhases };
