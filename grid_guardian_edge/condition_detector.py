"""
Grid-Guardian Edge - Condition Detector
Determines operating condition from sensor data for model routing
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, List

logger = logging.getLogger(__name__)


class ConditionDetector:
    """
    Detects the current operating condition based on sensor data.

    Conditions are used to route to the appropriate model and
    inform safety decisions.

    Operating Conditions:
    - normal: Balanced operation
    - high_pv: High solar generation (export opportunity)
    - high_load: High load demand (import/discharge)
    - low_soc: Low battery state (conservative)
    - peak_price: Peak pricing (maximize exports)
    - off_peak: Off-peak pricing (charge battery)
    - fault: System fault (safe mode)
    """

    VALID_CONDITIONS = {
        "normal",
        "high_pv",
        "high_load",
        "low_soc",
        "peak_price",
        "off_peak",
        "fault",
    }

    def __init__(self, routing_config_path: Optional[str] = None):
        """
        Initialize condition detector.

        Args:
            routing_config_path: Path to model_routing.json
        """
        self.thresholds = self._default_thresholds()
        self.current_condition = "normal"
        self.condition_history: List[str] = []
        self.history_max = 10

        # Load thresholds from routing config if available
        if routing_config_path:
            self._load_thresholds(routing_config_path)

        # Statistics
        self.stats = {
            "detections": 0,
            "condition_counts": {c: 0 for c in self.VALID_CONDITIONS},
            "faults_detected": 0,
        }

    def _default_thresholds(self) -> Dict[str, float]:
        """Default condition thresholds"""
        return {
            "high_pv_kw": 2.5,
            "high_load_kw": 2.0,
            "low_soc_fraction": 0.15,
            "peak_price_threshold": 6.5,
            "off_peak_price_threshold": 4.0,
            "voltage_min": 190,
            "voltage_max": 250,
            "current_max": 25,
            "soc_capacity_default": 4.0,
        }

    def _load_thresholds(self, config_path: str):
        """Load thresholds from routing config"""
        try:
            path = Path(config_path)
            if path.exists():
                with open(path, "r") as f:
                    config = json.load(f)
                    if "condition_thresholds" in config:
                        self.thresholds.update(config["condition_thresholds"])
                        logger.info(f"Loaded condition thresholds from {config_path}")
        except Exception as e:
            logger.warning(f"Could not load thresholds from {config_path}: {e}")

    def detect_condition(self, sensor_data: Dict[str, Any]) -> str:
        """
        Detect current operating condition from sensor data.

        Args:
            sensor_data: Dictionary with sensor readings

        Returns:
            Condition string (e.g., "normal", "high_pv", "fault")
        """
        self.stats["detections"] += 1

        # Extract values with defaults
        voltage = sensor_data.get("voltage", 230)
        current = sensor_data.get("current", 0)
        power = sensor_data.get("power", 0)
        pv_gen = sensor_data.get("pv_gen_kw", 0)
        load = sensor_data.get("load_kw", power / 1000 if power > 0 else 0)
        soc = sensor_data.get("soc_kwh", 2.0)
        soc_capacity = sensor_data.get("soc_capacity_kwh", self.thresholds["soc_capacity_default"])
        price = sensor_data.get("price_signal", 5.0)
        valid = sensor_data.get("valid", True)

        # Check for fault conditions first (highest priority)
        if not valid:
            return self._set_condition("fault", "Invalid sensor data")

        if voltage < self.thresholds["voltage_min"] or voltage > self.thresholds["voltage_max"]:
            return self._set_condition("fault", f"Voltage out of range: {voltage}V")

        if current > self.thresholds["current_max"]:
            return self._set_condition("fault", f"Over-current: {current}A")

        # Calculate SoC fraction
        soc_fraction = soc / soc_capacity if soc_capacity > 0 else 0.5

        # Check for low SoC (high priority - protect battery)
        if soc_fraction < self.thresholds["low_soc_fraction"]:
            return self._set_condition("low_soc", f"SoC low: {soc_fraction:.1%}")

        # Check pricing conditions
        if price >= self.thresholds["peak_price_threshold"]:
            return self._set_condition("peak_price", f"Peak price: {price}")

        if price <= self.thresholds["off_peak_price_threshold"]:
            return self._set_condition("off_peak", f"Off-peak price: {price}")

        # Check generation/load conditions
        if pv_gen >= self.thresholds["high_pv_kw"]:
            return self._set_condition("high_pv", f"High PV: {pv_gen:.1f}kW")

        if load >= self.thresholds["high_load_kw"]:
            return self._set_condition("high_load", f"High load: {load:.1f}kW")

        # Default to normal operation
        return self._set_condition("normal", "Normal operation")

    def _set_condition(self, condition: str, reason: str = "") -> str:
        """Set and log condition change"""
        if condition not in self.VALID_CONDITIONS:
            logger.warning(f"Unknown condition: {condition}, defaulting to normal")
            condition = "normal"

        # Track condition changes
        if condition != self.current_condition:
            logger.info(f"Condition changed: {self.current_condition} -> {condition} ({reason})")

        self.current_condition = condition
        self.stats["condition_counts"][condition] += 1

        if condition == "fault":
            self.stats["faults_detected"] += 1

        # Update history
        self.condition_history.append(condition)
        if len(self.condition_history) > self.history_max:
            self.condition_history.pop(0)

        return condition

    def get_current_condition(self) -> str:
        """Get the current operating condition"""
        return self.current_condition

    def is_fault_condition(self) -> bool:
        """Check if currently in fault condition"""
        return self.current_condition == "fault"

    def get_condition_details(self, sensor_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Get detailed condition analysis.

        Args:
            sensor_data: Current sensor readings

        Returns:
            Dictionary with condition details
        """
        condition = self.detect_condition(sensor_data)

        voltage = sensor_data.get("voltage", 230)
        current = sensor_data.get("current", 0)
        pv_gen = sensor_data.get("pv_gen_kw", 0)
        load = sensor_data.get("load_kw", 0)
        soc = sensor_data.get("soc_kwh", 2.0)
        soc_capacity = sensor_data.get("soc_capacity_kwh", self.thresholds["soc_capacity_default"])
        price = sensor_data.get("price_signal", 5.0)

        soc_fraction = soc / soc_capacity if soc_capacity > 0 else 0.5

        return {
            "condition": condition,
            "is_fault": condition == "fault",
            "metrics": {
                "voltage_v": voltage,
                "current_a": current,
                "pv_gen_kw": pv_gen,
                "load_kw": load,
                "soc_kwh": soc,
                "soc_fraction": round(soc_fraction, 3),
                "price_signal": price,
            },
            "thresholds": self.thresholds,
            "history": self.condition_history[-5:],
        }

    def get_dominant_condition(self, lookback: int = 5) -> str:
        """
        Get the most frequent condition in recent history.
        Useful for smoothing noisy transitions.

        Args:
            lookback: Number of recent conditions to consider

        Returns:
            Most frequent condition
        """
        if not self.condition_history:
            return self.current_condition

        recent = self.condition_history[-lookback:]
        counts = {}
        for c in recent:
            counts[c] = counts.get(c, 0) + 1

        return max(counts, key=counts.get)

    def get_stats(self) -> Dict[str, Any]:
        """Get detector statistics"""
        return {
            **self.stats,
            "current_condition": self.current_condition,
            "history_length": len(self.condition_history),
        }

    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "detections": 0,
            "condition_counts": {c: 0 for c in self.VALID_CONDITIONS},
            "faults_detected": 0,
        }
        self.condition_history.clear()
