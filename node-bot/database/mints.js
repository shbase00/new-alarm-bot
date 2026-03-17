'use strict';

const { getDb } = require('./index');

// ── helpers ────────────────────────────────────────────────────────────────────

function parseJson(value, fallback) {
  if (!value) return fallback;
  try { return JSON.parse(value); } catch { return fallback; }
}

function hydrate(row) {
  if (!row) return null;
  return {
    ...row,
    phases: parseJson(row.phases, []),
    alert_channels: parseJson(row.alert_channels, []),
    summary_channels: parseJson(row.summary_channels, []),
    market_links: parseJson(row.market_links, {}),
    paused: Boolean(row.paused),
    fast_mint_alerted: Boolean(row.fast_mint_alerted),
  };
}

// ── reads ──────────────────────────────────────────────────────────────────────

function getAllMints() {
  return getDb().prepare('SELECT * FROM mints ORDER BY id DESC').all().map(hydrate);
}

function getMintById(id) {
  return hydrate(getDb().prepare('SELECT * FROM mints WHERE id = ?').get(id));
}

function getMintsByStatus(status) {
  return getDb().prepare('SELECT * FROM mints WHERE status = ? AND paused = 0').all(status).map(hydrate);
}

function getActiveMints() {
  return getDb()
    .prepare("SELECT * FROM mints WHERE status IN ('upcoming','live') AND paused = 0")
    .all()
    .map(hydrate);
}

// ── writes ─────────────────────────────────────────────────────────────────────

function createMint(data) {
  const stmt = getDb().prepare(`
    INSERT INTO mints
      (name, chain, mint_link, phases, status, alert_channels, summary_channels,
       notes, x_link, os_link, contract, discord_link, total_supply, minted, market_links)
    VALUES
      (@name, @chain, @mint_link, @phases, @status, @alert_channels, @summary_channels,
       @notes, @x_link, @os_link, @contract, @discord_link, @total_supply, @minted, @market_links)
  `);
  const result = stmt.run({
    name: data.name,
    chain: data.chain || 'Ethereum',
    mint_link: data.mint_link,
    phases: JSON.stringify(data.phases || []),
    status: data.status || 'upcoming',
    alert_channels: JSON.stringify(data.alert_channels || []),
    summary_channels: JSON.stringify(data.summary_channels || []),
    notes: data.notes || null,
    x_link: data.x_link || null,
    os_link: data.os_link || null,
    contract: data.contract || null,
    discord_link: data.discord_link || null,
    total_supply: data.total_supply || null,
    minted: data.minted || 0,
    market_links: JSON.stringify(data.market_links || {}),
  });
  return getMintById(result.lastInsertRowid);
}

function updateMint(id, updates) {
  const allowed = [
    'name','chain','mint_link','phases','status','paused','alert_channels',
    'summary_channels','notes','x_link','os_link','contract','discord_link',
    'total_supply','minted','market_links','fast_mint_alerted',
  ];
  const fields = Object.keys(updates).filter(k => allowed.includes(k));
  if (fields.length === 0) return getMintById(id);

  const serialized = {};
  for (const f of fields) {
    const v = updates[f];
    serialized[f] = (typeof v === 'object' && v !== null) ? JSON.stringify(v) : v;
  }

  const setClause = fields.map(f => `${f} = @${f}`).join(', ');
  getDb().prepare(`UPDATE mints SET ${setClause} WHERE id = @id`).run({ ...serialized, id });
  return getMintById(id);
}

function deleteMint(id) {
  getDb().prepare('DELETE FROM mints WHERE id = ?').run(id);
}

function updateMintedCount(id, minted) {
  getDb().prepare('UPDATE mints SET minted = ? WHERE id = ?').run(minted, id);
}

function updateStatus(id, status) {
  getDb().prepare('UPDATE mints SET status = ? WHERE id = ?').run(status, id);
}

// ── alerts dedup ───────────────────────────────────────────────────────────────

function hasAlertBeenSent(mintId, phaseName, alertType) {
  const row = getDb()
    .prepare('SELECT id FROM sent_alerts WHERE mint_id = ? AND phase_name = ? AND alert_type = ?')
    .get(mintId, phaseName, alertType);
  return Boolean(row);
}

function markAlertSent(mintId, phaseName, alertType) {
  getDb()
    .prepare('INSERT INTO sent_alerts (mint_id, phase_name, alert_type) VALUES (?, ?, ?)')
    .run(mintId, phaseName, alertType);
}

// ── floor history ──────────────────────────────────────────────────────────────

function addFloorEntry(mintId, floorPrice) {
  getDb()
    .prepare('INSERT INTO floor_history (mint_id, floor_price) VALUES (?, ?)')
    .run(mintId, floorPrice);
}

function getLatestFloor(mintId) {
  return getDb()
    .prepare('SELECT floor_price FROM floor_history WHERE mint_id = ? ORDER BY id DESC LIMIT 1')
    .get(mintId);
}

// ── sweep events ───────────────────────────────────────────────────────────────

function addSweepEvent(mintId) {
  getDb().prepare('INSERT INTO sweep_events (mint_id) VALUES (?)').run(mintId);
}

function getSweepEventCount(mintId, windowSeconds) {
  const since = new Date(Date.now() - windowSeconds * 1000).toISOString();
  const row = getDb()
    .prepare('SELECT COUNT(*) as cnt FROM sweep_events WHERE mint_id = ? AND bought_at > ?')
    .get(mintId, since);
  return row ? row.cnt : 0;
}

function cleanOldSweepEvents(olderThanSeconds = 300) {
  const before = new Date(Date.now() - olderThanSeconds * 1000).toISOString();
  getDb().prepare('DELETE FROM sweep_events WHERE bought_at < ?').run(before);
}

module.exports = {
  getAllMints, getMintById, getMintsByStatus, getActiveMints,
  createMint, updateMint, deleteMint, updateMintedCount, updateStatus,
  hasAlertBeenSent, markAlertSent,
  addFloorEntry, getLatestFloor,
  addSweepEvent, getSweepEventCount, cleanOldSweepEvents,
};
