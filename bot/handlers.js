'use strict';

const db = require('../database/mints');
const channelDb = require('../database/channels');
const { detectMint } = require('../scrapers');
const { buildMarketLinks } = require('../scrapers/opensea');
const { fetchMintedCount } = require('../blockchain');
const {
  formatMintCard, formatMintsList, formatDailySummary, escHtml,
} = require('../utils/formatter');
const { sortPhases, parseTime, formatTimeUTC } = require('../utils/parser');
const {
  mainKeyboard, dashboardInline, mintActionsKeyboard, editMintKeyboard,
  confirmDeleteKeyboard, channelActionsKeyboard, cancelKeyboard, skipCancelKeyboard,
} = require('./keyboards');
const { getTodaysMints } = require('../jobs/summary');
const logger = require('../utils/logger');

// ── Conversation state store (in-memory, keyed by userId) ─────────────────────
const sessions = new Map();

function getSession(userId) {
  if (!sessions.has(userId)) sessions.set(userId, {});
  return sessions.get(userId);
}

function clearSession(userId) {
  sessions.delete(userId);
}

// ── Admin check ────────────────────────────────────────────────────────────────

function isAdmin(userId) {
  const adminIds = (process.env.ADMIN_IDS || '')
    .split(',')
    .map(s => parseInt(s.trim(), 10))
    .filter(Boolean);
  return adminIds.includes(userId);
}

// ── Dashboard ──────────────────────────────────────────────────────────────────

async function handleStart(ctx) {
  if (!isAdmin(ctx.from.id)) {
    return ctx.reply('⛔ Unauthorized.');
  }
  clearSession(ctx.from.id);

  const count = db.getAllMints().length;
  const live = db.getMintsByStatus('live').length;
  const channels = channelDb.getAllChannels().length;

  const text = `🎛 <b>NFT Mint Alarm Bot</b>\n\n` +
    `📦 Tracked mints: <b>${count}</b>\n` +
    `🟢 Live now: <b>${live}</b>\n` +
    `📢 Channels: <b>${channels}</b>\n\n` +
    `Use the buttons below to manage mints and channels.`;

  await ctx.reply(text, { parse_mode: 'HTML', ...mainKeyboard });
}

async function handleDashboard(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  clearSession(ctx.from.id);
  await ctx.reply('🎛 <b>Dashboard</b>', { parse_mode: 'HTML', ...dashboardInline });
}

// ── Mint listing ───────────────────────────────────────────────────────────────

async function handleListMints(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const mints = db.getAllMints();
  const text = formatMintsList(mints);

  if (mints.length === 0) {
    await safeReply(ctx, text, { parse_mode: 'HTML' });
    return;
  }

  // Build inline keyboard with one button per mint
  const { Markup } = require('telegraf');
  const buttons = mints.map(m => [
    Markup.button.callback(
      `${m.status === 'live' ? '🟢' : m.status === 'sold_out' ? '🔴' : '🕐'} ${m.name} #${m.id}`,
      `view_mint_${m.id}`
    ),
  ]);
  const kb = Markup.inlineKeyboard(buttons);
  await safeReply(ctx, text, { parse_mode: 'HTML', ...kb });
}

async function handleViewMint(ctx, mintId) {
  if (!isAdmin(ctx.from.id)) return;
  const mint = db.getMintById(mintId);
  if (!mint) {
    await safeReply(ctx, '❌ Mint not found.');
    return;
  }

  const text = formatMintCard(mint);
  await safeReply(ctx, text, { parse_mode: 'HTML', ...mintActionsKeyboard(mintId) });
}

// ── Today's mints ──────────────────────────────────────────────────────────────

async function handleTodaysMints(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const mints = getTodaysMints();
  const dateStr = new Date().toISOString().split('T')[0];

  const text = mints.length > 0
    ? formatDailySummary(mints, dateStr)
    : `📅 <b>Today's Mints — ${dateStr}</b>\n\nNo mints scheduled for today.`;

  await safeReply(ctx, text, { parse_mode: 'HTML' });
}

// ── Add Mint flow ──────────────────────────────────────────────────────────────

async function handleAddMint(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  session.state = 'waiting_mint_url';

  await safeReply(ctx,
    '➕ <b>Add Mint</b>\n\nSend the mint URL (OpenSea, Foundation, Highlight, MagicEden, Zora, etc.):',
    { parse_mode: 'HTML', ...cancelKeyboard() }
  );
}

