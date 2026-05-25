"""
Configuration settings for AI Decision Engine.
"""
import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Service Configuration
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"
    DEBUG: bool = False

    # Model Configuration
    MODEL_ARTIFACTS_PATH: str = str(Path(__file__).parent.parent.parent / "artifacts" / "models")
    DEFAULT_MODEL: str = "cql"  # Default model to use
    FALLBACK_MODEL: str = "bc"  # Fallback when other models fail

    # Inference Configuration
    INFERENCE_TIMEOUT: float = 5.0  # seconds
    BATCH_SIZE: int = 1

    # Safety Configuration
    SOC_MIN: float = 10.0  # Minimum State of Charge (%)
    SOC_MAX: float = 95.0  # Maximum State of Charge (%)
    POWER_MIN: float = -5.0  # Min power (kW, negative = discharge)
    POWER_MAX: float = 5.0  # Max power (kW, positive = charge)
    CONFIDENCE_THRESHOLD: float = 0.6  # Minimum confidence for trades
    PRICE_MIN: float = 0.0  # Minimum energy price
    PRICE_MAX: float = 100.0  # Maximum energy price

    # Policy Selection Thresholds
    VOLATILITY_THRESHOLD: float = 0.3
    RISK_THRESHOLD: float = 0.4
    SENSOR_HEALTH_THRESHOLD: float = 0.8
    ANOMALY_THRESHOLD: float = 0.5

    # CORS Settings
    CORS_ORIGINS: List[str] = ["*"]

    # Backend Integration
    BACKEND_URL: str = "http://localhost:3000"

    class Config:
        env_prefix = "AI_"
        env_file = ".env"
        extra = "ignore"


# Global settings instance
settings = Settings()


# Model metadata configuration
MODEL_METADATA = {
    "bc": {
        "name": "Behavior Cloning",
        "type": "BC",
        "description": "Safest baseline policy, good for fallback",
        "preferred_conditions": ["degraded", "low_confidence", "stress_test"],
        "risk_tolerance": "low",
        "horizon": "short",
    },
    "cql": {
        "name": "Conservative Q-Learning",
        "type": "CQL",
        "description": "Conservative choice for risky or uncertain conditions",
        "preferred_conditions": ["high_volatility", "uncertain", "risky"],
        "risk_tolerance": "medium",
        "horizon": "medium",
    },
    "dt": {
        "name": "Decision Transformer",
        "type": "DT",
        "description": "Best for stable conditions with long-horizon planning",
        "preferred_conditions": ["stable", "normal", "low_risk"],
        "risk_tolerance": "high",
        "horizon": "long",
    },
}


# Action mapping
ACTION_MAP = {
    0: {"name": "charge_small", "action": "CHARGE", "kw": 1.0, "trade": None},
    1: {"name": "charge_large", "action": "CHARGE", "kw": 3.0, "trade": None},
    2: {"name": "idle", "action": "HOLD", "kw": 0.0, "trade": None},
    3: {"name": "discharge_small", "action": "DISCHARGE", "kw": -1.0, "trade": None},
    4: {"name": "discharge_large", "action": "DISCHARGE", "kw": -3.0, "trade": None},
    5: {"name": "offer_sell", "action": "SELL", "kw": -1.5, "trade": "SELL"},
    6: {"name": "offer_buy", "action": "BUY", "kw": 1.5, "trade": "BUY"},
}


# Feature configuration
FEATURE_CONFIG = {
    "observation_dim": 18,
    "features": [
        "soc_kwh",
        "soc_capacity_kwh",
        "pv_gen_kw",
        "load_kw",
        "net_kw",
        "battery_power_kw",
        "price_signal",
        "forecast_irradiance_1h",
        "forecast_irradiance_3h",
        "forecast_temp_1h",
        "actual_irradiance_wm2",
        "voltage_v",
        "current_a",
        "volatility",
        "sensor_health",
        "grid_risk",
        "anomaly_score",
        "hour_of_day",
    ],
    "defaults": {
        "soc_kwh": 2.0,
        "soc_capacity_kwh": 4.0,
        "pv_gen_kw": 0.5,
        "load_kw": 0.8,
        "net_kw": -0.3,
        "battery_power_kw": 0.0,
        "price_signal": 0.15,
        "forecast_irradiance_1h": 400.0,
        "forecast_irradiance_3h": 350.0,
        "forecast_temp_1h": 25.0,
        "actual_irradiance_wm2": 450.0,
        "voltage_v": 230.0,
        "current_a": 3.5,
        "volatility": 0.1,
        "sensor_health": 1.0,
        "grid_risk": 0.1,
        "anomaly_score": 0.0,
        "hour_of_day": 12.0,
    },
}
