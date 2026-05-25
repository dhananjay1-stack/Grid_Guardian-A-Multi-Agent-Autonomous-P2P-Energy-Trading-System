#!/usr/bin/env python3
"""
Grid-Guardian Demo Orchestrator

Orchestrates the end-to-end P2P energy trading demo:
1. Starts virtual prosumers
2. Runs condition detection
3. Executes AI decisions with dynamic model selection
4. Simulates P2P trading and market matching
5. Emits events for dashboard/blockchain integration

Usage:
    python demo_orchestrator.py --mode simulation
    python demo_orchestrator.py --mode edge --backend-url http://localhost:3000
"""

import argparse
import asyncio
import json
import logging
import math
import random
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum

# Add parent directory for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    import numpy as np
except ImportError:
    print("Please install numpy: pip install numpy")
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Try importing local modules
try:
    from condition_detector import ConditionDetector, ConditionResult
    from policy_selector import PolicySelector, PolicyType
    SELECTOR_AVAILABLE = True
except ImportError:
    SELECTOR_AVAILABLE = False
    logger.warning("PolicySelector not available - using rule-based fallback")


# ============================================================================
# Virtual Battery
# ============================================================================

@dataclass
class BatteryState:
    """Battery state"""
    capacity_kwh: float = 5.0
    soc_kwh: float = 2.5
    min_soc_frac: float = 0.10
    max_soc_frac: float = 0.95
    max_charge_kw: float = 3.0
    max_discharge_kw: float = 3.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.93

    @property
    def soc_fraction(self) -> float:
        return self.soc_kwh / self.capacity_kwh

    @property
    def available_charge_kw(self) -> float:
        headroom = (self.max_soc_frac * self.capacity_kwh) - self.soc_kwh
        return min(self.max_charge_kw, headroom / 0.0833)  # 5 min timestep

    @property
    def available_discharge_kw(self) -> float:
        headroom = self.soc_kwh - (self.min_soc_frac * self.capacity_kwh)
        return min(self.max_discharge_kw, headroom / 0.0833)


class VirtualBattery:
    """Virtual battery simulator"""

    def __init__(self, config: Optional[Dict] = None):
        config = config or {}
        self.state = BatteryState(
            capacity_kwh=config.get("capacity_kwh", 5.0),
            soc_kwh=config.get("initial_soc_kwh", 2.5),
            min_soc_frac=config.get("min_soc", 0.10),
            max_soc_frac=config.get("max_soc", 0.95),
            max_charge_kw=config.get("max_charge_kw", 3.0),
            max_discharge_kw=config.get("max_discharge_kw", 3.0),
        )

    def charge(self, kw: float, duration_hours: float = 0.0833) -> float:
        """Charge battery, returns actual energy stored"""
        available = self.state.available_charge_kw
        actual_kw = min(kw, available)
        energy_kwh = actual_kw * duration_hours * self.state.charge_efficiency
        self.state.soc_kwh = min(
            self.state.max_soc_frac * self.state.capacity_kwh,
            self.state.soc_kwh + energy_kwh
        )
        return energy_kwh

    def discharge(self, kw: float, duration_hours: float = 0.0833) -> float:
        """Discharge battery, returns actual energy delivered"""
        available = self.state.available_discharge_kw
        actual_kw = min(kw, available)
        energy_kwh = actual_kw * duration_hours * self.state.discharge_efficiency
        self.state.soc_kwh = max(
            self.state.min_soc_frac * self.state.capacity_kwh,
            self.state.soc_kwh - energy_kwh
        )
        return energy_kwh

    def get_state(self) -> Dict[str, Any]:
        return {
            "soc_kwh": round(self.state.soc_kwh, 3),
            "soc_fraction": round(self.state.soc_fraction, 3),
            "capacity_kwh": self.state.capacity_kwh,
            "available_charge_kw": round(self.state.available_charge_kw, 2),
            "available_discharge_kw": round(self.state.available_discharge_kw, 2),
        }


