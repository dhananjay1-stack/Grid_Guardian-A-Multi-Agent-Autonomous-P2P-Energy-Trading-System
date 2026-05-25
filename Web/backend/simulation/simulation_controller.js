/**
 * P2P Trading Simulation Controller
 *
 * Orchestrates the virtual P2P trading simulation:
 * - Manages multiple prosumers
 * - Runs simulation timesteps
 * - Integrates AI decision making
 * - Coordinates market matching
 * - Emits events for dashboard/blockchain
 */

const VirtualProsumer = require('./virtual_prosumer');
const MarketEngine = require('./market_engine');
const logger = require('../utils/logger');
const EventEmitter = require('events');

/**
 * Simulation configuration
 */
const DEFAULT_CONFIG = {
  timestep_minutes: 5,           // Simulation timestep
  simulation_speed: 1,           // 1 = real-time, 10 = 10x speed
  auto_matching: true,           // Auto-run matching each timestep
  auto_settlement: true,         // Auto-settle executed trades

  // Default prosumers if none provided
  default_prosumers: [
    {
      id: 'prosumer-a',
      name: 'Solar Home A',
      config: {
        solar_capacity_kw: 6.0,
        battery: { capacity_kwh: 5.0, initial_soc: 0.7 },
        load_profile: 'residential',
      },
    },
    {
      id: 'prosumer-b',
      name: 'Neighbor B',
      config: {
        solar_capacity_kw: 3.0,
        base_load_kw: 1.5,
        peak_load_kw: 4.0,
        battery: { capacity_kwh: 3.0, initial_soc: 0.3 },
        load_profile: 'residential',
      },
    },
    {
      id: 'prosumer-c',
      name: 'Consumer C',
      config: {
        solar_capacity_kw: 0.0,    // Pure consumer — no solar
        base_load_kw: 1.0,
        peak_load_kw: 3.5,
        battery: { capacity_kwh: 4.0, initial_soc: 0.4 },
        load_profile: 'residential',
      },
    },
  ],
};

class SimulationController extends EventEmitter {
  constructor(config = {}) {
    super();
    this.config = { ...DEFAULT_CONFIG, ...config };

    // Prosumer management
    this.prosumers = new Map();

    // Market engine
    this.market = new MarketEngine();

    // Simulation state
    this.is_running = false;
    this.current_time = Date.now();
    this.simulation_hour = 6;  // Start at 6 AM — shows sunrise transition
    this.simulation_day = 0;
    this.tick_count = 0;
    this.interval_id = null;

    // AI adapter (will be set externally)
    this.ai_adapter = null;

    // Cached AI decisions per prosumer (updated each tick, read by dashboard)
    this.lastDecisions = new Map();

    // Blockchain service (set externally for real on-chain settlement)
    this.blockchainService = null;

    // Statistics
    this.stats = {
      ticks: 0,
      total_solar_kwh: 0,
      total_load_kwh: 0,
      total_p2p_volume: 0,
      total_grid_import: 0,
      total_grid_export: 0,
    };

    logger.info('Simulation controller initialized');
  }

  /**
   * Initialize simulation with default or provided prosumers
   * @param {Array} prosumer_configs - Optional prosumer configurations
   */
  initialize(prosumer_configs = null) {
    const configs = prosumer_configs || this.config.default_prosumers;

    for (const pc of configs) {
      const prosumer = new VirtualProsumer(pc.id, pc.name, pc.config);
      this.prosumers.set(prosumer.prosumer_id, prosumer);
      logger.info(`Prosumer added: ${prosumer.prosumer_id} (${prosumer.name})`);
    }

    this.emit('initialized', {
      prosumer_count: this.prosumers.size,
      prosumers: Array.from(this.prosumers.values()).map(p => ({
        id: p.prosumer_id,
        name: p.name,
      })),
    });

    return { success: true, prosumer_count: this.prosumers.size };
  }

  /**
   * Add a prosumer to the simulation
   * @param {string} id - Prosumer ID
   * @param {string} name - Display name
   * @param {Object} config - Prosumer config
   */
  addProsumer(id, name, config = {}) {
    if (this.prosumers.has(id)) {
      return { success: false, reason: 'prosumer_exists' };
    }

    const prosumer = new VirtualProsumer(id, name, config);
    this.prosumers.set(prosumer.prosumer_id, prosumer);

    this.emit('prosumer_added', { prosumer_id: prosumer.prosumer_id, name });
    return { success: true, prosumer };
  }

