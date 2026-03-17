'use strict';

const { Telegraf } = require('telegraf');
const handlers = require('./handlers');
const logger = require('../utils/logger');

let bot;

function createBot() {
  const token = process.env.BOT_TOKEN;
  if (!token) throw new Error('BOT_TOKEN environment variable is required');

  bot = new Telegraf(token);

  // ── Commands ────────────────────────────────────────────────────────────────
  bot.start(handlers.handleStart);
  bot.command('dashboard', handlers.handleDashboard);
  bot.command('status', handlers.handleStatus);
  bot.command('help', handlers.handleHelp);

  // ── Message handler ─────────────────────────────────────────────────────────
  bot.on('text', handlers.handleText);

  // ── Callback queries (inline keyboard) ──────────────────────────────────────
  bot.on('callback_query', handlers.handleCallback);

  // ── Error handler ───────────────────────────────────────────────────────────
  bot.catch((err, ctx) => {
    logger.error(`Bot error for update ${ctx?.update?.update_id}: ${err.message}`, err);
  });

  return bot;
}

function getBot() {
  return bot;
}

module.exports = { createBot, getBot };
