'use strict';

const Database = require('better-sqlite3');
const path = require('path');
const fs = require('fs');
const logger = require('../utils/logger');

let db;

function getDb() {
  if (!db) throw new Error('Database not initialized. Call initDb() first.');
  return db;
}

function initDb() {
  const dbPath = process.env.DATABASE_PATH || '/data/alarm.db';
  const dir = path.dirname(dbPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });

  db = new Database(dbPath);
  db.pragma('journal_mode = WAL');
  db.pragma('foreign_keys = ON');

  runMigrations();
  logger.info(`Database initialized at ${dbPath}`);
  return db;
}

function runMigrations() {
  db.exec(`
    CREATE TABLE IF NOT EXISTS mints (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      name          TEXT NOT NULL,
      chain         TEXT NOT NULL DEFAULT 'Ethereum',
      mint_link     TEXT NOT NULL,
      phases        TEXT NOT NULL DEFAULT '[]',
      status        TEXT NOT NULL DEFAULT 'upcoming',
      paused        INTEGER NOT NULL DEFAULT 0,
      alert_channels TEXT NOT NULL DEFAULT '[]',
      summary_channels TEXT NOT NULL DEFAULT '[]',
      created_at    TEXT NOT NULL DEFAULT (datetime('now')),
      notes         TEXT,
      x_link        TEXT,
      os_link       TEXT,
      contract      TEXT,
      discord_link  TEXT,
      total_supply  INTEGER,
      minted        INTEGER DEFAULT 0,
      market_links  TEXT NOT NULL DEFAULT '{}',
      fast_mint_alerted INTEGER NOT NULL DEFAULT 0,
      platform      TEXT
    );

    CREATE TABLE IF NOT EXISTS sent_alerts (
      id         INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id    INTEGER NOT NULL,
      phase_name TEXT NOT NULL DEFAULT '',
      alert_type TEXT NOT NULL,
      sent_at    TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (mint_id) REFERENCES mints(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS channels (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      channel_id      TEXT NOT NULL UNIQUE,
      channel_name    TEXT NOT NULL DEFAULT '',
      receive_alerts  INTEGER NOT NULL DEFAULT 1,
      receive_summary INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS floor_history (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id     INTEGER NOT NULL,
      floor_price REAL NOT NULL,
      recorded_at TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (mint_id) REFERENCES mints(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS sweep_events (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      mint_id     INTEGER NOT NULL,
      bought_at   TEXT NOT NULL DEFAULT (datetime('now')),
      FOREIGN KEY (mint_id) REFERENCES mints(id) ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_sent_alerts_mint ON sent_alerts(mint_id);
    CREATE INDEX IF NOT EXISTS idx_floor_mint ON floor_history(mint_id);
    CREATE INDEX IF NOT EXISTS idx_sweep_mint ON sweep_events(mint_id);
  `);

  // Safe column additions for existing databases
  const addColumnIfMissing = (table, column, definition) => {
    try {
      db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
    } catch (_) { /* column already exists */ }
  };

  addColumnIfMissing('mints', 'fast_mint_alerted', 'INTEGER NOT NULL DEFAULT 0');
  addColumnIfMissing('mints', 'market_links', "TEXT NOT NULL DEFAULT '{}'");
  addColumnIfMissing('mints', 'minted', 'INTEGER DEFAULT 0');
  addColumnIfMissing('mints', 'total_supply', 'INTEGER');
  addColumnIfMissing('mints', 'contract', 'TEXT');
  addColumnIfMissing('mints', 'discord_link', 'TEXT');
  addColumnIfMissing('mints', 'platform', 'TEXT');
}

module.exports = { initDb, getDb };
