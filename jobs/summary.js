'use strict';

const db = require('../database/mints');
const { getSummaryChannels } = require('../database/channels');
const { formatDailySummary } = require('../utils/formatter');
const { parseTime, sortPhases } = require('../utils/parser');
const logger = require('../utils/logger');

let telegramBot = null;

function setBot(bot) {
  telegramBot = bot;
}

/**
 * Get all mints that have phases scheduled for today (UTC).
 */
function getTodaysMints() {
  const allMints = db.getAllMints();
  const now = new Date();
  const todayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const todayEnd = new Date(todayStart.getTime() + 86400000);

  return allMints.filter(mint => {
    if (!mint.phases || mint.phases.length === 0) return false;
    return mint.phases.some(phase => {
      const t = parseTime(phase.time);
      return t && t >= todayStart && t < todayEnd;
    });
  }).map(mint => ({
    ...mint,
    phases: sortPhases(mint.phases).filter(p => {
      const t = parseTime(p.time);
      return t && t >= todayStart && t < todayEnd;
    }),
  }));
}

/**
 * Send daily summary to all summary channels.
 */
async function sendDailySummary() {
  if (!telegramBot) return;

  const channels = getSummaryChannels();
  if (channels.length === 0) return;

  const todaysMints = getTodaysMints();
  const dateStr = new Date().toISOString().split('T')[0];

  const text = todaysMints.length > 0
    ? formatDailySummary(todaysMints, dateStr)
    : `📅 <b>Daily Mint Summary — ${dateStr}</b>\n\nNo mints scheduled for today.`;

  for (const ch of channels) {
    try {
      await telegramBot.telegram.sendMessage(ch.channel_id, text, { parse_mode: 'HTML' });
      logger.info(`Daily summary sent to channel ${ch.channel_id}`);
    } catch (err) {
      logger.warn(`Failed to send summary to ${ch.channel_id}: ${err.message}`);
    }
  }
}

module.exports = { setBot, sendDailySummary, getTodaysMints };
