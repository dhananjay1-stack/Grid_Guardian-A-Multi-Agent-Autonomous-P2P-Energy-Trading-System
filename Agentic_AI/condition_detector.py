#!/usr/bin/env python3
"""
Grid-Guardian Condition Detector

Analyzes prosumer state and market conditions to determine the operating regime.
This drives the condition-to-model selection logic.

Conditions:
- normal: Stable operation, balanced supply/demand
- high_pv: High solar generation, excess energy
- high_load: High demand, deficit energy
- low_soc: Battery critically low
- high_soc: Battery near full
- peak_price: Grid prices elevated
- off_peak: Grid prices low
- volatile: Rapid changes in generation/load
- fault: Sensor issues or invalid data

Metrics (used for deterministic model routing):
- sensor_health: 0.0–1.0, derived from voltage/current deviation
- anomaly_score: 0.0–1.0, derived from net_kw deviation from recent history
- grid_risk: 0.0–1.0, derived from price extremes + SoC stress
"""

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class OperatingCondition(Enum):
    """Operating condition categories"""
    NORMAL = "normal"
    HIGH_PV = "high_pv"
    HIGH_LOAD = "high_load"
    LOW_SOC = "low_soc"
    HIGH_SOC = "high_soc"
    PEAK_PRICE = "peak_price"
    OFF_PEAK = "off_peak"
    VOLATILE = "volatile"
    FAULT = "fault"


@dataclass
class ConditionThresholds:
    """Configurable thresholds for condition detection"""
    # Solar thresholds (kW)
    high_pv_min: float = 2.0
    low_pv_max: float = 0.3

    # Load thresholds (kW)
    high_load_min: float = 2.5
    low_load_max: float = 0.5

    # Battery SoC thresholds (fraction)
    low_soc_threshold: float = 0.20
    high_soc_threshold: float = 0.85
    critical_soc_low: float = 0.10
    critical_soc_high: float = 0.95

    # Price thresholds (relative to base price)
    peak_price_multiplier: float = 1.5
    off_peak_price_multiplier: float = 0.7
    base_price: float = 5.0  # ₹/kWh

    # Volatility thresholds
    volatility_window: int = 6  # Number of samples for volatility calc
    high_volatility_threshold: float = 0.25  # 25% std/mean

    # Net energy thresholds (kW)
    surplus_threshold: float = 0.5
    deficit_threshold: float = -0.5

    # Sensor validity
    min_voltage: float = 200.0
    max_voltage: float = 260.0
    nominal_voltage: float = 230.0
    max_current: float = 50.0
    min_power_factor: float = 0.7

    # Anomaly detection
    anomaly_std_multiplier: float = 2.0  # net_kw > mean + N*std → anomaly


@dataclass
class ConditionResult:
    """Result of condition detection"""
    condition: OperatingCondition
    confidence: float
    volatility: float
    sub_conditions: List[str]
    metrics: Dict[str, float]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "condition": self.condition.value,
            "confidence": self.confidence,
            "volatility": self.volatility,
            "sub_conditions": self.sub_conditions,
            "metrics": self.metrics,
            "timestamp": self.timestamp,
        }


