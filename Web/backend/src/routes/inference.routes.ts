/**
 * AI Inference routes
 */
import { Router, Request, Response } from 'express';
import { authMiddleware, AuthenticatedRequest } from '../middleware/auth';
import { asyncHandler, AppError } from '../middleware/errorHandler';
import { inferenceService, Observation } from '../services/inference.service';
import { logger } from '../utils/logger';

const router = Router();

// Initialize inference service on first request
let initialized = false;
const ensureInitialized = async () => {
  if (!initialized) {
    try {
      await inferenceService.initialize();
      initialized = true;
    } catch (error) {
      logger.error('Failed to initialize inference service:', error);
    }
  }
};

/**
 * GET /inference/model-info
 * Get model metadata (no auth required)
 */
router.get('/model-info', asyncHandler(async (_req: Request, res: Response) => {
  await ensureInitialized();

  const modelInfo = inferenceService.getModelInfo();
  const isReady = inferenceService.isReady();

  res.json({
    ready: isReady,
    model: modelInfo,
    actions: {
      0: { name: 'charge_small', kw: 1.0 },
      1: { name: 'charge_large', kw: 3.0 },
      2: { name: 'idle', kw: 0.0 },
      3: { name: 'discharge_small', kw: -1.0 },
      4: { name: 'discharge_large', kw: -3.0 },
      5: { name: 'offer_sell', kw: -1.5 },
      6: { name: 'offer_hold', kw: 0.0 },
    },
  });
}));

/**
 * POST /inference/validate
 * Validate observation vector (no auth required)
 */
router.post('/validate', asyncHandler(async (req: Request, res: Response) => {
  const obs = req.body as Observation;
  const validation = inferenceService.validateObservation(obs);

  res.json(validation);
}));

// Protected routes below
router.use(authMiddleware);

/**
 * POST /inference/action
 * Get recommended action for observation
 */
router.post('/action', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  await ensureInitialized();

  if (!inferenceService.isReady()) {
    throw new AppError('Inference service not available', 503);
  }

  const obs = req.body.observation as Observation;
  const applySafety = req.body.applySafety !== false; // Default true

  if (!obs) {
    throw new AppError('Observation is required', 400);
  }

  const validation = inferenceService.validateObservation(obs);
  if (!validation.valid) {
    throw new AppError(`Invalid observation: ${validation.errors.join(', ')}`, 400);
  }

  const startTime = Date.now();
  const result = await inferenceService.infer(obs, applySafety);
  const inferenceTimeMs = Date.now() - startTime;

  res.json({
    ...result,
    inference_time_ms: inferenceTimeMs,
    observation: obs,
  });
}));

/**
 * POST /inference/batch
 * Batch inference for multiple observations
 */
router.post('/batch', asyncHandler(async (req: AuthenticatedRequest, res: Response) => {
  await ensureInitialized();

  if (!inferenceService.isReady()) {
    throw new AppError('Inference service not available', 503);
  }

  const observations = req.body.observations as Observation[];
  const applySafety = req.body.applySafety !== false;

  if (!observations || !Array.isArray(observations)) {
    throw new AppError('Observations array is required', 400);
  }

  if (observations.length > 100) {
    throw new AppError('Maximum 100 observations per batch', 400);
  }

  // Validate all observations
  for (let i = 0; i < observations.length; i++) {
    const validation = inferenceService.validateObservation(observations[i]);
    if (!validation.valid) {
      throw new AppError(`Invalid observation at index ${i}: ${validation.errors.join(', ')}`, 400);
    }
  }

  const startTime = Date.now();
  const results = await inferenceService.inferBatch(observations, applySafety);
  const totalTimeMs = Date.now() - startTime;

  res.json({
    results,
    count: results.length,
    total_time_ms: totalTimeMs,
    avg_time_per_obs_ms: totalTimeMs / results.length,
  });
}));

export default router;
