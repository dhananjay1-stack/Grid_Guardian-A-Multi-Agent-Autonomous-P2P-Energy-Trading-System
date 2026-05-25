/**
 * Node management routes
 */
import { Router, Response } from 'express';
import { authMiddleware, AuthenticatedRequest, requireRole } from '../middleware/auth';
import { asyncHandler, AppError } from '../middleware/errorHandler';
import { blockchainService } from '../services/blockchain.service';

const router = Router();

// All node routes require authentication
router.use(authMiddleware);

/**
 * GET /nodes
 * List all registered nodes
 */
router.get('/', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const page = parseInt(req.query.page as string) || 1;
  const limit = parseInt(req.query.limit as string) || 20;

  // TODO: Query from database
  res.json({
    nodes: [],
    pagination: {
      page,
      limit,
      total: 0,
      pages: 0,
    },
  });
}));

/**
 * GET /nodes/:nodeId
 * Get node details
 */
router.get('/:nodeId', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { nodeId } = req.params;

  // Get on-chain data
  const onChainNode = await blockchainService.getNode(nodeId);

  if (!onChainNode) {
    throw new AppError('Node not found', 404);
  }

  // Get deposit balance
  let deposit = '0';
  try {
    deposit = await blockchainService.getDeposit(nodeId);
  } catch {
    // Collateral contract may not be configured
  }

  res.json({
    nodeId,
    onChain: onChainNode,
    deposit,
    // TODO: Add database fields (lastSeen, telemetry, etc.)
  });
}));

/**
 * POST /nodes/:nodeId/attest
 * Attest a node (admin only)
 */
router.post('/:nodeId/attest', requireRole('admin'), asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { nodeId } = req.params;

  const txHash = await blockchainService.attestNode(nodeId);

  res.json({
    success: true,
    nodeId,
    txHash,
    message: 'Node attested successfully',
  });
}));

/**
 * GET /nodes/:nodeId/telemetry
 * Get node telemetry history
 */
router.get('/:nodeId/telemetry', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { nodeId } = req.params;
  const limit = parseInt(req.query.limit as string) || 100;
  const since = req.query.since as string;

  // TODO: Query from database
  res.json({
    nodeId,
    telemetry: [],
    limit,
    since,
  });
}));

/**
 * GET /nodes/:nodeId/trades
 * Get node trade history
 */
router.get('/:nodeId/trades', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { nodeId } = req.params;
  const page = parseInt(req.query.page as string) || 1;
  const limit = parseInt(req.query.limit as string) || 20;

  // TODO: Query from database
  res.json({
    nodeId,
    trades: [],
    pagination: {
      page,
      limit,
      total: 0,
      pages: 0,
    },
  });
}));

/**
 * POST /nodes/:nodeId/command
 * Send command to a node
 */
router.post('/:nodeId/command', requireRole('admin', 'operator'), asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  const { nodeId } = req.params;
  const { command, params } = req.body;

  if (!command) {
    throw new AppError('Command is required', 400);
  }

  // TODO: Send via WebSocket/MQTT
  res.json({
    success: true,
    nodeId,
    command,
    params,
    message: 'Command queued',
  });
}));

export default router;
