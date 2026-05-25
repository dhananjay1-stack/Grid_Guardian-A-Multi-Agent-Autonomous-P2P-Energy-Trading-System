"""Preprocessing pipeline for telemetry data."""
from __future__ import annotations

from typing import Dict, Any, Optional, Tuple, List
from dataclasses import dataclass
import numpy as np

from .config import settings, OBS_KEYS
from .logger import get_logger


logger = get_logger("preprocessing")


# Default observation values
DEFAULT_OBS: Dict[str, float] = {
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
    "grid_risk": 0.05,
    "anomaly_score": 0.0,
    "stress_test_mode": 0.0,
}

# Valid ranges for clipping
VALUE_RANGES: Dict[str, Tuple[float, float]] = {
    "soc_kwh": (0.0, 20.0),
    "soc_capacity_kwh": (0.1, 50.0),
    "pv_gen_kw": (0.0, 15.0),
    "load_kw": (0.0, 20.0),
    "net_kw": (-20.0, 20.0),
    "battery_power_kw": (-5.0, 5.0),
    "price_signal": (0.0, 1.0),
    "forecast_irradiance_1h": (0.0, 1200.0),
    "forecast_irradiance_3h": (0.0, 1200.0),
    "forecast_temp_1h": (-20.0, 50.0),
    "actual_irradiance_wm2": (0.0, 1200.0),
    "voltage_v": (0.0, 500.0),
    "current_a": (0.0, 50.0),
    "volatility": (0.0, 1.0),
    "sensor_health": (0.0, 1.0),
    "grid_risk": (0.0, 1.0),
    "anomaly_score": (0.0, 1.0),
    "stress_test_mode": (0.0, 1.0),
}


@dataclass
class PreprocessingResult:
    """Result of preprocessing pipeline."""
    observation: np.ndarray
    obs_dict: Dict[str, float]
    derived_features: Dict[str, float]
    warnings: List[str]
    valid: bool


