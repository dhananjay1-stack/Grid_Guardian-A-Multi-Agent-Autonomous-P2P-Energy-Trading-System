#!/usr/bin/env python3
"""
Grid-Guardian AI Inference Server

REST API server for AI inference, providing:
- /health - Server health check
- /infer - Run model inference on telemetry
- /model-info - Get model metadata
- /condition - Get current condition detection
- /selection-stats - Get policy selection statistics

Features:
- Dynamic model selection based on conditions (BC/CQL/DT)
- Condition detection and volatility tracking
- Safety clipping on actions

Designed to integrate with Web backend (ai.service.js)
Default port: 5050
"""

import argparse
import json
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    from flask import Flask, jsonify, request
    from flask_cors import CORS
except ImportError:
    print("Please install Flask and flask-cors: pip install flask flask-cors")
    sys.exit(1)

# Import condition detector and policy selector
try:
    from condition_detector import ConditionDetector, get_condition_detector
    from policy_selector import PolicySelector, get_policy_selector
    SELECTOR_AVAILABLE = True
except ImportError:
    SELECTOR_AVAILABLE = False
    logger.warning("PolicySelector not available - using single model mode")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Global model state
model_state = {
    "model": None,
    "model_type": None,
    "model_loaded": False,
    "model_path": None,
    "norm_params": None,
    "model_info": None,
    "policy_selector": None,
    "use_dynamic_selection": False,
}


# Action mapping (matches edge/training)
DISCRETE_ACTIONS = {
    0: {"name": "charge_small", "kw": 1.0, "decision": "CHARGE", "trade": None},
    1: {"name": "charge_large", "kw": 3.0, "decision": "CHARGE", "trade": None},
    2: {"name": "idle", "kw": 0.0, "decision": "HOLD", "trade": None},
    3: {"name": "discharge_small", "kw": -1.0, "decision": "DISCHARGE", "trade": None},
    4: {"name": "discharge_large", "kw": -3.0, "decision": "DISCHARGE", "trade": None},
    5: {"name": "offer_sell", "kw": -1.5, "decision": "SELL", "trade": "SELL"},
    6: {"name": "buy_energy", "kw": 1.5, "decision": "BUY", "trade": "BUY"},
}

# Observation feature names (18 features)
OBS_KEYS = [
    "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
    "battery_power_kw", "price_signal", "forecast_irradiance_1h",
    "forecast_irradiance_3h", "forecast_temp_1h", "actual_irradiance_wm2",
    "voltage_v", "current_a", "sin_hour", "cos_hour", "sin_day",
    "cos_day", "neighbor_balance"
]


def load_torchscript_model(path: str):
    """Load TorchScript model"""
    import torch
    model = torch.jit.load(path, map_location="cpu")
    model.eval()
    return model, "torchscript"


def load_onnx_model(path: str):
    """Load ONNX model"""
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    return sess, "onnx"


