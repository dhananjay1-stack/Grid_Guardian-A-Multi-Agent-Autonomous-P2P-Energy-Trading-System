"""API routes for AI Decision Engine."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, status

from ..schemas import (
    PredictRequest, PredictResponse,
    DecideRequest, DecideResponse,
    BatchDecideRequest, BatchDecideResponse, BatchDecideResult,
    HealthResponse, ModelStatusResponse, MetricsResponse,
    ReloadModelRequest, ReloadModelResponse
)
from ..services.decision_engine import decision_engine
from ..models.registry import model_registry
from ..core.logger import get_logger


logger = get_logger("api")

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Health check endpoint.

    Returns service status and model availability.
    """
    status_info = model_registry.get_all_status()

    return HealthResponse(
        status="healthy" if decision_engine.is_initialized else "degraded",
        models_loaded=decision_engine.is_initialized,
        model_type="multi-policy",
        available_policies=status_info.get("available_policies", []),
        active_policy=status_info.get("active_policy"),
        timestamp=datetime.utcnow().isoformat()
    )


@router.get("/model/status", response_model=ModelStatusResponse)
async def model_status():
    """
    Get detailed model status.

    Returns status of all policies and their metadata.
    """
    return model_registry.get_all_status()


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics():
    """
    Get service metrics.

    Returns inference counts, latencies, and policy usage statistics.
    """
    return decision_engine.get_metrics()


@router.post("/predict", response_model=PredictResponse)
async def predict(request: PredictRequest):
    """
    Generate predictions/forecasts from telemetry.

    Returns forecast values for load, solar, SoC without making trading decisions.
    """
    if not decision_engine.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Decision engine not initialized"
        )

    try:
        telemetry_dict = request.telemetry.model_dump(by_alias=True, exclude_none=True)
        context_dict = request.context.model_dump() if request.context else {}

        result = decision_engine.predict(
            node_id=request.node_id,
            telemetry=telemetry_dict,
            context=context_dict
        )

        return PredictResponse(
            node_id=result.node_id,
            forecast_load_kw=result.forecast_load_kw,
            forecast_solar_kw=result.forecast_solar_kw,
            net_power_kw=result.net_power_kw,
            soc_kwh=result.soc_kwh,
            soc_forecast_1h=result.soc_forecast_1h,
            price_signal=result.price_signal,
            model_version=result.model_version,
            timestamp=result.timestamp
        )

    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/decide", response_model=DecideResponse)
async def decide(request: DecideRequest):
    """
    Generate trading/control decision from telemetry.

    Full decision pipeline:
    - Preprocesses telemetry
    - Selects appropriate policy (BC/CQL/DT) based on conditions
    - Runs inference
    - Applies safety constraints
    - Returns actionable decision

    Response includes:
    - action: BUY/SELL/HOLD/CHARGE/DISCHARGE
    - energy: Recommended energy amount
    - confidence: Model confidence score
    - selected_model: Which policy was used
    - safety_status: APPROVED/MODIFIED/BLOCKED/FALLBACK
    - condition_reason: Why this policy was selected
    """
    if not decision_engine.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Decision engine not initialized"
        )

    try:
        telemetry_dict = request.telemetry.model_dump(by_alias=True, exclude_none=True)
        context_dict = request.context.model_dump() if request.context else {}

        result = decision_engine.decide(
            node_id=request.node_id,
            telemetry=telemetry_dict,
            context=context_dict,
            apply_safety=request.apply_safety,
            force_policy=request.force_policy
        )

        return DecideResponse(
            node_id=result.node_id,
            action=result.action,
            energy=result.energy,
            price=result.price,
            confidence=result.confidence,
            selected_model=result.selected_model,
            safety_status=result.safety_status,
            condition_reason=result.condition_reason,
            fallback_reason=result.fallback_reason,
            reason=result.reason,
            action_index=result.action_index,
            action_name=result.action_name,
            action_kw=result.action_kw,
            trade_action=result.trade_action,
            logits=result.logits,
            preprocessing_warnings=result.preprocessing_warnings,
            latency_ms=result.latency_ms,
            timestamp=time.time()
        )

    except Exception as e:
        logger.error(f"Decision error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/batch-decide", response_model=BatchDecideResponse)
async def batch_decide(request: BatchDecideRequest):
    """
    Run decisions for multiple nodes in a batch.

    More efficient than calling /decide multiple times.
    """
    if not decision_engine.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Decision engine not initialized"
        )

    results = []

    for node_data in request.nodes:
        try:
            telemetry_dict = node_data.telemetry.model_dump(by_alias=True, exclude_none=True)
            context_dict = node_data.context.model_dump() if node_data.context else {}

            result = decision_engine.decide(
                node_id=node_data.node_id,
                telemetry=telemetry_dict,
                context=context_dict,
                apply_safety=request.apply_safety
            )

            results.append(BatchDecideResult(
                node_id=node_data.node_id,
                success=True,
                decision=result.action,
                confidence=result.confidence,
                action_kw=result.action_kw,
                trade_action=result.trade_action
            ))

        except Exception as e:
            results.append(BatchDecideResult(
                node_id=node_data.node_id,
                success=False,
                error=str(e)
            ))

    return BatchDecideResponse(
        results=results,
        timestamp=time.time()
    )


@router.post("/reload-model", response_model=ReloadModelResponse)
async def reload_model(request: ReloadModelRequest):
    """
    Reload a specific model.

    Useful for hot-reloading updated model weights.
    """
    model_name = request.model_name.upper()

    if model_name not in ["BC", "CQL", "DT"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid model name: {model_name}. Must be BC, CQL, or DT."
        )

    success = model_registry.reload_policy(model_name)

    return ReloadModelResponse(
        success=success,
        model_name=model_name,
        message=f"Model {model_name} {'reloaded successfully' if success else 'reload failed'}"
    )


# Legacy endpoint for compatibility with existing backend
@router.post("/infer")
async def infer_legacy(data: Dict[str, Any]):
    """
    Legacy inference endpoint for backward compatibility.

    Maps to the new /decide endpoint.
    """
    if not decision_engine.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Decision engine not initialized"
        )

    try:
        node_id = data.get("node_id", "unknown")
        telemetry = data.get("telemetry", {})
        context = data.get("context", {})
        apply_safety = data.get("apply_safety", True)

        result = decision_engine.decide(
            node_id=node_id,
            telemetry=telemetry,
            context=context,
            apply_safety=apply_safety
        )

        # Return in legacy format
        return {
            "node_id": result.node_id,
            "decision": result.action,
            "confidence": result.confidence,
            "action_index": result.action_index,
            "action_name": result.action_name,
            "action_kw": result.action_kw,
            "trade_action": result.trade_action,
            "recommended_quantity": result.energy,
            "forecasted_load": result.price * 1000,  # Approximate
            "forecasted_solar": result.energy * 1000 if result.energy > 0 else 0,
            "net_power_kw": result.action_kw,
            "timestamp": datetime.utcnow().isoformat(),
            "model_version": f"GridGuardian-{result.selected_model}",
            "safety_status": result.safety_status,
            "selected_model": result.selected_model,
            "condition": result.condition_reason.split("_")[0] if result.condition_reason else "normal",
            "condition_reason": result.condition_reason
        }

    except Exception as e:
        logger.error(f"Legacy inference error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
