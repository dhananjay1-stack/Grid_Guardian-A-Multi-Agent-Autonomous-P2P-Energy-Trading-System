"""
Safety Shield - Enforces safety constraints on all AI decisions.
"""
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from enum import Enum

from app.core.config import settings
from app.core.logger import get_logger, metrics_tracker

logger = get_logger(__name__)


class SafetyStatus(str, Enum):
    """Safety check result status."""
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    BLOCKED = "BLOCKED"
    FALLBACK = "FALLBACK"


@dataclass
class SafetyResult:
    """Result of safety check."""
    status: SafetyStatus
    original_action: str
    final_action: str
    original_energy: float
    final_energy: float
    violations: list
    reason: str
    modified: bool


class SafetyShield:
    """
    Safety shield that validates and potentially modifies AI decisions.

    Checks:
    - SoC limits
    - Power constraints
    - Input validation
    - Price sanity
    - Confidence threshold
    - Anomaly detection
    """

    def __init__(self):
        self.constraints = {
            "soc_min": settings.SOC_MIN,
            "soc_max": settings.SOC_MAX,
            "power_min": settings.POWER_MIN,
            "power_max": settings.POWER_MAX,
            "confidence_min": settings.CONFIDENCE_THRESHOLD,
            "price_min": settings.PRICE_MIN,
            "price_max": settings.PRICE_MAX,
        }

    def validate_and_constrain(
        self,
        action: str,
        energy: float,
        price: float,
        confidence: float,
        soc: float,
        model_name: str,
        anomaly_score: float = 0.0,
        sensor_health: float = 1.0,
    ) -> SafetyResult:
        """
        Validate decision and apply safety constraints.

        Args:
            action: Proposed action (BUY, SELL, HOLD, etc.)
            energy: Proposed energy amount (kW)
            price: Proposed price
            confidence: Model confidence (0-1)
            soc: Current state of charge (%)
            model_name: Name of selected model
            anomaly_score: Anomaly detection score (0-1)
            sensor_health: Sensor reliability (0-1)

        Returns:
            SafetyResult with final approved/modified action
        """
        violations = []
        original_action = action
        original_energy = energy
        final_action = action
        final_energy = energy
        modified = False

        # Check 1: Confidence threshold
        if confidence < self.constraints["confidence_min"]:
            violations.append(
                f"confidence_below_threshold ({confidence:.2f} < {self.constraints['confidence_min']:.2f})"
            )
            if action in ["BUY", "SELL"]:
                final_action = "HOLD"
                final_energy = 0.0
                modified = True

        # Check 2: Anomaly detection
        if anomaly_score > settings.ANOMALY_THRESHOLD:
            violations.append(f"anomaly_detected ({anomaly_score:.2f})")
            final_action = "HOLD"
            final_energy = 0.0
            modified = True

        # Check 3: Sensor health
        if sensor_health < settings.SENSOR_HEALTH_THRESHOLD:
            violations.append(f"sensor_degraded ({sensor_health:.2f})")
            if action in ["BUY", "SELL"]:
                final_action = "HOLD"
                final_energy = 0.0
                modified = True

        # Check 4: SoC limits for charge/discharge
        if action in ["CHARGE", "BUY"] or energy > 0:
            if soc >= self.constraints["soc_max"]:
                violations.append(f"soc_at_max ({soc:.1f}%)")
                if action in ["CHARGE", "BUY"]:
                    final_action = "HOLD"
                    final_energy = 0.0
                    modified = True
                else:
                    final_energy = max(0, final_energy)

        if action in ["DISCHARGE", "SELL"] or energy < 0:
            if soc <= self.constraints["soc_min"]:
                violations.append(f"soc_at_min ({soc:.1f}%)")
                if action in ["DISCHARGE", "SELL"]:
                    final_action = "HOLD"
                    final_energy = 0.0
                    modified = True
                else:
                    final_energy = min(0, final_energy)

        # Check 5: Power constraints
        if energy > self.constraints["power_max"]:
            violations.append(f"power_exceeds_max ({energy:.2f} > {self.constraints['power_max']:.2f})")
            final_energy = self.constraints["power_max"]
            modified = True
        elif energy < self.constraints["power_min"]:
            violations.append(f"power_below_min ({energy:.2f} < {self.constraints['power_min']:.2f})")
            final_energy = self.constraints["power_min"]
            modified = True

        # Check 6: Price sanity
        if price < self.constraints["price_min"] or price > self.constraints["price_max"]:
            violations.append(f"price_out_of_range ({price:.2f})")
            # Don't block but flag

        # Check 7: Critical SoC emergency
        if soc < 5 or soc > 98:
            violations.append(f"critical_soc ({soc:.1f}%)")
            final_action = "HOLD"
            final_energy = 0.0
            modified = True

        # Determine status
        if not violations:
            status = SafetyStatus.APPROVED
            reason = "all_checks_passed"
        elif final_action == original_action and final_energy == original_energy:
            status = SafetyStatus.APPROVED
            reason = "minor_violations_action_approved"
        elif final_action != "HOLD":
            status = SafetyStatus.MODIFIED
            reason = f"action_modified_due_to_{len(violations)}_violations"
        else:
            status = SafetyStatus.BLOCKED
            reason = f"action_blocked_due_to_{len(violations)}_violations"
            metrics_tracker.record_safety_block()

        result = SafetyResult(
            status=status,
            original_action=original_action,
            final_action=final_action,
            original_energy=original_energy,
            final_energy=final_energy,
            violations=violations,
            reason=reason,
            modified=modified,
        )

        if violations:
            logger.warning(
                f"Safety shield: {status.value} - {len(violations)} violations "
                f"({original_action} -> {final_action}, model={model_name})"
            )
            logger.debug(f"Safety violations: {violations}")

        return result

    def apply_soc_constraints(
        self, energy: float, soc: float, soc_capacity: float, dt_hours: float = 1/12
    ) -> Tuple[float, bool]:
        """
        Apply SoC-aware constraints to energy decision.

        Args:
            energy: Proposed energy change (kW)
            soc: Current SoC (kWh)
            soc_capacity: Battery capacity (kWh)
            dt_hours: Time step in hours (default 5 minutes)

        Returns:
            (constrained_energy, was_modified)
        """
        soc_percent = (soc / soc_capacity) * 100 if soc_capacity > 0 else 50.0
        energy_delta = energy * dt_hours  # kWh change

        projected_soc = soc + energy_delta
        projected_soc_percent = (projected_soc / soc_capacity) * 100

        modified = False
        constrained_energy = energy

        # Prevent over-charging
        if projected_soc_percent > self.constraints["soc_max"]:
            max_charge = ((self.constraints["soc_max"] / 100) * soc_capacity - soc) / dt_hours
            constrained_energy = min(energy, max(0, max_charge))
            modified = True

        # Prevent over-discharging
        if projected_soc_percent < self.constraints["soc_min"]:
            max_discharge = ((self.constraints["soc_min"] / 100) * soc_capacity - soc) / dt_hours
            constrained_energy = max(energy, min(0, max_discharge))
            modified = True

        return constrained_energy, modified


# Global safety shield instance
safety_shield = SafetyShield()
