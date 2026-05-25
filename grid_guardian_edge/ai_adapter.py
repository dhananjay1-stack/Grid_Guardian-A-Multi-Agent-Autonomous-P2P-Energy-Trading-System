"""
Grid-Guardian Edge - AI Adapter
Local model inference with condition-based routing and backend fallback
"""

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import requests

# Check onnxruntime availability once at import time
try:
    import onnxruntime as ort
    ONNX_AVAILABLE = True
except ImportError:
    ONNX_AVAILABLE = False

from config import (
    BACKEND_URL,
    BACKEND_TIMEOUT,
    BACKEND_RETRY_ATTEMPTS,
    BACKEND_RETRY_DELAY,
    NODE_ID,
    API_AI_DECIDE,
)

# Shorter timeout/retries for AI backend calls to avoid blocking the main loop
AI_BACKEND_TIMEOUT = min(BACKEND_TIMEOUT, 3)
AI_BACKEND_RETRIES = min(BACKEND_RETRY_ATTEMPTS, 2)

logger = logging.getLogger(__name__)


# Discrete action mapping (matches training environment)
DISCRETE_ACTIONS = {
    0: ("charge_small", +1.0, "ON"),
    1: ("charge_large", +3.0, "ON"),
    2: ("idle", 0.0, "HOLD"),
    3: ("discharge_small", -1.0, "ON"),
    4: ("discharge_large", -3.0, "ON"),
    5: ("offer_sell", -1.5, "ON"),
    6: ("offer_hold", 0.0, "HOLD"),
}