  /**
   * Remove a prosumer from the simulation
   * @param {string} prosumer_id - Prosumer to remove
   */
  removeProsumer(prosumer_id) {
    if (!this.prosumers.has(prosumer_id)) {
      return { success: false, reason: 'prosumer_not_found' };
    }

    this.prosumers.delete(prosumer_id);
    this.emit('prosumer_removed', { prosumer_id });
    return { success: true };
  }

  /**
   * Set AI adapter for decision making
   * @param {Object} adapter - AI adapter with decide(prosumer_state) method
   */
  setAIAdapter(adapter) {
    this.ai_adapter = adapter;
    logger.info('AI adapter set');
  }

  /**
   * Set blockchain service for real on-chain settlement
   * @param {Object} service - blockchainWeb3.service instance
   */
  setBlockchainService(service) {
    this.blockchainService = service;
    logger.info('Blockchain service set for real settlement');
  }

  /**
   * Get AI decision for a prosumer
   * @param {Object} prosumer - VirtualProsumer instance
   * @returns {Object} AI decision
   */
  async getAIDecision(prosumer) {
    if (!this.ai_adapter) {
      // Default rule-based fallback
      return this.ruleBasedDecision(prosumer);
    }

    try {
      const observation = prosumer.getAIObservation(this.market.current_price);
      const state = prosumer.getState();

      const decision = await this.ai_adapter.processAndDecide(
        prosumer.prosumer_id,
        {
          voltage: observation.voltage_v,
          current: observation.current_a,
          power: observation.load_kw * 1000,
        },
        {
          soc_kwh: observation.soc_kwh,
          soc_capacity_kwh: observation.soc_capacity_kwh,
          pv_gen_kw: observation.pv_gen_kw,
          load_kw: observation.load_kw,
          net_kw: observation.net_kw,
          battery_power_kw: observation.battery_power_kw,
          grid_price: this.market.current_price,
          forecast_irradiance_1h: observation.forecast_irradiance_1h,
          forecast_irradiance_3h: observation.forecast_irradiance_3h,
          forecast_temp_1h: observation.forecast_temp_1h,
          actual_irradiance_wm2: observation.actual_irradiance_wm2,
        }
      );

      return {
        action: decision.decision || 'HOLD',
        action_kw: decision.action_kw || 0,
        confidence: decision.confidence || 0.5,
        trade_action: decision.trade_action,
        source: decision.is_mock ? 'rule_based' : 'ai',
        // AI metadata for dashboard and traceability
        selected_policy: decision.selected_model || 'CQL',
        condition: decision.condition || 'normal',
        policy_reason: decision.condition_reason || '',
        volatility: decision.volatility || 0,
        condition_confidence: decision.condition_confidence || 0.8,
        sub_conditions: decision.sub_conditions || [],
        action_name: decision.action_name || '',
      };
    } catch (error) {
      logger.warn(`AI decision failed for ${prosumer.prosumer_id}: ${error.message}`);
      return this.ruleBasedDecision(prosumer);
    }
  }