async function handleMintUrlInput(ctx, url) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);

  if (session.state !== 'waiting_mint_url') return;

  // Validate URL
  try { new URL(url); } catch {
    await safeReply(ctx, '❌ Invalid URL. Please send a valid mint link.');
    return;
  }

  session.state = 'detecting';
  const loadingMsg = await ctx.reply('🔍 Detecting mint info... This may take up to 30 seconds.', { parse_mode: 'HTML' });

  try {
    const detected = await detectMint(url);
    session.detected = detected;

    await ctx.telegram.deleteMessage(ctx.chat.id, loadingMsg.message_id).catch(() => {});

    if (detected.needs_manual) {
      // Fall through to manual phase entry
      session.state = 'waiting_manual_name';
      session.detected = { ...detected, mint_link: url, phases: [] };
      await safeReply(ctx,
        `⚠️ Auto-detection incomplete.\n\n🔗 URL: ${escHtml(url)}\n\nEnter the <b>collection name</b>:`,
        { parse_mode: 'HTML', ...cancelKeyboard() }
      );
    } else {
      // Show preview and ask for confirmation
      session.state = 'confirming_mint';
      const preview = buildDetectionPreview(detected);
      const { Markup } = require('telegraf');
      const kb = Markup.inlineKeyboard([
        [
          Markup.button.callback('✅ Save', 'confirm_add_mint'),
          Markup.button.callback('✏️ Edit', 'edit_before_add'),
          Markup.button.callback('❌ Cancel', 'cancel_action'),
        ],
      ]);
      await safeReply(ctx, preview, { parse_mode: 'HTML', ...kb });
    }
  } catch (err) {
    logger.error(`Detection failed: ${err.message}`);
    await ctx.telegram.deleteMessage(ctx.chat.id, loadingMsg.message_id).catch(() => {});
    session.state = 'waiting_manual_name';
    session.detected = { mint_link: url, phases: [], chain: 'Ethereum' };
    await safeReply(ctx,
      `⚠️ Detection failed. Enter the <b>collection name</b> manually:`,
      { parse_mode: 'HTML', ...cancelKeyboard() }
    );
  }
}

function buildDetectionPreview(data) {
  const lines = ['🔍 <b>Detection Result</b>\n'];
  lines.push(`📝 Name: <b>${escHtml(data.name || 'Unknown')}</b>`);
  lines.push(`⛓ Chain: ${escHtml(data.chain || 'Ethereum')}`);
  if (data.platform) lines.push(`🏷 Platform: ${escHtml(data.platform)}`);
  if (data.contract) lines.push(`📋 Contract: <code>${escHtml(data.contract)}</code>`);
  if (data.total_supply) lines.push(`📦 Supply: ${data.total_supply.toLocaleString()}`);

  if (data.phases && data.phases.length > 0) {
    lines.push(`\n📅 Phases (${data.phases.length}):`);
    for (const p of data.phases) {
      const t = parseTime(p.time);
      lines.push(`  • ${escHtml(p.name)}: ${t ? formatTimeUTC(t) : 'TBD'} @ ${escHtml(p.price || 'TBD')}`);
    }
  } else {
    lines.push(`\n⚠️ No phases detected — will need manual entry after saving`);
  }

  lines.push(`\n✅ Save this mint?`);
  return lines.join('\n');
}

async function handleConfirmAddMint(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  const data = session.detected;
  if (!data) return;

  try {
    const mint = db.createMint(data);
    clearSession(ctx.from.id);
    await safeReply(ctx,
      `✅ Mint <b>${escHtml(mint.name)}</b> added! (ID: #${mint.id})`,
      { parse_mode: 'HTML', ...mintActionsKeyboard(mint.id) }
    );
  } catch (err) {
    logger.error(`Create mint failed: ${err.message}`);
    await safeReply(ctx, `❌ Failed to save: ${err.message}`);
  }
}

// Manual entry steps
async function handleManualNameInput(ctx, name) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  if (session.state !== 'waiting_manual_name') return;

  session.detected.name = name;
  session.state = 'waiting_manual_phase_time';
  await safeReply(ctx,
    `📅 Enter the <b>first phase start time</b> (UTC):\nExamples:\n• <code>2026-03-20 15:00</code>\n• <code>March 20 at 3:00 PM UTC</code>`,
    { parse_mode: 'HTML', ...cancelKeyboard() }
  );
}

