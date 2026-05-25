"""
API Routes for AI Decision Engine.
"""
import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logger import get_logger, metrics_tracker
from app.core.preprocessing import preprocessor
from app.models.registry import model_registry
from app.services.decision_engine import decision_engine
from app.schemas.schemas import (
    DecisionRequest,
    DecisionResponse,
    PredictRequest,
    PredictResponse,
    HealthResponse,
    ModelStatusResponse,
    MetricsResponse,
    ReloadModelRequest,
    ErrorResponse,
)

logger = get_logger(__name__)
router = APIRouter()

# Track service start time
_start_time = time.time()


@router.post("/decide", response_model=DecisionResponse, tags=["Decision"])
async def make_decision(request: DecisionRequest):
    """
    Generate AI trading decision from telemetry.

    The decision engine will:
    1. Preprocess the telemetry input
    2. Assess operating conditions
    3. Select the appropriate policy (BC/CQL/DT)
    4. Run model inference
    5. Apply safety constraints
    6. Return the final decision

    Returns:
        DecisionResponse with action, energy, confidence, and metadata
    """
    try:
        # Convert to dict
        telemetry_dict = request.telemetry.model_dump(exclude_none=True)

        # Run decision engine
        result = await decision_engine.decide(
            telemetry=telemetry_dict,
            stress_test_mode=request.stress_test_mode,
            force_model=request.force_model,
            previous_confidence=request.previous_confidence,
        )

        logger.info(
            f"Decision: {result.action} (model={result.selected_model}, "
            f"confidence={result.confidence:.2f}, latency={result.latency_ms:.1f}ms)"
        )

        return DecisionResponse(
            action=result.action,
            energy=result.energy,
            price=result.price,
            confidence=result.confidence,
            selected_model=result.selected_model,
            safety_status=result.safety_status,
            condition_reason=result.condition_reason,
            reason=result.reason,
            fallback_reason=result.fallback_reason,
            action_name=result.action_name,
            action_index=result.action_index,
            trade_action=result.trade_action,
            net_energy=result.net_energy,
            soc_percent=result.soc_percent,
            latency_ms=result.latency_ms,
            model_version=result.model_version,
            is_fallback=result.is_fallback,
        )

    except Exception as e:
        logger.error(f"Decision endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/predict", response_model=PredictResponse, tags=["Prediction"])
async def make_prediction(request: PredictRequest):
    """
    Generate prediction/forecast from telemetry.

    Returns forecasted values for load, solar, net energy, and risk assessment.
    """
    try:
        telemetry_dict = request.telemetry.model_dump(exclude_none=True)

        # Preprocess
        processed = preprocessor.preprocess(telemetry_dict, normalize=False)

        # Simple forecast (in production, would use actual forecasting model)
        load = processed.get("load_kw", 0.8) * 1000
        solar = processed.get("pv_gen_kw", 0.5) * 1000
        net = solar - load
        soc = (processed.get("soc_kwh", 2.0) / processed.get("soc_capacity_kwh", 4.0)) * 100
        price = processed.get("price_signal", 0.15)
        risk = processed.get("grid_risk", 0.1)
        sensor_health = processed.get("sensor_health", 1.0)

        return PredictResponse(
            forecasted_load=load,
            forecasted_solar=solar,
            net_forecast=net,
            soc_forecast=soc,
            price_forecast=price,
            risk_level=risk,
            confidence=sensor_health * 0.9,
            horizon=request.horizon or "1h",
        )

    except Exception as e:
        logger.error(f"Predict endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """
    Service health check.

    Returns service status, model availability, and uptime.
    """
    models = model_registry.get_available_models()
    loaded_count = sum(1 for v in models.values() if v)

    status = "healthy"
    if loaded_count == 0:
        status = "degraded"
    elif loaded_count < len(models):
        status = "partial"

    return HealthResponse(
        status=status,
        models_loaded=loaded_count,
        total_models=len(models),
        uptime_seconds=time.time() - _start_time,
        environment=settings.ENVIRONMENT,
    )


@router.get("/model/status", response_model=ModelStatusResponse, tags=["System"])
async def model_status():
    """
    Get detailed model status.

    Returns information about all models including load state and errors.
    """
    all_info = model_registry.get_all_info()

    models_dict = {}
    for name, info in all_info.items():
        models_dict[name] = {
            "name": info.name,
            "type": info.type,
            "loaded": info.loaded,
            "format": info.format,
            "path": info.path,
            "error": info.error,
            "metadata": info.metadata,
        }

    return ModelStatusResponse(
        models=models_dict,
        active_model=settings.DEFAULT_MODEL.upper(),
        fallback_model=settings.FALLBACK_MODEL.upper(),
    )


@router.post("/reload-model", tags=["System"])
async def reload_model(request: ReloadModelRequest):
    """
    Reload a specific model.

    Use this to update models without restarting the service.
    """
    model_name = request.model_name.lower()

    if model_name not in ["bc", "cql", "dt"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model name: {model_name}. Valid options: bc, cql, dt"
        )

    success = await model_registry.reload_model(model_name)

    if success:
        return {"success": True, "message": f"Model {model_name} reloaded successfully"}
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reload model {model_name}"
        )


@router.get("/metrics", response_model=MetricsResponse, tags=["System"])
async def get_metrics():
    """
    Get service metrics.

    Returns request counts, latencies, model selections, and safety statistics.
    """
    metrics = metrics_tracker.get_metrics()

    return MetricsResponse(
        total_requests=metrics["total_requests"],
        successful_requests=metrics["successful_requests"],
        failed_requests=metrics["failed_requests"],
        avg_latency_ms=metrics["avg_latency_ms"],
        model_selections=metrics["model_selections"],
        safety_blocks=metrics["safety_blocks"],
        fallback_events=metrics["fallback_events"],
        decision_counts=metrics["decision_counts"],
    )


@router.post("/metrics/reset", tags=["System"])
async def reset_metrics():
    """Reset all metrics counters."""
    metrics_tracker.reset()
    return {"success": True, "message": "Metrics reset"}


# Convenience endpoints for direct integration

@router.post("/infer", tags=["Inference"])
async def direct_inference(
    node_id: Optional[str] = Query(None, description="Node ID"),
    model: Optional[str] = Query(None, description="Model to use (bc/cql/dt)"),
):
    """
    Direct inference endpoint for quick integration.

    Accepts query parameters and returns a simplified decision.
    """
    # This would typically receive telemetry via body
    # For now, return a sample response
    return {
        "node_id": node_id or "unknown",
        "action": "HOLD",
        "confidence": 0.5,
        "selected_model": model or "cql",
        "message": "Use POST /decide for full inference",
    }


# Error handlers
@router.get("/test-error", include_in_schema=False)
async def test_error():
    """Test endpoint to verify error handling."""
    raise HTTPException(status_code=500, detail="Test error")
