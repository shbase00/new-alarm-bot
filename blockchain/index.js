'use strict';

const { getTotalSupply, watchTransferEvents, getRecentMintEvents, isNFTContract } = require('./evm');
const { getNetwork, getChainId } = require('./networks');
const logger = require('../utils/logger');

// Active event subscriptions keyed by "mintId"
const activeWatchers = new Map();

/**
 * Fetch current minted count for a mint entry.
 * Tries: blockchain contract → OpenSea Drops API fallback.
 */
async function fetchMintedCount(mint) {
  if (mint.contract && mint.chain !== 'Solana' && mint.chain !== 'Bitcoin') {
    const count = await getTotalSupply(mint.contract, mint.chain);
    if (count !== null) return count;
  }
  return null;
}

/**
 * Start watching Transfer events for a live mint.
 * Calls onNewMint(tokenId, txHash) for each new NFT minted.
 */
async function startContractWatcher(mint, onNewMint) {
  if (!mint.contract || mint.chain === 'Solana' || mint.chain === 'Bitcoin') return;

  const key = String(mint.id);
  if (activeWatchers.has(key)) return; // already watching

  const unsub = await watchTransferEvents(mint.contract, mint.chain, ({ tokenId, txHash }) => {
    onNewMint({ mintId: mint.id, tokenId, txHash });
  });

  activeWatchers.set(key, unsub);
  logger.info(`Started contract watcher for mint #${mint.id} (${mint.name})`);
}

/**
 * Stop watching a specific mint's contract events.
 */
function stopContractWatcher(mintId) {
  const key = String(mintId);
  const unsub = activeWatchers.get(key);
  if (unsub) {
    try { unsub(); } catch {}
    activeWatchers.delete(key);
    logger.info(`Stopped contract watcher for mint #${mintId}`);
  }
}

/**
 * Stop all watchers (called on shutdown).
 */
function stopAllWatchers() {
  for (const [key, unsub] of activeWatchers.entries()) {
    try { unsub(); } catch {}
    activeWatchers.delete(key);
  }
}

module.exports = {
  fetchMintedCount,
  startContractWatcher,
  stopContractWatcher,
  stopAllWatchers,
  getTotalSupply,
  getRecentMintEvents,
  isNFTContract,
  getNetwork,
  getChainId,
};
