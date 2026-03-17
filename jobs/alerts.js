'use strict';

const db = require('../database/mints');
const { getAlertChannels } = require('../database/channels');
const { fetchMintedCount } = require('../blockchain');
const { fetchFloorPrice, fetchRecentSales } = require('../scrapers/opensea');
const { extractOpenSeaSlug } = require('../utils/parser');
const {
  formatPreAlert, formatLiveAlert, formatSoldOutAlert,
  formatFloorPumpAlert, formatSweepAlert, formatFastMintAlert,
} = require('../utils/formatter');
const { parseTime } = require('../utils/parser');
const logger = require('../utils/logger');

const ALERT_MINUTES_BEFORE = parseInt(process.env.ALERT_MINUTES_BEFORE || '15', 10);
const FLOOR_PUMP_THRESHOLD = parseFloat(process.env.FLOOR_PUMP_THRESHOLD || '0.5');
const SWEEP_COUNT_THRESHOLD = parseInt(process.env.SWEEP_COUNT_THRESHOLD || '10', 10);
const SWEEP_WINDOW_SECONDS = parseInt(process.env.SWEEP_WINDOW_SECONDS || '60', 10);

let telegramBot = null; // set via setBot()

function setBot(bot) {
  telegramBot = bot;
}

async function sendToChannels(text, channels) {
  if (!telegramBot) return;
  for (const ch of channels) {
    try {
      await telegramBot.telegram.sendMessage(ch.channel_id, text, { parse_mode: 'HTML' });
    } catch (err) {
      logger.warn(`Failed to send to channel ${ch.channel_id}: ${err.message}`);
    }
  }
}

// ── Pre-mint alerts (15 min before) ───────────────────────────────────────────

async function checkPreMintAlerts() {
  const mints = db.getActiveMints();
  const channels = getAlertChannels();
  if (channels.length === 0) return;

  const now = Date.now();
  const windowMs = ALERT_MINUTES_BEFORE * 60 * 1000;

  for (const mint of mints) {
    if (!mint.phases || mint.phases.length === 0) continue;

    for (const phase of mint.phases) {
      const t = parseTime(phase.time);
      if (!t) continue;

      const diff = t.getTime() - now;
      const isInWindow = diff > 0 && diff <= windowMs + 60000; // +1min grace

      if (!isInWindow) continue;

      const alertKey = `pre_${ALERT_MINUTES_BEFORE}min`;
      if (db.hasAlertBeenSent(mint.id, phase.name, alertKey)) continue;

      // Refresh minted count before alerting
      try {
        const freshCount = await fetchMintedCount(mint);
        if (freshCount !== null) db.updateMintedCount(mint.id, freshCount);
        const freshMint = db.getMintById(mint.id);
        const text = formatPreAlert(freshMint, phase, ALERT_MINUTES_BEFORE);
        await sendToChannels(text, channels);
        db.markAlertSent(mint.id, phase.name, alertKey);
        logger.info(`Pre-alert sent for "${mint.name}" phase "${phase.name}"`);
      } catch (err) {
        logger.error(`Pre-alert failed for mint #${mint.id}: ${err.message}`);
      }
    }
  }
}

// ── Live transition (±2 min of phase start) ───────────────────────────────────

async function checkLiveTransitions() {
  const mints = db.getMintsByStatus('upcoming');
  const channels = getAlertChannels();
  const now = Date.now();
  const WINDOW = 2 * 60 * 1000;

  for (const mint of mints) {
    if (!mint.phases || mint.phases.length === 0) continue;

    for (const phase of mint.phases) {
      const t = parseTime(phase.time);
      if (!t) continue;

      const diff = now - t.getTime();
      if (diff < 0 || diff > WINDOW) continue;

      if (db.hasAlertBeenSent(mint.id, phase.name, 'live')) continue;

      // Transition to live
      db.updateStatus(mint.id, 'live');
      db.markAlertSent(mint.id, phase.name, 'live');

      const freshMint = db.getMintById(mint.id);
      const text = formatLiveAlert(freshMint, phase);
      await sendToChannels(text, channels);
      logger.info(`Live alert sent for "${mint.name}" phase "${phase.name}"`);
    }
  }
}

// ── Sold-out detection ─────────────────────────────────────────────────────────

async function checkSoldOutStatus() {
  const liveMints = db.getMintsByStatus('live');
  const channels = getAlertChannels();

  for (const mint of liveMints) {
    try {
      // Check via contract totalSupply
      const currentCount = await fetchMintedCount(mint);
      if (currentCount !== null) {
        db.updateMintedCount(mint.id, currentCount);
      }

      const freshMint = db.getMintById(mint.id);
      const isSoldOut = freshMint.total_supply &&
        freshMint.minted >= freshMint.total_supply;

      if (!isSoldOut) continue;
      if (db.hasAlertBeenSent(mint.id, '', 'sold_out')) continue;

      // Get floor price
      let floorPrice = null;
      const slug = extractOpenSeaSlug(mint.os_link || mint.mint_link);
      if (slug) floorPrice = await fetchFloorPrice(slug);

      db.updateStatus(mint.id, 'sold_out');
      db.markAlertSent(mint.id, '', 'sold_out');

      const text = formatSoldOutAlert(freshMint, floorPrice);
      await sendToChannels(text, channels);
      logger.info(`Sold-out alert sent for "${mint.name}"`);
    } catch (err) {
      logger.error(`Sold-out check failed for mint #${mint.id}: ${err.message}`);
    }
  }
}

