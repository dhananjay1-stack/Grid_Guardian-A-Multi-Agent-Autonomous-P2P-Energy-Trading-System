/**
 * AI Trading Integration Service
 *
 * Connects AI decisions to blockchain trading actions.
 * When AI decides to SELL or BUY, this service:
 * 1. Validates the decision against confidence thresholds
 * 2. Creates a trade proposal with calculated quantities
 * 3. Submits commitment to blockchain (commit-reveal scheme)
 * 4. Notifies Pi devices via MQTT and dashboard via Socket.io
 * 5. Tracks trade lifecycle through settlement
 */
const logger = require('../utils/logger');
const aiConfig = require('../config/ai.config');
const aiService = require('./ai.service');
const { ethers } = require('ethers');
const EventEmitter = require('events');

class AITradingIntegration extends EventEmitter {
  constructor() {
    super();
    this.blockchainService = null;
    this.settlementService = null;
    this.commandPublisher = null;
    this.io = null;
    this.enabled = false;
    this.pendingTrades = new Map();
    this.tradeQueue = [];
    this.isProcessingQueue = false;
    this.stats = {
      totalDecisions: 0,
      tradesProposed: 0,
      tradesExecuted: 0,
      tradesFailed: 0,
    };
  }

  /**
   * Initialize the integration with required services
   */
  initialize(services) {
    this.blockchainService = services.blockchainService;
    this.settlementService = services.settlementService;
    this.commandPublisher = services.commandPublisher;
    this.io = services.io;
    this.enabled = true;

    // Start cleanup interval
    this._cleanupInterval = setInterval(() => {
      this.cleanupOldTrades();
    }, 5 * 60 * 1000); // Every 5 minutes

    logger.info('AI Trading Integration initialized');
  }

  /**
   * Shutdown the integration
   */
  shutdown() {
    if (this._cleanupInterval) {
      clearInterval(this._cleanupInterval);
    }
    this.enabled = false;
    logger.info('AI Trading Integration shutdown');
  }

  /**
   * Complete flow: Process telemetry, get AI decision, and execute trade if needed
   *
   * @param {string} nodeId - Node identifier
   * @param {object} telemetryData - Raw telemetry from node
   * @param {object} additionalData - Additional context (forecasts, prices, etc.)
   * @returns {object} Result including AI decision and any trade actions
   */
  async processAndTrade(nodeId, telemetryData, additionalData = {}) {
    this.stats.totalDecisions++;

    try {
      // Get AI decision from AI service
      const aiDecision = await aiService.processAndDecide(nodeId, telemetryData, additionalData);

      // Emit decision event
      this.emit('decision', {
        nodeId,
        decision: aiDecision,
        timestamp: Date.now(),
      });

      // Process for potential trade
      const tradeProposal = await this.processAIDecision(aiDecision);

      return {
        aiDecision,
        tradeProposal,
        tradeExecuted: !!tradeProposal,
      };
    } catch (error) {
      logger.error(`Process and trade error for ${nodeId}:`, error);
      throw error;
    }
  }

