"""Safety shield for action validation and constraint enforcement."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
import numpy as np

from .config import settings, DISCRETE_ACTIONS
from .logger import get_logger


logger = get_logger("safety")


class SafetyStatus(str, Enum):
    """Safety check result status."""
    APPROVED = "APPROVED"
    MODIFIED = "MODIFIED"
    BLOCKED = "BLOCKED"
    FALLBACK = "FALLBACK"


@dataclass
class SafetyCheckResult:
    """Result of safety check."""
    status: SafetyStatus
    original_action_kw: float
    safe_action_kw: float
    original_action_idx: int
    safe_action_idx: int
    reasons: List[str] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)


class SafetyShield:
    """
    Safety layer for validating and constraining actions.

    Enforces:
    - SoC limits (min/max)
    - Power constraints (charge/discharge rates)
    - Input sanity checks
    - Confidence thresholds
    - Price sanity
    """

    def __init__(self):
        self.cfg = settings.safety
        self._incident_log: List[Dict[str, Any]] = []
        self._incident_count = 0

    def check(self,
              action_idx: int,
              action_kw: float,
              soc: float,
              soc_capacity: float,
              confidence: float,
              price: float = 5.0,
              telemetry: Optional[Dict[str, Any]] = None) -> SafetyCheckResult:
        """
        Apply safety checks to proposed action.

        Parameters
        ----------
        action_idx : int
            Discrete action index
        action_kw : float
            Action power in kW (positive=charge, negative=discharge)
        soc : float
            Current state of charge in kWh
        soc_capacity : float
            Battery capacity in kWh
        confidence : float
            Model confidence score
        price : float
            Current price signal
        telemetry : dict, optional
            Full telemetry for additional checks

        Returns
        -------
        SafetyCheckResult
            Result with safe action and any violations
        """
        telemetry = telemetry or {}
        violations: List[str] = []
        reasons: List[str] = []
        safe_kw = action_kw
        safe_idx = action_idx

        # Check confidence threshold
        if confidence < self.cfg.min_confidence:
            violations.append(f"Low confidence: {confidence:.2f} < {self.cfg.min_confidence}")
            safe_kw = 0.0  # Default to idle
            safe_idx = 2

        # Check for invalid/missing telemetry
        sensor_health = telemetry.get("sensor_health", 1.0)
        if sensor_health < 0.5:
            violations.append(f"Degraded sensor health: {sensor_health:.2f}")

        # Power limits
        if safe_kw > self.cfg.max_charge_kw:
            safe_kw = self.cfg.max_charge_kw
            reasons.append(f"Charge capped to {self.cfg.max_charge_kw} kW")

        if safe_kw < -self.cfg.max_discharge_kw:
            safe_kw = -self.cfg.max_discharge_kw
            reasons.append(f"Discharge capped to {self.cfg.max_discharge_kw} kW")

        # SoC bounds check (5-min step)
        dt_h = 5.0 / 60.0  # hours
        new_soc = soc + safe_kw * dt_h
        soc_lo = self.cfg.soc_min_frac * soc_capacity
        soc_hi = self.cfg.soc_max_frac * soc_capacity

        if new_soc < soc_lo:
            original_kw = safe_kw
            safe_kw = (soc_lo - soc) / dt_h
            violations.append(f"SoC would drop to {new_soc:.2f} kWh (below {soc_lo:.2f})")
            reasons.append(f"Discharge limited to protect SoC")

        elif new_soc > soc_hi:
            original_kw = safe_kw
            safe_kw = (soc_hi - soc) / dt_h
            violations.append(f"SoC would exceed {new_soc:.2f} kWh (above {soc_hi:.2f})")
            reasons.append(f"Charge limited to protect battery")

        # Price sanity check (don't sell at very low prices)
        if action_kw < 0 and DISCRETE_ACTIONS.get(action_idx, {}).get("trade") == "SELL":
            if price < 0.05:  # Very low price
                violations.append(f"Sell action at low price: {price}")
                # Don't block, just warn

        # Map back to discrete action if kW changed
        if safe_kw != action_kw:
            safe_idx = self._find_nearest_action(safe_kw)

        # Determine status
        if violations and safe_idx == 2:  # Blocked to idle
            status = SafetyStatus.BLOCKED
        elif violations or safe_kw != action_kw:
            status = SafetyStatus.MODIFIED
        else:
            status = SafetyStatus.APPROVED

        result = SafetyCheckResult(
            status=status,
            original_action_kw=action_kw,
            safe_action_kw=safe_kw,
            original_action_idx=action_idx,
            safe_action_idx=safe_idx,
            reasons=reasons,
            violations=violations
        )

        # Log incidents
        if status != SafetyStatus.APPROVED:
            self._log_incident(result, soc, confidence)

        return result

    def _find_nearest_action(self, target_kw: float) -> int:
        """Find the discrete action nearest to target kW."""
        best_idx = 2  # Default to idle
        best_dist = float("inf")

        for idx, info in DISCRETE_ACTIONS.items():
            dist = abs(info["kw"] - target_kw)
            if dist < best_dist:
                best_idx = idx
                best_dist = dist

        return best_idx

    def _log_incident(self, result: SafetyCheckResult, soc: float, confidence: float):
        """Log safety incident for monitoring."""
        self._incident_count += 1

        if len(self._incident_log) < 10000:
            self._incident_log.append({
                "status": result.status.value,
                "original_kw": result.original_action_kw,
                "safe_kw": result.safe_action_kw,
                "soc": soc,
                "confidence": confidence,
                "violations": result.violations,
                "reasons": result.reasons,
            })

        if self._incident_count % 1000 == 0:
            logger.info(f"Safety shield: {self._incident_count} total incidents")

    def get_fallback_action(self, soc: float, soc_capacity: float,
                            price: float = 5.0) -> Tuple[int, float]:
        """
        Get safe fallback action based on current state.

        Simple rule-based policy:
        - Low SoC + low price -> charge
        - High SoC + high price -> gentle sell
        - Otherwise -> idle
        """
        soc_frac = soc / max(soc_capacity, 0.1)

        if soc_frac < 0.3 and price < 5.0:
            return 0, min(1.0, self.cfg.max_charge_kw)  # Gentle charge
        elif soc_frac > 0.8 and price > 6.0:
            return 3, max(-1.0, -self.cfg.max_discharge_kw)  # Gentle discharge
        else:
            return 2, 0.0  # Idle

    def get_incident_summary(self) -> Dict[str, Any]:
        """Get summary of safety incidents."""
        if not self._incident_log:
            return {"total_incidents": 0}

        statuses = [i["status"] for i in self._incident_log]
        return {
            "total_incidents": self._incident_count,
            "logged_incidents": len(self._incident_log),
            "status_counts": {s: statuses.count(s) for s in set(statuses)},
        }

    def reset_log(self):
        """Clear incident log."""
        self._incident_log.clear()
        self._incident_count = 0


# Global instance
safety_shield = SafetyShield()