async function handleManualPhaseTime(ctx, timeStr) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  if (session.state !== 'waiting_manual_phase_time') return;

  const t = parseTime(timeStr);
  if (!t) {
    await safeReply(ctx, '❌ Could not parse time. Try: <code>2026-03-20 15:00</code>', { parse_mode: 'HTML' });
    return;
  }

  session.pendingPhase = { name: 'Public Mint', time: t.toISOString() };
  session.state = 'waiting_manual_phase_price';
  await safeReply(ctx,
    `💰 Enter the <b>mint price</b> (e.g. <code>0.08 ETH</code>, <code>Free</code>):`,
    { parse_mode: 'HTML', ...skipCancelKeyboard() }
  );
}

async function handleManualPhasePrice(ctx, priceStr) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  if (session.state !== 'waiting_manual_phase_price') return;

  session.pendingPhase.price = priceStr || 'TBD';
  session.detected.phases.push({ ...session.pendingPhase });
  delete session.pendingPhase;

  // Save mint
  try {
    const mint = db.createMint(session.detected);
    clearSession(ctx.from.id);
    await safeReply(ctx,
      `✅ Mint <b>${escHtml(mint.name)}</b> saved! (ID: #${mint.id})`,
      { parse_mode: 'HTML', ...mintActionsKeyboard(mint.id) }
    );
  } catch (err) {
    await safeReply(ctx, `❌ Failed: ${err.message}`);
  }
}

// ── Edit Mint ──────────────────────────────────────────────────────────────────

async function handleEditMint(ctx, mintId) {
  if (!isAdmin(ctx.from.id)) return;
  const mint = db.getMintById(mintId);
  if (!mint) { await safeReply(ctx, '❌ Mint not found.'); return; }
  await safeReply(ctx, `✏️ <b>Edit: ${escHtml(mint.name)}</b>\n\nChoose field to edit:`,
    { parse_mode: 'HTML', ...editMintKeyboard(mintId) });
}

async function handleEditField(ctx, mintId, field) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  session.state = `editing_${field}`;
  session.editingMintId = mintId;

  const fieldLabels = {
    name: 'Name', chain: 'Chain', mint_link: 'Mint URL', contract: 'Contract address',
    x_link: 'Twitter URL', discord_link: 'Discord URL', total_supply: 'Total supply',
  };
  const label = fieldLabels[field] || field;

  await safeReply(ctx, `Enter new <b>${label}</b>:`, { parse_mode: 'HTML', ...cancelKeyboard() });
}

async function handleEditFieldInput(ctx, value) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);

  const match = session.state?.match(/^editing_(.+)$/);
  if (!match) return;

  const field = match[1];
  const mintId = session.editingMintId;

  try {
    const updates = {};
    updates[field] = field === 'total_supply' ? parseInt(value, 10) : value;
    const updated = db.updateMint(mintId, updates);
    clearSession(ctx.from.id);
    await safeReply(ctx,
      `✅ <b>${escHtml(updated.name)}</b> updated!`,
      { parse_mode: 'HTML', ...mintActionsKeyboard(mintId) }
    );
  } catch (err) {
    await safeReply(ctx, `❌ Update failed: ${err.message}`);
  }
}

// ── Delete Mint ────────────────────────────────────────────────────────────────

async function handleDeleteMint(ctx, mintId) {
  if (!isAdmin(ctx.from.id)) return;
  const mint = db.getMintById(mintId);
  if (!mint) { await safeReply(ctx, '❌ Not found.'); return; }
  await safeReply(ctx,
    `🗑 Delete <b>${escHtml(mint.name)}</b>?\nThis cannot be undone.`,
    { parse_mode: 'HTML', ...confirmDeleteKeyboard(mintId) }
  );
}

async function handleConfirmDelete(ctx, mintId) {
  if (!isAdmin(ctx.from.id)) return;
  const mint = db.getMintById(mintId);
  if (!mint) { await safeReply(ctx, '❌ Not found.'); return; }
  db.deleteMint(mintId);
  await safeReply(ctx, `✅ <b>${escHtml(mint.name)}</b> deleted.`, { parse_mode: 'HTML' });
}

// ── Toggle Pause ───────────────────────────────────────────────────────────────

async function handleTogglePause(ctx, mintId) {
  if (!isAdmin(ctx.from.id)) return;
  const mint = db.getMintById(mintId);
  if (!mint) return;
  const updated = db.updateMint(mintId, { paused: mint.paused ? 0 : 1 });
  const state = updated.paused ? 'paused ⏸' : 'resumed ▶️';
  await safeReply(ctx, `${escHtml(updated.name)} is now ${state}.`, { parse_mode: 'HTML' });
}