class ConditionDetector:
    """
    Detects operating condition from prosumer state.

    Uses a weighted multi-factor analysis to determine:
    1. Primary condition (most significant factor)
    2. Sub-conditions (secondary factors)
    3. Confidence score
    4. Volatility measure
    5. sensor_health, anomaly_score, grid_risk (for model routing)
    """

    def __init__(self, thresholds: Optional[ConditionThresholds] = None):
        self.thresholds = thresholds or ConditionThresholds()
        self.history: List[Dict[str, float]] = []
        self.max_history = 50

    def detect(self, observation: Dict[str, float]) -> ConditionResult:
        """
        Detect operating condition from observation.

        Args:
            observation: Dict with keys like:
                - soc_kwh, soc_capacity_kwh
                - pv_gen_kw, load_kw, net_kw
                - price_signal
                - voltage_v, current_a

        Returns:
            ConditionResult with condition, confidence, volatility,
            and metrics including sensor_health, anomaly_score, grid_risk
        """
        # Validate and extract values
        soc_frac = self._safe_div(
            observation.get("soc_kwh", 2.0),
            observation.get("soc_capacity_kwh", 4.0)
        )
        pv_gen = observation.get("pv_gen_kw", 0.0)
        load_kw = observation.get("load_kw", 0.5)
        net_kw = observation.get("net_kw", pv_gen - load_kw)
        price = observation.get("price_signal", self.thresholds.base_price)
        voltage = observation.get("voltage_v", 230.0)
        current = observation.get("current_a", 0.0)

        # Update history for volatility calculation
        self._update_history(observation)

        # Check for fault conditions first
        fault_check = self._check_faults(voltage, current, observation)
        if fault_check:
            # Even on fault, compute metrics for downstream
            fault_check.metrics["sensor_health"] = self._compute_sensor_health(voltage, current)
            fault_check.metrics["anomaly_score"] = 1.0
            fault_check.metrics["grid_risk"] = 1.0
            fault_check.metrics["soc_fraction"] = soc_frac
            return fault_check

        # Calculate individual condition scores
        condition_scores = {}
        sub_conditions = []

        # 1. Check battery state
        if soc_frac <= self.thresholds.critical_soc_low:
            condition_scores[OperatingCondition.LOW_SOC] = 1.0
            sub_conditions.append("critical_battery_low")
        elif soc_frac <= self.thresholds.low_soc_threshold:
            condition_scores[OperatingCondition.LOW_SOC] = 0.8
            sub_conditions.append("battery_low")
        elif soc_frac >= self.thresholds.critical_soc_high:
            condition_scores[OperatingCondition.HIGH_SOC] = 1.0
            sub_conditions.append("battery_full")
        elif soc_frac >= self.thresholds.high_soc_threshold:
            condition_scores[OperatingCondition.HIGH_SOC] = 0.7
            sub_conditions.append("battery_high")

        # 2. Check generation/load
        if pv_gen >= self.thresholds.high_pv_min:
            score = min(1.0, pv_gen / (self.thresholds.high_pv_min * 2))
            condition_scores[OperatingCondition.HIGH_PV] = score
            sub_conditions.append("high_solar")

        if load_kw >= self.thresholds.high_load_min:
            score = min(1.0, load_kw / (self.thresholds.high_load_min * 2))
            condition_scores[OperatingCondition.HIGH_LOAD] = score
            sub_conditions.append("high_load")

        # 3. Check price signal
        price_ratio = price / self.thresholds.base_price
        if price_ratio >= self.thresholds.peak_price_multiplier:
            score = min(1.0, (price_ratio - 1) / 0.5)
            condition_scores[OperatingCondition.PEAK_PRICE] = score
            sub_conditions.append("peak_price")
        elif price_ratio <= self.thresholds.off_peak_price_multiplier:
            score = min(1.0, (1 - price_ratio) / 0.3)
            condition_scores[OperatingCondition.OFF_PEAK] = score
            sub_conditions.append("off_peak")

        # 4. Calculate volatility
        volatility = self._calculate_volatility()
        if volatility >= self.thresholds.high_volatility_threshold:
            condition_scores[OperatingCondition.VOLATILE] = min(1.0, volatility / 0.4)
            sub_conditions.append("volatile_conditions")

        # 5. Determine primary condition
        if not condition_scores:
            # Normal operation
            primary_condition = OperatingCondition.NORMAL
            confidence = 0.9
        else:
            # Get highest scoring condition
            primary_condition = max(condition_scores, key=condition_scores.get)
            confidence = condition_scores[primary_condition]

        # 6. Compute routing metrics
        sensor_health = self._compute_sensor_health(voltage, current)
        anomaly_score = self._compute_anomaly_score(net_kw)
        grid_risk = self._compute_grid_risk(price_ratio, soc_frac, volatility)

        # Build metrics
        metrics = {
            "soc_fraction": soc_frac,
            "pv_gen_kw": pv_gen,
            "load_kw": load_kw,
            "net_kw": net_kw,
            "price_ratio": price_ratio,
            "volatility": volatility,
            "surplus_kw": max(0, net_kw),
            "deficit_kw": abs(min(0, net_kw)),
            # New routing metrics
            "sensor_health": sensor_health,
            "anomaly_score": anomaly_score,
            "grid_risk": grid_risk,
        }

        return ConditionResult(
            condition=primary_condition,
            confidence=confidence,
            volatility=volatility,
            sub_conditions=sub_conditions,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Routing metrics
    # ------------------------------------------------------------------

    def _compute_sensor_health(self, voltage: float, current: float) -> float:
        """
        Sensor health: 1.0 = perfect, 0.0 = faulty.
        Degrades when voltage deviates from nominal or current is extreme.
        """
        nom = self.thresholds.nominal_voltage
        v_range = self.thresholds.max_voltage - self.thresholds.min_voltage
        v_dev = abs(voltage - nom) / (v_range / 2)  # 0 = perfect, 1 = at limit
        v_health = max(0.0, 1.0 - v_dev)

        c_dev = current / self.thresholds.max_current  # 0 = no load, 1 = at limit
        c_health = max(0.0, 1.0 - c_dev * 0.5)  # penalise less than voltage

        return round(min(v_health, c_health), 3)

    def _compute_anomaly_score(self, net_kw: float) -> float:
        """
        Anomaly score: 0.0 = normal, 1.0 = extreme anomaly.
        Based on how far current net_kw is from recent history.
        """
        if len(self.history) < 3:
            return 0.0

        recent = self.history[-min(len(self.history), self.thresholds.volatility_window):]
        net_vals = [h["net_kw"] for h in recent]
        mean = np.mean(net_vals)
        std = max(np.std(net_vals), 0.1)  # floor to avoid div-by-zero

        deviation = abs(net_kw - mean) / std
        # Map: 0–1 std → 0, 1–3 std → 0–1
        score = min(1.0, max(0.0, (deviation - 1.0) / self.thresholds.anomaly_std_multiplier))
        return round(score, 3)

    def _compute_grid_risk(self, price_ratio: float, soc_frac: float,
                           volatility: float) -> float:
        """
        Grid risk: 0.0 = safe, 1.0 = high risk.
        Combines price stress, battery vulnerability, and volatility.
        """
        # Price component: risk rises when price > 1.3x base
        price_risk = min(1.0, max(0.0, (price_ratio - 1.0) / 1.0))

        # SoC component: risk rises at extremes (< 25% or > 90%)
        if soc_frac < 0.25:
            soc_risk = 1.0 - soc_frac / 0.25  # 0% → 1.0, 25% → 0.0
        elif soc_frac > 0.90:
            soc_risk = (soc_frac - 0.90) / 0.10  # 90% → 0.0, 100% → 1.0
        else:
            soc_risk = 0.0

        # Volatility component
        vol_risk = min(1.0, volatility / 0.4)

        # Weighted combination
        risk = 0.35 * price_risk + 0.40 * soc_risk + 0.25 * vol_risk
        return round(min(1.0, risk), 3)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_faults(self, voltage: float, current: float,
                      obs: Dict[str, float]) -> Optional[ConditionResult]:
        """Check for fault conditions"""
        faults = []

        if voltage < self.thresholds.min_voltage or voltage > self.thresholds.max_voltage:
            faults.append("voltage_out_of_range")

        if current > self.thresholds.max_current:
            faults.append("overcurrent")

        # Check for NaN/Inf values
        for key, value in obs.items():
            if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                faults.append(f"invalid_{key}")

        if faults:
            return ConditionResult(
                condition=OperatingCondition.FAULT,
                confidence=1.0,
                volatility=0.0,
                sub_conditions=faults,
                metrics={"fault_count": len(faults)},
            )
        return None

    def _update_history(self, observation: Dict[str, float]):
        """Update observation history for volatility calculation"""
        self.history.append({
            "pv_gen_kw": observation.get("pv_gen_kw", 0),
            "load_kw": observation.get("load_kw", 0),
            "net_kw": observation.get("net_kw", 0),
            "timestamp": time.time(),
        })

        if len(self.history) > self.max_history:
            self.history.pop(0)

    def _calculate_volatility(self) -> float:
        """Calculate volatility from recent history"""
        if len(self.history) < self.thresholds.volatility_window:
            return 0.0

        recent = self.history[-self.thresholds.volatility_window:]
        net_values = [h["net_kw"] for h in recent]

        mean = np.mean(net_values)
        std = np.std(net_values)

        if abs(mean) < 0.5:
            # Near-zero net power: use absolute std (capped)
            # Typical residential std is 0.2–1.0 kW
            # Map: 0 → 0.0, 0.5 → 0.25, 1.0 → 0.5
            return float(min(std * 0.5, 0.5))
        else:
            # Use coefficient of variation, capped at 0.5
            cv = std / abs(mean)
            return float(min(cv, 0.5))

    def _safe_div(self, a: float, b: float, default: float = 0.5) -> float:
        """Safe division with default for zero denominator"""
        if b == 0:
            return default
        return a / b

    def reset(self):
        """Clear history"""
        self.history.clear()


# Singleton instance
_detector_instance: Optional[ConditionDetector] = None


def get_condition_detector(thresholds: Optional[ConditionThresholds] = None) -> ConditionDetector:
    """Get or create condition detector singleton"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = ConditionDetector(thresholds)
    return _detector_instance