# ============================================================================
# Virtual Prosumer
# ============================================================================

# Load profiles (hourly multipliers, 24 values)
RESIDENTIAL_LOAD_PROFILE = [
    0.3, 0.25, 0.2, 0.2, 0.25, 0.4,   # 00-05
    0.6, 0.8, 0.7, 0.5, 0.4, 0.5,     # 06-11
    0.6, 0.5, 0.4, 0.5, 0.7, 1.0,     # 12-17
    1.0, 0.9, 0.8, 0.6, 0.5, 0.4,     # 18-23
]

SOLAR_PROFILE = [
    0.0, 0.0, 0.0, 0.0, 0.0, 0.05,    # 00-05
    0.15, 0.35, 0.55, 0.75, 0.9, 1.0, # 06-11
    1.0, 0.95, 0.85, 0.7, 0.5, 0.3,   # 12-17
    0.1, 0.0, 0.0, 0.0, 0.0, 0.0,     # 18-23
]


@dataclass
class ProsumerConfig:
    """Prosumer configuration"""
    prosumer_id: str = "prosumer-a"
    name: str = "Solar Home A"
    solar_capacity_kw: float = 6.0
    base_load_kw: float = 0.8
    peak_load_kw: float = 3.5
    battery_config: Dict = field(default_factory=dict)


