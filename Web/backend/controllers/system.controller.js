const mongoose = require('mongoose');
const { pool, isPostgresConnected } = require('../config/db.postgres');
const mqttClient = require('../utils/mqttClient');
const logger = require('../utils/logger');

const systemController = {
  /**
   * GET /api/system/health
   * Get system health status
   */
  async getHealth(req, res, next) {
    try {
      const health = {
        status: 'OK',
        timestamp: Date.now(),
        uptime: process.uptime(),
        services: {},
      };

      // Check MongoDB
      try {
        const mongoState = mongoose.connection.readyState;
        health.services.mongodb = {
          status: mongoState === 1 ? 'connected' : 'disconnected',
          readyState: mongoState,
        };
        if (mongoState !== 1) health.status = 'DEGRADED';
      } catch (err) {
        health.services.mongodb = { status: 'error', error: err.message };
        health.status = 'DEGRADED';
      }

      // Check PostgreSQL (optional)
      try {
        if (isPostgresConnected()) {
          const pgResult = await pool.query('SELECT 1');
          health.services.postgresql = {
            status: pgResult ? 'connected' : 'disconnected',
          };
        } else {
          health.services.postgresql = { status: 'disabled', note: 'Running in demo mode' };
        }
      } catch (err) {
        health.services.postgresql = { status: 'unavailable', note: 'Demo mode active' };
      }

      // Check MQTT (optional)
      try {
        const mqttStatus = mqttClient.getStatus();
        health.services.mqtt = {
          status: mqttStatus.connected ? 'connected' : 'disconnected',
          broker: mqttStatus.broker,
          reconnectAttempts: mqttStatus.reconnectAttempts,
        };
      } catch (err) {
        health.services.mqtt = { status: 'unavailable' };
      }

      // Memory usage
      const memUsage = process.memoryUsage();
      health.memory = {
        heapUsed: Math.round(memUsage.heapUsed / 1024 / 1024) + ' MB',
        heapTotal: Math.round(memUsage.heapTotal / 1024 / 1024) + ' MB',
        external: Math.round(memUsage.external / 1024 / 1024) + ' MB',
        rss: Math.round(memUsage.rss / 1024 / 1024) + ' MB',
      };

      // Environment
      health.environment = process.env.NODE_ENV || 'development';
      health.nodeVersion = process.version;

      // For demo mode, always return 200 OK
      res.status(200).json({
        success: true,
        data: health,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * GET /api/system/info
   * Get system information
   */
  async getInfo(req, res, next) {
    try {
      res.json({
        success: true,
        data: {
          name: 'Grid-Guardian Backend',
          version: '1.0.0',
          description: 'Unified Backend Layer for AI + Blockchain + Edge Integration',
          environment: process.env.NODE_ENV || 'development',
          nodeVersion: process.version,
          uptime: process.uptime(),
          timestamp: Date.now(),
          endpoints: {
            telemetry: {
              'POST /api/telemetry': 'Submit telemetry data',
              'GET /api/telemetry/latest/:node_id': 'Get latest telemetry for a node',
              'GET /api/telemetry/history/:node_id': 'Get telemetry history for a node',
            },
            dashboard: {
              'GET /api/dashboard/summary': 'Get dashboard summary',
              'GET /api/dashboard/node/:node_id': 'Get node-specific dashboard',
            },
            system: {
              'GET /api/system/health': 'Get system health status',
              'GET /api/system/info': 'Get system information',
              'POST /api/system/refresh': 'Trigger data refresh',
            },
            control: {
              'GET /api/control/state': 'Get control state',
              'POST /api/control/trading/enable': 'Enable trading',
              'POST /api/control/trading/disable': 'Disable trading',
              'POST /api/control/manual-override': 'Toggle manual override',
              'POST /api/control/safe-mode/enable': 'Enable safe mode',
              'POST /api/control/safe-mode/disable': 'Disable safe mode',
            },
            ai: {
              'GET /api/ai/decision/:node_id': 'Get AI decision for a node',
              'GET /api/ai/history/:node_id': 'Get AI decision history',
              'POST /api/ai/infer/:node_id': 'Trigger AI inference',
            },
            blockchain: {
              'GET /api/blockchain/trades': 'Get all trades',
              'GET /api/blockchain/trades/active': 'Get active trades',
              'GET /api/blockchain/events': 'Get blockchain events',
              'GET /api/blockchain/status': 'Get blockchain status',
            },
          },
        },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * POST /api/system/refresh
   * Trigger system data refresh
   */
  async refresh(req, res, next) {
    try {
      logger.info('System refresh triggered');

      // Emit refresh event to all connected clients
      const io = req.app.get('io');
      if (io) {
        io.emit('system:refresh', {
          timestamp: Date.now(),
          message: 'Data refresh triggered',
        });
      }

      res.json({
        success: true,
        message: 'System refresh triggered successfully',
        timestamp: Date.now(),
      });
    } catch (error) {
      next(error);
    }
  },
};

module.exports = systemController;
