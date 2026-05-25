/**
 * Blockchain Controller
 * Grid-Guardian - API Endpoints for Blockchain Operations
 */

const blockchainService = require('../services/blockchainWeb3.service');
const settlementService = require('../services/settlement.service');
const receiptService = require('../services/receipt.service');
const eventListenerService = require('../services/eventListener.service');
const commandPublisher = require('../services/commandPublisher.service');
const Trade = require('../models/trade.model');
const SettlementRecord = require('../models/settlement.model');
const Receipt = require('../models/receipt.model');
const BlockchainLog = require('../models/blockchainLog.model');
const logger = require('../utils/logger');

const blockchainController = {
  // ═══════════════════════════════════════════════════════════════════════
  //                      STATUS & HEALTH
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * GET /api/blockchain/status
   */
  async getStatus(req, res) {
    try {
      const status = await blockchainService.getStatus();
      const eventStatus = eventListenerService.getStatus();
      const commandStatus = commandPublisher.getStatus();

      res.json({
        success: true,
        data: {
          blockchain: status,
          eventListener: eventStatus,
          commandPublisher: commandStatus,
        },
      });
    } catch (error) {
      logger.error('Error getting blockchain status:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/health
   */
  async healthCheck(req, res) {
    try {
      const health = await blockchainService.healthCheck();
      res.json({
        success: true,
        data: health,
      });
    } catch (error) {
      logger.error('Error in health check:', error);
      res.status(503).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      IDENTITY OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * POST /api/blockchain/register-node
   */
  async registerNode(req, res) {
    try {
      const { nodeId, pubkeyHash, metaURI } = req.body;

      if (!nodeId || !pubkeyHash || !metaURI) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields: nodeId, pubkeyHash, metaURI',
        });
      }

      const result = await blockchainService.registerNode(nodeId, pubkeyHash, metaURI);
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error registering node:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/node/:nodeId
   */
  async getNode(req, res) {
    try {
      const { nodeId } = req.params;
      const node = await blockchainService.getNode(nodeId);

      if (node) {
        res.json({ success: true, data: node });
      } else {
        res.status(404).json({ success: false, error: 'Node not found' });
      }
    } catch (error) {
      logger.error('Error getting node:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      TRADING OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * POST /api/blockchain/post-commit
   */
  async postCommit(req, res) {
    try {
      const { commitHash, offerHash, roundId, nodeId, expiryBlock, signature } = req.body;

      if (!commitHash || !offerHash || !nodeId || !signature) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields',
        });
      }

      const result = await blockchainService.postCommit(
        commitHash,
        offerHash,
        roundId,
        nodeId,
        expiryBlock,
        signature
      );

      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error posting commit:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * POST /api/blockchain/reveal-offer
   */
  async revealOffer(req, res) {
    try {
      const { commitHash, offerHash, salt, nodeId } = req.body;

      if (!commitHash || !offerHash || !salt) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields: commitHash, offerHash, salt',
        });
      }

      const result = await blockchainService.revealOffer(commitHash, offerHash, salt, nodeId);
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error revealing offer:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      MATCH OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * POST /api/blockchain/finalize-match
   */
  async finalizeMatch(req, res) {
    try {
      const { matchHash, matchBlob } = req.body;

      if (!matchHash || !matchBlob) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields: matchHash, matchBlob',
        });
      }

      const result = await blockchainService.finalizeMatch(matchHash, matchBlob);
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error finalizing match:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/match/:matchHash
   */
  async getMatch(req, res) {
    try {
      const { matchHash } = req.params;
      const match = await blockchainService.getMatch(matchHash);

      if (match) {
        res.json({ success: true, data: match });
      } else {
        res.status(404).json({ success: false, error: 'Match not found' });
      }
    } catch (error) {
      logger.error('Error getting match:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      SETTLEMENT OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * POST /api/blockchain/lock-funds
   */
  async lockFunds(req, res) {
    try {
      const { nodeId, amount, tradeId } = req.body;

      if (!nodeId || !amount || !tradeId) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields: nodeId, amount, tradeId',
        });
      }

      const result = await blockchainService.lockFunds(nodeId, amount, tradeId);
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error locking funds:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * POST /api/blockchain/execute-settlement
   */
  async executeSettlement(req, res) {
    try {
      const { tradeId } = req.body;

      if (!tradeId) {
        return res.status(400).json({
          success: false,
          error: 'Missing required field: tradeId',
        });
      }

      const result = await settlementService.finalizeSettlement(tradeId);
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error executing settlement:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * POST /api/blockchain/initiate-trade
   */
  async initiateTrade(req, res) {
    try {
      const { buyerNodeId, sellerNodeId, kwhBucket, priceBucket, amount, matchHash } = req.body;

      if (!buyerNodeId || !sellerNodeId || kwhBucket === undefined || priceBucket === undefined || !amount) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields',
        });
      }

      const result = await settlementService.initiateTrade({
        buyerNodeId,
        sellerNodeId,
        kwhBucket,
        priceBucket,
        amount,
        matchHash,
      });

      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error initiating trade:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      RECEIPT OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * POST /api/blockchain/post-receipt
   */
  async postReceipt(req, res) {
    try {
      const { tradeId, nodeId, meterReading, deliveredKwhBucket, periodStart, periodEnd, signature } = req.body;

      if (!tradeId || !nodeId || !meterReading || !signature) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields',
        });
      }

      const result = await receiptService.submitReceipt({
        tradeId,
        nodeId,
        meterReading,
        deliveredKwhBucket,
        periodStart,
        periodEnd,
        signature,
      });

      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error posting receipt:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/receipt/:tradeId/:nodeId
   */
  async getReceipt(req, res) {
    try {
      const { tradeId, nodeId } = req.params;
      const receipt = await receiptService.getReceipt(tradeId, nodeId);

      if (receipt) {
        res.json({ success: true, data: receipt });
      } else {
        res.status(404).json({ success: false, error: 'Receipt not found' });
      }
    } catch (error) {
      logger.error('Error getting receipt:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      TRADE QUERIES
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * GET /api/blockchain/trades
   */
  async getTrades(req, res) {
    try {
      const limit = parseInt(req.query.limit, 10) || 50;
      const trades = await blockchainService.getAllTrades(limit);
      res.json({ success: true, data: trades });
    } catch (error) {
      logger.error('Error getting trades:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/trades/active
   */
  async getActiveTrades(req, res) {
    try {
      const trades = await blockchainService.getActiveTrades();
      res.json({ success: true, data: trades });
    } catch (error) {
      logger.error('Error getting active trades:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/trades/:nodeId
   */
  async getNodeTrades(req, res) {
    try {
      const { nodeId } = req.params;
      const limit = parseInt(req.query.limit, 10) || 50;
      const trades = await blockchainService.getNodeTrades(nodeId, limit);
      res.json({ success: true, data: trades });
    } catch (error) {
      logger.error('Error getting node trades:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/trade/:tradeId
   */
  async getTradeById(req, res) {
    try {
      const { tradeId } = req.params;
      const trade = await blockchainService.getTradeById(tradeId);

      if (trade) {
        res.json({ success: true, data: trade });
      } else {
        res.status(404).json({ success: false, error: 'Trade not found' });
      }
    } catch (error) {
      logger.error('Error getting trade:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      EVENT QUERIES
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * GET /api/blockchain/events
   */
  async getEvents(req, res) {
    try {
      const limit = parseInt(req.query.limit, 10) || 50;
      const nodeId = req.query.node_id || null;
      const events = await blockchainService.getRecentLogs(nodeId, limit);
      res.json({ success: true, data: events });
    } catch (error) {
      logger.error('Error getting events:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/events/latest
   */
  async getLatestEvents(req, res) {
    try {
      const limit = parseInt(req.query.limit, 10) || 20;
      const events = await BlockchainLog.getRecentLogs(null, limit);
      res.json({ success: true, data: events });
    } catch (error) {
      logger.error('Error getting latest events:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      SETTLEMENT QUERIES
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * GET /api/blockchain/settlements
   */
  async getSettlements(req, res) {
    try {
      const limit = parseInt(req.query.limit, 10) || 50;
      const settlements = await SettlementRecord.getRecent(limit);
      res.json({ success: true, data: settlements });
    } catch (error) {
      logger.error('Error getting settlements:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/settlements/pending
   */
  async getPendingSettlements(req, res) {
    try {
      const settlements = await settlementService.getPendingSettlements();
      res.json({ success: true, data: settlements });
    } catch (error) {
      logger.error('Error getting pending settlements:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/settlement/:tradeId
   */
  async getSettlement(req, res) {
    try {
      const { tradeId } = req.params;
      const settlement = await settlementService.getSettlement(tradeId);

      if (settlement) {
        res.json({ success: true, data: settlement });
      } else {
        res.status(404).json({ success: false, error: 'Settlement not found' });
      }
    } catch (error) {
      logger.error('Error getting settlement:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * GET /api/blockchain/stats
   */
  async getStats(req, res) {
    try {
      const settlementStats = await settlementService.getStats();
      const receiptStats = await receiptService.getStats();

      res.json({
        success: true,
        data: {
          settlements: settlementStats,
          receipts: receiptStats,
        },
      });
    } catch (error) {
      logger.error('Error getting stats:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      COMMAND OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * POST /api/blockchain/command
   */
  async sendCommand(req, res) {
    try {
      const { nodeId, commandType, payload } = req.body;

      if (!nodeId || !commandType) {
        return res.status(400).json({
          success: false,
          error: 'Missing required fields: nodeId, commandType',
        });
      }

      const result = await commandPublisher.sendCommand(nodeId, commandType, payload || {});
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error sending command:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },

  /**
   * POST /api/blockchain/safe-mode
   */
  async sendSafeMode(req, res) {
    try {
      const { nodeId, reason } = req.body;

      if (!nodeId) {
        return res.status(400).json({
          success: false,
          error: 'Missing required field: nodeId',
        });
      }

      const result = await commandPublisher.sendSafeMode(nodeId, reason || 'manual');
      res.json({ success: result.success, ...result });
    } catch (error) {
      logger.error('Error sending safe mode:', error);
      res.status(500).json({ success: false, error: error.message });
    }
  },
};

module.exports = blockchainController;
