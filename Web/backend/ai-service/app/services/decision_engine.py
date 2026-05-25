"""Decision engine service - main inference orchestrator."""
from __future__ import annotations

import time
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np

from ..core.config import settings, DISCRETE_ACTIONS
from ..core.logger import get_logger
from ..core.preprocessing import preprocessor, PreprocessingResult
from ..core.safety import safety_shield, SafetyCheckResult, SafetyStatus
from ..core.router import policy_router, PolicyDecision
from ..models.registry import model_registry


logger = get_logger("decision_engine")


@dataclass
class PredictionResult:
    """Result from prediction endpoint."""
    node_id: str
    forecast_load_kw: float
    forecast_solar_kw: float
    net_power_kw: float
    soc_kwh: float
    soc_forecast_1h: float
    price_signal: float
    model_version: str
    timestamp: float


@dataclass
class DecisionResult:
    """Result from decision endpoint."""
    node_id: str
    action: str  # BUY, SELL, HOLD, CHARGE, DISCHARGE
    energy: float
    price: float
    confidence: float
    selected_model: str
    safety_status: str
    condition_reason: str
    fallback_reason: str
    reason: str
    action_index: int
    action_name: str
    action_kw: float
    trade_action: Optional[str]
    logits: list
    preprocessing_warnings: list
    latency_ms: float


