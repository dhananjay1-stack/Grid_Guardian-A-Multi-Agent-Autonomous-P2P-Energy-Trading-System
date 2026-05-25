"""
Condition Detector - Analyzes operating conditions to inform policy selection.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional
from enum import Enum

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


class OperatingCondition(str, Enum):
    """Operating condition categories."""
    STABLE = "stable"
    NORMAL = "normal"
    UNCERTAIN = "uncertain"
    HIGH_VOLATILITY = "high_volatility"
    HIGH_RISK = "high_risk"
    DEGRADED = "degraded"
    STRESS_TEST = "stress_test"
    LOW_CONFIDENCE = "low_confidence"


@dataclass
class ConditionAssessment:
    """Result of condition assessment."""
    primary_condition: OperatingCondition
    conditions: List[OperatingCondition]
    risk_level: float  # 0.0 to 1.0
    confidence_level: float  # 0.0 to 1.0
    factors: Dict[str, float]
    recommended_model: str
    reason: str


class ConditionDetector:
    """
    Detects current operating conditions from telemetry and context.

    Uses multiple factors to determine the safest policy selection:
    - Battery SoC level
    - Forecast uncertainty
    - Sensor reliability
    - Anomaly score
    - Grid risk
    - Volatility
    """

    def __init__(self):
        self.thresholds = {
            "volatility": settings.VOLATILITY_THRESHOLD,
            "risk": settings.RISK_THRESHOLD,
            "sensor_health": settings.SENSOR_HEALTH_THRESHOLD,
            "anomaly": settings.ANOMALY_THRESHOLD,
            "soc_critical_low": 15.0,
            "soc_critical_high": 92.0,
            "soc_low": 25.0,
            "soc_high": 85.0,
        }

    def assess(
        self,
        soc: float,
        volatility: float = 0.1,
        sensor_health: float = 1.0,
        anomaly_score: float = 0.0,
        grid_risk: float = 0.1,
        forecast_uncertainty: float = 0.1,
        stress_test_mode: bool = False,
        previous_confidence: Optional[float] = None,
    ) -> ConditionAssessment:
        """
        Assess current operating conditions.

        Args:
            soc: State of charge (%)
            volatility: Market/demand volatility (0-1)
            sensor_health: Sensor reliability score (0-1)
            anomaly_score: Anomaly detection score (0-1)
            grid_risk: Grid instability risk (0-1)
            forecast_uncertainty: Forecast uncertainty (0-1)
            stress_test_mode: Whether system is in stress test
            previous_confidence: Previous decision confidence

        Returns:
            ConditionAssessment with recommended model and reasoning
        """
        conditions = []
        factors = {
            "soc": soc,
            "volatility": volatility,
            "sensor_health": sensor_health,
            "anomaly_score": anomaly_score,
            "grid_risk": grid_risk,
            "forecast_uncertainty": forecast_uncertainty,
        }

        # Calculate composite risk level
        risk_level = self._calculate_risk_level(factors)
        confidence_level = self._calculate_confidence_level(factors, previous_confidence)

        # Detect conditions
        if stress_test_mode:
            conditions.append(OperatingCondition.STRESS_TEST)

        if sensor_health < self.thresholds["sensor_health"]:
            conditions.append(OperatingCondition.DEGRADED)

        if anomaly_score > self.thresholds["anomaly"]:
            conditions.append(OperatingCondition.DEGRADED)

        if volatility > self.thresholds["volatility"]:
            conditions.append(OperatingCondition.HIGH_VOLATILITY)

        if grid_risk > self.thresholds["risk"]:
            conditions.append(OperatingCondition.HIGH_RISK)

        if forecast_uncertainty > 0.4:
            conditions.append(OperatingCondition.UNCERTAIN)

        if confidence_level < 0.5:
            conditions.append(OperatingCondition.LOW_CONFIDENCE)

        # SoC-based conditions
        if soc < self.thresholds["soc_critical_low"] or soc > self.thresholds["soc_critical_high"]:
            conditions.append(OperatingCondition.HIGH_RISK)
        elif soc < self.thresholds["soc_low"] or soc > self.thresholds["soc_high"]:
            conditions.append(OperatingCondition.UNCERTAIN)

        # Determine primary condition
        if not conditions:
            if risk_level < 0.2 and confidence_level > 0.7:
                conditions.append(OperatingCondition.STABLE)
            else:
                conditions.append(OperatingCondition.NORMAL)

        primary_condition = self._prioritize_conditions(conditions)

        # Determine recommended model
        recommended_model, reason = self._recommend_model(
            conditions, risk_level, confidence_level
        )

        assessment = ConditionAssessment(
            primary_condition=primary_condition,
            conditions=conditions,
            risk_level=risk_level,
            confidence_level=confidence_level,
            factors=factors,
            recommended_model=recommended_model,
            reason=reason,
        )

        logger.debug(
            f"Condition assessment: {primary_condition.value} -> {recommended_model} "
            f"(risk={risk_level:.2f}, confidence={confidence_level:.2f})"
        )

        return assessment

    def _calculate_risk_level(self, factors: Dict[str, float]) -> float:
        """Calculate composite risk level (0-1)."""
        weights = {
            "volatility": 0.2,
            "anomaly_score": 0.25,
            "grid_risk": 0.25,
            "sensor_health": 0.15,
            "forecast_uncertainty": 0.15,
        }

        risk = 0.0
        for key, weight in weights.items():
            value = factors.get(key, 0.0)
            if key == "sensor_health":
                # Invert sensor health (low health = high risk)
                value = 1.0 - value
            risk += value * weight

        # Add SoC risk
        soc = factors.get("soc", 50.0)
        if soc < 15 or soc > 92:
            risk += 0.3
        elif soc < 25 or soc > 85:
            risk += 0.1

        return min(1.0, risk)

    def _calculate_confidence_level(
        self, factors: Dict[str, float], previous_confidence: Optional[float]
    ) -> float:
        """Calculate confidence level (0-1)."""
        # Start with sensor health as base
        confidence = factors.get("sensor_health", 1.0)

        # Reduce confidence based on uncertainty factors
        confidence -= factors.get("forecast_uncertainty", 0.0) * 0.3
        confidence -= factors.get("anomaly_score", 0.0) * 0.4

        # Incorporate previous confidence with decay
        if previous_confidence is not None:
            confidence = 0.7 * confidence + 0.3 * previous_confidence

        return max(0.0, min(1.0, confidence))

    def _prioritize_conditions(
        self, conditions: List[OperatingCondition]
    ) -> OperatingCondition:
        """Return highest priority condition."""
        priority_order = [
            OperatingCondition.STRESS_TEST,
            OperatingCondition.DEGRADED,
            OperatingCondition.HIGH_RISK,
            OperatingCondition.HIGH_VOLATILITY,
            OperatingCondition.UNCERTAIN,
            OperatingCondition.LOW_CONFIDENCE,
            OperatingCondition.NORMAL,
            OperatingCondition.STABLE,
        ]

        for condition in priority_order:
            if condition in conditions:
                return condition

        return OperatingCondition.NORMAL

    def _recommend_model(
        self,
        conditions: List[OperatingCondition],
        risk_level: float,
        confidence_level: float,
    ) -> tuple[str, str]:
        """
        Recommend the best model based on conditions.

        Selection Logic:
        - BC: Safest baseline for degraded/stress test/low confidence
        - CQL: Conservative for uncertain/risky/volatile conditions
        - DT: Best for stable conditions with long-horizon planning
        """
        # BC for degraded states
        if OperatingCondition.STRESS_TEST in conditions:
            return "bc", "stress_test_mode_active"

        if OperatingCondition.DEGRADED in conditions:
            return "bc", "sensor_degraded_or_anomaly_detected"

        if OperatingCondition.LOW_CONFIDENCE in conditions:
            return "bc", "low_confidence_fallback"

        # CQL for risky/uncertain states
        if OperatingCondition.HIGH_RISK in conditions:
            return "cql", "high_risk_conservative_policy"

        if OperatingCondition.HIGH_VOLATILITY in conditions:
            return "cql", "high_volatility_conservative_policy"

        if OperatingCondition.UNCERTAIN in conditions:
            return "cql", "uncertain_conditions_conservative_policy"

        # DT for stable states
        if OperatingCondition.STABLE in conditions:
            return "dt", "stable_condition_long_horizon_planning"

        # Default to CQL for normal conditions
        if risk_level < 0.3 and confidence_level > 0.6:
            return "dt", "normal_condition_long_horizon_planning"

        return "cql", "default_conservative_policy"


# Global condition detector instance
condition_detector = ConditionDetector()