class VirtualProsumer:
    """Virtual prosumer with solar, load, and battery"""

    def __init__(self, config: ProsumerConfig):
        self.config = config
        self.battery = VirtualBattery(config.battery_config)

        # State
        self.current_solar_kw = 0.0
        self.current_load_kw = 0.0
        self.net_power_kw = 0.0
        self.grid_import_kw = 0.0
        self.grid_export_kw = 0.0

        # Trading state
        self.current_offer = None
        self.current_bid = None
        self.trade_status = "idle"

        # AI state
        self.last_ai_decision = None
        self.last_condition = None
        self.last_selected_model = None

    def update(self, hour: float, cloud_factor: float = 0.0) -> Dict[str, Any]:
        """
        Update prosumer state for current hour.

        Args:
            hour: Hour of day (0-23.99)
            cloud_factor: Cloud coverage factor (0-1)

        Returns:
            Current state dict
        """
        hour_idx = int(hour) % 24
        minute_frac = hour - int(hour)

        # Interpolate solar
        next_idx = (hour_idx + 1) % 24
        solar_mult = SOLAR_PROFILE[hour_idx] * (1 - minute_frac) + \
                     SOLAR_PROFILE[next_idx] * minute_frac
        solar_mult *= (1 - cloud_factor)  # Apply cloud factor
        solar_mult *= (0.85 + random.random() * 0.3)  # Random variation

        # Interpolate load
        load_mult = RESIDENTIAL_LOAD_PROFILE[hour_idx] * (1 - minute_frac) + \
                    RESIDENTIAL_LOAD_PROFILE[next_idx] * minute_frac
        load_mult *= (0.8 + random.random() * 0.4)  # Random variation

        # Calculate actual values
        self.current_solar_kw = self.config.solar_capacity_kw * solar_mult
        load_range = self.config.peak_load_kw - self.config.base_load_kw
        self.current_load_kw = self.config.base_load_kw + (load_range * load_mult)

        # Net power (positive = surplus, negative = deficit)
        self.net_power_kw = self.current_solar_kw - self.current_load_kw

        return self.get_state()

    def apply_action(self, action: str, action_kw: float, duration_hours: float = 0.0833) -> Dict[str, Any]:
        """
        Apply AI action to prosumer.

        Args:
            action: Action name (charge_battery, discharge_battery, sell_surplus, etc.)
            action_kw: Power in kW
            duration_hours: Timestep duration

        Returns:
            Action result
        """
        result = {
            "action": action,
            "requested_kw": action_kw,
            "actual_kw": 0.0,
            "energy_kwh": 0.0,
            "grid_import_kw": 0.0,
            "grid_export_kw": 0.0,
            "tradeable_surplus_kwh": 0.0,
            "unmet_demand_kwh": 0.0,
        }

        if action in ["charge_battery", "charge_small", "charge_large"]:
            # Charge battery from solar surplus or grid
            charge_kw = abs(action_kw)
            if self.net_power_kw > 0:
                # Use solar surplus first
                from_solar = min(self.net_power_kw, charge_kw)
                from_grid = max(0, charge_kw - from_solar)
            else:
                from_solar = 0
                from_grid = charge_kw

            energy = self.battery.charge(charge_kw, duration_hours)
            result["actual_kw"] = charge_kw
            result["energy_kwh"] = energy
            result["grid_import_kw"] = from_grid
            self.grid_import_kw = from_grid

        elif action in ["discharge_battery", "discharge_small", "discharge_large"]:
            # Discharge battery to meet load or export
            discharge_kw = abs(action_kw)
            energy = self.battery.discharge(discharge_kw, duration_hours)
            result["actual_kw"] = -discharge_kw
            result["energy_kwh"] = -energy
            self.grid_export_kw = max(0, energy / duration_hours - abs(self.net_power_kw))

        elif action in ["sell_surplus", "offer_sell"]:
            # Sell surplus energy
            if self.net_power_kw > 0.1:
                surplus = self.net_power_kw * duration_hours
                result["tradeable_surplus_kwh"] = surplus
                result["grid_export_kw"] = self.net_power_kw
                self.grid_export_kw = self.net_power_kw
                self.trade_status = "offering"

        elif action in ["buy_energy"]:
            # Buy energy from P2P market
            if self.net_power_kw < -0.1:
                deficit = abs(self.net_power_kw) * duration_hours
                result["unmet_demand_kwh"] = deficit
                result["grid_import_kw"] = abs(self.net_power_kw)
                self.grid_import_kw = abs(self.net_power_kw)
                self.trade_status = "bidding"

        else:
            # Hold/idle - handle net power normally
            if self.net_power_kw > 0:
                # Surplus - try to charge battery
                self.battery.charge(min(self.net_power_kw, 1.0), duration_hours)
            else:
                # Deficit - try to discharge battery
                energy = self.battery.discharge(min(abs(self.net_power_kw), 1.0), duration_hours)
                if energy < abs(self.net_power_kw) * duration_hours:
                    self.grid_import_kw = abs(self.net_power_kw) - (energy / duration_hours)

        return result

    def get_state(self) -> Dict[str, Any]:
        """Get current prosumer state"""
        battery = self.battery.get_state()
        return {
            "prosumer_id": self.config.prosumer_id,
            "name": self.config.name,
            "solar_kw": round(self.current_solar_kw, 3),
            "load_kw": round(self.current_load_kw, 3),
            "net_power_kw": round(self.net_power_kw, 3),
            "grid_import_kw": round(self.grid_import_kw, 3),
            "grid_export_kw": round(self.grid_export_kw, 3),
            "battery": battery,
            "trade_status": self.trade_status,
            "surplus_kw": max(0, self.net_power_kw),
            "deficit_kw": abs(min(0, self.net_power_kw)),
        }

    def get_ai_observation(self) -> Dict[str, float]:
        """Get observation dict for AI inference"""
        battery = self.battery.state
        return {
            "soc_kwh": battery.soc_kwh,
            "soc_capacity_kwh": battery.capacity_kwh,
            "pv_gen_kw": self.current_solar_kw,
            "load_kw": self.current_load_kw,
            "net_kw": self.net_power_kw,
            "battery_power_kw": 0.0,
            "price_signal": 5.0,  # Default price
            "forecast_irradiance_1h": 400,
            "forecast_irradiance_3h": 350,
            "forecast_temp_1h": 25,
            "actual_irradiance_wm2": 500 * max(0, self.current_solar_kw / self.config.solar_capacity_kw),
            "voltage_v": 230.0 + random.uniform(-5, 5),
            "current_a": self.current_load_kw * 1000 / 230,
            "neighbor_balance": 0.0,
            "timestamp": time.time(),
        }


