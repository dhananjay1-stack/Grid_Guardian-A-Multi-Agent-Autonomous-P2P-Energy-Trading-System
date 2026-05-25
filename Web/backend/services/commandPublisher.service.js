/**
 * Command Publisher Service
 * Grid-Guardian - Pi Command Channel via MQTT
 */

const mqttClient = require('../utils/mqttClient');
const logger = require('../utils/logger');
const { createHash, randomBytes } = require('crypto');
const BlockchainLog = require('../models/blockchainLog.model');

// Command history for idempotency
const commandHistory = new Map();
const COMMAND_HISTORY_TTL = 300000; // 5 minutes

const commandPublisher = {
  /**
   * Send a command to a Pi node
   */
  async sendCommand(nodeId, commandType, payload = {}, options = {}) {
    const { requireAck = true, ttlMs = 60000, idempotencyWindowMs = 10000 } = options;

    // Generate unique command ID
    const commandId = this._generateCommandId(nodeId, commandType, payload, idempotencyWindowMs);

    // Check for duplicate command
    if (this._isDuplicate(commandId)) {
      logger.warn(`Duplicate command blocked: ${commandType} for ${nodeId}`);
      return { success: false, error: 'Duplicate command', commandId };
    }

    // Build command payload
    const command = {
      command_id: commandId,
      command_type: commandType,
      node_id: nodeId,
      payload,
      timestamp: Date.now(),
      nonce: randomBytes(8).toString('hex'),
      require_ack: requireAck,
      ttl_ms: ttlMs,
    };

    // Topic format: gridguardian/{node_id}/commands
    const topic = `gridguardian/${nodeId}/commands`;

    try {
      await mqttClient.publish(topic, command);

      // Store in history for idempotency
      this._recordCommand(commandId, command);

      await this._logCommand(nodeId, commandType, command, topic, 'CONFIRMED');

      logger.info(`Command sent: ${commandType} to ${nodeId} (${commandId})`);

      return {
        success: true,
        commandId,
        topic,
        timestamp: command.timestamp,
      };
    } catch (error) {
      logger.error(`Failed to send command ${commandType} to ${nodeId}:`, error);

      await this._logCommand(nodeId, commandType, {
        payload,
        command_id: commandId,
      }, topic, 'FAILED', error.message);

      return {
        success: false,
        error: error.message,
        commandId,
      };
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      SPECIFIC COMMAND HELPERS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Send execute_trade command to Pi
   */
  async sendExecuteTrade(nodeId, tradeId, buyerNodeId, kwhBucket, action = 'discharge') {
    return this.sendCommand(nodeId, 'execute_trade', {
      trade_id: tradeId,
      buyer_node_id: buyerNodeId,
      kwh_bucket: kwhBucket,
      action, // 'charge' or 'discharge'
      safety_flag: true,
    });
  },

  /**
   * Send discharge command
   */
  async sendDischarge(nodeId, kwhAmount, tradeId = null, reason = 'trade') {
    return this.sendCommand(nodeId, 'discharge', {
      kwh_amount: kwhAmount,
      trade_id: tradeId,
      reason,
      safety_flag: true,
    });
  },

  /**
   * Send charge command
   */
  async sendCharge(nodeId, kwhAmount, tradeId = null, reason = 'trade') {
    return this.sendCommand(nodeId, 'charge', {
      kwh_amount: kwhAmount,
      trade_id: tradeId,
      reason,
      safety_flag: true,
    });
  },

  /**
   * Send safe_mode command (emergency stop)
   */
  async sendSafeMode(nodeId, reason = 'manual') {
    return this.sendCommand(nodeId, 'safe_mode', {
      reason,
      priority: 'high',
      safety_flag: true,
    }, { requireAck: true, ttlMs: 120000 });
  },

  /**
   * Send hold command (pause operations)
   */
  async sendHold(nodeId, durationMs = 60000, reason = 'system') {
    return this.sendCommand(nodeId, 'hold', {
      duration_ms: durationMs,
      reason,
      safety_flag: true,
    });
  },

  /**
   * Request post_receipt from Pi
   */
  async requestReceipt(nodeId, tradeId, periodStart, periodEnd) {
    return this.sendCommand(nodeId, 'post_receipt', {
      trade_id: tradeId,
      period_start: periodStart,
      period_end: periodEnd,
      safety_flag: true,
    });
  },

  /**
   * Send heartbeat check
   */
  async sendHeartbeat(nodeId) {
    return this.sendCommand(nodeId, 'heartbeat_check', {
      timestamp: Date.now(),
    }, { requireAck: true, ttlMs: 30000 });
  },

  /**
   * Send settlement complete notification
   */
  async sendSettlementComplete(nodeId, tradeId, status) {
    return this.sendCommand(nodeId, 'settlement_complete', {
      trade_id: tradeId,
      status,
      timestamp: Date.now(),
    });
  },

  /**
   * Send delivery confirmed notification
   */
  async sendDeliveryConfirmed(nodeId, tradeId) {
    return this.sendCommand(nodeId, 'delivery_confirmed', {
      trade_id: tradeId,
      timestamp: Date.now(),
    });
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      BATCH OPERATIONS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Send command to multiple nodes
   */
  async broadcastCommand(nodeIds, commandType, payload = {}) {
    const results = await Promise.allSettled(
      nodeIds.map((nodeId) => this.sendCommand(nodeId, commandType, payload))
    );

    return results.map((result, index) => ({
      nodeId: nodeIds[index],
      ...(result.status === 'fulfilled' ? result.value : { success: false, error: result.reason }),
    }));
  },

  /**
   * Send safe_mode to all registered nodes
   */
  async emergencyStopAll(nodeIds, reason = 'emergency') {
    logger.warn(`Emergency stop triggered for ${nodeIds.length} nodes: ${reason}`);
    return this.broadcastCommand(nodeIds, 'safe_mode', {
      reason,
      priority: 'critical',
      safety_flag: true,
    });
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      IDEMPOTENCY HELPERS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Generate unique command ID
   */
  _generateCommandId(nodeId, commandType, payload, idempotencyWindowMs = 10000) {
    const bucket = Math.floor(Date.now() / idempotencyWindowMs);
    const data = `${nodeId}:${commandType}:${JSON.stringify(payload)}:${bucket}`;
    return createHash('sha256').update(data).digest('hex').substring(0, 16);
  },

  async _logCommand(nodeId, commandType, payload, topic, status, errorMessage = null) {
    try {
      await BlockchainLog.create({
        node_id: nodeId,
        event_type: status === 'FAILED' ? 'COMMAND_SEND_FAILED' : 'COMMAND_SENT',
        payload: {
          command_type: commandType,
          topic,
          ...payload,
          error: errorMessage,
        },
        status,
      });
    } catch (logError) {
      logger.debug(`Unable to persist command log: ${logError.message}`);
    }
  },

  /**
   * Check if command is duplicate
   */
  _isDuplicate(commandId) {
    return commandHistory.has(commandId);
  },

  /**
   * Record command in history
   */
  _recordCommand(commandId, command) {
    commandHistory.set(commandId, {
      command,
      timestamp: Date.now(),
    });

    // Clean up old entries
    this._cleanupHistory();
  },

  /**
   * Clean up old command history entries
   */
  _cleanupHistory() {
    const now = Date.now();
    for (const [id, entry] of commandHistory) {
      if (now - entry.timestamp > COMMAND_HISTORY_TTL) {
        commandHistory.delete(id);
      }
    }
  },

  // ═══════════════════════════════════════════════════════════════════════
  //                      STATUS
  // ═══════════════════════════════════════════════════════════════════════

  /**
   * Get command publisher status
   */
  getStatus() {
    return {
      mqttConnected: mqttClient.isConnected,
      commandHistorySize: commandHistory.size,
    };
  },
};

module.exports = commandPublisher;
