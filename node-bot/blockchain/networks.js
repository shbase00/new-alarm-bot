'use strict';

// Chain ID → RPC config for Etherscan V2 multi-chain API
// Etherscan V2 uses a single key with ?chainid= parameter
const ETHERSCAN_V2_URL = 'https://api.etherscan.io/v2/api';

const NETWORKS = {
  Ethereum: {
    chainId: 1,
    rpcUrls: [
      'https://eth.llamarpc.com',
      'https://rpc.ankr.com/eth',
      'https://cloudflare-eth.com',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Base: {
    chainId: 8453,
    rpcUrls: [
      'https://mainnet.base.org',
      'https://rpc.ankr.com/base',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Arbitrum: {
    chainId: 42161,
    rpcUrls: [
      'https://arb1.arbitrum.io/rpc',
      'https://rpc.ankr.com/arbitrum',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Optimism: {
    chainId: 10,
    rpcUrls: [
      'https://mainnet.optimism.io',
      'https://rpc.ankr.com/optimism',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Polygon: {
    chainId: 137,
    rpcUrls: [
      'https://polygon-rpc.com',
      'https://rpc.ankr.com/polygon',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'MATIC',
  },
  Blast: {
    chainId: 81457,
    rpcUrls: ['https://rpc.blast.io'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Zora: {
    chainId: 7777777,
    rpcUrls: ['https://rpc.zora.energy'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Linea: {
    chainId: 59144,
    rpcUrls: ['https://rpc.linea.build'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Scroll: {
    chainId: 534352,
    rpcUrls: ['https://rpc.scroll.io'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Mantle: {
    chainId: 5000,
    rpcUrls: ['https://rpc.mantle.xyz'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'MNT',
  },
  opBNB: {
    chainId: 204,
    rpcUrls: ['https://opbnb-mainnet-rpc.bnbchain.org'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'BNB',
  },
  BNB: {
    chainId: 56,
    rpcUrls: [
      'https://bsc-dataseed1.binance.org',
      'https://rpc.ankr.com/bsc',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'BNB',
  },
  Avalanche: {
    chainId: 43114,
    rpcUrls: [
      'https://api.avax.network/ext/bc/C/rpc',
      'https://rpc.ankr.com/avalanche',
    ],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'AVAX',
  },
  Abstract: {
    chainId: 2741,
    rpcUrls: ['https://api.mainnet.abs.xyz'],
    explorerApi: 'https://api.abscan.org/api', // Abscan
    nativeCurrency: 'ETH',
  },
  MegaETH: {
    chainId: 4326,
    rpcUrls: ['https://rpc.megaeth.systems'],
    explorerApi: null,
    nativeCurrency: 'ETH',
  },
  ApeChain: {
    chainId: 33139,
    rpcUrls: ['https://rpc.apechain.com/http'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'APE',
  },
  Celo: {
    chainId: 42220,
    rpcUrls: ['https://forno.celo.org'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'CELO',
  },
  UniChain: {
    chainId: 130,
    rpcUrls: ['https://mainnet.unichain.org'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
  Taiko: {
    chainId: 167000,
    rpcUrls: ['https://rpc.mainnet.taiko.xyz'],
    explorerApi: ETHERSCAN_V2_URL,
    nativeCurrency: 'ETH',
  },
};

function getNetwork(chain) {
  return NETWORKS[chain] || NETWORKS.Ethereum;
}

function getChainId(chain) {
  return (NETWORKS[chain] || NETWORKS.Ethereum).chainId;
}

module.exports = { NETWORKS, getNetwork, getChainId };