def load_pytorch_checkpoint(path: str):
    """Load PyTorch checkpoint and extract policy"""
    import torch

    checkpoint = torch.load(path, map_location="cpu")

    # Try to extract model state
    if "model_state_dict" in checkpoint:
        state_dict = checkpoint["model_state_dict"]
    elif "state_dict" in checkpoint:
        state_dict = checkpoint["state_dict"]
    else:
        state_dict = checkpoint

    # Build simple MLP policy network
    class SimpleMLP(torch.nn.Module):
        def __init__(self, obs_dim=18, act_dim=7, hidden_dims=[256, 256]):
            super().__init__()
            layers = []
            prev_dim = obs_dim
            for h in hidden_dims:
                layers.append(torch.nn.Linear(prev_dim, h))
                layers.append(torch.nn.ReLU())
                prev_dim = h
            layers.append(torch.nn.Linear(prev_dim, act_dim))
            self.net = torch.nn.Sequential(*layers)

        def forward(self, x):
            return self.net(x)

    # Try to infer dimensions from state dict
    obs_dim = 18
    act_dim = 7

    # Look for input/output dimensions in state dict keys
    for key in state_dict.keys():
        if "0.weight" in key or "net.0.weight" in key:
            obs_dim = state_dict[key].shape[1]
            break

    for key in reversed(list(state_dict.keys())):
        if "weight" in key:
            act_dim = state_dict[key].shape[0]
            break

    model = SimpleMLP(obs_dim=obs_dim, act_dim=act_dim)

    # Try to load state dict (may need to filter keys)
    try:
        # Filter to just the network weights
        filtered_state = {}
        for k, v in state_dict.items():
            if "policy" in k or "net" in k or k.startswith("0.") or k.startswith("1."):
                new_key = k.replace("policy.", "").replace("net.", "")
                if new_key.startswith("net."):
                    new_key = new_key[4:]
                filtered_state[new_key] = v

        if filtered_state:
            model.load_state_dict(filtered_state, strict=False)
        else:
            # Try loading directly
            model.load_state_dict(state_dict, strict=False)
    except Exception as e:
        logger.warning(f"Could not load exact state dict: {e}")
        logger.info("Using randomly initialized model as fallback")

    model.eval()
    return model, "pytorch"


def load_norm_params(path: str) -> Optional[Dict]:
    """Load normalization parameters"""
    try:
        if Path(path).exists():
            data = np.load(path)
            return {
                "means": data["means"].astype(np.float32),
                "stds": np.clip(data["stds"].astype(np.float32), 1e-8, None),
            }
    except Exception as e:
        logger.warning(f"Could not load norm params from {path}: {e}")
    return None


def load_model(model_path: str, norm_path: Optional[str] = None):
    """Load model from path"""
    global model_state

    path = Path(model_path)

    if not path.exists():
        raise FileNotFoundError(f"Model not found: {model_path}")

    # Determine model type and load
    if str(path).endswith(".onnx"):
        model, model_type = load_onnx_model(str(path))
    elif str(path).endswith(".torchscript"):
        model, model_type = load_torchscript_model(str(path))
    elif str(path).endswith(".pt"):
        model, model_type = load_pytorch_checkpoint(str(path))
    else:
        raise ValueError(f"Unknown model format: {path.suffix}")

    model_state["model"] = model
    model_state["model_type"] = model_type
    model_state["model_loaded"] = True
    model_state["model_path"] = str(path)

    # Load normalization parameters
    if norm_path:
        model_state["norm_params"] = load_norm_params(norm_path)
    else:
        # Try to find norm_params.npz in same directory
        norm_file = path.parent / "norm_params.npz"
        if norm_file.exists():
            model_state["norm_params"] = load_norm_params(str(norm_file))

    # Model info
    model_state["model_info"] = {
        "name": "GridGuardian-CQL",
        "version": "1.0.0",
        "path": str(path),
        "type": model_type,
        "obs_dim": 18,
        "act_dim": 7,
        "has_norm_params": model_state["norm_params"] is not None,
    }

    logger.info(f"Model loaded: {path} ({model_type})")
    return True


