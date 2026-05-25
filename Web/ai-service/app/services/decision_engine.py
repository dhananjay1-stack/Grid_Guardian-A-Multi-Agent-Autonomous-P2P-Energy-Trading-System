"""
Decision Engine Service - Core AI decision-making logic.
"""
import time
from typing import Dict, Any, Optional
from dataclasses import dataclass

from app.core.config import settings, ACTION_MAP
from app.core.logger import get_logger, metrics_tracker
from app.core.condition_detector import condition_detector, ConditionAssessment
from app.core.safety import safety_shield, SafetyResult
from app.core.preprocessing import preprocessor
from app.models.registry import model_registry

logger = get_logger(__name__)


@dataclass
class DecisionResult:
    """Complete decision result from the AI engine."""
    action: str
    energy: float
    price: float
    confidence: float
    selected_model: str
    safety_status: str
    condition_reason: str
    reason: str
    fallback_reason: Optional[str]
    # Extended fields
    action_name: str
    action_index: int
    trade_action: Optional[str]
    net_energy: float
    soc_percent: float
    # Metadata
    latency_ms: float
    model_version: str
    is_fallback: bool


class DecisionEngine:
    """
    Main decision engine that orchestrates:
    1. Preprocessing
    2. Condition detection
    3. Policy selection
    4. Model inference
    5. Safety validation
    """

    def __init__(self):
        self.fallback_model = settings.FALLBACK_MODEL
        self.default_model = settings.DEFAULT_MODEL

    async def decide(
        self,
        telemetry: Dict[str, Any],
        stress_test_mode: bool = False,
        force_model: Optional[str] = None,
        previous_confidence: Optional[float] = None,
    ) -> DecisionResult:
        """
        Generate a trading decision from telemetry input.

        Args:
            telemetry: Raw telemetry data
            stress_test_mode: Whether in stress test mode
            force_model: Force use of a specific model
            previous_confidence: Previous decision confidence

        Returns:
            DecisionResult with complete decision information
        """
        start_time = time.time()

        try:
            # Step 1: Preprocess input
            processed = preprocessor.preprocess(telemetry, normalize=False)

            # Extract key values
            soc = processed.get("soc_kwh", 2.0)
            soc_capacity = processed.get("soc_capacity_kwh", 4.0)
            soc_percent = (soc / soc_capacity * 100) if soc_capacity > 0 else 50.0
            volatility = processed.get("volatility", 0.1)
            sensor_health = processed.get("sensor_health", 1.0)
            anomaly_score = processed.get("anomaly_score", 0.0)
            grid_risk = processed.get("grid_risk", 0.1)
            price = processed.get("price_signal", 0.15)
            net_kw = processed.get("net_kw", 0.0)

            # Step 2: Assess conditions
            condition_assessment = condition_detector.assess(
                soc=soc_percent,
                volatility=volatility,
                sensor_health=sensor_health,
                anomaly_score=anomaly_score,
                grid_risk=grid_risk,
                stress_test_mode=stress_test_mode,
                previous_confidence=previous_confidence,
            )

            # Step 3: Select model
            if force_model and force_model.lower() in model_registry.get_available_models():
                selected_model = force_model.lower()
                condition_reason = "forced_model_selection"
            else:
                selected_model = condition_assessment.recommended_model
                condition_reason = condition_assessment.reason

            # Step 4: Run inference
            inference_result, is_fallback, fallback_reason = await self._run_inference(
                selected_model, processed
            )

            if is_fallback:
                metrics_tracker.record_fallback()
                selected_model = self.fallback_model

            metrics_tracker.record_model_selection(selected_model)

            # Step 5: Apply safety checks
            safety_result = safety_shield.validate_and_constrain(
                action=inference_result["action"],
                energy=inference_result["action_kw"],
                price=price,
                confidence=inference_result["confidence"],
                soc=soc_percent,
                model_name=selected_model,
                anomaly_score=anomaly_score,
                sensor_health=sensor_health,
            )

            # Determine final action
            final_action = safety_result.final_action
            final_energy = safety_result.final_energy

            # Calculate recommended trade energy
            trade_energy = 0.0
            if safety_result.status.value in ["APPROVED", "MODIFIED"]:
                if inference_result["trade_action"] == "SELL" and net_kw > 0:
                    trade_energy = min(net_kw * 0.8, 2.0)
                elif inference_result["trade_action"] == "BUY" and net_kw < 0:
                    trade_energy = min(abs(net_kw) * 0.8, 2.0)

            # Build reason
            if safety_result.violations:
                reason = f"safety_{safety_result.status.value.lower()}_{len(safety_result.violations)}_violations"
            else:
                reason = self._determine_reason(final_action, net_kw, soc_percent, price)

            latency_ms = (time.time() - start_time) * 1000
            metrics_tracker.record_request(True, latency_ms)
            metrics_tracker.record_decision(final_action)

            return DecisionResult(
                action=final_action,
                energy=trade_energy,
                price=price * 1000,  # Convert to market price units
                confidence=inference_result["confidence"],
                selected_model=selected_model.upper(),
                safety_status=safety_result.status.value,
                condition_reason=condition_reason,
                reason=reason,
                fallback_reason=fallback_reason,
                action_name=inference_result["action_name"],
                action_index=inference_result["action_index"],
                trade_action=inference_result["trade_action"],
                net_energy=final_energy,
                soc_percent=soc_percent,
                latency_ms=latency_ms,
                model_version=f"GridGuardian-{selected_model.upper()}",
                is_fallback=is_fallback,
            )

        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            metrics_tracker.record_request(False, latency_ms)
            logger.error(f"Decision engine error: {e}")

            # Return safe fallback
            return DecisionResult(
                action="HOLD",
                energy=0.0,
                price=0.0,
                confidence=0.0,
                selected_model="FALLBACK",
                safety_status="FALLBACK",
                condition_reason="error_occurred",
                reason=str(e),
                fallback_reason=str(e),
                action_name="idle",
                action_index=2,
                trade_action=None,
                net_energy=0.0,
                soc_percent=50.0,
                latency_ms=latency_ms,
                model_version="GridGuardian-FALLBACK",
                is_fallback=True,
            )

    async def _run_inference(
        self, model_name: str, processed: Dict[str, Any]
    ) -> tuple[Dict[str, Any], bool, Optional[str]]:
        """
        Run model inference with fallback support.

        Returns:
            (inference_result, is_fallback, fallback_reason)
        """
        # Build observation vector
        obs = preprocessor.to_observation_vector(processed)

        # Try selected model
        if model_name in model_registry.models:
            try:
                result = model_registry.run_inference(model_name, obs)
                return result, False, None
            except Exception as e:
                logger.warning(f"Model {model_name} inference failed: {e}")

        # Try fallback model
        if self.fallback_model in model_registry.models:
            try:
                result = model_registry.run_inference(self.fallback_model, obs)
                return result, True, f"{model_name}_failed_using_{self.fallback_model}"
            except Exception as e:
                logger.warning(f"Fallback model {self.fallback_model} failed: {e}")

        # Return safe default
        logger.error("All models failed, returning HOLD")
        return {
            "action_index": 2,
            "action_name": "idle",
            "action": "HOLD",
            "action_kw": 0.0,
            "trade_action": None,
            "confidence": 0.0,
            "logits": [0.0] * 7,
            "probabilities": [0.0] * 7,
        }, True, "all_models_failed"

    def _determine_reason(
        self, action: str, net_kw: float, soc: float, price: float
    ) -> str:
        """Determine human-readable reason for decision."""
        if action == "SELL":
            if net_kw > 0.5:
                return "forecast_surplus_detected"
            return "sell_opportunity"

        if action == "BUY":
            if net_kw < -0.5:
                return "forecast_deficit_detected"
            if price < 0.1:
                return "low_price_opportunity"
            return "buy_opportunity"

        if action == "CHARGE":
            if soc < 30:
                return "low_soc_charging"
            if net_kw > 0:
                return "surplus_charging"
            return "scheduled_charging"

        if action == "DISCHARGE":
            if soc > 80:
                return "high_soc_discharging"
            if price > 0.2:
                return "high_price_discharging"
            return "scheduled_discharging"

        # HOLD
        if soc >= 10 and soc <= 95:
            return "stable_conditions_holding"
        return "conservative_holding"


# Global decision engine instance
decision_engine = DecisionEngine()
