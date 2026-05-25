#!/usr/bin/env python3
"""
Grid-Guardian Policy Selector

Deterministic, tier-based model selection and environmental action override.

Routing tiers (evaluated top-down, first match wins):
  Tier 1 → BC : FAULT, sensor_health < 0.5, anomaly > 0.7, grid_risk > 0.7,
                 critical SoC, volatility > 0.30
  Tier 2 → CQL: moderate volatility, moderate sensor health, moderate anomaly,
                 conditions PEAK_PRICE / LOW_SOC / HIGH_LOAD / VOLATILE
  Tier 3 → DT : stable, sensor_health >= 0.8, anomaly < 0.3, grid_risk < 0.3,
                 conditions NORMAL / HIGH_PV / HIGH_SOC / OFF_PEAK

After model inference, an environmental override layer adjusts the action
based on net_kw, SoC, and price so the final action is physically consistent.
Each model has distinct magnitude limits (BC=±1kW, CQL=±2kW, DT=±3kW).
"""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import torch
except ImportError:
    torch = None

try:
    import onnxruntime as ort
except ImportError:
    ort = None

from condition_detector import (
    ConditionDetector,
    ConditionResult,
    ConditionThresholds,
    OperatingCondition,
    get_condition_detector,
)

logger = logging.getLogger(__name__)


class PolicyType(Enum):
    """Available policy types"""
    BC = "BC"      # Behavioral Cloning - safe imitation
    CQL = "CQL"    # Conservative Q-Learning - safe exploration
    DT = "DT"      # Decision Transformer - long horizon


@dataclass
class SelectionThresholds:
    """Thresholds for deterministic tier-based policy selection"""
    # Volatility thresholds
    high_volatility: float = 0.30      # Above this → BC
    moderate_volatility: float = 0.15  # Above this → CQL, below → DT

    # Sensor health thresholds
    sensor_health_poor: float = 0.50   # Below this → BC
    sensor_health_ok: float = 0.80     # Below this → CQL, above → DT eligible

    # Anomaly score thresholds
    anomaly_high: float = 0.70         # Above this → BC
    anomaly_moderate: float = 0.30     # Above this → CQL

    # Grid risk thresholds
    grid_risk_high: float = 0.70       # Above this → BC
    grid_risk_moderate: float = 0.30   # Above this → CQL

    # Confidence thresholds
    min_confidence_dt: float = 0.70    # Need this confidence for DT
    min_confidence_cql: float = 0.50   # Need this confidence for CQL

    # Safety thresholds
    critical_soc_low: float = 0.15     # Below this → force BC
    critical_soc_high: float = 0.98    # Above this → force BC (prevent overcharge)

    # Action magnitude limits per model
    bc_max_kw: float = 1.0
    cql_max_kw: float = 2.0
    dt_max_kw: float = 3.0

    # Environmental override thresholds
    surplus_threshold: float = 0.3     # net_kw above this → prefer SELL
    deficit_threshold: float = -0.3    # net_kw below this → prefer BUY
    low_soc_charge_threshold: float = 0.20   # SoC below this → force CHARGE
    high_soc_sell_threshold: float = 0.85    # SoC above this → prefer SELL/DISCHARGE
    base_price: float = 5.0


@dataclass
class SelectionResult:
    """Result of policy selection"""
    policy: PolicyType
    reason: str
    condition: ConditionResult
    alternatives: List[Tuple[PolicyType, float]]  # (policy, score) pairs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy": self.policy.value,
            "policy_name": self._get_policy_name(),
            "reason": self.reason,
            "condition": self.condition.to_dict(),
            "alternatives": [
                {"policy": p.value, "score": s}
                for p, s in self.alternatives
            ],
        }

    def _get_policy_name(self) -> str:
        names = {
            PolicyType.BC: "Behavioral Cloning (Safe Fallback)",
            PolicyType.CQL: "Conservative Q-Learning",
            PolicyType.DT: "Decision Transformer",
        }
        return names.get(self.policy, self.policy.value)


