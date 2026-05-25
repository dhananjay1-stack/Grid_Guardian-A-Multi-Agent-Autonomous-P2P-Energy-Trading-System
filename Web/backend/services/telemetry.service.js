const Telemetry = require('../models/telemetry.model');
const logger = require('../utils/logger');
const { isMongoConnected } = require('../config/db.mongo');

// In-memory fallback storage when MongoDB unavailable
const memoryStore = {
  telemetry: new Map(), // nodeId -> array of readings
  maxPerNode: 100,
};

const telemetryService = {
  /**
   * Validate telemetry payload
   */
  validateTelemetry(data) {
    const errors = [];

    if (!data.node_id || typeof data.node_id !== 'string') {
      errors.push('node_id is required and must be a string');
    }

    // Make voltage/current/power optional with defaults
    if (data.voltage !== undefined && (typeof data.voltage !== 'number' || data.voltage < 0)) {
      errors.push('voltage must be a non-negative number');
    }

    if (data.current !== undefined && (typeof data.current !== 'number' || data.current < 0)) {
      errors.push('current must be a non-negative number');
    }

    if (data.power !== undefined && (typeof data.power !== 'number' || data.power < 0)) {
      errors.push('power must be a non-negative number');
    }

    return {
      isValid: errors.length === 0,
      errors,
    };
  },

  /**
   * Save telemetry data (MongoDB or in-memory fallback)
   */
  async saveTelemetry(data, source = 'REST') {
    const record = {
      _id: Date.now().toString(),
      node_id: data.node_id,
      voltage: data.voltage || 0,
      current: data.current || 0,
      power: data.power || 0,
      energy: data.energy || 0,
      timestamp: data.timestamp || Date.now() / 1000,
      source,
      status: 'ACTIVE',
      createdAt: new Date(),
    };

    // Try MongoDB first
    if (isMongoConnected()) {
      try {
        const telemetryDoc = new Telemetry(record);
        const saved = await telemetryDoc.save();
        logger.debug(`Telemetry saved to MongoDB for node ${data.node_id}`);
        return saved;
      } catch (error) {
        logger.warn('MongoDB save failed, using memory fallback:', error.message);
      }
    }

    // Fallback to in-memory storage
    if (!memoryStore.telemetry.has(data.node_id)) {
      memoryStore.telemetry.set(data.node_id, []);
    }
    const nodeData = memoryStore.telemetry.get(data.node_id);
    nodeData.unshift(record);

    // Keep only recent readings
    if (nodeData.length > memoryStore.maxPerNode) {
      nodeData.pop();
    }

    logger.debug(`Telemetry saved to memory for node ${data.node_id}`);
    return record;
  },

  /**
   * Get latest telemetry for a node
   */
  async getLatestTelemetry(nodeId) {
    // Try MongoDB first
    if (isMongoConnected()) {
      try {
        const latest = await Telemetry.getLatestByNodeId(nodeId);
        if (latest) return latest;
      } catch (error) {
        logger.warn('MongoDB query failed, using memory fallback');
      }
    }

    // Fallback to in-memory
    const nodeData = memoryStore.telemetry.get(nodeId);
    return nodeData && nodeData.length > 0 ? nodeData[0] : null;
  },

  /**
   * Get telemetry history for a node
   */
  async getTelemetryHistory(nodeId, limit = 100, skip = 0) {
    // Try MongoDB first
    if (isMongoConnected()) {
      try {
        const history = await Telemetry.getHistoryByNodeId(nodeId, limit, skip);
        if (history && history.length > 0) return history;
      } catch (error) {
        logger.warn('MongoDB query failed, using memory fallback');
      }
    }

    // Fallback to in-memory
    const nodeData = memoryStore.telemetry.get(nodeId) || [];
    return nodeData.slice(skip, skip + limit);
  },

  /**
   * Get all active nodes with their latest telemetry
   */
  async getActiveNodes() {
    // Try MongoDB first
    if (isMongoConnected()) {
      try {
        const activeNodes = await Telemetry.aggregate([
          { $sort: { timestamp: -1 } },
          {
            $group: {
              _id: '$node_id',
              latest_power: { $first: '$power' },
              latest_voltage: { $first: '$voltage' },
              latest_current: { $first: '$current' },
              last_seen: { $first: '$timestamp' },
              status: { $first: '$status' },
            },
          },
          {
            $project: {
              node_id: '$_id',
              latest_power: 1,
              latest_voltage: 1,
              latest_current: 1,
              last_seen: 1,
              status: 1,
              _id: 0,
            },
          },
        ]);
        if (activeNodes && activeNodes.length > 0) return activeNodes;
      } catch (error) {
        logger.warn('MongoDB query failed, using memory fallback');
      }
    }

    // Fallback to in-memory
    const activeNodes = [];
    for (const [nodeId, readings] of memoryStore.telemetry.entries()) {
      if (readings.length > 0) {
        const latest = readings[0];
        activeNodes.push({
          node_id: nodeId,
          latest_power: latest.power,
          latest_voltage: latest.voltage,
          latest_current: latest.current,
          last_seen: latest.timestamp,
          status: latest.status,
        });
      }
    }
    return activeNodes;
  },

  /**
   * Calculate power statistics for a node
   */
  async getPowerStats(nodeId, hours = 24) {
    const since = Date.now() / 1000 - hours * 3600;

    // Try MongoDB first
    if (isMongoConnected()) {
      try {
        const stats = await Telemetry.aggregate([
          {
            $match: {
              node_id: nodeId,
              timestamp: { $gte: since },
            },
          },
          {
            $group: {
              _id: null,
              avg_power: { $avg: '$power' },
              max_power: { $max: '$power' },
              min_power: { $min: '$power' },
              total_readings: { $sum: 1 },
            },
          },
        ]);
        if (stats && stats.length > 0) return stats[0];
      } catch (error) {
        logger.warn('MongoDB query failed, using memory fallback');
      }
    }

    // Fallback to in-memory
    const nodeData = (memoryStore.telemetry.get(nodeId) || [])
      .filter(r => r.timestamp >= since);

    if (nodeData.length === 0) {
      return { avg_power: 0, max_power: 0, min_power: 0, total_readings: 0 };
    }

    const powers = nodeData.map(r => r.power);
    return {
      avg_power: powers.reduce((a, b) => a + b, 0) / powers.length,
      max_power: Math.max(...powers),
      min_power: Math.min(...powers),
      total_readings: powers.length,
    };
  },
};

module.exports = telemetryService;
