"""
Request and response schemas for API endpoints.
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime


# Request Schemas

class TelemetryInput(BaseModel):
    """Telemetry input for prediction/decision."""
    load: Optional[float] = Field(None, description="Load in watts")
    solar: Optional[float] = Field(None, description="Solar generation in watts")
    soc: Optional[float] = Field(None, description="State of charge (%)")
    soc_kwh: Optional[float] = Field(None, description="State of charge (kWh)")
    soc_capacity_kwh: Optional[float] = Field(None, description="Battery capacity (kWh)")
    price: Optional[float] = Field(None, description="Energy price signal")
    volatility: Optional[float] = Field(0.1, description="Market volatility (0-1)")
    sensor_health: Optional[float] = Field(1.0, description="Sensor reliability (0-1)")
    grid_risk: Optional[float] = Field(0.1, description="Grid risk level (0-1)")
    anomaly_score: Optional[float] = Field(0.0, description="Anomaly detection score (0-1)")
    # Extended fields
    voltage: Optional[float] = Field(None, description="Voltage (V)")
    current: Optional[float] = Field(None, description="Current (A)")
    power: Optional[float] = Field(None, description="Power (W)")
    forecast_irradiance_1h: Optional[float] = Field(None, description="1h irradiance forecast")
    forecast_irradiance_3h: Optional[float] = Field(None, description="3h irradiance forecast")
    forecast_temp_1h: Optional[float] = Field(None, description="1h temperature forecast")
    timestamp: Optional[float] = Field(None, description="Unix timestamp")

    class Config:
        extra = "allow"


class DecisionRequest(BaseModel):
    """Request for AI decision."""
    telemetry: TelemetryInput
    node_id: Optional[str] = Field(None, description="Node identifier")
    stress_test_mode: bool = Field(False, description="Enable stress test mode")
    force_model: Optional[str] = Field(None, description="Force specific model (bc/cql/dt)")
    previous_confidence: Optional[float] = Field(None, description="Previous decision confidence")


class PredictRequest(BaseModel):
    """Request for prediction."""
    telemetry: TelemetryInput
    node_id: Optional[str] = Field(None, description="Node identifier")
    horizon: Optional[str] = Field("1h", description="Prediction horizon")


class ReloadModelRequest(BaseModel):
    """Request to reload a model."""
    model_name: str = Field(..., description="Model name (bc/cql/dt)")


# Response Schemas

class DecisionResponse(BaseModel):
    """AI decision response."""
    action: str = Field(..., description="Trading action (BUY/SELL/HOLD/CHARGE/DISCHARGE)")
    energy: float = Field(..., description="Recommended energy amount (kWh)")
    price: float = Field(..., description="Recommended price")
    confidence: float = Field(..., description="Decision confidence (0-1)")
    selected_model: str = Field(..., description="Selected policy model")
    safety_status: str = Field(..., description="Safety check status")
    condition_reason: str = Field(..., description="Condition assessment reason")
    reason: str = Field(..., description="Decision reason")
    fallback_reason: Optional[str] = Field(None, description="Fallback reason if applicable")
    # Extended fields
    action_name: str = Field(..., description="Action name (e.g., offer_sell)")
    action_index: int = Field(..., description="Action index (0-6)")
    trade_action: Optional[str] = Field(None, description="Trade action (BUY/SELL/None)")
    net_energy: float = Field(..., description="Net energy flow (kW)")
    soc_percent: float = Field(..., description="State of charge (%)")
    # Metadata
    latency_ms: float = Field(..., description="Inference latency (ms)")
    model_version: str = Field(..., description="Model version")
    is_fallback: bool = Field(..., description="Whether fallback was used")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class PredictResponse(BaseModel):
    """Prediction response."""
    forecasted_load: float = Field(..., description="Forecasted load (W)")
    forecasted_solar: float = Field(..., description="Forecasted solar (W)")
    net_forecast: float = Field(..., description="Net energy forecast (W)")
    soc_forecast: float = Field(..., description="Forecasted SoC (%)")
    price_forecast: float = Field(..., description="Forecasted price")
    risk_level: float = Field(..., description="Risk assessment (0-1)")
    confidence: float = Field(..., description="Forecast confidence (0-1)")
    horizon: str = Field(..., description="Prediction horizon")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = Field(..., description="Service status")
    models_loaded: int = Field(..., description="Number of models loaded")
    total_models: int = Field(..., description="Total models expected")
    uptime_seconds: float = Field(..., description="Service uptime")
    environment: str = Field(..., description="Environment")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ModelStatusResponse(BaseModel):
    """Model status response."""
    models: Dict[str, Dict[str, Any]] = Field(..., description="Model status details")
    active_model: str = Field(..., description="Default active model")
    fallback_model: str = Field(..., description="Fallback model")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class MetricsResponse(BaseModel):
    """Service metrics response."""
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_latency_ms: float
    model_selections: Dict[str, int]
    safety_blocks: int
    fallback_events: int
    decision_counts: Dict[str, int]
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ErrorResponse(BaseModel):
    """Error response."""
    error: str = Field(..., description="Error message")
    detail: Optional[str] = Field(None, description="Error details")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