def build_observation(telemetry: Dict[str, Any]) -> np.ndarray:
    """Build observation vector from telemetry data"""
    # Extract time features
    ts = telemetry.get("timestamp", time.time())
    if isinstance(ts, (int, float)) and ts > 1e10:
        ts = ts / 1000  # Convert ms to seconds

    hour = (ts % 86400) / 3600
    day = (ts % (86400 * 7)) / 86400

    sin_hour = math.sin(2 * math.pi * hour / 24)
    cos_hour = math.cos(2 * math.pi * hour / 24)
    sin_day = math.sin(2 * math.pi * day / 7)
    cos_day = math.cos(2 * math.pi * day / 7)

    # Extract values with defaults
    voltage = telemetry.get("voltage", telemetry.get("voltage_v", 230))
    current = telemetry.get("current", telemetry.get("current_a", 0))
    power = telemetry.get("power", 0)

    load_kw = telemetry.get("load_kw", power / 1000 if power > 0 else 0.5)
    pv_gen = telemetry.get("pv_gen_kw", 0)

    obs = np.array([
        telemetry.get("soc_kwh", 2.0),
        telemetry.get("soc_capacity_kwh", 4.0),
        pv_gen,
        load_kw,
        telemetry.get("net_kw", pv_gen - load_kw),
        telemetry.get("battery_power_kw", 0.0),
        telemetry.get("price_signal", 5.0),
        telemetry.get("forecast_irradiance_1h", 300),
        telemetry.get("forecast_irradiance_3h", 300),
        telemetry.get("forecast_temp_1h", 25),
        telemetry.get("actual_irradiance_wm2", 400),
        voltage,
        current,
        sin_hour,
        cos_hour,
        sin_day,
        cos_day,
        telemetry.get("neighbor_balance", 0.0),
    ], dtype=np.float32)

    return obs


def normalize_observation(obs: np.ndarray) -> np.ndarray:
    """Apply normalization if available"""
    if model_state["norm_params"]:
        means = model_state["norm_params"]["means"]
        stds = model_state["norm_params"]["stds"]
        return (obs - means) / stds
    return obs


def run_inference(obs: np.ndarray) -> Dict[str, Any]:
    """Run model inference"""
    if not model_state["model_loaded"]:
        raise RuntimeError("Model not loaded")

    model = model_state["model"]
    model_type = model_state["model_type"]

    start_time = time.time()

    if model_type == "torchscript":
        import torch
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits = model(t).squeeze(0).numpy()
    elif model_type == "pytorch":
        import torch
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits = model(t).squeeze(0).detach().numpy()
    else:  # onnx
        logits = model.run(None, {"observation": obs.reshape(1, -1)})[0].squeeze(0)

    inference_time = (time.time() - start_time) * 1000

    # Get action from logits
    action_idx = int(np.argmax(logits))
    action_info = DISCRETE_ACTIONS.get(action_idx, DISCRETE_ACTIONS[2])

    # Calculate confidence from softmax
    exp_logits = np.exp(logits - np.max(logits))
    probs = exp_logits / np.sum(exp_logits)
    confidence = float(probs[action_idx])

    return {
        "action_index": action_idx,
        "action_name": action_info["name"],
        "action_kw": action_info["kw"],
        "decision": action_info["decision"],
        "trade_action": action_info["trade"],
        "confidence": confidence,
        "logits": logits.tolist(),
        "probabilities": probs.tolist(),
        "inference_time_ms": inference_time,
    }


def apply_safety_shield(result: Dict[str, Any], soc: float, soc_cap: float,
                        net_kw: float, soc_min: float = 0.10, soc_max: float = 0.95,
                        max_charge: float = 3.0, max_discharge: float = 3.0) -> str:
    """
    Full safety shield: clamp kW AND modify action type if unsafe.

    Returns safety_status: "safe" | "clamped" | "modified"
    """
    action_kw = result.get("action_kw", 0.0)
    decision = result.get("decision", "HOLD")
    soc_frac = soc / soc_cap if soc_cap > 0 else 0.5
    dt = 5.0 / 60.0  # 5-minute timestep
    status = "safe"

    # Step 1: Clamp kW to physical limits
    capped = float(np.clip(action_kw, -max_discharge, max_charge))
    if capped != action_kw:
        status = "clamped"

    # Step 2: Check SoC boundaries after proposed action
    new_soc_frac = soc_frac + (capped * dt / soc_cap) if soc_cap > 0 else soc_frac

    if new_soc_frac < soc_min:
        # Discharging/selling would drain battery below limit
        if decision in ("DISCHARGE", "SELL"):
            result["decision"] = "HOLD"
            result["trade_action"] = None
            result["action_name"] = "idle"
            result["action_index"] = 2
            capped = 0.0
            status = "modified"
        else:
            # Clamp charge to just reach soc_min
            capped = max(0.0, (soc_min * soc_cap - soc) / dt)
            if capped != action_kw:
                status = "clamped"

    elif new_soc_frac > soc_max:
        # Charging/buying would overfill battery
        if decision in ("CHARGE", "BUY"):
            # If there's surplus, redirect to SELL
            if net_kw > 0.3:
                result["decision"] = "SELL"
                result["trade_action"] = "SELL"
                result["action_name"] = "offer_sell"
                result["action_index"] = 5
                capped = -min(net_kw * 0.8, max_discharge)
            else:
                result["decision"] = "HOLD"
                result["trade_action"] = None
                result["action_name"] = "idle"
                result["action_index"] = 2
                capped = 0.0
            status = "modified"
        else:
            capped = max(0.0, (soc_max * soc_cap - soc) / dt)
            if capped != action_kw:
                status = "clamped"

    # Step 3: Prevent SELL when no surplus and battery low
    if result.get("decision") == "SELL" and net_kw <= 0 and soc_frac < 0.30:
        result["decision"] = "HOLD"
        result["trade_action"] = None
        result["action_name"] = "idle"
        result["action_index"] = 2
        capped = 0.0
        status = "modified"

    result["action_kw"] = float(np.clip(capped, -max_discharge, max_charge))
    return status


