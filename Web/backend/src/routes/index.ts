/**
 * Main routes aggregator
 */
import { Router } from 'express';
import authRoutes from './auth.routes';
import dashboardRoutes from './dashboard.routes';
import nodesRoutes from './nodes.routes';
import tradesRoutes from './trades.routes';
import inferenceRoutes from './inference.routes';
import blockchainRoutes from './blockchain.routes';
import relayerRoutes from './relayer.routes';

const router = Router();

// Public routes
router.use('/auth', authRoutes);

// Protected routes (require authentication)
router.use('/dashboard', dashboardRoutes);
router.use('/nodes', nodesRoutes);
router.use('/trades', tradesRoutes);
router.use('/inference', inferenceRoutes);
router.use('/blockchain', blockchainRoutes);
router.use('/relayer', relayerRoutes);

export default router;
