"""Condition detector for dynamic policy selection."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, List, Optional
import numpy as np

from .config import settings


class OperatingCondition(str, Enum):
    """Operating condition categories."""
    STABLE = "stable"
    UNCERTAIN = "uncertain"
    RISKY = "risky"
    DEGRADED = "degraded"
    STRESS_TEST = "stress_test"


@dataclass
class ConditionAssessment:
    """Result of condition detection."""
    condition: OperatingCondition
    recommended_policy: str
    confidence: float
    risk_score: float
    factors: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    warnings: List[str] = field(default_factory=list)


class ConditionDetector:
    """
    Detects operating conditions from telemetry data and recommends
    the appropriate policy (BC, CQL, or DT).

    Policy selection logic:
    - DT: Stable conditions, long-horizon planning useful
    - CQL: Uncertain/risky conditions, conservative approach needed
    - BC: Degraded conditions, fallback to safe baseline
    """

    def __init__(self):
        self.thresholds = settings.thresholds
        self._history: List[ConditionAssessment] = []

    def assess(self, telemetry: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> ConditionAssessment:
        """
        Assess current operating conditions from telemetry.

        Parameters
        ----------
        telemetry : dict
            Raw telemetry data including SoC, load, solar, price, etc.
        context : dict, optional
            Additional context (stress test mode, recent history, etc.)

        Returns
        -------
        ConditionAssessment
            Assessment with recommended policy and reasoning.
        """
        context = context or {}
        factors: Dict[str, Any] = {}
        warnings: List[str] = []
        risk_score = 0.0

        # Extract key metrics with defaults
        soc = telemetry.get("soc_kwh", telemetry.get("soc", 2.0))
        soc_cap = telemetry.get("soc_capacity_kwh", 4.0)
        soc_frac = soc / max(soc_cap, 0.1)

        volatility = telemetry.get("volatility", context.get("volatility", 0.1))
        sensor_health = telemetry.get("sensor_health", context.get("sensor_health", 1.0))
        grid_risk = telemetry.get("grid_risk", context.get("grid_risk", 0.05))
        anomaly_score = telemetry.get("anomaly_score", context.get("anomaly_score", 0.0))
        stress_test = context.get("stress_test_mode", False)

        # Previous cycle confidence
        prev_confidence = context.get("previous_confidence", 1.0)

        factors["soc_fraction"] = soc_frac
        factors["volatility"] = volatility
        factors["sensor_health"] = sensor_health
        factors["grid_risk"] = grid_risk
        factors["anomaly_score"] = anomaly_score
        factors["stress_test_mode"] = stress_test

        # Check for stress test mode or degraded sensors
        if stress_test:
            return ConditionAssessment(
                condition=OperatingCondition.STRESS_TEST,
                recommended_policy="BC",
                confidence=0.95,
                risk_score=0.8,
                factors=factors,
                reason="stress_test_mode_active",
                warnings=["System in stress test mode, using safe baseline policy"]
            )

        if sensor_health < self.thresholds.sensor_health_low:
            risk_score += 0.3
            warnings.append(f"Sensor health degraded: {sensor_health:.2f}")
            factors["sensor_degraded"] = True

        if anomaly_score > self.thresholds.anomaly_score_high:
            risk_score += 0.3
            warnings.append(f"High anomaly score: {anomaly_score:.2f}")
            factors["anomaly_detected"] = True

        # Check SoC bounds
        if soc_frac < self.thresholds.soc_critical_low:
            risk_score += 0.25
            warnings.append(f"SoC critically low: {soc_frac:.1%}")
            factors["soc_critical_low"] = True
        elif soc_frac > self.thresholds.soc_critical_high:
            risk_score += 0.15
            warnings.append(f"SoC critically high: {soc_frac:.1%}")
            factors["soc_critical_high"] = True

        # Check volatility and grid risk
        if volatility > self.thresholds.volatility_high:
            risk_score += 0.2
            factors["high_volatility"] = True

        if grid_risk > self.thresholds.grid_risk_high:
            risk_score += 0.25
            factors["high_grid_risk"] = True

        # Low confidence from previous cycle
        if prev_confidence < self.thresholds.confidence_low:
            risk_score += 0.15
            factors["low_previous_confidence"] = True

        # Determine condition and policy
        condition, policy, reason = self._select_policy(risk_score, factors, warnings)

        assessment = ConditionAssessment(
            condition=condition,
            recommended_policy=policy,
            confidence=max(0.0, 1.0 - risk_score),
            risk_score=min(1.0, risk_score),
            factors=factors,
            reason=reason,
            warnings=warnings
        )

        self._history.append(assessment)
        if len(self._history) > 100:
            self._history = self._history[-100:]

        return assessment

    def _select_policy(self, risk_score: float, factors: Dict[str, Any],
                       warnings: List[str]) -> tuple:
        """Select policy based on risk score and factors."""

        # BC fallback conditions
        if (factors.get("sensor_degraded") or
            factors.get("anomaly_detected") or
            factors.get("stress_test_mode") or
            risk_score > 0.6):
            return (
                OperatingCondition.DEGRADED,
                "BC",
                "degraded_condition_safe_fallback"
            )

        # CQL for uncertain/risky conditions
        if (factors.get("high_volatility") or
            factors.get("high_grid_risk") or
            factors.get("soc_critical_low") or
            risk_score > 0.35):
            return (
                OperatingCondition.RISKY if risk_score > 0.4 else OperatingCondition.UNCERTAIN,
                "CQL",
                "uncertain_or_risky_condition_conservative_policy"
            )

        # DT for stable conditions
        return (
            OperatingCondition.STABLE,
            "DT",
            "stable_condition_long_horizon_planning"
        )

    def get_recent_conditions(self, n: int = 10) -> List[ConditionAssessment]:
        """Get recent condition assessments."""
        return self._history[-n:]

    def get_condition_stats(self) -> Dict[str, Any]:
        """Get statistics on recent conditions."""
        if not self._history:
            return {"total": 0}

        conditions = [a.condition.value for a in self._history]
        policies = [a.recommended_policy for a in self._history]

        return {
            "total": len(self._history),
            "condition_counts": {c: conditions.count(c) for c in set(conditions)},
            "policy_counts": {p: policies.count(p) for p in set(policies)},
            "avg_risk_score": np.mean([a.risk_score for a in self._history]),
            "avg_confidence": np.mean([a.confidence for a in self._history]),
        }


# Global instance
condition_detector = ConditionDetector()
