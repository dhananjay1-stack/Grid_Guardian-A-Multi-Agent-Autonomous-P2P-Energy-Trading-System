"""Policy router for dynamic model selection."""
from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass
import time

from .config import settings
from .logger import get_logger
from .condition_detector import condition_detector, ConditionAssessment


logger = get_logger("policy_router")


@dataclass
class PolicyDecision:
    """Result of policy routing."""
    selected_policy: str
    condition: str
    reason: str
    fallback_used: bool = False
    fallback_reason: str = ""


class PolicyRouter:
    """
    Routes inference requests to the appropriate policy model
    based on condition assessment.

    Maintains state about available models and handles fallback logic.
    """

    def __init__(self):
        self._available_policies: Dict[str, bool] = {
            "BC": False,
            "CQL": False,
            "DT": False,
        }
        self._preferred_order = ["DT", "CQL", "BC"]
        self._fallback_policy = settings.fallback_policy
        self._selection_history: list = []

    def set_policy_available(self, policy: str, available: bool):
        """Mark a policy as available or unavailable."""
        if policy in self._available_policies:
            self._available_policies[policy] = available
            logger.info(f"Policy {policy} availability: {available}")

    def get_available_policies(self) -> Dict[str, bool]:
        """Get status of all policies."""
        return self._available_policies.copy()

    def route(self, telemetry: Dict[str, Any],
              context: Optional[Dict[str, Any]] = None,
              force_policy: Optional[str] = None) -> Tuple[str, PolicyDecision]:
        """
        Route to the appropriate policy based on conditions.

        Parameters
        ----------
        telemetry : dict
            Current telemetry data
        context : dict, optional
            Additional context
        force_policy : str, optional
            Force a specific policy (for testing)

        Returns
        -------
        tuple
            (selected_policy_name, PolicyDecision)
        """
        context = context or {}

        # Forced policy selection (for testing/debugging)
        if force_policy and force_policy in self._available_policies:
            if self._available_policies[force_policy]:
                return force_policy, PolicyDecision(
                    selected_policy=force_policy,
                    condition="forced",
                    reason="policy_forced_by_request"
                )

        # Assess conditions
        assessment = condition_detector.assess(telemetry, context)
        recommended = assessment.recommended_policy

        # Check if recommended policy is available
        if self._available_policies.get(recommended, False):
            decision = PolicyDecision(
                selected_policy=recommended,
                condition=assessment.condition.value,
                reason=assessment.reason
            )
        else:
            # Fallback logic
            fallback = self._find_fallback(recommended)
            decision = PolicyDecision(
                selected_policy=fallback,
                condition=assessment.condition.value,
                reason=assessment.reason,
                fallback_used=True,
                fallback_reason=f"{recommended}_unavailable_using_{fallback}"
            )
            logger.policy_switch(recommended, fallback, f"original unavailable")

        # Record selection
        self._record_selection(decision, assessment)

        return decision.selected_policy, decision

    def _find_fallback(self, failed_policy: str) -> str:
        """Find best available fallback policy."""
        # Priority: try other policies in preferred order
        for policy in self._preferred_order:
            if policy != failed_policy and self._available_policies.get(policy, False):
                return policy

        # If nothing available, return BC as ultimate fallback
        # (even if not loaded, decision engine will handle it)
        return self._fallback_policy

    def _record_selection(self, decision: PolicyDecision, assessment: ConditionAssessment):
        """Record policy selection for metrics."""
        record = {
            "timestamp": time.time(),
            "policy": decision.selected_policy,
            "condition": decision.condition,
            "reason": decision.reason,
            "fallback": decision.fallback_used,
            "risk_score": assessment.risk_score,
            "confidence": assessment.confidence,
        }
        self._selection_history.append(record)

        # Keep bounded history
        if len(self._selection_history) > settings.metrics_window_size:
            self._selection_history = self._selection_history[-settings.metrics_window_size:]

    def get_selection_stats(self) -> Dict[str, Any]:
        """Get policy selection statistics."""
        if not self._selection_history:
            return {"total": 0}

        policies = [r["policy"] for r in self._selection_history]
        conditions = [r["condition"] for r in self._selection_history]
        fallbacks = sum(1 for r in self._selection_history if r["fallback"])

        return {
            "total_selections": len(self._selection_history),
            "policy_counts": {p: policies.count(p) for p in set(policies)},
            "condition_counts": {c: conditions.count(c) for c in set(conditions)},
            "fallback_count": fallbacks,
            "fallback_rate": fallbacks / len(self._selection_history),
        }


# Global instance
policy_router = PolicyRouter()
