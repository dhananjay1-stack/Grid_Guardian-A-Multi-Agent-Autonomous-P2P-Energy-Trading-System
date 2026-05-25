const logger = require('../utils/logger');

// In-memory control state (in production, use Redis or database)
let controlState = {
  trading_enabled: true,
  manual_override: false,
  safe_mode: false,
  last_action: null,
};

const controlController = {
  /**
   * GET /api/control/state
   * Get current control state
   */
  async getState(req, res, next) {
    try {
      res.json({
        success: true,
        data: {
          ...controlState,
          timestamp: Date.now(),
        },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * POST /api/control/trading/enable
   * Enable trading
   */
  async enableTrading(req, res, next) {
    try {
      if (controlState.safe_mode) {
        return res.status(400).json({
          success: false,
          error: {
            message: 'Cannot enable trading while in safe mode',
          },
        });
      }

      controlState.trading_enabled = true;
      controlState.last_action = {
        action: 'TRADING_ENABLED',
        timestamp: Date.now(),
        user: req.user?.id || 'system',
      };

      logger.info('Trading enabled');

      // Emit to Socket.io
      const io = req.app.get('io');
      if (io) {
        io.emit('control:update', { trading_enabled: true });
      }

      res.json({
        success: true,
        message: 'Trading enabled successfully',
        data: { ...controlState },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * POST /api/control/trading/disable
   * Disable trading
   */
  async disableTrading(req, res, next) {
    try {
      controlState.trading_enabled = false;
      controlState.last_action = {
        action: 'TRADING_DISABLED',
        timestamp: Date.now(),
        user: req.user?.id || 'system',
      };

      logger.info('Trading disabled');

      // Emit to Socket.io
      const io = req.app.get('io');
      if (io) {
        io.emit('control:update', { trading_enabled: false });
      }

      res.json({
        success: true,
        message: 'Trading disabled successfully',
        data: { ...controlState },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * POST /api/control/manual-override
   * Toggle manual override mode
   */
  async setManualOverride(req, res, next) {
    try {
      const { enabled } = req.body;

      if (typeof enabled !== 'boolean') {
        return res.status(400).json({
          success: false,
          error: {
            message: 'Invalid request: enabled must be a boolean',
          },
        });
      }

      controlState.manual_override = enabled;
      controlState.last_action = {
        action: enabled ? 'MANUAL_OVERRIDE_ENABLED' : 'MANUAL_OVERRIDE_DISABLED',
        timestamp: Date.now(),
        user: req.user?.id || 'system',
      };

      logger.info(`Manual override ${enabled ? 'enabled' : 'disabled'}`);

      // Emit to Socket.io
      const io = req.app.get('io');
      if (io) {
        io.emit('control:update', { manual_override: enabled });
      }

      res.json({
        success: true,
        message: `Manual override ${enabled ? 'enabled' : 'disabled'} successfully`,
        data: { ...controlState },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * POST /api/control/safe-mode/enable
   * Enable safe mode (emergency stop)
   */
  async enableSafeMode(req, res, next) {
    try {
      controlState.safe_mode = true;
      controlState.trading_enabled = false; // Automatically disable trading
      controlState.last_action = {
        action: 'SAFE_MODE_ENABLED',
        timestamp: Date.now(),
        user: req.user?.id || 'system',
      };

      logger.warn('SAFE MODE ACTIVATED - All trading halted');

      // Emit to Socket.io
      const io = req.app.get('io');
      if (io) {
        io.emit('control:update', { safe_mode: true, trading_enabled: false });
        io.emit('alert', {
          id: `alert-${Date.now()}`,
          type: 'general',
          message: 'Safe mode activated - Trading halted',
          severity: 'critical',
          timestamp: Date.now(),
          acknowledged: false,
        });
      }

      res.json({
        success: true,
        message: 'Safe mode enabled - All trading halted',
        data: { ...controlState },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * POST /api/control/safe-mode/disable
   * Disable safe mode
   */
  async disableSafeMode(req, res, next) {
    try {
      controlState.safe_mode = false;
      controlState.last_action = {
        action: 'SAFE_MODE_DISABLED',
        timestamp: Date.now(),
        user: req.user?.id || 'system',
      };

      logger.info('Safe mode disabled');

      // Emit to Socket.io
      const io = req.app.get('io');
      if (io) {
        io.emit('control:update', { safe_mode: false });
      }

      res.json({
        success: true,
        message: 'Safe mode disabled successfully',
        data: { ...controlState },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Check if trading is allowed (utility function for other services)
   */
  isTradingAllowed() {
    return controlState.trading_enabled && !controlState.safe_mode;
  },

  /**
   * Get raw control state (utility function)
   */
  getControlState() {
    return { ...controlState };
  },
};

module.exports = controlController;
