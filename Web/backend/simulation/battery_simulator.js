/**
 * Battery Simulator for Grid-Guardian Virtual Trading
 *
 * Simulates realistic battery behavior including:
 * - Charging/discharging with efficiency losses
 * - State of Charge (SoC) management
 * - Safety bounds enforcement
 * - Degradation modeling (simplified)
 */

const logger = require('../utils/logger');

/**
 * Default battery configuration
 */
const DEFAULT_CONFIG = {
  capacity_kwh: 4.0,           // Total battery capacity in kWh
  initial_soc: 0.5,            // Initial state of charge (0-1)
  min_soc: 0.10,               // Minimum SoC (protect battery)
  max_soc: 0.95,               // Maximum SoC (protect battery)
  charge_rate_kw: 3.0,         // Max charging rate in kW
  discharge_rate_kw: 3.0,      // Max discharging rate in kW
  charge_efficiency: 0.95,     // Charging efficiency (95%)
  discharge_efficiency: 0.93,  // Discharging efficiency (93%)
  self_discharge_rate: 0.001,  // Self-discharge per hour (0.1%)
  degradation_factor: 0.9999,  // Capacity degradation per cycle
};

class BatterySimulator {
  /**
   * Create a battery simulator instance
   * @param {Object} config - Battery configuration
   */
  constructor(config = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };

    // Current state
    this.capacity_kwh = this.config.capacity_kwh;
    this.soc = this.config.initial_soc;
    this.soc_kwh = this.soc * this.capacity_kwh;

    // Tracking
    this.total_energy_charged = 0;
    this.total_energy_discharged = 0;
    this.cycle_count = 0;
    this.last_update = Date.now();

    // State tracking
    this.current_power_kw = 0;
    this.is_charging = false;
    this.is_discharging = false;

