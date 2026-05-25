const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const { createServer } = require('http');
const { Server } = require('socket.io');

// Import routes
const telemetryRoutes = require('./routes/telemetry.routes');
const dashboardRoutes = require('./routes/dashboard.routes');
const systemRoutes = require('./routes/system.routes');
const controlRoutes = require('./routes/control.routes');
const aiRoutes = require('./routes/ai.routes');
const blockchainRoutes = require('./routes/blockchain.routes');
const simulationRoutes = require('./routes/simulation.routes');
const demoRoutes = require('./routes/demo.routes');

// Import middleware
const { errorHandler, notFoundHandler } = require('./middleware/error.middleware');
const logger = require('./utils/logger');

// Create Express app
const app = express();
const httpServer = createServer(app);

// Initialize Socket.io
const io = new Server(httpServer, {
  cors: {
    origin: process.env.CORS_ORIGIN || '*',
    methods: ['GET', 'POST'],
  },
});

// Store io instance in app
app.set('io', io);

// Security middleware
app.use(helmet());

// CORS configuration
app.use(cors({
  origin: process.env.CORS_ORIGIN || '*',
  methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
  allowedHeaders: ['Content-Type', 'Authorization'],
}));

// Body parsing middleware
app.use(express.json({ limit: '1mb' }));
app.use(express.urlencoded({ extended: true }));

// Request logging middleware
app.use((req, res, next) => {
  const start = Date.now();
  res.on('finish', () => {
    const duration = Date.now() - start;
    logger.debug(`${req.method} ${req.originalUrl} ${res.statusCode} - ${duration}ms`);
  });
  next();
});

// Health check endpoint (root level)
app.get('/', (req, res) => {
  res.json({
    success: true,
    message: 'Grid-Guardian Backend API',
    version: '1.0.0',
    timestamp: Date.now(),
  });
});

// API routes
app.use('/api/telemetry', telemetryRoutes);
app.use('/api/dashboard', dashboardRoutes);
app.use('/api/system', systemRoutes);
app.use('/api/control', controlRoutes);
app.use('/api/ai', aiRoutes);
app.use('/api/blockchain', blockchainRoutes);
app.use('/api/simulation', simulationRoutes);
app.use('/api/demo', demoRoutes);

// 404 handler
app.use(notFoundHandler);

// Error handler
app.use(errorHandler);

// Socket.io connection handling
io.on('connection', (socket) => {
  logger.info(`Socket connected: ${socket.id}`);

  socket.on('subscribe', (nodeId) => {
    socket.join(`node:${nodeId}`);
    logger.debug(`Socket ${socket.id} subscribed to node:${nodeId}`);
  });

  socket.on('unsubscribe', (nodeId) => {
    socket.leave(`node:${nodeId}`);
    logger.debug(`Socket ${socket.id} unsubscribed from node:${nodeId}`);
  });

  socket.on('disconnect', () => {
    logger.info(`Socket disconnected: ${socket.id}`);
  });
});

// Export for server.js
module.exports = { app, httpServer, io };
