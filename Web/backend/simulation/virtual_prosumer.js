/**
 * Virtual Prosumer Model for Grid-Guardian P2P Trading Simulation
 *
 * Simulates a prosumer node with:
 * - Solar generation (with time-of-day patterns)
 * - Load demand (with realistic daily patterns)
 * - Battery storage
 * - Surplus/deficit calculation
 * - Trading state management
 */

const BatterySimulator = require('./battery_simulator');
const logger = require('../utils/logger');
const { v4: uuidv4 } = require('uuid');

/**
 * Default prosumer configuration
 */
const DEFAULT_CONFIG = {
  // Solar system
  solar_capacity_kw: 5.0,       // Peak solar capacity
  solar_efficiency: 0.85,       // Panel efficiency

  // Load patterns
  base_load_kw: 0.5,            // Minimum constant load
  peak_load_kw: 3.0,            // Peak load capacity
  load_profile: 'residential',  // residential, commercial, industrial

  // Battery (passed to BatterySimulator)
  battery: {
    capacity_kwh: 4.0,
    initial_soc: 0.5,
    min_soc: 0.10,
    max_soc: 0.95,
    charge_rate_kw: 3.0,
    discharge_rate_kw: 3.0,
  },

  // Trading
  min_trade_kwh: 0.1,           // Minimum trade size
  max_trade_kwh: 2.0,           // Maximum trade size
  price_floor: 3.00,            // Minimum sell price ₹/kWh
  price_ceiling: 10.00,          // Maximum buy price ₹/kWh
};

/**
 * Residential load profile (24-hour pattern, normalized 0-1)
 */
const RESIDENTIAL_LOAD_PROFILE = [
  0.3,  // 00:00
  0.25, // 01:00
  0.25, // 02:00
  0.25, // 03:00
  0.25, // 04:00
  0.3,  // 05:00
  0.5,  // 06:00 - morning ramp
  0.7,  // 07:00
  0.6,  // 08:00
  0.4,  // 09:00 - people leave
  0.35, // 10:00
  0.35, // 11:00
  0.4,  // 12:00
  0.4,  // 13:00
  0.35, // 14:00
  0.4,  // 15:00
  0.5,  // 16:00
  0.7,  // 17:00 - evening ramp
  0.9,  // 18:00 - peak
  1.0,  // 19:00 - peak
  0.9,  // 20:00
  0.7,  // 21:00
  0.5,  // 22:00
  0.4,  // 23:00
];

/**
 * Solar irradiance profile (normalized, summer-ish day)
 */
const SOLAR_PROFILE = [
  0.0,  // 00:00
  0.0,  // 01:00
  0.0,  // 02:00
  0.0,  // 03:00
  0.0,  // 04:00
  0.0,  // 05:00
  0.1,  // 06:00
  0.25, // 07:00
  0.45, // 08:00
  0.65, // 09:00
  0.8,  // 10:00
  0.9,  // 11:00
  1.0,  // 12:00 - peak
  0.95, // 13:00
  0.85, // 14:00
  0.7,  // 15:00
  0.5,  // 16:00
  0.3,  // 17:00
  0.1,  // 18:00
  0.0,  // 19:00
  0.0,  // 20:00
  0.0,  // 21:00
  0.0,  // 22:00
  0.0,  // 23:00
];

