/**
 * Relayer routes - meta-transaction endpoints
 */
import { Router, Request, Response } from 'express';
import { ethers } from 'ethers';
import { asyncHandler, AppError } from '../middleware/errorHandler';
import { blockchainService } from '../services/blockchain.service';
import { config } from '../config/env';
import { logger } from '../utils/logger';

const router = Router();

// EIP-712 domain for registration
function getRegistrationDomain() {
  return {
    name: 'GridGuardian-Relayer',
    version: '1',
    chainId: config.chainId,
    verifyingContract: config.contracts.identity,
  };
}

// EIP-712 domain for delivery
function getDeliveryDomain() {
  return {
    name: 'GridGuardian-Delivery',
    version: '1',
    chainId: config.chainId,
    verifyingContract: config.contracts.deliveryRegistry,
  };
}

const REGISTER_TYPES = {
  RegisterNode: [
    { name: 'nodeId', type: 'bytes32' },
    { name: 'pubkeyHash', type: 'bytes32' },
    { name: 'metaURI', type: 'string' },
    { name: 'nonce', type: 'uint256' },
    { name: 'expiry', type: 'uint256' },
  ],
};

const DELIVERY_TYPES = {
  DeliveryReceipt: [
    { name: 'tradeId', type: 'bytes32' },
    { name: 'nodeId', type: 'bytes32' },
    { name: 'meterSnapshotHash', type: 'bytes32' },
    { name: 'deliveredKwhBucket', type: 'uint16' },
    { name: 'periodStart', type: 'uint256' },
    { name: 'periodEnd', type: 'uint256' },
    { name: 'nonce', type: 'uint256' },
  ],
};

/**
 * GET /relayer/health
 * Relayer health check
 */
router.get('/health', asyncHandler(async (_req: Request, res: Response) => {
  const status = await blockchainService.getStatus();
  res.json({
    status: status.connected ? 'ok' : 'degraded',
    relayer: status.relayerAddress,
    balance: status.relayerBalance,
    chainId: status.chainId,
    blockNumber: status.blockNumber,
  });
}));

/**
 * GET /relayer/nonce/:address
 * Get nonce for address
 */
router.get('/nonce/:address', asyncHandler(async (req: Request, res: Response) => {
  const { address } = req.params;

  if (!ethers.isAddress(address)) {
    throw new AppError('Invalid address', 400);
  }

  const nonce = await blockchainService.getNonce(address);
  res.json({ nonce });
}));

/**
 * POST /relayer/register
 * Register node via meta-transaction
 */
router.post('/register', asyncHandler(async (req: Request, res: Response) => {
  const { nodeId, pubkeyHash, metaURI, nonce, expiry, signature, signer } = req.body;

  // Validate required fields
  if (!nodeId || !pubkeyHash || !metaURI || signature === undefined || !signer) {
    throw new AppError('Missing required fields', 400);
  }

  // Verify EIP-712 signature
  const message = { nodeId, pubkeyHash, metaURI, nonce, expiry };
  const recovered = ethers.verifyTypedData(
    getRegistrationDomain(),
    REGISTER_TYPES,
    message,
    signature
  );

  if (recovered.toLowerCase() !== signer.toLowerCase()) {
    throw new AppError(`Invalid signature. Expected ${signer}, recovered ${recovered}`, 403);
  }

  logger.info(`Registration verified for ${signer}`);

  // Submit on-chain
  const txHash = await blockchainService.registerNodeMeta(
    nodeId,
    pubkeyHash,
    metaURI,
    nonce,
    expiry,
    signature
  );

  res.json({
    success: true,
    txHash,
    nodeId,
    owner: signer,
  });
}));

/**
 * POST /relayer/commit
 * Post offer commit via relayer
 */
router.post('/commit', asyncHandler(async (req: Request, res: Response) => {
  const { nodeId, commitHash, roundId, expiryBlock, signature, signer } = req.body;

  // Validate required fields
  if (!nodeId || !commitHash || !signature || !signer) {
    throw new AppError('Missing required fields', 400);
  }

  // TODO: Implement commit posting via TradingSC
  // For now, return a placeholder response
  res.json({
    success: true,
    message: 'Commit posting not yet implemented',
    nodeId,
    commitHash,
    roundId,
    expiryBlock,
  });
}));

/**
 * POST /relayer/delivery
 * Submit delivery receipt via meta-transaction
 */
router.post('/delivery', asyncHandler(async (req: Request, res: Response) => {
  const {
    tradeId,
    nodeId,
    meterSnapshotHash,
    deliveredKwhBucket,
    periodStart,
    periodEnd,
    nonce,
    signature,
    signer,
  } = req.body;

  // Validate required fields
  if (!tradeId || !nodeId || !meterSnapshotHash || !signature || !signer) {
    throw new AppError('Missing required fields', 400);
  }

  // Verify EIP-712 signature
  const message = {
    tradeId,
    nodeId,
    meterSnapshotHash,
    deliveredKwhBucket,
    periodStart,
    periodEnd,
    nonce,
  };
  const recovered = ethers.verifyTypedData(
    getDeliveryDomain(),
    DELIVERY_TYPES,
    message,
    signature
  );

  if (recovered.toLowerCase() !== signer.toLowerCase()) {
    throw new AppError(`Invalid signature. Expected ${signer}, recovered ${recovered}`, 403);
  }

  logger.info(`Delivery receipt verified for trade ${tradeId}`);

  // Submit on-chain
  const txHash = await blockchainService.submitDeliveryReceipt(
    tradeId,
    nodeId,
    meterSnapshotHash,
    deliveredKwhBucket,
    periodStart,
    periodEnd,
    nonce,
    signature
  );

  res.json({
    success: true,
    txHash,
    tradeId,
    nodeId,
  });
}));

/**
 * POST /relayer/voucher
 * Submit gas voucher for claim
 */
router.post('/voucher', asyncHandler(async (req: Request, res: Response) => {
  const { voucher, signature, signer } = req.body;

  if (!voucher || !signature) {
    throw new AppError('Missing voucher or signature', 400);
  }

  // TODO: Implement voucher claiming via CollateralSC
  res.json({
    success: true,
    message: 'Voucher claiming not yet implemented',
    voucher,
  });
}));

export default router;