// ── Refresh Mint ───────────────────────────────────────────────────────────────

async function handleRefreshMint(ctx, mintId) {
  if (!isAdmin(ctx.from.id)) return;
  const mint = db.getMintById(mintId);
  if (!mint) return;

  let msg = null;
  try { msg = await ctx.reply('🔄 Refreshing...'); } catch {}
  try {
    const count = await fetchMintedCount(mint);
    if (count !== null) db.updateMintedCount(mintId, count);
    const fresh = db.getMintById(mintId);
    if (msg) await ctx.telegram.deleteMessage(ctx.chat.id, msg.message_id).catch(() => {});
    const text = formatMintCard(fresh);
    await safeReply(ctx, text, { parse_mode: 'HTML', ...mintActionsKeyboard(mintId) });
  } catch (err) {
    if (msg) await ctx.telegram.deleteMessage(ctx.chat.id, msg.message_id).catch(() => {});
    await safeReply(ctx, `❌ Refresh failed: ${err.message}`);
  }
}

// ── Channels ───────────────────────────────────────────────────────────────────

async function handleManageChannels(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const channels = channelDb.getAllChannels();

  if (channels.length === 0) {
    const session = getSession(ctx.from.id);
    session.state = 'waiting_channel_id';
    await safeReply(ctx,
      '📢 <b>Channels</b>\n\nNo channels yet.\n\nSend a <b>channel ID</b> (e.g. <code>-1001234567890</code>) to add one:',
      { parse_mode: 'HTML', ...cancelKeyboard() }
    );
    return;
  }

  const { Markup } = require('telegraf');
  const lines = ['📢 <b>Channels</b>\n'];
  const buttons = channels.map(ch => {
    const alertIcon = ch.receive_alerts ? '🔔' : '🔕';
    const summaryIcon = ch.receive_summary ? '📊' : '➖';
    lines.push(`${alertIcon}${summaryIcon} <b>${escHtml(ch.channel_name || ch.channel_id)}</b>`);
    return [Markup.button.callback(
      `${alertIcon} ${ch.channel_name || ch.channel_id}`,
      `channel_settings_${ch.channel_id}`
    )];
  });
  buttons.push([Markup.button.callback('➕ Add Channel', 'add_channel')]);

  await safeReply(ctx, lines.join('\n'), { parse_mode: 'HTML', ...Markup.inlineKeyboard(buttons) });
}

async function handleAddChannel(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  session.state = 'waiting_channel_id';
  await safeReply(ctx,
    '📢 Send the <b>channel ID</b> (e.g. <code>-1001234567890</code>).\n\nTo get your channel ID, forward a message from it to @userinfobot.',
    { parse_mode: 'HTML', ...cancelKeyboard() }
  );
}

async function handleChannelIdInput(ctx, channelId) {
  if (!isAdmin(ctx.from.id)) return;
  const session = getSession(ctx.from.id);
  if (session.state !== 'waiting_channel_id') return;

  const idStr = channelId.trim();
  if (!idStr.match(/^-?\d+$/)) {
    await safeReply(ctx, '❌ Invalid channel ID format. It should be a number like <code>-1001234567890</code>.', { parse_mode: 'HTML' });
    return;
  }

  // Try to get channel name from Telegram
  let channelName = idStr;
  try {
    const chat = await ctx.telegram.getChat(idStr);
    channelName = chat.title || chat.username || idStr;
  } catch {}

  channelDb.upsertChannel(idStr, channelName, true, true);
  clearSession(ctx.from.id);
  await safeReply(ctx,
    `✅ Channel <b>${escHtml(channelName)}</b> added!`,
    { parse_mode: 'HTML' }
  );
}

async function handleChannelSettings(ctx, channelId) {
  if (!isAdmin(ctx.from.id)) return;
  const ch = channelDb.getChannelById(channelId);
  if (!ch) { await safeReply(ctx, '❌ Channel not found.'); return; }

  const text = `📢 <b>${escHtml(ch.channel_name || ch.channel_id)}</b>\n\n` +
    `🔔 Alerts: ${ch.receive_alerts ? 'On' : 'Off'}\n` +
    `📊 Summary: ${ch.receive_summary ? 'On' : 'Off'}`;

  await safeReply(ctx, text, { parse_mode: 'HTML', ...channelActionsKeyboard(channelId) });
}