class PolicySelector:
    """
    Deterministic tier-based policy selection with environmental action override.
    """

    def __init__(
        self,
        models_dir: Optional[str] = None,
        thresholds: Optional[SelectionThresholds] = None,
        condition_detector: Optional[ConditionDetector] = None,
    ):
        self.thresholds = thresholds or SelectionThresholds()
        self.condition_detector = condition_detector or get_condition_detector()

        # Model paths
        if models_dir:
            self.models_dir = Path(models_dir)
        else:
            self.models_dir = Path(__file__).parent.parent / "grid_guardian_edge" / "models"

        # Loaded models
        self.models: Dict[PolicyType, Any] = {}
        self.model_types: Dict[PolicyType, str] = {}
        self.norm_params: Dict[PolicyType, Dict] = {}

        # Selection statistics
        self.selection_history: List[SelectionResult] = []
        self.max_history = 100

        # Trust scores (can be updated based on performance)
        self.trust_scores = {
            PolicyType.BC: 1.0,
            PolicyType.CQL: 1.0,
            PolicyType.DT: 1.0,
        }

    def load_models(self) -> Dict[PolicyType, bool]:
        """Load all available policy models"""
        results = {}

        model_files = {
            PolicyType.BC: ["bc_policy.onnx", "bc_policy.torchscript"],
            PolicyType.CQL: ["cql_policy.onnx", "cql_policy.torchscript"],
            PolicyType.DT: ["dt_policy.onnx", "dt_policy.torchscript"],
        }

        norm_files = {
            PolicyType.BC: "norm_params_bc.npz",
            PolicyType.CQL: "norm_params.npz",
            PolicyType.DT: "norm_params_dt.npz",
        }

        for policy_type, filenames in model_files.items():
            loaded = False

            for filename in filenames:
                model_path = self.models_dir / filename
                if model_path.exists():
                    try:
                        if filename.endswith(".onnx") and ort:
                            self.models[policy_type] = ort.InferenceSession(
                                str(model_path),
                                providers=["CPUExecutionProvider"]
                            )
                            self.model_types[policy_type] = "onnx"
                            loaded = True
                            logger.info(f"Loaded {policy_type.value} from {model_path}")
                            break
                        elif filename.endswith(".torchscript") and torch:
                            self.models[policy_type] = torch.jit.load(
                                str(model_path),
                                map_location="cpu"
                            )
                            self.models[policy_type].eval()
                            self.model_types[policy_type] = "torchscript"
                            loaded = True
                            logger.info(f"Loaded {policy_type.value} from {model_path}")
                            break
                    except Exception as e:
                        logger.warning(f"Failed to load {model_path}: {e}")

            # Load normalization parameters
            norm_file = self.models_dir / norm_files[policy_type]
            if not norm_file.exists():
                norm_file = self.models_dir / "norm_params.npz"

            if norm_file.exists():
                try:
                    data = np.load(str(norm_file))
                    self.norm_params[policy_type] = {
                        "means": data["means"].astype(np.float32),
                        "stds": np.clip(data["stds"].astype(np.float32), 1e-8, None),
                    }
                except Exception as e:
                    logger.warning(f"Failed to load norm params for {policy_type.value}: {e}")

            results[policy_type] = loaded

        return results

    # ------------------------------------------------------------------
    # Tier-based selection
    # ------------------------------------------------------------------

    def select(self, observation: Dict[str, float]) -> SelectionResult:
        """
        Deterministic tier-based policy selection.
        Evaluated top-down: first tier match wins.
        """
        condition = self.condition_detector.detect(observation)
        metrics = condition.metrics
        volatility = condition.volatility

        sensor_health = metrics.get("sensor_health", 1.0)
        anomaly_score = metrics.get("anomaly_score", 0.0)
        grid_risk = metrics.get("grid_risk", 0.0)
        soc_frac = metrics.get("soc_fraction", 0.5)

        th = self.thresholds

        # ── Tier 1: BC (forced safety fallback) ──────────────────────
        bc_reasons = []
        if condition.condition == OperatingCondition.FAULT:
            bc_reasons.append("FAULT detected")
        if sensor_health < th.sensor_health_poor:
            bc_reasons.append(f"sensor_health={sensor_health:.2f}<{th.sensor_health_poor}")
        if anomaly_score > th.anomaly_high:
            bc_reasons.append(f"anomaly={anomaly_score:.2f}>{th.anomaly_high}")
        if grid_risk > th.grid_risk_high:
            bc_reasons.append(f"grid_risk={grid_risk:.2f}>{th.grid_risk_high}")
        if soc_frac < th.critical_soc_low:
            bc_reasons.append(f"critical_low_soc={soc_frac:.1%}")
        if soc_frac > th.critical_soc_high:
            bc_reasons.append(f"critical_high_soc={soc_frac:.1%}")
        if volatility > th.high_volatility:
            bc_reasons.append(f"high_volatility={volatility:.2f}")

        if bc_reasons:
            reason = "BC forced: " + ", ".join(bc_reasons)
            return self._build_result(PolicyType.BC, reason, condition)

        # ── Tier 2: CQL (moderate uncertainty) ───────────────────────
        cql_reasons = []
        if th.moderate_volatility <= volatility <= th.high_volatility:
            cql_reasons.append(f"moderate_volatility={volatility:.2f}")
        if th.sensor_health_poor <= sensor_health < th.sensor_health_ok:
            cql_reasons.append(f"sensor_health={sensor_health:.2f}")
        if th.anomaly_moderate <= anomaly_score <= th.anomaly_high:
            cql_reasons.append(f"moderate_anomaly={anomaly_score:.2f}")
        if th.grid_risk_moderate <= grid_risk <= th.grid_risk_high:
            cql_reasons.append(f"moderate_grid_risk={grid_risk:.2f}")
        if condition.condition in (
            OperatingCondition.PEAK_PRICE,
            OperatingCondition.LOW_SOC,
            OperatingCondition.HIGH_LOAD,
            OperatingCondition.VOLATILE,
        ):
            cql_reasons.append(f"condition={condition.condition.value}")

        if cql_reasons:
            # Check that CQL model is available, else fall through
            if PolicyType.CQL in self.models:
                reason = "CQL selected: " + ", ".join(cql_reasons)
                return self._build_result(PolicyType.CQL, reason, condition)
            else:
                # CQL unavailable, try DT, else BC
                logger.warning("CQL model not loaded, falling through to DT/BC")

        # ── Tier 3: DT (stable, optimal planning) ────────────────────
        if PolicyType.DT in self.models:
            reason = (
                f"DT selected: stable conditions "
                f"(volatility={volatility:.2f}, sensor={sensor_health:.2f}, "
                f"anomaly={anomaly_score:.2f}, risk={grid_risk:.2f}, "
                f"condition={condition.condition.value})"
            )
            return self._build_result(PolicyType.DT, reason, condition)

        # ── Fallback: whatever is available ───────────────────────────
        for p in [PolicyType.CQL, PolicyType.BC]:
            if p in self.models:
                reason = f"{p.value} fallback: DT not available"
                return self._build_result(p, reason, condition)

        reason = "No models loaded — rule-based fallback"
        return self._build_result(PolicyType.BC, reason, condition)

    def _build_result(
        self, policy: PolicyType, reason: str, condition: ConditionResult
    ) -> SelectionResult:
        """Build SelectionResult with alternatives"""
        # Score-like ordering for the alternatives list
        order = {PolicyType.DT: 3, PolicyType.CQL: 2, PolicyType.BC: 1}
        alternatives = sorted(
            [(p, order.get(p, 0) / 3.0) for p in PolicyType if p != policy],
            key=lambda x: x[1], reverse=True
        )
        result = SelectionResult(
            policy=policy,
            reason=reason,
            condition=condition,
            alternatives=alternatives,
        )
        self.selection_history.append(result)
        if len(self.selection_history) > self.max_history:
            self.selection_history.pop(0)
        return result

    # ------------------------------------------------------------------
    # Inference with environmental override
    # ------------------------------------------------------------------

    def infer(
        self,
        observation: Dict[str, float],
        obs_array: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        """
        Run inference with deterministic policy selection + environmental override.
        """
        # 1. Select policy
        selection = self.select(observation)

        # 2. Build observation array if not provided
        if obs_array is None:
            obs_array = self._build_observation(observation)

        # 3. Get model
        model = self.models.get(selection.policy)
        model_type = self.model_types.get(selection.policy)

        if model is None:
            # Fallback to any loaded model
            for policy in [PolicyType.CQL, PolicyType.BC, PolicyType.DT]:
                if policy in self.models:
                    model = self.models[policy]
                    model_type = self.model_types[policy]
                    selection.reason += f" (using {policy.value} as model fallback)"
                    break

        if model is None:
            return self._rule_based_action(observation, selection)

        # 4. Normalize
        norm_params = self.norm_params.get(selection.policy)
        if norm_params:
            obs_norm = (obs_array - norm_params["means"]) / norm_params["stds"]
        else:
            obs_norm = obs_array

        # 5. Run inference
        if model_type == "onnx":
            logits = model.run(None, {"observation": obs_norm.reshape(1, -1)})[0].squeeze(0)
        else:  # torchscript
            with torch.no_grad():
                t = torch.as_tensor(obs_norm, dtype=torch.float32).unsqueeze(0)
                logits = model(t).squeeze(0).numpy()

        # 6. Raw action from model
        action_idx = int(np.argmax(logits))
        exp_logits = np.exp(logits - np.max(logits))
        probs = exp_logits / np.sum(exp_logits)
        confidence = float(probs[action_idx])

        action_map = {
            0: {"name": "charge_small", "kw": 1.0, "decision": "CHARGE", "trade": None},
            1: {"name": "charge_large", "kw": 3.0, "decision": "CHARGE", "trade": None},
            2: {"name": "idle", "kw": 0.0, "decision": "HOLD", "trade": None},
            3: {"name": "discharge_small", "kw": -1.0, "decision": "DISCHARGE", "trade": None},
            4: {"name": "discharge_large", "kw": -3.0, "decision": "DISCHARGE", "trade": None},
            5: {"name": "offer_sell", "kw": -1.5, "decision": "SELL", "trade": "SELL"},
            6: {"name": "buy_energy", "kw": 1.5, "decision": "BUY", "trade": "BUY"},
        }

        action_info = action_map.get(action_idx, action_map[2])
        trade_action = action_info.get("trade", None)

        # Infer trade from charge/discharge + net_kw
        if trade_action is None:
            net_kw = observation.get("net_kw", 0)
            if action_info["name"] in ("charge_small", "charge_large") and net_kw < -0.3:
                trade_action = "BUY"
            elif action_info["name"] in ("discharge_small", "discharge_large") and net_kw > 0.3:
                trade_action = "SELL"

        result = {
            "action_index": action_idx,
            "action_name": action_info["name"],
            "action_kw": action_info["kw"],
            "decision": action_info["decision"],
            "trade_action": trade_action,
            "confidence": confidence,
            "probabilities": probs.tolist(),
            "selected_policy": selection.policy.value,
            "policy_reason": selection.reason,
            "condition": selection.condition.condition.value,
            "condition_confidence": selection.condition.confidence,
            "volatility": selection.condition.volatility,
            "sub_conditions": selection.condition.sub_conditions,
            "sensor_health": selection.condition.metrics.get("sensor_health", 1.0),
            "anomaly_score": selection.condition.metrics.get("anomaly_score", 0.0),
            "grid_risk": selection.condition.metrics.get("grid_risk", 0.0),
        }

        # 7. Environmental action override
        self._apply_environmental_override(result, observation, selection.policy)

        # 8. Clamp kW by model magnitude limits
        self._apply_model_kw_limits(result, selection.policy)

        return result

    # ------------------------------------------------------------------
    # Environmental override
    # ------------------------------------------------------------------

    def _apply_environmental_override(
        self, result: Dict[str, Any], obs: Dict[str, float], policy: PolicyType
    ):
        """
        Post-model override: adjust action based on physical environment.
        Ensures idle doesn't dominate, and actions match real conditions.
        Each model tier has distinct override behavior:
          BC:  conservative — only override for clear surplus/deficit, small kW
          CQL: moderate — override idle when signals are present
          DT:  aggressive — exploit surplus/deficit for optimal trading
        """
        net_kw = obs.get("net_kw", 0.0)
        soc_kwh = obs.get("soc_kwh", 2.0)
        soc_cap = obs.get("soc_capacity_kwh", 4.0)
        soc_frac = soc_kwh / soc_cap if soc_cap > 0 else 0.5
        price = obs.get("price_signal", self.thresholds.base_price)
        base_price = self.thresholds.base_price

        decision = result["decision"]
        trade = result["trade_action"]
        override_reason = None

        # ── Rule 1: Critical low SoC → force CHARGE / BUY (ALL tiers) ────
        if soc_frac < self.thresholds.low_soc_charge_threshold:
            if decision in ("DISCHARGE", "SELL"):
                if net_kw < self.thresholds.deficit_threshold:
                    result["decision"] = "BUY"
                    result["trade_action"] = "BUY"
                    result["action_name"] = "buy_energy"
                    result["action_index"] = 6
                    result["action_kw"] = min(abs(net_kw), 1.5)
                else:
                    result["decision"] = "CHARGE"
                    result["trade_action"] = None
                    result["action_name"] = "charge_small"
                    result["action_index"] = 0
                    result["action_kw"] = abs(result["action_kw"])  # positive = charge
                override_reason = f"low_soc({soc_frac:.1%})→force charge/buy"
            elif decision == "HOLD" and net_kw < self.thresholds.deficit_threshold:
                result["decision"] = "BUY"
                result["trade_action"] = "BUY"
                result["action_name"] = "buy_energy"
                result["action_index"] = 6
                result["action_kw"] = min(abs(net_kw), 1.5)
                override_reason = f"low_soc({soc_frac:.1%})+deficit→buy"

        # ── Rule 2: High SoC + favorable price → prefer SELL / DISCHARGE (ALL tiers) ──
        elif soc_frac > self.thresholds.high_soc_sell_threshold:
            if price >= base_price * 1.2:
                if decision in ("CHARGE", "HOLD"):
                    result["decision"] = "SELL"
                    result["trade_action"] = "SELL"
                    result["action_name"] = "offer_sell"
                    result["action_index"] = 5
                    result["action_kw"] = -min(abs(net_kw) if net_kw > 0 else 1.0, 2.0)
                    override_reason = f"high_soc({soc_frac:.1%})+good_price→sell"
            elif decision == "CHARGE":
                result["decision"] = "HOLD"
                result["trade_action"] = None
                result["action_name"] = "idle"
                result["action_index"] = 2
                result["action_kw"] = 0.0
                override_reason = f"high_soc({soc_frac:.1%})→stop charging"

        # ── Rule 3: Clear surplus + idle → trade (tier-dependent aggressiveness) ──
        surplus_thresh = self.thresholds.surplus_threshold
        deficit_thresh = self.thresholds.deficit_threshold

        if net_kw > surplus_thresh and soc_frac >= 0.35:
            if result["trade_action"] is None:
                if policy == PolicyType.DT:
                    # DT: aggressive sell
                    result["decision"] = "SELL"
                    result["trade_action"] = "SELL"
                    result["action_name"] = "offer_sell"
                    result["action_index"] = 5
                    result["action_kw"] = -min(net_kw * 0.8, 3.0)
                    override_reason = f"DT:surplus({net_kw:.1f}kW)→sell"
                elif policy == PolicyType.CQL:
                    # CQL: moderate sell
                    result["decision"] = "SELL"
                    result["trade_action"] = "SELL"
                    result["action_name"] = "offer_sell"
                    result["action_index"] = 5
                    result["action_kw"] = -min(net_kw * 0.6, 2.0)
                    override_reason = f"CQL:surplus({net_kw:.1f}kW)→sell"
                elif policy == PolicyType.BC and net_kw > surplus_thresh * 1.5:
                    # BC: cautious sell only on strong surplus
                    result["decision"] = "SELL"
                    result["trade_action"] = "SELL"
                    result["action_name"] = "offer_sell"
                    result["action_index"] = 5
                    result["action_kw"] = -min(net_kw * 0.4, 1.0)
                    override_reason = f"BC:strong_surplus({net_kw:.1f}kW)→sell"
                elif result["decision"] == "HOLD" and soc_frac < 0.85:
                    # Still no trade, at least charge
                    result["decision"] = "CHARGE"
                    result["action_name"] = "charge_small"
                    result["action_index"] = 0
                    result["action_kw"] = min(net_kw * 0.5, 1.5)
                    override_reason = f"surplus({net_kw:.1f}kW)+idle→charge"

        # ── Rule 4: Clear deficit + idle → trade (tier-dependent) ──
        if net_kw < deficit_thresh and soc_frac <= 0.75:
            if result["trade_action"] is None:
                if policy == PolicyType.DT:
                    result["decision"] = "BUY"
                    result["trade_action"] = "BUY"
                    result["action_name"] = "buy_energy"
                    result["action_index"] = 6
                    result["action_kw"] = min(abs(net_kw) * 0.8, 3.0)
                    override_reason = f"DT:deficit({net_kw:.1f}kW)→buy"
                elif policy == PolicyType.CQL:
                    result["decision"] = "BUY"
                    result["trade_action"] = "BUY"
                    result["action_name"] = "buy_energy"
                    result["action_index"] = 6
                    result["action_kw"] = min(abs(net_kw) * 0.6, 2.0)
                    override_reason = f"CQL:deficit({net_kw:.1f}kW)→buy"
                elif policy == PolicyType.BC and net_kw < deficit_thresh * 1.5:
                    result["decision"] = "BUY"
                    result["trade_action"] = "BUY"
                    result["action_name"] = "buy_energy"
                    result["action_index"] = 6
                    result["action_kw"] = min(abs(net_kw) * 0.4, 1.0)
                    override_reason = f"BC:strong_deficit({net_kw:.1f}kW)→buy"
                elif result["decision"] == "HOLD" and soc_frac > 0.15:
                    # Discharge battery to cover deficit
                    result["decision"] = "DISCHARGE"
                    result["action_name"] = "discharge_small"
                    result["action_index"] = 3
                    result["action_kw"] = -min(abs(net_kw) * 0.5, 1.5)
                    override_reason = f"deficit({net_kw:.1f}kW)+idle→discharge"

        if override_reason:
            result["environmental_override"] = override_reason
            logger.debug(f"Environmental override: {override_reason}")

    def _apply_model_kw_limits(self, result: Dict[str, Any], policy: PolicyType):
        """Clamp action kW magnitude based on the selected model's risk tier"""
        if policy == PolicyType.BC:
            limit = self.thresholds.bc_max_kw
        elif policy == PolicyType.CQL:
            limit = self.thresholds.cql_max_kw
        else:
            limit = self.thresholds.dt_max_kw

        result["action_kw"] = float(np.clip(result["action_kw"], -limit, limit))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_observation(self, obs: Dict[str, float]) -> np.ndarray:
        """Build observation array from dict"""
        import math
        import time

        ts = obs.get("timestamp", time.time())
        hour = (ts % 86400) / 3600
        day = (ts % (86400 * 7)) / 86400

        return np.array([
            obs.get("soc_kwh", 2.0),
            obs.get("soc_capacity_kwh", 4.0),
            obs.get("pv_gen_kw", 0.0),
            obs.get("load_kw", 0.5),
            obs.get("net_kw", 0.0),
            obs.get("battery_power_kw", 0.0),
            obs.get("price_signal", 5.0),
            obs.get("forecast_irradiance_1h", 300),
            obs.get("forecast_irradiance_3h", 300),
            obs.get("forecast_temp_1h", 25),
            obs.get("actual_irradiance_wm2", 400),
            obs.get("voltage_v", 230.0),
            obs.get("current_a", 0.0),
            math.sin(2 * math.pi * hour / 24),
            math.cos(2 * math.pi * hour / 24),
            math.sin(2 * math.pi * day / 7),
            math.cos(2 * math.pi * day / 7),
            obs.get("neighbor_balance", 0.0),
        ], dtype=np.float32)

    def _rule_based_action(
        self,
        observation: Dict[str, float],
        selection: SelectionResult
    ) -> Dict[str, Any]:
        """Fallback rule-based action when no models available"""
        soc_frac = observation.get("soc_kwh", 2.0) / observation.get("soc_capacity_kwh", 4.0)
        net_kw = observation.get("net_kw", 0.0)
        price = observation.get("price_signal", self.thresholds.base_price)

        trade_action = None

        if net_kw > 0.5 and soc_frac >= 0.5:
            action = {"name": "offer_sell", "kw": -min(net_kw * 0.8, 2.0), "decision": "SELL", "idx": 5}
            trade_action = "SELL"
        elif net_kw > 0.3 and soc_frac < 0.85:
            action = {"name": "charge_small", "kw": 1.0, "decision": "CHARGE", "idx": 0}
        elif net_kw < -0.5 and soc_frac <= 0.6:
            action = {"name": "buy_energy", "kw": min(abs(net_kw) * 0.8, 2.0), "decision": "BUY", "idx": 6}
            trade_action = "BUY"
        elif net_kw < -0.3 and soc_frac > 0.2:
            action = {"name": "discharge_small", "kw": -1.0, "decision": "DISCHARGE", "idx": 3}
        elif soc_frac > 0.85 and price >= self.thresholds.base_price * 1.3:
            action = {"name": "offer_sell", "kw": -1.0, "decision": "SELL", "idx": 5}
            trade_action = "SELL"
        else:
            action = {"name": "idle", "kw": 0.0, "decision": "HOLD", "idx": 2}

        return {
            "action_index": action["idx"],
            "action_name": action["name"],
            "action_kw": action["kw"],
            "decision": action["decision"],
            "trade_action": trade_action,
            "confidence": 0.6,
            "probabilities": [0.0] * 7,
            "selected_policy": "RULE_BASED",
            "policy_reason": "No models available - using rule-based fallback",
            "condition": selection.condition.condition.value,
            "condition_confidence": selection.condition.confidence,
            "volatility": selection.condition.volatility,
            "sub_conditions": selection.condition.sub_conditions,
            "sensor_health": selection.condition.metrics.get("sensor_health", 1.0),
            "anomaly_score": selection.condition.metrics.get("anomaly_score", 0.0),
            "grid_risk": selection.condition.metrics.get("grid_risk", 0.0),
        }

    def update_trust(self, policy: PolicyType, performance_delta: float):
        """Update trust score for a policy based on performance"""
        current = self.trust_scores[policy]
        self.trust_scores[policy] = 0.9 * current + 0.1 * (1.0 + performance_delta)
        self.trust_scores[policy] = max(0.5, min(1.5, self.trust_scores[policy]))

    def get_selection_stats(self) -> Dict[str, Any]:
        """Get selection statistics"""
        if not self.selection_history:
            return {"total": 0, "by_policy": {}, "by_condition": {}}

        by_policy = {}
        by_condition = {}

        for result in self.selection_history:
            p = result.policy.value
            c = result.condition.condition.value
            by_policy[p] = by_policy.get(p, 0) + 1
            by_condition[c] = by_condition.get(c, 0) + 1

        total = len(self.selection_history)
        return {
            "total": total,
            "by_policy": {k: v / total for k, v in by_policy.items()},
            "by_condition": {k: v / total for k, v in by_condition.items()},
            "trust_scores": {p.value: s for p, s in self.trust_scores.items()},
        }


# Singleton instance
_selector_instance: Optional[PolicySelector] = None


def get_policy_selector(models_dir: Optional[str] = None) -> PolicySelector:
    """Get or create policy selector singleton"""
    global _selector_instance
    if _selector_instance is None:
        _selector_instance = PolicySelector(models_dir)
        _selector_instance.load_models()
    return _selector_instance