    logger.debug(`Battery initialized: ${this.capacity_kwh}kWh, SoC: ${(this.soc * 100).toFixed(1)}%`);
  }

  /**
   * Get available capacity for charging
   * @returns {number} Available capacity in kWh
   */
  getAvailableChargeCapacity() {
    const max_kwh = this.config.max_soc * this.capacity_kwh;
    return Math.max(0, max_kwh - this.soc_kwh);
  }

  /**
   * Get available energy for discharging
   * @returns {number} Available energy in kWh
   */
  getAvailableDischargeEnergy() {
    const min_kwh = this.config.min_soc * this.capacity_kwh;
    return Math.max(0, this.soc_kwh - min_kwh);
  }

  /**
   * Charge the battery
   * @param {number} power_kw - Charging power in kW
   * @param {number} duration_hours - Duration in hours
   * @returns {Object} Result of charging operation
   */
  charge(power_kw, duration_hours) {
    // Cap power to max charge rate
    const actual_power = Math.min(Math.abs(power_kw), this.config.charge_rate_kw);

    // Calculate energy with efficiency
    const gross_energy = actual_power * duration_hours;
    const net_energy = gross_energy * this.config.charge_efficiency;

    // Check capacity limits
    const available = this.getAvailableChargeCapacity();
    const energy_to_add = Math.min(net_energy, available);

    if (energy_to_add <= 0) {
      return {
        success: false,
        reason: 'battery_full',
        energy_added: 0,
        power_used: 0,
        new_soc: this.soc,
        new_soc_kwh: this.soc_kwh,
      };
    }

    // Update state
    const old_soc = this.soc;
    this.soc_kwh += energy_to_add;
    this.soc = this.soc_kwh / this.capacity_kwh;
    this.total_energy_charged += energy_to_add;

    // Track current operation
    this.current_power_kw = actual_power;
    this.is_charging = true;
    this.is_discharging = false;
    this.last_update = Date.now();

    // Calculate partial cycles
    this.cycle_count += energy_to_add / this.capacity_kwh;

    logger.debug(
      `Battery charged: ${energy_to_add.toFixed(3)}kWh, ` +
      `SoC: ${(old_soc * 100).toFixed(1)}% -> ${(this.soc * 100).toFixed(1)}%`
    );

    return {
      success: true,
      energy_added: energy_to_add,
      power_used: actual_power,
      duration_hours,
      efficiency_loss: gross_energy - net_energy,
      new_soc: this.soc,
      new_soc_kwh: this.soc_kwh,
    };
  }

  /**
   * Discharge the battery
   * @param {number} power_kw - Discharging power in kW
   * @param {number} duration_hours - Duration in hours
   * @returns {Object} Result of discharging operation
   */
  discharge(power_kw, duration_hours) {
    // Cap power to max discharge rate
    const actual_power = Math.min(Math.abs(power_kw), this.config.discharge_rate_kw);

    // Calculate requested energy
    const requested_energy = actual_power * duration_hours;

    // Check available energy
    const available = this.getAvailableDischargeEnergy();
    const energy_to_remove = Math.min(requested_energy, available);

    if (energy_to_remove <= 0) {
      return {
        success: false,
        reason: 'battery_empty',
        energy_delivered: 0,
        power_delivered: 0,
        new_soc: this.soc,
        new_soc_kwh: this.soc_kwh,
      };
    }

    // Calculate delivered energy with efficiency
    const energy_delivered = energy_to_remove * this.config.discharge_efficiency;

    // Update state
    const old_soc = this.soc;
    this.soc_kwh -= energy_to_remove;
    this.soc = this.soc_kwh / this.capacity_kwh;
    this.total_energy_discharged += energy_delivered;

    // Track current operation
    this.current_power_kw = -actual_power;
    this.is_charging = false;
    this.is_discharging = true;
    this.last_update = Date.now();

    // Calculate partial cycles
    this.cycle_count += energy_to_remove / this.capacity_kwh;

    logger.debug(
      `Battery discharged: ${energy_delivered.toFixed(3)}kWh delivered, ` +
      `SoC: ${(old_soc * 100).toFixed(1)}% -> ${(this.soc * 100).toFixed(1)}%`
    );

    return {
      success: true,
      energy_removed: energy_to_remove,
      energy_delivered,
      power_delivered: actual_power,
      duration_hours,
      efficiency_loss: energy_to_remove - energy_delivered,
      new_soc: this.soc,
      new_soc_kwh: this.soc_kwh,
    };
  }

  /**
   * Apply self-discharge over time
   * @param {number} hours - Hours elapsed
   */
  applySelfDischarge(hours) {
    const loss = this.soc_kwh * this.config.self_discharge_rate * hours;
    this.soc_kwh = Math.max(0, this.soc_kwh - loss);
    this.soc = this.soc_kwh / this.capacity_kwh;
    return loss;
  }

  /**
   * Apply degradation to capacity
   */
  applyDegradation() {
    // Apply once per full cycle
    if (this.cycle_count >= 1) {
      const cycles = Math.floor(this.cycle_count);
      this.capacity_kwh *= Math.pow(this.config.degradation_factor, cycles);
      this.cycle_count -= cycles;

      // Recalculate SoC based on new capacity
      this.soc = Math.min(this.soc_kwh / this.capacity_kwh, 1.0);
    }
  }

  /**
   * Update battery state for a timestep
   * @param {number} action_kw - Power action (positive=charge, negative=discharge)
   * @param {number} timestep_hours - Timestep duration in hours
   * @returns {Object} Updated state
   */
  update(action_kw, timestep_hours) {
    let result;

    if (action_kw > 0) {
      result = this.charge(action_kw, timestep_hours);
    } else if (action_kw < 0) {
      result = this.discharge(-action_kw, timestep_hours);
    } else {
      // Idle - only apply self-discharge
      const loss = this.applySelfDischarge(timestep_hours);
      this.current_power_kw = 0;
      this.is_charging = false;
      this.is_discharging = false;
      result = {
        success: true,
        action: 'idle',
        self_discharge_loss: loss,
        new_soc: this.soc,
        new_soc_kwh: this.soc_kwh,
      };
    }

    // Apply degradation periodically
    this.applyDegradation();
    this.last_update = Date.now();

    return result;
  }

  /**
   * Get current battery state
   * @returns {Object} Current state
   */
  getState() {
    return {
      soc: this.soc,
      soc_kwh: this.soc_kwh,
      soc_percent: this.soc * 100,
      capacity_kwh: this.capacity_kwh,
      current_power_kw: this.current_power_kw,
      is_charging: this.is_charging,
      is_discharging: this.is_discharging,
      available_charge_kwh: this.getAvailableChargeCapacity(),
      available_discharge_kwh: this.getAvailableDischargeEnergy(),
      total_charged_kwh: this.total_energy_charged,
      total_discharged_kwh: this.total_energy_discharged,
      cycle_count: this.cycle_count,
      health_percent: (this.capacity_kwh / this.config.capacity_kwh) * 100,
      last_update: this.last_update,
    };
  }

  /**
   * Set SoC directly (for initialization or testing)
   * @param {number} soc - State of charge (0-1)
   */
  setSoC(soc) {
    this.soc = Math.max(this.config.min_soc, Math.min(this.config.max_soc, soc));
    this.soc_kwh = this.soc * this.capacity_kwh;
  }

  /**
   * Reset battery to initial state
   */
  reset() {
    this.capacity_kwh = this.config.capacity_kwh;
    this.soc = this.config.initial_soc;
    this.soc_kwh = this.soc * this.capacity_kwh;
    this.total_energy_charged = 0;
    this.total_energy_discharged = 0;
    this.cycle_count = 0;
    this.current_power_kw = 0;
    this.is_charging = false;
    this.is_discharging = false;
    this.last_update = Date.now();
  }

  /**
   * Serialize battery state for persistence
   * @returns {Object} Serializable state
   */
  toJSON() {
    return {
      config: this.config,
      state: {
        capacity_kwh: this.capacity_kwh,
        soc: this.soc,
        soc_kwh: this.soc_kwh,
        total_energy_charged: this.total_energy_charged,
        total_energy_discharged: this.total_energy_discharged,
        cycle_count: this.cycle_count,
        current_power_kw: this.current_power_kw,
        is_charging: this.is_charging,
        is_discharging: this.is_discharging,
        last_update: this.last_update,
      },
    };
  }

  /**
   * Restore battery state from serialized data
   * @param {Object} data - Serialized state
   */
  static fromJSON(data) {
    const battery = new BatterySimulator(data.config);
    Object.assign(battery, data.state);
    return battery;
  }
}

module.exports = BatterySimulator;
