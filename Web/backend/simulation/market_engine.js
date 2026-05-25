/**
 * Market Engine for Grid-Guardian P2P Trading
 *
 * Manages the P2P energy market:
 * - Order book for offers and bids
 * - Trade matching algorithm
 * - Price discovery
 * - Settlement coordination
 */

const { v4: uuidv4 } = require('uuid');
const logger = require('../utils/logger');

/**
 * Market configuration
 */
const DEFAULT_CONFIG = {
  // Matching rules
  matching_interval_ms: 5000,     // How often to run matching
  min_trade_kwh: 0.01,            // Minimum trade size (lowered for 5-min timestep quantities)
  max_trade_kwh: 10.0,            // Maximum single trade

  // Pricing (Indian Rupees)
  grid_buy_price: 8.00,            // Grid import price ₹/kWh
  grid_sell_price: 3.00,           // Grid export price ₹/kWh
  p2p_fee_percent: 1.0,           // Platform fee percentage

  // Order book
  max_orders_per_prosumer: 5,     // Max open orders
  order_expiry_ms: 15 * 60 * 1000, // 15 minutes
};

class MarketEngine {
  constructor(config = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };

    // Order books
    this.offers = new Map();  // offer_id -> offer
    this.bids = new Map();    // bid_id -> bid

    // Matched trades
    this.trades = new Map();  // trade_id -> trade
    this.completed_trades = [];

    // Market state
    this.current_price = 6.00;  // Current market price (₹/kWh)
    this.price_history = [];
    this.round_id = 0;

    // Statistics
    this.stats = {
      total_offers: 0,
      total_bids: 0,
      total_matches: 0,
      total_volume_kwh: 0,
      total_value: 0,
    };

