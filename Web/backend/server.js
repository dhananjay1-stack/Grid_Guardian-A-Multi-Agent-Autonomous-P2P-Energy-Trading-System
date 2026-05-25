require('dotenv').config();

const { app, httpServer, io } = require('./app');
const { connectMongo } = require('./config/db.mongo');
const { connectPostgres } = require('./config/db.postgres');
const mqttClient = require('./utils/mqttClient');
const telemetryService = require('./services/telemetry.service');
const aiService = require('./services/ai.service');
const aiTradingIntegration = require('./services/aiTradingIntegration.service');
const aiStreamingService = require('./services/aiStreamingService');
const blockchainWeb3Service = require('./services/blockchainWeb3.service');
const eventListenerService = require('./services/eventListener.service');
const settlementService = require('./services/settlement.service');
const commandPublisher = require('./services/commandPublisher.service');
const logger = require('./utils/logger');

const PORT = process.env.PORT || 3000;

// MQTT message handlers
const setupMqttHandlers = () => {
  // Handle telemetry messages from MQTT
  mqttClient.on('telemetry', async (data) => {
    try {
      const validation = telemetryService.validateTelemetry(data);
      if (!validation.isValid) {
        logger.warn('Invalid MQTT telemetry:', validation.errors);
        return;
      }

      // Save telemetry
      await telemetryService.saveTelemetry(data, 'MQTT');

      // Emit to Socket.io clients
      io.emit('telemetry', {
        node_id: data.node_id,
        data,
      });

      // Also emit to node-specific room
      io.to(`node:${data.node_id}`).emit('telemetry:node', data);

      // Process AI decision (async)
      const stats = await telemetryService.getPowerStats(data.node_id, 24);
      const aiResult = await aiService.processAndDecide(data.node_id, data, {
        avg_power_24h: stats.avg_power,
        peak_power: stats.max_power,
      });

      // Extended AI decision payload
      const aiDecisionPayload = {
        node_id: data.node_id,
        decision: aiResult.decision,
        confidence: aiResult.confidence,
        action_kw: aiResult.action_kw,
        action_name: aiResult.action_name,
        trade_action: aiResult.trade_action,
        recommended_quantity: aiResult.recommended_quantity,
        forecasted_load: aiResult.forecasted_load,
        forecasted_solar: aiResult.forecasted_solar,
        net_power_kw: aiResult.net_power_kw,
        model_version: aiResult.model_version,
        is_mock: aiResult.is_mock,
        timestamp: Date.now(),
      };

      // Emit AI decision to all listeners
      io.to(`node:${data.node_id}`).emit('ai:decision', aiDecisionPayload);
      io.emit('ai:decision', aiDecisionPayload);

      // Process trade if AI recommends trading
      if (aiResult.trade_action) {
        const tradeProposal = await aiTradingIntegration.processAIDecision(aiResult.toObject());
        if (tradeProposal) {
          logger.info(
            `AI Trade Proposal generated: ${tradeProposal.trade_type} ` +
              `${tradeProposal.quantity_kwh.toFixed(3)} kWh for ${data.node_id}`
          );
        }
      }
    } catch (error) {
      logger.error('Error processing MQTT telemetry:', error);
    }
  });

  // Handle status messages
  mqttClient.on('status', (data) => {
    logger.info(`Status update from ${data.node_id}:`, data);
    io.emit('status', data);
  });

  // Handle alert messages
  mqttClient.on('alert', (data) => {
    logger.warn(`Alert from ${data.node_id}:`, data);
    io.emit('alert', data);
  });

  // Handle MQTT errors
  mqttClient.on('error', (error) => {
    logger.error('MQTT client error:', error);
  });

  // Handle malformed messages
  mqttClient.on('malformed', ({ topic, message }) => {
    logger.warn(`Malformed MQTT message on ${topic}:`, message.substring(0, 100));
  });
};

// Initialize AI Trading Integration
const initializeAITradingIntegration = () => {
  try {
    aiTradingIntegration.initialize({
      blockchainService: blockchainWeb3Service,
      settlementService: settlementService,
      commandPublisher: commandPublisher,
      io: io,
    });
    logger.info('AI Trading Integration initialized');

    // Setup periodic cleanup of old pending trades
    setInterval(() => {
      aiTradingIntegration.cleanupOldTrades(3600000); // 1 hour
    }, 300000); // Every 5 minutes

    return true;
  } catch (error) {
    logger.error('Failed to initialize AI Trading Integration:', error);
    return false;
  }
};