  /**
   * Rule-based fallback decision
   * @param {Object} prosumer - VirtualProsumer instance
   */
  ruleBasedDecision(prosumer) {
    const state = prosumer.getState();
    const battery = state.battery;
    const price = this.market.current_price;

    let action = 'hold';
    let action_kw = 0;
    let trade_action = null;

    if (state.net_power_kw > 0.3) {
      // Surplus energy available
      if (battery.soc >= 0.5) {
        // Battery reasonably charged — sell surplus
        action = 'sell_surplus';
        trade_action = 'SELL';
      } else {
        // Battery needs charge — charge first, but also sell if large surplus
        action = 'charge_battery';
        action_kw = Math.min(state.net_power_kw * 0.6, 3.0);
        if (state.net_power_kw > 1.0 && battery.soc >= 0.3) {
          trade_action = 'SELL';
        }
      }
    } else if (state.net_power_kw < -0.3) {
      // Deficit — need energy
      if (battery.soc <= 0.6) {
        // Battery low enough — buy energy
        action = 'buy_energy';
        trade_action = 'BUY';
      } else if (battery.soc > 0.6) {
        // Battery has charge — discharge first
        action = 'discharge_battery';
        action_kw = Math.max(state.net_power_kw, -3.0);
        if (battery.soc <= 0.4) {
          trade_action = 'BUY';
        }
      }
    }

    const netPower = state.net_power_kw;
    let condition = 'normal';
    if (netPower > 1.0) condition = 'high_pv';
    else if (netPower < -1.0) condition = 'high_load';
    if (battery.soc < 0.2) condition = 'low_soc';
    else if (battery.soc > 0.9) condition = 'high_soc';

    return {
      action,
      action_kw,
      confidence: 0.7,
      trade_action,
      source: 'rule_based',
      selected_policy: 'FALLBACK',
      condition,
      policy_reason: 'AI server unavailable - rule-based fallback',
      volatility: 0,
      condition_confidence: 0.6,
      sub_conditions: [],
      action_name: action,
    };
  }

