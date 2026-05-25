/**
 * Blockchain Configuration
 * Grid-Guardian - Smart Contract Integration
 */

// Contract ABIs - import from compiled artifacts
const path = require('path');
const fs = require('fs');

// Helper to load ABI from artifact
const loadABI = (contractName) => {
  const artifactPath = path.resolve(
    __dirname,
    `../../../Blockchain/artifacts/contracts/${contractName}.sol/${contractName}.json`
  );

  try {
    if (fs.existsSync(artifactPath)) {
      const artifact = JSON.parse(fs.readFileSync(artifactPath, 'utf8'));
      return artifact.abi;
    }
  } catch (error) {
    console.warn(`Warning: Could not load ABI for ${contractName}:`, error.message);
  }

  return null;
};

module.exports = {
  // Network configuration
  rpcUrl: process.env.RPC_URL || 'http://127.0.0.1:8545',
  chainId: parseInt(process.env.CHAIN_ID || '31337', 10),

  // Relayer wallet (backend wallet for signing transactions)
  relayerPrivateKey: process.env.RELAYER_PRIVATE_KEY,

  // Contract addresses (set after deployment)
  contracts: {
    identity: {
      address: process.env.IDENTITY_SC_ADDRESS || null,
      abi: loadABI('IdentitySC'),
    },
    collateral: {
      address: process.env.COLLATERAL_SC_ADDRESS || null,
      abi: loadABI('CollateralSC'),
    },
    trading: {
      address: process.env.TRADING_SC_ADDRESS || null,
      abi: loadABI('TradingSC'),
    },
    matchRegistry: {
      address: process.env.MATCH_REGISTRY_ADDRESS || null,
      abi: loadABI('MatchRegistry'),
    },
    settlement: {
      address: process.env.SETTLEMENT_SC_ADDRESS || null,
      abi: loadABI('SettlementSC'),
    },
    deliveryRegistry: {
      address: process.env.DELIVERY_REGISTRY_ADDRESS || null,
      abi: loadABI('DeliveryRegistry'),
    },
    mockUsdc: {
      address: process.env.MOCK_USDC_ADDRESS || null,
      abi: loadABI('MockUSDC'),
    },
  },

  // Transaction settings
  tx: {
    gasLimit: parseInt(process.env.GAS_LIMIT || '500000', 10),
    maxPriorityFeePerGas: process.env.MAX_PRIORITY_FEE || '1000000000', // 1 gwei
    maxFeePerGas: process.env.MAX_FEE || '50000000000', // 50 gwei
    confirmations: parseInt(process.env.TX_CONFIRMATIONS || '1', 10),
    retryAttempts: parseInt(process.env.TX_RETRY_ATTEMPTS || '3', 10),
    retryDelayMs: parseInt(process.env.TX_RETRY_DELAY_MS || '5000', 10),
  },

  // Event listener settings
  eventListener: {
    pollIntervalMs: parseInt(process.env.EVENT_POLL_INTERVAL_MS || '5000', 10),
    blockConfirmations: parseInt(process.env.BLOCK_CONFIRMATIONS || '1', 10),
    maxBlocksPerPoll: parseInt(process.env.MAX_BLOCKS_PER_POLL || '100', 10),
    reconnectDelayMs: parseInt(process.env.EVENT_RECONNECT_DELAY_MS || '10000', 10),
  },

  // Settlement settings
  settlement: {
    settlementTimeoutBlocks: parseInt(process.env.SETTLEMENT_TIMEOUT_BLOCKS || '100', 10),
    disputeWindowBlocks: parseInt(process.env.DISPUTE_WINDOW_BLOCKS || '50', 10),
  },

  // EIP-712 domain configuration
  eip712: {
    trading: {
      name: 'GridGuardian-Trading',
      version: '1',
    },
    delivery: {
      name: 'GridGuardian-Delivery',
      version: '1',
    },
    collateral: {
      name: 'GridGuardian-Collateral',
      version: '1',
    },
    match: {
      name: 'GridGuardian-Match',
      version: '1',
    },
    identity: {
      name: 'GridGuardian-Relayer',
      version: '1',
    },
  },

  // Validate configuration
  isConfigured() {
    return !!(
      this.rpcUrl &&
      this.relayerPrivateKey &&
      this.contracts.identity.address &&
      this.contracts.collateral.address &&
      this.contracts.settlement.address
    );
  },

  // Get configured contracts
  getConfiguredContracts() {
    const configured = [];
    for (const [name, config] of Object.entries(this.contracts)) {
      if (config.address && config.abi) {
        configured.push(name);
      }
    }
    return configured;
  },
};
