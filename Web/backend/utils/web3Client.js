/**
 * Web3 Client Utility
 * Grid-Guardian - Blockchain Connectivity Layer
 */

const { ethers } = require('ethers');
const blockchainConfig = require('../config/blockchain.config');
const logger = require('./logger');
const { createIdempotencyKey } = require('./retryHelper');
const TxLog = require('../models/txLog.model');

class Web3Client {
  constructor() {
    this.provider = null;
    this.wallet = null;
    this.contracts = {};
    this.isConnected = false;
    this.nonceManager = new Map(); // nodeId -> nonce
    this.lastBlockNumber = 0;
  }

  /**
   * Initialize the Web3 client
   */
  async initialize() {
    try {
      // Create provider
      this.provider = new ethers.JsonRpcProvider(blockchainConfig.rpcUrl);

      // Verify connection
      const network = await this.provider.getNetwork();
      logger.info(`Connected to blockchain network: chainId=${network.chainId}`);

      if (Number(network.chainId) !== blockchainConfig.chainId) {
        logger.warn(
          `Chain ID mismatch: expected ${blockchainConfig.chainId}, got ${network.chainId}`
        );
      }

      // Create wallet
      if (blockchainConfig.relayerPrivateKey) {
        this.wallet = new ethers.Wallet(blockchainConfig.relayerPrivateKey, this.provider);
        logger.info(`Relayer wallet initialized: ${this.wallet.address}`);
      } else {
        logger.warn('No relayer private key provided - read-only mode');
      }

      // Initialize contracts
      await this._initializeContracts();

      this.isConnected = true;
      this.lastBlockNumber = await this.provider.getBlockNumber();

      logger.info(`Web3 client initialized. Current block: ${this.lastBlockNumber}`);
      return true;
    } catch (error) {
      logger.error('Failed to initialize Web3 client:', error);
      this.isConnected = false;
      throw error;
    }
  }

  /**
   * Initialize contract instances
   */
  async _initializeContracts() {
    const contractConfigs = blockchainConfig.contracts;

    for (const [name, config] of Object.entries(contractConfigs)) {
      if (config.address && config.abi) {
        try {
          const signer = this.wallet || this.provider;
          this.contracts[name] = new ethers.Contract(config.address, config.abi, signer);
          logger.info(`Contract ${name} initialized at ${config.address}`);
        } catch (error) {
          logger.error(`Failed to initialize contract ${name}:`, error);
        }
      } else {
        logger.debug(`Contract ${name} not configured (missing address or ABI)`);
      }
    }
  }

  /**
   * Get a contract instance
   */
  getContract(name) {
    if (!this.contracts[name]) {
      throw new Error(`Contract ${name} not initialized`);
    }
    return this.contracts[name];
  }

  /**
   * Check if a contract is available
   */
  hasContract(name) {
    return !!this.contracts[name];
  }

  /**
   * Get current block number
   */
  async getBlockNumber() {
    return await this.provider.getBlockNumber();
  }

  /**
   * Get managed nonce for a wallet
   */
  async getManagedNonce(address = null) {
    const addr = address || this.wallet?.address;
    if (!addr) throw new Error('No address provided');

    const currentNonce = await this.provider.getTransactionCount(addr, 'pending');
    const managedNonce = this.nonceManager.get(addr) || 0;

    const nonce = Math.max(currentNonce, managedNonce);
    this.nonceManager.set(addr, nonce + 1);

    return nonce;
  }

  /**
   * Reset nonce for an address
   */
  resetNonce(address = null) {
    const addr = address || this.wallet?.address;
    if (addr) {
      this.nonceManager.delete(addr);
    }
  }

