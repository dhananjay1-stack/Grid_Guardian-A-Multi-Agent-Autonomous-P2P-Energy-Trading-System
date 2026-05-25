/**
 * Grid-Guardian Unified Backend Layer
 *
 * Main entry point for the backend service.
 * Integrates Blockchain, AI Inference, and Hardware communication.
 */
import express from 'express';
import cors from 'cors';
import helmet from 'helmet';
import compression from 'compression';
import { createServer } from 'http';
import { config } from './config/env';
import { logger } from './utils/logger';
import { errorHandler } from './middleware/errorHandler';
import { rateLimiter } from './middleware/rateLimiter';
import routes from './routes';
import { WebSocketServer } from './websocket/server';
import { MqttBridge } from './websocket/mqtt-bridge';
import { initDatabase } from './config/database';

const app = express();
const httpServer = createServer(app);

// Middleware
app.use(helmet());
app.use(cors({
  origin: config.corsOrigins,
  credentials: true,
}));
app.use(compression());
app.use(express.json({ limit: '10mb' }));
app.use(express.urlencoded({ extended: true }));
app.use(rateLimiter);

// Health check (no auth required)
app.get('/health', (_req, res) => {
  res.json({
    status: 'ok',
    timestamp: new Date().toISOString(),
    version: '1.0.0',
    environment: config.nodeEnv,
  });
});

// API Routes
app.use('/api/v1', routes);

// Error handling
app.use(errorHandler);

// 404 handler
app.use((_req, res) => {
  res.status(404).json({ error: 'Not found' });
});

async function startServer() {
  try {
    // Initialize database
    await initDatabase();
    logger.info('Database connected');

    // Initialize WebSocket server
    const wsServer = new WebSocketServer(httpServer);
    logger.info('WebSocket server initialized');

    // Initialize MQTT bridge (connects Pi telemetry to WebSocket)
    if (config.mqttBrokerUrl) {
      const mqttBridge = new MqttBridge(wsServer);
      await mqttBridge.connect();
      logger.info('MQTT bridge connected');
    } else {
      logger.warn('MQTT_BROKER_URL not set - MQTT bridge disabled');
    }

    // Start HTTP server
    httpServer.listen(config.port, () => {
      logger.info(`Grid-Guardian Backend listening on port ${config.port}`);
      logger.info(`Environment: ${config.nodeEnv}`);
      logger.info(`API docs: http://localhost:${config.port}/api/v1`);
    });

  } catch (error) {
    logger.error('Failed to start server:', error);
    process.exit(1);
  }
}

// Graceful shutdown
process.on('SIGTERM', () => {
  logger.info('SIGTERM received, shutting down gracefully');
  httpServer.close(() => {
    logger.info('HTTP server closed');
    process.exit(0);
  });
});

process.on('SIGINT', () => {
  logger.info('SIGINT received, shutting down gracefully');
  httpServer.close(() => {
    logger.info('HTTP server closed');
    process.exit(0);
  });
});

startServer();

export { app, httpServer };
