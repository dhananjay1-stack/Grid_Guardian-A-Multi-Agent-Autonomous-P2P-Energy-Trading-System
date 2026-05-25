"""
Grid-Guardian Edge - Safety Guard
Safety layer between AI decisions and relay actuation
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple

from config import (
    MAX_VOLTAGE,
    MIN_VOLTAGE,
    MAX_CURRENT,
    MAX_POWER,
    SAFE_MODE_ENABLED,
)

logger = logging.getLogger(__name__)


class SafetyGuard:
    """
    Safety guard that validates AI decisions before relay actuation.

    This module acts as a firewall between the AI decision engine
    and the physical relay hardware. It blocks unsafe actions and
    provides fallback behavior.

    Safety Checks:
    - Voltage bounds
    - Current limits
    - Power limits
    - SoC protection (min/max)
    - Confidence threshold
    - Rate limiting (prevent oscillation)
    - Sensor validity
    - Model failure detection
    """

    # Minimum confidence to act on AI decision
    MIN_CONFIDENCE = 0.5

    # Action rate limiting (minimum time between state changes)
    MIN_ACTION_INTERVAL_MS = 5000

    # SoC protection bounds
    SOC_MIN_FRACTION = 0.10
    SOC_MAX_FRACTION = 0.95

    # Maximum power for battery charge/discharge
    MAX_CHARGE_KW = 3.0
    MAX_DISCHARGE_KW = 3.0

    def __init__(self):
        """Initialize safety guard"""
        self.enabled = SAFE_MODE_ENABLED
        self.last_action_time = 0
        self.last_action = None
        self.blocked_count = 0
        self.passed_count = 0

        # Track safety events
        self.safety_events = []
        self.max_events = 100

        # Statistics
        self.stats = {
            "checks_total": 0,
            "checks_passed": 0,
            "checks_blocked": 0,
            "violations_by_type": {
                "voltage": 0,
                "current": 0,
                "power": 0,
                "soc": 0,
                "confidence": 0,
                "rate_limit": 0,
                "invalid_sensor": 0,
                "model_failure": 0,
            },
        }

    def check_action(
        self,
        action: Dict[str, Any],
        sensor_data: Dict[str, Any],
        condition: str = "normal"
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate an AI action before execution.

        Args:
            action: AI decision dictionary with 'decision', 'confidence', 'action_kw'
            sensor_data: Current sensor readings
            condition: Current operating condition

        Returns:
            Tuple of (is_safe, modified_action)
            - is_safe: True if action is safe to execute
            - modified_action: Possibly modified action with safety adjustments
        """
        self.stats["checks_total"] += 1

        if not self.enabled:
            logger.debug("Safety guard disabled - passing through")
            return True, action

        violations = []
        modified_action = action.copy()

        # 1. Check sensor validity
        if not sensor_data.get("valid", True):
            violations.append(("invalid_sensor", "Sensor data invalid"))
            self.stats["violations_by_type"]["invalid_sensor"] += 1

        # 2. Check voltage bounds
        voltage = sensor_data.get("voltage", 230)
        if voltage < MIN_VOLTAGE or voltage > MAX_VOLTAGE:
            violations.append(("voltage", f"Voltage {voltage}V out of bounds [{MIN_VOLTAGE}-{MAX_VOLTAGE}V]"))
            self.stats["violations_by_type"]["voltage"] += 1

        # 3. Check current limits
        current = sensor_data.get("current", 0)
        if current > MAX_CURRENT:
            violations.append(("current", f"Current {current}A exceeds {MAX_CURRENT}A"))
            self.stats["violations_by_type"]["current"] += 1

        # 4. Check power limits
        power = sensor_data.get("power", 0)
        if power > MAX_POWER:
            violations.append(("power", f"Power {power}W exceeds {MAX_POWER}W"))
            self.stats["violations_by_type"]["power"] += 1

        # 5. Check confidence threshold
        confidence = action.get("confidence", 0.5)
        if confidence < self.MIN_CONFIDENCE:
            violations.append(("confidence", f"Confidence {confidence:.2f} below {self.MIN_CONFIDENCE}"))
            self.stats["violations_by_type"]["confidence"] += 1

        # 6. Check model failure
        if action.get("source") == "fallback" and condition != "fault":
            violations.append(("model_failure", "AI model in fallback mode"))
            self.stats["violations_by_type"]["model_failure"] += 1

        # 7. SoC protection
        soc = sensor_data.get("soc_kwh", 2.0)
        soc_capacity = sensor_data.get("soc_capacity_kwh", 4.0)
        if soc_capacity > 0:
            soc_fraction = soc / soc_capacity
            action_kw = action.get("action_kw", 0)

            # Prevent over-discharge
            if soc_fraction <= self.SOC_MIN_FRACTION and action_kw < 0:
                violations.append(("soc", f"SoC too low ({soc_fraction:.1%}) for discharge"))
                self.stats["violations_by_type"]["soc"] += 1
                modified_action["action_kw"] = 0
                modified_action["action_name"] = "soc_protected_idle"

            # Prevent over-charge
            elif soc_fraction >= self.SOC_MAX_FRACTION and action_kw > 0:
                violations.append(("soc", f"SoC too high ({soc_fraction:.1%}) for charge"))
                self.stats["violations_by_type"]["soc"] += 1
                modified_action["action_kw"] = 0
                modified_action["action_name"] = "soc_protected_idle"

        # 8. Rate limiting
        time_since_last = (time.time() * 1000) - self.last_action_time
        decision = action.get("decision", "HOLD")
        if decision != "HOLD" and time_since_last < self.MIN_ACTION_INTERVAL_MS:
            violations.append(("rate_limit", f"Action too soon ({time_since_last:.0f}ms < {self.MIN_ACTION_INTERVAL_MS}ms)"))
            self.stats["violations_by_type"]["rate_limit"] += 1

        # 9. Clip action_kw to safe bounds
        action_kw = modified_action.get("action_kw", 0)
        clipped_kw = self._clip_action_kw(action_kw, soc, soc_capacity)
        if clipped_kw != action_kw:
            modified_action["action_kw"] = clipped_kw
            modified_action["action_clipped"] = True

        # Determine if action is safe
        critical_violations = [v for v in violations if v[0] in ("voltage", "current", "invalid_sensor")]
        is_safe = len(critical_violations) == 0

        # Log violations
        if violations:
            for vtype, vmsg in violations:
                logger.warning(f"Safety violation ({vtype}): {vmsg}")
            self._record_event(violations, action, sensor_data)

        # Update statistics
        if is_safe:
            self.stats["checks_passed"] += 1
            self.passed_count += 1
            self.last_action_time = time.time() * 1000
            self.last_action = modified_action
        else:
            self.stats["checks_blocked"] += 1
            self.blocked_count += 1
            # Return safe fallback action
            modified_action = self._get_safe_fallback(violations)

        return is_safe, modified_action

    def _clip_action_kw(self, action_kw: float, soc: float, soc_capacity: float) -> float:
        """Clip action_kw to safe bounds based on SoC"""
        # Basic power clipping
        clipped = max(-self.MAX_DISCHARGE_KW, min(self.MAX_CHARGE_KW, action_kw))

        # SoC-based adjustment
        if soc_capacity > 0:
            dt = 5.0 / 60.0  # 5-minute time step in hours
            projected_soc = soc + clipped * dt

            # Enforce SoC bounds
            soc_min = self.SOC_MIN_FRACTION * soc_capacity
            soc_max = self.SOC_MAX_FRACTION * soc_capacity

            if projected_soc < soc_min:
                # Limit discharge to maintain minimum SoC
                clipped = max(clipped, (soc_min - soc) / dt)
            elif projected_soc > soc_max:
                # Limit charge to maintain maximum SoC
                clipped = min(clipped, (soc_max - soc) / dt)

        return round(clipped, 3)

    def _get_safe_fallback(self, violations: list) -> Dict[str, Any]:
        """Generate safe fallback action when violations detected"""
        return {
            "decision": "HOLD",
            "confidence": 1.0,
            "action_kw": 0.0,
            "action_name": "safety_fallback",
            "action_index": 2,  # idle
            "source": "safety_guard",
            "violations": [v[0] for v in violations],
            "timestamp": time.time(),
        }

    def _record_event(self, violations: list, action: Dict, sensor_data: Dict):
        """Record safety event for logging/analysis"""
        event = {
            "timestamp": time.time(),
            "violations": violations,
            "action": {k: v for k, v in action.items() if k != "logits"},
            "sensor": {
                "voltage": sensor_data.get("voltage"),
                "current": sensor_data.get("current"),
                "power": sensor_data.get("power"),
                "valid": sensor_data.get("valid"),
            },
        }
        self.safety_events.append(event)
        if len(self.safety_events) > self.max_events:
            self.safety_events.pop(0)

    def force_safe_mode(self, reason: str = "manual") -> Dict[str, Any]:
        """
        Force return of safe/idle action.

        Args:
            reason: Reason for forcing safe mode

        Returns:
            Safe action dictionary
        """
        logger.warning(f"Safety guard forcing safe mode: {reason}")
        self._record_event([("forced", reason)], {}, {})
        return {
            "decision": "OFF",
            "confidence": 1.0,
            "action_kw": 0.0,
            "action_name": "forced_safe",
            "action_index": 2,
            "source": "safety_guard",
            "reason": reason,
            "timestamp": time.time(),
        }

    def is_enabled(self) -> bool:
        """Check if safety guard is enabled"""
        return self.enabled

    def enable(self):
        """Enable safety guard"""
        self.enabled = True
        logger.info("Safety guard enabled")

    def disable(self):
        """Disable safety guard (use with caution)"""
        self.enabled = False
        logger.warning("Safety guard disabled - proceed with caution")

    def get_stats(self) -> Dict[str, Any]:
        """Get safety guard statistics"""
        return {
            **self.stats,
            "enabled": self.enabled,
            "passed_count": self.passed_count,
            "blocked_count": self.blocked_count,
            "block_rate": self.blocked_count / max(1, self.stats["checks_total"]),
            "recent_events_count": len(self.safety_events),
        }

    def get_recent_events(self, count: int = 10) -> list:
        """Get recent safety events"""
        return self.safety_events[-count:]

    def reset_stats(self):
        """Reset statistics"""
        self.stats = {
            "checks_total": 0,
            "checks_passed": 0,
            "checks_blocked": 0,
            "violations_by_type": {k: 0 for k in self.stats["violations_by_type"]},
        }
        self.passed_count = 0
        self.blocked_count = 0
        self.safety_events.clear()


# Convenience function for quick safety check
def quick_safety_check(
    action: Dict[str, Any],
    voltage: float,
    current: float,
    soc_fraction: float
) -> Tuple[bool, str]:
    """
    Quick safety check without full SafetyGuard instance.

    Returns:
        Tuple of (is_safe, reason)
    """
    if voltage < MIN_VOLTAGE or voltage > MAX_VOLTAGE:
        return False, f"Voltage {voltage}V out of bounds"

    if current > MAX_CURRENT:
        return False, f"Current {current}A too high"

    if action.get("confidence", 0) < 0.5:
        return False, "Low confidence"

    action_kw = action.get("action_kw", 0)
    if soc_fraction <= 0.10 and action_kw < 0:
        return False, "SoC too low for discharge"

    if soc_fraction >= 0.95 and action_kw > 0:
        return False, "SoC too high for charge"

    return True, "OK"