class TelemetryPreprocessor:
    """
    Preprocesses telemetry data for model inference.

    Handles:
    - Schema validation
    - Missing field imputation
    - Value clipping
    - Derived feature calculation
    - Normalization
    """

    def __init__(self, norm_params_path: Optional[str] = None):
        self.norm_means: Optional[np.ndarray] = None
        self.norm_stds: Optional[np.ndarray] = None

        if norm_params_path:
            self.load_norm_params(norm_params_path)

    def load_norm_params(self, path: str):
        """Load normalization parameters from file."""
        try:
            data = np.load(path)
            self.norm_means = data["means"].astype(np.float32)
            # Prevent division by zero
            self.norm_stds = np.clip(data["stds"].astype(np.float32), 1e-8, None)
            logger.info(f"Loaded normalization params from {path}")
        except Exception as e:
            logger.warning(f"Could not load norm params: {e}")

    def preprocess(self, telemetry: Dict[str, Any],
                   context: Optional[Dict[str, Any]] = None,
                   normalize: bool = True) -> PreprocessingResult:
        """
        Preprocess telemetry data into model-ready observation.

        Parameters
        ----------
        telemetry : dict
            Raw telemetry data
        context : dict, optional
            Additional context data
        normalize : bool
            Whether to apply normalization

        Returns
        -------
        PreprocessingResult
            Processed observation and metadata
        """
        context = context or {}
        warnings: List[str] = []
        derived: Dict[str, float] = {}

        # Build observation dictionary with fallbacks
        obs_dict = self._build_obs_dict(telemetry, context, warnings)

        # Calculate derived features
        derived = self._calculate_derived(obs_dict)
        obs_dict.update(derived)

        # Clip values to valid ranges
        obs_dict = self._clip_values(obs_dict, warnings)

        # Build observation vector
        obs = self._build_obs_vector(obs_dict)

        # Apply normalization
        if normalize and self.norm_means is not None:
            obs = self._normalize(obs)

        valid = len(warnings) == 0 or all("imputed" not in w.lower() for w in warnings)

        return PreprocessingResult(
            observation=obs,
            obs_dict=obs_dict,
            derived_features=derived,
            warnings=warnings,
            valid=valid
        )

    def _build_obs_dict(self, telemetry: Dict[str, Any],
                        context: Dict[str, Any],
                        warnings: List[str]) -> Dict[str, float]:
        """Build observation dictionary from telemetry and context."""
        obs_dict: Dict[str, float] = {}

        # Field mappings for common alternative names
        field_mappings = {
            "soc": "soc_kwh",
            "solar": "pv_gen_kw",
            "power": "pv_gen_kw",
            "load": "load_kw",
            "price": "price_signal",
            "voltage": "voltage_v",
            "current": "current_a",
        }

        for key in OBS_KEYS:
            value = None

            # Try direct lookup
            if key in telemetry:
                value = telemetry[key]
            elif key in context:
                value = context[key]
            else:
                # Try mapped names
                for alt_name, mapped_key in field_mappings.items():
                    if mapped_key == key and alt_name in telemetry:
                        value = telemetry[alt_name]
                        # Handle power in watts -> kW
                        if alt_name == "power" and value is not None:
                            value = value / 1000.0
                        break

            # Use default if still missing
            if value is None:
                value = DEFAULT_OBS.get(key, 0.0)
                if key not in ["battery_power_kw", "net_kw", "stress_test_mode"]:
                    warnings.append(f"Field {key} imputed with default {value}")

            obs_dict[key] = float(value) if value is not None else 0.0

        return obs_dict

    def _calculate_derived(self, obs_dict: Dict[str, float]) -> Dict[str, float]:
        """Calculate derived features."""
        derived: Dict[str, float] = {}

        pv = obs_dict.get("pv_gen_kw", 0.0)
        load = obs_dict.get("load_kw", 0.0)

        # Net power (surplus/deficit)
        if obs_dict.get("net_kw", 0.0) == 0.0 or obs_dict.get("net_kw") == DEFAULT_OBS["net_kw"]:
            derived["net_kw"] = pv - load

        # Surplus indicator
        derived["has_surplus"] = float(pv > load)

        # SoC fraction
        soc = obs_dict.get("soc_kwh", 2.0)
        soc_cap = obs_dict.get("soc_capacity_kwh", 4.0)
        derived["soc_fraction"] = soc / max(soc_cap, 0.1)

        return derived

    def _clip_values(self, obs_dict: Dict[str, float],
                     warnings: List[str]) -> Dict[str, float]:
        """Clip values to valid ranges."""
        clipped = obs_dict.copy()

        for key, value in obs_dict.items():
            if key in VALUE_RANGES:
                low, high = VALUE_RANGES[key]
                if value < low or value > high:
                    clipped[key] = np.clip(value, low, high)
                    warnings.append(f"Field {key} clipped: {value} -> {clipped[key]}")

        return clipped

    def _build_obs_vector(self, obs_dict: Dict[str, float]) -> np.ndarray:
        """Build observation vector from dictionary."""
        obs = []
        for key in OBS_KEYS:
            obs.append(float(obs_dict.get(key, DEFAULT_OBS.get(key, 0.0))))

        # Pad to expected dimension
        while len(obs) < settings.obs_dim:
            obs.append(0.0)

        return np.array(obs[:settings.obs_dim], dtype=np.float32)

    def _normalize(self, obs: np.ndarray) -> np.ndarray:
        """Apply z-score normalization."""
        if self.norm_means is None or self.norm_stds is None:
            return obs

        n = min(len(obs), len(self.norm_means))
        obs_normalized = obs.copy()
        obs_normalized[:n] = (obs[:n] - self.norm_means[:n]) / self.norm_stds[:n]
        return obs_normalized


# Global instance
preprocessor = TelemetryPreprocessor()
