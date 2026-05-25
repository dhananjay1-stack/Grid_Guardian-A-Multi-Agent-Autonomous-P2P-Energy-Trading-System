"""Model loading and management."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Protocol
import numpy as np

from ..core.config import settings
from ..core.logger import get_logger


logger = get_logger("model_loader")


class InferenceModel(Protocol):
    """Protocol for inference models."""
    def predict(self, obs: np.ndarray) -> np.ndarray:
        """Return logits or action values."""
        ...


class TorchScriptModel:
    """Wrapper for TorchScript models."""

    def __init__(self, model, device: str = "cpu"):
        self.model = model
        self.device = device

    def predict(self, obs: np.ndarray) -> np.ndarray:
        import torch
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits = self.model(t).squeeze(0).numpy()
        return logits


class ONNXModel:
    """Wrapper for ONNX Runtime models."""

    def __init__(self, session):
        self.session = session

    def predict(self, obs: np.ndarray) -> np.ndarray:
        outputs = self.session.run(
            None,
            {"observation": obs.reshape(1, -1).astype(np.float32)}
        )
        return outputs[0].squeeze(0)


class PyTorchModel:
    """Wrapper for raw PyTorch models (BC, CQL, DT agents)."""

    def __init__(self, model, agent_type: str, device: str = "cpu"):
        self.model = model
        self.agent_type = agent_type
        self.device = device

    def predict(self, obs: np.ndarray) -> np.ndarray:
        import torch
        self.model.eval()
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)

            if self.agent_type == "BC":
                logits = self.model(t)
            elif self.agent_type == "CQL":
                logits = self.model(t)
            elif self.agent_type == "DT":
                # DT needs sequence input - use single-step approximation
                logits = self.model(t)
            else:
                logits = self.model(t)

            return logits.squeeze(0).cpu().numpy()


def load_torchscript(path: str) -> TorchScriptModel:
    """Load a TorchScript model."""
    import torch
    model = torch.jit.load(path, map_location="cpu")
    model.eval()
    logger.info(f"Loaded TorchScript model from {path}")
    return TorchScriptModel(model)


def load_onnx(path: str) -> ONNXModel:
    """Load an ONNX model."""
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    logger.info(f"Loaded ONNX model from {path}")
    return ONNXModel(sess)


def load_pytorch_checkpoint(path: str, agent_type: str,
                            obs_dim: int = 18, act_dim: int = 7) -> PyTorchModel:
    """Load a PyTorch checkpoint for BC/CQL/DT agents."""
    import torch
    import torch.nn as nn

    device = "cuda" if torch.cuda.is_available() else "cpu"

    if agent_type == "BC":
        from .bc_network import BCNetwork
        model = BCNetwork(obs_dim, act_dim).to(device)
        ckpt = torch.load(path, map_location=device, weights_only=False)
        model.load_state_dict(ckpt.get("state_dict", ckpt))

    elif agent_type == "CQL":
        from .cql_network import CQLNetwork
        model = CQLNetwork(obs_dim, act_dim).to(device)
        state_dict = torch.load(path, map_location=device, weights_only=True)
        model.load_state_dict(state_dict)

    elif agent_type == "DT":
        from .dt_network import DTNetwork
        model = DTNetwork(obs_dim, act_dim).to(device)
        ckpt = torch.load(path, map_location=device, weights_only=False)
        model.load_state_dict(ckpt.get("model", ckpt))

    else:
        raise ValueError(f"Unknown agent type: {agent_type}")

    model.eval()
    logger.info(f"Loaded {agent_type} checkpoint from {path}")
    return PyTorchModel(model, agent_type, device)


def load_norm_params(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """Load normalization parameters."""
    d = np.load(path)
    means = d["means"].astype(np.float32)
    stds = np.clip(d["stds"].astype(np.float32), 1e-8, None)
    return means, stds


def load_model_card(path: str) -> Dict[str, Any]:
    """Load model metadata."""
    with open(path, 'r') as f:
        return json.load(f)