  /**
   * Run a single simulation tick
   */
  async tick() {
    this.tick_count++;
    const timestep_hours = this.config.timestep_minutes / 60;

    // Calculate cloud factor (varies through day)
    const cloud_factor = Math.random() * 0.3;  // 0-30% random clouds

    const tick_data = {
      tick: this.tick_count,
      hour: this.simulation_hour,
      day: this.simulation_day,
      timestamp: this.current_time,
      prosumers: [],
      trades: [],
      market: null,
    };

    // Process each prosumer
    for (const [id, prosumer] of this.prosumers) {
      // 1. Calculate energy balance
      const balance = prosumer.calculateEnergyBalance(
        this.simulation_hour,
        timestep_hours,
        cloud_factor
      );

      // 2. Get AI decision
      const decision = await this.getAIDecision(prosumer);

      // Cache the decision for dashboard to read (single source of truth)
      this.lastDecisions.set(id, {
        ...decision,
        timestamp: Date.now(),
        tick: this.tick_count,
        prosumer_state: {
          net_power_kw: prosumer.net_power_kw,
          solar_kw: prosumer.solar_generation_kw,
          load_kw: prosumer.load_demand_kw,
          soc: prosumer.battery?.soc || 0.5,
          soc_kwh: prosumer.battery?.soc_kwh || 2.0,
        },
      });

      // 3. Process energy (battery/grid interaction)
      const energy_result = prosumer.processEnergy(
        decision.action,
        decision.action_kw,
        timestep_hours
      );

      // 4. Handle trading actions
      // Supplementary trading logic: if AI says HOLD but prosumer has clear surplus/deficit,
      // force a trade action so the market stays active
      let effectiveTradeAction = decision.trade_action;

      if (!effectiveTradeAction || effectiveTradeAction === 'HOLD') {
        const pState = prosumer.getState();
        const soc = pState.battery?.soc || 0.5;
        logger.debug(`[SUPP-TRADE] ${id}: trade_action=${decision.trade_action}, net=${pState.net_power_kw?.toFixed(2)}, soc=${soc?.toFixed(3)}`);
        if (pState.net_power_kw > 0.3 && soc > 0.40) {
          effectiveTradeAction = 'SELL';
        } else if (pState.net_power_kw < -0.3 && soc < 0.70) {
          effectiveTradeAction = 'BUY';
        }
        if (effectiveTradeAction && effectiveTradeAction !== decision.trade_action) {
          // Update cached decision so dashboard reflects what actually happened
          const cached = this.lastDecisions.get(id);
          if (cached) {
            cached.trade_action = effectiveTradeAction;
            cached.supplementary_override = true;
          }
        }
        logger.debug(`[SUPP-TRADE] ${id}: effectiveTradeAction=${effectiveTradeAction}`);
      }

      // Create sell offer when surplus exists
      if (effectiveTradeAction === 'SELL') {
        const surplus = energy_result.tradeable_surplus_kwh;
        // Also consider battery discharge as tradeable when selling
        const batteryAvail = prosumer.battery ? prosumer.battery.getAvailableDischargeEnergy() : 0;
        const tradeQty = surplus > 0.01 ? surplus : Math.min(batteryAvail * 0.3, 0.5);
        if (tradeQty > 0.01) {
          const sell_price = this.market.current_price * (0.90 + Math.random() * 0.15);
          this.market.submitOffer(prosumer, tradeQty, sell_price);
        }
      }
      // Create buy bid when deficit exists  
      if (effectiveTradeAction === 'BUY') {
        const demand = energy_result.unmet_demand_kwh;
        // Also consider battery charge room as tradeable when buying
        const chargeRoom = prosumer.battery ? prosumer.battery.getAvailableChargeCapacity() : 0;
        const tradeQty = demand > 0.01 ? demand : Math.min(chargeRoom * 0.3, 0.5);
        logger.info(`[BUY-BID] ${id}: demand=${demand?.toFixed(4)}, chargeRoom=${chargeRoom?.toFixed(4)}, tradeQty=${tradeQty?.toFixed(4)}, price=${this.market.current_price}`);
        if (tradeQty > 0.01) {
          const buy_price = this.market.current_price * (1.0 + Math.random() * 0.15);
          const bidResult = this.market.submitBid(prosumer, tradeQty, buy_price);
          logger.info(`[BUY-BID] ${id}: submitBid result=${JSON.stringify(bidResult?.success)}, reason=${bidResult?.reason}`);
        }
      }

      // Collect prosumer data
      tick_data.prosumers.push({
        prosumer_id: id,
        name: prosumer.name,
        balance,
        decision,
        energy_result,
        state: prosumer.getState(),
      });

      // Update stats
      this.stats.total_solar_kwh += balance.solar_energy_kwh;
      this.stats.total_load_kwh += balance.load_energy_kwh;
      this.stats.total_grid_import += energy_result.grid_import_kw * timestep_hours;
      this.stats.total_grid_export += energy_result.grid_export_kw * timestep_hours;
    }

    // 5. Run market matching
    if (this.config.auto_matching) {
      const matches = this.market.runMatching();

      // 6. Execute matched trades
      for (const trade of matches) {
        const exec_result = this.market.executeTrade(trade.trade_id, this.prosumers);

        if (exec_result.success) {
          // Mark as delivered
          this.market.markDelivered(trade.trade_id);

          // Auto-settle if enabled
          if (this.config.auto_settlement) {
            let tx_hash;

            // Try real blockchain settlement first
            if (this.blockchainService && this.blockchainService.isReady) {
              try {
                const bcResult = await this.blockchainService.settleTrade({
                  tradeId: trade.trade_id,
                  sellerId: trade.seller_id,
                  buyerId: trade.buyer_id,
                  quantityKwh: trade.quantity_kwh,
                  pricePerKwh: trade.price_per_kwh,
                  totalPrice: trade.total_price,
                });
                tx_hash = bcResult?.txHash || bcResult?.transactionHash;
                logger.info(`Real blockchain settlement: ${trade.trade_id} → ${tx_hash}`);
              } catch (bcErr) {
                logger.warn(`Blockchain settlement failed, using simulated hash: ${bcErr.message}`);
              }
            }

            // Fallback: simulated hash
            if (!tx_hash) {
              tx_hash = `0x${Date.now().toString(16)}${Math.random().toString(16).substring(2, 10)}`;
            }

            this.market.settleTrade(trade.trade_id, tx_hash);
          }

          tick_data.trades.push({
            ...trade,
            execution: exec_result,
          });

          this.stats.total_p2p_volume += trade.quantity_kwh;

          // Emit trade event for blockchain integration
          this.emit('trade_settled', {
            trade_id: trade.trade_id,
            seller_id: trade.seller_id,
            buyer_id: trade.buyer_id,
            quantity_kwh: trade.quantity_kwh,
            price_per_kwh: trade.price_per_kwh,
            total_price: trade.total_price,
            tx_hash: trade.blockchain_tx_hash,
          });
        }
      }
    }

    // 7. Advance simulation time
    this.advanceTime();

    // 8. Get market state
    tick_data.market = this.market.getMarketState();
    this.stats.ticks = this.tick_count;

    // Emit tick event
    this.emit('tick', tick_data);

    return tick_data;
  }

