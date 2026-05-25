"""
Model Registry and Loader - Manages multiple policy models.
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass
import numpy as np

from app.core.config import settings, MODEL_METADATA, ACTION_MAP
from app.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ModelInfo:
    """Model metadata and status."""
    name: str
    type: str
    loaded: bool
    path: Optional[str]
    format: Optional[str]  # torchscript, onnx, tflite
    metadata: Dict[str, Any]
    error: Optional[str] = None


class ModelRegistry:
    """
    Registry for managing multiple policy models.

    Supports:
    - BC (Behavior Cloning)
    - CQL (Conservative Q-Learning)
    - DT (Decision Transformer)
    """

    def __init__(self):
        self.models: Dict[str, Any] = {}
        self.model_info: Dict[str, ModelInfo] = {}
        self.model_types: Dict[str, str] = {}  # model_name -> format
        self._initialized = False

    async def initialize(self):
        """Initialize the model registry and load all available models."""
        artifacts_path = Path(settings.MODEL_ARTIFACTS_PATH)

        logger.info(f"Loading models from: {artifacts_path}")

        # Try to load each model type
        for model_name, metadata in MODEL_METADATA.items():
            model_path = artifacts_path / model_name
            loaded = False
            error = None
            model_format = None
            model_file = None

            if model_path.exists():
                # Try different formats
                formats = [
                    ("torchscript", f"{model_name}_policy.torchscript"),
                    ("onnx", f"{model_name}_policy.onnx"),
                    ("pt", f"checkpoint_best.pt"),
                ]

                for fmt, filename in formats:
                    file_path = model_path / filename
                    if file_path.exists():
                        try:
                            model = await self._load_model(str(file_path), fmt)
                            self.models[model_name] = model
                            model_format = fmt
                            model_file = str(file_path)
                            loaded = True
                            logger.info(f"Loaded {model_name} model ({fmt}): {file_path}")
                            break
                        except Exception as e:
                            error = str(e)
                            logger.warning(f"Failed to load {model_name} ({fmt}): {e}")
            else:
                error = f"Model path not found: {model_path}"
                logger.warning(error)

            self.model_info[model_name] = ModelInfo(
                name=model_name,
                type=metadata["type"],
                loaded=loaded,
                path=model_file,
                format=model_format,
                metadata=metadata,
                error=error,
            )

            if loaded:
                self.model_types[model_name] = model_format

        # Load from main policy pack if available
        policy_pack = Path(settings.MODEL_ARTIFACTS_PATH).parent / "policy_pack"
        if policy_pack.exists() and "cql" not in self.models:
            try:
                ts_path = policy_pack / "cql_policy.torchscript"
                onnx_path = policy_pack / "cql_policy.onnx"

                if ts_path.exists():
                    model = await self._load_model(str(ts_path), "torchscript")
                    self.models["cql"] = model
                    self.model_types["cql"] = "torchscript"
                    self.model_info["cql"] = ModelInfo(
                        name="cql",
                        type="CQL",
                        loaded=True,
                        path=str(ts_path),
                        format="torchscript",
                        metadata=MODEL_METADATA["cql"],
                    )
                    logger.info(f"Loaded CQL from policy pack: {ts_path}")
                elif onnx_path.exists():
                    model = await self._load_model(str(onnx_path), "onnx")
                    self.models["cql"] = model
                    self.model_types["cql"] = "onnx"
                    self.model_info["cql"] = ModelInfo(
                        name="cql",
                        type="CQL",
                        loaded=True,
                        path=str(onnx_path),
                        format="onnx",
                        metadata=MODEL_METADATA["cql"],
                    )
                    logger.info(f"Loaded CQL from policy pack: {onnx_path}")
            except Exception as e:
                logger.warning(f"Failed to load from policy pack: {e}")

        self._initialized = True

        # Log summary
        loaded_count = sum(1 for m in self.model_info.values() if m.loaded)
        logger.info(f"Model registry initialized: {loaded_count}/{len(MODEL_METADATA)} models loaded")

    async def _load_model(self, path: str, format: str):
        """Load a model from file."""
        if format == "torchscript":
            import torch
            model = torch.jit.load(path, map_location="cpu")
            model.eval()
            return model

        elif format == "onnx":
            import onnxruntime as ort
            return ort.InferenceSession(path, providers=["CPUExecutionProvider"])

        elif format == "pt":
            import torch
            checkpoint = torch.load(path, map_location="cpu")
            # For checkpoint files, we need to know the model architecture
            # This is a simplified version - in production, you'd load the full model
            return checkpoint

        else:
            raise ValueError(f"Unsupported model format: {format}")

    def get_model(self, name: str) -> Optional[Any]:
        """Get a loaded model by name."""
        return self.models.get(name.lower())

    def get_available_models(self) -> Dict[str, bool]:
        """Get dict of model availability."""
        return {name: info.loaded for name, info in self.model_info.items()}

    def get_model_info(self, name: str) -> Optional[ModelInfo]:
        """Get model info by name."""
        return self.model_info.get(name.lower())

    def get_all_info(self) -> Dict[str, ModelInfo]:
        """Get all model info."""
        return self.model_info

    def run_inference(
        self, model_name: str, observation: np.ndarray
    ) -> Dict[str, Any]:
        """
        Run inference with a specific model.

        Args:
            model_name: Name of model to use
            observation: Observation vector (numpy array)

        Returns:
            Dict with action_index, logits, probabilities
        """
        model_name = model_name.lower()

        if model_name not in self.models:
            raise ValueError(f"Model '{model_name}' not loaded")

        model = self.models[model_name]
        model_type = self.model_types[model_name]

        # Run inference based on model type
        if model_type == "torchscript":
            import torch
            with torch.no_grad():
                obs_tensor = torch.as_tensor(observation, dtype=torch.float32)
                if obs_tensor.dim() == 1:
                    obs_tensor = obs_tensor.unsqueeze(0)
                logits = model(obs_tensor).squeeze(0).numpy()

        elif model_type == "onnx":
            obs_input = observation.reshape(1, -1).astype(np.float32)
            logits = model.run(None, {"observation": obs_input})[0].squeeze(0)

        else:
            # Fallback for checkpoint files - use random action
            logger.warning(f"Using random action for model type: {model_type}")
            logits = np.random.randn(len(ACTION_MAP))

        # Get action and probabilities
        action_idx = int(np.argmax(logits))
        exp_logits = np.exp(logits - np.max(logits))
        probabilities = exp_logits / np.sum(exp_logits)
        confidence = float(probabilities[action_idx])

        # Get action info
        action_info = ACTION_MAP.get(action_idx, ACTION_MAP[2])  # default to idle

        return {
            "action_index": action_idx,
            "action_name": action_info["name"],
            "action": action_info["action"],
            "action_kw": action_info["kw"],
            "trade_action": action_info["trade"],
            "confidence": confidence,
            "logits": logits.tolist(),
            "probabilities": probabilities.tolist(),
        }

    async def reload_model(self, name: str) -> bool:
        """Reload a specific model."""
        info = self.model_info.get(name.lower())
        if not info or not info.path:
            return False

        try:
            model = await self._load_model(info.path, info.format)
            self.models[name.lower()] = model
            info.loaded = True
            info.error = None
            logger.info(f"Reloaded model: {name}")
            return True
        except Exception as e:
            info.error = str(e)
            logger.error(f"Failed to reload model {name}: {e}")
            return False

    async def shutdown(self):
        """Clean up resources."""
        self.models.clear()
        self._initialized = False
        logger.info("Model registry shut down")


# Global model registry instance
model_registry = ModelRegistry()
