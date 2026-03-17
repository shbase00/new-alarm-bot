'use strict';

const { getDb } = require('./index');

function getAllChannels() {
  return getDb().prepare('SELECT * FROM channels').all();
}

function getAlertChannels() {
  return getDb().prepare('SELECT * FROM channels WHERE receive_alerts = 1').all();
}

function getSummaryChannels() {
  return getDb().prepare('SELECT * FROM channels WHERE receive_summary = 1').all();
}

function getChannelById(channelId) {
  return getDb().prepare('SELECT * FROM channels WHERE channel_id = ?').get(String(channelId));
}

function upsertChannel(channelId, channelName, receiveAlerts = true, receiveSummary = true) {
  getDb().prepare(`
    INSERT INTO channels (channel_id, channel_name, receive_alerts, receive_summary)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(channel_id) DO UPDATE SET
      channel_name = excluded.channel_name,
      receive_alerts = excluded.receive_alerts,
      receive_summary = excluded.receive_summary
  `).run(String(channelId), channelName || '', receiveAlerts ? 1 : 0, receiveSummary ? 1 : 0);
  return getChannelById(channelId);
}

function updateChannel(channelId, updates) {
  const { receive_alerts, receive_summary, channel_name } = updates;
  getDb().prepare(`
    UPDATE channels SET
      receive_alerts = COALESCE(?, receive_alerts),
      receive_summary = COALESCE(?, receive_summary),
      channel_name = COALESCE(?, channel_name)
    WHERE channel_id = ?
  `).run(
    receive_alerts !== undefined ? (receive_alerts ? 1 : 0) : null,
    receive_summary !== undefined ? (receive_summary ? 1 : 0) : null,
    channel_name !== undefined ? channel_name : null,
    String(channelId)
  );
  return getChannelById(channelId);
}

function deleteChannel(channelId) {
  getDb().prepare('DELETE FROM channels WHERE channel_id = ?').run(String(channelId));
}

module.exports = {
  getAllChannels, getAlertChannels, getSummaryChannels, getChannelById,
  upsertChannel, updateChannel, deleteChannel,
};
