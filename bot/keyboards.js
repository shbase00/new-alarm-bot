'use strict';

const { Markup } = require('telegraf');

// Main reply keyboard
const mainKeyboard = Markup.keyboard([
  ['➕ Add Mint', '📋 All Mints'],
  ['📅 Today\'s Mints', '📢 Channels'],
  ['🎛 Dashboard', 'ℹ️ Help'],
]).resize();

// Dashboard inline keyboard
const dashboardInline = Markup.inlineKeyboard([
  [
    Markup.button.callback('➕ Add Mint', 'add_mint'),
    Markup.button.callback('📋 All Mints', 'list_mints'),
  ],
  [
    Markup.button.callback('📅 Today\'s Mints', 'today_mints'),
    Markup.button.callback('📢 Channels', 'manage_channels'),
  ],
  [Markup.button.callback('📊 Status', 'bot_status')],
]);

function mintActionsKeyboard(mintId) {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback('✏️ Edit', `edit_mint_${mintId}`),
      Markup.button.callback('🗑 Delete', `delete_mint_${mintId}`),
    ],
    [
      Markup.button.callback('⏸ Toggle Pause', `toggle_pause_${mintId}`),
      Markup.button.callback('🔄 Refresh', `refresh_mint_${mintId}`),
    ],
    [Markup.button.callback('« Back to List', 'list_mints')],
  ]);
}

function editMintKeyboard(mintId) {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback('📝 Name', `edit_field_${mintId}_name`),
      Markup.button.callback('⛓ Chain', `edit_field_${mintId}_chain`),
    ],
    [
      Markup.button.callback('🔗 Mint Link', `edit_field_${mintId}_mint_link`),
      Markup.button.callback('📋 Contract', `edit_field_${mintId}_contract`),
    ],
    [
      Markup.button.callback('🐦 Twitter', `edit_field_${mintId}_x_link`),
      Markup.button.callback('💬 Discord', `edit_field_${mintId}_discord_link`),
    ],
    [
      Markup.button.callback('📦 Supply', `edit_field_${mintId}_total_supply`),
      Markup.button.callback('📅 Phases', `edit_phases_${mintId}`),
    ],
    [Markup.button.callback('« Back', `view_mint_${mintId}`)],
  ]);
}

function confirmDeleteKeyboard(mintId) {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback('✅ Yes, Delete', `confirm_delete_${mintId}`),
      Markup.button.callback('❌ Cancel', `view_mint_${mintId}`),
    ],
  ]);
}

function channelActionsKeyboard(channelId) {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback('🔔 Toggle Alerts', `toggle_alerts_${channelId}`),
      Markup.button.callback('📊 Toggle Summary', `toggle_summary_${channelId}`),
    ],
    [Markup.button.callback('🗑 Remove Channel', `remove_channel_${channelId}`)],
    [Markup.button.callback('« Back', 'manage_channels')],
  ]);
}

function cancelKeyboard() {
  return Markup.inlineKeyboard([
    [Markup.button.callback('❌ Cancel', 'cancel_action')],
  ]);
}

function skipCancelKeyboard() {
  return Markup.inlineKeyboard([
    [
      Markup.button.callback('⏭ Skip', 'skip_step'),
      Markup.button.callback('❌ Cancel', 'cancel_action'),
    ],
  ]);
}

module.exports = {
  mainKeyboard,
  dashboardInline,
  mintActionsKeyboard,
  editMintKeyboard,
  confirmDeleteKeyboard,
  channelActionsKeyboard,
  cancelKeyboard,
  skipCancelKeyboard,
};
