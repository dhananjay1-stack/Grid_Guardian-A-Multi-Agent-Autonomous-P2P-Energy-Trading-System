"""Response schemas for API endpoints."""
from __future__ import annotations

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class PredictResponse(BaseModel):
    """Response from /predict endpoint."""
    node_id: str
    forecast_load_kw: float
    forecast_solar_kw: float
    net_power_kw: float
    soc_kwh: float
    soc_forecast_1h: float
    price_signal: float
    model_version: str
    timestamp: float


class DecideResponse(BaseModel):
    """Response from /decide endpoint."""
    node_id: str
    action: str  # BUY, SELL, HOLD, CHARGE, DISCHARGE
    energy: float
    price: float
    confidence: float
    selected_model: str
    safety_status: str
    condition_reason: str
    fallback_reason: str = ""
    reason: str
    action_index: int
    action_name: str
    action_kw: float
    trade_action: Optional[str]
    logits: List[float] = []
    preprocessing_warnings: List[str] = []
    latency_ms: float
    timestamp: Optional[float] = None


class BatchDecideResult(BaseModel):
    """Single result in batch response."""
    node_id: str
    success: bool
    decision: Optional[str] = None
    confidence: Optional[float] = None
    action_kw: Optional[float] = None
    trade_action: Optional[str] = None
    error: Optional[str] = None


class BatchDecideResponse(BaseModel):
    """Response from batch /decide endpoint."""
    results: List[BatchDecideResult]
    timestamp: float


class HealthResponse(BaseModel):
    """Response from /health endpoint."""
    status: str
    models_loaded: bool
    model_type: str
    available_policies: List[str]
    active_policy: Optional[str]
    timestamp: str


class ModelStatusResponse(BaseModel):
    """Response from /model/status endpoint."""
    initialized: bool
    active_policy: Optional[str]
    fallback_policy: str
    policies: Dict[str, Any]
    available_policies: List[str]


class MetricsResponse(BaseModel):
    """Response from /metrics endpoint."""
    initialized: bool
    inference_count: int
    error_count: int
    avg_latency_ms: float
    model_status: Dict[str, Any]
    policy_selection_stats: Dict[str, Any]
    safety_incidents: Dict[str, Any]


class ReloadModelResponse(BaseModel):
    """Response from /reload-model endpoint."""
    success: bool
    model_name: str
    message: str
