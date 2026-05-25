const express = require('express');
const router = express.Router();
const dashboardController = require('../controllers/dashboard.controller');
const { validateNodeId } = require('../middleware/validate.middleware');
const { asyncHandler } = require('../middleware/error.middleware');

/**
 * @route   GET /api/dashboard/summary
 * @desc    Get dashboard summary with all nodes
 * @access  Public
 */
router.get(
  '/summary',
  asyncHandler(dashboardController.getSummary)
);

/**
 * @route   GET /api/dashboard/node/:node_id
 * @desc    Get detailed dashboard for a specific node
 * @access  Public
 */
router.get(
  '/node/:node_id',
  validateNodeId,
  asyncHandler(dashboardController.getNodeDashboard)
);

module.exports = router;
