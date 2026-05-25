/**
 * Receipt Service
 * Grid-Guardian - Delivery Receipt Management
 */

const blockchainService = require('./blockchainWeb3.service');
const commandPublisher = require('./commandPublisher.service');
const Receipt = require('../models/receipt.model');
const logger = require('../utils/logger');
const { createHash } = require('crypto');

const receiptService = {
  /**
   * Create a meter snapshot hash
   * keccak256(meter_reading || timestamp || nodeId)
   */
  createMeterSnapshotHash(meterReading, timestamp, nodeId) {
    const data = `${meterReading}:${timestamp}:${nodeId}`;
    return '0x' + createHash('sha256').update(data).digest('hex');
  },

  /**
   * Request Pi to submit delivery receipt
   */
  async requestDeliveryReceipt(tradeId, nodeId, periodStart, periodEnd) {
    try {
      const result = await commandPublisher.requestReceipt(nodeId, tradeId, periodStart, periodEnd);

      // Create pending receipt record
      if (result.success) {
        await Receipt.create({
          trade_id: tradeId,
          node_id: nodeId,
          period_start: new Date(periodStart),
          period_end: new Date(periodEnd),
          status: 'REQUESTED',
          command_id: result.commandId,
        });
      }

      return result;
    } catch (error) {
      logger.error('Error requesting delivery receipt:', error);
      return { success: false, error: error.message };
    }
  },

  /**
   * Process receipt submission from Pi
   * Called when Pi sends signed receipt via MQTT or API
   */
  async submitReceipt(receiptData) {
    const {
      tradeId,
      nodeId,
      meterReading,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      signature,
    } = receiptData;

    try {
      // Create meter snapshot hash
      const meterSnapshotHash = this.createMeterSnapshotHash(meterReading, periodEnd, nodeId);

      // Get current nonce
      const nonce = await blockchainService.getDeliveryNonce(nodeId);

      // Submit to blockchain
      const txResult = await blockchainService.submitReceipt(
        tradeId,
        nodeId,
        meterSnapshotHash,
        deliveredKwhBucket,
        periodStart,
        periodEnd,
        nonce,
        signature
      );

      if (txResult.success) {
        // Update or create receipt record
        await Receipt.upsert({
          trade_id: tradeId,
          node_id: nodeId,
          meter_snapshot_hash: meterSnapshotHash,
          meter_reading: meterReading,
          delivered_kwh_bucket: deliveredKwhBucket,
          period_start: new Date(periodStart),
          period_end: new Date(periodEnd),
          nonce,
          tx_hash: txResult.txHash,
          block_number: txResult.receipt?.blockNumber,
          status: 'SUBMITTED',
          submitted_at: new Date(),
        });

        logger.info(`Receipt submitted: ${tradeId} from ${nodeId}, tx: ${txResult.txHash}`);
        return { success: true, txHash: txResult.txHash, meterSnapshotHash };
      } else {
        // Record failed attempt
        await Receipt.upsert({
          trade_id: tradeId,
          node_id: nodeId,
          status: 'FAILED',
          error_message: txResult.error,
        });

        return { success: false, error: txResult.error };
      }
    } catch (error) {
      logger.error('Error submitting receipt:', error);
      return { success: false, error: error.message };
    }
  },

  /**
   * Verify receipt on-chain
   */
  async verifyReceipt(tradeId, nodeId) {
    try {
      const onChainReceipt = await blockchainService.getReceipt(tradeId, nodeId);

      if (onChainReceipt) {
        // Update local record to match on-chain
        await Receipt.markVerified(tradeId, nodeId, onChainReceipt);
        return { success: true, verified: true, receipt: onChainReceipt };
      }

      return { success: true, verified: false };
    } catch (error) {
      logger.error('Error verifying receipt:', error);
      return { success: false, error: error.message };
    }
  },

  /**
   * Get receipt for a trade-node pair
   */
  async getReceipt(tradeId, nodeId) {
    return await Receipt.findByTradeAndNode(tradeId, nodeId);
  },

  /**
   * Get all receipts for a trade
   */
  async getTradeReceipts(tradeId) {
    return await Receipt.findByTrade(tradeId);
  },

  /**
   * Get all receipts for a node
   */
  async getNodeReceipts(nodeId, limit = 50) {
    return await Receipt.findByNode(nodeId, limit);
  },

  /**
   * Check if trade has sufficient receipts
   */
  async hasRequiredReceipts(tradeId, requiredCount = 1) {
    const count = await Receipt.getSubmittedCount(tradeId);
    return count >= requiredCount;
  },

  /**
   * Get receipt stats
   */
  async getStats() {
    return await Receipt.getStats();
  },
};

module.exports = receiptService;
