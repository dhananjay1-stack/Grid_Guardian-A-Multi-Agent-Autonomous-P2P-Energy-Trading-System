"""Model registry for managing multiple policies."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import json

from .loader import (
    load_torchscript, load_onnx, load_pytorch_checkpoint,
    load_norm_params, load_model_card, InferenceModel
)
from ..core.config import settings
from ..core.logger import get_logger
from ..core.router import policy_router


logger = get_logger("model_registry")


@dataclass
class ModelMetadata:
    """Metadata for a loaded model."""
    name: str
    policy_type: str
    version: str = "1.0.0"
    algorithm: str = ""
    obs_dim: int = 18
    act_dim: int = 7
    training_date: str = ""
    preferred_conditions: list = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LoadedPolicy:
    """A loaded policy with its model and metadata."""
    model: InferenceModel
    metadata: ModelMetadata
    norm_means: Optional[Any] = None
    norm_stds: Optional[Any] = None


class ModelRegistry:
    """
    Registry for managing multiple policy models.

    Handles loading, tracking, and providing access to BC/CQL/DT policies.
    """

    def __init__(self):
        self._policies: Dict[str, LoadedPolicy] = {}
        self._active_policy: Optional[str] = None
        self._fallback_policy: str = settings.fallback_policy
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize the registry by loading all available models."""
        success = True

        # Try to load each policy type
        policies_to_load = [
            ("BC", self._load_bc_policy),
            ("CQL", self._load_cql_policy),
            ("DT", self._load_dt_policy),
        ]

        for policy_name, loader_fn in policies_to_load:
            try:
                loaded = loader_fn()
                if loaded:
                    self._policies[policy_name] = loaded
                    policy_router.set_policy_available(policy_name, True)
                    logger.info(f"Policy {policy_name} loaded successfully")
                else:
                    policy_router.set_policy_available(policy_name, False)
                    logger.warning(f"Policy {policy_name} not loaded")
            except Exception as e:
                policy_router.set_policy_available(policy_name, False)
                logger.error(f"Failed to load {policy_name}: {e}")
                success = False

        # Set default active policy
        if self._policies:
            self._active_policy = list(self._policies.keys())[0]
            self._initialized = True
        else:
            logger.error("No policies loaded!")
            success = False

        return success

    def _load_bc_policy(self) -> Optional[LoadedPolicy]:
        """Load Behavior Cloning policy."""
        # Try checkpoint first
        checkpoint_path = settings.model_base_path / "BC" / "run_42" / "checkpoint_best.pt"
        if checkpoint_path.exists():
            model = load_pytorch_checkpoint(
                str(checkpoint_path), "BC",
                settings.obs_dim, settings.act_dim
            )
            return LoadedPolicy(
                model=model,
                metadata=ModelMetadata(
                    name="GridGuardian-BC",
                    policy_type="BC",
                    algorithm="Behavior Cloning",
                    preferred_conditions=["degraded", "fallback", "stress_test"]
                )
            )
        return None

    def _load_cql_policy(self) -> Optional[LoadedPolicy]:
        """Load Conservative Q-Learning policy."""
        # Try TorchScript first (from policy pack)
        torchscript_path = settings.policy_pack_path / "cql_policy.torchscript"
        onnx_path = settings.policy_pack_path / "cql_policy.onnx"
        checkpoint_path = settings.model_base_path / "CQL" / "run_42" / "checkpoint_best.pt"
        norm_path = settings.policy_pack_path / "norm_params.npz"
        model_card_path = settings.policy_pack_path / "model_card.json"

        model = None
        norm_means, norm_stds = None, None
        metadata_dict = {}

        # Load normalization params
        if norm_path.exists():
            norm_means, norm_stds = load_norm_params(str(norm_path))

        # Load model card
        if model_card_path.exists():
            metadata_dict = load_model_card(str(model_card_path))

        # Try loading in order of preference
        if torchscript_path.exists() and not settings.use_onnx:
            model = load_torchscript(str(torchscript_path))
        elif onnx_path.exists():
            model = load_onnx(str(onnx_path))
        elif checkpoint_path.exists():
            model = load_pytorch_checkpoint(
                str(checkpoint_path), "CQL",
                settings.obs_dim, settings.act_dim
            )

        if model:
            return LoadedPolicy(
                model=model,
                metadata=ModelMetadata(
                    name=metadata_dict.get("model_name", "GridGuardian-CQL"),
                    policy_type="CQL",
                    algorithm="Conservative Q-Learning",
                    obs_dim=metadata_dict.get("obs_dim", settings.obs_dim),
                    act_dim=metadata_dict.get("act_dim", settings.act_dim),
                    metrics=metadata_dict.get("metrics", {}),
                    preferred_conditions=["uncertain", "risky", "high_volatility"]
                ),
                norm_means=norm_means,
                norm_stds=norm_stds
            )
        return None

    def _load_dt_policy(self) -> Optional[LoadedPolicy]:
        """Load Decision Transformer policy."""
        checkpoint_path = settings.model_base_path / "DT" / "run_42" / "checkpoint_best.pt"
        norm_path = settings.model_base_path / "DT" / "run_42" / "norm_params.npz"

        if checkpoint_path.exists():
            model = load_pytorch_checkpoint(
                str(checkpoint_path), "DT",
                settings.obs_dim, settings.act_dim
            )

            norm_means, norm_stds = None, None
            if norm_path.exists():
                norm_means, norm_stds = load_norm_params(str(norm_path))

            return LoadedPolicy(
                model=model,
                metadata=ModelMetadata(
                    name="GridGuardian-DT",
                    policy_type="DT",
                    algorithm="Decision Transformer",
                    preferred_conditions=["stable", "normal", "long_horizon"]
                ),
                norm_means=norm_means,
                norm_stds=norm_stds
            )
        return None

    def get_policy(self, name: str) -> Optional[LoadedPolicy]:
        """Get a specific policy by name."""
        return self._policies.get(name)

    def get_active_policy(self) -> Optional[LoadedPolicy]:
        """Get the currently active policy."""
        if self._active_policy:
            return self._policies.get(self._active_policy)
        return None

    def set_active_policy(self, name: str) -> bool:
        """Set the active policy."""
        if name in self._policies:
            self._active_policy = name
            return True
        return False

    def is_available(self, name: str) -> bool:
        """Check if a policy is available."""
        return name in self._policies

    def get_all_status(self) -> Dict[str, Any]:
        """Get status of all policies."""
        return {
            "initialized": self._initialized,
            "active_policy": self._active_policy,
            "fallback_policy": self._fallback_policy,
            "policies": {
                name: {
                    "loaded": True,
                    "algorithm": policy.metadata.algorithm,
                    "preferred_conditions": policy.metadata.preferred_conditions,
                }
                for name, policy in self._policies.items()
            },
            "available_policies": list(self._policies.keys()),
        }

    def reload_policy(self, name: str) -> bool:
        """Reload a specific policy."""
        loaders = {
            "BC": self._load_bc_policy,
            "CQL": self._load_cql_policy,
            "DT": self._load_dt_policy,
        }

        if name not in loaders:
            return False

        try:
            loaded = loaders[name]()
            if loaded:
                self._policies[name] = loaded
                policy_router.set_policy_available(name, True)
                logger.info(f"Policy {name} reloaded successfully")
                return True
        except Exception as e:
            logger.error(f"Failed to reload {name}: {e}")

        return False


# Global instance
model_registry = ModelRegistry()
