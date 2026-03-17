'use strict';

const cron = require('node-cron');
const alerts = require('./alerts');
const summary = require('./summary');
const logger = require('../utils/logger');

const DAILY_SUMMARY_HOUR = parseInt(process.env.DAILY_SUMMARY_HOUR || '10', 10);
const DAILY_SUMMARY_MINUTE = parseInt(process.env.DAILY_SUMMARY_MINUTE || '0', 10);

let tasks = [];

function setBot(bot) {
  alerts.setBot(bot);
  summary.setBot(bot);
}

/**
 * Run a job safely, catching and logging errors without crashing.
 */
async function safeRun(name, fn) {
  try {
    await fn();
  } catch (err) {
    logger.error(`Job "${name}" failed: ${err.message}`, err);
  }
}

/**
 * Start all scheduled jobs.
 */
function startJobs() {
  logger.info('Starting scheduled jobs...');

  // Main alert loop: every 60 seconds
  tasks.push(cron.schedule('* * * * *', async () => {
    await safeRun('checkPreMintAlerts', alerts.checkPreMintAlerts);
    await safeRun('checkLiveTransitions', alerts.checkLiveTransitions);
  }));

  // Sold-out + fast mint checks: every 2 minutes
  tasks.push(cron.schedule('*/2 * * * *', async () => {
    await safeRun('checkSoldOutStatus', alerts.checkSoldOutStatus);
    await safeRun('checkFastMint', alerts.checkFastMint);
  }));

  // Floor pump + sweep checks: every 60 seconds
  tasks.push(cron.schedule('* * * * *', async () => {
    await safeRun('checkFloorPumps', alerts.checkFloorPumps);
    await safeRun('checkSweeps', alerts.checkSweeps);
  }));

  // Minted count refresh: every 3 minutes for live mints
  tasks.push(cron.schedule('*/3 * * * *', () =>
    safeRun('refreshMintedCounts', alerts.refreshMintedCounts)
  ));

  // Daily summary
  const summaryMinute = `${DAILY_SUMMARY_MINUTE} ${DAILY_SUMMARY_HOUR} * * *`;
  tasks.push(cron.schedule(summaryMinute, () =>
    safeRun('sendDailySummary', summary.sendDailySummary),
    { timezone: 'UTC' }
  ));

  logger.info(`Scheduled jobs started. Daily summary at ${DAILY_SUMMARY_HOUR}:${String(DAILY_SUMMARY_MINUTE).padStart(2,'0')} UTC`);
}

function stopJobs() {
  for (const task of tasks) {
    try { task.stop(); } catch {}
  }
  tasks = [];
  logger.info('All scheduled jobs stopped.');
}

module.exports = { setBot, startJobs, stopJobs };
