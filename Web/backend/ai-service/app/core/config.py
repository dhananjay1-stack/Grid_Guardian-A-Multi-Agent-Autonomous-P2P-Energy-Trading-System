"""Configuration management for AI service."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class SafetySettings(BaseSettings):
    """Safety layer configuration."""
    soc_min_frac: float = 0.10
    soc_max_frac: float = 0.95
    max_charge_kw: float = 3.0
    max_discharge_kw: float = 3.0
    max_grid_draw_kw: float = 5.0
    min_confidence: float = 0.3
    shield_mode: str = "clip"


class PolicySelectionThresholds(BaseSettings):
    """Thresholds for dynamic policy selection."""
    volatility_high: float = 0.25
    grid_risk_high: float = 0.3
    sensor_health_low: float = 0.8
    anomaly_score_high: float = 0.5
    soc_critical_low: float = 0.15
    soc_critical_high: float = 0.90
    confidence_low: float = 0.4


class Settings(BaseSettings):
    """Main application settings."""

    # Server settings
    host: str = Field(default="0.0.0.0", env="AI_SERVER_HOST")
    port: int = Field(default=5050, env="AI_SERVER_PORT")
    debug: bool = Field(default=False, env="AI_SERVER_DEBUG")
    workers: int = Field(default=1, env="AI_SERVER_WORKERS")

    # Model paths
    model_base_path: Path = Field(
        default_factory=lambda: Path(__file__).parent.parent.parent.parent.parent / "Agentic_AI" / "models"
    )
    policy_pack_path: Path = Field(
        default_factory=lambda: Path(__file__).parent.parent.parent.parent.parent / "Agentic_AI" / "edge" / "policy_pack"
    )

    # Model configuration
    default_policy: str = Field(default="DT", env="DEFAULT_POLICY")
    fallback_policy: str = Field(default="BC", env="FALLBACK_POLICY")
    use_onnx: bool = Field(default=False, env="USE_ONNX_RUNTIME")

    # Feature dimensions
    obs_dim: int = 18
    act_dim: int = 7

    # Safety and policy selection
    safety: SafetySettings = SafetySettings()
    thresholds: PolicySelectionThresholds = PolicySelectionThresholds()

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    # Metrics
    enable_metrics: bool = True
    metrics_window_size: int = 1000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()


# Discrete action mapping
DISCRETE_ACTIONS: Dict[int, Dict[str, Any]] = {
    0: {"name": "charge_small", "kw": 1.0, "decision": "CHARGE", "trade": None},
    1: {"name": "charge_large", "kw": 3.0, "decision": "CHARGE", "trade": None},
    2: {"name": "idle", "kw": 0.0, "decision": "HOLD", "trade": None},
    3: {"name": "discharge_small", "kw": -1.0, "decision": "DISCHARGE", "trade": None},
    4: {"name": "discharge_large", "kw": -3.0, "decision": "DISCHARGE", "trade": None},
    5: {"name": "offer_sell", "kw": -1.5, "decision": "SELL", "trade": "SELL"},
    6: {"name": "offer_hold", "kw": 0.0, "decision": "HOLD", "trade": None},
}


# Observation keys
OBS_KEYS = [
    "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
    "battery_power_kw", "price_signal", "forecast_irradiance_1h",
    "forecast_irradiance_3h", "forecast_temp_1h", "actual_irradiance_wm2",
    "voltage_v", "current_a", "volatility", "sensor_health",
    "grid_risk", "anomaly_score", "stress_test_mode"
]
