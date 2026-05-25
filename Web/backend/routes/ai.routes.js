const express = require('express');
const router = express.Router();
const aiService = require('../services/ai.service');
const aiTradingIntegration = require('../services/aiTradingIntegration.service');
const telemetryService = require('../services/telemetry.service');
const { asyncHandler } = require('../middleware/error.middleware');
const { validateNodeId } = require('../middleware/validate.middleware');

/**
 * @route   GET /api/ai/decision/:node_id
 * @desc    Get latest AI decision for a node
 * @access  Public
 */
router.get(
  '/decision/:node_id',
  validateNodeId,
  asyncHandler(async (req, res) => {
    const { node_id } = req.params;

    const decision = await aiService.getLatestDecision(node_id);

    if (!decision) {
      return res.json({
        success: true,
        data: {
          node_id,
          decision: 'HOLD',
          confidence: 0.5,
          forecasted_load: 800,
          forecasted_solar: 600,
          recommended_quantity: 0,
          action_kw: 0,
          action_name: 'idle',
          trade_action: null,
          net_power_kw: -0.2,
          timestamp: Date.now() / 1000,
          features: {},
          is_mock: true,
        },
      });
    }

    res.json({
      success: true,
      data: {
        ...decision,
        forecasted_load: decision.forecasted_load || decision.features?.avg_power_24h || 800,
        forecasted_solar: decision.forecasted_solar || decision.features?.peak_power * 0.7 || 600,
        recommended_quantity: decision.recommended_quantity || 0,
      },
    });
  })
);

/**
 * @route   GET /api/ai/history/:node_id
 * @desc    Get AI decision history for a node
 * @access  Public
 */
router.get(
  '/history/:node_id',
  validateNodeId,
  asyncHandler(async (req, res) => {
    const { node_id } = req.params;
    const limit = parseInt(req.query.limit) || 50;

    const history = await aiService.getDecisionHistory(node_id, limit);

    res.json({
      success: true,
      data: history.map((h) => ({
        ...h,
        forecasted_load: h.forecasted_load || h.features?.avg_power_24h || 800,
        forecasted_solar: h.forecasted_solar || h.features?.peak_power * 0.7 || 600,
        recommended_quantity: h.recommended_quantity || 0,
      })),
    });
  })
);

/**
 * @route   POST /api/ai/infer/:node_id
 * @desc    Trigger AI inference manually
 * @access  Public
 */
router.post(
  '/infer/:node_id',
  validateNodeId,
  asyncHandler(async (req, res) => {
    const { node_id } = req.params;
    const { context } = req.body || {};

    // Get latest telemetry
    const telemetry = await telemetryService.getLatestTelemetry(node_id);

    if (!telemetry) {
      return res.status(404).json({
        success: false,
        error: { message: `No telemetry data found for node ${node_id}` },
      });
    }

    // Get power stats
    const stats = await telemetryService.getPowerStats(node_id, 24);

    // Merge context with stats
    const additionalData = {
      avg_power_24h: stats.avg_power,
      peak_power: stats.max_power,
      ...context,
    };

    // Process AI decision
    const decision = await aiService.processAndDecide(node_id, telemetry, additionalData);

    // Check if trade action should be triggered
    let tradeProposal = null;
    if (decision.trade_action) {
      tradeProposal = await aiTradingIntegration.processAIDecision(decision.toObject());
    }

    // Emit to Socket.io
    const io = req.app.get('io');
    if (io) {
      io.to(`node:${node_id}`).emit('ai:decision', {
        node_id,
        decision: decision.decision,
        confidence: decision.confidence,
        action_kw: decision.action_kw,
        action_name: decision.action_name,
        trade_action: decision.trade_action,
        recommended_quantity: decision.recommended_quantity,
      });
    }

    res.json({
      success: true,
      data: {
        ...decision.toObject(),
        forecasted_load: decision.forecasted_load || stats.avg_power || 800,
        forecasted_solar: decision.forecasted_solar || stats.max_power * 0.7 || 600,
        trade_proposal: tradeProposal,
      },
    });
  })
);

/**
 * @route   GET /api/ai/decisions
 * @desc    Get all latest AI decisions
 * @access  Public
 */
router.get(
  '/decisions',
  asyncHandler(async (req, res) => {
    res.json({
      success: true,
      data: [],
    });
  })
);

