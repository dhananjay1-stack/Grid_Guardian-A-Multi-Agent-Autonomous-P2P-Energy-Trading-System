const BlockchainLog = require('../models/blockchainLog.model');
const Trade = require('../models/trade.model');
const logger = require('../utils/logger');
const { v4: uuidv4 } = require('uuid');

const blockchainService = {
  /**
   * Log a blockchain event
   */
  async logEvent(nodeId, eventType, payload = {}, txHash = null) {
    try {
      const log = new BlockchainLog({
        node_id: nodeId,
        event_type: eventType,
        tx_hash: txHash,
        payload,
        status: txHash ? 'CONFIRMED' : 'PENDING',
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
   * Create a trade record (placeholder for actual blockchain submission)
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
   * Confirm a trade (mock blockchain confirmation)
   */
  async confirmTrade(tradeId) {
    try {
      // Generate mock transaction hash
      const txHash = `0x${uuidv4().replace(/-/g, '')}`;

      const trade = await Trade.updateStatus(tradeId, 'CONFIRMED', txHash);

      if (trade) {
        await this.logEvent(trade.node_id, 'TRADE_CONFIRMED', { trade_id: tradeId }, txHash);
        logger.info(`Trade confirmed: ${tradeId}, tx: ${txHash}`);
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
};

module.exports = blockchainService;
