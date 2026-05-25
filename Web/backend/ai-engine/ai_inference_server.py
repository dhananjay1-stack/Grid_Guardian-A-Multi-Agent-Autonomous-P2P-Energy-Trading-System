#!/usr/bin/env python3
"""
AI Decision Engine Server for Grid-Guardian.

This Flask server provides HTTP endpoints for the Node.js backend to query
the trained CQL model for energy trading decisions.

The server loads the exported policy pack and processes telemetry observations
to produce actionable decisions (BUY, SELL, HOLD, CHARGE, DISCHARGE).
"""
import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

import numpy as np
from flask import Flask, request, jsonify
from flask_cors import CORS

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('ai_inference_server')

app = Flask(__name__)
CORS(app)

# Global model and normalization parameters
model = None
model_type = None
norm_means = None
norm_stds = None
model_card = None


# Discrete action mapping - matches edge_inference.py
DISCRETE_ACTIONS = {
    0: {"name": "charge_small", "kw": 1.0, "decision": "CHARGE", "trade": None},
    1: {"name": "charge_large", "kw": 3.0, "decision": "CHARGE", "trade": None},
    2: {"name": "idle", "kw": 0.0, "decision": "HOLD", "trade": None},
    3: {"name": "discharge_small", "kw": -1.0, "decision": "DISCHARGE", "trade": None},
    4: {"name": "discharge_large", "kw": -3.0, "decision": "DISCHARGE", "trade": None},
    5: {"name": "offer_sell", "kw": -1.5, "decision": "SELL", "trade": "SELL"},
    6: {"name": "offer_hold", "kw": 0.0, "decision": "HOLD", "trade": None},
}

# Observation keys from model card
OBS_KEYS = [
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
    "current_a"
]

# Default observation values for missing fields
DEFAULT_OBS = {
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
    "current_a": 3.5
}


def load_torchscript(path: str):
    """Load TorchScript model."""
    import torch
    model = torch.jit.load(path, map_location="cpu")
    model.eval()
    return model, "torchscript"


def load_onnx(path: str):
    """Load ONNX model."""
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    return sess, "onnx"