async function handleToggleAlerts(ctx, channelId) {
  if (!isAdmin(ctx.from.id)) return;
  const ch = channelDb.getChannelById(channelId);
  if (!ch) return;
  channelDb.updateChannel(channelId, { receive_alerts: !ch.receive_alerts });
  await handleChannelSettings(ctx, channelId);
}

async function handleToggleSummary(ctx, channelId) {
  if (!isAdmin(ctx.from.id)) return;
  const ch = channelDb.getChannelById(channelId);
  if (!ch) return;
  channelDb.updateChannel(channelId, { receive_summary: !ch.receive_summary });
  await handleChannelSettings(ctx, channelId);
}

async function handleRemoveChannel(ctx, channelId) {
  if (!isAdmin(ctx.from.id)) return;
  const ch = channelDb.getChannelById(channelId);
  if (!ch) return;
  channelDb.deleteChannel(channelId);
  await safeReply(ctx, `✅ Channel <b>${escHtml(ch.channel_name || channelId)}</b> removed.`, { parse_mode: 'HTML' });
}

// ── Status ─────────────────────────────────────────────────────────────────────

async function handleStatus(ctx) {
  if (!isAdmin(ctx.from.id)) return;
  const allMints = db.getAllMints();
  const live = allMints.filter(m => m.status === 'live').length;
  const upcoming = allMints.filter(m => m.status === 'upcoming').length;
  const soldOut = allMints.filter(m => m.status === 'sold_out').length;
  const channels = channelDb.getAllChannels();
  const uptime = Math.floor(process.uptime());
  const uptimeStr = `${Math.floor(uptime/3600)}h ${Math.floor((uptime%3600)/60)}m`;

  const text = `📊 <b>Bot Status</b>\n\n` +
    `🟢 Live: <b>${live}</b>\n` +
    `🕐 Upcoming: <b>${upcoming}</b>\n` +
    `🔴 Sold Out: <b>${soldOut}</b>\n` +
    `📦 Total Mints: <b>${allMints.length}</b>\n\n` +
    `📢 Channels: <b>${channels.length}</b>\n` +
    `  Alert: ${channels.filter(c => c.receive_alerts).length}\n` +
    `  Summary: ${channels.filter(c => c.receive_summary).length}\n\n` +
    `⏱ Uptime: ${uptimeStr}\n` +
    `💾 Memory: ${Math.round(process.memoryUsage().heapUsed / 1024 / 1024)}MB`;

  await safeReply(ctx, text, { parse_mode: 'HTML' });
}

async function handleHelp(ctx) {
  const text = `ℹ️ <b>NFT Mint Alarm Bot</b>\n\n` +
    `<b>Commands:</b>\n` +
    `/start — Open dashboard\n` +
    `/status — Bot status\n` +
    `/help — This message\n\n` +
    `<b>Features:</b>\n` +
    `• Multi-layer mint detection\n` +
    `• Pre-mint alerts (15 min before)\n` +
    `• Live mint alerts\n` +
    `• Sold-out detection\n` +
    `• Floor pump alerts\n` +
    `• Sweep detection\n` +
    `• Daily mint summaries\n\n` +
    `<b>Supported Platforms:</b>\n` +
    `OpenSea, Foundation, Highlight, Manifold, MagicEden, Zora, Sound.xyz, LaunchMyNFT\n\n` +
    `<b>Supported Chains:</b>\n` +
    `Ethereum, Base, Arbitrum, Optimism, Polygon, Blast, Zora, Linea, Scroll, Abstract, Solana, and 15+ more`;

  await safeReply(ctx, text, { parse_mode: 'HTML' });
}

// ── Text router ────────────────────────────────────────────────────────────────

/**
 * Route incoming text messages based on session state or button presses.
 */
async function handleText(ctx) {
  if (!isAdmin(ctx.from?.id)) return;
  const text = ctx.message?.text || '';
  const session = getSession(ctx.from.id);

  // Reply keyboard buttons
  if (text === '➕ Add Mint') return handleAddMint(ctx);
  if (text === '📋 All Mints') return handleListMints(ctx);
  if (text === '📅 Today\'s Mints') return handleTodaysMints(ctx);
  if (text === '📢 Channels') return handleManageChannels(ctx);
  if (text === '🎛 Dashboard') return handleDashboard(ctx);
  if (text === 'ℹ️ Help') return handleHelp(ctx);

  // Session-based handlers
  if (session.state === 'waiting_mint_url') return handleMintUrlInput(ctx, text);
  if (session.state === 'waiting_manual_name') return handleManualNameInput(ctx, text);
  if (session.state === 'waiting_manual_phase_time') return handleManualPhaseTime(ctx, text);
  if (session.state === 'waiting_manual_phase_price') return handleManualPhasePrice(ctx, text);
  if (session.state === 'waiting_channel_id') return handleChannelIdInput(ctx, text);
  if (session.state?.startsWith('editing_')) return handleEditFieldInput(ctx, text);
}