// Check AI server health periodically
const startAIHealthMonitor = () => {
  const checkInterval = parseInt(process.env.AI_HEALTH_CHECK_INTERVAL) || 60000;

  const checkHealth = async () => {
    try {
      const status = await aiService.getServerStatus();
      if (status.healthy) {
        logger.debug('AI inference server healthy');
      } else {
        logger.warn('AI inference server unhealthy - using mock fallback');
      }
    } catch (error) {
      logger.debug('AI health check skipped:', error.message);
    }
  };

  // Initial check
  setTimeout(checkHealth, 5000);

  // Periodic checks
  setInterval(checkHealth, checkInterval);
};

// Graceful shutdown handler
const gracefulShutdown = async (signal) => {
  logger.info(`${signal} received. Starting graceful shutdown...`);

  // Stop accepting new connections
  httpServer.close(() => {
    logger.info('HTTP server closed');
  });

  // Close database connections
  try {
    const { disconnectMongo } = require('./config/db.mongo');
    const { disconnectPostgres } = require('./config/db.postgres');

    await mqttClient.disconnect();
    eventListenerService.stop();
    aiStreamingService.shutdown();
    aiTradingIntegration.shutdown();
    await blockchainWeb3Service.shutdown();
    await disconnectMongo();
    await disconnectPostgres();

    logger.info('All connections closed. Exiting...');
    process.exit(0);
  } catch (error) {
    logger.error('Error during shutdown:', error);
    process.exit(1);
  }
};

// Start the server
const startServer = async () => {
  try {
    logger.info('Starting Grid-Guardian Backend...');
    logger.info('='.repeat(50));

    // Connect to MongoDB (optional for demo)
    const mongoConn = await connectMongo();
    if (mongoConn) {
      logger.info('[1/7] MongoDB connected');
    } else {
      logger.warn('[1/7] MongoDB unavailable (running in demo mode without telemetry storage)');
    }

    // Connect to PostgreSQL (optional for demo)
    const pgPool = await connectPostgres();
    if (pgPool) {
      logger.info('[2/7] PostgreSQL connected');
    } else {
      logger.warn('[2/7] PostgreSQL unavailable (running in demo mode without trade storage)');
    }

    // Connect to MQTT broker (optional for demo)
    const mqttConnected = await mqttClient.connect();
    if (mqttConnected) {
      setupMqttHandlers();
      logger.info('[3/7] MQTT client connected');
    } else {
      logger.warn('[3/7] MQTT unavailable (Pi telemetry via HTTP only)');
    }

    // Initialize blockchain integration services
    const blockchainReady = await blockchainWeb3Service.initialize();
    settlementService.initialize(io);
    logger.info(`[4/7] Blockchain services initialized (ready: ${blockchainReady})`);

    if (blockchainReady) {
      await eventListenerService.start();
      logger.info('[5/7] Blockchain event listener started');
    } else {
      logger.warn('[5/7] Blockchain services in degraded mode (web3 unavailable)');
    }

    // Initialize AI Trading Integration
    const aiIntegrationReady = initializeAITradingIntegration();
    logger.info(`[6/7] AI Trading Integration initialized (ready: ${aiIntegrationReady})`);

    // Initialize AI Streaming Service
    aiStreamingService.initialize({
      io: io,
      mqtt: mqttClient.getClient ? mqttClient.getClient() : null,
    });
    logger.info('[7/8] AI Streaming Service initialized');

    // Start AI health monitoring
    startAIHealthMonitor();
    logger.info('[8/8] AI health monitor started');

    // Start HTTP server
    httpServer.listen(PORT, () => {
      logger.info('='.repeat(50));
      logger.info(`Server running on port ${PORT}`);
      logger.info(`Environment: ${process.env.NODE_ENV || 'development'}`);
      logger.info(`AI Server URL: ${process.env.AI_SERVER_URL || 'http://127.0.0.1:5050'}`);
      logger.info('Grid-Guardian Backend is ready!');
      logger.info('='.repeat(50));
    });

    // Handle shutdown signals
    process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
    process.on('SIGINT', () => gracefulShutdown('SIGINT'));

    // Handle uncaught exceptions
    process.on('uncaughtException', (error) => {
      logger.error('Uncaught Exception:', error);
      gracefulShutdown('uncaughtException');
    });

    // Handle unhandled rejections
    process.on('unhandledRejection', (reason, promise) => {
      logger.error('Unhandled Rejection at:', promise, 'reason:', reason);
    });
  } catch (error) {
    logger.error('Failed to start server:', error);
    process.exit(1);
  }
};

// Start the application
startServer();
