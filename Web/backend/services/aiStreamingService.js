/**
 * AI Decision Streaming Service
 *
 * Handles real-time streaming of AI decisions via Socket.io and MQTT.
 * Listens to the AI trading integration events and broadcasts them
 * to connected clients (dashboard, Pi devices).
 */

const logger = require('../utils/logger');
const aiConfig = require('../config/ai.config');
const aiTradingIntegration = require('./aiTradingIntegration.service');

class AIStreamingService {
  constructor() {
    this.io = null;
    this.mqtt = null;
    this.initialized = false;
    this.stats = {
      decisionsStreamed: 0,
      tradesStreamed: 0,
      errorsStreamed: 0,
      clientsConnected: 0,
    };
  }

  /**
   * Initialize the streaming service
   *
   * @param {object} options - Configuration options
   * @param {Server} options.io - Socket.io server instance
   * @param {object} options.mqtt - MQTT client instance
   */
  initialize({ io, mqtt }) {
    this.io = io;
    this.mqtt = mqtt;

    // Listen to AI trading integration events
    this.setupEventListeners();

    // Track connected clients
    if (io) {
      io.on('connection', (socket) => {
        this.stats.clientsConnected++;

        socket.on('disconnect', () => {
          this.stats.clientsConnected--;
        });

        // Subscribe to AI events
        socket.on('subscribe:ai', () => {
          socket.join('ai:decisions');
          socket.join('ai:trades');
          logger.debug(`Socket ${socket.id} subscribed to AI events`);
        });

        socket.on('unsubscribe:ai', () => {
          socket.leave('ai:decisions');
          socket.leave('ai:trades');
          logger.debug(`Socket ${socket.id} unsubscribed from AI events`);
        });
      });
    }

    this.initialized = true;
    logger.info('AI Streaming Service initialized');
  }

  /**
   * Setup event listeners for AI trading integration
   */
  setupEventListeners() {
    // Listen for AI decisions
    aiTradingIntegration.on('decision', (data) => {
      this.broadcastDecision(data);
    });

    // Listen for trade events (if they're emitted)
    if (typeof aiTradingIntegration.on === 'function') {
      // These may not exist if aiTradingIntegration doesn't emit them
      // We'll handle silently
    }

    logger.debug('AI event listeners configured');
  }

  /**
   * Broadcast AI decision to all connected clients
   */
  broadcastDecision(decision) {
    if (!this.initialized) return;

    const payload = {
      type: 'ai:decision',
      data: {
        node_id: decision.nodeId,
        decision: decision.decision?.decision || decision.decision,
        confidence: decision.decision?.confidence || decision.confidence,
        action_kw: decision.decision?.action_kw,
        action_name: decision.decision?.action_name,
        trade_action: decision.decision?.trade_action || decision.tradeAction,
        recommended_quantity: decision.decision?.recommended_quantity,
        net_power_kw: decision.decision?.net_power_kw,
        is_mock: decision.decision?.is_mock,
        model_version: decision.decision?.model_version,
      },
      timestamp: decision.timestamp || Date.now(),
    };

    // Emit via Socket.io
    if (this.io && aiConfig.streaming.emitToSocketio) {
      // Broadcast to node-specific room
      this.io.to(`node:${decision.nodeId}`).emit('ai:decision', payload.data);

      // Broadcast to global AI decisions room
      this.io.to('ai:decisions').emit('ai:decision', payload);

      this.stats.decisionsStreamed++;
    }

    // Publish to MQTT
    if (this.mqtt && aiConfig.streaming.publishToMqtt) {
      const topic = `${aiConfig.streaming.mqttTopicPrefix}/ai/decision/${decision.nodeId}`;
      try {
        this.mqtt.publish(topic, JSON.stringify(payload.data));
      } catch (error) {
        logger.error(`Failed to publish AI decision to MQTT: ${error.message}`);
      }
    }

    logger.debug(`AI decision streamed for ${decision.nodeId}: ${payload.data.decision}`);
  }

  /**
   * Broadcast trade proposal to connected clients
   */
  broadcastTradeProposal(tradeProposal) {
    if (!this.initialized) return;

    const payload = {
      type: 'ai:trade_proposal',
      data: tradeProposal,
      timestamp: Date.now(),
    };

    // Emit via Socket.io
    if (this.io && aiConfig.streaming.emitToSocketio) {
      this.io.to(`node:${tradeProposal.node_id}`).emit('ai:trade_proposal', payload.data);
      this.io.to('ai:trades').emit('ai:trade_proposal', payload);
      this.stats.tradesStreamed++;
    }

    // Publish to MQTT
    if (this.mqtt && aiConfig.streaming.publishToMqtt) {
      const topic = `${aiConfig.streaming.mqttTopicPrefix}/ai/trade/${tradeProposal.node_id}`;
      try {
        this.mqtt.publish(topic, JSON.stringify(payload.data));
      } catch (error) {
        logger.error(`Failed to publish trade proposal to MQTT: ${error.message}`);
      }
    }

    logger.info(
      `Trade proposal streamed: ${tradeProposal.trade_id} ` +
        `(${tradeProposal.trade_type} ${tradeProposal.quantity_kwh?.toFixed(3)} kWh)`
    );
  }

  /**
   * Broadcast trade execution status
   */
  broadcastTradeStatus(tradeId, nodeId, status, details = {}) {
    if (!this.initialized) return;

    const payload = {
      type: 'ai:trade_status',
      data: {
        trade_id: tradeId,
        node_id: nodeId,
        status,
        ...details,
      },
      timestamp: Date.now(),
    };

    // Emit via Socket.io
    if (this.io) {
      this.io.to(`node:${nodeId}`).emit('ai:trade_status', payload.data);
      this.io.to('ai:trades').emit('ai:trade_status', payload);
    }

    // Publish to MQTT
    if (this.mqtt && aiConfig.streaming.publishToMqtt) {
      const topic = `${aiConfig.streaming.mqttTopicPrefix}/ai/trade/status/${nodeId}`;
      try {
        this.mqtt.publish(topic, JSON.stringify(payload.data));
      } catch (error) {
        logger.error(`Failed to publish trade status to MQTT: ${error.message}`);
      }
    }
  }

  /**
   * Broadcast error event
   */
  broadcastError(nodeId, error, context = {}) {
    if (!this.initialized) return;

    const payload = {
      type: 'ai:error',
      data: {
        node_id: nodeId,
        error: error.message || error,
        context,
      },
      timestamp: Date.now(),
    };

    // Emit via Socket.io
    if (this.io) {
      this.io.to(`node:${nodeId}`).emit('ai:error', payload.data);
      this.io.to('ai:decisions').emit('ai:error', payload);
      this.stats.errorsStreamed++;
    }

    logger.warn(`AI error broadcast for ${nodeId}: ${error.message || error}`);
  }

  /**
   * Manually emit a decision (for testing or external triggers)
   */
  emitDecision(nodeId, decisionData) {
    this.broadcastDecision({
      nodeId,
      decision: decisionData,
      timestamp: Date.now(),
    });
  }

  /**
   * Get streaming service stats
   */
  getStats() {
    return {
      ...this.stats,
      initialized: this.initialized,
      socketioEnabled: !!this.io && aiConfig.streaming.emitToSocketio,
      mqttEnabled: !!this.mqtt && aiConfig.streaming.publishToMqtt,
    };
  }

  /**
   * Shutdown the streaming service
   */
  shutdown() {
    this.initialized = false;
    logger.info('AI Streaming Service shutdown');
  }
}

// Singleton instance
const aiStreamingService = new AIStreamingService();

module.exports = aiStreamingService;
