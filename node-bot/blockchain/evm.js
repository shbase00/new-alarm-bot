'use strict';

const { ethers } = require('ethers');
const axios = require('axios');
const { getNetwork } = require('./networks');
const logger = require('../utils/logger');

// ERC-721 / ERC-1155 minimal ABI selectors
const TOTAL_SUPPLY_SELECTOR = '0x18160ddd'; // totalSupply()

// Transfer event topic (ERC-721 & ERC-20)
const TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef';

/**
 * Get an ethers JsonRpcProvider for a given chain, trying each RPC in order.
 */
async function getProvider(chain) {
  const network = getNetwork(chain);
  for (const url of network.rpcUrls) {
    try {
      const provider = new ethers.JsonRpcProvider(url, network.chainId, { staticNetwork: true });
      // Quick connectivity test
      await Promise.race([
        provider.getBlockNumber(),
        new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000)),
      ]);
      return provider;
    } catch {
      logger.debug(`RPC ${url} unreachable, trying next`);
    }
  }
  throw new Error(`No working RPC for chain ${chain}`);
}

/**
 * Call totalSupply() on an ERC-721 contract via Etherscan V2 API (preferred)
 * or direct eth_call as fallback.
 */
async function getTotalSupply(contractAddress, chain) {
  if (!contractAddress) return null;

  // Method 1: Etherscan V2 API eth_call
  const etherscanKey = process.env.ETHERSCAN_API_KEY;
  const network = getNetwork(chain);
  const chainId = network.chainId;

  if (etherscanKey && network.explorerApi && !network.explorerApi.includes('abscan')) {
    try {
      const url = `${network.explorerApi}?chainid=${chainId}&module=proxy&action=eth_call` +
        `&to=${contractAddress}&data=${TOTAL_SUPPLY_SELECTOR}&tag=latest&apikey=${etherscanKey}`;
      const resp = await axios.get(url, { timeout: 8000 });
      if (resp.data?.result && resp.data.result !== '0x') {
        const supply = parseInt(resp.data.result, 16);
        if (!isNaN(supply)) return supply;
      }
    } catch (err) {
      logger.debug(`Etherscan totalSupply failed for ${contractAddress}: ${err.message}`);
    }
  }

  // Method 2: Abscan for Abstract chain
  if (chain === 'Abstract') {
    const abscanKey = process.env.ABSCAN_API_KEY;
    if (abscanKey) {
      try {
        const url = `https://api.abscan.org/api?module=proxy&action=eth_call` +
          `&to=${contractAddress}&data=${TOTAL_SUPPLY_SELECTOR}&tag=latest&apikey=${abscanKey}`;
        const resp = await axios.get(url, { timeout: 8000 });
        if (resp.data?.result && resp.data.result !== '0x') {
          const supply = parseInt(resp.data.result, 16);
          if (!isNaN(supply)) return supply;
        }
      } catch (err) {
        logger.debug(`Abscan totalSupply failed: ${err.message}`);
      }
    }
  }

  // Method 3: Direct eth_call via RPC
  try {
    const provider = await getProvider(chain);
    const result = await provider.call({
      to: contractAddress,
      data: TOTAL_SUPPLY_SELECTOR,
    });
    if (result && result !== '0x') {
      const supply = parseInt(result, 16);
      if (!isNaN(supply)) return supply;
    }
  } catch (err) {
    logger.debug(`RPC totalSupply failed for ${contractAddress}: ${err.message}`);
  }

  return null;
}

/**
 * Watch Transfer events on a contract for real-time monitoring.
 * Returns an unsubscribe function.
 */
async function watchTransferEvents(contractAddress, chain, onTransfer) {
  const minAbi = ['event Transfer(address indexed from, address indexed to, uint256 indexed tokenId)'];
  let provider;
  try {
    provider = await getProvider(chain);
  } catch (err) {
    logger.warn(`Cannot watch events on ${chain}: ${err.message}`);
    return () => {};
  }

  const contract = new ethers.Contract(contractAddress, minAbi, provider);
  const filter = contract.filters.Transfer();

  const listener = (from, to, tokenId, event) => {
    onTransfer({ from, to, tokenId: tokenId.toString(), txHash: event.log.transactionHash });
  };

  contract.on(filter, listener);
  logger.info(`Watching Transfer events on ${contractAddress} (${chain})`);

  return () => {
    try { contract.off(filter, listener); } catch {}
    try { provider.destroy(); } catch {}
  };
}

/**
 * Get recent Transfer events (mint = from zero address) via Etherscan logs.
 */
async function getRecentMintEvents(contractAddress, chain, blocksBack = 200) {
  const etherscanKey = process.env.ETHERSCAN_API_KEY;
  const network = getNetwork(chain);
  if (!etherscanKey || !network.explorerApi) return [];

  try {
    const provider = await getProvider(chain);
    const latestBlock = await provider.getBlockNumber();
    const fromBlock = latestBlock - blocksBack;
    const zeroAddress = '0x0000000000000000000000000000000000000000000000000000000000000000';

    const url = `${network.explorerApi}?chainid=${network.chainId}&module=logs&action=getLogs` +
      `&fromBlock=${fromBlock}&toBlock=latest` +
      `&address=${contractAddress}` +
      `&topic0=${TRANSFER_TOPIC}` +
      `&topic1=${zeroAddress}` +
      `&topic0_1_opr=and` +
      `&apikey=${etherscanKey}`;

    const resp = await axios.get(url, { timeout: 10000 });
    return resp.data?.result || [];
  } catch (err) {
    logger.debug(`getRecentMintEvents failed: ${err.message}`);
    return [];
  }
}

/**
 * Detect contract address from transaction data by scanning recent txs to the contract.
 * Used when we have a contract but want to verify it's an NFT.
 */
async function isNFTContract(contractAddress, chain) {
  // ERC-165 supportsInterface checks
  const ERC721_INTERFACE = '0x80ac58cd';
  const ERC1155_INTERFACE = '0xd9b67a26';
  const SUPPORTS_INTERFACE = (id) =>
    '0x01ffc9a7' + '0'.repeat(24) + id.slice(2) + '0'.repeat(56);

  try {
    const provider = await getProvider(chain);
    for (const interfaceId of [ERC721_INTERFACE, ERC1155_INTERFACE]) {
      const result = await provider.call({
        to: contractAddress,
        data: SUPPORTS_INTERFACE(interfaceId),
      });
      if (result === '0x0000000000000000000000000000000000000000000000000000000000000001') {
        return true;
      }
    }
  } catch {}
  return false;
}

module.exports = {
  getProvider,
  getTotalSupply,
  watchTransferEvents,
  getRecentMintEvents,
  isNFTContract,
};