  /**
   * Process an AI decision and determine if trading action is needed
   */
  async processAIDecision(decision) {
    if (!this.enabled) {
      logger.warn('AI Trading Integration not enabled');
      return null;
    }

    const {
      node_id,
      decision: action,
      confidence,
      trade_action,
      recommended_quantity,
      forecasted_load,
      forecasted_solar,
      net_power_kw,
    } = decision;

    // Check if this decision requires a trade
    if (!trade_action) {
      logger.debug(`No trade action for ${node_id}: ${action}`);
      return null;
    }

    // Validate confidence threshold
    if (confidence < aiConfig.trading.minConfidence) {
      logger.info(
        `Trade decision for ${node_id} below confidence threshold: ${(confidence * 100).toFixed(1)}% < ${(aiConfig.trading.minConfidence * 100).toFixed(1)}%`
      );
      return null;
    }

    // Calculate trade quantity
    let quantity = recommended_quantity || 0;

    if (trade_action === 'SELL' && net_power_kw > aiConfig.trading.minNetPowerForSale) {
      // For SELL, use surplus energy (capped)
      quantity = Math.min(
        net_power_kw * aiConfig.trading.surplusSellFraction,
        aiConfig.trading.maxQuantity
      );
    } else if (trade_action === 'BUY') {
      // For BUY, calculate deficit
      const deficit = forecasted_load / 1000 - forecasted_solar / 1000;
      quantity = Math.min(Math.max(deficit, 0.1), aiConfig.trading.maxQuantity);
    }

    if (quantity <= 0.01) {
      logger.debug(`Insufficient quantity for trade: ${quantity} kWh`);
      return null;
    }

    // Create trade proposal
    const tradeProposal = {
      node_id,
      trade_type: trade_action,
      quantity_kwh: quantity,
      confidence,
      ai_decision: action,
      net_power_kw,
      forecasted_load,
      forecasted_solar,
      timestamp: Date.now(),
      status: 'pending',
    };

    logger.info(
      `AI Trade Proposal: ${node_id} ${trade_action} ${quantity.toFixed(3)} kWh ` +
        `(confidence: ${(confidence * 100).toFixed(1)}%)`
    );

    // Submit to blockchain if service available
    if (this.blockchainService) {
      try {
        const result = await this.submitTradeProposal(tradeProposal);
        tradeProposal.blockchain_result = result;
        tradeProposal.status = result.success ? 'submitted' : 'failed';
      } catch (error) {
        logger.error(`Failed to submit trade proposal: ${error.message}`);
        tradeProposal.status = 'failed';
        tradeProposal.error = error.message;
      }
    }

    // Track pending trade
    const tradeId = `${node_id}_${Date.now()}`;
    tradeProposal.trade_id = tradeId;
    this.pendingTrades.set(tradeId, tradeProposal);

    // Emit trade proposal event
    if (this.io && aiConfig.streaming.emitToSocketio) {
      this.io.to(`node:${node_id}`).emit('ai:trade_proposal', tradeProposal);
      this.io.emit('ai:trade_proposal', tradeProposal);
    }

    // Publish MQTT notification
    if (this.commandPublisher && aiConfig.streaming.publishToMqtt) {
      await this.notifyPi(node_id, tradeProposal);
    }

    return tradeProposal;
  }

  /**
   * Submit trade proposal to blockchain using commit-reveal scheme
   */
  async submitTradeProposal(proposal) {
    if (!this.blockchainService) {
      return { success: false, error: 'Blockchain service not available' };
    }

    try {
      // Generate commitment hash using ethers for proper hashing
      const salt = ethers.randomBytes(32);
      const commitData = {
        node_id: proposal.node_id,
        trade_type: proposal.trade_type,
        quantity: proposal.quantity_kwh,
        timestamp: proposal.timestamp,
      };

      // Create offer hash
      const offerBytes = ethers.toUtf8Bytes(JSON.stringify(commitData));
      const offerHash = ethers.keccak256(offerBytes);

      // Create commit hash (hash of offer hash + salt)
      const commitHash = ethers.keccak256(
        ethers.AbiCoder.defaultAbiCoder().encode(['bytes32', 'bytes32'], [offerHash, salt])
      );

      // Get current round (5-minute rounds)
      const roundId = Math.floor(Date.now() / (5 * 60 * 1000));
      const currentBlock = await this.blockchainService.getStatus();
      const expiryBlock = (currentBlock.blockNumber || 0) + 50; // ~12.5 minutes at 15s blocks

      // Generate signature (simplified - in production, use proper signing)
      const signatureData = ethers.AbiCoder.defaultAbiCoder().encode(
        ['bytes32', 'bytes32', 'uint256', 'string', 'uint256'],
        [commitHash, offerHash, roundId, proposal.node_id, expiryBlock]
      );
      const signature = ethers.keccak256(signatureData);

      // Post commitment to blockchain trading contract
      const result = await this.blockchainService.postCommit(
        commitHash,
        offerHash,
        roundId,
        proposal.node_id,
        expiryBlock,
        signature
      );

      // Store for later reveal
      this._storeForReveal(commitHash, {
        offerHash,
        salt: ethers.hexlify(salt),
        proposal,
        roundId,
      });

      this.stats.tradesProposed++;

      return {
        success: result.success,
        tx_hash: result.txHash,
        commit_hash: commitHash,
        offer_hash: offerHash,
        round_id: roundId,
        expiry_block: expiryBlock,
      };
    } catch (error) {
      logger.error(`Blockchain submission failed: ${error.message}`);
      this.stats.tradesFailed++;
      return {
        success: false,
        error: error.message,
      };
    }
  }

  // Store for reveal phase
  _pendingReveals = new Map();

  _storeForReveal(commitHash, data) {
    this._pendingReveals.set(commitHash, data);

    // Auto-cleanup after 15 minutes (2 rounds)
    setTimeout(() => {
      this._pendingReveals.delete(commitHash);
    }, 15 * 60 * 1000);
  }