// ── Fast mint (50%+ supply) ────────────────────────────────────────────────────

async function checkFastMint() {
  const liveMints = db.getMintsByStatus('live');
  const channels = getAlertChannels();

  for (const mint of liveMints) {
    if (!mint.total_supply || mint.fast_mint_alerted) continue;

    const pct = (mint.minted || 0) / mint.total_supply;
    if (pct < 0.5) continue;

    db.updateMint(mint.id, { fast_mint_alerted: 1 });
    const text = formatFastMintAlert(mint);
    await sendToChannels(text, channels);
    logger.info(`Fast mint alert sent for "${mint.name}" (${Math.round(pct * 100)}% minted)`);
  }
}

// ── Floor pump detection ───────────────────────────────────────────────────────

async function checkFloorPumps() {
  const liveMints = db.getMintsByStatus('live');
  const channels = getAlertChannels();

  for (const mint of liveMints) {
    try {
      const slug = extractOpenSeaSlug(mint.os_link || mint.mint_link);
      if (!slug) continue;

      const newFloor = await fetchFloorPrice(slug);
      if (!newFloor) continue;

      const lastEntry = db.getLatestFloor(mint.id);
      if (!lastEntry) {
        db.addFloorEntry(mint.id, newFloor);
        continue;
      }

      const oldFloor = lastEntry.floor_price;
      const change = (newFloor - oldFloor) / oldFloor;

      if (change < FLOOR_PUMP_THRESHOLD) {
        // Update history even without alert
        db.addFloorEntry(mint.id, newFloor);
        continue;
      }

      // Check dedup by price point
      const alertKey = `floor_pump_${newFloor.toFixed(4)}`;
      if (db.hasAlertBeenSent(mint.id, '', alertKey)) continue;

      db.addFloorEntry(mint.id, newFloor);
      db.markAlertSent(mint.id, '', alertKey);

      const text = formatFloorPumpAlert(mint, oldFloor, newFloor, change);
      await sendToChannels(text, channels);
      logger.info(`Floor pump alert for "${mint.name}": ${oldFloor} → ${newFloor} ETH`);
    } catch (err) {
      logger.debug(`Floor pump check failed for mint #${mint.id}: ${err.message}`);
    }
  }
}

// ── Sweep detection ────────────────────────────────────────────────────────────

async function checkSweeps() {
  const liveMints = db.getMintsByStatus('live');
  const channels = getAlertChannels();

  for (const mint of liveMints) {
    try {
      const slug = extractOpenSeaSlug(mint.os_link || mint.mint_link);
      if (!slug) continue;

      const sales = await fetchRecentSales(slug, 50);
      const now = Date.now();
      const windowMs = SWEEP_WINDOW_SECONDS * 1000;

      // Count sales directly from API response — no DB insert to avoid double-counting
      const count = sales.filter(sale => {
        const ts = sale.event_timestamp
          ? sale.event_timestamp * 1000
          : new Date(sale.created_date).getTime();
        return now - ts < windowMs;
      }).length;

      if (count < SWEEP_COUNT_THRESHOLD) continue;

      // Dedup: one alert per sweep window duration
      const alertKey = `sweep_${Math.floor(Date.now() / windowMs)}`;
      if (db.hasAlertBeenSent(mint.id, '', alertKey)) continue;

      db.markAlertSent(mint.id, '', alertKey);
      const text = formatSweepAlert(mint, count);
      await sendToChannels(text, channels);
      logger.info(`Sweep alert for "${mint.name}": ${count} NFTs in ${SWEEP_WINDOW_SECONDS}s`);
    } catch (err) {
      logger.debug(`Sweep check failed for mint #${mint.id}: ${err.message}`);
    }
  }
}

// ── Minted count refresh ───────────────────────────────────────────────────────

async function refreshMintedCounts() {
  const liveMints = db.getMintsByStatus('live');
  for (const mint of liveMints) {
    try {
      const count = await fetchMintedCount(mint);
      if (count !== null) db.updateMintedCount(mint.id, count);
    } catch (err) {
      logger.debug(`Minted count refresh failed for #${mint.id}: ${err.message}`);
    }
  }
}

module.exports = {
  setBot,
  checkPreMintAlerts,
  checkLiveTransitions,
  checkSoldOutStatus,
  checkFastMint,
  checkFloorPumps,
  checkSweeps,
  refreshMintedCounts,
};
