/**
 * Blockchain routes - chain status and contract info
 */
import { Router, Response } from 'express';
import { authMiddleware, AuthenticatedRequest } from '../middleware/auth';
import { asyncHandler } from '../middleware/errorHandler';
import { blockchainService } from '../services/blockchain.service';

const router = Router();

// All blockchain routes require authentication
router.use(authMiddleware);

/**
 * GET /blockchain/status
 * Chain connection status
 */
router.get('/status', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  const status = await blockchainService.getStatus();
  res.json(status);
}));

/**
 * GET /blockchain/contracts
 * Deployed contract addresses
 */
router.get('/contracts', asyncHandler(async (_req: AuthenticatedRequest, res: Response) => {
  const addresses = blockchainService.getContractAddresses();
  res.json(addresses);
}));

/**
 * GET /blockchain/node/:nodeId/balance
 * Get node collateral balance
 */
router.get('/node/:nodeId/balance', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { nodeId } = req.params;

  const [deposit, node] = await Promise.all([
    blockchainService.getDeposit(nodeId).catch(() => '0'),
    blockchainService.getNode(nodeId),
  ]);

  res.json({
    nodeId,
    deposit,
    stake: node?.stake || '0',
    active: node?.active || false,
  });
}));

/**
 * GET /blockchain/trade/:tradeId
 * Get on-chain trade state
 */
router.get('/trade/:tradeId', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { tradeId } = req.params;
  const trade = await blockchainService.getTrade(tradeId);

  if (!trade) {
    res.status(404).json({ error: 'Trade not found' });
    return;
  }

  res.json({
    tradeId,
    ...trade,
  });
}));

/**
 * GET /blockchain/events
 * Recent blockchain events
 */
router.get('/events', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const limit = parseInt(req.query.limit as string) || 50;

  // TODO: Query from database where events are indexed
  res.json({
    events: [],
    limit,
  });
}));

export default router;