# ============================================================================
# Market Engine
# ============================================================================

class MarketEngine:
    """Simple P2P market matching engine"""

    def __init__(self):
        self.offers: List[Dict] = []
        self.bids: List[Dict] = []
        self.trades: List[Dict] = []
        self.trade_counter = 0
        self.current_price = 5.0  # cents/kWh

    def submit_offer(self, prosumer_id: str, quantity_kwh: float, price: float):
        """Submit sell offer"""
        offer = {
            "id": f"offer-{len(self.offers)+1}",
            "prosumer_id": prosumer_id,
            "type": "sell",
            "quantity_kwh": quantity_kwh,
            "price_per_kwh": price,
            "status": "open",
            "timestamp": time.time(),
        }
        self.offers.append(offer)
        logger.info(f"[MARKET] Offer: {prosumer_id} selling {quantity_kwh:.2f} kWh @ {price:.2f}c")
        return offer

    def submit_bid(self, prosumer_id: str, quantity_kwh: float, max_price: float):
        """Submit buy bid"""
        bid = {
            "id": f"bid-{len(self.bids)+1}",
            "prosumer_id": prosumer_id,
            "type": "buy",
            "quantity_kwh": quantity_kwh,
            "max_price_per_kwh": max_price,
            "status": "open",
            "timestamp": time.time(),
        }
        self.bids.append(bid)
        logger.info(f"[MARKET] Bid: {prosumer_id} buying {quantity_kwh:.2f} kWh @ max {max_price:.2f}c")
        return bid

    def run_matching(self) -> List[Dict]:
        """Run matching algorithm"""
        matches = []

        open_offers = [o for o in self.offers if o["status"] == "open"]
        open_bids = [b for b in self.bids if b["status"] == "open"]

        # Sort by price (offers ascending, bids descending)
        open_offers.sort(key=lambda x: x["price_per_kwh"])
        open_bids.sort(key=lambda x: x["max_price_per_kwh"], reverse=True)

        for bid in open_bids:
            for offer in open_offers:
                if offer["status"] != "open":
                    continue

                # Check price compatibility
                if bid["max_price_per_kwh"] >= offer["price_per_kwh"]:
                    # Match found
                    quantity = min(bid["quantity_kwh"], offer["quantity_kwh"])
                    price = (bid["max_price_per_kwh"] + offer["price_per_kwh"]) / 2

                    self.trade_counter += 1
                    trade = {
                        "trade_id": f"trade-{self.trade_counter}",
                        "seller_id": offer["prosumer_id"],
                        "buyer_id": bid["prosumer_id"],
                        "quantity_kwh": quantity,
                        "price_per_kwh": price,
                        "total_price": quantity * price,
                        "status": "matched",
                        "timestamp": time.time(),
                        "blockchain_tx_hash": None,
                    }

                    # Update statuses
                    offer["status"] = "matched"
                    bid["status"] = "matched"

                    self.trades.append(trade)
                    matches.append(trade)

                    logger.info(
                        f"[MARKET] Trade matched: {trade['seller_id']} -> {trade['buyer_id']} "
                        f"({quantity:.2f} kWh @ {price:.2f}c)"
                    )
                    break

        return matches

    def settle_trade(self, trade_id: str) -> Dict:
        """Settle a matched trade"""
        for trade in self.trades:
            if trade["trade_id"] == trade_id and trade["status"] == "matched":
                trade["status"] = "settled"
                trade["settlement_time"] = time.time()
                trade["blockchain_tx_hash"] = f"0x{int(time.time()*1000):016x}{random.randint(0, 0xFFFF):04x}"
                logger.info(f"[BLOCKCHAIN] Trade {trade_id} settled: {trade['blockchain_tx_hash']}")
                return trade
        return None

    def get_state(self) -> Dict:
        """Get market state"""
        return {
            "current_price": self.current_price,
            "open_offers": len([o for o in self.offers if o["status"] == "open"]),
            "open_bids": len([b for b in self.bids if b["status"] == "open"]),
            "total_trades": len(self.trades),
            "recent_trades": self.trades[-5:] if self.trades else [],
        }


