'use strict';

require('dotenv').config();

const http = require('http');
const logger = require('./utils/logger');
const { initDb } = require('./database');
const { createBot } = require('./bot');
const jobs = require('./jobs');
const { stopAllWatchers } = require('./blockchain');

// ── Optional: minimal HTTP API for Railway health + PC scraper bridge ──────────
const PORT = parseInt(process.env.PORT || process.env.API_PORT || '3000', 10);
const API_SECRET_KEY = process.env.API_SECRET_KEY || '';

function createApiServer(bot) {
  const db = require('./database/mints');

  const server = http.createServer(async (req, res) => {
    // Health check
    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ok', uptime: process.uptime() }));
      return;
    }

    // Receive mint from external scraper (PC bridge)
    if (req.method === 'POST' && req.url === '/api/mint') {
      // Auth check
      if (API_SECRET_KEY) {
        const key = req.headers['x-api-key'];
        if (key !== API_SECRET_KEY) {
          res.writeHead(401, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Unauthorized' }));
          return;
        }
      }

      let body = '';
      req.on('data', chunk => {
        body += chunk;
        if (body.length > 512 * 1024) { // 512 KB limit
          res.writeHead(413, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Payload too large' }));
          req.destroy();
        }
      });
      req.on('end', async () => {
        try {
          const data = JSON.parse(body);
          if (!data.name || !data.mint_link) {
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify({ error: 'name and mint_link required' }));
            return;
          }

          // Check if already tracked by URL
          const existing = db.getAllMints().find(m => m.mint_link === data.mint_link);
          let mint, action;

          if (existing) {
            mint = db.updateMint(existing.id, {
              phases: data.phases || existing.phases,
              total_supply: data.supply || existing.total_supply,
              minted: data.minted || existing.minted,
              contract: data.contract || existing.contract,
              x_link: data.twitter || existing.x_link,
              discord_link: data.discord || existing.discord_link,
            });
            action = 'updated';
          } else {
            mint = db.createMint({
              name: data.name,
              chain: data.chain || 'Ethereum',
              mint_link: data.mint_link,
              phases: data.phases || [],
              total_supply: data.supply || null,
              minted: data.minted || 0,
              contract: data.contract || null,
              x_link: data.twitter || null,
              discord_link: data.discord || null,
            });
            action = 'created';
          }

          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ status: 'ok', action, mint_id: mint.id, name: mint.name }));
          logger.info(`API: mint ${action} — ${mint.name} (#${mint.id})`);
        } catch (err) {
          logger.error(`API error: ${err.message}`);
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: err.message }));
        }
      });
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not Found' }));
  });

  return server;
}

// ── Bot launch with 409 retry ──────────────────────────────────────────────────

async function launchWithRetry(bot, maxRetries = 6) {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      await bot.launch();
      return;
    } catch (err) {
      const is409 = err.message && err.message.includes('409');
      if (is409 && attempt < maxRetries) {
        const delayMs = Math.min(Math.pow(2, attempt) * 2000, 30000); // 2s 4s 8s 16s 30s 30s
        logger.warn(`409 Conflict (attempt ${attempt + 1}/${maxRetries}) — waiting ${delayMs / 1000}s for previous instance to stop...`);
        await new Promise(r => setTimeout(r, delayMs));
      } else {
        throw err;
      }
    }
  }
}

// ── Graceful shutdown ──────────────────────────────────────────────────────────

function setupShutdown(bot, server) {
  const shutdown = async (signal) => {
    logger.info(`Received ${signal}, shutting down gracefully...`);
    jobs.stopJobs();
    stopAllWatchers();
    await bot.stop(signal);
    server.close();
    process.exit(0);
  };

  process.once('SIGINT', () => shutdown('SIGINT'));
  process.once('SIGTERM', () => shutdown('SIGTERM'));
}

// ── Main ───────────────────────────────────────────────────────────────────────

async function main() {
  logger.info('Starting NFT Mint Alarm Bot (Node.js)...');

  // 1. Initialize database
  initDb();

  // 2. Create Telegram bot
  const bot = createBot();

  // 3. Wire jobs to bot instance
  jobs.setBot(bot);

  // 4. Start scheduled jobs
  jobs.startJobs();

  // 5. Start API server
  const server = createApiServer(bot);
  server.listen(PORT, () => {
    logger.info(`API server listening on port ${PORT}`);
  });

  // 6. Setup shutdown handlers
  setupShutdown(bot, server);

  // 7. Launch Telegram bot (retry on 409 — previous instance may still be alive)
  await launchWithRetry(bot);
  logger.info('Bot launched and polling for updates.');
}

main().catch(err => {
  logger.error(`Fatal startup error: ${err.message}`, err);
  process.exit(1);
});