# === API Endpoints ===

@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy" if model_state["model_loaded"] else "degraded",
        "model_loaded": model_state["model_loaded"],
        "model_type": model_state["model_type"],
        "timestamp": time.time(),
    })


@app.route("/model-info", methods=["GET"])
def model_info():
    """Get model information"""
    if not model_state["model_loaded"]:
        return jsonify({"error": "Model not loaded"}), 503

    return jsonify(model_state["model_info"])


@app.route("/infer", methods=["POST"])
def infer():
    """Run inference on telemetry data with dynamic model selection"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        node_id = data.get("node_id", "unknown")
        telemetry = data.get("telemetry", {})
        context = data.get("context", {})
        apply_safety = data.get("apply_safety", True)
        use_dynamic = data.get("use_dynamic_selection", model_state["use_dynamic_selection"])

        # Merge context into telemetry
        merged = {**telemetry, **context}
        merged["timestamp"] = merged.get("timestamp", time.time())

        # Use PolicySelector if available and enabled
        if use_dynamic and model_state["policy_selector"] and SELECTOR_AVAILABLE:
            selector = model_state["policy_selector"]

            # Build observation array
            obs = build_observation(merged)

            # Run inference with dynamic selection + environmental override
            result = selector.infer(merged, obs)

            # Use trade_action from selector if present, else infer from action
            if not result.get("trade_action"):
                action_name = result.get("action_name", "")
                net_kw = merged.get("net_kw", 0)
                if action_name in ("offer_sell",) or result.get("decision") == "SELL":
                    result["trade_action"] = "SELL"
                elif action_name in ("buy_energy",) or (net_kw < -0.3 and action_name in ("charge_small", "charge_large")):
                    result["trade_action"] = "BUY"
            result["inference_time_ms"] = 0.0

        elif model_state["model_loaded"]:
            # Fallback to single model inference
            obs = build_observation(merged)
            obs_norm = normalize_observation(obs)
            result = run_inference(obs_norm)

            # Add placeholder selection info
            result["selected_policy"] = model_state["model_info"].get("name", "CQL")
            result["policy_reason"] = "Single model mode"
            result["condition"] = "normal"
            result["condition_confidence"] = 0.8
            result["volatility"] = 0.0
            result["sub_conditions"] = []

        else:
            return jsonify({"error": "No model loaded"}), 503

        # Apply full safety shield (modifies action type + clamps kW)
        if apply_safety:
            soc = merged.get("soc_kwh", 2.0)
            soc_cap = merged.get("soc_capacity_kwh", 4.0)
            net_kw = merged.get("net_kw", 0.0)
            safety_status = apply_safety_shield(result, soc, soc_cap, net_kw)
            result["safety_applied"] = True
            result["safety_status"] = safety_status
        else:
            result["safety_applied"] = False
            result["safety_status"] = "unchecked"

        # Add metadata
        result["node_id"] = node_id
        result["model_version"] = model_state["model_info"]["name"] if model_state["model_info"] else "PolicySelector"
        result["timestamp"] = time.time()

        # Calculate trade recommendations
        trade_action = result.get("trade_action")
        if trade_action:
            net_kw = merged.get("net_kw", 0)
            if trade_action == "SELL" and net_kw > 0:
                result["recommended_quantity"] = min(net_kw * 0.8, 5.0)
            elif trade_action == "BUY" and net_kw < 0:
                result["recommended_quantity"] = min(abs(net_kw) * 0.8, 5.0)
            else:
                result["recommended_quantity"] = 0.3
        else:
            result["recommended_quantity"] = 0

        # Add forecast info
        result["forecasted_load"] = merged.get("load_kw", 0.8) * 1000
        result["forecasted_solar"] = merged.get("pv_gen_kw", 0) * 1000
        result["net_power_kw"] = merged.get("net_kw", 0)

        # Enhanced logging
        override = result.get("environmental_override", "")
        logger.info(
            f"Inference for {node_id}: {result.get('action_name', '?')} "
            f"[{result.get('selected_policy', '?')}] "
            f"condition={result.get('condition', '?')} "
            f"decision={result.get('decision', '?')} "
            f"trade={result.get('trade_action', 'none')} "
            f"confidence={result.get('confidence', 0):.2f} "
            f"safety={result.get('safety_status', '?')}"
            + (f" override={override}" if override else "")
        )

        return jsonify(result)

    except Exception as e:
        logger.error(f"Inference error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/condition", methods=["POST"])
def detect_condition():
    """Detect operating condition from telemetry"""
    try:
        data = request.json
        if not data:
            return jsonify({"error": "No JSON data provided"}), 400

        telemetry = data.get("telemetry", data)

        if SELECTOR_AVAILABLE and model_state["policy_selector"]:
            detector = model_state["policy_selector"].condition_detector
            result = detector.detect(telemetry)
            return jsonify(result.to_dict())
        else:
            # Basic condition detection fallback
            soc_frac = telemetry.get("soc_kwh", 2) / telemetry.get("soc_capacity_kwh", 4)
            net_kw = telemetry.get("net_kw", 0)

            if soc_frac < 0.2:
                condition = "low_soc"
            elif soc_frac > 0.9:
                condition = "high_soc"
            elif net_kw > 1.0:
                condition = "high_pv"
            elif net_kw < -1.0:
                condition = "high_load"
            else:
                condition = "normal"

            return jsonify({
                "condition": condition,
                "confidence": 0.7,
                "volatility": 0.0,
                "sub_conditions": [],
                "metrics": {
                    "soc_fraction": soc_frac,
                    "net_kw": net_kw,
                },
            })

    except Exception as e:
        logger.error(f"Condition detection error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/selection-stats", methods=["GET"])
def selection_stats():
    """Get policy selection statistics"""
    if SELECTOR_AVAILABLE and model_state["policy_selector"]:
        stats = model_state["policy_selector"].get_selection_stats()
        return jsonify(stats)
    else:
        return jsonify({
            "total": 0,
            "by_policy": {},
            "by_condition": {},
            "message": "Dynamic selection not enabled",
        })


@app.route("/batch-infer", methods=["POST"])
def batch_infer():
    """Run batch inference on multiple telemetry records"""
    if not model_state["model_loaded"]:
        return jsonify({"error": "Model not loaded"}), 503

    try:
        data = request.json
        if not data or "items" not in data:
            return jsonify({"error": "No items provided"}), 400

        results = []
        for item in data["items"]:
            node_id = item.get("node_id", "unknown")
            telemetry = item.get("telemetry", {})
            context = item.get("context", {})

            merged = {**telemetry, **context}
            obs = build_observation(merged)
            obs_norm = normalize_observation(obs)
            result = run_inference(obs_norm)
            result["node_id"] = node_id
            results.append(result)

        return jsonify({"results": results})

    except Exception as e:
        logger.error(f"Batch inference error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/reload", methods=["POST"])
def reload_model():
    """Reload the model"""
    try:
        if model_state["model_path"]:
            load_model(model_state["model_path"])
            return jsonify({"status": "reloaded", "model": model_state["model_path"]})
        return jsonify({"error": "No model path stored"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def main():
    parser = argparse.ArgumentParser(description="Grid-Guardian AI Inference Server")
    parser.add_argument("--port", type=int, default=5050, help="Server port")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--model", help="Path to model file (.onnx, .torchscript, or .pt)")
    parser.add_argument("--norm", help="Path to normalization parameters (.npz)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--dynamic", action="store_true", help="Enable dynamic model selection (BC/CQL/DT)")
    parser.add_argument("--models-dir", help="Directory containing all policy models for dynamic selection")
    args = parser.parse_args()

    # Initialize PolicySelector if dynamic selection enabled
    if args.dynamic and SELECTOR_AVAILABLE:
        logger.info("Initializing dynamic policy selection...")
        models_dir = args.models_dir
        if not models_dir:
            # Try default locations
            search_dirs = [
                Path(__file__).parent.parent / "grid_guardian_edge" / "models",
                Path(__file__).parent / "edge" / "policy_pack",
            ]
            for d in search_dirs:
                if d.exists():
                    models_dir = str(d)
                    break

        if models_dir:
            selector = PolicySelector(models_dir)
            load_results = selector.load_models()
            model_state["policy_selector"] = selector
            model_state["use_dynamic_selection"] = True

            loaded_count = sum(1 for v in load_results.values() if v)
            logger.info(f"Loaded {loaded_count}/3 policies for dynamic selection")

            # Mark as loaded if any model available
            if loaded_count > 0:
                model_state["model_loaded"] = True
                model_state["model_info"] = {
                    "name": "PolicySelector",
                    "version": "2.0.0",
                    "type": "dynamic",
                    "policies_loaded": {k.value: v for k, v in load_results.items()},
                    "obs_dim": 18,
                    "act_dim": 7,
                }
        else:
            logger.warning("No models directory found for dynamic selection")

    # Also load single model if specified (fallback)
    model_path = args.model
    if not model_path and not model_state["use_dynamic_selection"]:
        # Search for models in default locations
        search_paths = [
            Path(__file__).parent / "models" / "CQL" / "run_42" / "checkpoint_best.pt",
            Path(__file__).parent.parent / "grid_guardian_edge" / "models" / "cql_policy.onnx",
            Path(__file__).parent / "edge" / "policy_pack" / "policy.onnx",
        ]

        for p in search_paths:
            if p.exists():
                model_path = str(p)
                logger.info(f"Found model at: {model_path}")
                break

    if model_path:
        try:
            load_model(model_path, args.norm)
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            if not model_state["use_dynamic_selection"]:
                sys.exit(1)

    if not model_state["model_loaded"] and not model_state["use_dynamic_selection"]:
        logger.error("No model found! Specify with --model or use --dynamic")
        sys.exit(1)

    # Start server
    logger.info(f"Starting AI Inference Server on {args.host}:{args.port}")
    if model_state["use_dynamic_selection"]:
        logger.info("Mode: Dynamic Policy Selection (BC/CQL/DT)")
    else:
        logger.info(f"Mode: Single Model ({model_state['model_info']['name']})")

    # Use waitress for production if available
    try:
        from waitress import serve
        logger.info("Using Waitress production server")
        serve(app, host=args.host, port=args.port)
    except ImportError:
        logger.info("Using Flask development server (install waitress for production)")
        app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