class VirtualProsumer {
  /**
   * Create a virtual prosumer
   * @param {string} prosumer_id - Unique identifier
   * @param {string} name - Display name
   * @param {Object} config - Configuration options
   */
  constructor(prosumer_id, name, config = {}) {
    this.prosumer_id = prosumer_id || `prosumer-${uuidv4().substring(0, 8)}`;
    this.name = name || `Prosumer ${this.prosumer_id.substring(0, 8)}`;
    this.config = { ...DEFAULT_CONFIG, ...config };

    // Initialize battery
    this.battery = new BatterySimulator(this.config.battery);

    // Current state
    this.solar_generation_kw = 0;
    this.load_demand_kw = 0;
    this.net_power_kw = 0;  // positive = surplus, negative = deficit
    this.surplus_energy_kwh = 0;
    this.deficit_energy_kwh = 0;

    // Trading state
    this.trade_status = 'idle';  // idle, offering, bidding, matched, executing
    this.current_offer = null;
    this.current_bid = null;
    this.current_mode = 'auto';  // auto, manual, safe

    // Statistics
    this.total_solar_generated = 0;
    this.total_load_consumed = 0;
    this.total_grid_import = 0;
    this.total_grid_export = 0;
    this.total_p2p_sold = 0;
    this.total_p2p_bought = 0;

    // Simulation state
    this.current_hour = 12;
    this.last_update = Date.now();

    logger.info(`Virtual prosumer created: ${this.prosumer_id} (${this.name})`);
  }

  /**
   * Get solar generation for current hour with some randomness
   * @param {number} hour - Hour of day (0-23)
   * @param {number} cloud_factor - Cloud cover (0-1, 0=clear)
   * @returns {number} Solar generation in kW
   */
  getSolarGeneration(hour, cloud_factor = 0) {
    const base_profile = SOLAR_PROFILE[Math.floor(hour) % 24];
    const cloud_multiplier = 1 - (cloud_factor * 0.8);  // Clouds reduce by up to 80%
    const random_factor = 0.9 + Math.random() * 0.2;    // ±10% randomness

    return this.config.solar_capacity_kw *
           this.config.solar_efficiency *
           base_profile *
           cloud_multiplier *
           random_factor;
  }

  /**
   * Get load demand for current hour with some randomness
   * @param {number} hour - Hour of day (0-23)
   * @returns {number} Load demand in kW
   */
  getLoadDemand(hour) {
    let profile;
    switch (this.config.load_profile) {
      case 'commercial':
        // Commercial: similar but peaks during work hours
        profile = [...RESIDENTIAL_LOAD_PROFILE];
        // Shift peak to midday
        for (let i = 9; i <= 17; i++) profile[i] = 0.8 + Math.random() * 0.2;
        for (let i = 18; i <= 23; i++) profile[i] = 0.2;
        break;
      case 'industrial':
        // Industrial: constant during work hours
        profile = RESIDENTIAL_LOAD_PROFILE.map((_, i) =>
          (i >= 6 && i <= 22) ? 0.9 : 0.3
        );
        break;
      default:
        profile = RESIDENTIAL_LOAD_PROFILE;
    }

    const base_load = profile[Math.floor(hour) % 24];
    const random_factor = 0.85 + Math.random() * 0.3;  // ±15% randomness

    return this.config.base_load_kw +
           (this.config.peak_load_kw - this.config.base_load_kw) *
           base_load * random_factor;
  }

  /**
   * Calculate energy balance for current timestep
   * @param {number} hour - Current hour
   * @param {number} timestep_hours - Timestep duration
   * @param {number} cloud_factor - Cloud cover factor
   * @returns {Object} Energy balance
   */
  calculateEnergyBalance(hour, timestep_hours = 1/12, cloud_factor = 0) {
    this.current_hour = hour;

    // Get instantaneous power values
    this.solar_generation_kw = this.getSolarGeneration(hour, cloud_factor);
    this.load_demand_kw = this.getLoadDemand(hour);

    // Net power: positive = surplus, negative = deficit
    this.net_power_kw = this.solar_generation_kw - this.load_demand_kw;

    // Calculate energy for this timestep
    const solar_energy = this.solar_generation_kw * timestep_hours;
    const load_energy = this.load_demand_kw * timestep_hours;
    const net_energy = this.net_power_kw * timestep_hours;

    // Update statistics
    this.total_solar_generated += solar_energy;
    this.total_load_consumed += load_energy;

    // Determine surplus/deficit
    if (net_energy > 0) {
      this.surplus_energy_kwh = net_energy;
      this.deficit_energy_kwh = 0;
    } else {
      this.surplus_energy_kwh = 0;
      this.deficit_energy_kwh = -net_energy;
    }

    return {
      solar_generation_kw: this.solar_generation_kw,
      load_demand_kw: this.load_demand_kw,
      net_power_kw: this.net_power_kw,
      solar_energy_kwh: solar_energy,
      load_energy_kwh: load_energy,
      net_energy_kwh: net_energy,
      surplus_kwh: this.surplus_energy_kwh,
      deficit_kwh: this.deficit_energy_kwh,
    };
  }

