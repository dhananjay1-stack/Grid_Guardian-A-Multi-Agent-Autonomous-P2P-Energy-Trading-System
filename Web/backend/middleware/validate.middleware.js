const logger = require('../utils/logger');

/**
 * Validate telemetry payload
 */
const validateTelemetry = (req, res, next) => {
  const { node_id, voltage, current, power, timestamp } = req.body;
  const errors = [];

  if (!node_id || typeof node_id !== 'string' || node_id.trim() === '') {
    errors.push('node_id is required and must be a non-empty string');
  }

  if (voltage === undefined || typeof voltage !== 'number' || voltage < 0) {
    errors.push('voltage is required and must be a non-negative number');
  }

  if (current === undefined || typeof current !== 'number' || current < 0) {
    errors.push('current is required and must be a non-negative number');
  }

  if (power === undefined || typeof power !== 'number' || power < 0) {
    errors.push('power is required and must be a non-negative number');
  }

  if (timestamp === undefined || typeof timestamp !== 'number') {
    errors.push('timestamp is required and must be a number');
  }

  if (errors.length > 0) {
    logger.warn('Telemetry validation failed:', errors);
    return res.status(400).json({
      success: false,
      error: {
        message: 'Validation failed',
        details: errors,
      },
    });
  }

  next();
};

/**
 * Validate node_id parameter
 */
const validateNodeId = (req, res, next) => {
  const { node_id } = req.params;

  if (!node_id || typeof node_id !== 'string' || node_id.trim() === '') {
    logger.warn('Invalid node_id parameter');
    return res.status(400).json({
      success: false,
      error: {
        message: 'node_id parameter is required and must be a non-empty string',
      },
    });
  }

  // Sanitize node_id (basic alphanumeric and dash/underscore)
  if (!/^[a-zA-Z0-9_-]+$/.test(node_id)) {
    return res.status(400).json({
      success: false,
      error: {
        message: 'node_id must contain only alphanumeric characters, dashes, and underscores',
      },
    });
  }

  next();
};

/**
 * Validate pagination parameters
 */
const validatePagination = (req, res, next) => {
  let { limit, skip, page } = req.query;

  // Parse and validate limit
  if (limit !== undefined) {
    limit = parseInt(limit, 10);
    if (isNaN(limit) || limit < 1 || limit > 1000) {
      return res.status(400).json({
        success: false,
        error: {
          message: 'limit must be a number between 1 and 1000',
        },
      });
    }
    req.query.limit = limit;
  } else {
    req.query.limit = 100; // Default limit
  }

  // Parse and validate skip
  if (skip !== undefined) {
    skip = parseInt(skip, 10);
    if (isNaN(skip) || skip < 0) {
      return res.status(400).json({
        success: false,
        error: {
          message: 'skip must be a non-negative number',
        },
      });
    }
    req.query.skip = skip;
  } else {
    req.query.skip = 0;
  }

  // Calculate skip from page if provided
  if (page !== undefined) {
    page = parseInt(page, 10);
    if (isNaN(page) || page < 1) {
      return res.status(400).json({
        success: false,
        error: {
          message: 'page must be a positive number',
        },
      });
    }
    req.query.skip = (page - 1) * req.query.limit;
  }

  next();
};

/**
 * General JSON body validator
 */
const validateJsonBody = (req, res, next) => {
  if (req.method !== 'GET' && req.method !== 'DELETE') {
    if (!req.body || Object.keys(req.body).length === 0) {
      return res.status(400).json({
        success: false,
        error: {
          message: 'Request body cannot be empty',
        },
      });
    }
  }
  next();
};

module.exports = {
  validateTelemetry,
  validateNodeId,
  validatePagination,
  validateJsonBody,
};