# ============================================================================
# Demo Orchestrator
# ============================================================================

class DemoOrchestrator:
    """Orchestrates the end-to-end demo"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

        # Simulation state
        self.simulation_hour = self.config.get("start_hour", 10.0)
        self.simulation_day = 0
        self.tick_count = 0
        self.timestep_minutes = self.config.get("timestep_minutes", 5)
        self.is_running = False

        # Components
        self.prosumers: Dict[str, VirtualProsumer] = {}
        self.market = MarketEngine()
        self.policy_selector: Optional[PolicySelector] = None
        self.condition_detector: Optional[ConditionDetector] = None

        # Event log
        self.event_log: List[Dict] = []
        self.max_events = 100

        # Initialize
        self._init_components()

    def _init_components(self):
        """Initialize demo components"""
        # Create prosumers
        prosumer_configs = self.config.get("prosumers", [
            {
                "prosumer_id": "prosumer-a",
                "name": "Solar Home A (Seller)",
                "solar_capacity_kw": 6.0,
                "base_load_kw": 0.8,
                "peak_load_kw": 2.5,
                "battery_config": {"capacity_kwh": 5.0, "initial_soc_kwh": 3.5},
            },
            {
                "prosumer_id": "prosumer-b",
                "name": "Neighbor B (Buyer)",
                "solar_capacity_kw": 2.0,
                "base_load_kw": 1.2,
                "peak_load_kw": 4.0,
                "battery_config": {"capacity_kwh": 4.0, "initial_soc_kwh": 1.5},
            },
        ])

        for pc in prosumer_configs:
            config = ProsumerConfig(**pc)
            self.prosumers[config.prosumer_id] = VirtualProsumer(config)
            logger.info(f"Created prosumer: {config.prosumer_id} ({config.name})")

        # Initialize AI components
        if SELECTOR_AVAILABLE:
            models_dir = self.config.get("models_dir")
            if not models_dir:
                # Try default locations
                for d in [
                    Path(__file__).parent.parent / "grid_guardian_edge" / "models",
                    Path(__file__).parent / "edge" / "policy_pack",
                ]:
                    if d.exists():
                        models_dir = str(d)
                        break

            if models_dir:
                self.policy_selector = PolicySelector(models_dir)
                load_results = self.policy_selector.load_models()
                loaded = sum(1 for v in load_results.values() if v)
                logger.info(f"Loaded {loaded}/3 AI policies from {models_dir}")
            else:
                logger.warning("No models directory found - using rule-based fallback")

    def _log_event(self, event_type: str, data: Dict):
        """Log an event"""
        event = {
            "type": event_type,
            "tick": self.tick_count,
            "hour": self.simulation_hour,
            "timestamp": time.time(),
            "data": data,
        }
        self.event_log.append(event)
        if len(self.event_log) > self.max_events:
            self.event_log.pop(0)

    def tick(self) -> Dict[str, Any]:
        """Run a single simulation tick"""
        self.tick_count += 1
        timestep_hours = self.timestep_minutes / 60

        # Random cloud factor
        cloud_factor = random.random() * 0.3

        tick_result = {
            "tick": self.tick_count,
            "hour": round(self.simulation_hour, 2),
            "day": self.simulation_day,
            "timestamp": time.time(),
            "prosumers": {},
            "ai_decisions": {},
            "trades": [],
            "market": None,
            "events": [],
        }

        logger.info(f"\n{'='*60}")
        logger.info(f"TICK {self.tick_count} | Hour: {self.simulation_hour:.1f} | Day: {self.simulation_day}")
        logger.info(f"{'='*60}")

        # Process each prosumer
        for pid, prosumer in self.prosumers.items():
            # 1. Update prosumer state
            prosumer.update(self.simulation_hour, cloud_factor)
            state = prosumer.get_state()

            logger.info(f"\n[{pid}] Solar: {state['solar_kw']:.2f}kW | Load: {state['load_kw']:.2f}kW | "
                       f"Net: {state['net_power_kw']:+.2f}kW | SoC: {state['battery']['soc_fraction']:.0%}")

            # 2. Get AI decision
            obs = prosumer.get_ai_observation()

            if self.policy_selector:
                decision = self.policy_selector.infer(obs)
            else:
                # Rule-based fallback
                decision = self._rule_based_decision(prosumer)

            prosumer.last_ai_decision = decision
            prosumer.last_condition = decision.get("condition", "normal")
            prosumer.last_selected_model = decision.get("selected_policy", "RULE")

            logger.info(f"    AI: {decision.get('action_name', 'unknown')} | "
                       f"Model: {decision.get('selected_policy', 'rule')} | "
                       f"Condition: {decision.get('condition', 'normal')} | "
                       f"Confidence: {decision.get('confidence', 0):.2f}")

            self._log_event("ai_decision", {
                "prosumer_id": pid,
                "action": decision.get("action_name"),
                "policy": decision.get("selected_policy"),
                "condition": decision.get("condition"),
            })

            # 3. Apply action
            action_result = prosumer.apply_action(
                decision.get("action_name", "idle"),
                decision.get("action_kw", 0),
                timestep_hours
            )

            # 4. Handle trading
            if action_result.get("tradeable_surplus_kwh", 0) > 0.1:
                self.market.submit_offer(
                    pid,
                    action_result["tradeable_surplus_kwh"],
                    self.market.current_price * 0.95
                )
                self._log_event("offer_created", {"prosumer_id": pid, "kwh": action_result["tradeable_surplus_kwh"]})

            if action_result.get("unmet_demand_kwh", 0) > 0.1:
                self.market.submit_bid(
                    pid,
                    action_result["unmet_demand_kwh"],
                    self.market.current_price * 1.05
                )
                self._log_event("bid_created", {"prosumer_id": pid, "kwh": action_result["unmet_demand_kwh"]})

            # Store results
            tick_result["prosumers"][pid] = prosumer.get_state()
            tick_result["ai_decisions"][pid] = decision

        # 5. Run market matching
        matches = self.market.run_matching()
        for trade in matches:
            tick_result["trades"].append(trade)
            self._log_event("trade_matched", trade)

            # Settle trade immediately for demo
            self.market.settle_trade(trade["trade_id"])
            self._log_event("trade_settled", {"trade_id": trade["trade_id"]})

        # 6. Get market state
        tick_result["market"] = self.market.get_state()

        # 7. Advance time
        self._advance_time()

        # 8. Get recent events
        tick_result["events"] = self.event_log[-10:]

        return tick_result

    def _rule_based_decision(self, prosumer: VirtualProsumer) -> Dict:
        """Rule-based fallback decision"""
        state = prosumer.get_state()
        battery = state["battery"]
        net_kw = state["net_power_kw"]
        soc = battery["soc_fraction"]

        if net_kw > 0.5 and soc < 0.85:
            action = "charge_small"
            action_kw = 1.0
            decision = "CHARGE"
        elif net_kw > 0.5 and soc >= 0.85:
            action = "offer_sell"
            action_kw = 0
            decision = "SELL"
        elif net_kw < -0.5 and soc > 0.25:
            action = "discharge_small"
            action_kw = -1.0
            decision = "DISCHARGE"
        elif net_kw < -0.5 and soc <= 0.25:
            action = "buy_energy"
            action_kw = 0
            decision = "BUY"
        else:
            action = "idle"
            action_kw = 0
            decision = "HOLD"

        return {
            "action_name": action,
            "action_kw": action_kw,
            "decision": decision,
            "confidence": 0.7,
            "selected_policy": "RULE_BASED",
            "policy_reason": "No AI models - using rule-based fallback",
            "condition": "normal",
            "condition_confidence": 0.7,
            "volatility": 0.0,
            "sub_conditions": [],
        }

    def _advance_time(self):
        """Advance simulation time"""
        self.simulation_hour += self.timestep_minutes / 60

        if self.simulation_hour >= 24:
            self.simulation_hour -= 24
            self.simulation_day += 1
            logger.info(f"\n{'*'*60}")
            logger.info(f"*** NEW DAY: {self.simulation_day} ***")
            logger.info(f"{'*'*60}\n")

    def run(self, num_ticks: int = 10, interval_seconds: float = 2.0):
        """Run demo for specified number of ticks"""
        self.is_running = True

        logger.info(f"\n{'#'*60}")
        logger.info(f"# GRID GUARDIAN DEMO STARTING")
        logger.info(f"# Prosumers: {len(self.prosumers)}")
        logger.info(f"# AI Models: {'PolicySelector' if self.policy_selector else 'Rule-based'}")
        logger.info(f"# Ticks: {num_ticks} @ {interval_seconds}s interval")
        logger.info(f"{'#'*60}\n")

        try:
            for i in range(num_ticks):
                if not self.is_running:
                    break

                tick_result = self.tick()

                # Print summary
                self._print_summary(tick_result)

                if i < num_ticks - 1:
                    time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("\nDemo interrupted by user")
            self.is_running = False

        self._print_final_summary()

    def _print_summary(self, tick_result: Dict):
        """Print tick summary"""
        print()
        print("-" * 60)
        for pid, state in tick_result["prosumers"].items():
            ai = tick_result["ai_decisions"].get(pid, {})
            print(f"  {state['name']}")
            print(f"    Net: {state['net_power_kw']:+.2f}kW | SoC: {state['battery']['soc_fraction']:.0%}")
            print(f"    Action: {ai.get('action_name', 'none')} [{ai.get('selected_policy', 'none')}]")

        if tick_result["trades"]:
            print(f"\n  Trades: {len(tick_result['trades'])}")
            for t in tick_result["trades"]:
                print(f"    {t['seller_id']} -> {t['buyer_id']}: {t['quantity_kwh']:.2f}kWh @ {t['price_per_kwh']:.1f}c")

        print("-" * 60)

    def _print_final_summary(self):
        """Print final demo summary"""
        print()
        print("=" * 60)
        print("DEMO SUMMARY")
        print("=" * 60)
        print(f"Total Ticks: {self.tick_count}")
        print(f"Total Trades: {len(self.market.trades)}")
        print(f"Simulation Days: {self.simulation_day}")

        for pid, prosumer in self.prosumers.items():
            state = prosumer.get_state()
            print(f"\n{state['name']}:")
            print(f"  Final SoC: {state['battery']['soc_fraction']:.0%}")

        if self.policy_selector:
            stats = self.policy_selector.get_selection_stats()
            print(f"\nAI Policy Usage:")
            for policy, frac in stats.get("by_policy", {}).items():
                print(f"  {policy}: {frac:.0%}")

        print("=" * 60)


# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Grid-Guardian Demo Orchestrator")
    parser.add_argument("--ticks", type=int, default=20, help="Number of simulation ticks")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between ticks")
    parser.add_argument("--start-hour", type=float, default=10.0, help="Starting hour (0-23)")
    parser.add_argument("--timestep", type=int, default=5, help="Timestep in minutes")
    parser.add_argument("--models-dir", type=str, help="Path to AI models directory")
    args = parser.parse_args()

    config = {
        "start_hour": args.start_hour,
        "timestep_minutes": args.timestep,
        "models_dir": args.models_dir,
    }

    demo = DemoOrchestrator(config)
    demo.run(num_ticks=args.ticks, interval_seconds=args.interval)


if __name__ == "__main__":
    main()
