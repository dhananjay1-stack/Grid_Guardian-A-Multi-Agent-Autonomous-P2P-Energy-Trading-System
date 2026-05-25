const express = require('express');
const router = express.Router();
const telemetryController = require('../controllers/telemetry.controller');
const { validateTelemetry, validateNodeId, validatePagination } = require('../middleware/validate.middleware');
const { asyncHandler } = require('../middleware/error.middleware');

/**
 * @route   POST /api/telemetry
 * @desc    Submit telemetry data
 * @access  Public
 */
router.post(
  '/',
  validateTelemetry,
  asyncHandler(telemetryController.createTelemetry)
);

/**
 * @route   GET /api/telemetry/latest/:node_id
 * @desc    Get latest telemetry for a node
 * @access  Public
 */
router.get(
  '/latest/:node_id',
  validateNodeId,
  asyncHandler(telemetryController.getLatestTelemetry)
);

/**
 * @route   GET /api/telemetry/history/:node_id
 * @desc    Get telemetry history for a node
 * @access  Public
 */
router.get(
  '/history/:node_id',
  validateNodeId,
  validatePagination,
  asyncHandler(telemetryController.getTelemetryHistory)
);

module.exports = router;