  /**
   * Advance simulation time
   */
  advanceTime() {
    // Advance by timestep
    const minutes_per_tick = this.config.timestep_minutes;
    const current_minute = (this.simulation_hour * 60) % (24 * 60);
    const new_minute = current_minute + minutes_per_tick;

    if (new_minute >= 24 * 60) {
      // New day
      this.simulation_day++;
      this.market.nextRound();
      this.emit('new_day', { day: this.simulation_day });
    }

    this.simulation_hour = (new_minute % (24 * 60)) / 60;
    this.current_time = Date.now();
  }

  /**
   * Start continuous simulation
   */
  start() {
    if (this.is_running) {
      return { success: false, reason: 'already_running' };
    }

    if (this.prosumers.size === 0) {
      this.initialize();
    }

    this.is_running = true;
    const interval_ms = (this.config.timestep_minutes * 60 * 1000) / this.config.simulation_speed;

    this.interval_id = setInterval(async () => {
      try {
        await this.tick();
      } catch (error) {
        logger.error(`Simulation tick error: ${error.message}`);
        this.emit('error', error);
      }
    }, interval_ms);

    logger.info(`Simulation started (speed: ${this.config.simulation_speed}x)`);
    this.emit('started', { speed: this.config.simulation_speed });
    return { success: true };
  }

  /**
   * Stop simulation
   */
  stop() {
    if (!this.is_running) {
      return { success: false, reason: 'not_running' };
    }

    if (this.interval_id) {
      clearInterval(this.interval_id);
      this.interval_id = null;
    }

    this.is_running = false;
    logger.info('Simulation stopped');
    this.emit('stopped', { ticks: this.tick_count });
    return { success: true };
  }

  /**
   * Run single tick manually
   */
  async step() {
    return await this.tick();
  }

  /**
   * Set simulation speed
   * @param {number} speed - Speed multiplier
   */
  setSpeed(speed) {
    this.config.simulation_speed = speed;

    if (this.is_running) {
      this.stop();
      this.start();
    }

    return { success: true, speed };
  }

  /**
   * Set simulation hour (for testing)
   * @param {number} hour - Hour (0-23)
   */
  setHour(hour) {
    this.simulation_hour = Math.max(0, Math.min(23.99, hour));
    return { success: true, hour: this.simulation_hour };
  }

  /**
   * Get simulation state
   */
  getState() {
    // Convert cached decisions to plain object for serialization
    const cachedDecisions = {};
    for (const [pid, dec] of this.lastDecisions) {
      cachedDecisions[pid] = dec;
    }

    return {
      is_running: this.is_running,
      tick_count: this.tick_count,
      simulation_hour: this.simulation_hour,
      simulation_day: this.simulation_day,
      prosumer_count: this.prosumers.size,
      prosumers: Array.from(this.prosumers.values()).map(p => p.getState()),
      market: this.market.getMarketState(),
      order_book: this.market.getOrderBook(),
      recent_trades: this.market.getRecentTrades(10),
      cached_ai_decisions: cachedDecisions,
      stats: this.stats,
    };
  }

  /**
   * Get prosumer by ID
   */
  getProsumer(prosumer_id) {
    const prosumer = this.prosumers.get(prosumer_id);
    return prosumer ? prosumer.getState() : null;
  }

  /**
   * Reset simulation
   */
  reset() {
    this.stop();

    for (const prosumer of this.prosumers.values()) {
      prosumer.reset();
    }

    this.market.reset();
    this.tick_count = 0;
    this.simulation_hour = 6;  // Start at 6 AM
    this.simulation_day = 0;
    this.lastDecisions.clear();
    this.stats = {
      ticks: 0,
      total_solar_kwh: 0,
      total_load_kwh: 0,
      total_p2p_volume: 0,
      total_grid_import: 0,
      total_grid_export: 0,
    };

    this.emit('reset');
    logger.info('Simulation reset');
    return { success: true };
  }
}

// Singleton instance
let simulationInstance = null;

/**
 * Get or create simulation controller instance
 */
function getSimulationController(config = {}) {
  if (!simulationInstance) {
    simulationInstance = new SimulationController(config);
  }
  return simulationInstance;
}

module.exports = {
  SimulationController,
  getSimulationController,
};