  /**
   * Send a transaction with retry logic
   */
  async sendTransaction(contract, method, args, options = {}) {
    const maxRetries = options.retries || blockchainConfig.tx.retryAttempts;
    const retryDelay = options.retryDelay || blockchainConfig.tx.retryDelayMs;
    const idempotencyKey = createIdempotencyKey(method, contract?.target, args);

    let lastError;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        logger.debug(`Sending tx ${method} (attempt ${attempt}/${maxRetries})`);

        const txOptions = {
          gasLimit: options.gasLimit || blockchainConfig.tx.gasLimit,
        };

        // Add nonce management
        if (this.wallet) {
          txOptions.nonce = await this.getManagedNonce();
        }

        const tx = await contract[method](...args, txOptions);
        logger.info(`Transaction sent: ${tx.hash}`);

        await this._persistTxLog({
          operation: method,
          contractAddress: String(contract.target),
          idempotencyKey,
          method,
          args,
          txHash: tx.hash,
          status: 'PENDING',
          attempt,
        });

        // Wait for confirmation
        const receipt = await tx.wait(blockchainConfig.tx.confirmations);

        logger.info(
          `Transaction confirmed: ${tx.hash}, block: ${receipt.blockNumber}, gasUsed: ${receipt.gasUsed}`
        );

        await this._persistTxLog({
          operation: method,
          contractAddress: String(contract.target),
          idempotencyKey,
          method,
          args,
          txHash: tx.hash,
          status: 'CONFIRMED',
          attempt,
          receipt: {
            blockNumber: receipt.blockNumber,
            gasUsed: receipt.gasUsed?.toString?.() || String(receipt.gasUsed),
            status: receipt.status,
          },
        });

        return {
          success: true,
          txHash: tx.hash,
          receipt,
          blockNumber: receipt.blockNumber,
          gasUsed: receipt.gasUsed.toString(),
        };
      } catch (error) {
        lastError = error;
        logger.warn(`Transaction ${method} failed (attempt ${attempt}):`, error.message);

        // Handle nonce errors
        if (error.message.includes('nonce') || error.code === 'NONCE_EXPIRED') {
          this.resetNonce();
        }

        // Don't retry for certain errors
        if (
          error.message.includes('insufficient funds') ||
          error.message.includes('execution reverted')
        ) {
          break;
        }

        if (attempt < maxRetries) {
          await this._sleep(retryDelay * attempt);
        }

        await this._persistTxLog({
          operation: method,
          contractAddress: String(contract.target),
          idempotencyKey,
          method,
          args,
          status: 'FAILED',
          attempt,
          errorMessage: error.message,
        });
      }
    }

    return {
      success: false,
      error: lastError.message,
      reason: lastError.reason || 'Unknown error',
    };
  }

  /**
   * Call a read-only contract method
   */
  async callMethod(contract, method, args = []) {
    try {
      const result = await contract[method](...args);
      return { success: true, data: result };
    } catch (error) {
      logger.error(`Call to ${method} failed:`, error);
      return { success: false, error: error.message };
    }
  }

  /**
   * Get past events from a contract
   */
  async getPastEvents(contract, eventName, fromBlock, toBlock = 'latest') {
    try {
      const filter = contract.filters[eventName]();
      const events = await contract.queryFilter(filter, fromBlock, toBlock);
      return events.map((event) => ({
        eventName: event.eventName || eventName,
        args: event.args,
        transactionHash: event.transactionHash,
        blockNumber: event.blockNumber,
        logIndex: event.index,
      }));
    } catch (error) {
      logger.error(`Failed to get events ${eventName}:`, error);
      return [];
    }
  }

  /**
   * Subscribe to contract events
   */
  subscribeToEvent(contract, eventName, callback) {
    try {
      contract.on(eventName, (...args) => {
        const event = args[args.length - 1]; // Last arg is the event object
        callback({
          eventName,
          args: args.slice(0, -1),
          transactionHash: event.transactionHash,
          blockNumber: event.blockNumber,
        });
      });

      logger.info(`Subscribed to event: ${eventName}`);
      return true;
    } catch (error) {
      logger.error(`Failed to subscribe to ${eventName}:`, error);
      return false;
    }
  }

  /**
   * Unsubscribe from all events on a contract
   */
  unsubscribeAll(contract) {
    contract.removeAllListeners();
    logger.info('Unsubscribed from all contract events');
  }

  /**
   * Get connection status
   */
  getStatus() {
    return {
      connected: this.isConnected,
      rpcUrl: blockchainConfig.rpcUrl,
      chainId: blockchainConfig.chainId,
      walletAddress: this.wallet?.address || null,
      lastBlockNumber: this.lastBlockNumber,
      configuredContracts: Object.keys(this.contracts),
    };
  }

  /**
   * Helper sleep function
   */
  _sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async _persistTxLog({
    operation,
    contractAddress,
    idempotencyKey,
    method,
    args,
    txHash = null,
    status,
    attempt,
    receipt = null,
    errorMessage = null,
  }) {
    try {
      await TxLog.create({
        operation,
        contract_address: contractAddress,
        idempotency_key: idempotencyKey,
        method,
        args,
        tx_hash: txHash,
        status,
        attempt,
        receipt,
        error_message: errorMessage,
      });
    } catch (error) {
      logger.debug(`Unable to persist tx log for ${operation}: ${error.message}`);
    }
  }

  /**
   * Disconnect
   */
  async disconnect() {
    // Unsubscribe from all events
    for (const contract of Object.values(this.contracts)) {
      contract.removeAllListeners();
    }

    this.isConnected = false;
    logger.info('Web3 client disconnected');
  }
}

// Export singleton
const web3Client = new Web3Client();
module.exports = web3Client;