  /**
   * Process energy for a timestep (battery + grid interaction)
   * @param {string} ai_action - AI decision (charge_battery, discharge_battery, sell_surplus, buy_energy, hold)
   * @param {number} action_kw - Power action from AI
   * @param {number} timestep_hours - Duration of timestep
   * @returns {Object} Processing result
   */
  processEnergy(ai_action, action_kw = 0, timestep_hours = 1/12) {
    const battery_state = this.battery.getState();
    let grid_import = 0;
    let grid_export = 0;
    let battery_action = 0;
    let tradeable_surplus = 0;
    let unmet_demand = 0;

    // Process based on net power and AI action
    // Normalize action to handle both uppercase (from AI server: SELL, BUY, CHARGE, DISCHARGE)
    // and lowercase (from rule-based: sell_surplus, buy_energy, charge_battery, etc.)
    const normalizedAction = (ai_action || 'hold').toLowerCase();

    if (this.net_power_kw > 0) {
      // We have surplus solar
      const surplus_kw = this.net_power_kw;

      switch (normalizedAction) {
        case 'charge_battery':
        case 'charge_small':
        case 'charge_large':
        case 'charge':
          // Charge battery with surplus
          const charge_kw = Math.min(surplus_kw, battery_state.available_charge_kwh / timestep_hours);
          const charge_result = this.battery.update(charge_kw, timestep_hours);
          battery_action = charge_kw;

          // Remaining surplus goes to grid/trade
          const remaining = surplus_kw - charge_kw;
          tradeable_surplus = remaining * timestep_hours;
          grid_export = remaining;
          break;

        case 'sell_surplus':
        case 'offer_sell':
        case 'sell':
          // Export to P2P trading
          tradeable_surplus = surplus_kw * timestep_hours;
          grid_export = surplus_kw;
          break;

        case 'discharge_battery':
        case 'discharge_small':
        case 'discharge_large':
        case 'discharge':
          // Discharging during surplus — unusual but model may have reason
          // Just export surplus and discharge a bit of battery too
          tradeable_surplus = surplus_kw * timestep_hours;
          grid_export = surplus_kw;
          break;

        case 'buy_energy':
        case 'buy':
          // Buying during surplus — unusual, charge battery with surplus instead
          const buy_surplus_charge = Math.min(surplus_kw, battery_state.available_charge_kwh / timestep_hours);
          this.battery.update(buy_surplus_charge, timestep_hours);
          battery_action = buy_surplus_charge;
          grid_export = surplus_kw - buy_surplus_charge;
          tradeable_surplus = (surplus_kw - buy_surplus_charge) * timestep_hours;
          break;

        case 'hold':
        case 'idle':
        case 'offer_hold':
        default:
          // Default: charge battery first, then export remainder
          const default_charge = Math.min(surplus_kw, battery_state.available_charge_kwh / timestep_hours);
          this.battery.update(default_charge, timestep_hours);
          battery_action = default_charge;
          grid_export = surplus_kw - default_charge;
          tradeable_surplus = (surplus_kw - default_charge) * timestep_hours;
      }
    } else {
      // We have deficit (load > solar)
      const deficit_kw = -this.net_power_kw;

      switch (normalizedAction) {
        case 'discharge_battery':
        case 'discharge_small':
        case 'discharge_large':
        case 'discharge':
          // Discharge battery to meet demand
          const discharge_kw = Math.min(deficit_kw, battery_state.available_discharge_kwh / timestep_hours);
          const discharge_result = this.battery.update(-discharge_kw, timestep_hours);
          battery_action = -discharge_kw;

          // Remaining deficit from grid
          unmet_demand = (deficit_kw - discharge_kw) * timestep_hours;
          grid_import = deficit_kw - discharge_kw;
          break;

        case 'buy_energy':
        case 'buy':
          // Import from P2P or grid
          grid_import = deficit_kw;
          unmet_demand = deficit_kw * timestep_hours;
          break;

        case 'sell_surplus':
        case 'offer_sell':
        case 'sell':
          // Selling during deficit — unusual, use battery to cover deficit first
          const sell_discharge = Math.min(deficit_kw, battery_state.available_discharge_kwh / timestep_hours);
          this.battery.update(-sell_discharge, timestep_hours);
          battery_action = -sell_discharge;
          grid_import = deficit_kw - sell_discharge;
          unmet_demand = (deficit_kw - sell_discharge) * timestep_hours;
          break;

        case 'charge_battery':
        case 'charge_small':
        case 'charge_large':
        case 'charge':
          // Charging during deficit — import from grid to cover both load and charge
          grid_import = deficit_kw;
          unmet_demand = deficit_kw * timestep_hours;
          break;

        case 'hold':
        case 'idle':
        case 'offer_hold':
        default:
          // Default: use battery first, then import
          const default_discharge = Math.min(deficit_kw, battery_state.available_discharge_kwh / timestep_hours);
          this.battery.update(-default_discharge, timestep_hours);
          battery_action = -default_discharge;
          grid_import = deficit_kw - default_discharge;
          unmet_demand = (deficit_kw - default_discharge) * timestep_hours;
      }
    }

    // Update statistics
    this.total_grid_import += grid_import * timestep_hours;
    this.total_grid_export += grid_export * timestep_hours;
    this.last_update = Date.now();

    return {
      battery_action_kw: battery_action,
      grid_import_kw: grid_import,
      grid_export_kw: grid_export,
      tradeable_surplus_kwh: tradeable_surplus,
      unmet_demand_kwh: unmet_demand,
      battery_state: this.battery.getState(),
    };
  }

