/**
 * Settlement Service
 * Grid-Guardian - Trade Settlement Lifecycle Management
 */

const blockchainService = require('./blockchainWeb3.service');
const eventListenerService = require('./eventListener.service');
const commandPublisher = require('./commandPublisher.service');
const Trade = require('../models/trade.model');
const SettlementRecord = require('../models/settlement.model');
const logger = require('../utils/logger');
const { v4: uuidv4 } = require('uuid');
const { ethers } = require('ethers');

const settlementService = {
  /**
   * Initialize settlement service with event handlers
   */
  initialize(io) {
    this.io = io;

    // Listen to blockchain events
    eventListenerService.on('trade:executed', (event) => this._handleTradeExecuted(event));
    eventListenerService.on('delivery:marked', (event) => this._handleDeliveryMarked(event));
    eventListenerService.on('settlement:completed', (event) => this._handleSettlementCompleted(event));
    eventListenerService.on('receipt:submitted', (event) => this._handleReceiptSubmitted(event));
    eventListenerService.on('delivery:confirmed', (event) => this._handleDeliveryConfirmed(event));

    logger.info('Settlement service initialized with event handlers');
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      SETTLEMENT FLOW
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Initiate a new trade settlement flow
   * 1. Lock buyer funds
   * 2. Propose trade on-chain
   * 3. Notify seller Pi
   * 4. Wait for delivery
   */
  async initiateTrade(tradeParams) {
    const {
      buyerNodeId,
      sellerNodeId,
      kwhBucket,
      priceBucket,
      amount,
      matchHash,
    } = tradeParams;

    const tradeId = ethers.keccak256(
      ethers.solidityPacked(
        ['bytes32', 'bytes32', 'bytes32', 'uint16', 'uint16', 'uint256'],
        [matchHash || ethers.ZeroHash, buyerNodeId, sellerNodeId, kwhBucket, priceBucket, Date.now()]
      )
    );

    try {
      // 1. Create local trade record
      const localTrade = await Trade.create({
        trade_id: tradeId.slice(0, 18).toUpperCase().replace('0X', 'TRADE-'),
        node_id: buyerNodeId,
        trade_type: 'BUY',
        energy_amount: kwhBucket * 0.1, // Convert bucket to kWh
        price_per_unit: priceBucket * 0.01,
        total_price: (kwhBucket * 0.1) * (priceBucket * 0.01),
        status: 'PENDING',
        counterparty_id: sellerNodeId,
      });

      // 2. Create settlement record
      const settlement = await SettlementRecord.create({
        trade_id: tradeId,
        local_trade_id: localTrade.trade_id,
        match_hash: matchHash,
        buyer_node_id: buyerNodeId,
        seller_node_id: sellerNodeId,
        kwh_bucket: kwhBucket,
        price_bucket: priceBucket,
        locked_amount: amount.toString(),
        status: 'INITIATED',
      });

      // 3. Propose trade on-chain (this will lock funds)
      const txResult = await blockchainService.proposeTrade(
        tradeId,
        matchHash || ethers.ZeroHash,
        buyerNodeId,
        sellerNodeId,
        kwhBucket,
        priceBucket,
        amount
      );

      if (txResult.success) {
        // Update settlement record
        await SettlementRecord.updateStatus(tradeId, 'LOCKED', txResult.txHash);
        await Trade.updateStatus(localTrade.trade_id, 'MATCHED', txResult.txHash);

        // 4. Notify seller Pi to deliver energy
        await commandPublisher.sendCommand(sellerNodeId, 'execute_trade', {
          trade_id: tradeId,
          local_trade_id: localTrade.trade_id,
          buyer_node_id: buyerNodeId,
          kwh_bucket: kwhBucket,
          action: 'discharge',
          timestamp: Date.now(),
        });

        // Notify dashboard
        this._emitToDashboard('trade:initiated', {
          tradeId,
          localTradeId: localTrade.trade_id,
          buyerNodeId,
          sellerNodeId,
          txHash: txResult.txHash,
        });

        logger.info(`Trade initiated: ${tradeId}`);
        return { success: true, tradeId, localTradeId: localTrade.trade_id, txHash: txResult.txHash };
      } else {
        // Rollback on failure
        await Trade.updateStatus(localTrade.trade_id, 'FAILED');
        await SettlementRecord.updateStatus(tradeId, 'FAILED', null, txResult.error);

        return { success: false, error: txResult.error };
      }
    } catch (error) {
      logger.error('Error initiating trade:', error);
      return { success: false, error: error.message };
    }
  },

  /**
   * Process delivery receipt from Pi
   * Called when Pi sends receipt via MQTT or API
   */
  async processDeliveryReceipt(receiptData) {
    const {
      tradeId,
      nodeId,
      meterSnapshotHash,
      deliveredKwhBucket,
      periodStart,
      periodEnd,
      signature,
    } = receiptData;

    try {
      // Get delivery nonce
      const nonce = await blockchainService.getDeliveryNonce(nodeId);

      // Submit receipt to blockchain
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
        // Update settlement
        const settlement = await SettlementRecord.findByTradeId(tradeId);
        if (settlement) {
          await SettlementRecord.addReceipt(tradeId, nodeId, {
            meterSnapshotHash,
            deliveredKwhBucket,
            periodStart,
            periodEnd,
            txHash: txResult.txHash,
          });

          // Notify dashboard
          this._emitToDashboard('receipt:submitted', {
            tradeId,
            nodeId,
            txHash: txResult.txHash,
          });
        }

        logger.info(`Delivery receipt submitted: ${tradeId} from ${nodeId}`);
        return { success: true, txHash: txResult.txHash };
      } else {
        return { success: false, error: txResult.error };
      }
    } catch (error) {
      logger.error('Error processing delivery receipt:', error);
      return { success: false, error: error.message };
    }
  },

  /**
   * Finalize settlement (called after dispute window)
   */
  async finalizeSettlement(tradeId) {
    try {
      // Execute settlement on-chain
      const txResult = await blockchainService.executeSettlement(tradeId);

      if (txResult.success) {
        // Update records
        await SettlementRecord.updateStatus(tradeId, 'SETTLED', txResult.txHash);

        // Find and update local trade
        const settlement = await SettlementRecord.findByTradeId(tradeId);
        if (settlement && settlement.local_trade_id) {
          await Trade.updateStatus(settlement.local_trade_id, 'SETTLED', txResult.txHash);
        }

        // Notify buyer and seller
        if (settlement) {
          await commandPublisher.sendCommand(settlement.buyer_node_id, 'settlement_complete', {
            trade_id: tradeId,
            status: 'SETTLED',
          });
          await commandPublisher.sendCommand(settlement.seller_node_id, 'settlement_complete', {
            trade_id: tradeId,
            status: 'SETTLED',
          });
        }

        // Notify dashboard
        this._emitToDashboard('settlement:completed', {
          tradeId,
          txHash: txResult.txHash,
        });

        logger.info(`Settlement finalized: ${tradeId}`);
        return { success: true, txHash: txResult.txHash };
      } else {
        return { success: false, error: txResult.error };
      }
    } catch (error) {
      logger.error('Error finalizing settlement:', error);
      return { success: false, error: error.message };
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      EVENT HANDLERS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Handle TradeExecuted event from blockchain
   */
  async _handleTradeExecuted(event) {
    logger.info(`TradeExecuted event: ${event.tradeId}`);

    try {
      // Update settlement status
      await SettlementRecord.updateStatus(event.tradeId, 'EXECUTED');

      // Notify seller Pi to start delivery
      await commandPublisher.sendCommand(event.sellerNodeId, 'execute_trade', {
        trade_id: event.tradeId,
        buyer_node_id: event.buyerNodeId,
        kwh_bucket: event.kwhBucket,
        action: 'discharge',
        timestamp: Date.now(),
      });

      // Also notify buyer Pi to prepare for receiving
      await commandPublisher.sendCommand(event.buyerNodeId, 'execute_trade', {
        trade_id: event.tradeId,
        seller_node_id: event.sellerNodeId,
        kwh_bucket: event.kwhBucket,
        action: 'charge',
        timestamp: Date.now(),
      });

      // Emit to dashboard
      this._emitToDashboard('trade:executed', event);
    } catch (error) {
      logger.error('Error handling TradeExecuted:', error);
    }
  },

  /**
   * Handle DeliveryMarked event
   */
  async _handleDeliveryMarked(event) {
    logger.info(`DeliveryMarked event: ${event.tradeId}`);

    try {
      await SettlementRecord.updateStatus(event.tradeId, 'DELIVERED');
      this._emitToDashboard('delivery:marked', event);
    } catch (error) {
      logger.error('Error handling DeliveryMarked:', error);
    }
  },

  /**
   * Handle SettlementCompleted event
   */
  async _handleSettlementCompleted(event) {
    logger.info(`SettlementCompleted event: ${event.tradeId}`);

    try {
      await SettlementRecord.updateStatus(event.tradeId, 'SETTLED');

      const settlement = await SettlementRecord.findByTradeId(event.tradeId);
      if (settlement && settlement.local_trade_id) {
        await Trade.updateStatus(settlement.local_trade_id, 'SETTLED');
      }

      this._emitToDashboard('settlement:completed', event);
    } catch (error) {
      logger.error('Error handling SettlementCompleted:', error);
    }
  },

  /**
   * Handle DeliveryReceiptSubmitted event
   */
  async _handleReceiptSubmitted(event) {
    logger.info(`ReceiptSubmitted event: ${event.tradeId} from ${event.nodeId}`);

    try {
      await SettlementRecord.addReceipt(event.tradeId, event.nodeId, {
        meterSnapshotHash: event.meterSnapshotHash,
        deliveredKwhBucket: event.deliveredKwhBucket,
        submittedBlock: event.submittedBlock,
      });

      this._emitToDashboard('receipt:submitted', event);
    } catch (error) {
      logger.error('Error handling ReceiptSubmitted:', error);
    }
  },

  /**
   * Handle DeliveryConfirmed event (when sufficient receipts received)
   */
  async _handleDeliveryConfirmed(event) {
    logger.info(`DeliveryConfirmed event: ${event.args?.[0]}`);

    try {
      const tradeId = event.args?.[0];
      if (tradeId) {
        await SettlementRecord.updateStatus(tradeId, 'DELIVERY_CONFIRMED');

        // Notify both parties
        const settlement = await SettlementRecord.findByTradeId(tradeId);
        if (settlement) {
          await commandPublisher.sendCommand(settlement.buyer_node_id, 'delivery_confirmed', {
            trade_id: tradeId,
          });
          await commandPublisher.sendCommand(settlement.seller_node_id, 'delivery_confirmed', {
            trade_id: tradeId,
          });
        }
      }

      this._emitToDashboard('delivery:confirmed', event);
    } catch (error) {
      logger.error('Error handling DeliveryConfirmed:', error);
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      QUERIES
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Get settlement by trade ID
   */
  async getSettlement(tradeId) {
    return await SettlementRecord.findByTradeId(tradeId);
  },

  /**
   * Get settlements by node
   */
  async getNodeSettlements(nodeId, limit = 50) {
    return await SettlementRecord.findByNode(nodeId, limit);
  },

  /**
   * Get pending settlements
   */
  async getPendingSettlements() {
    return await SettlementRecord.findPending();
  },

  /**
   * Get settlement stats
   */
  async getStats() {
    return await SettlementRecord.getStats();
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      HELPERS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Emit event to dashboard via Socket.io
   */
  _emitToDashboard(event, data) {
    if (this.io) {
      this.io.emit(`blockchain:${event}`, {
        ...data,
        timestamp: Date.now(),
      });
    }
  },
};

module.exports = settlementService;
