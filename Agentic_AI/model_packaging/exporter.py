"""
Packaging & edge-deployment utilities.

Provides:
 - export_torchscript : convert PyTorch model to TorchScript
 - export_onnx        : convert to ONNX format
 - quantize_model     : post-training dynamic quantization
 - NormalizationPipeline : input normalization for edge inference
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


class NormalizationPipeline:
    """Stores mean/std for each observation dimension and applies norm."""

    def __init__(self, means: np.ndarray, stds: np.ndarray):
        self.means = means.astype(np.float32)
        self.stds = np.clip(stds.astype(np.float32), 1e-8, None)

    @classmethod
    def fit(cls, observations: np.ndarray) -> "NormalizationPipeline":
        return cls(observations.mean(axis=0), observations.std(axis=0))

    def transform(self, obs: np.ndarray) -> np.ndarray:
        return ((obs - self.means) / self.stds).astype(np.float32)

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, means=self.means, stds=self.stds)

    @classmethod
    def load(cls, path: str) -> "NormalizationPipeline":
        d = np.load(path)
        return cls(d["means"], d["stds"])


class WrappedPolicy(nn.Module):
    """Policy with built-in normalization for TorchScript export."""

    def __init__(self, policy_net: nn.Module, means: torch.Tensor, stds: torch.Tensor):
        super().__init__()
        self.policy = policy_net
        self.register_buffer("means", means)
        self.register_buffer("stds", stds)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        normed = (obs - self.means) / self.stds
        return self.policy(normed)


def export_torchscript(
    policy_net: nn.Module,
    obs_dim: int,
    save_path: str,
    norm: Optional[NormalizationPipeline] = None,
) -> str:
    """Export policy to TorchScript (.pt) file."""
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    policy_net.eval()

    if norm is not None:
        wrapped = WrappedPolicy(
            policy_net,
            torch.as_tensor(norm.means),
            torch.as_tensor(norm.stds),
        )
        wrapped.eval()
        model_to_export = wrapped
    else:
        model_to_export = policy_net

    dummy = torch.randn(1, obs_dim)
    scripted = torch.jit.trace(model_to_export, dummy)
    scripted.save(save_path)
    logger.info("TorchScript model saved to %s", save_path)
    return save_path


def export_onnx(
    policy_net: nn.Module,
    obs_dim: int,
    save_path: str,
    norm: Optional[NormalizationPipeline] = None,
) -> str:
    """Export policy to ONNX format."""
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    policy_net.eval()

    if norm is not None:
        wrapped = WrappedPolicy(
            policy_net,
            torch.as_tensor(norm.means),
            torch.as_tensor(norm.stds),
        )
        wrapped.eval()
        model_to_export = wrapped
    else:
        model_to_export = policy_net

    dummy = torch.randn(1, obs_dim)
    torch.onnx.export(
        model_to_export, dummy, save_path,
        input_names=["observation"],
        output_names=["action_logits"],
        dynamic_axes={"observation": {0: "batch"}, "action_logits": {0: "batch"}},
        opset_version=14,
    )
    logger.info("ONNX model saved to %s", save_path)
    return save_path


def quantize_model(model_path: str, output_path: str) -> str:
    """Apply dynamic quantization to a TorchScript model (CPU only)."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    try:
        model = torch.jit.load(model_path)
        # TorchScript models can't always be quantized directly;
        # attempt dynamic quantization and fall back gracefully
        quantized = torch.quantization.quantize_dynamic(
            model, {nn.Linear}, dtype=torch.qint8
        )
        if isinstance(quantized, torch.jit.ScriptModule):
            torch.jit.save(quantized, output_path)
        else:
            torch.jit.save(torch.jit.script(quantized), output_path)
        logger.info("Quantized model saved to %s", output_path)
    except Exception as e:
        logger.warning("Dynamic quantization not supported for ScriptModule, "
                       "copying original model: %s", e)
        import shutil
        shutil.copy2(model_path, output_path)
    return output_path


def save_model_card(
    save_dir: str,
    algo: str,
    obs_dim: int,
    act_dim: int,
    obs_keys: List[str],
    metrics: Dict,
):
    """Save a model card (JSON) describing the model inputs/outputs."""
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    card = {
        "model_name": f"GridGuardian-{algo}",
        "obs_dim": obs_dim,
        "act_dim": act_dim,
        "obs_keys": obs_keys,
        "input_schema": "float32 vector of normalized observations",
        "output_schema": "action logits (discrete) or mean action (continuous)",
        "metrics": metrics,
        "normalization": "z-score (mean/std stored in norm_params.npz)",
        "target_platform": "Raspberry Pi 5 (ARM64)",
    }
    def _default(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return float(o)
        if isinstance(o, np.ndarray):
            return o.tolist()
        raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")

    with open(Path(save_dir) / "model_card.json", "w") as f:
        json.dump(card, f, indent=2, default=_default)
    logger.info("Model card saved to %s", save_dir)
