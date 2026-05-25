const express = require('express');
const router = express.Router();
const controlController = require('../controllers/control.controller');
const { asyncHandler } = require('../middleware/error.middleware');

/**
 * @route   GET /api/control/state
 * @desc    Get current control state
 * @access  Public
 */
router.get(
  '/state',
  asyncHandler(controlController.getState)
);

/**
 * @route   POST /api/control/trading/enable
 * @desc    Enable trading
 * @access  Public (should be protected in production)
 */
router.post(
  '/trading/enable',
  asyncHandler(controlController.enableTrading)
);

/**
 * @route   POST /api/control/trading/disable
 * @desc    Disable trading
 * @access  Public (should be protected in production)
 */
router.post(
  '/trading/disable',
  asyncHandler(controlController.disableTrading)
);

/**
 * @route   POST /api/control/manual-override
 * @desc    Toggle manual override mode
 * @access  Public (should be protected in production)
 */
router.post(
  '/manual-override',
  asyncHandler(controlController.setManualOverride)
);

/**
 * @route   POST /api/control/safe-mode/enable
 * @desc    Enable safe mode (emergency stop)
 * @access  Public (should be protected in production)
 */
router.post(
  '/safe-mode/enable',
  asyncHandler(controlController.enableSafeMode)
);

/**
 * @route   POST /api/control/safe-mode/disable
 * @desc    Disable safe mode
 * @access  Public (should be protected in production)
 */
router.post(
  '/safe-mode/disable',
  asyncHandler(controlController.disableSafeMode)
);

module.exports = router;
