'use strict';

const { getChainEmoji, formatTimeUTC, timeUntil, normalizePriceStr, parseTime } = require('./parser');

// Escape HTML entities for Telegram HTML parse mode
function escHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function supplyBar(minted, total, width = 10) {
  if (!total || total <= 0) return '';
  const ratio = Math.min(1, minted / total);
  const filled = Math.round(ratio * width);
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

function formatMintCard(mint) {
  const chainEmoji = getChainEmoji(mint.chain);
  const statusEmoji = { upcoming: '🕐', live: '🟢', sold_out: '🔴', ended: '⚫' }[mint.status] || '⚪';
  const lines = [];

  lines.push(`${statusEmoji} <b>${escHtml(mint.name)}</b>`);
  lines.push(`${chainEmoji} <b>Chain:</b> ${escHtml(mint.chain)}`);
  lines.push(`🔗 <b>Mint:</b> <a href="${escHtml(mint.mint_link)}">Link</a>`);

  if (mint.total_supply) {
    const minted = mint.minted || 0;
    const pct = Math.round((minted / mint.total_supply) * 100);
    const bar = supplyBar(minted, mint.total_supply);
    lines.push(`📦 <b>Supply:</b> ${minted.toLocaleString()} / ${mint.total_supply.toLocaleString()} (${pct}%)`);
    lines.push(`  <code>${bar}</code>`);
  }

  if (mint.contract) {
    lines.push(`📋 <b>Contract:</b> <code>${escHtml(mint.contract)}</code>`);
  }

  if (mint.phases && mint.phases.length > 0) {
    lines.push('\n📅 <b>Phases:</b>');
    for (const p of mint.phases) {
      const t = parseTime(p.time);
      const timeStr = t ? formatTimeUTC(t) : 'TBD';
      const until = t ? ` (${timeUntil(t)})` : '';
      const price = normalizePriceStr(p.price);
      const limit = p.limit ? ` · max ${p.limit}` : '';
      lines.push(`  ┣ ${escHtml(p.name)}: ${timeStr}${until}`);
      lines.push(`  ┗ 💰 ${escHtml(price)}${escHtml(limit)}`);
    }
  }

  const socials = [];
  if (mint.x_link) socials.push(`<a href="${escHtml(mint.x_link)}">Twitter/X</a>`);
  if (mint.discord_link) socials.push(`<a href="${escHtml(mint.discord_link)}">Discord</a>`);
  if (mint.os_link) socials.push(`<a href="${escHtml(mint.os_link)}">OpenSea</a>`);
  if (socials.length > 0) lines.push(`\n🌐 ${socials.join(' · ')}`);

  if (mint.notes) lines.push(`\n📝 ${escHtml(mint.notes)}`);

  return lines.join('\n');
}

function formatPreAlert(mint, phase, minutesBefore) {
  const chainEmoji = getChainEmoji(mint.chain);
  const t = parseTime(phase.time);
  const price = normalizePriceStr(phase.price);
  const lines = [];

  lines.push(`⏰ <b>MINT ALERT — ${escHtml(minutesBefore)} min!</b>`);
  lines.push(`\n${chainEmoji} <b>${escHtml(mint.name)}</b>`);
  lines.push(`📌 Phase: ${escHtml(phase.name)}`);
  lines.push(`⏱ Time: ${t ? formatTimeUTC(t) : 'TBD'}`);
  lines.push(`💰 Price: ${escHtml(price)}`);
  if (phase.limit) lines.push(`🎫 Limit: ${escHtml(String(phase.limit))} per wallet`);

  if (mint.total_supply) {
    const minted = mint.minted || 0;
    const bar = supplyBar(minted, mint.total_supply);
    lines.push(`📦 ${minted.toLocaleString()} / ${mint.total_supply.toLocaleString()} minted`);
    lines.push(`  <code>${bar}</code>`);
  }

  lines.push(`\n🔗 <a href="${escHtml(mint.mint_link)}">Mint Now</a>`);
  if (mint.x_link) lines.push(`🐦 <a href="${escHtml(mint.x_link)}">Twitter</a>`);
  if (mint.discord_link) lines.push(`💬 <a href="${escHtml(mint.discord_link)}">Discord</a>`);

  return lines.join('\n');
}

function formatLiveAlert(mint, phase) {
  const chainEmoji = getChainEmoji(mint.chain);
  const price = normalizePriceStr(phase.price);
  const lines = [];

  lines.push(`🟢 <b>MINT IS LIVE!</b>`);
  lines.push(`\n${chainEmoji} <b>${escHtml(mint.name)}</b>`);
  lines.push(`📌 Phase: ${escHtml(phase.name)}`);
  lines.push(`💰 Price: ${escHtml(price)}`);
  if (phase.limit) lines.push(`🎫 Limit: ${escHtml(String(phase.limit))} per wallet`);

  if (mint.total_supply) {
    const minted = mint.minted || 0;
    const bar = supplyBar(minted, mint.total_supply);
    lines.push(`📦 ${minted.toLocaleString()} / ${mint.total_supply.toLocaleString()} minted`);
    lines.push(`  <code>${bar}</code>`);
  }

  lines.push(`\n🔗 <a href="${escHtml(mint.mint_link)}">Mint Now!</a>`);
  if (mint.x_link) lines.push(`🐦 <a href="${escHtml(mint.x_link)}">Twitter</a>`);

  return lines.join('\n');
}

function formatSoldOutAlert(mint, floorPrice) {
  const chainEmoji = getChainEmoji(mint.chain);
  const lines = [];

  lines.push(`🔴 <b>SOLD OUT!</b>`);
  lines.push(`\n${chainEmoji} <b>${escHtml(mint.name)}</b>`);

  if (mint.total_supply) {
    lines.push(`📦 ${mint.total_supply.toLocaleString()} / ${mint.total_supply.toLocaleString()} minted`);
  }

  if (floorPrice) {
    lines.push(`\n💎 Floor: ${floorPrice} ETH`);
  }

  const marketLinks = [];
  if (mint.market_links) {
    if (mint.market_links.opensea) marketLinks.push(`<a href="${escHtml(mint.market_links.opensea)}">OpenSea</a>`);
    if (mint.market_links.blur) marketLinks.push(`<a href="${escHtml(mint.market_links.blur)}">Blur</a>`);
    if (mint.market_links.magiceden) marketLinks.push(`<a href="${escHtml(mint.market_links.magiceden)}">MagicEden</a>`);
  }
  if (marketLinks.length > 0) lines.push(`\n🛒 Trade: ${marketLinks.join(' · ')}`);
  if (mint.x_link) lines.push(`🐦 <a href="${escHtml(mint.x_link)}">Twitter</a>`);

  return lines.join('\n');
}

function formatFloorPumpAlert(mint, oldFloor, newFloor, pctChange) {
  const chainEmoji = getChainEmoji(mint.chain);
  const lines = [];

  lines.push(`📈 <b>FLOOR PUMP!</b>`);
  lines.push(`\n${chainEmoji} <b>${escHtml(mint.name)}</b>`);
  lines.push(`📊 ${oldFloor} ETH → <b>${newFloor} ETH</b> (+${Math.round(pctChange * 100)}%)`);

  if (mint.market_links && mint.market_links.opensea) {
    lines.push(`\n🛒 <a href="${escHtml(mint.market_links.opensea)}">Trade on OpenSea</a>`);
  }

  return lines.join('\n');
}

function formatSweepAlert(mint, sweepCount) {
  const chainEmoji = getChainEmoji(mint.chain);
  const lines = [];

  lines.push(`🧹 <b>SWEEP ALERT!</b>`);
  lines.push(`\n${chainEmoji} <b>${escHtml(mint.name)}</b>`);
  lines.push(`💥 ${sweepCount} NFTs bought in the last 60 seconds!`);

  if (mint.market_links && mint.market_links.opensea) {
    lines.push(`\n🛒 <a href="${escHtml(mint.market_links.opensea)}">OpenSea</a>`);
  }

  return lines.join('\n');
}

function formatFastMintAlert(mint) {
  const chainEmoji = getChainEmoji(mint.chain);
  const minted = mint.minted || 0;
  const total = mint.total_supply || 0;
  const pct = total ? Math.round((minted / total) * 100) : 0;
  const bar = supplyBar(minted, total);
  const lines = [];

  lines.push(`⚡ <b>FAST MINT!</b>`);
  lines.push(`\n${chainEmoji} <b>${escHtml(mint.name)}</b>`);
  lines.push(`📦 ${minted.toLocaleString()} / ${total.toLocaleString()} minted (${pct}%)`);
  lines.push(`  <code>${bar}</code>`);
  lines.push(`\n🔗 <a href="${escHtml(mint.mint_link)}">Mint Now!</a>`);

  return lines.join('\n');
}

function formatDailySummary(mints, date) {
  const dateStr = date || new Date().toISOString().split('T')[0];
  const lines = [`📅 <b>Daily Mint Summary — ${escHtml(dateStr)}</b>\n`];

  const numEmojis = ['1️⃣','2️⃣','3️⃣','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟'];
  let idx = 0;

  for (const mint of mints) {
    const chainEmoji = getChainEmoji(mint.chain);
    const num = numEmojis[idx] || `${idx + 1}.`;
    idx++;

    lines.push(`${num} ${chainEmoji} <b>${escHtml(mint.name)}</b>`);

    if (mint.phases && mint.phases.length > 0) {
      for (const p of mint.phases) {
        const t = parseTime(p.time);
        const timeStr = t ? formatTimeUTC(t) : 'TBD';
        const price = normalizePriceStr(p.price);
        lines.push(`  ┣ ${escHtml(p.name)}: ${timeStr}`);
        lines.push(`  ┗ 💰 ${escHtml(price)}`);
      }
    }

    if (mint.mint_link) lines.push(`  🔗 <a href="${escHtml(mint.mint_link)}">Mint</a>`);
    lines.push('');
  }

  lines.push(`\n⏱ Updated: ${new Date().toUTCString()}`);
  return lines.join('\n');
}

function formatMintsList(mints) {
  if (mints.length === 0) return '📭 No mints tracked yet.';
  const statusEmoji = { upcoming: '🕐', live: '🟢', sold_out: '🔴', ended: '⚫' };
  const lines = ['📋 <b>Tracked Mints</b>\n'];

  for (const m of mints) {
    const se = statusEmoji[m.status] || '⚪';
    const chainEmoji = getChainEmoji(m.chain);
    const pause = m.paused ? ' ⏸' : '';
    lines.push(`${se}${chainEmoji} <b>${escHtml(m.name)}</b>${pause} <code>[#${m.id}]</code>`);
  }
  return lines.join('\n');
}

module.exports = {
  escHtml,
  supplyBar,
  formatMintCard,
  formatPreAlert,
  formatLiveAlert,
  formatSoldOutAlert,
  formatFloorPumpAlert,
  formatSweepAlert,
  formatFastMintAlert,
  formatDailySummary,
  formatMintsList,
};
