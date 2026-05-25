"""
Preprocessing pipeline for telemetry data.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime
import numpy as np

from app.core.config import FEATURE_CONFIG
from app.core.logger import get_logger

logger = get_logger(__name__)


class Preprocessor:
    """
    Preprocesses telemetry data for model inference.

    - Validates input schema
    - Normalizes/scales values
    - Handles missing fields
    - Clips unrealistic values
    - Calculates derived features
    """

    def __init__(self):
        self.feature_config = FEATURE_CONFIG
        self.defaults = FEATURE_CONFIG["defaults"]
        self.feature_order = FEATURE_CONFIG["features"]

        # Normalization stats (from training data - loaded from config)
        self.norm_stats = {
            "soc_kwh": {"mean": 2.0, "std": 1.0, "min": 0.0, "max": 4.0},
            "soc_capacity_kwh": {"mean": 4.0, "std": 0.1, "min": 0.0, "max": 10.0},
            "pv_gen_kw": {"mean": 1.0, "std": 1.0, "min": 0.0, "max": 5.0},
            "load_kw": {"mean": 1.0, "std": 0.5, "min": 0.0, "max": 5.0},
            "net_kw": {"mean": 0.0, "std": 1.0, "min": -5.0, "max": 5.0},
            "battery_power_kw": {"mean": 0.0, "std": 1.0, "min": -3.0, "max": 3.0},
            "price_signal": {"mean": 0.15, "std": 0.05, "min": 0.0, "max": 1.0},
            "forecast_irradiance_1h": {"mean": 400.0, "std": 200.0, "min": 0.0, "max": 1200.0},
            "forecast_irradiance_3h": {"mean": 350.0, "std": 200.0, "min": 0.0, "max": 1200.0},
            "forecast_temp_1h": {"mean": 25.0, "std": 10.0, "min": -10.0, "max": 50.0},
            "actual_irradiance_wm2": {"mean": 450.0, "std": 250.0, "min": 0.0, "max": 1200.0},
            "voltage_v": {"mean": 230.0, "std": 10.0, "min": 200.0, "max": 260.0},
            "current_a": {"mean": 5.0, "std": 3.0, "min": 0.0, "max": 30.0},
            "volatility": {"mean": 0.1, "std": 0.1, "min": 0.0, "max": 1.0},
            "sensor_health": {"mean": 0.95, "std": 0.1, "min": 0.0, "max": 1.0},
            "grid_risk": {"mean": 0.1, "std": 0.1, "min": 0.0, "max": 1.0},
            "anomaly_score": {"mean": 0.05, "std": 0.1, "min": 0.0, "max": 1.0},
            "hour_of_day": {"mean": 12.0, "std": 7.0, "min": 0.0, "max": 23.0},
        }

    def preprocess(
        self,
        raw_input: Dict[str, Any],
        normalize: bool = True,
        calculate_derived: bool = True,
    ) -> Dict[str, Any]:
        """
        Preprocess raw telemetry input.

        Args:
            raw_input: Raw telemetry data
            normalize: Whether to normalize values
            calculate_derived: Whether to calculate derived features

        Returns:
            Preprocessed data ready for model inference
        """
        # Start with defaults
        processed = {k: v for k, v in self.defaults.items()}

        # Map input fields to features
        field_mapping = self._get_field_mapping()

        for input_field, feature_name in field_mapping.items():
            if input_field in raw_input and raw_input[input_field] is not None:
                processed[feature_name] = float(raw_input[input_field])

        # Handle special conversions
        if "power" in raw_input and "pv_gen_kw" not in raw_input:
            # Convert power in Watts to kW
            processed["pv_gen_kw"] = raw_input["power"] / 1000.0

        if "voltage" in raw_input:
            processed["voltage_v"] = raw_input["voltage"]

        if "current" in raw_input:
            processed["current_a"] = raw_input["current"]

        # Add time features
        if "timestamp" in raw_input:
            dt = datetime.fromtimestamp(raw_input["timestamp"])
            processed["hour_of_day"] = float(dt.hour)
        else:
            processed["hour_of_day"] = float(datetime.now().hour)

        # Calculate derived features
        if calculate_derived:
            processed = self._calculate_derived(processed)

        # Clip unrealistic values
        processed = self._clip_values(processed)

        # Validate
        validation_result = self._validate(processed)
        if not validation_result["valid"]:
            logger.warning(f"Preprocessing validation issues: {validation_result['issues']}")

        # Normalize
        if normalize:
            processed = self._normalize(processed)

        return processed

    def to_observation_vector(
        self, processed: Dict[str, Any], obs_dim: int = 18
    ) -> np.ndarray:
        """
        Convert processed data to observation vector for model.

        Args:
            processed: Preprocessed feature dictionary
            obs_dim: Expected observation dimension

        Returns:
            numpy array of shape (obs_dim,)
        """
        obs = []

        for feature_name in self.feature_order[:obs_dim]:
            value = processed.get(feature_name, self.defaults.get(feature_name, 0.0))
            obs.append(float(value))

        # Pad to obs_dim if needed
        while len(obs) < obs_dim:
            obs.append(0.0)

        return np.array(obs[:obs_dim], dtype=np.float32)

    def _get_field_mapping(self) -> Dict[str, str]:
        """Get mapping from input field names to feature names."""
        return {
            # Direct mappings
            "soc": "soc_kwh",
            "soc_kwh": "soc_kwh",
            "soc_capacity": "soc_capacity_kwh",
            "soc_capacity_kwh": "soc_capacity_kwh",
            "solar": "pv_gen_kw",
            "pv_gen_kw": "pv_gen_kw",
            "load": "load_kw",
            "load_kw": "load_kw",
            "net_kw": "net_kw",
            "battery_power_kw": "battery_power_kw",
            "price": "price_signal",
            "price_signal": "price_signal",
            "grid_price": "price_signal",
            "forecast_irradiance_1h": "forecast_irradiance_1h",
            "forecast_irradiance_3h": "forecast_irradiance_3h",
            "forecast_temp_1h": "forecast_temp_1h",
            "actual_irradiance_wm2": "actual_irradiance_wm2",
            "irradiance": "actual_irradiance_wm2",
            "voltage_v": "voltage_v",
            "voltage": "voltage_v",
            "current_a": "current_a",
            "current": "current_a",
            "volatility": "volatility",
            "sensor_health": "sensor_health",
            "grid_risk": "grid_risk",
            "anomaly_score": "anomaly_score",
        }

    def _calculate_derived(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate derived features."""
        # Net power
        if data.get("net_kw", 0) == 0:
            data["net_kw"] = data.get("pv_gen_kw", 0) - data.get("load_kw", 0)

        # SoC percentage (for internal use)
        soc_kwh = data.get("soc_kwh", 2.0)
        soc_cap = data.get("soc_capacity_kwh", 4.0)
        data["soc_percent"] = (soc_kwh / soc_cap * 100) if soc_cap > 0 else 50.0

        return data

    def _clip_values(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Clip values to realistic ranges."""
        for feature_name, stats in self.norm_stats.items():
            if feature_name in data:
                data[feature_name] = np.clip(
                    data[feature_name],
                    stats["min"],
                    stats["max"]
                )
        return data

    def _validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate preprocessed data."""
        issues = []

        # Check for NaN/Inf
        for key, value in data.items():
            if isinstance(value, (int, float)):
                if np.isnan(value) or np.isinf(value):
                    issues.append(f"{key} is NaN or Inf")

        # Check SoC bounds
        soc_percent = data.get("soc_percent", 50)
        if soc_percent < 0 or soc_percent > 100:
            issues.append(f"soc_percent out of range: {soc_percent}")

        return {"valid": len(issues) == 0, "issues": issues}

    def _normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize values using z-score normalization."""
        normalized = {}

        for feature_name, value in data.items():
            if feature_name in self.norm_stats:
                stats = self.norm_stats[feature_name]
                mean = stats["mean"]
                std = stats["std"] if stats["std"] > 0 else 1.0
                normalized[feature_name] = (value - mean) / std
            else:
                normalized[feature_name] = value

        return normalized


# Global preprocessor instance
preprocessor = Preprocessor()