/**
 * @route   GET /api/ai/status
 * @desc    Get AI engine status
 * @access  Public
 */
router.get(
  '/status',
  asyncHandler(async (req, res) => {
    const status = await aiService.getServerStatus();

    res.json({
      success: true,
      data: {
        ...status,
        trading_integration: {
          enabled: true,
          pending_trades: aiTradingIntegration.getTradeStats(),
        },
      },
    });
  })
);

/**
 * @route   POST /api/ai/refresh-health
 * @desc    Force refresh AI server health check
 * @access  Public
 */
router.post(
  '/refresh-health',
  asyncHandler(async (req, res) => {
    const healthy = await aiService.refreshHealthCheck();

    res.json({
      success: true,
      data: {
        healthy,
        checked_at: new Date().toISOString(),
      },
    });
  })
);

/**
 * @route   GET /api/ai/trades/pending
 * @desc    Get pending AI-generated trades
 * @access  Public
 */
router.get(
  '/trades/pending',
  asyncHandler(async (req, res) => {
    const { node_id } = req.query;
    const trades = aiTradingIntegration.getPendingTrades(node_id);

    res.json({
      success: true,
      data: trades,
    });
  })
);

/**
 * @route   GET /api/ai/trades/pending/:node_id
 * @desc    Get pending AI-generated trades for a node
 * @access  Public
 */
router.get(
  '/trades/pending/:node_id',
  validateNodeId,
  asyncHandler(async (req, res) => {
    const { node_id } = req.params;
    const trades = aiTradingIntegration.getPendingTrades(node_id);

    res.json({
      success: true,
      data: trades,
    });
  })
);

/**
 * @route   GET /api/ai/trades/stats
 * @desc    Get AI trading statistics
 * @access  Public
 */
router.get(
  '/trades/stats',
  asyncHandler(async (req, res) => {
    const stats = aiTradingIntegration.getTradeStats();

    res.json({
      success: true,
      data: stats,
    });
  })
);

/**
 * @route   POST /api/ai/trades/:trade_id/cancel
 * @desc    Cancel a pending AI trade
 * @access  Public
 */
router.post(
  '/trades/:trade_id/cancel',
  asyncHandler(async (req, res) => {
    const { trade_id } = req.params;
    const { reason } = req.body;

    await aiTradingIntegration.onTradeCancelled(trade_id, reason || 'User cancelled');

    res.json({
      success: true,
      message: `Trade ${trade_id} cancelled`,
    });
  })
);

/**
 * @route   POST /api/ai/infer-with-trade/:node_id
 * @desc    Trigger AI inference and automatically process trade
 * @access  Public
 */
router.post(
  '/infer-with-trade/:node_id',
  validateNodeId,
  asyncHandler(async (req, res) => {
    const { node_id } = req.params;
    const { context, auto_trade } = req.body || {};

    // Get latest telemetry
    const telemetry = await telemetryService.getLatestTelemetry(node_id);

    if (!telemetry) {
      return res.status(404).json({
        success: false,
        error: { message: `No telemetry data found for node ${node_id}` },
      });
    }

    // Get power stats
    const stats = await telemetryService.getPowerStats(node_id, 24);

    const additionalData = {
      avg_power_24h: stats.avg_power,
      peak_power: stats.max_power,
      ...context,
    };

    // Process AI decision
    const decision = await aiService.processAndDecide(node_id, telemetry, additionalData);

    // Process trade if auto_trade enabled and trade action present
    let tradeProposal = null;
    if (auto_trade !== false && decision.trade_action) {
      tradeProposal = await aiTradingIntegration.processAIDecision(decision.toObject());
    }

    // Emit to Socket.io
    const io = req.app.get('io');
    if (io) {
      io.to(`node:${node_id}`).emit('ai:decision', {
        node_id,
        decision: decision.decision,
        confidence: decision.confidence,
        action_kw: decision.action_kw,
        action_name: decision.action_name,
        trade_action: decision.trade_action,
        recommended_quantity: decision.recommended_quantity,
        trade_proposal: tradeProposal,
      });
    }

    res.json({
      success: true,
      data: {
        decision: decision.toObject(),
        trade_proposal: tradeProposal,
      },
    });
  })
);

module.exports = router;