  /**
   * Reveal a committed offer
   */
  async revealOffer(commitHash) {
    const revealData = this._pendingReveals.get(commitHash);

    if (!revealData) {
      throw new Error('Commit hash not found in pending reveals');
    }

    if (!this.blockchainService) {
      throw new Error('Blockchain service not available');
    }

    const result = await this.blockchainService.revealOffer(
      commitHash,
      revealData.offerHash,
      revealData.salt,
      revealData.proposal.node_id
    );

    if (result.success) {
      this._pendingReveals.delete(commitHash);
    }

    return result;
  }

  /**
   * Notify Pi device about trade decision
   */
  async notifyPi(nodeId, proposal) {
    if (!this.commandPublisher) {
      return;
    }

    const command = {
      command_type: proposal.trade_type === 'SELL' ? 'prepare_sell' : 'prepare_buy',
      trade_id: proposal.trade_id,
      quantity_kwh: proposal.quantity_kwh,
      confidence: proposal.confidence,
      timestamp: proposal.timestamp,
      action_required: 'await_confirmation',
    };

    try {
      await this.commandPublisher.publishCommand(nodeId, command);
      logger.debug(`Pi notification sent for ${nodeId}: ${command.command_type}`);
    } catch (error) {
      logger.error(`Failed to notify Pi ${nodeId}: ${error.message}`);
    }
  }

  /**
   * Handle trade execution confirmation
   */
  async onTradeExecuted(tradeId, executionData) {
    const trade = this.pendingTrades.get(tradeId);

    if (!trade) {
      logger.warn(`Trade ${tradeId} not found in pending trades`);
      return;
    }

    trade.status = 'executed';
    trade.execution_data = executionData;
    trade.executed_at = Date.now();

    // Emit execution event
    if (this.io) {
      this.io.to(`node:${trade.node_id}`).emit('ai:trade_executed', trade);
      this.io.emit('ai:trade_executed', trade);
    }

    // Move to settlement
    if (this.settlementService) {
      await this.settlementService.initiateSettlement(trade);
    }

    logger.info(`Trade ${tradeId} executed successfully`);
  }

  /**
   * Handle trade cancellation
   */
  async onTradeCancelled(tradeId, reason) {
    const trade = this.pendingTrades.get(tradeId);

    if (trade) {
      trade.status = 'cancelled';
      trade.cancellation_reason = reason;
      trade.cancelled_at = Date.now();

      if (this.io) {
        this.io.to(`node:${trade.node_id}`).emit('ai:trade_cancelled', trade);
      }

      this.pendingTrades.delete(tradeId);
    }

    logger.info(`Trade ${tradeId} cancelled: ${reason}`);
  }

  /**
   * Get pending trades for a node
   */
  getPendingTrades(nodeId = null) {
    const trades = Array.from(this.pendingTrades.values());

    if (nodeId) {
      return trades.filter((t) => t.node_id === nodeId);
    }

    return trades;
  }

  /**
   * Get trade statistics
   */
  getTradeStats() {
    const trades = Array.from(this.pendingTrades.values());

    return {
      // Overall stats
      total_decisions: this.stats.totalDecisions,
      trades_proposed: this.stats.tradesProposed,
      trades_executed: this.stats.tradesExecuted,
      trades_failed: this.stats.tradesFailed,
      // Current pending trades
      total_pending: trades.filter((t) => t.status === 'pending').length,
      total_submitted: trades.filter((t) => t.status === 'submitted').length,
      total_executed: trades.filter((t) => t.status === 'executed').length,
      total_failed: trades.filter((t) => t.status === 'failed').length,
      total_cancelled: trades.filter((t) => t.status === 'cancelled').length,
      sell_proposals: trades.filter((t) => t.trade_type === 'SELL').length,
      buy_proposals: trades.filter((t) => t.trade_type === 'BUY').length,
      pending_reveals: this._pendingReveals.size,
      enabled: this.enabled,
    };
  }

  /**
   * Get detailed status
   */
  getStatus() {
    return {
      enabled: this.enabled,
      hasBlockchainService: !!this.blockchainService,
      hasSettlementService: !!this.settlementService,
      hasCommandPublisher: !!this.commandPublisher,
      hasSocketIO: !!this.io,
      stats: this.getTradeStats(),
    };
  }

  /**
   * Cleanup old pending trades
   */
  cleanupOldTrades(maxAgeMs = 3600000) {
    const now = Date.now();
    let cleaned = 0;

    for (const [tradeId, trade] of this.pendingTrades.entries()) {
      if (now - trade.timestamp > maxAgeMs) {
        this.pendingTrades.delete(tradeId);
        cleaned++;
      }
    }

    if (cleaned > 0) {
      logger.debug(`Cleaned up ${cleaned} old pending trades`);
    }

    return cleaned;
  }
}

// Singleton instance
const aiTradingIntegration = new AITradingIntegration();

module.exports = aiTradingIntegration;