    logger.info('Market engine initialized');
  }

  /**
   * Submit a sell offer to the market
   * @param {Object} prosumer - VirtualProsumer instance
   * @param {number} quantity_kwh - Energy to sell
   * @param {number} price_per_kwh - Asking price
   * @returns {Object} Offer result
   */
  submitOffer(prosumer, quantity_kwh, price_per_kwh) {
    // Validate quantity
    if (quantity_kwh < this.config.min_trade_kwh) {
      return { success: false, reason: 'quantity_too_small', min: this.config.min_trade_kwh };
    }
    if (quantity_kwh > this.config.max_trade_kwh) {
      return { success: false, reason: 'quantity_too_large', max: this.config.max_trade_kwh };
    }

    // Check prosumer has energy available
    const state = prosumer.getState();
    const available = state.surplus_energy_kwh + state.battery.available_discharge_kwh;
    if (quantity_kwh > available) {
      return { success: false, reason: 'insufficient_energy', available };
    }

    // Check max orders
    const prosumer_offers = this.getOffersByProsumer(prosumer.prosumer_id);
    if (prosumer_offers.length >= this.config.max_orders_per_prosumer) {
      return { success: false, reason: 'max_orders_reached' };
    }

    // Create offer
    const offer = {
      offer_id: `OFFER-${uuidv4().substring(0, 8).toUpperCase()}`,
      prosumer_id: prosumer.prosumer_id,
      prosumer_name: prosumer.name,
      type: 'sell',
      quantity_kwh,
      remaining_kwh: quantity_kwh,
      price_per_kwh,
      status: 'open',
      round_id: this.round_id,
      created_at: Date.now(),
      expires_at: Date.now() + this.config.order_expiry_ms,
      matched_trades: [],
    };

    this.offers.set(offer.offer_id, offer);
    this.stats.total_offers++;

    logger.info(`Offer submitted: ${offer.offer_id} - ${quantity_kwh}kWh @ ₹${price_per_kwh}/kWh`);

    return { success: true, offer };
  }

  /**
   * Submit a buy bid to the market
   * @param {Object} prosumer - VirtualProsumer instance
   * @param {number} quantity_kwh - Energy needed
   * @param {number} max_price_per_kwh - Maximum price willing to pay
   * @returns {Object} Bid result
   */
  submitBid(prosumer, quantity_kwh, max_price_per_kwh) {
    // Validate quantity
    if (quantity_kwh < this.config.min_trade_kwh) {
      return { success: false, reason: 'quantity_too_small', min: this.config.min_trade_kwh };
    }
    if (quantity_kwh > this.config.max_trade_kwh) {
      return { success: false, reason: 'quantity_too_large', max: this.config.max_trade_kwh };
    }

    // Check max orders
    const prosumer_bids = this.getBidsByProsumer(prosumer.prosumer_id);
    if (prosumer_bids.length >= this.config.max_orders_per_prosumer) {
      return { success: false, reason: 'max_orders_reached' };
    }

    // Create bid
    const bid = {
      bid_id: `BID-${uuidv4().substring(0, 8).toUpperCase()}`,
      prosumer_id: prosumer.prosumer_id,
      prosumer_name: prosumer.name,
      type: 'buy',
      quantity_kwh,
      remaining_kwh: quantity_kwh,
      max_price_per_kwh,
      status: 'open',
      round_id: this.round_id,
      created_at: Date.now(),
      expires_at: Date.now() + this.config.order_expiry_ms,
      matched_trades: [],
    };

    this.bids.set(bid.bid_id, bid);
    this.stats.total_bids++;

    logger.info(`Bid submitted: ${bid.bid_id} - ${quantity_kwh}kWh @ max ₹${max_price_per_kwh}/kWh`);

    return { success: true, bid };
  }

  /**
   * Cancel an order
   * @param {string} order_id - Offer or bid ID
   * @returns {Object} Cancellation result
   */
  cancelOrder(order_id) {
    if (this.offers.has(order_id)) {
      const offer = this.offers.get(order_id);
      offer.status = 'cancelled';
      offer.cancelled_at = Date.now();
      logger.info(`Offer cancelled: ${order_id}`);
      return { success: true, type: 'offer', order: offer };
    }

    if (this.bids.has(order_id)) {
      const bid = this.bids.get(order_id);
      bid.status = 'cancelled';
      bid.cancelled_at = Date.now();
      logger.info(`Bid cancelled: ${order_id}`);
      return { success: true, type: 'bid', order: bid };
    }

    return { success: false, reason: 'order_not_found' };
  }

  /**
   * Run the matching algorithm
   * Matches bids with offers based on price-time priority
   * @returns {Array} List of matched trades
   */
  runMatching() {
    const matches = [];
    const now = Date.now();

    // Clean expired orders first
    this.cleanExpiredOrders();

    // Get sorted order books
    // Offers sorted by price ascending (lowest first)
    const sorted_offers = Array.from(this.offers.values())
      .filter(o => o.status === 'open' && o.remaining_kwh > 0)
      .sort((a, b) => a.price_per_kwh - b.price_per_kwh || a.created_at - b.created_at);

    // Bids sorted by max_price descending (highest first)
    const sorted_bids = Array.from(this.bids.values())
      .filter(b => b.status === 'open' && b.remaining_kwh > 0)
      .sort((a, b) => b.max_price_per_kwh - a.max_price_per_kwh || a.created_at - b.created_at);

    // Match orders where bid.max_price >= offer.price
    for (const offer of sorted_offers) {
      if (offer.remaining_kwh <= 0) continue;

      for (const bid of sorted_bids) {
        if (bid.remaining_kwh <= 0) continue;
        if (bid.prosumer_id === offer.prosumer_id) continue; // No self-trading

        // Check price compatibility
        if (bid.max_price_per_kwh >= offer.price_per_kwh) {
          // Calculate match quantity
          const match_qty = Math.min(offer.remaining_kwh, bid.remaining_kwh);
          if (match_qty < this.config.min_trade_kwh) continue;

          // Determine trade price (midpoint or offer price)
          const trade_price = (offer.price_per_kwh + bid.max_price_per_kwh) / 2;

          // Create trade
          const trade = {
            trade_id: `TRADE-${uuidv4().substring(0, 8).toUpperCase()}`,
            offer_id: offer.offer_id,
            bid_id: bid.bid_id,
            seller_id: offer.prosumer_id,
            seller_name: offer.prosumer_name,
            buyer_id: bid.prosumer_id,
            buyer_name: bid.prosumer_name,
            quantity_kwh: match_qty,
            price_per_kwh: trade_price,
            total_price: match_qty * trade_price,
            fee: match_qty * trade_price * (this.config.p2p_fee_percent / 100),
            status: 'matched',
            round_id: this.round_id,
            matched_at: now,
          };

          // Update order quantities
          offer.remaining_kwh -= match_qty;
          bid.remaining_kwh -= match_qty;

          // Update order statuses
          if (offer.remaining_kwh <= 0) offer.status = 'filled';
          if (bid.remaining_kwh <= 0) bid.status = 'filled';

          // Track matched trades
          offer.matched_trades.push(trade.trade_id);
          bid.matched_trades.push(trade.trade_id);

          // Store trade
          this.trades.set(trade.trade_id, trade);
          matches.push(trade);

          // Update statistics
          this.stats.total_matches++;
          this.stats.total_volume_kwh += match_qty;
          this.stats.total_value += trade.total_price;

          logger.info(
            `Trade matched: ${trade.trade_id} - ` +
            `${trade.seller_name} -> ${trade.buyer_name}: ` +
            `${match_qty.toFixed(3)}kWh @ ₹${trade_price.toFixed(2)}/kWh`
          );
        }
      }
    }

    // Update market price based on recent trades
    if (matches.length > 0) {
      this.updateMarketPrice(matches);
    }

    return matches;
  }

  /**
   * Update market price based on recent trades
   * @param {Array} recent_trades - Recent matched trades
   */
  updateMarketPrice(recent_trades) {
    if (recent_trades.length === 0) return;

    // Volume-weighted average price
    let total_value = 0;
    let total_volume = 0;

    for (const trade of recent_trades) {
      total_value += trade.quantity_kwh * trade.price_per_kwh;
      total_volume += trade.quantity_kwh;
    }

    if (total_volume > 0) {
      const new_price = total_value / total_volume;
      this.current_price = new_price;
      this.price_history.push({
        timestamp: Date.now(),
        price: new_price,
        volume: total_volume,
        trades: recent_trades.length,
      });

      // Keep only last 100 price points
      if (this.price_history.length > 100) {
        this.price_history = this.price_history.slice(-100);
      }
    }
  }

  /**
   * Execute a matched trade (transfer energy)
   * @param {string} trade_id - Trade to execute
   * @param {Map} prosumers - Map of prosumer_id -> VirtualProsumer
   * @returns {Object} Execution result
   */
  executeTrade(trade_id, prosumers) {
    const trade = this.trades.get(trade_id);
    if (!trade) {
      return { success: false, reason: 'trade_not_found' };
    }
    if (trade.status !== 'matched') {
      return { success: false, reason: 'trade_not_in_matched_state', status: trade.status };
    }

    const seller = prosumers.get(trade.seller_id);
    const buyer = prosumers.get(trade.buyer_id);

    if (!seller || !buyer) {
      trade.status = 'failed';
      trade.failure_reason = 'prosumer_not_found';
      return { success: false, reason: 'prosumer_not_found' };
    }

    try {
      // Execute seller side
      const sale_result = seller.executeSale(trade.quantity_kwh, trade.price_per_kwh);
      if (!sale_result.success) {
        trade.status = 'failed';
        trade.failure_reason = 'seller_execution_failed';
        return { success: false, reason: 'seller_execution_failed' };
      }

      // Execute buyer side
      const purchase_result = buyer.executePurchase(trade.quantity_kwh, trade.price_per_kwh);
      if (!purchase_result.success) {
        trade.status = 'failed';
        trade.failure_reason = 'buyer_execution_failed';
        return { success: false, reason: 'buyer_execution_failed' };
      }

      // Update trade status
      trade.status = 'executed';
      trade.executed_at = Date.now();
      trade.seller_result = sale_result;
      trade.buyer_result = purchase_result;

      // Move to completed
      this.completed_trades.push(trade);

      logger.info(
        `Trade executed: ${trade_id} - ` +
        `${trade.quantity_kwh.toFixed(3)}kWh transferred from ${seller.name} to ${buyer.name}`
      );

      return {
        success: true,
        trade,
        seller_result: sale_result,
        buyer_result: purchase_result,
      };
    } catch (error) {
      trade.status = 'failed';
      trade.failure_reason = error.message;
      logger.error(`Trade execution failed: ${trade_id} - ${error.message}`);
      return { success: false, reason: error.message };
    }
  }

  /**
   * Mark trade as delivered (for blockchain settlement)
   * @param {string} trade_id - Trade ID
   * @returns {Object} Result
   */
  markDelivered(trade_id) {
    const trade = this.trades.get(trade_id);
    if (!trade) {
      return { success: false, reason: 'trade_not_found' };
    }
    if (trade.status !== 'executed') {
      return { success: false, reason: 'trade_not_executed' };
    }

    trade.status = 'delivered';
    trade.delivered_at = Date.now();

    logger.info(`Trade marked delivered: ${trade_id}`);
    return { success: true, trade };
  }

  /**
   * Mark trade as settled (blockchain confirmed)
   * @param {string} trade_id - Trade ID
   * @param {string} tx_hash - Blockchain transaction hash
   * @returns {Object} Result
   */
  settleTrade(trade_id, tx_hash) {
    const trade = this.trades.get(trade_id);
    if (!trade) {
      return { success: false, reason: 'trade_not_found' };
    }

    trade.status = 'settled';
    trade.settled_at = Date.now();
    trade.blockchain_tx_hash = tx_hash;

    logger.info(`Trade settled: ${trade_id} (tx: ${tx_hash})`);
    return { success: true, trade };
  }

  /**
   * Clean expired orders
   */
  cleanExpiredOrders() {
    const now = Date.now();

    for (const [id, offer] of this.offers) {
      if (offer.status === 'open' && offer.expires_at < now) {
        offer.status = 'expired';
        logger.debug(`Offer expired: ${id}`);
      }
    }

    for (const [id, bid] of this.bids) {
      if (bid.status === 'open' && bid.expires_at < now) {
        bid.status = 'expired';
        logger.debug(`Bid expired: ${id}`);
      }
    }
  }

  /**
   * Get market state
   */
  getMarketState() {
    const open_offers = Array.from(this.offers.values()).filter(o => o.status === 'open');
    const open_bids = Array.from(this.bids.values()).filter(b => b.status === 'open');
    const pending_trades = Array.from(this.trades.values()).filter(t =>
      t.status === 'matched' || t.status === 'executed' || t.status === 'delivered'
    );

    return {
      round_id: this.round_id,
      current_price: this.current_price,
      grid_buy_price: this.config.grid_buy_price,
      grid_sell_price: this.config.grid_sell_price,
      open_offers: open_offers.length,
      open_bids: open_bids.length,
      total_offer_volume: open_offers.reduce((sum, o) => sum + o.remaining_kwh, 0),
      total_bid_volume: open_bids.reduce((sum, b) => sum + b.remaining_kwh, 0),
      pending_trades: pending_trades.length,
      stats: this.stats,
      price_history: this.price_history.slice(-10),
    };
  }

  /**
   * Get order book (sorted offers and bids)
   */
  getOrderBook() {
    const offers = Array.from(this.offers.values())
      .filter(o => o.status === 'open')
      .sort((a, b) => a.price_per_kwh - b.price_per_kwh)
      .map(o => ({
        price: o.price_per_kwh,
        quantity: o.remaining_kwh,
        prosumer: o.prosumer_name,
        created_at: o.created_at,
      }));

    const bids = Array.from(this.bids.values())
      .filter(b => b.status === 'open')
      .sort((a, b) => b.max_price_per_kwh - a.max_price_per_kwh)
      .map(b => ({
        price: b.max_price_per_kwh,
        quantity: b.remaining_kwh,
        prosumer: b.prosumer_name,
        created_at: b.created_at,
      }));

    return { offers, bids };
  }

  /**
   * Get prosumer's offers
   */
  getOffersByProsumer(prosumer_id) {
    return Array.from(this.offers.values())
      .filter(o => o.prosumer_id === prosumer_id && o.status === 'open');
  }

  /**
   * Get prosumer's bids
   */
  getBidsByProsumer(prosumer_id) {
    return Array.from(this.bids.values())
      .filter(b => b.prosumer_id === prosumer_id && b.status === 'open');
  }

  /**
   * Get all trades for a prosumer
   */
  getTradesByProsumer(prosumer_id) {
    return Array.from(this.trades.values())
      .filter(t => t.seller_id === prosumer_id || t.buyer_id === prosumer_id)
      .sort((a, b) => b.matched_at - a.matched_at);
  }

  /**
   * Get recent trades
   */
  getRecentTrades(limit = 20) {
    return Array.from(this.trades.values())
      .sort((a, b) => b.matched_at - a.matched_at)
      .slice(0, limit);
  }

  /**
   * Advance to next market round
   */
  nextRound() {
    this.round_id++;
    logger.info(`Market round advanced to ${this.round_id}`);
    return this.round_id;
  }

  /**
   * Reset market (for testing)
   */
  reset() {
    this.offers.clear();
    this.bids.clear();
    this.trades.clear();
    this.completed_trades = [];
    this.current_price = 6.00;
    this.price_history = [];
    this.round_id = 0;
    this.stats = {
      total_offers: 0,
      total_bids: 0,
      total_matches: 0,
      total_volume_kwh: 0,
      total_value: 0,
    };
    logger.info('Market engine reset');
  }
}

module.exports = MarketEngine;