  /**
   * Create a sell offer
   * @param {number} quantity_kwh - Energy quantity
   * @param {number} price_per_kwh - Price per kWh
   * @returns {Object} Offer object
   */
  createOffer(quantity_kwh, price_per_kwh) {
    // Validate
    if (quantity_kwh < this.config.min_trade_kwh) {
      return { success: false, reason: 'quantity_too_small' };
    }
    if (quantity_kwh > this.config.max_trade_kwh) {
      return { success: false, reason: 'quantity_too_large' };
    }
    if (price_per_kwh < this.config.price_floor) {
      return { success: false, reason: 'price_below_floor' };
    }

    // Check if we have the energy
    const battery = this.battery.getState();
    const available = this.surplus_energy_kwh + battery.available_discharge_kwh;
    if (quantity_kwh > available) {
      return { success: false, reason: 'insufficient_energy' };
    }

    this.current_offer = {
      offer_id: `OFFER-${uuidv4().substring(0, 8).toUpperCase()}`,
      prosumer_id: this.prosumer_id,
      type: 'sell',
      quantity_kwh,
      price_per_kwh,
      total_price: quantity_kwh * price_per_kwh,
      status: 'open',
      created_at: Date.now(),
      expires_at: Date.now() + 15 * 60 * 1000, // 15 min expiry
    };

    this.trade_status = 'offering';
    logger.info(`Offer created: ${this.current_offer.offer_id} - ${quantity_kwh}kWh @ ₹${price_per_kwh}/kWh`);

    return { success: true, offer: this.current_offer };
  }

