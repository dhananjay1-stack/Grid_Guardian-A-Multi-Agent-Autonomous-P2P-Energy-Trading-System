/**
 * Enhanced Blockchain Service
 * Grid-Guardian - Smart Contract Interaction Layer
 */

const { ethers } = require('ethers');
const web3Client = require('../utils/web3Client');
const blockchainConfig = require('../config/blockchain.config');
const { withRetry, createIdempotencyKey, CircuitBreaker } = require('../utils/retryHelper');
const BlockchainLog = require('../models/blockchainLog.model');
const Trade = require('../models/trade.model');
const logger = require('../utils/logger');
const { v4: uuidv4 } = require('uuid');

// Circuit breaker for blockchain calls
const txCircuitBreaker = new CircuitBreaker({
  failureThreshold: 5,
  resetTimeoutMs: 60000,
});

const blockchainService = {
  /**
   * Initialize blockchain service
   */
  async initialize() {
    try {
      await web3Client.initialize();
      logger.info('Blockchain service initialized');
      return true;
    } catch (error) {
      logger.error('Failed to initialize blockchain service:', error);
      return false;
    }
  },

  /**
   * Shutdown blockchain resources
   */
  async shutdown() {
    try {
      await web3Client.disconnect();
    } catch (error) {
      logger.warn(`Blockchain shutdown warning: ${error.message}`);
    }
  },

  /**
   * Get blockchain status
   */
  async getStatus() {
    try {
      const blockNumber = await web3Client.getBlockNumber();
      const status = web3Client.getStatus();

      return {
        connected: status.connected,
        blockNumber,
        chainId: status.chainId,
        walletAddress: status.walletAddress,
        contracts: status.configuredContracts,
        circuitBreaker: txCircuitBreaker.getState(),
      };
    } catch (error) {
      logger.error('Error getting blockchain status:', error);
      return {
        connected: false,
        error: error.message,
      };
    }
  },

  /**
   * Health check
   */
  async healthCheck() {
    try {
      const blockNumber = await web3Client.getBlockNumber();
      return {
        healthy: true,
        blockNumber,
        timestamp: Date.now(),
      };
    } catch (error) {
      return {
        healthy: false,
        error: error.message,
        timestamp: Date.now(),
      };
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      IDENTITY OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Register a node on-chain
   */
  async registerNode(nodeId, pubkeyHash, metaURI) {
    if (!web3Client.hasContract('identity')) {
      throw new Error('Identity contract not configured');
    }

    const contract = web3Client.getContract('identity');
    const idempotencyKey = createIdempotencyKey('registerNode', nodeId);

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'registerNode', [
        nodeId,
        pubkeyHash,
        metaURI,
      ]);

      if (result.success) {
        await this.logEvent(nodeId, 'NODE_REGISTERED', { pubkeyHash, metaURI }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Register node via meta-transaction (relayer pays gas)
   */
  async registerNodeMeta(nodeId, pubkeyHash, metaURI, nonce, expiry, signature) {
    if (!web3Client.hasContract('identity')) {
      throw new Error('Identity contract not configured');
    }

    const contract = web3Client.getContract('identity');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'registerNodeMeta', [
        nodeId,
        pubkeyHash,
        metaURI,
        nonce,
        expiry,
        signature,
      ]);

      if (result.success) {
        await this.logEvent(nodeId, 'NODE_REGISTERED_META', { pubkeyHash, metaURI }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Check if node is registered
   */
  async isNodeRegistered(nodeId) {
    if (!web3Client.hasContract('identity')) {
      return { registered: false, error: 'Contract not configured' };
    }

    const contract = web3Client.getContract('identity');
    const result = await web3Client.callMethod(contract, 'isRegistered', [nodeId]);

    return { registered: result.data || false };
  },

  /**
   * Get node details
   */
  async getNode(nodeId) {
    if (!web3Client.hasContract('identity')) {
      return null;
    }

    const contract = web3Client.getContract('identity');
    const result = await web3Client.callMethod(contract, 'getNode', [nodeId]);

    if (result.success && result.data) {
      const [owner, pubkeyHash, metaURI, stake, registeredAt, active, attested] = result.data;
      return {
        nodeId,
        owner,
        pubkeyHash,
        metaURI,
        stake: stake.toString(),
        registeredAt: Number(registeredAt),
        active,
        attested,
      };
    }

    return null;
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      TRADING OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Post commit (commit-reveal scheme)
   */
  async postCommit(commitHash, offerHash, roundId, nodeId, expiryBlock, signature) {
    if (!web3Client.hasContract('trading')) {
      throw new Error('Trading contract not configured');
    }

    const contract = web3Client.getContract('trading');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'postCommit', [
        commitHash,
        offerHash,
        roundId,
        nodeId,
        expiryBlock,
        signature,
      ]);

      if (result.success) {
        await this.logEvent(nodeId, 'COMMIT_POSTED', {
          commitHash,
          offerHash,
          roundId,
          expiryBlock,
        }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Reveal offer
   */
  async revealOffer(commitHash, offerHash, salt, nodeId) {
    if (!web3Client.hasContract('trading')) {
      throw new Error('Trading contract not configured');
    }

    const contract = web3Client.getContract('trading');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'revealOffer', [
        commitHash,
        offerHash,
        salt,
      ]);

      if (result.success) {
        await this.logEvent(nodeId, 'OFFER_REVEALED', { commitHash, offerHash }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Get commit details
   */
  async getCommit(commitHash) {
    if (!web3Client.hasContract('trading')) {
      return null;
    }

    const contract = web3Client.getContract('trading');
    const result = await web3Client.callMethod(contract, 'getCommit', [commitHash]);

    if (result.success && result.data) {
      const [owner, roundId, expiryBlock, revealed] = result.data;
      return {
        commitHash,
        owner,
        roundId: Number(roundId),
        expiryBlock: Number(expiryBlock),
        revealed,
      };
    }

    return null;
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      COLLATERAL OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Get node deposit balance
   */
  async getDeposit(nodeId) {
    if (!web3Client.hasContract('collateral')) {
      return null;
    }

    const contract = web3Client.getContract('collateral');
    const result = await web3Client.callMethod(contract, 'getDeposit', [nodeId]);

    return result.success ? result.data.toString() : null;
  },

  /**
   * Lock funds for a trade
   */
  async lockFunds(nodeId, amount, tradeId) {
    if (!web3Client.hasContract('collateral')) {
      throw new Error('Collateral contract not configured');
    }

    const contract = web3Client.getContract('collateral');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'lockFunds', [
        nodeId,
        amount,
        tradeId,
      ]);

      if (result.success) {
        await this.logEvent(nodeId, 'FUNDS_LOCKED', { tradeId, amount: amount.toString() }, result.txHash);
      }

      return result;
    });
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      MATCH REGISTRY OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Publish match result
   */
  async publishMatch(matchHash, inputsHash, roundId, optimizerId, sigTimestamp, signatures) {
    if (!web3Client.hasContract('matchRegistry')) {
      throw new Error('MatchRegistry contract not configured');
    }

    const contract = web3Client.getContract('matchRegistry');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'publishMatch', [
        matchHash,
        inputsHash,
        roundId,
        optimizerId,
        sigTimestamp,
        signatures,
      ]);

      if (result.success) {
        await this.logEvent(null, 'MATCH_PUBLISHED', {
          matchHash,
          inputsHash,
          roundId,
          optimizerId,
        }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Finalize match
   */
  async finalizeMatch(matchHash, matchBlob) {
    if (!web3Client.hasContract('matchRegistry')) {
      throw new Error('MatchRegistry contract not configured');
    }

    const contract = web3Client.getContract('matchRegistry');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'finalizeMatch', [
        matchHash,
        matchBlob,
      ]);

      if (result.success) {
        await this.logEvent(null, 'MATCH_FINALIZED', { matchHash }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Get match details
   */
  async getMatch(matchHash) {
    if (!web3Client.hasContract('matchRegistry')) {
      return null;
    }

    const contract = web3Client.getContract('matchRegistry');
    const result = await web3Client.callMethod(contract, 'getMatch', [matchHash]);

    if (result.success && result.data) {
      const [publisher, inputsHash, publishedBlock, roundId, publishNonce, status, validSigners] = result.data;
      return {
        matchHash,
        publisher,
        inputsHash,
        publishedBlock: Number(publishedBlock),
        roundId: Number(roundId),
        publishNonce: Number(publishNonce),
        status: Number(status),
        validSigners: Number(validSigners),
      };
    }

    return null;
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      SETTLEMENT OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Propose trade (called after match finalization)
   */
  async proposeTrade(tradeId, matchHash, buyerNodeId, sellerNodeId, kwhBucket, priceBucket, amount) {
    if (!web3Client.hasContract('settlement')) {
      throw new Error('Settlement contract not configured');
    }

    const contract = web3Client.getContract('settlement');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'proposeTrade', [
        tradeId,
        matchHash,
        buyerNodeId,
        sellerNodeId,
        kwhBucket,
        priceBucket,
        amount,
      ]);

      if (result.success) {
        await this.logEvent(buyerNodeId, 'TRADE_PROPOSED', {
          tradeId,
          matchHash,
          sellerNodeId,
          kwhBucket,
          priceBucket,
          amount: amount.toString(),
        }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Execute settlement (after delivery and dispute window)
   */
  async executeSettlement(tradeId) {
    if (!web3Client.hasContract('settlement')) {
      throw new Error('Settlement contract not configured');
    }

    const contract = web3Client.getContract('settlement');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'executeSettlement', [tradeId]);

      if (result.success) {
        await this.logEvent(null, 'SETTLEMENT_EXECUTED', { tradeId }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Raise trade dispute
   */
  async raiseDispute(tradeId) {
    if (!web3Client.hasContract('settlement')) {
      throw new Error('Settlement contract not configured');
    }

    const contract = web3Client.getContract('settlement');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'disputeTrade', [tradeId]);

      if (result.success) {
        await this.logEvent(null, 'DISPUTE_RAISED', { tradeId }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Get trade from settlement contract
   */
  async getTradeFromChain(tradeId) {
    if (!web3Client.hasContract('settlement')) {
      return null;
    }

    const contract = web3Client.getContract('settlement');
    const result = await web3Client.callMethod(contract, 'getTrade', [tradeId]);

    if (result.success && result.data) {
      return {
        tradeId,
        matchHash: result.data.matchHash,
        buyerNodeId: result.data.buyerNodeId,
        sellerNodeId: result.data.sellerNodeId,
        kwhBucket: Number(result.data.kwhBucket),
        priceBucket: Number(result.data.priceBucket),
        lockedAmount: result.data.lockedAmount.toString(),
        proposedBlock: Number(result.data.proposedBlock),
        deliveredBlock: Number(result.data.deliveredBlock),
        status: Number(result.data.status),
      };
    }

    return null;
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      DELIVERY REGISTRY OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Submit delivery receipt (called by relayer on behalf of Pi)
   */
  async submitReceipt(tradeId, nodeId, meterSnapshotHash, deliveredKwhBucket, periodStart, periodEnd, nonce, signature) {
    if (!web3Client.hasContract('deliveryRegistry')) {
      throw new Error('DeliveryRegistry contract not configured');
    }

    const contract = web3Client.getContract('deliveryRegistry');

    return txCircuitBreaker.execute(async () => {
      const result = await web3Client.sendTransaction(contract, 'submitReceipt', [
        tradeId,
        nodeId,
        meterSnapshotHash,
        deliveredKwhBucket,
        periodStart,
        periodEnd,
        nonce,
        signature,
      ]);

      if (result.success) {
        await this.logEvent(nodeId, 'DELIVERY_RECEIPT_SUBMITTED', {
          tradeId,
          meterSnapshotHash,
          deliveredKwhBucket,
        }, result.txHash);
      }

      return result;
    });
  },

  /**
   * Get delivery receipt
   */
  async getReceipt(tradeId, nodeId) {
    if (!web3Client.hasContract('deliveryRegistry')) {
      return null;
    }

    const contract = web3Client.getContract('deliveryRegistry');
    const result = await web3Client.callMethod(contract, 'getReceipt', [tradeId, nodeId]);

    if (result.success && result.data && result.data.exists) {
      return {
        tradeId,
        nodeId: result.data.nodeId,
        meterSnapshotHash: result.data.meterSnapshotHash,
        deliveredKwhBucket: Number(result.data.deliveredKwhBucket),
        periodStart: Number(result.data.periodStart),
        periodEnd: Number(result.data.periodEnd),
        submittedBlock: Number(result.data.submittedBlock),
      };
    }

    return null;
  },

  /**
   * Get delivery nonce for a node
   */
  async getDeliveryNonce(nodeId) {
    if (!web3Client.hasContract('deliveryRegistry')) {
      return 0;
    }

    const contract = web3Client.getContract('deliveryRegistry');
    const result = await web3Client.callMethod(contract, 'getDeliveryNonce', [nodeId]);

    return result.success ? Number(result.data) : 0;
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      LOGGING AND TRADE MANAGEMENT
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Log a blockchain event
   */
  async logEvent(nodeId, eventType, payload = {}, txHash = null, blockNumber = null, gasUsed = 0) {
    try {
      const log = new BlockchainLog({
        node_id: nodeId || 'SYSTEM',
        event_type: eventType,
        tx_hash: txHash,
        block_number: blockNumber,
        payload,
        status: txHash ? 'CONFIRMED' : 'PENDING',
        gas_used: gasUsed,
      });

      const saved = await log.save();
      logger.debug(`Blockchain event logged: ${eventType} for node ${nodeId}`);

      return saved;
    } catch (error) {
      logger.error('Error logging blockchain event:', error);
      throw error;
    }
  },

  /**
   * Prepare trade payload for blockchain submission
   */
  prepareTradePayload(aiDecision, telemetryData, pricePerUnit = 0.15) {
    const energyAmount = (telemetryData.power / 1000) * 0.25; // Convert to kWh for 15-min interval
    const totalPrice = energyAmount * pricePerUnit;

    return {
      trade_id: `TRADE-${uuidv4().substr(0, 8).toUpperCase()}`,
      node_id: telemetryData.node_id,
      trade_type: aiDecision.decision,
      energy_amount: parseFloat(energyAmount.toFixed(4)),
      price_per_unit: pricePerUnit,
      total_price: parseFloat(totalPrice.toFixed(4)),
      timestamp: Date.now(),
      ai_confidence: aiDecision.confidence,
    };
  },

  /**
   * Create a trade record
   */
  async createTrade(tradePayload) {
    try {
      // Save to PostgreSQL
      const trade = await Trade.create({
        trade_id: tradePayload.trade_id,
        node_id: tradePayload.node_id,
        trade_type: tradePayload.trade_type,
        energy_amount: tradePayload.energy_amount,
        price_per_unit: tradePayload.price_per_unit,
        total_price: tradePayload.total_price,
        status: 'PENDING',
      });

      // Log the trade initiation
      await this.logEvent(tradePayload.node_id, 'TRADE_INITIATED', tradePayload);

      logger.info(`Trade created: ${tradePayload.trade_id}`);
      return trade;
    } catch (error) {
      logger.error('Error creating trade:', error);
      throw error;
    }
  },

  /**
   * Confirm a trade with blockchain tx
   */
  async confirmTrade(tradeId, txHash = null) {
    try {
      // Generate tx hash if not provided
      const hash = txHash || `0x${uuidv4().replace(/-/g, '')}`;

      const trade = await Trade.updateStatus(tradeId, 'CONFIRMED', hash);

      if (trade) {
        await this.logEvent(trade.node_id, 'TRADE_CONFIRMED', { trade_id: tradeId }, hash);
        logger.info(`Trade confirmed: ${tradeId}, tx: ${hash}`);
      }

      return trade;
    } catch (error) {
      logger.error('Error confirming trade:', error);
      throw error;
    }
  },

  /**
   * Get recent blockchain logs for a node
   */
  async getRecentLogs(nodeId, limit = 50) {
    try {
      return await BlockchainLog.getRecentLogs(nodeId, limit);
    } catch (error) {
      logger.error(`Error getting blockchain logs for ${nodeId}:`, error);
      throw error;
    }
  },

  /**
   * Get trades for a node
   */
  async getNodeTrades(nodeId, limit = 50) {
    try {
      return await Trade.findByNodeId(nodeId, limit);
    } catch (error) {
      logger.error(`Error getting trades for ${nodeId}:`, error);
      throw error;
    }
  },

  /**
   * Get all trades
   */
  async getAllTrades(limit = 50) {
    try {
      return await Trade.findAll(limit);
    } catch (error) {
      logger.error('Error getting all trades:', error);
      throw error;
    }
  },

  /**
   * Get active trades
   */
  async getActiveTrades() {
    try {
      return await Trade.findActive();
    } catch (error) {
      logger.error('Error getting active trades:', error);
      throw error;
    }
  },

  /**
   * Get trade by ID
   */
  async getTradeById(tradeId) {
    try {
      return await Trade.findByTradeId(tradeId);
    } catch (error) {
      logger.error('Error getting trade:', error);
      throw error;
    }
  },
};

module.exports = blockchainService;