def load_norm_params(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load normalization parameters."""
    d = np.load(path)
    means = d["means"].astype(np.float32)
    stds = np.clip(d["stds"].astype(np.float32), 1e-8, None)
    return means, stds


def load_model_card(path: str) -> Dict[str, Any]:
    """Load model card metadata."""
    with open(path, 'r') as f:
        return json.load(f)


def initialize_model():
    """Initialize the AI model from the policy pack."""
    global model, model_type, norm_means, norm_stds, model_card

    # Policy pack path - configurable via environment variable
    policy_pack_path = os.environ.get(
        'POLICY_PACK_PATH',
        str(Path(__file__).parent.parent.parent.parent / 'Agentic_AI' / 'edge' / 'policy_pack')
    )

    policy_pack = Path(policy_pack_path)

    if not policy_pack.exists():
        logger.error(f"Policy pack not found at {policy_pack}")
        raise FileNotFoundError(f"Policy pack not found at {policy_pack}")

    # Load model card
    model_card_path = policy_pack / "model_card.json"
    if model_card_path.exists():
        model_card = load_model_card(str(model_card_path))
        logger.info(f"Loaded model card: {model_card.get('model_name', 'unknown')}")

    # Load normalization parameters
    norm_path = policy_pack / "norm_params.npz"
    if norm_path.exists():
        norm_means, norm_stds = load_norm_params(str(norm_path))
        logger.info(f"Loaded normalization parameters: means shape {norm_means.shape}")

    # Load model - prefer TorchScript, fallback to ONNX
    torchscript_path = policy_pack / "cql_policy.torchscript"
    onnx_path = policy_pack / "cql_policy.onnx"

    if torchscript_path.exists():
        model, model_type = load_torchscript(str(torchscript_path))
        logger.info(f"Loaded TorchScript model from {torchscript_path}")
    elif onnx_path.exists():
        model, model_type = load_onnx(str(onnx_path))
        logger.info(f"Loaded ONNX model from {onnx_path}")
    else:
        raise FileNotFoundError("No model file found (cql_policy.torchscript or cql_policy.onnx)")

    logger.info("AI Decision Engine initialized successfully")


def build_observation(telemetry: Dict[str, Any], context: Dict[str, Any] = None) -> np.ndarray:
    """
    Build observation vector from telemetry data.

    Maps telemetry fields to the expected observation format.
    """
    context = context or {}

    # Map telemetry fields to observation keys
    obs_dict = {
        "soc_kwh": telemetry.get("soc_kwh", context.get("soc_kwh", DEFAULT_OBS["soc_kwh"])),
        "soc_capacity_kwh": telemetry.get("soc_capacity_kwh", context.get("soc_capacity_kwh", DEFAULT_OBS["soc_capacity_kwh"])),
        "pv_gen_kw": telemetry.get("pv_gen_kw", telemetry.get("power", 0) / 1000.0),  # Convert W to kW
        "load_kw": telemetry.get("load_kw", context.get("load_kw", DEFAULT_OBS["load_kw"])),
        "net_kw": telemetry.get("net_kw", 0),
        "battery_power_kw": telemetry.get("battery_power_kw", 0),
        "price_signal": telemetry.get("price_signal", context.get("grid_price", DEFAULT_OBS["price_signal"])),
        "forecast_irradiance_1h": telemetry.get("forecast_irradiance_1h", context.get("forecast_irradiance_1h", DEFAULT_OBS["forecast_irradiance_1h"])),
        "forecast_irradiance_3h": telemetry.get("forecast_irradiance_3h", context.get("forecast_irradiance_3h", DEFAULT_OBS["forecast_irradiance_3h"])),
        "forecast_temp_1h": telemetry.get("forecast_temp_1h", context.get("forecast_temp_1h", DEFAULT_OBS["forecast_temp_1h"])),
        "actual_irradiance_wm2": telemetry.get("actual_irradiance_wm2", context.get("actual_irradiance_wm2", DEFAULT_OBS["actual_irradiance_wm2"])),
        "voltage_v": telemetry.get("voltage", telemetry.get("voltage_v", DEFAULT_OBS["voltage_v"])),
        "current_a": telemetry.get("current", telemetry.get("current_a", DEFAULT_OBS["current_a"])),
    }

    # Calculate derived fields if not provided
    if obs_dict["net_kw"] == 0 and obs_dict["pv_gen_kw"] > 0:
        obs_dict["net_kw"] = obs_dict["pv_gen_kw"] - obs_dict["load_kw"]

    # Build observation vector - pad to 18 dimensions as expected by model
    obs = []
    for key in OBS_KEYS:
        obs.append(float(obs_dict.get(key, DEFAULT_OBS.get(key, 0.0))))

    # Pad to 18 dimensions if needed (model expects 18-dim input)
    while len(obs) < 18:
        obs.append(0.0)

    return np.array(obs[:18], dtype=np.float32)


def safety_clip(action_kw: float, soc: float, soc_cap: float,
                soc_min_frac: float = 0.10, soc_max_frac: float = 0.95,
                max_charge: float = 3.0, max_discharge: float = 3.0) -> float:
    """Apply safety constraints to action."""
    capped = np.clip(action_kw, -max_discharge, max_charge)
    dt = 5.0 / 60.0  # 5-minute intervals
    new_soc = soc + capped * dt

    if new_soc < soc_min_frac * soc_cap:
        capped = (soc_min_frac * soc_cap - soc) / dt
    elif new_soc > soc_max_frac * soc_cap:
        capped = (soc_max_frac * soc_cap - soc) / dt

    return float(capped)


def run_inference(obs: np.ndarray) -> Dict[str, Any]:
    """Run model inference on observation."""
    global model, model_type

    if model is None:
        raise RuntimeError("Model not initialized")

    # Normalize observation if normalization params available
    if norm_means is not None and norm_stds is not None:
        obs_normalized = (obs - norm_means[:len(obs)]) / norm_stds[:len(obs)]
    else:
        obs_normalized = obs

    # Run inference
    if model_type == "torchscript":
        import torch
        with torch.no_grad():
            t = torch.as_tensor(obs_normalized, dtype=torch.float32).unsqueeze(0)
            logits = model(t).squeeze(0).numpy()
    else:  # ONNX
        logits = model.run(None, {"observation": obs_normalized.reshape(1, -1)})[0].squeeze(0)

    # Get action
    action_idx = int(np.argmax(logits))
    action_info = DISCRETE_ACTIONS.get(action_idx, DISCRETE_ACTIONS[2])  # Default to idle

    # Calculate confidence (softmax probability of selected action)
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
        "probabilities": probs.tolist()
    }


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "model_loaded": model is not None,
        "model_type": model_type,
        "timestamp": datetime.utcnow().isoformat()
    })


@app.route('/model-info', methods=['GET'])
def model_info():
    """Get model metadata."""
    if model_card is None:
        return jsonify({"error": "Model card not loaded"}), 500

    return jsonify({
        "model_name": model_card.get("model_name"),
        "obs_dim": model_card.get("obs_dim"),
        "act_dim": model_card.get("act_dim"),
        "obs_keys": model_card.get("obs_keys"),
        "metrics": model_card.get("metrics"),
        "target_platform": model_card.get("target_platform")
    })


@app.route('/infer', methods=['POST'])
def infer():
    """
    Run AI inference on telemetry data.

    Request body:
    {
        "node_id": "node_123",
        "telemetry": {
            "voltage": 230.0,
            "current": 3.5,
            "power": 805.0,
            "soc_kwh": 2.0,
            "soc_capacity_kwh": 4.0,
            "pv_gen_kw": 1.2,
            "load_kw": 0.8,
            ...
        },
        "context": {
            "grid_price": 0.15,
            "forecast_irradiance_1h": 400,
            ...
        },
        "apply_safety": true
    }

    Response:
    {
        "node_id": "node_123",
        "decision": "SELL",
        "confidence": 0.85,
        "action_index": 5,
        "action_name": "offer_sell",
        "action_kw": -1.5,
        "trade_action": "SELL",
        "recommended_quantity": 0.25,
        "forecasted_load": 800,
        "forecasted_solar": 1200,
        "timestamp": "2024-03-22T12:00:00Z"
    }
    """
    try:
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        node_id = data.get("node_id", "unknown")
        telemetry = data.get("telemetry", {})
        context = data.get("context", {})
        apply_safety = data.get("apply_safety", True)

        # Build observation vector
        obs = build_observation(telemetry, context)

        # Run inference
        result = run_inference(obs)

        # Apply safety constraints if requested
        if apply_safety:
            soc = telemetry.get("soc_kwh", DEFAULT_OBS["soc_kwh"])
            soc_cap = telemetry.get("soc_capacity_kwh", DEFAULT_OBS["soc_capacity_kwh"])
            result["action_kw"] = safety_clip(result["action_kw"], soc, soc_cap)

        # Calculate recommended trade quantity
        pv_gen = telemetry.get("pv_gen_kw", telemetry.get("power", 0) / 1000.0)
        load = telemetry.get("load_kw", context.get("load_kw", DEFAULT_OBS["load_kw"]))
        net = pv_gen - load

        recommended_quantity = 0.0
        if result["trade_action"] == "SELL" and net > 0:
            recommended_quantity = min(net * 0.8, 2.0)  # Sell up to 80% of surplus, max 2 kWh

        # Build response
        response = {
            "node_id": node_id,
            "decision": result["decision"],
            "confidence": result["confidence"],
            "action_index": result["action_index"],
            "action_name": result["action_name"],
            "action_kw": result["action_kw"],
            "trade_action": result["trade_action"],
            "recommended_quantity": recommended_quantity,
            "forecasted_load": load * 1000,  # Convert to W
            "forecasted_solar": pv_gen * 1000,  # Convert to W
            "net_power_kw": net,
            "timestamp": datetime.utcnow().isoformat(),
            "model_version": model_card.get("model_name", "unknown") if model_card else "unknown"
        }

        logger.info(f"Inference for {node_id}: {result['decision']} (confidence: {result['confidence']:.2f})")

        return jsonify(response)

    except Exception as e:
        logger.error(f"Inference error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/batch-infer', methods=['POST'])
def batch_infer():
    """
    Run AI inference on multiple nodes.

    Request body:
    {
        "nodes": [
            {"node_id": "node_1", "telemetry": {...}, "context": {...}},
            {"node_id": "node_2", "telemetry": {...}, "context": {...}}
        ],
        "apply_safety": true
    }
    """
    try:
        data = request.get_json()

        if not data or "nodes" not in data:
            return jsonify({"error": "No nodes data provided"}), 400

        apply_safety = data.get("apply_safety", True)
        results = []

        for node_data in data["nodes"]:
            node_id = node_data.get("node_id", "unknown")
            telemetry = node_data.get("telemetry", {})
            context = node_data.get("context", {})

            try:
                obs = build_observation(telemetry, context)
                result = run_inference(obs)

                if apply_safety:
                    soc = telemetry.get("soc_kwh", DEFAULT_OBS["soc_kwh"])
                    soc_cap = telemetry.get("soc_capacity_kwh", DEFAULT_OBS["soc_capacity_kwh"])
                    result["action_kw"] = safety_clip(result["action_kw"], soc, soc_cap)

                pv_gen = telemetry.get("pv_gen_kw", telemetry.get("power", 0) / 1000.0)
                load = telemetry.get("load_kw", context.get("load_kw", DEFAULT_OBS["load_kw"]))

                results.append({
                    "node_id": node_id,
                    "decision": result["decision"],
                    "confidence": result["confidence"],
                    "action_kw": result["action_kw"],
                    "trade_action": result["trade_action"],
                    "success": True
                })
            except Exception as e:
                results.append({
                    "node_id": node_id,
                    "success": False,
                    "error": str(e)
                })

        return jsonify({
            "results": results,
            "timestamp": datetime.utcnow().isoformat()
        })

    except Exception as e:
        logger.error(f"Batch inference error: {str(e)}")
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    # Initialize model on startup
    try:
        initialize_model()
    except Exception as e:
        logger.error(f"Failed to initialize model: {e}")
        logger.warning("Starting server without model - inference will fail")

    # Get configuration from environment
    host = os.environ.get('AI_SERVER_HOST', '127.0.0.1')
    port = int(os.environ.get('AI_SERVER_PORT', 5050))
    debug = os.environ.get('AI_SERVER_DEBUG', 'false').lower() == 'true'

    logger.info(f"Starting AI Decision Engine server on {host}:{port}")
    app.run(host=host, port=port, debug=debug)
