const express = require('express');
const router = express.Router();
const systemController = require('../controllers/system.controller');
const { asyncHandler } = require('../middleware/error.middleware');

/**
 * @route   GET /api/system/health
 * @desc    Get system health status
 * @access  Public
 */
router.get(
  '/health',
  asyncHandler(systemController.getHealth)
);

/**
 * @route   GET /api/system/info
 * @desc    Get system information
 * @access  Public
 */
router.get(
  '/info',
  asyncHandler(systemController.getInfo)
);

/**
 * @route   POST /api/system/refresh
 * @desc    Trigger system data refresh
 * @access  Public
 */
router.post(
  '/refresh',
  asyncHandler(systemController.refresh)
);

module.exports = router;
