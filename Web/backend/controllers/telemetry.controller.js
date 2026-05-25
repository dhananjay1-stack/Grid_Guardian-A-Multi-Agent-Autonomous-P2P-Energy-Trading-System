const telemetryService = require('../services/telemetry.service');
const aiService = require('../services/ai.service');
const logger = require('../utils/logger');

const telemetryController = {
  /**
   * POST /api/telemetry
   * Receive and store telemetry data via REST
   */
  async createTelemetry(req, res, next) {
    try {
      const telemetryData = req.body;

      // Validate telemetry
      const validation = telemetryService.validateTelemetry(telemetryData);
      if (!validation.isValid) {
        return res.status(400).json({
          success: false,
          error: {
            message: 'Validation failed',
            details: validation.errors,
          },
        });
      }

      // Save telemetry
      const saved = await telemetryService.saveTelemetry(telemetryData, 'REST');

      // Emit to Socket.io if available
      if (req.app.get('io')) {
        req.app.get('io').emit('telemetry', {
          node_id: telemetryData.node_id,
          data: telemetryData,
        });
      }

      // Process AI decision (async, non-blocking)
      const stats = await telemetryService.getPowerStats(telemetryData.node_id, 24);
      aiService.processAndDecide(telemetryData.node_id, telemetryData, {
        avg_power_24h: stats.avg_power,
        peak_power: stats.max_power,
      }).catch(err => logger.error('AI processing error:', err));

      res.status(201).json({
        success: true,
        data: {
          id: saved._id,
          node_id: saved.node_id,
          timestamp: saved.timestamp,
          message: 'Telemetry data received successfully',
        },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * GET /api/telemetry/latest/:node_id
   * Get latest telemetry for a specific node
   */
  async getLatestTelemetry(req, res, next) {
    try {
      const { node_id } = req.params;

      const latest = await telemetryService.getLatestTelemetry(node_id);

      if (!latest) {
        return res.status(404).json({
          success: false,
          error: {
            message: `No telemetry found for node: ${node_id}`,
          },
        });
      }

      res.json({
        success: true,
        data: latest,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * GET /api/telemetry/history/:node_id
   * Get telemetry history for a specific node
   */
  async getTelemetryHistory(req, res, next) {
    try {
      const { node_id } = req.params;
      const { limit, skip } = req.query;

      const history = await telemetryService.getTelemetryHistory(node_id, limit, skip);

      res.json({
        success: true,
        data: history,
        meta: {
          count: history.length,
          limit,
          skip,
        },
      });
    } catch (error) {
      next(error);
    }
  },
};

module.exports = telemetryController;