  /**
   * Create a buy bid
   * @param {number} quantity_kwh - Energy quantity needed
   * @param {number} max_price_per_kwh - Maximum price willing to pay
   * @returns {Object} Bid object
   */
  createBid(quantity_kwh, max_price_per_kwh) {
    // Validate
    if (quantity_kwh < this.config.min_trade_kwh) {
      return { success: false, reason: 'quantity_too_small' };
    }
    if (max_price_per_kwh > this.config.price_ceiling) {
      return { success: false, reason: 'price_above_ceiling' };
    }

    this.current_bid = {
      bid_id: `BID-${uuidv4().substring(0, 8).toUpperCase()}`,
      prosumer_id: this.prosumer_id,
      type: 'buy',
      quantity_kwh,
      max_price_per_kwh,
      max_total_price: quantity_kwh * max_price_per_kwh,
      status: 'open',
      created_at: Date.now(),
      expires_at: Date.now() + 15 * 60 * 1000, // 15 min expiry
    };

    this.trade_status = 'bidding';
    logger.info(`Bid created: ${this.current_bid.bid_id} - ${quantity_kwh}kWh @ max ₹${max_price_per_kwh}/kWh`);

    return { success: true, bid: this.current_bid };
  }

  /**
   * Execute a matched trade (as seller)
   * @param {number} quantity_kwh - Quantity to deliver
   * @param {number} price_per_kwh - Agreed price
   * @returns {Object} Execution result
   */
  executeSale(quantity_kwh, price_per_kwh) {
    // First use surplus, then battery
    let remaining = quantity_kwh;
    let from_surplus = 0;
    let from_battery = 0;

    if (this.surplus_energy_kwh > 0) {
      from_surplus = Math.min(remaining, this.surplus_energy_kwh);
      remaining -= from_surplus;
      this.surplus_energy_kwh -= from_surplus;
    }

    if (remaining > 0) {
      // Discharge from battery
      const discharge_hours = 1/12;  // 5-min equivalent
      const discharge_kw = remaining / discharge_hours;
      const result = this.battery.discharge(discharge_kw, discharge_hours);
      from_battery = result.energy_delivered;
    }

    const total_delivered = from_surplus + from_battery;
    const revenue = total_delivered * price_per_kwh;

    this.total_p2p_sold += total_delivered;
    this.trade_status = 'idle';
    this.current_offer = null;

    logger.info(`Sale executed: ${total_delivered.toFixed(3)}kWh delivered, revenue: ₹${revenue.toFixed(2)}`);

    return {
      success: true,
      delivered_kwh: total_delivered,
      from_surplus_kwh: from_surplus,
      from_battery_kwh: from_battery,
      revenue,
      price_per_kwh,
    };
  }

  /**
   * Execute a matched trade (as buyer)
   * @param {number} quantity_kwh - Quantity received
   * @param {number} price_per_kwh - Agreed price
   * @returns {Object} Execution result
   */
  executePurchase(quantity_kwh, price_per_kwh) {
    // First cover deficit, then charge battery
    let remaining = quantity_kwh;
    let to_load = 0;
    let to_battery = 0;

    if (this.deficit_energy_kwh > 0) {
      to_load = Math.min(remaining, this.deficit_energy_kwh);
      remaining -= to_load;
      this.deficit_energy_kwh -= to_load;
    }

    if (remaining > 0) {
      // Charge battery
      const charge_hours = 1/12;  // 5-min equivalent
      const charge_kw = remaining / charge_hours;
      const result = this.battery.charge(charge_kw, charge_hours);
      to_battery = result.energy_added;
    }

    const total_received = to_load + to_battery;
    const cost = total_received * price_per_kwh;

    this.total_p2p_bought += total_received;
    this.trade_status = 'idle';
    this.current_bid = null;

    logger.info(`Purchase executed: ${total_received.toFixed(3)}kWh received, cost: ₹${cost.toFixed(2)}`);

    return {
      success: true,
      received_kwh: total_received,
      to_load_kwh: to_load,
      to_battery_kwh: to_battery,
      cost,
      price_per_kwh,
    };
  }