class AiAdapter:
    """
    AI adapter with local model inference and condition-based routing.

    Supports:
    - Local TorchScript/ONNX model inference
    - Condition-to-model routing
    - Input normalization
    - Backend API fallback
    - Safe fallback rules when all else fails

    Decisions:
    - ON: Turn relay/load ON
    - OFF: Turn relay/load OFF
    - HOLD: Maintain current state

    Extended decisions include:
    - action_kw: Power amount in kW
    - confidence: Decision confidence score
    - action_name: Human-readable action name
    """

    # Observation feature order (must match training)
    OBS_KEYS = [
        "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
        "battery_power_kw", "price_signal", "forecast_irradiance_1h",
        "forecast_irradiance_3h", "forecast_temp_1h", "actual_irradiance_wm2",
        "voltage_v", "current_a", "sin_hour", "cos_hour", "sin_day",
        "cos_day", "neighbor_balance"
    ]

    # Default decision when AI is unreachable
    DEFAULT_DECISION = {
        "decision": "HOLD",
        "confidence": 0.5,
        "action_kw": 0.0,
        "action_name": "idle",
        "action_index": 2,
        "trade_action": None,
        "recommended_quantity": 0.0,
        "is_mock": True,
        "source": "fallback",
    }

    def __init__(self, models_dir: Optional[str] = None):
        """
        Initialize AI adapter.

        Args:
            models_dir: Path to models directory (default: ./models)
        """
        self.models_dir = Path(models_dir or os.path.join(os.path.dirname(__file__), "models"))
        self.routing_config: Dict = {}
        self.loaded_models: Dict = {}
        self.norm_params: Dict = {}
        self.current_model_key: Optional[str] = None
        self.runtime_type: str = "onnx"  # Prefer ONNX on Pi

        # Backend fallback
        self.backend_endpoint = f"{BACKEND_URL}{API_AI_DECIDE}/{NODE_ID}"
        self.use_backend_fallback = True

        # Cache
        self.last_decision: Optional[Dict] = None
        self.last_decision_time: float = 0
        self.decision_cache_ttl: float = 30

        # Statistics
        self.stats = {
            "requests": 0,
            "local_inferences": 0,
            "backend_calls": 0,
            "fallback_rules": 0,
            "model_load_errors": 0,
            "inference_errors": 0,
            "successes": 0,
            "failures": 0,
            "timeouts": 0,
        }

        # Track health
        self.local_model_healthy = False
        self.ai_healthy = True
        self.consecutive_failures = 0
        self.max_consecutive_failures = 5

        # Load configuration and models
        self._load_routing_config()
        self._load_default_model()

    def _load_routing_config(self):
        """Load model routing configuration"""
        config_path = self.models_dir / "model_routing.json"
        try:
            if config_path.exists():
                with open(config_path, "r") as f:
                    self.routing_config = json.load(f)
                    self.runtime_type = self.routing_config.get("default_runtime", "onnx")
                    logger.info(f"Loaded routing config: {len(self.routing_config.get('models', {}))} models")
            else:
                logger.warning(f"Routing config not found at {config_path}")
                self.routing_config = {"default_model": "cql_policy", "models": {}}
        except Exception as e:
            logger.error(f"Error loading routing config: {e}")
            self.routing_config = {}

    def _load_default_model(self):
        """Load the default model"""
        if self.runtime_type == "onnx" and not ONNX_AVAILABLE:
            logger.warning("onnxruntime not installed — local ONNX inference disabled. Using fallback rules.")
            return
        default_model = self.routing_config.get("default_model", "cql_policy")
        if default_model:
            self._load_model(default_model)

    def _load_model(self, model_key: str) -> bool:
        """
        Load a model by key.

        Args:
            model_key: Model key from routing config

        Returns:
            True if model loaded successfully
        """
        if model_key in self.loaded_models:
            self.current_model_key = model_key
            return True

        models_config = self.routing_config.get("models", {})
        if model_key not in models_config:
            logger.warning(f"Model '{model_key}' not found in routing config")
            return False

        model_info = models_config[model_key]

        # Determine model file based on runtime preference
        if self.runtime_type == "onnx":
            model_file = model_info.get("onnx")
        else:
            model_file = model_info.get("torchscript")

        if not model_file:
            logger.warning(f"No {self.runtime_type} file for model '{model_key}'")
            return False

        model_path = self.models_dir / model_file

        try:
            if self.runtime_type == "onnx":
                model, mtype = self._load_onnx(str(model_path))
            else:
                model, mtype = self._load_torchscript(str(model_path))

            self.loaded_models[model_key] = {"model": model, "type": mtype, "info": model_info}
            self.current_model_key = model_key
            self.local_model_healthy = True

            # Load normalization parameters
            norm_file = model_info.get("norm_params", "norm_params.npz")
            self._load_norm_params(model_key, norm_file)

            logger.info(f"Loaded model '{model_key}' ({mtype}) from {model_path}")
            return True

        except Exception as e:
            logger.error(f"Error loading model '{model_key}': {e}")
            self.stats["model_load_errors"] += 1
            return False

    def _load_torchscript(self, path: str) -> Tuple[Any, str]:
        """Load TorchScript model"""
        import torch
        model = torch.jit.load(path, map_location="cpu")
        model.eval()
        return model, "torchscript"

    def _load_onnx(self, path: str) -> Tuple[Any, str]:
        """Load ONNX model"""
        if not ONNX_AVAILABLE:
            raise ImportError("onnxruntime is not installed")
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        return sess, "onnx"

    def _load_norm_params(self, model_key: str, norm_file: str):
        """Load normalization parameters for a model"""
        norm_path = self.models_dir / norm_file
        try:
            if norm_path.exists():
                data = np.load(str(norm_path))
                self.norm_params[model_key] = {
                    "means": data["means"].astype(np.float32),
                    "stds": np.clip(data["stds"].astype(np.float32), 1e-8, None),
                }
                logger.debug(f"Loaded norm params for '{model_key}'")
        except Exception as e:
            logger.warning(f"Could not load norm params: {e}")

    def select_model_for_condition(self, condition: str) -> str:
        """
        Select the appropriate model for the given condition.

        Args:
            condition: Operating condition string

        Returns:
            Model key to use
        """
        conditions = self.routing_config.get("conditions", {})
        if condition in conditions:
            model_key = conditions[condition].get("model")
            if model_key:
                # Load model if not already loaded (skip if onnx unavailable)
                if model_key not in self.loaded_models:
                    if self.runtime_type == "onnx" and not ONNX_AVAILABLE:
                        pass  # Can't load, will fall through to fallback rules
                    else:
                        self._load_model(model_key)
                return model_key

        # Fall back to default model
        return self.routing_config.get("default_model", "cql_policy")

    def _build_observation(self, sensor_data: Dict[str, Any]) -> np.ndarray:
        """
        Build observation vector from sensor data.

        Args:
            sensor_data: Sensor reading dictionary

        Returns:
            Numpy array of shape (obs_dim,)
        """
        # Extract time features
        ts = sensor_data.get("timestamp", time.time())
        hour = (ts % 86400) / 3600
        day = (ts % (86400 * 7)) / 86400

        sin_hour = math.sin(2 * math.pi * hour / 24)
        cos_hour = math.cos(2 * math.pi * hour / 24)
        sin_day = math.sin(2 * math.pi * day / 7)
        cos_day = math.cos(2 * math.pi * day / 7)

        # Build feature vector
        voltage = sensor_data.get("voltage", 230)
        current = sensor_data.get("current", 0)
        power = sensor_data.get("power", 0)
        load_kw = power / 1000 if power > 0 else sensor_data.get("load_kw", 0.5)

        obs = np.array([
            sensor_data.get("soc_kwh", 2.0),
            sensor_data.get("soc_capacity_kwh", 4.0),
            sensor_data.get("pv_gen_kw", 0.0),
            load_kw,
            sensor_data.get("net_kw", 0.0),
            sensor_data.get("battery_power_kw", 0.0),
            sensor_data.get("price_signal", 5.0),
            sensor_data.get("forecast_irradiance_1h", 300),
            sensor_data.get("forecast_irradiance_3h", 300),
            sensor_data.get("forecast_temp_1h", 25),
            sensor_data.get("actual_irradiance_wm2", 400),
            voltage,
            current,
            sin_hour,
            cos_hour,
            sin_day,
            cos_day,
            sensor_data.get("neighbor_balance", 0.0),
        ], dtype=np.float32)

        return obs

    def _normalize(self, obs: np.ndarray, model_key: str) -> np.ndarray:
        """Apply normalization to observation"""
        if model_key in self.norm_params:
            params = self.norm_params[model_key]
            return (obs - params["means"]) / params["stds"]
        return obs

    def _run_inference(self, obs: np.ndarray, model_key: str) -> Dict[str, Any]:
        """
        Run model inference.

        Args:
            obs: Normalized observation array
            model_key: Model to use

        Returns:
            Inference result dictionary
        """
        if model_key not in self.loaded_models:
            raise ValueError(f"Model '{model_key}' not loaded")

        model_data = self.loaded_models[model_key]
        model = model_data["model"]
        mtype = model_data["type"]

        if mtype == "torchscript":
            import torch
            with torch.no_grad():
                t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
                logits = model(t).squeeze(0).numpy()
        else:  # onnx
            logits = model.run(None, {"observation": obs.reshape(1, -1)})[0].squeeze(0)

        action_idx = int(np.argmax(logits))
        name, kw, relay_decision = DISCRETE_ACTIONS.get(action_idx, ("idle", 0.0, "HOLD"))

        # Calculate confidence from softmax
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)
        confidence = float(probs[action_idx])

        return {
            "action_index": action_idx,
            "action_name": name,
            "action_kw": kw,
            "decision": relay_decision,
            "confidence": confidence,
            "logits": logits.tolist(),
            "model_key": model_key,
            "source": "local_inference",
            "timestamp": time.time(),
        }

    def get_decision(
        self,
        telemetry_data: Dict[str, Any],
        condition: str = "normal"
    ) -> Dict[str, Any]:
        """
        Get AI decision for current state.

        Pipeline:
        1. Try local model inference
        2. Fall back to backend API if local fails
        3. Fall back to rules if both fail

        Args:
            telemetry_data: Current sensor readings
            condition: Operating condition

        Returns:
            Decision dictionary with 'decision' field (ON/OFF/HOLD)
        """
        self.stats["requests"] += 1

        # Check cache
        if self._should_use_cache():
            logger.debug("Using cached AI decision")
            return self.last_decision

        # Determine model to use
        model_key = self.select_model_for_condition(condition)

        # Try local inference first
        if self.local_model_healthy and model_key in self.loaded_models:
            try:
                obs = self._build_observation(telemetry_data)
                # Note: Model has baked-in normalization, skip manual norm
                # If using raw model: obs = self._normalize(obs, model_key)

                result = self._run_inference(obs, model_key)
                result["condition"] = condition
                self.stats["local_inferences"] += 1
                self.stats["successes"] += 1
                self.consecutive_failures = 0

                # Cache and return
                self.last_decision = result
                self.last_decision_time = time.time()

                logger.debug(
                    f"Local inference: {result['decision']} "
                    f"({result['action_name']}, confidence: {result['confidence']:.2f})"
                )
                return result

            except Exception as e:
                logger.error(f"Local inference error: {e}")
                self.stats["inference_errors"] += 1
                self.local_model_healthy = False

        # Try backend API fallback (skip if already failed too many times)
        if self.use_backend_fallback and self.consecutive_failures < self.max_consecutive_failures:
            try:
                result = self._call_backend(telemetry_data)
                if result:
                    result["condition"] = condition
                    result["source"] = "backend"
                    self.stats["backend_calls"] += 1
                    self.stats["successes"] += 1
                    self.consecutive_failures = 0
                    self.ai_healthy = True
                    self.last_decision = result
                    self.last_decision_time = time.time()
                    return result
            except Exception as e:
                logger.warning(f"Backend fallback failed: {e}")
                self.stats["failures"] += 1
                self.consecutive_failures += 1
                if self.consecutive_failures >= self.max_consecutive_failures:
                    logger.warning("Backend unreachable after repeated failures — using rule-based fallback only")
                    self.ai_healthy = False

        # Final fallback to rules
        return self._get_fallback_decision(telemetry_data, condition)

    def _call_backend(self, telemetry_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Call backend AI API"""
        payload = {
            "node_id": NODE_ID,
            "telemetry": {
                "voltage": telemetry_data.get("voltage", 0),
                "current": telemetry_data.get("current", 0),
                "power": telemetry_data.get("power", 0),
                "energy": telemetry_data.get("energy", 0),
                "frequency": telemetry_data.get("frequency", 50),
                "power_factor": telemetry_data.get("power_factor", 1.0),
                "relay_state": telemetry_data.get("relay_state", False),
                "timestamp": telemetry_data.get("timestamp", time.time()),
            },
        }

        for attempt in range(AI_BACKEND_RETRIES):
            try:
                response = requests.post(
                    self.backend_endpoint,
                    json=payload,
                    timeout=AI_BACKEND_TIMEOUT,
                    headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                data = response.json().get("data", response.json())
                return {
                    "decision": data.get("decision", "HOLD"),
                    "confidence": data.get("confidence", 0.5),
                    "action_kw": data.get("action_kw", 0.0),
                    "action_name": data.get("action_name", "idle"),
                    "action_index": data.get("action_index", 2),
                    "is_mock": data.get("is_mock", False),
                    "model_version": data.get("model_version", "unknown"),
                }
            except requests.exceptions.Timeout:
                logger.warning(f"Backend timeout (attempt {attempt + 1}/{AI_BACKEND_RETRIES})")
                self.stats["timeouts"] += 1
            except Exception as e:
                logger.debug(f"Backend attempt {attempt + 1} failed: {e}")
                if attempt < AI_BACKEND_RETRIES - 1:
                    time.sleep(min(BACKEND_RETRY_DELAY, 1.0))

        return None

    def _get_fallback_decision(
        self,
        telemetry_data: Dict[str, Any],
        condition: str = "normal"
    ) -> Dict[str, Any]:
        """Generate fallback decision based on simple rules"""
        self.stats["fallback_rules"] += 1

        voltage = telemetry_data.get("voltage", 230)
        power = telemetry_data.get("power", 0)

        decision = "HOLD"
        action_name = "idle"
        action_kw = 0.0

        # Safety checks first
        if voltage > 250 or voltage < 190:
            decision = "OFF"
            action_name = "voltage_protection"
        elif power > 5000:
            decision = "OFF"
            action_name = "overload_protection"
        elif condition == "fault":
            decision = "OFF"
            action_name = "fault_protection"
        elif condition == "low_soc":
            decision = "HOLD"
            action_name = "soc_protection"
        elif condition == "peak_price":
            decision = "ON"
            action_name = "peak_export"
            action_kw = -1.0
        elif condition == "off_peak":
            decision = "ON"
            action_name = "charge_opportunity"
            action_kw = 1.0

        fallback = {
            **self.DEFAULT_DECISION,
            "decision": decision,
            "action_name": action_name,
            "action_kw": action_kw,
            "condition": condition,
            "source": "fallback_rules",
            "timestamp": time.time(),
        }

        logger.debug(f"Fallback decision: {decision} ({action_name})")
        return fallback

    def _should_use_cache(self) -> bool:
        """Check if cached decision should be used"""
        if self.last_decision is None:
            return False
        age = time.time() - self.last_decision_time
        return age < self.decision_cache_ttl

    def get_latest_decision(self) -> Optional[Dict[str, Any]]:
        """Get the most recent decision (from cache)"""
        return self.last_decision

    def check_ai_health(self) -> bool:
        """Check if AI (local or backend) is healthy"""
        if self.local_model_healthy:
            return True
        try:
            response = requests.get(f"{BACKEND_URL}/api/ai/status", timeout=5)
            if response.status_code == 200:
                data = response.json()
                healthy = data.get("data", {}).get("healthy", False)
                self.ai_healthy = healthy
                return healthy
        except Exception:
            pass
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Get AI adapter statistics"""
        return {
            **self.stats,
            "local_model_healthy": self.local_model_healthy,
            "ai_healthy": self.ai_healthy,
            "consecutive_failures": self.consecutive_failures,
            "loaded_models": list(self.loaded_models.keys()),
            "current_model": self.current_model_key,
            "runtime_type": self.runtime_type,
            "last_decision_age": time.time() - self.last_decision_time if self.last_decision else None,
        }

    def get_model_info(self, model_key: Optional[str] = None) -> Dict[str, Any]:
        """Get information about loaded model"""
        key = model_key or self.current_model_key
        if key and key in self.loaded_models:
            return {
                "model_key": key,
                "type": self.loaded_models[key]["type"],
                "info": self.loaded_models[key]["info"],
            }
        return {}

    def invalidate_cache(self):
        """Force refresh on next decision request"""
        self.last_decision_time = 0

    def reload_models(self):
        """Reload all models"""
        self.loaded_models.clear()
        self.norm_params.clear()
        self._load_routing_config()
        self._load_default_model()
        logger.info("Models reloaded")
