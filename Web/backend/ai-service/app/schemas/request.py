"""Request schemas for API endpoints."""
from __future__ import annotations

from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class TelemetryInput(BaseModel):
    """Telemetry data from edge device or backend."""
    soc_kwh: Optional[float] = Field(None, description="State of charge in kWh")
    soc_capacity_kwh: Optional[float] = Field(None, description="Battery capacity in kWh")
    pv_gen_kw: Optional[float] = Field(None, description="Solar generation in kW")
    load_kw: Optional[float] = Field(None, description="Load demand in kW")
    net_kw: Optional[float] = Field(None, description="Net power (solar - load)")
    battery_power_kw: Optional[float] = Field(None, description="Battery power")
    price_signal: Optional[float] = Field(None, description="Grid price signal")
    voltage_v: Optional[float] = Field(None, alias="voltage", description="Voltage")
    current_a: Optional[float] = Field(None, alias="current", description="Current")
    forecast_irradiance_1h: Optional[float] = None
    forecast_irradiance_3h: Optional[float] = None
    forecast_temp_1h: Optional[float] = None
    actual_irradiance_wm2: Optional[float] = None
    volatility: Optional[float] = Field(None, description="Price volatility")
    sensor_health: Optional[float] = Field(None, description="Sensor health score 0-1")
    grid_risk: Optional[float] = Field(None, description="Grid risk score 0-1")
    anomaly_score: Optional[float] = Field(None, description="Anomaly detection score")

    class Config:
        populate_by_name = True


class ContextInput(BaseModel):
    """Additional context for decision making."""
    stress_test_mode: bool = False
    previous_confidence: Optional[float] = None
    grid_price: Optional[float] = None
    avg_power_24h: Optional[float] = None
    peak_power: Optional[float] = None
    forecast_irradiance_1h: Optional[float] = None
    forecast_irradiance_3h: Optional[float] = None
    forecast_temp_1h: Optional[float] = None


class PredictRequest(BaseModel):
    """Request for /predict endpoint."""
    node_id: str = Field(..., description="Node identifier")
    telemetry: TelemetryInput = Field(..., description="Current telemetry data")
    context: Optional[ContextInput] = Field(None, description="Additional context")


class DecideRequest(BaseModel):
    """Request for /decide endpoint."""
    node_id: str = Field(..., description="Node identifier")
    telemetry: TelemetryInput = Field(..., description="Current telemetry data")
    context: Optional[ContextInput] = Field(None, description="Additional context")
    apply_safety: bool = Field(True, description="Apply safety constraints")
    force_policy: Optional[str] = Field(None, description="Force specific policy (BC/CQL/DT)")


class BatchNode(BaseModel):
    """Single node in batch request."""
    node_id: str
    telemetry: TelemetryInput
    context: Optional[ContextInput] = None


class BatchDecideRequest(BaseModel):
    """Request for batch /decide endpoint."""
    nodes: List[BatchNode]
    apply_safety: bool = True


class ReloadModelRequest(BaseModel):
    """Request to reload a specific model."""
    model_name: str = Field(..., description="Model to reload: BC, CQL, or DT")
