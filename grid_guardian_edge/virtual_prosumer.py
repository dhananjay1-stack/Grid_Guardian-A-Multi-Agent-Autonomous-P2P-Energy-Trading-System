"""
Virtual Prosumer Simulation for Grid-Guardian Edge
Provides simulation mode without physical hardware.
"""

import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BatteryConfig:
    """Battery configuration"""
    capacity_kwh: float = 4.0
    initial_soc: float = 0.5
    min_soc: float = 0.10
    max_soc: float = 0.95
    charge_rate_kw: float = 3.0
    discharge_rate_kw: float = 3.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.93
    self_discharge_rate: float = 0.001


@dataclass
class ProsumerConfig:
    """Prosumer configuration"""
    solar_capacity_kw: float = 5.0
    solar_efficiency: float = 0.85
    base_load_kw: float = 0.5
    peak_load_kw: float = 3.0
    load_profile: str = "residential"
    battery: BatteryConfig = field(default_factory=BatteryConfig)


# Load profiles (normalized 0-1)
RESIDENTIAL_LOAD_PROFILE = [
    0.3, 0.25, 0.25, 0.25, 0.25, 0.3,   # 00-05
    0.5, 0.7, 0.6, 0.4, 0.35, 0.35,     # 06-11
    0.4, 0.4, 0.35, 0.4, 0.5, 0.7,      # 12-17
    0.9, 1.0, 0.9, 0.7, 0.5, 0.4,       # 18-23
]

SOLAR_PROFILE = [
    0.0, 0.0, 0.0, 0.0, 0.0, 0.0,       # 00-05
    0.1, 0.25, 0.45, 0.65, 0.8, 0.9,    # 06-11
    1.0, 0.95, 0.85, 0.7, 0.5, 0.3,     # 12-17
    0.1, 0.0, 0.0, 0.0, 0.0, 0.0,       # 18-23
]


class VirtualBattery:
    """Virtual battery simulator"""

    def __init__(self, config: BatteryConfig = None):
        self.config = config or BatteryConfig()
        self.reset()

    def reset(self):
        """Reset battery to initial state"""
        self.capacity_kwh = self.config.capacity_kwh
        self.soc = self.config.initial_soc
        self.soc_kwh = self.soc * self.capacity_kwh
        self.total_charged = 0.0
        self.total_discharged = 0.0
        self.current_power_kw = 0.0
        self.is_charging = False
        self.is_discharging = False

    def get_available_charge(self) -> float:
        """Get available capacity for charging in kWh"""
        max_kwh = self.config.max_soc * self.capacity_kwh
        return max(0, max_kwh - self.soc_kwh)

    def get_available_discharge(self) -> float:
        """Get available energy for discharging in kWh"""
        min_kwh = self.config.min_soc * self.capacity_kwh
        return max(0, self.soc_kwh - min_kwh)

    def charge(self, power_kw: float, duration_hours: float) -> Dict:
        """Charge the battery"""
        actual_power = min(abs(power_kw), self.config.charge_rate_kw)
        gross_energy = actual_power * duration_hours
        net_energy = gross_energy * self.config.charge_efficiency

        available = self.get_available_charge()
        energy_to_add = min(net_energy, available)

        if energy_to_add <= 0:
            return {"success": False, "reason": "battery_full", "energy_added": 0}

        old_soc = self.soc
        self.soc_kwh += energy_to_add
        self.soc = self.soc_kwh / self.capacity_kwh
        self.total_charged += energy_to_add
        self.current_power_kw = actual_power
        self.is_charging = True
        self.is_discharging = False

        return {
            "success": True,
            "energy_added": energy_to_add,
            "power_used": actual_power,
            "old_soc": old_soc,
            "new_soc": self.soc,
        }

    def discharge(self, power_kw: float, duration_hours: float) -> Dict:
        """Discharge the battery"""
        actual_power = min(abs(power_kw), self.config.discharge_rate_kw)
        requested_energy = actual_power * duration_hours

        available = self.get_available_discharge()
        energy_to_remove = min(requested_energy, available)

        if energy_to_remove <= 0:
            return {"success": False, "reason": "battery_empty", "energy_delivered": 0}

        energy_delivered = energy_to_remove * self.config.discharge_efficiency

        old_soc = self.soc
        self.soc_kwh -= energy_to_remove
        self.soc = self.soc_kwh / self.capacity_kwh
        self.total_discharged += energy_delivered
        self.current_power_kw = -actual_power
        self.is_charging = False
        self.is_discharging = True

        return {
            "success": True,
            "energy_removed": energy_to_remove,
            "energy_delivered": energy_delivered,
            "power_delivered": actual_power,
            "old_soc": old_soc,
            "new_soc": self.soc,
        }

    def update(self, action_kw: float, timestep_hours: float) -> Dict:
        """Update battery for a timestep"""
        if action_kw > 0:
            return self.charge(action_kw, timestep_hours)
        elif action_kw < 0:
            return self.discharge(-action_kw, timestep_hours)
        else:
            # Idle
            self.current_power_kw = 0
            self.is_charging = False
            self.is_discharging = False
            return {"success": True, "action": "idle", "new_soc": self.soc}

    def get_state(self) -> Dict:
        """Get battery state"""
        return {
            "soc": self.soc,
            "soc_kwh": self.soc_kwh,
            "soc_percent": self.soc * 100,
            "capacity_kwh": self.capacity_kwh,
            "current_power_kw": self.current_power_kw,
            "is_charging": self.is_charging,
            "is_discharging": self.is_discharging,
            "available_charge_kwh": self.get_available_charge(),
            "available_discharge_kwh": self.get_available_discharge(),
        }