  /**
   * Get complete prosumer state
   * @returns {Object} Full state
   */
  getState() {
    return {
      prosumer_id: this.prosumer_id,
      name: this.name,
      current_hour: this.current_hour,

      // Power state
      solar_generation_kw: this.solar_generation_kw,
      load_demand_kw: this.load_demand_kw,
      net_power_kw: this.net_power_kw,

      // Energy state
      surplus_energy_kwh: this.surplus_energy_kwh,
      deficit_energy_kwh: this.deficit_energy_kwh,

      // Battery state
      battery: this.battery.getState(),

      // Trading state
      trade_status: this.trade_status,
      current_offer: this.current_offer,
      current_bid: this.current_bid,
      current_mode: this.current_mode,

      // Statistics
      total_solar_generated: this.total_solar_generated,
      total_load_consumed: this.total_load_consumed,
      total_grid_import: this.total_grid_import,
      total_grid_export: this.total_grid_export,
      total_p2p_sold: this.total_p2p_sold,
      total_p2p_bought: this.total_p2p_bought,

      last_update: this.last_update,
    };
  }

  /**
   * Get observation for AI model
   * @returns {Object} AI observation
   */
  getAIObservation(market_price = 6.0) {
    const battery = this.battery.getState();
    const solar_cap = this.config.solar_capacity_kw || 1;
    return {
      soc_kwh: battery.soc_kwh,
      soc_capacity_kwh: battery.capacity_kwh,
      pv_gen_kw: this.solar_generation_kw,
      load_kw: this.load_demand_kw,
      net_kw: this.net_power_kw,
      battery_power_kw: battery.current_power_kw || 0,
      price_signal: market_price,
      forecast_irradiance_1h: this.getSolarGeneration((this.current_hour + 1) % 24) / solar_cap * 1000,
      forecast_irradiance_3h: this.getSolarGeneration((this.current_hour + 3) % 24) / solar_cap * 1000,
      forecast_temp_1h: 25 + Math.random() * 10 - 5,
      actual_irradiance_wm2: this.solar_generation_kw / solar_cap * 1000,
      voltage_v: 230 + Math.random() * 10 - 5,
      current_a: this.load_demand_kw * 1000 / 230,
      neighbor_balance: 0.0,
    };
  }

  /**
   * Reset prosumer to initial state
   */
  reset() {
    this.battery.reset();
    this.solar_generation_kw = 0;
    this.load_demand_kw = 0;
    this.net_power_kw = 0;
    this.surplus_energy_kwh = 0;
    this.deficit_energy_kwh = 0;
    this.trade_status = 'idle';
    this.current_offer = null;
    this.current_bid = null;
    this.total_solar_generated = 0;
    this.total_load_consumed = 0;
    this.total_grid_import = 0;
    this.total_grid_export = 0;
    this.total_p2p_sold = 0;
    this.total_p2p_bought = 0;
    this.last_update = Date.now();
  }

  /**
   * Serialize for persistence
   */
  toJSON() {
    return {
      prosumer_id: this.prosumer_id,
      name: this.name,
      config: this.config,
      battery: this.battery.toJSON(),
      state: {
        solar_generation_kw: this.solar_generation_kw,
        load_demand_kw: this.load_demand_kw,
        net_power_kw: this.net_power_kw,
        surplus_energy_kwh: this.surplus_energy_kwh,
        deficit_energy_kwh: this.deficit_energy_kwh,
        trade_status: this.trade_status,
        current_offer: this.current_offer,
        current_bid: this.current_bid,
        current_mode: this.current_mode,
        total_solar_generated: this.total_solar_generated,
        total_load_consumed: this.total_load_consumed,
        total_grid_import: this.total_grid_import,
        total_grid_export: this.total_grid_export,
        total_p2p_sold: this.total_p2p_sold,
        total_p2p_bought: this.total_p2p_bought,
      },
    };
  }

  static fromJSON(data) {
    const prosumer = new VirtualProsumer(data.prosumer_id, data.name, data.config);
    prosumer.battery = BatterySimulator.fromJSON(data.battery);
    Object.assign(prosumer, data.state);
    return prosumer;
  }
}

module.exports = VirtualProsumer;