class DecisionEngine:
    """
    Main decision engine that orchestrates:
    1. Telemetry preprocessing
    2. Condition detection and policy routing
    3. Model inference
    4. Safety shield
    5. Result formatting
    """

    def __init__(self):
        self._initialized = False
        self._inference_count = 0
        self._total_latency_ms = 0.0
        self._error_count = 0

    def initialize(self) -> bool:
        """Initialize the decision engine and load models."""
        logger.info("Initializing Decision Engine...")

        # Initialize model registry
        success = model_registry.initialize()

        if success:
            # Load normalization params into preprocessor
            cql_policy = model_registry.get_policy("CQL")
            if cql_policy and cql_policy.norm_means is not None:
                preprocessor.norm_means = cql_policy.norm_means
                preprocessor.norm_stds = cql_policy.norm_stds

            self._initialized = True
            logger.info("Decision Engine initialized successfully")
        else:
            logger.error("Decision Engine initialization failed")

        return success

    def predict(self, node_id: str, telemetry: Dict[str, Any],
                context: Optional[Dict[str, Any]] = None) -> PredictionResult:
        """
        Generate predictions/forecasts from telemetry.

        This endpoint returns forecast values without making trading decisions.
        """
        context = context or {}

        # Extract current values with preprocessing
        prep_result = preprocessor.preprocess(telemetry, context, normalize=False)

        load_kw = prep_result.obs_dict.get("load_kw", 0.8)
        solar_kw = prep_result.obs_dict.get("pv_gen_kw", 0.5)
        net_kw = solar_kw - load_kw
        soc = prep_result.obs_dict.get("soc_kwh", 2.0)
        soc_cap = prep_result.obs_dict.get("soc_capacity_kwh", 4.0)
        price = prep_result.obs_dict.get("price_signal", 0.15)

        # Simple SoC forecast (assuming current net power continues)
        dt_h = 1.0  # 1 hour forecast
        soc_forecast = np.clip(soc + net_kw * dt_h, 0, soc_cap)

        return PredictionResult(
            node_id=node_id,
            forecast_load_kw=load_kw,
            forecast_solar_kw=solar_kw,
            net_power_kw=net_kw,
            soc_kwh=soc,
            soc_forecast_1h=soc_forecast,
            price_signal=price,
            model_version=self._get_model_version(),
            timestamp=time.time()
        )

    def decide(self, node_id: str, telemetry: Dict[str, Any],
               context: Optional[Dict[str, Any]] = None,
               apply_safety: bool = True,
               force_policy: Optional[str] = None) -> DecisionResult:
        """
        Generate trading/control decision from telemetry.

        Full pipeline:
        1. Preprocess telemetry
        2. Detect conditions and route to policy
        3. Run inference
        4. Apply safety shield
        5. Format result
        """
        start_time = time.time()
        context = context or {}

        try:
            # Step 1: Preprocess
            prep_result = preprocessor.preprocess(telemetry, context, normalize=True)

            # Step 2: Route to policy
            policy_name, policy_decision = policy_router.route(
                telemetry, context, force_policy
            )

            # Step 3: Run inference
            policy = model_registry.get_policy(policy_name)
            if policy is None:
                # Fallback to rule-based if no policy available
                return self._fallback_decision(node_id, prep_result, start_time)

            logits = policy.model.predict(prep_result.observation)
            action_idx = int(np.argmax(logits))
            action_info = DISCRETE_ACTIONS.get(action_idx, DISCRETE_ACTIONS[2])

            # Calculate confidence
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / np.sum(exp_logits)
            confidence = float(probs[action_idx])

            # Step 4: Apply safety shield
            soc = prep_result.obs_dict.get("soc_kwh", 2.0)
            soc_cap = prep_result.obs_dict.get("soc_capacity_kwh", 4.0)
            price = prep_result.obs_dict.get("price_signal", 5.0)

            if apply_safety:
                safety_result = safety_shield.check(
                    action_idx=action_idx,
                    action_kw=action_info["kw"],
                    soc=soc,
                    soc_capacity=soc_cap,
                    confidence=confidence,
                    price=price,
                    telemetry=telemetry
                )
                final_action_kw = safety_result.safe_action_kw
                final_action_idx = safety_result.safe_action_idx
                safety_status = safety_result.status.value
            else:
                final_action_kw = action_info["kw"]
                final_action_idx = action_idx
                safety_status = SafetyStatus.APPROVED.value
                safety_result = None

            final_action_info = DISCRETE_ACTIONS.get(final_action_idx, DISCRETE_ACTIONS[2])

            # Calculate energy amount for trading
            energy_amount = self._calculate_energy_amount(
                final_action_kw, prep_result.obs_dict
            )

            # Build result
            latency_ms = (time.time() - start_time) * 1000
            self._record_inference(latency_ms)

            # Log inference
            logger.inference_log(
                node_id=node_id,
                policy=policy_name,
                decision=final_action_info["decision"],
                confidence=confidence,
                latency_ms=latency_ms,
                safety_status=safety_status
            )

            return DecisionResult(
                node_id=node_id,
                action=final_action_info["decision"],
                energy=energy_amount,
                price=price,
                confidence=confidence,
                selected_model=policy_name,
                safety_status=safety_status,
                condition_reason=policy_decision.reason,
                fallback_reason=policy_decision.fallback_reason,
                reason=self._determine_reason(final_action_info, prep_result),
                action_index=final_action_idx,
                action_name=final_action_info["name"],
                action_kw=final_action_kw,
                trade_action=final_action_info["trade"],
                logits=logits.tolist(),
                preprocessing_warnings=prep_result.warnings,
                latency_ms=latency_ms
            )

        except Exception as e:
            self._error_count += 1
            logger.error(f"Decision error for {node_id}: {e}")
            return self._error_decision(node_id, str(e), start_time)

    def _fallback_decision(self, node_id: str, prep_result: PreprocessingResult,
                           start_time: float) -> DecisionResult:
        """Generate fallback decision when no model available."""
        soc = prep_result.obs_dict.get("soc_kwh", 2.0)
        soc_cap = prep_result.obs_dict.get("soc_capacity_kwh", 4.0)
        price = prep_result.obs_dict.get("price_signal", 5.0)

        action_idx, action_kw = safety_shield.get_fallback_action(soc, soc_cap, price)
        action_info = DISCRETE_ACTIONS.get(action_idx, DISCRETE_ACTIONS[2])

        latency_ms = (time.time() - start_time) * 1000

        return DecisionResult(
            node_id=node_id,
            action=action_info["decision"],
            energy=0.0,
            price=price,
            confidence=0.5,
            selected_model="FALLBACK",
            safety_status=SafetyStatus.FALLBACK.value,
            condition_reason="no_model_available",
            fallback_reason="using_rule_based_fallback",
            reason="models_unavailable_using_safe_fallback",
            action_index=action_idx,
            action_name=action_info["name"],
            action_kw=action_kw,
            trade_action=action_info["trade"],
            logits=[],
            preprocessing_warnings=prep_result.warnings,
            latency_ms=latency_ms
        )

    def _error_decision(self, node_id: str, error: str,
                        start_time: float) -> DecisionResult:
        """Generate safe decision on error."""
        latency_ms = (time.time() - start_time) * 1000

        return DecisionResult(
            node_id=node_id,
            action="HOLD",
            energy=0.0,
            price=0.0,
            confidence=0.0,
            selected_model="ERROR",
            safety_status=SafetyStatus.BLOCKED.value,
            condition_reason="error_occurred",
            fallback_reason=error,
            reason=f"error_safe_fallback: {error}",
            action_index=2,
            action_name="idle",
            action_kw=0.0,
            trade_action=None,
            logits=[],
            preprocessing_warnings=[f"Error: {error}"],
            latency_ms=latency_ms
        )

    def _calculate_energy_amount(self, action_kw: float,
                                 obs_dict: Dict[str, float]) -> float:
        """Calculate recommended energy amount for trading."""
        if action_kw >= 0:  # Charging or idle
            return 0.0

        pv = obs_dict.get("pv_gen_kw", 0.5)
        load = obs_dict.get("load_kw", 0.8)
        surplus = max(0, pv - load)

        # Sell up to 80% of surplus, max 2 kWh
        return min(surplus * 0.8, 2.0)

    def _determine_reason(self, action_info: Dict[str, Any],
                          prep_result: PreprocessingResult) -> str:
        """Determine human-readable reason for decision."""
        net = prep_result.derived_features.get("net_kw", 0)
        soc_frac = prep_result.derived_features.get("soc_fraction", 0.5)

        if action_info["trade"] == "SELL":
            if net > 0:
                return "forecast_surplus_detected"
            return "high_price_opportunity"

        if action_info["decision"] == "CHARGE":
            if soc_frac < 0.3:
                return "low_soc_charging"
            return "low_price_opportunity"

        if action_info["decision"] == "DISCHARGE":
            if soc_frac > 0.8:
                return "high_soc_discharging"
            return "high_price_selling"

        return "optimal_strategy"

    def _get_model_version(self) -> str:
        """Get version string for currently active models."""
        status = model_registry.get_all_status()
        policies = status.get("available_policies", [])
        return f"GridGuardian-{'+'.join(policies)}"

    def _record_inference(self, latency_ms: float):
        """Record inference metrics."""
        self._inference_count += 1
        self._total_latency_ms += latency_ms

    def get_metrics(self) -> Dict[str, Any]:
        """Get engine metrics."""
        avg_latency = (self._total_latency_ms / self._inference_count
                       if self._inference_count > 0 else 0)

        return {
            "initialized": self._initialized,
            "inference_count": self._inference_count,
            "error_count": self._error_count,
            "avg_latency_ms": avg_latency,
            "model_status": model_registry.get_all_status(),
            "policy_selection_stats": policy_router.get_selection_stats(),
            "safety_incidents": safety_shield.get_incident_summary(),
        }

    @property
    def is_initialized(self) -> bool:
        return self._initialized


# Global instance
decision_engine = DecisionEngine()