class VirtualProsumer:
    """Virtual prosumer simulator for edge runtime"""

    def __init__(self, prosumer_id: str, name: str, config: ProsumerConfig = None):
        self.prosumer_id = prosumer_id
        self.name = name
        self.config = config or ProsumerConfig()
        self.battery = VirtualBattery(self.config.battery)

        # Current state
        self.solar_generation_kw = 0.0
        self.load_demand_kw = 0.0
        self.net_power_kw = 0.0
        self.surplus_kwh = 0.0
        self.deficit_kwh = 0.0

        # Statistics
        self.total_solar = 0.0
        self.total_load = 0.0
        self.total_grid_import = 0.0
        self.total_grid_export = 0.0

        # Time tracking
        self.current_hour = 12.0

    def get_solar_generation(self, hour: float, cloud_factor: float = 0.0) -> float:
        """Get solar generation for current hour"""
        hour_idx = int(hour) % 24
        base = SOLAR_PROFILE[hour_idx]
        cloud_mult = 1 - (cloud_factor * 0.8)
        random_factor = 0.9 + random.random() * 0.2

        return (self.config.solar_capacity_kw *
                self.config.solar_efficiency *
                base * cloud_mult * random_factor)

    def get_load_demand(self, hour: float) -> float:
        """Get load demand for current hour"""
        hour_idx = int(hour) % 24
        base = RESIDENTIAL_LOAD_PROFILE[hour_idx]
        random_factor = 0.85 + random.random() * 0.3

        return (self.config.base_load_kw +
                (self.config.peak_load_kw - self.config.base_load_kw) *
                base * random_factor)

    def calculate_balance(self, hour: float, timestep_hours: float = 1/12,
                          cloud_factor: float = 0.0) -> Dict:
        """Calculate energy balance for timestep"""
        self.current_hour = hour
        self.solar_generation_kw = self.get_solar_generation(hour, cloud_factor)
        self.load_demand_kw = self.get_load_demand(hour)
        self.net_power_kw = self.solar_generation_kw - self.load_demand_kw

        solar_energy = self.solar_generation_kw * timestep_hours
        load_energy = self.load_demand_kw * timestep_hours
        net_energy = self.net_power_kw * timestep_hours

        self.total_solar += solar_energy
        self.total_load += load_energy

        if net_energy > 0:
            self.surplus_kwh = net_energy
            self.deficit_kwh = 0
        else:
            self.surplus_kwh = 0
            self.deficit_kwh = -net_energy

        return {
            "solar_kw": self.solar_generation_kw,
            "load_kw": self.load_demand_kw,
            "net_kw": self.net_power_kw,
            "surplus_kwh": self.surplus_kwh,
            "deficit_kwh": self.deficit_kwh,
        }

    def process_energy(self, action: str, action_kw: float = 0,
                       timestep_hours: float = 1/12) -> Dict:
        """Process energy based on AI decision"""
        battery_state = self.battery.get_state()
        grid_import = 0.0
        grid_export = 0.0
        battery_action = 0.0

        if self.net_power_kw > 0:
            # Surplus solar
            surplus_kw = self.net_power_kw

            if action in ["charge_battery", "charge_small", "charge_large"]:
                charge_kw = min(surplus_kw, battery_state["available_charge_kwh"] / timestep_hours)
                self.battery.update(charge_kw, timestep_hours)
                battery_action = charge_kw
                remaining = surplus_kw - charge_kw
                grid_export = remaining
            elif action in ["sell_surplus", "offer_sell"]:
                grid_export = surplus_kw
            else:
                # Default: charge then export
                charge_kw = min(surplus_kw, battery_state["available_charge_kwh"] / timestep_hours)
                self.battery.update(charge_kw, timestep_hours)
                battery_action = charge_kw
                grid_export = surplus_kw - charge_kw
        else:
            # Deficit
            deficit_kw = -self.net_power_kw

            if action in ["discharge_battery", "discharge_small", "discharge_large"]:
                discharge_kw = min(deficit_kw, battery_state["available_discharge_kwh"] / timestep_hours)
                self.battery.update(-discharge_kw, timestep_hours)
                battery_action = -discharge_kw
                grid_import = deficit_kw - discharge_kw
            elif action in ["buy_energy"]:
                grid_import = deficit_kw
            else:
                # Default: discharge then import
                discharge_kw = min(deficit_kw, battery_state["available_discharge_kwh"] / timestep_hours)
                self.battery.update(-discharge_kw, timestep_hours)
                battery_action = -discharge_kw
                grid_import = deficit_kw - discharge_kw

        self.total_grid_import += grid_import * timestep_hours
        self.total_grid_export += grid_export * timestep_hours

        return {
            "battery_action_kw": battery_action,
            "grid_import_kw": grid_import,
            "grid_export_kw": grid_export,
            "battery_state": self.battery.get_state(),
        }

    def get_sensor_data(self) -> Dict:
        """Get simulated sensor data (mimics real PZEM readings)"""
        # Simulate voltage with small variations
        voltage = 230 + random.uniform(-5, 5)

        # Calculate current from load
        total_power = (self.load_demand_kw - self.solar_generation_kw +
                       self.battery.current_power_kw) * 1000  # Convert to W

        # If negative, we're exporting
        if total_power < 0:
            total_power = abs(total_power)

        current = total_power / voltage if voltage > 0 else 0

        # Power factor
        power_factor = 0.92 + random.uniform(-0.02, 0.02)

        return {
            "voltage": voltage,
            "current": current,
            "power": total_power,
            "energy": self.total_load,  # Cumulative
            "frequency": 50 + random.uniform(-0.1, 0.1),
            "power_factor": power_factor,
            "solar_kw": self.solar_generation_kw,
            "load_kw": self.load_demand_kw,
            "net_kw": self.net_power_kw,
            "battery_soc": self.battery.soc,
            "battery_power_kw": self.battery.current_power_kw,
        }

    def get_ai_observation(self) -> Dict:
        """Get observation for AI model"""
        battery = self.battery.get_state()
        return {
            "soc_kwh": battery["soc_kwh"],
            "soc_capacity_kwh": battery["capacity_kwh"],
            "pv_gen_kw": self.solar_generation_kw,
            "load_kw": self.load_demand_kw,
            "net_kw": self.net_power_kw,
            "battery_power_kw": battery["current_power_kw"],
            "price_signal": 0.15,  # Default
            "forecast_irradiance_1h": self.get_solar_generation(self.current_hour + 1) / self.config.solar_capacity_kw * 1000,
            "forecast_irradiance_3h": self.get_solar_generation(self.current_hour + 3) / self.config.solar_capacity_kw * 1000,
            "forecast_temp_1h": 25,
            "actual_irradiance_wm2": self.solar_generation_kw / self.config.solar_capacity_kw * 1000,
            "voltage_v": 230 + random.uniform(-5, 5),
            "current_a": self.load_demand_kw * 1000 / 230,
        }

    def get_state(self) -> Dict:
        """Get full prosumer state"""
        return {
            "prosumer_id": self.prosumer_id,
            "name": self.name,
            "hour": self.current_hour,
            "solar_kw": self.solar_generation_kw,
            "load_kw": self.load_demand_kw,
            "net_kw": self.net_power_kw,
            "surplus_kwh": self.surplus_kwh,
            "deficit_kwh": self.deficit_kwh,
            "battery": self.battery.get_state(),
            "stats": {
                "total_solar": self.total_solar,
                "total_load": self.total_load,
                "total_grid_import": self.total_grid_import,
                "total_grid_export": self.total_grid_export,
            },
        }

    def reset(self):
        """Reset prosumer state"""
        self.battery.reset()
        self.solar_generation_kw = 0.0
        self.load_demand_kw = 0.0
        self.net_power_kw = 0.0
        self.surplus_kwh = 0.0
        self.deficit_kwh = 0.0
        self.total_solar = 0.0
        self.total_load = 0.0
        self.total_grid_import = 0.0
        self.total_grid_export = 0.0


