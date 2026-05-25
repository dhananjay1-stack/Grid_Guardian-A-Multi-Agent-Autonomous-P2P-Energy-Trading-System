/**
 * Simulation Controller
 *
 * Handles API endpoints for the virtual P2P trading simulation.
 */

const { getSimulationController } = require('../simulation');
const aiService = require('../services/ai.service');
const blockchainService = require('../services/blockchain.service');
const logger = require('../utils/logger');

// Get simulation instance
const simulation = getSimulationController();

// Set AI adapter for real AI decisions
simulation.setAIAdapter(aiService);

// Listen for trade events to log to blockchain
simulation.on('trade_settled', async (trade) => {
  try {
    await blockchainService.logEvent(
      trade.seller_id,
      'P2P_TRADE_SETTLED',
      {
        trade_id: trade.trade_id,
        seller_id: trade.seller_id,
        buyer_id: trade.buyer_id,
        quantity_kwh: trade.quantity_kwh,
        price_per_kwh: trade.price_per_kwh,
        total_price: trade.total_price,
      },
      trade.tx_hash
    );
  } catch (error) {
    logger.error('Failed to log P2P trade to blockchain:', error.message);
  }
});

const simulationController = {
  /**
   * Initialize simulation with prosumers
   * POST /api/simulation/initialize
   */
  async initialize(req, res, next) {
    try {
      const { prosumers } = req.body;
      const result = simulation.initialize(prosumers);
      res.json({
        success: true,
        message: 'Simulation initialized',
        ...result,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Start simulation
   * POST /api/simulation/start
   */
  async start(req, res, next) {
    try {
      const { speed } = req.body;
      if (speed) {
        simulation.setSpeed(speed);
      }
      const result = simulation.start();
      res.json({
        success: result.success,
        message: result.success ? 'Simulation started' : result.reason,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Stop simulation
   * POST /api/simulation/stop
   */
  async stop(req, res, next) {
    try {
      const result = simulation.stop();
      res.json({
        success: result.success,
        message: result.success ? 'Simulation stopped' : result.reason,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Run single simulation step
   * POST /api/simulation/step
   */
  async step(req, res, next) {
    try {
      const tick_data = await simulation.step();
      res.json({
        success: true,
        data: tick_data,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Get simulation state
   * GET /api/simulation/state
   */
  async getState(req, res, next) {
    try {
      const state = simulation.getState();
      res.json({
        success: true,
        data: state,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Reset simulation
   * POST /api/simulation/reset
   */
  async reset(req, res, next) {
    try {
      const result = simulation.reset();
      res.json({
        success: true,
        message: 'Simulation reset',
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Set simulation speed
   * POST /api/simulation/speed
   */
  async setSpeed(req, res, next) {
    try {
      const { speed } = req.body;
      if (!speed || speed < 1 || speed > 100) {
        return res.status(400).json({
          success: false,
          message: 'Speed must be between 1 and 100',
        });
      }
      const result = simulation.setSpeed(speed);
      res.json({
        success: true,
        message: `Speed set to ${speed}x`,
        ...result,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Set simulation hour
   * POST /api/simulation/time
   */
  async setTime(req, res, next) {
    try {
      const { hour } = req.body;
      if (hour === undefined || hour < 0 || hour > 24) {
        return res.status(400).json({
          success: false,
          message: 'Hour must be between 0 and 24',
        });
      }
      const result = simulation.setHour(hour);
      res.json({
        success: true,
        message: `Time set to ${hour}:00`,
        ...result,
      });
    } catch (error) {
      next(error);
    }
  },

  // ========================================
  // Prosumer Management
  // ========================================

  /**
   * Get all prosumers
   * GET /api/simulation/prosumers
   */
  async getProsumers(req, res, next) {
    try {
      const state = simulation.getState();
      res.json({
        success: true,
        data: state.prosumers,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Get single prosumer
   * GET /api/simulation/prosumers/:id
   */
  async getProsumer(req, res, next) {
    try {
      const { id } = req.params;
      const prosumer = simulation.getProsumer(id);
      if (!prosumer) {
        return res.status(404).json({
          success: false,
          message: 'Prosumer not found',
        });
      }
      res.json({
        success: true,
        data: prosumer,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Add prosumer
   * POST /api/simulation/prosumers
   */
  async addProsumer(req, res, next) {
    try {
      const { id, name, config } = req.body;
      if (!id || !name) {
        return res.status(400).json({
          success: false,
          message: 'ID and name are required',
        });
      }
      const result = simulation.addProsumer(id, name, config);
      if (!result.success) {
        return res.status(400).json(result);
      }
      res.json({
        success: true,
        message: 'Prosumer added',
        data: result.prosumer.getState(),
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Remove prosumer
   * DELETE /api/simulation/prosumers/:id
   */
  async removeProsumer(req, res, next) {
    try {
      const { id } = req.params;
      const result = simulation.removeProsumer(id);
      if (!result.success) {
        return res.status(404).json(result);
      }
      res.json({
        success: true,
        message: 'Prosumer removed',
      });
    } catch (error) {
      next(error);
    }
  },

  // ========================================
  // Market Operations
  // ========================================

  /**
   * Get market state
   * GET /api/simulation/market
   */
  async getMarket(req, res, next) {
    try {
      const state = simulation.getState();
      res.json({
        success: true,
        data: {
          market: state.market,
          order_book: state.order_book,
        },
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Get order book
   * GET /api/simulation/market/orderbook
   */
  async getOrderBook(req, res, next) {
    try {
      const state = simulation.getState();
      res.json({
        success: true,
        data: state.order_book,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Get recent trades
   * GET /api/simulation/market/trades
   */
  async getTrades(req, res, next) {
    try {
      const { limit = 20 } = req.query;
      const trades = simulation.market.getRecentTrades(parseInt(limit));
      res.json({
        success: true,
        data: trades,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Submit offer
   * POST /api/simulation/market/offer
   */
  async submitOffer(req, res, next) {
    try {
      const { prosumer_id, quantity_kwh, price_per_kwh } = req.body;

      const prosumer = simulation.prosumers.get(prosumer_id);
      if (!prosumer) {
        return res.status(404).json({
          success: false,
          message: 'Prosumer not found',
        });
      }

      const result = simulation.market.submitOffer(prosumer, quantity_kwh, price_per_kwh);
      if (!result.success) {
        return res.status(400).json(result);
      }

      res.json({
        success: true,
        message: 'Offer submitted',
        data: result.offer,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Submit bid
   * POST /api/simulation/market/bid
   */
  async submitBid(req, res, next) {
    try {
      const { prosumer_id, quantity_kwh, max_price_per_kwh } = req.body;

      const prosumer = simulation.prosumers.get(prosumer_id);
      if (!prosumer) {
        return res.status(404).json({
          success: false,
          message: 'Prosumer not found',
        });
      }

      const result = simulation.market.submitBid(prosumer, quantity_kwh, max_price_per_kwh);
      if (!result.success) {
        return res.status(400).json(result);
      }

      res.json({
        success: true,
        message: 'Bid submitted',
        data: result.bid,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Cancel order
   * DELETE /api/simulation/market/order/:id
   */
  async cancelOrder(req, res, next) {
    try {
      const { id } = req.params;
      const result = simulation.market.cancelOrder(id);
      if (!result.success) {
        return res.status(404).json(result);
      }
      res.json({
        success: true,
        message: 'Order cancelled',
        data: result,
      });
    } catch (error) {
      next(error);
    }
  },

  /**
   * Run market matching manually
   * POST /api/simulation/market/match
   */
  async runMatching(req, res, next) {
    try {
      const matches = simulation.market.runMatching();

      // Execute and settle matched trades
      const results = [];
      for (const trade of matches) {
        const exec_result = simulation.market.executeTrade(trade.trade_id, simulation.prosumers);
        if (exec_result.success) {
          simulation.market.markDelivered(trade.trade_id);
          const tx_hash = `0x${Date.now().toString(16)}${Math.random().toString(16).substring(2, 10)}`;
          simulation.market.settleTrade(trade.trade_id, tx_hash);
          results.push({ ...trade, execution: exec_result });
        }
      }

      res.json({
        success: true,
        message: `${matches.length} trades matched`,
        data: {
          matches: results,
          market: simulation.market.getMarketState(),
        },
      });
    } catch (error) {
      next(error);
    }
  },
};

module.exports = simulationController;
