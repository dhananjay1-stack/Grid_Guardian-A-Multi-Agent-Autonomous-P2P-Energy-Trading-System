/**
 * Trade routes
 */
import { Router, Response } from 'express';
import { authMiddleware, AuthenticatedRequest } from '../middleware/auth';
import { asyncHandler, AppError } from '../middleware/errorHandler';
import { blockchainService } from '../services/blockchain.service';

const router = Router();

// All trade routes require authentication
router.use(authMiddleware);

// Trade status mapping
const TRADE_STATUS = {
  0: 'None',
  1: 'Locked',
  2: 'Delivered',
  3: 'Settled',
  4: 'Disputed',
  5: 'Refunded',
};

/**
 * GET /trades
 * List all trades (paginated)
 */
router.get('/', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const page = parseInt(req.query.page as string) || 1;
  const limit = parseInt(req.query.limit as string) || 20;
  const status = req.query.status as string;

  // TODO: Query from database
  res.json({
    trades: [],
    pagination: {
      page,
      limit,
      total: 0,
      pages: 0,
    },
    filter: { status },
  });
}));

/**
 * GET /trades/active
 * Get active/pending trades
 */
router.get('/active', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  // TODO: Query from database where status in (Locked, Delivered, Disputed)
  res.json({
    trades: [],
  });
}));

/**
 * GET /trades/stats
 * Trade statistics
 */
router.get('/stats', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const period = req.query.period as string || '24h';

  // TODO: Query from database with aggregation
  res.json({
    period,
    totalTrades: 0,
    totalVolume: '0',
    avgPrice: '0',
    successRate: 0,
    disputeRate: 0,
    breakdown: {
      settled: 0,
      refunded: 0,
      disputed: 0,
    },
  });
}));

/**
 * GET /trades/:tradeId
 * Get trade details
 */
router.get('/:tradeId', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { tradeId } = req.params;

  // Get on-chain data
  const onChainTrade = await blockchainService.getTrade(tradeId);

  if (!onChainTrade) {
    throw new AppError('Trade not found', 404);
  }

  // Get delivery receipt count
  let receiptCount = 0;
  try {
    receiptCount = await blockchainService.getReceiptCount(tradeId);
  } catch {
    // Delivery contract may not be configured
  }

  res.json({
    tradeId,
    ...onChainTrade,
    statusName: TRADE_STATUS[onChainTrade.status as keyof typeof TRADE_STATUS] || 'Unknown',
    receiptCount,
    // TODO: Add database fields (timestamps, events, etc.)
  });
}));

/**
 * GET /trades/:tradeId/events
 * Get trade event history
 */
router.get('/:tradeId/events', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { tradeId } = req.params;

  // TODO: Query from audit_logs table
  res.json({
    tradeId,
    events: [],
  });
}));

export default router;
