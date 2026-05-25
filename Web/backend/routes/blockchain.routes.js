const express = require('express');
const router = express.Router();
const blockchainService = require('../services/blockchain.service');
const blockchainWeb3Service = require('../services/blockchainWeb3.service');
const settlementService = require('../services/settlement.service');
const receiptService = require('../services/receipt.service');
const eventListenerService = require('../services/eventListener.service');
const commandPublisher = require('../services/commandPublisher.service');
const { asyncHandler } = require('../middleware/error.middleware');
const { validateNodeId } = require('../middleware/validate.middleware');

// ═══════════════════════════════════════════════════════════════════════
//                      STATUS & HEALTH
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   GET /api/blockchain/health
 * @desc    Blockchain health check
 */
router.get(
  '/health',
  asyncHandler(async (req, res) => {
    const health = await blockchainWeb3Service.healthCheck();
    res.json({ success: true, data: health });
  })
);

/**
 * @route   GET /api/blockchain/stats
 * @desc    Get blockchain stats
 */
router.get(
  '/stats',
  asyncHandler(async (req, res) => {
    const SettlementRecord = require('../models/settlement.model');
    const Receipt = require('../models/receipt.model');

    let settlementStats = { total: 0, settled: 0, pending: 0 };
    let receiptStats = { total: 0, submitted: 0 };

    try {
      settlementStats = await SettlementRecord.getStats();
    } catch (e) { /* Settlement table may not exist yet */ }

    try {
      receiptStats = await Receipt.getStats();
    } catch (e) { /* Receipt table may not exist yet */ }

    res.json({
      success: true,
      data: { settlements: settlementStats, receipts: receiptStats },
    });
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      IDENTITY OPERATIONS
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   POST /api/blockchain/register-node
 * @desc    Register a node on-chain
 */
router.post(
  '/register-node',
  asyncHandler(async (req, res) => {
    const { nodeId, pubkeyHash, metaURI } = req.body;
    if (!nodeId || !pubkeyHash || !metaURI) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await blockchainWeb3Service.registerNode(nodeId, pubkeyHash, metaURI);
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   GET /api/blockchain/node/:nodeId
 * @desc    Get node details from blockchain
 */
router.get(
  '/node/:nodeId',
  asyncHandler(async (req, res) => {
    const node = await blockchainWeb3Service.getNode(req.params.nodeId);
    if (node) {
      res.json({ success: true, data: node });
    } else {
      res.status(404).json({ success: false, error: 'Node not found' });
    }
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      TRADING OPERATIONS
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   POST /api/blockchain/post-commit
 * @desc    Post commit hash for commit-reveal scheme
 */
router.post(
  '/post-commit',
  asyncHandler(async (req, res) => {
    const { commitHash, offerHash, roundId, nodeId, expiryBlock, signature } = req.body;
    if (!commitHash || !offerHash || !nodeId || !signature) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await blockchainWeb3Service.postCommit(
      commitHash, offerHash, roundId, nodeId, expiryBlock, signature
    );
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   POST /api/blockchain/reveal-offer
 * @desc    Reveal offer with salt
 */
router.post(
  '/reveal-offer',
  asyncHandler(async (req, res) => {
    const { commitHash, offerHash, salt, nodeId } = req.body;
    if (!commitHash || !offerHash || !salt) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await blockchainWeb3Service.revealOffer(commitHash, offerHash, salt, nodeId);
    res.json({ success: result.success, ...result });
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      MATCH OPERATIONS
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   POST /api/blockchain/finalize-match
 * @desc    Finalize a match after challenge window
 */
router.post(
  '/finalize-match',
  asyncHandler(async (req, res) => {
    const { matchHash, matchBlob } = req.body;
    if (!matchHash || !matchBlob) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await blockchainWeb3Service.finalizeMatch(matchHash, matchBlob);
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   GET /api/blockchain/match/:matchHash
 * @desc    Get match details
 */
router.get(
  '/match/:matchHash',
  asyncHandler(async (req, res) => {
    const match = await blockchainWeb3Service.getMatch(req.params.matchHash);
    if (match) {
      res.json({ success: true, data: match });
    } else {
      res.status(404).json({ success: false, error: 'Match not found' });
    }
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      SETTLEMENT OPERATIONS
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   POST /api/blockchain/lock-funds
 * @desc    Lock funds for a trade
 */
router.post(
  '/lock-funds',
  asyncHandler(async (req, res) => {
    const { nodeId, amount, tradeId } = req.body;
    if (!nodeId || !amount || !tradeId) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await blockchainWeb3Service.lockFunds(nodeId, amount, tradeId);
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   POST /api/blockchain/execute-settlement
 * @desc    Execute final settlement
 */
router.post(
  '/execute-settlement',
  asyncHandler(async (req, res) => {
    const { tradeId } = req.body;
    if (!tradeId) {
      return res.status(400).json({ success: false, error: 'Missing tradeId' });
    }
    const result = await settlementService.finalizeSettlement(tradeId);
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   POST /api/blockchain/raise-dispute
 * @desc    Raise dispute on a delivered trade
 */
router.post(
  '/raise-dispute',
  asyncHandler(async (req, res) => {
    const { tradeId } = req.body;
    if (!tradeId) {
      return res.status(400).json({ success: false, error: 'Missing tradeId' });
    }
    const result = await blockchainWeb3Service.raiseDispute(tradeId);
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   POST /api/blockchain/initiate-trade
 * @desc    Initiate full trade settlement flow
 */
router.post(
  '/initiate-trade',
  asyncHandler(async (req, res) => {
    const { buyerNodeId, sellerNodeId, kwhBucket, priceBucket, amount, matchHash } = req.body;
    if (!buyerNodeId || !sellerNodeId || kwhBucket === undefined || priceBucket === undefined || !amount) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await settlementService.initiateTrade({
      buyerNodeId, sellerNodeId, kwhBucket, priceBucket, amount, matchHash,
    });
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   GET /api/blockchain/settlements
 * @desc    Get settlement records
 */
router.get(
  '/settlements',
  asyncHandler(async (req, res) => {
    const limit = parseInt(req.query.limit) || 50;
    const SettlementRecord = require('../models/settlement.model');
    let settlements = [];
    try {
      settlements = await SettlementRecord.getRecent(limit);
    } catch (e) { /* Table may not exist */ }
    res.json({ success: true, data: settlements });
  })
);

/**
 * @route   GET /api/blockchain/settlements/pending
 * @desc    Get pending settlements
 */
router.get(
  '/settlements/pending',
  asyncHandler(async (req, res) => {
    let settlements = [];
    try {
      settlements = await settlementService.getPendingSettlements();
    } catch (e) { /* Table may not exist */ }
    res.json({ success: true, data: settlements });
  })
);

/**
 * @route   GET /api/blockchain/settlement/:tradeId
 * @desc    Get settlement by trade ID
 */
router.get(
  '/settlement/:tradeId',
  asyncHandler(async (req, res) => {
    const settlement = await settlementService.getSettlement(req.params.tradeId);
    if (settlement) {
      res.json({ success: true, data: settlement });
    } else {
      res.status(404).json({ success: false, error: 'Settlement not found' });
    }
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      RECEIPT OPERATIONS
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   POST /api/blockchain/post-receipt
 * @desc    Submit delivery receipt
 */
router.post(
  '/post-receipt',
  asyncHandler(async (req, res) => {
    const { tradeId, nodeId, meterReading, deliveredKwhBucket, periodStart, periodEnd, signature } = req.body;
    if (!tradeId || !nodeId || !meterReading || !signature) {
      return res.status(400).json({ success: false, error: 'Missing required fields' });
    }
    const result = await receiptService.submitReceipt({
      tradeId, nodeId, meterReading, deliveredKwhBucket, periodStart, periodEnd, signature,
    });
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   GET /api/blockchain/receipt/:tradeId/:nodeId
 * @desc    Get receipt
 */
router.get(
  '/receipt/:tradeId/:nodeId',
  asyncHandler(async (req, res) => {
    const receipt = await receiptService.getReceipt(req.params.tradeId, req.params.nodeId);
    if (receipt) {
      res.json({ success: true, data: receipt });
    } else {
      res.status(404).json({ success: false, error: 'Receipt not found' });
    }
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      COMMAND OPERATIONS
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   POST /api/blockchain/command
 * @desc    Send command to Pi
 */
router.post(
  '/command',
  asyncHandler(async (req, res) => {
    const { nodeId, commandType, payload } = req.body;
    if (!nodeId || !commandType) {
      return res.status(400).json({ success: false, error: 'Missing nodeId or commandType' });
    }
    const result = await commandPublisher.sendCommand(nodeId, commandType, payload || {});
    res.json({ success: result.success, ...result });
  })
);

/**
 * @route   POST /api/blockchain/safe-mode
 * @desc    Send safe mode command to Pi
 */
router.post(
  '/safe-mode',
  asyncHandler(async (req, res) => {
    const { nodeId, reason } = req.body;
    if (!nodeId) {
      return res.status(400).json({ success: false, error: 'Missing nodeId' });
    }
    const result = await commandPublisher.sendSafeMode(nodeId, reason || 'manual');
    res.json({ success: result.success, ...result });
  })
);

// ═══════════════════════════════════════════════════════════════════════
//                      EXISTING ROUTES (PRESERVED)
// ═══════════════════════════════════════════════════════════════════════

/**
 * @route   GET /api/blockchain/trades
 * @desc    Get all trades
 * @access  Public
 */
router.get(
  '/trades',
  asyncHandler(async (req, res) => {
    const limit = parseInt(req.query.limit) || 50;

    // Get all trades from database
    const Trade = require('../models/trade.model');
    const trades = await Trade.findAll(limit);

    res.json({
      success: true,
      data: trades,
    });
  })
);

/**
 * @route   GET /api/blockchain/trades/active
 * @desc    Get active trades only
 * @access  Public
 */
router.get(
  '/trades/active',
  asyncHandler(async (req, res) => {
    const Trade = require('../models/trade.model');
    const trades = await Trade.findActive();

    res.json({
      success: true,
      data: trades,
    });
  })
);

/**
 * @route   GET /api/blockchain/trades/:trade_id
 * @desc    Get a specific trade by trade ID
 * @access  Public
 */
router.get(
  '/trades/:trade_id',
  asyncHandler(async (req, res) => {
    const { trade_id } = req.params;

    const Trade = require('../models/trade.model');
    const trade = await Trade.findByTradeId(trade_id);

    if (!trade) {
      return res.status(404).json({
        success: false,
        error: { message: `Trade not found: ${trade_id}` },
      });
    }

    res.json({
      success: true,
      data: trade,
    });
  })
);

/**
 * @route   GET /api/blockchain/nodes/:node_id/trades
 * @desc    Get trades for a specific node
 * @access  Public
 */
router.get(
  '/nodes/:node_id/trades',
  validateNodeId,
  asyncHandler(async (req, res) => {
    const { node_id } = req.params;
    const limit = parseInt(req.query.limit) || 50;

    const trades = await blockchainService.getNodeTrades(node_id, limit);

    res.json({
      success: true,
      data: trades,
    });
  })
);

/**
 * @route   GET /api/blockchain/trade/:trade_id
 * @desc    Get a specific trade by ID
 * @access  Public
 */
router.get(
  '/trade/:trade_id',
  asyncHandler(async (req, res) => {
    const { trade_id } = req.params;

    const Trade = require('../models/trade.model');
    const trade = await Trade.findByTradeId(trade_id);

    if (!trade) {
      return res.status(404).json({
        success: false,
        error: { message: `Trade not found: ${trade_id}` },
      });
    }

    res.json({
      success: true,
      data: trade,
    });
  })
);

/**
 * @route   GET /api/blockchain/events
 * @desc    Get blockchain events
 * @access  Public
 */
router.get(
  '/events',
  asyncHandler(async (req, res) => {
    const limit = parseInt(req.query.limit) || 50;

    const BlockchainLog = require('../models/blockchainLog.model');
    const events = await BlockchainLog.getRecentLogs(null, limit);

    res.json({
      success: true,
      data: events,
    });
  })
);

/**
 * @route   GET /api/blockchain/status
 * @desc    Get blockchain connection status
 * @access  Public
 */
router.get(
  '/status',
  asyncHandler(async (req, res) => {
    const blockchainStatus = await blockchainWeb3Service.getStatus();
    const eventStatus = eventListenerService.getStatus();
    const commandStatus = commandPublisher.getStatus();

    res.json({
      success: true,
      data: {
        blockchain: blockchainStatus,
        eventListener: eventStatus,
        commandPublisher: commandStatus,
        timestamp: Date.now(),
      },
    });
  })
);

/**
 * @route   GET /api/blockchain/events/latest
 * @desc    Get latest blockchain events
 */
router.get(
  '/events/latest',
  asyncHandler(async (req, res) => {
    const limit = parseInt(req.query.limit) || 20;
    const BlockchainLog = require('../models/blockchainLog.model');
    const events = await BlockchainLog.getRecentLogs(null, limit);
    res.json({ success: true, data: events });
  })
);

/**
 * @route   POST /api/blockchain/trade
 * @desc    Create a new trade
 * @access  Public
 */
router.post(
  '/trade',
  asyncHandler(async (req, res) => {
    const { node_id, trade_type, energy_amount, price_per_unit } = req.body;

    if (!node_id || !trade_type || !energy_amount || !price_per_unit) {
      return res.status(400).json({
        success: false,
        error: { message: 'Missing required fields: node_id, trade_type, energy_amount, price_per_unit' },
      });
    }

    const tradePayload = blockchainService.prepareTradePayload(
      { decision: trade_type, confidence: 0.85 },
      { node_id, power: energy_amount * 4000 }, // Convert kWh back to W for 15-min
      price_per_unit
    );

    const trade = await blockchainService.createTrade(tradePayload);

    // Emit to Socket.io
    const io = req.app.get('io');
    if (io) {
      io.emit('blockchain:trade', {
        event_type: 'TRADE_CREATED',
        payload: trade,
        timestamp: Date.now(),
      });
    }

    res.json({
      success: true,
      data: trade,
    });
  })
);

module.exports = router;
