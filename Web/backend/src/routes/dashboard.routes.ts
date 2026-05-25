/**
 * Dashboard routes - overview and metrics
 */
import { Router, Response } from 'express';
import { authMiddleware, AuthenticatedRequest } from '../middleware/auth';
import { asyncHandler } from '../middleware/errorHandler';
import { blockchainService } from '../services/blockchain.service';

const router = Router();

// All dashboard routes require authentication
router.use(authMiddleware);

/**
 * GET /dashboard/overview
 * System overview statistics
 */
router.get('/overview', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  const blockchainStatus = await blockchainService.getStatus();

  res.json({
    system: {
      status: 'operational',
      uptime: process.uptime(),
      timestamp: new Date().toISOString(),
    },
    blockchain: blockchainStatus,
    nodes: {
      total: 0, // TODO: Query from database
      active: 0,
      attested: 0,
    },
    trades: {
      total: 0,
      active: 0,
      settled: 0,
      volume24h: '0',
    },
    energy: {
      traded24h: '0 kWh',
      avgPrice: '0.10 $/kWh',
    },
  });
}));

/**
 * GET /dashboard/metrics
 * Real-time system metrics
 */
router.get('/metrics', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  const memUsage = process.memoryUsage();

  res.json({
    timestamp: new Date().toISOString(),
    process: {
      uptime: process.uptime(),
      memory: {
        rss: Math.round(memUsage.rss / 1024 / 1024),
        heapUsed: Math.round(memUsage.heapUsed / 1024 / 1024),
        heapTotal: Math.round(memUsage.heapTotal / 1024 / 1024),
        external: Math.round(memUsage.external / 1024 / 1024),
      },
    },
    websocket: {
      connections: 0, // TODO: Get from WebSocket server
      messagesPerSecond: 0,
    },
    api: {
      requestsPerMinute: 0, // TODO: Track metrics
      avgResponseTimeMs: 0,
    },
  });
}));

/**
 * GET /dashboard/alerts
 * Active alerts and warnings
 */
router.get('/alerts', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  // TODO: Implement alerting system
  res.json({
    alerts: [],
    warnings: [],
    timestamp: new Date().toISOString(),
  });
}));

/**
 * GET /dashboard/charts/trades
 * Trade volume chart data
 */
router.get('/charts/trades', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  // TODO: Query from database with time series aggregation
  res.json({
    period: '24h',
    interval: '1h',
    data: [],
  });
}));

/**
 * GET /dashboard/charts/energy
 * Energy flow chart data
 */
router.get('/charts/energy', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  // TODO: Query from database with time series aggregation
  res.json({
    period: '24h',
    interval: '1h',
    data: [],
  });
}));

export default router;