class SimulationSensorReader:
    """
    Drop-in replacement for SensorReader that uses VirtualProsumer
    for simulation mode.
    """

    def __init__(self, prosumer_id: str = "sim-node-01", name: str = "Simulation Node"):
        self.prosumer = VirtualProsumer(prosumer_id, name)
        self.mock_mode = True  # Always in mock mode
        self.current_hour = 12.0
        self.stats = {
            "reads": 0,
            "errors": 0,
        }
        logger.info(f"SimulationSensorReader initialized: {prosumer_id}")

    def read_data(self) -> Optional[Dict]:
        """Read simulated sensor data"""
        try:
            # Calculate balance first
            self.prosumer.calculate_balance(self.current_hour)

            # Get sensor data
            data = self.prosumer.get_sensor_data()
            data["node_id"] = self.prosumer.prosumer_id
            data["timestamp"] = int(time.time() * 1000)
            data["simulation_hour"] = self.current_hour

            self.stats["reads"] += 1
            return data

        except Exception as e:
            logger.error(f"Simulation sensor read error: {e}")
            self.stats["errors"] += 1
            return None

    def set_hour(self, hour: float):
        """Set simulation hour"""
        self.current_hour = hour % 24

    def advance_time(self, minutes: float = 5):
        """Advance simulation time"""
        self.current_hour = (self.current_hour + minutes / 60) % 24

    def get_prosumer(self) -> VirtualProsumer:
        """Get underlying prosumer"""
        return self.prosumer

    def get_stats(self) -> Dict:
        """Get reader statistics"""
        return self.stats

    def close(self):
        """Cleanup (no-op for simulation)"""
        pass