// ── Callback query router ──────────────────────────────────────────────────────

async function handleCallback(ctx) {
  if (!isAdmin(ctx.from?.id)) {
    return ctx.answerCbQuery('⛔ Unauthorized');
  }

  await ctx.answerCbQuery().catch(() => {});
  const data = ctx.callbackQuery?.data || '';

  // Dashboard
  if (data === 'add_mint') return handleAddMint(ctx);
  if (data === 'list_mints') return handleListMints(ctx);
  if (data === 'today_mints') return handleTodaysMints(ctx);
  if (data === 'manage_channels') return handleManageChannels(ctx);
  if (data === 'bot_status') return handleStatus(ctx);
  if (data === 'add_channel') return handleAddChannel(ctx);
  if (data === 'cancel_action') {
    clearSession(ctx.from.id);
    return ctx.editMessageText('❌ Cancelled.').catch(() => ctx.reply('❌ Cancelled.'));
  }
  if (data === 'skip_step') {
    const session = getSession(ctx.from.id);
    if (session.state === 'waiting_manual_phase_price') {
      return handleManualPhasePrice(ctx, 'TBD');
    }
  }
  if (data === 'confirm_add_mint') return handleConfirmAddMint(ctx);
  if (data === 'edit_before_add') {
    // Drop back to manual name entry so user can correct detected data
    const session = getSession(ctx.from.id);
    if (session.detected) {
      session.state = 'waiting_manual_name';
      await safeReply(ctx, `✏️ Enter the <b>collection name</b>:`, { parse_mode: 'HTML', ...cancelKeyboard() });
    }
    return;
  }

  // Dynamic patterns
  let m;

  if ((m = data.match(/^view_mint_(\d+)$/))) return handleViewMint(ctx, parseInt(m[1]));
  if ((m = data.match(/^edit_mint_(\d+)$/))) return handleEditMint(ctx, parseInt(m[1]));
  if ((m = data.match(/^delete_mint_(\d+)$/))) return handleDeleteMint(ctx, parseInt(m[1]));
  if ((m = data.match(/^confirm_delete_(\d+)$/))) return handleConfirmDelete(ctx, parseInt(m[1]));
  if ((m = data.match(/^toggle_pause_(\d+)$/))) return handleTogglePause(ctx, parseInt(m[1]));
  if ((m = data.match(/^refresh_mint_(\d+)$/))) return handleRefreshMint(ctx, parseInt(m[1]));
  if ((m = data.match(/^edit_phases_(\d+)$/))) return handleEditMint(ctx, parseInt(m[1]));
  if ((m = data.match(/^edit_field_(\d+)_(.+)$/))) return handleEditField(ctx, parseInt(m[1]), m[2]);
  if ((m = data.match(/^channel_settings_(-?\d+)$/))) return handleChannelSettings(ctx, m[1]);
  if ((m = data.match(/^toggle_alerts_(-?\d+)$/))) return handleToggleAlerts(ctx, m[1]);
  if ((m = data.match(/^toggle_summary_(-?\d+)$/))) return handleToggleSummary(ctx, m[1]);
  if ((m = data.match(/^remove_channel_(-?\d+)$/))) return handleRemoveChannel(ctx, m[1]);
}

// ── Safe reply helper (handles message too long, edits, etc.) ─────────────────

async function safeReply(ctx, text, options = {}) {
  // Truncate if too long for Telegram (max 4096 chars)
  const truncated = text.length > 4000 ? text.slice(0, 3990) + '\n...' : text;
  try {
    return await ctx.reply(truncated, options);
  } catch (err) {
    logger.warn(`Reply failed: ${err.message}`);
    // Try plain text fallback
    return ctx.reply(truncated.replace(/<[^>]+>/g, '')).catch(() => {});
  }
}

module.exports = {
  handleStart,
  handleDashboard,
  handleListMints,
  handleViewMint,
  handleTodaysMints,
  handleText,
  handleCallback,
  handleStatus,
  handleHelp,
  isAdmin,
};
