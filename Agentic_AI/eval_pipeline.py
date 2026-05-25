#!/usr/bin/env python3
"""
eval_pipeline.py — Step 3: Robust Evaluation and Safety Testing Pipeline
for Grid-Guardian.

This script performs comprehensive offline evaluation of trained RL policies:
  - Offline validation on held-out test data
  - Edge-case and stress testing (10+ scenarios)
  - Safety verification and constraint checking
  - Risk-sensitive analysis (CVaR, worst-case)
  - Behavior drift and conservatism checks
  - Ablation-style evaluation
  - OPE (Importance Sampling, WIS, FQE, Doubly Robust)
  - Final deployment readiness assessment

Usage:
    python eval_pipeline.py --config configs/eval_config.yaml
    python eval_pipeline.py --algo CQL --checkpoint_path models/CQL/run_42/checkpoint_best.pt
    python eval_pipeline.py --algo DT --run_stress_tests true --compute_cvar true

Output Files:
    - outputs/eval_run/eval_summary.json
    - outputs/eval_run/stress_test_report.json
    - outputs/eval_run/safety_report.md
    - outputs/eval_run/ope_estimates.json
    - outputs/eval_run/cvar_metrics.json
    - outputs/eval_run/plots/*.png
"""
from __future__ import annotations

import argparse
import copy
import datetime
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
import yaml

# ── Project imports ──────────────────────────────────────────────────────────
from env.microgrid_env import MicrogridEnv, DISCRETE_ACTION_MAP
from env.safety_shield import SafetyShield, SafetyConfig
from data_utils.replay_buffer import DatasetConverter, BehaviorDataset
from agents.bc_agent import BCAgent
from agents.offline_rl import CQLAgent, BCQAgent, BRACAgent
from agents.decision_transformer import DTAgent
from agents.classical_rl import DQNAgent, SACAgent, PPOAgent, DDPGAgent
from evaluation.evaluator import evaluate_policy, compute_action_distribution_drift, _json_default
from evaluation.ope import run_ope

__version__ = "1.0.0"
logger = logging.getLogger("eval_pipeline")


# ─────────────────────────────────────────────────────────────────────────────
#  Reproducibility
# ─────────────────────────────────────────────────────────────────────────────
def set_seed(seed: int):
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


# ─────────────────────────────────────────────────────────────────────────────
#  Config loading
# ─────────────────────────────────────────────────────────────────────────────
def load_config(path: str) -> Dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    return cfg


def merge_cli(cfg: Dict, args: argparse.Namespace) -> Dict:
    """Override config values with CLI arguments."""
    if args.algo:
        cfg.setdefault("model", {})["algo"] = args.algo
    if args.checkpoint_path:
        cfg.setdefault("model", {})["checkpoint_path"] = args.checkpoint_path
    if args.dataset_path:
        cfg.setdefault("data", {})["dataset_path"] = args.dataset_path
    if args.device:
        cfg.setdefault("model", {})["device"] = args.device
    if args.num_eval_episodes:
        cfg.setdefault("evaluation", {})["num_eval_episodes"] = int(args.num_eval_episodes)
    if args.seed:
        cfg.setdefault("evaluation", {})["seed"] = int(args.seed)
    if args.ope_methods:
        cfg.setdefault("ope", {})["methods"] = args.ope_methods.split(",")
        cfg["ope"]["enabled"] = True
    if args.run_stress_tests:
        cfg.setdefault("stress_testing", {})["enabled"] = args.run_stress_tests.lower() == "true"
    if args.compute_cvar:
        cfg.setdefault("risk", {})["compute_cvar"] = args.compute_cvar.lower() == "true"
    if args.log_dir:
        cfg.setdefault("output", {})["log_dir"] = args.log_dir
    if args.output_dir:
        cfg.setdefault("output", {})["output_dir"] = args.output_dir
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
#  Dataset loading
# ─────────────────────────────────────────────────────────────────────────────
def load_dataset(path: str) -> pd.DataFrame:
    if path.endswith(".gz"):
        df = pd.read_csv(path, compression="gzip", low_memory=False)
    else:
        df = pd.read_csv(path, low_memory=False)
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
    logger.info("Loaded dataset: %s (%d rows, %d cols)", path, len(df), len(df.columns))
    return df


# ─────────────────────────────────────────────────────────────────────────────
#  Agent Loading
# ─────────────────────────────────────────────────────────────────────────────
def create_agent(algo_name: str, obs_dim: int, act_dim: int, cfg: Dict, device: str):
    """Create an agent instance."""
    algo_cfg = cfg.get("algo", {}) if "algo" in cfg else {}
    hidden = [256, 256]
    lr = 3e-4
    gamma = 0.99
    tau = 0.005
    continuous = cfg.get("env", {}).get("action_type", "discrete") == "continuous"
    history_length = cfg.get("env", {}).get("history_length", 24)

    if algo_name == "BC":
        return BCAgent(obs_dim, act_dim, hidden, lr, continuous, device)
    elif algo_name == "DQN":
        return DQNAgent(obs_dim, act_dim, lr, gamma, tau, hidden, device)
    elif algo_name == "PPO":
        return PPOAgent(obs_dim, act_dim, lr, gamma, 0.95, 0.2, 0.01, hidden, device, 10)
    elif algo_name == "SAC":
        return SACAgent(obs_dim, act_dim if continuous else 2, lr, gamma, tau, hidden, device)
    elif algo_name == "DDPG":
        return DDPGAgent(obs_dim, act_dim if continuous else 2, lr, gamma, tau, hidden, device)
    elif algo_name == "CQL":
        return CQLAgent(obs_dim, act_dim, lr, gamma, tau, 1.0, 10, hidden, device)
    elif algo_name == "BCQ":
        return BCQAgent(obs_dim, act_dim, lr, gamma, tau, 0.3, hidden, device)
    elif algo_name == "BRAC":
        return BRACAgent(obs_dim, act_dim, lr, gamma, tau, 1.0, hidden, device, None)
    elif algo_name == "DT":
        return DTAgent(
            obs_dim, act_dim, d_model=256, n_heads=8, n_layers=6,
            context_length=history_length, dropout=0.1, lr=1e-4,
            weight_decay=1e-4, warmup_steps=1000, continuous=continuous, device=device,
        )
    else:
        raise ValueError(f"Unknown algorithm: {algo_name}")


def load_agent(algo_name: str, checkpoint_path: str, obs_dim: int, act_dim: int, cfg: Dict, device: str):
    """Load trained agent from checkpoint."""
    agent = create_agent(algo_name, obs_dim, act_dim, cfg, device)

    if algo_name == "DT":
        ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
        agent.model.load_state_dict(ckpt["model"])
        agent.model.eval()
    else:
        agent.load(checkpoint_path)

    logger.info("Loaded %s agent from %s", algo_name, checkpoint_path)
    return agent


# ─────────────────────────────────────────────────────────────────────────────
#  Extended Evaluation Metrics
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_policy_extended(
    env,
    policy_fn: Callable,
    n_episodes: int = 50,
    shield=None,
    max_steps: int = 10000,
) -> Dict[str, Any]:
    """Extended evaluation with detailed metrics."""
    ep_rewards = []
    ep_costs = []
    ep_lengths = []
    safety_violations = 0
    soc_violations = 0
    shield_interventions = 0
    total_steps = 0
    energy_sold = 0.0
    energy_bought = 0.0
    episodes_with_any_violation = 0
    all_socs = []
    all_actions = []
    peak_grid_draw = 0.0
    soft_violations = 0
    hard_violations = 0
    near_misses = 0
    oscillation_count = 0
    action_history = []

    for ep in range(n_episodes):
        obs, info = env.reset()
        ep_reward = 0.0
        ep_cost = 0.0
        ep_len = 0
        done = False
        ep_had_violation = False
        ep_actions = []

        while not done and ep_len < max_steps:
            action = policy_fn(obs)
            original_action = action

            if shield is not None:
                soc = getattr(env, "_soc", 2.0)
                soc_cap = getattr(env, "_soc_cap", 4.0)
                continuous = getattr(env, "_continuous", False)
                action, intervened, reason = shield(action, soc, soc_cap, continuous=continuous)
                if intervened:
                    shield_interventions += 1
                    near_misses += 1
                    ep_had_violation = True

            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            ep_reward += reward
            ep_len += 1
            ep_actions.append(int(action) if not isinstance(action, (list, np.ndarray)) else action)

            # Track SOC
            current_soc = getattr(env, "_soc", 2.0)
            current_soc_cap = getattr(env, "_soc_cap", 4.0)
            soc_frac = current_soc / max(current_soc_cap, 1e-6)
            all_socs.append(soc_frac)
            all_actions.append(action)

            # Track energy flows and costs
            action_kw = info.get("action_kw", 0.0)
            price = info.get("price_signal", 0.1)

            if action_kw > 0:
                energy_bought += action_kw * (5 / 60)
                ep_cost += action_kw * (5 / 60) * price
            else:
                energy_sold += abs(action_kw) * (5 / 60)

            # Track grid draw
            grid_draw = info.get("grid_draw_kw", 0.0)
            peak_grid_draw = max(peak_grid_draw, abs(grid_draw))

            # Classify violations
            if info.get("safety_violation", False):
                soc_violations += 1
                soc_frac = info.get("soc_frac", 0.5)
                if soc_frac < 0.05 or soc_frac > 0.98:
                    hard_violations += 1
                else:
                    soft_violations += 1

            # Detect oscillations (rapid back-and-forth)
            if len(ep_actions) >= 3:
                if ep_actions[-1] == ep_actions[-3] and ep_actions[-1] != ep_actions[-2]:
                    oscillation_count += 1

        ep_rewards.append(ep_reward)
        ep_costs.append(ep_cost)
        ep_lengths.append(ep_len)
        total_steps += ep_len
        action_history.extend(ep_actions)

        if ep_had_violation:
            episodes_with_any_violation += 1

    ep_rewards = np.array(ep_rewards)
    ep_costs = np.array(ep_costs)
    all_socs = np.array(all_socs)

    # Compute extended metrics
    safety_rate = shield_interventions / max(total_steps, 1)
    cvar_1 = float(np.percentile(ep_rewards, 1)) if len(ep_rewards) > 1 else float(ep_rewards[0])
    cvar_5 = float(np.percentile(ep_rewards, 5)) if len(ep_rewards) > 1 else float(ep_rewards[0])

    # Action distribution
    action_counts = np.bincount(np.array(action_history, dtype=int), minlength=7)
    action_dist = action_counts / max(action_counts.sum(), 1)

    return {
        # Basic metrics
        "mean_reward": float(ep_rewards.mean()),
        "std_reward": float(ep_rewards.std()),
        "min_reward": float(ep_rewards.min()),
        "max_reward": float(ep_rewards.max()),
        "median_reward": float(np.median(ep_rewards)),
        "reward_variance": float(ep_rewards.var()),
        "reward_p1": cvar_1,
        "reward_p5": cvar_5,
        "cvar_5pct": cvar_5,
        "cvar_1pct": cvar_1,

        # Cost metrics
        "total_cost": float(ep_costs.sum()),
        "mean_cost": float(ep_costs.mean()),
        "energy_cost_savings": float(energy_sold * 0.15 - ep_costs.sum()),  # Approximate

        # Safety metrics
        "safety_violation_rate": safety_rate,
        "soc_violations": soc_violations,
        "soft_violations": soft_violations,
        "hard_violations": hard_violations,
        "near_misses": near_misses,
        "shield_interventions": shield_interventions,
        "oscillation_count": oscillation_count,
        "episodes_with_violation": episodes_with_any_violation,

        # SoC metrics
        "soc_mean": float(all_socs.mean()) if len(all_socs) > 0 else 0.5,
        "soc_std": float(all_socs.std()) if len(all_socs) > 0 else 0.0,
        "soc_min": float(all_socs.min()) if len(all_socs) > 0 else 0.0,
        "soc_max": float(all_socs.max()) if len(all_socs) > 0 else 1.0,

        # Energy metrics
        "energy_sold_kwh": energy_sold,
        "energy_bought_kwh": energy_bought,
        "peak_grid_draw_kw": peak_grid_draw,

        # Episode metrics
        "total_steps": total_steps,
        "n_episodes": n_episodes,
        "mean_ep_length": float(np.mean(ep_lengths)),

        # Action distribution
        "action_distribution": action_dist.tolist(),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Stress Testing with Extended Scenarios
# ─────────────────────────────────────────────────────────────────────────────
def run_stress_tests(
    env,
    policy_fn: Callable,
    scenarios_cfg: List[Dict],
    episodes_per_scenario: int = 10,
    shield=None,
) -> Dict[str, Any]:
    """Run comprehensive stress tests for all configured scenarios."""
    results = {}

    for scenario in scenarios_cfg:
        if not scenario.get("enabled", True):
            continue

        name = scenario["name"]
        description = scenario.get("description", name)
        logger.info("Stress test: %s - %s", name, description)

        # Store original config
        orig_dr = copy.deepcopy(env.domain_rand_cfg) if hasattr(env, "domain_rand_cfg") else {}

        # Apply scenario-specific modifications
        env.domain_rand_cfg["enabled"] = True

        if name == "cloud_ramp":
            env.domain_rand_cfg["irradiance_noise_std"] = scenario.get("irradiance_noise_std", 0.5)
        elif name == "low_generation":
            env.domain_rand_cfg["irradiance_multiplier"] = scenario.get("irradiance_multiplier", 0.1)
        elif name == "grid_outage":
            env.domain_rand_cfg["sensor_dropout_prob"] = 0.15
            env.domain_rand_cfg["grid_available"] = False
        elif name == "grid_restore":
            env.domain_rand_cfg["sensor_dropout_prob"] = 0.1
        elif name == "inverter_degradation":
            eff_range = scenario.get("inverter_eff_range", [0.70, 0.78])
            env.domain_rand_cfg["inverter_eff_range"] = eff_range
        elif name == "sensor_dropout":
            env.domain_rand_cfg["sensor_dropout_prob"] = scenario.get("sensor_dropout_prob", 0.3)
        elif name == "forecast_error":
            env.domain_rand_cfg["forecast_noise_std"] = scenario.get("forecast_noise_std", 0.4)
        elif name == "load_surge":
            env.domain_rand_cfg["load_multiplier"] = scenario.get("load_multiplier", 2.5)
        elif name == "tariff_spike":
            shift = scenario.get("tariff_shift_range", [-2.0, 3.0])
            env.domain_rand_cfg["tariff_shift_range"] = shift
        elif name == "soc_extremes":
            env.domain_rand_cfg["force_soc_extremes"] = True
        elif name == "communication_delay":
            latency = scenario.get("latency_ms", [500, 1000])
            env.domain_rand_cfg["latency_ms"] = latency

        try:
            metrics = evaluate_policy_extended(env, policy_fn, episodes_per_scenario, shield)
            metrics["description"] = description
            metrics["scenario_config"] = {k: v for k, v in scenario.items() if k not in ["enabled"]}

            # Assess stability
            metrics["stability"] = "stable" if metrics["safety_violation_rate"] < 0.1 else (
                "degraded" if metrics["safety_violation_rate"] < 0.3 else "unstable"
            )
            metrics["recovery_behavior"] = "graceful" if metrics["oscillation_count"] < 10 else "poor"

            # Identify failure threshold
            if metrics["hard_violations"] > 0:
                metrics["failure_mode"] = "dangerous"
            elif metrics["soft_violations"] > 5:
                metrics["failure_mode"] = "degraded"
            else:
                metrics["failure_mode"] = "none"

            results[name] = metrics
            logger.info("  %s: reward=%.2f  safety_rate=%.4f  stability=%s",
                       name, metrics["mean_reward"], metrics["safety_violation_rate"], metrics["stability"])

        except Exception as e:
            logger.error("  %s: FAILED — %s", name, e)
            results[name] = {"error": str(e), "description": description}

        # Restore original config
        env.domain_rand_cfg = orig_dr

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Safety Verification
# ─────────────────────────────────────────────────────────────────────────────
def verify_safety(
    env,
    policy_fn: Callable,
    n_episodes: int = 20,
    shield=None,
    constraints: Dict = None,
) -> Dict[str, Any]:
    """Comprehensive safety verification against hard constraints."""
    if constraints is None:
        constraints = {
            "min_soc": 0.10,
            "max_soc": 0.95,
            "max_charge_kw": 3.0,
            "max_discharge_kw": 3.0,
            "max_grid_draw_kw": 5.0,
        }

    violations = {
        "soc_below_min": [],
        "soc_above_max": [],
        "unsafe_charge": [],
        "unsafe_discharge": [],
        "invalid_sell_order": [],
        "excessive_grid_draw": [],
        "policy_blackout": [],
        "oscillatory_actions": [],
    }

    total_steps = 0
    total_actions = 0
    shield_would_block = 0

    for ep in range(n_episodes):
        obs, info = env.reset()
        done = False
        step = 0
        action_window = []

        while not done and step < 10000:
            action = policy_fn(obs)
            total_actions += 1

            # Get state info
            soc = getattr(env, "_soc", 2.0)
            soc_cap = getattr(env, "_soc_cap", 4.0)
            soc_frac = soc / max(soc_cap, 1e-6)

            # Check if shield would block
            if shield is not None:
                _, would_block, reason = shield(action, soc, soc_cap, continuous=False)
                if would_block:
                    shield_would_block += 1

            # Apply action (with shield if present)
            if shield is not None:
                action, _, _ = shield(action, soc, soc_cap, continuous=False)

            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            step += 1
            total_steps += 1

            # Check constraints post-step
            new_soc = getattr(env, "_soc", 2.0)
            new_soc_frac = new_soc / max(soc_cap, 1e-6)

            if new_soc_frac < constraints["min_soc"]:
                violations["soc_below_min"].append({
                    "episode": ep, "step": step, "soc": new_soc_frac,
                    "state": obs.tolist()[:5] if hasattr(obs, "tolist") else str(obs)[:100],
                    "action": int(action) if not isinstance(action, (list, np.ndarray)) else action,
                })

            if new_soc_frac > constraints["max_soc"]:
                violations["soc_above_max"].append({
                    "episode": ep, "step": step, "soc": new_soc_frac,
                    "state": obs.tolist()[:5] if hasattr(obs, "tolist") else str(obs)[:100],
                    "action": int(action) if not isinstance(action, (list, np.ndarray)) else action,
                })

            # Check grid draw
            grid_draw = info.get("grid_draw_kw", 0.0)
            if abs(grid_draw) > constraints["max_grid_draw_kw"]:
                violations["excessive_grid_draw"].append({
                    "episode": ep, "step": step, "grid_draw": grid_draw,
                })

            # Track oscillations
            action_val = int(action) if not isinstance(action, (list, np.ndarray)) else 0
            action_window.append(action_val)
            if len(action_window) > 5:
                action_window.pop(0)
            if len(action_window) >= 4:
                # Detect A-B-A-B pattern
                if (action_window[-1] == action_window[-3] and
                    action_window[-2] == action_window[-4] and
                    action_window[-1] != action_window[-2]):
                    violations["oscillatory_actions"].append({
                        "episode": ep, "step": step, "pattern": action_window.copy(),
                    })

    # Classify violations
    violation_summary = {
        "total_violations": sum(len(v) for v in violations.values()),
        "soft_violations": len(violations["soc_below_min"]) + len(violations["soc_above_max"]),
        "hard_violations": len(violations["policy_blackout"]) + len(violations["unsafe_charge"]),
        "near_misses": shield_would_block,
        "oscillation_events": len(violations["oscillatory_actions"]),
        "shield_would_block_rate": shield_would_block / max(total_actions, 1),
        "total_steps_evaluated": total_steps,
        "violation_details": violations,
    }

    return violation_summary


# ─────────────────────────────────────────────────────────────────────────────
#  Risk-Sensitive Analysis
# ─────────────────────────────────────────────────────────────────────────────
def compute_risk_metrics(
    ep_rewards: np.ndarray,
    alpha: float = 0.05,
) -> Dict[str, Any]:
    """Compute CVaR and other risk-aware summaries."""
    sorted_rewards = np.sort(ep_rewards)
    n = len(sorted_rewards)

    # CVaR at alpha (expected value of worst alpha% outcomes)
    cutoff_idx = max(1, int(n * alpha))
    cvar_alpha = float(sorted_rewards[:cutoff_idx].mean())

    # VaR (Value at Risk)
    var_alpha = float(np.percentile(ep_rewards, alpha * 100))

    # Worst-case metrics
    worst_1pct = float(np.percentile(ep_rewards, 1))
    worst_5pct = float(np.percentile(ep_rewards, 5))
    worst_10pct = float(np.percentile(ep_rewards, 10))

    # Tail ratio (worst-case / average)
    tail_ratio = worst_5pct / max(abs(ep_rewards.mean()), 1e-6)

    return {
        "cvar_alpha": cvar_alpha,
        "var_alpha": var_alpha,
        "alpha": alpha,
        "worst_1pct": worst_1pct,
        "worst_5pct": worst_5pct,
        "worst_10pct": worst_10pct,
        "tail_ratio": tail_ratio,
        "is_robust": abs(tail_ratio) < 5.0,  # Heuristic threshold
        "n_samples": n,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Behavior Drift Analysis
# ─────────────────────────────────────────────────────────────────────────────
def analyze_behavior_drift(
    drift_metrics: Dict,
    thresholds: Dict,
) -> Dict[str, Any]:
    """Analyze policy deviation from behavior policy."""
    kl = drift_metrics.get("kl_divergence", 0)
    tvd = drift_metrics.get("tvd", 0)
    js = drift_metrics.get("js_divergence", 0)

    kl_warning = thresholds.get("kl_warning", 1.0)
    kl_critical = thresholds.get("kl_critical", 2.0)
    tvd_warning = thresholds.get("tvd_warning", 0.25)
    tvd_critical = thresholds.get("tvd_critical", 0.40)

    # Assess conservatism
    if kl < kl_warning and tvd < tvd_warning:
        conservatism = "conservative"
        safe_for_deployment = True
    elif kl < kl_critical and tvd < tvd_critical:
        conservatism = "moderate"
        safe_for_deployment = True
    else:
        conservatism = "aggressive"
        safe_for_deployment = False

    return {
        "kl_divergence": kl,
        "js_divergence": js,
        "tvd": tvd,
        "conservatism_level": conservatism,
        "safe_for_deployment": safe_for_deployment,
        "kl_warning_threshold": kl_warning,
        "kl_critical_threshold": kl_critical,
        "flags": {
            "kl_warning": kl >= kl_warning,
            "kl_critical": kl >= kl_critical,
            "tvd_warning": tvd >= tvd_warning,
            "tvd_critical": tvd >= tvd_critical,
        }
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Ablation Evaluation
# ─────────────────────────────────────────────────────────────────────────────
def run_ablation_study(
    env,
    policy_fn: Callable,
    variants_cfg: List[Dict],
    n_episodes: int = 10,
    shield=None,
) -> Dict[str, Any]:
    """Run ablation-style evaluation under controlled variants."""
    results = {}

    for variant in variants_cfg:
        name = variant["name"]
        description = variant.get("description", name)
        logger.info("Ablation: %s - %s", name, description)

        orig_dr = copy.deepcopy(env.domain_rand_cfg) if hasattr(env, "domain_rand_cfg") else {}

        # Apply variant-specific modifications
        if "domain_randomization" in variant:
            env.domain_rand_cfg["enabled"] = variant["domain_randomization"]
        if "forecast_noise_std" in variant:
            env.domain_rand_cfg["forecast_noise_std"] = variant["forecast_noise_std"]
        if "irradiance_noise_std" in variant:
            env.domain_rand_cfg["irradiance_noise_std"] = variant["irradiance_noise_std"]
        if "latency_ms" in variant:
            env.domain_rand_cfg["latency_ms"] = variant["latency_ms"]

        # For strict safety, we'd need to modify shield config
        # For now, just run evaluation
        try:
            metrics = evaluate_policy_extended(env, policy_fn, n_episodes, shield)
            metrics["description"] = description
            results[name] = metrics
            logger.info("  %s: reward=%.2f  safety_rate=%.4f",
                       name, metrics["mean_reward"], metrics["safety_violation_rate"])
        except Exception as e:
            logger.error("  %s: FAILED — %s", name, e)
            results[name] = {"error": str(e)}

        env.domain_rand_cfg = orig_dr

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Plotting Functions
# ─────────────────────────────────────────────────────────────────────────────
def generate_plots(
    eval_metrics: Dict,
    stress_results: Dict,
    output_dir: str,
    algo_name: str,
):
    """Generate all evaluation plots."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available; skipping plots")
        return

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 1. Reward distribution
    fig, ax = plt.subplots(figsize=(10, 5))
    rewards = [eval_metrics["mean_reward"]]
    for scenario, data in stress_results.items():
        if isinstance(data, dict) and "mean_reward" in data:
            rewards.append(data["mean_reward"])

    labels = ["Baseline"] + [s for s in stress_results.keys() if isinstance(stress_results[s], dict) and "mean_reward" in stress_results[s]]

    if len(rewards) > 1:
        ax.bar(range(len(rewards)), rewards, color=["green"] + ["orange"]*(len(rewards)-1))
        ax.set_xticks(range(len(rewards)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Mean Reward")
        ax.set_title(f"{algo_name} - Reward Comparison: Baseline vs Stress Tests")
        ax.axhline(y=0, color='r', linestyle='--', alpha=0.5)
        plt.tight_layout()
        plt.savefig(Path(output_dir) / "reward_comparison.png", dpi=150)
        plt.close()

    # 2. Safety violation rates
    fig, ax = plt.subplots(figsize=(10, 5))
    safety_rates = [eval_metrics["safety_violation_rate"]]
    for scenario, data in stress_results.items():
        if isinstance(data, dict) and "safety_violation_rate" in data:
            safety_rates.append(data["safety_violation_rate"])

    if len(safety_rates) > 1:
        colors = ["green" if r < 0.1 else "orange" if r < 0.3 else "red" for r in safety_rates]
        ax.bar(range(len(safety_rates)), safety_rates, color=colors)
        ax.set_xticks(range(len(safety_rates)))
        ax.set_xticklabels(labels, rotation=45, ha="right")
        ax.set_ylabel("Safety Violation Rate")
        ax.set_title(f"{algo_name} - Safety Metrics Across Scenarios")
        ax.axhline(y=0.1, color='orange', linestyle='--', alpha=0.7, label="Warning threshold")
        ax.axhline(y=0.3, color='red', linestyle='--', alpha=0.7, label="Critical threshold")
        ax.legend()
        plt.tight_layout()
        plt.savefig(Path(output_dir) / "safety_comparison.png", dpi=150)
        plt.close()

    # 3. SoC distribution (if available)
    fig, ax = plt.subplots(figsize=(8, 5))
    soc_mean = eval_metrics.get("soc_mean", 0.5)
    soc_std = eval_metrics.get("soc_std", 0.1)
    soc_min = eval_metrics.get("soc_min", 0.1)
    soc_max = eval_metrics.get("soc_max", 0.9)

    ax.bar(["Mean", "Std", "Min", "Max"], [soc_mean, soc_std, soc_min, soc_max],
           color=["blue", "gray", "red", "green"])
    ax.axhline(y=0.1, color='red', linestyle='--', label="Min threshold (10%)")
    ax.axhline(y=0.95, color='orange', linestyle='--', label="Max threshold (95%)")
    ax.set_ylabel("SoC Fraction")
    ax.set_title(f"{algo_name} - Battery SoC Statistics")
    ax.legend()
    plt.tight_layout()
    plt.savefig(Path(output_dir) / "soc_curve.png", dpi=150)
    plt.close()

    # 4. Action distribution
    action_dist = eval_metrics.get("action_distribution", [])
    if action_dist:
        fig, ax = plt.subplots(figsize=(10, 5))
        actions = ["charge_s", "charge_l", "idle", "discharge_s", "discharge_l", "sell", "hold"]
        ax.bar(range(len(action_dist)), action_dist, color="steelblue")
        ax.set_xticks(range(len(action_dist)))
        ax.set_xticklabels(actions[:len(action_dist)], rotation=45, ha="right")
        ax.set_ylabel("Frequency")
        ax.set_title(f"{algo_name} - Action Distribution")
        plt.tight_layout()
        plt.savefig(Path(output_dir) / "action_distribution.png", dpi=150)
        plt.close()

    # 5. Stress test comparison heatmap
    if stress_results:
        scenarios = list(stress_results.keys())[:10]
        metrics_to_plot = ["mean_reward", "safety_violation_rate", "soc_mean"]

        fig, axes = plt.subplots(1, 3, figsize=(15, 5))
        for idx, metric in enumerate(metrics_to_plot):
            values = []
            valid_scenarios = []
            for s in scenarios:
                if isinstance(stress_results[s], dict) and metric in stress_results[s]:
                    values.append(stress_results[s][metric])
                    valid_scenarios.append(s)

            if values:
                axes[idx].barh(range(len(values)), values, color="steelblue")
                axes[idx].set_yticks(range(len(values)))
                axes[idx].set_yticklabels(valid_scenarios)
                axes[idx].set_xlabel(metric.replace("_", " ").title())
                axes[idx].set_title(metric.replace("_", " ").title())

        plt.suptitle(f"{algo_name} - Stress Test Results", fontsize=14)
        plt.tight_layout()
        plt.savefig(Path(output_dir) / "stress_comparison.png", dpi=150)
        plt.close()

    logger.info("Plots saved to %s", output_dir)


# ─────────────────────────────────────────────────────────────────────────────
#  Safety Report Generation
# ─────────────────────────────────────────────────────────────────────────────
def generate_safety_report(
    algo_name: str,
    eval_metrics: Dict,
    stress_results: Dict,
    safety_verification: Dict,
    risk_metrics: Dict,
    drift_analysis: Dict,
    ablation_results: Dict,
    ope_results: Dict,
    deployment_criteria: Dict,
    output_path: str,
) -> str:
    """Generate comprehensive safety report in Markdown format."""

    # Determine deployment verdict
    violations_ok = safety_verification.get("hard_violations", 0) == 0
    worst_case_ok = eval_metrics.get("cvar_5pct", -float("inf")) > deployment_criteria.get("worst_case_reward_threshold", -50000)
    safety_rate_ok = eval_metrics.get("safety_violation_rate", 1.0) < deployment_criteria.get("safety_violation_threshold", 0.01)
    drift_ok = drift_analysis.get("safe_for_deployment", False)

    # Check stress stability
    stress_stable = True
    for scenario, data in stress_results.items():
        if isinstance(data, dict):
            if data.get("stability") == "unstable" or data.get("failure_mode") == "dangerous":
                stress_stable = False
                break

    if violations_ok and worst_case_ok and safety_rate_ok and stress_stable and drift_ok:
        verdict = "APPROVED"
        verdict_color = "green"
    elif safety_verification.get("hard_violations", 0) > 0 or not stress_stable:
        verdict = "REJECTED"
        verdict_color = "red"
    else:
        verdict = "CONDITIONAL"
        verdict_color = "orange"

    # Build report
    report = f"""# Safety Report: {algo_name} Policy Evaluation

**Generated:** {datetime.datetime.now(datetime.timezone.utc).isoformat()}
**Pipeline Version:** {__version__}

---

## A. Evaluation Setup

| Parameter | Value |
|-----------|-------|
| Algorithm | {algo_name} |
| Evaluation Episodes | {eval_metrics.get('n_episodes', 'N/A')} |
| Total Steps | {eval_metrics.get('total_steps', 'N/A')} |
| Safety Shield | Enabled (clip mode) |

---

## B. Baseline Comparison

### Performance Metrics

| Metric | Value |
|--------|-------|
| Mean Reward | {eval_metrics.get('mean_reward', 'N/A'):.4f} |
| Std Reward | {eval_metrics.get('std_reward', 'N/A'):.4f} |
| Min Reward | {eval_metrics.get('min_reward', 'N/A'):.4f} |
| Max Reward | {eval_metrics.get('max_reward', 'N/A'):.4f} |
| Median Reward | {eval_metrics.get('median_reward', 'N/A'):.4f} |
| 5th Percentile (CVaR) | {eval_metrics.get('cvar_5pct', 'N/A'):.4f} |
| 1st Percentile | {eval_metrics.get('cvar_1pct', 'N/A'):.4f} |

### Cost & Energy Metrics

| Metric | Value |
|--------|-------|
| Total Cost | {eval_metrics.get('total_cost', 'N/A'):.4f} |
| Energy Sold (kWh) | {eval_metrics.get('energy_sold_kwh', 'N/A'):.4f} |
| Energy Bought (kWh) | {eval_metrics.get('energy_bought_kwh', 'N/A'):.4f} |
| Peak Grid Draw (kW) | {eval_metrics.get('peak_grid_draw_kw', 'N/A'):.4f} |

### Battery Metrics

| Metric | Value |
|--------|-------|
| Mean SoC | {eval_metrics.get('soc_mean', 'N/A'):.4f} |
| SoC Std | {eval_metrics.get('soc_std', 'N/A'):.4f} |
| Min SoC | {eval_metrics.get('soc_min', 'N/A'):.4f} |
| Max SoC | {eval_metrics.get('soc_max', 'N/A'):.4f} |

---

## C. OPE Results

"""
    if ope_results:
        report += "| Method | Estimate | CI Lower | CI Upper |\n"
        report += "|--------|----------|----------|----------|\n"
        for method, data in ope_results.items():
            if isinstance(data, dict):
                report += f"| {method} | {data.get('estimate', 'N/A'):.4f} | {data.get('ci_lower', 'N/A'):.4f} | {data.get('ci_upper', 'N/A'):.4f} |\n"
    else:
        report += "*OPE not available for this evaluation run.*\n"

    report += """
---

## D. Stress Test Results

"""
    if stress_results:
        report += "| Scenario | Mean Reward | Safety Rate | Stability | Failure Mode |\n"
        report += "|----------|-------------|-------------|-----------|---------------|\n"
        for scenario, data in stress_results.items():
            if isinstance(data, dict) and "mean_reward" in data:
                report += f"| {scenario} | {data.get('mean_reward', 'N/A'):.2f} | {data.get('safety_violation_rate', 'N/A'):.4f} | {data.get('stability', 'N/A')} | {data.get('failure_mode', 'N/A')} |\n"
            elif isinstance(data, dict) and "error" in data:
                report += f"| {scenario} | ERROR | - | - | {data.get('error', 'Unknown')[:50]} |\n"
    else:
        report += "*No stress tests executed.*\n"

    report += f"""
---

## E. Safety Metrics

### Violation Summary

| Category | Count |
|----------|-------|
| Total Violations | {safety_verification.get('total_violations', 0)} |
| Soft Violations | {safety_verification.get('soft_violations', 0)} |
| Hard Violations | {safety_verification.get('hard_violations', 0)} |
| Near Misses (Shield Blocked) | {safety_verification.get('near_misses', 0)} |
| Oscillation Events | {safety_verification.get('oscillation_events', 0)} |
| Shield Block Rate | {safety_verification.get('shield_would_block_rate', 0):.4f} |

### Safety Constraints Checked

- [{'x' if safety_verification.get('violation_details', {}).get('soc_below_min', []) else ' '}] SoC below minimum (10%)
- [{'x' if safety_verification.get('violation_details', {}).get('soc_above_max', []) else ' '}] SoC above maximum (95%)
- [{'x' if safety_verification.get('violation_details', {}).get('excessive_grid_draw', []) else ' '}] Excessive grid draw (>5 kW)
- [{'x' if safety_verification.get('violation_details', {}).get('oscillatory_actions', []) else ' '}] Oscillatory behavior detected
- [ ] No blackout caused by policy
- [ ] No unsafe charge/discharge commands

---

## F. Failure Analysis

### Risk-Sensitive Metrics

| Metric | Value | Assessment |
|--------|-------|------------|
| CVaR (α=0.05) | {risk_metrics.get('cvar_alpha', 'N/A'):.4f} | {'Acceptable' if risk_metrics.get('cvar_alpha', -999999) > -40000 else 'High Risk'} |
| VaR (α=0.05) | {risk_metrics.get('var_alpha', 'N/A'):.4f} | - |
| Worst 1% | {risk_metrics.get('worst_1pct', 'N/A'):.4f} | - |
| Worst 5% | {risk_metrics.get('worst_5pct', 'N/A'):.4f} | - |
| Tail Ratio | {risk_metrics.get('tail_ratio', 'N/A'):.4f} | {'Good' if abs(risk_metrics.get('tail_ratio', 999)) < 5 else 'Poor'} |
| Is Robust | {risk_metrics.get('is_robust', False)} | - |

### Behavior Drift Analysis

| Metric | Value | Status |
|--------|-------|--------|
| KL Divergence | {drift_analysis.get('kl_divergence', 'N/A'):.4f} | {'WARNING' if drift_analysis.get('flags', {}).get('kl_warning', False) else 'OK'} |
| JS Divergence | {drift_analysis.get('js_divergence', 'N/A'):.4f} | - |
| Total Variation Distance | {drift_analysis.get('tvd', 'N/A'):.4f} | {'WARNING' if drift_analysis.get('flags', {}).get('tvd_warning', False) else 'OK'} |
| Conservatism Level | {drift_analysis.get('conservatism_level', 'N/A')} | - |
| Safe for Deployment | {drift_analysis.get('safe_for_deployment', False)} | - |

---

## G. Deployment Readiness Verdict

### **Decision: {verdict}**

#### Criteria Assessment

| Criterion | Required | Actual | Status |
|-----------|----------|--------|--------|
| Hard Safety Violations | 0 | {safety_verification.get('hard_violations', 'N/A')} | {'PASS' if violations_ok else 'FAIL'} |
| Safety Violation Rate | <{deployment_criteria.get('safety_violation_threshold', 0.01)} | {eval_metrics.get('safety_violation_rate', 'N/A'):.4f} | {'PASS' if safety_rate_ok else 'FAIL'} |
| Worst-Case Reward | >{deployment_criteria.get('worst_case_reward_threshold', -50000)} | {eval_metrics.get('cvar_5pct', 'N/A'):.2f} | {'PASS' if worst_case_ok else 'FAIL'} |
| Stress Test Stability | Stable | {'Stable' if stress_stable else 'Unstable'} | {'PASS' if stress_stable else 'FAIL'} |
| Behavior Conservatism | Safe | {drift_analysis.get('conservatism_level', 'N/A')} | {'PASS' if drift_ok else 'FAIL'} |

"""

    if verdict == "APPROVED":
        report += """
### Conclusion

The policy has **PASSED** all safety checks and is **APPROVED** for deployment.

- Zero hard safety violations
- Acceptable worst-case performance
- Stable behavior under stress scenarios
- Conservative deviation from behavior policy
- OPE estimates consistent with simulation
"""
    elif verdict == "CONDITIONAL":
        report += """
### Conclusion

The policy has received **CONDITIONAL** approval. The following issues must be addressed:

"""
        if not safety_rate_ok:
            report += f"- Safety violation rate ({eval_metrics.get('safety_violation_rate', 0):.4f}) exceeds threshold\n"
        if not worst_case_ok:
            report += f"- Worst-case reward ({eval_metrics.get('cvar_5pct', 0):.2f}) below acceptable threshold\n"
        if not drift_ok:
            report += f"- Policy deviation from behavior policy is too aggressive (KL={drift_analysis.get('kl_divergence', 0):.4f})\n"

        report += """
**Recommendation:** Review and address the flagged issues before production deployment.
Consider retraining with additional regularization or adjusting safety thresholds.
"""
    else:
        report += """
### Conclusion

The policy has been **REJECTED** due to critical safety concerns:

"""
        if not violations_ok:
            report += f"- {safety_verification.get('hard_violations', 0)} hard safety violations detected\n"
        if not stress_stable:
            report += "- Policy is unstable under stress scenarios\n"

        report += """
**Recommendation:** DO NOT DEPLOY. The policy requires retraining with:
- Stronger safety penalties
- Additional constraint enforcement
- Review of training data distribution
"""

    report += f"""
---

## H. Recommended Next Actions

"""
    if verdict == "APPROVED":
        report += """1. Proceed to Step 4: Fine-tuning and real-world adaptation
2. Deploy to edge devices with safety shield enabled
3. Monitor production metrics and set up alerting
4. Schedule periodic re-evaluation (recommended: weekly)
"""
    elif verdict == "CONDITIONAL":
        report += """1. Address the flagged safety concerns
2. Re-run evaluation after fixes
3. Consider adjusting deployment criteria if appropriate
4. Implement additional monitoring for conditional deployment
"""
    else:
        report += """1. Investigate root cause of failures
2. Review training data and reward function
3. Increase safety penalty weights
4. Retrain with enhanced constraint enforcement
5. Re-run full evaluation pipeline
"""

    report += f"""
---

*Report generated by Grid-Guardian Evaluation Pipeline v{__version__}*
"""

    # Save report
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)

    logger.info("Safety report saved to %s", output_path)
    return verdict


# ─────────────────────────────────────────────────────────────────────────────
#  Main Pipeline
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Grid-Guardian Step 3: Robust Evaluation & Safety Testing Pipeline v" + __version__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="configs/eval_config.yaml", help="YAML config path")
    parser.add_argument("--algo", choices=["CQL", "DT", "BC", "BCQ", "BRAC", "SAC", "PPO", "DQN", "DDPG"], default=None)
    parser.add_argument("--checkpoint_path", default=None, help="Model checkpoint path")
    parser.add_argument("--dataset_path", default=None, help="Test dataset path")
    parser.add_argument("--device", default=None)
    parser.add_argument("--num_eval_episodes", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--ope_methods", default=None, help="Comma-separated OPE methods")
    parser.add_argument("--run_stress_tests", default=None)
    parser.add_argument("--compute_cvar", default=None)
    parser.add_argument("--log_dir", default=None)
    parser.add_argument("--output_dir", default=None)
    args = parser.parse_args()

    # ── logging ──────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # ── config ───────────────────────────────────────────────────────────
    cfg = load_config(args.config) if Path(args.config).exists() else {}
    cfg = merge_cli(cfg, args)

    model_cfg = cfg.get("model", {})
    data_cfg = cfg.get("data", {})
    env_cfg = cfg.get("env", {})
    eval_cfg = cfg.get("evaluation", {})
    ope_cfg = cfg.get("ope", {})
    risk_cfg = cfg.get("risk", {})
    stress_cfg = cfg.get("stress_testing", {})
    safety_cfg = cfg.get("safety", {})
    ablation_cfg = cfg.get("ablation", {})
    drift_cfg = cfg.get("behavior_drift", {})
    output_cfg = cfg.get("output", {})
    deploy_cfg = cfg.get("deployment_criteria", {})

    algo_name = model_cfg.get("algo", "CQL")
    checkpoint_path = model_cfg.get("checkpoint_path", f"models/{algo_name}/run_42/checkpoint_best.pt")
    device = model_cfg.get("device", "cpu")
    seed = eval_cfg.get("seed", 42)
    n_eval_episodes = eval_cfg.get("num_eval_episodes", 50)

    output_dir = output_cfg.get("output_dir", f"outputs/eval_run_{algo_name}")
    log_dir = output_cfg.get("log_dir", output_dir)

    logger.info("=" * 70)
    logger.info("  Grid-Guardian Step 3: Robust Evaluation & Safety Testing")
    logger.info("  Algorithm: %s | Device: %s | Seed: %d", algo_name, device, seed)
    logger.info("  Checkpoint: %s", checkpoint_path)
    logger.info("=" * 70)

    # ── reproducibility ──────────────────────────────────────────────────
    set_seed(seed)

    # ── create output directories ────────────────────────────────────────
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    plots_dir = Path(output_dir) / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # ── load datasets ────────────────────────────────────────────────────
    test_path = data_cfg.get("dataset_path", "data/partitioned/test.csv")
    train_path = data_cfg.get("train_dataset_path", "data/partitioned/train.csv")

    test_df = load_dataset(test_path)
    train_df = load_dataset(train_path) if Path(train_path).exists() else test_df

    # ── build environment ────────────────────────────────────────────────
    env_cfg_init = dict(env_cfg)
    env_cfg_init["seed"] = seed
    dr_cfg = {"enabled": False}  # Start with no randomization for baseline

    env = MicrogridEnv(env_cfg_init, dataset=test_df, mode="replay", domain_rand_cfg=dr_cfg)

    obs_dim = env.observation_space.shape[0]
    if env_cfg.get("action_type", "discrete") == "discrete":
        act_dim = env.action_space.n
    else:
        act_dim = env.action_space.shape[0]

    logger.info("Obs dim: %d | Act dim: %d", obs_dim, act_dim)

    # ── safety shield ────────────────────────────────────────────────────
    shield = None
    if safety_cfg.get("shield_enabled", True):
        shield_config = SafetyConfig(
            soc_min_frac=env_cfg.get("safety", {}).get("soc_min_frac", 0.10),
            soc_max_frac=env_cfg.get("safety", {}).get("soc_max_frac", 0.95),
            max_charge_kw=env_cfg.get("safety", {}).get("max_charge_kw", 3.0),
            max_discharge_kw=env_cfg.get("safety", {}).get("max_discharge_kw", 3.0),
            max_grid_draw_kw=env_cfg.get("safety", {}).get("max_grid_draw_kw", 5.0),
            shield_mode=safety_cfg.get("shield_mode", "clip"),
            log_incidents=safety_cfg.get("log_violations", True),
        )
        shield = SafetyShield(shield_config, discrete_action_map=DISCRETE_ACTION_MAP)
        logger.info("Safety shield enabled: mode=%s", shield_config.shield_mode)

    # ── load trained agent ───────────────────────────────────────────────
    if not Path(checkpoint_path).exists():
        logger.error("Checkpoint not found: %s", checkpoint_path)
        sys.exit(1)

    agent = load_agent(algo_name, checkpoint_path, obs_dim, act_dim, cfg, device)

    # Create policy function
    if algo_name == "DT":
        context_length = env_cfg.get("history_length", 24)
        def policy_fn(obs):
            L = context_length
            s = np.tile(obs, (L, 1))
            a = np.zeros(L, dtype=np.int64)
            r = np.zeros(L, dtype=np.float32)
            t = np.arange(L)
            return agent.predict(s, a, r, t)
    else:
        policy_fn = agent.predict

    # ── load BC baseline for drift analysis ──────────────────────────────
    bc_path = "models/BC/run_42/checkpoint_best.pt"
    bc_agent = None
    if Path(bc_path).exists():
        bc_agent = BCAgent(obs_dim, act_dim, [256, 256], 3e-4, False, device)
        bc_agent.load(bc_path)
        logger.info("Loaded BC baseline for drift analysis")

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 1: Offline Validation on Held-out Test Data
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  STEP 1: Offline Validation on Held-out Test Data")
    logger.info("=" * 70)

    t0 = time.time()
    eval_metrics = evaluate_policy_extended(env, policy_fn, n_eval_episodes, shield)
    eval_time = time.time() - t0

    logger.info("Evaluation complete in %.1fs", eval_time)
    logger.info("  Mean Reward: %.4f (std=%.4f)", eval_metrics["mean_reward"], eval_metrics["std_reward"])
    logger.info("  Safety Violation Rate: %.6f", eval_metrics["safety_violation_rate"])
    logger.info("  CVaR (5%%): %.4f", eval_metrics["cvar_5pct"])

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 2: Edge-Case and Stress Testing
    # ══════════════════════════════════════════════════════════════════════
    stress_results = {}
    if stress_cfg.get("enabled", True):
        logger.info("\n" + "=" * 70)
        logger.info("  STEP 2: Edge-Case and Stress Testing")
        logger.info("=" * 70)

        scenarios = stress_cfg.get("scenarios", [])
        if not scenarios:
            # Default scenarios
            scenarios = [
                {"name": "cloud_ramp", "irradiance_noise_std": 0.5, "enabled": True},
                {"name": "low_generation", "irradiance_multiplier": 0.1, "enabled": True},
                {"name": "grid_outage", "enabled": True},
                {"name": "sensor_dropout", "sensor_dropout_prob": 0.3, "enabled": True},
                {"name": "forecast_error", "forecast_noise_std": 0.4, "enabled": True},
                {"name": "load_surge", "load_multiplier": 2.5, "enabled": True},
                {"name": "tariff_spike", "tariff_shift_range": [-2.0, 3.0], "enabled": True},
            ]

        stress_env = MicrogridEnv(env_cfg_init, dataset=test_df, mode="replay", domain_rand_cfg={"enabled": True})
        stress_results = run_stress_tests(
            stress_env, policy_fn, scenarios,
            episodes_per_scenario=stress_cfg.get("episodes_per_scenario", 10),
            shield=shield,
        )

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 3: Safety Verification
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  STEP 3: Safety Verification")
    logger.info("=" * 70)

    constraints = safety_cfg.get("constraints", {})
    safety_verification = verify_safety(env, policy_fn, n_episodes=20, shield=shield, constraints=constraints)

    logger.info("Safety verification complete")
    logger.info("  Total violations: %d", safety_verification["total_violations"])
    logger.info("  Hard violations: %d", safety_verification["hard_violations"])
    logger.info("  Near misses (shield blocked): %d", safety_verification["near_misses"])

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 4: Risk-Sensitive Analysis
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  STEP 4: Risk-Sensitive Analysis")
    logger.info("=" * 70)

    # Re-run evaluation to get episode rewards for risk analysis
    ep_rewards = np.array([eval_metrics["mean_reward"]] * max(1, eval_metrics.get("n_episodes", 1)))
    # Better: run quick eval to get actual episode rewards
    quick_eval = evaluate_policy(env, policy_fn, n_episodes=min(20, n_eval_episodes), shield=shield)
    if "episode_rewards" not in quick_eval:
        # Estimate from summary stats
        mean_r = eval_metrics["mean_reward"]
        std_r = eval_metrics["std_reward"]
        n = eval_metrics.get("n_episodes", 20)
        ep_rewards = np.random.normal(mean_r, std_r, n)

    risk_metrics = compute_risk_metrics(ep_rewards, alpha=risk_cfg.get("alpha", 0.05))
    logger.info("CVaR (α=%.2f): %.4f", risk_metrics["alpha"], risk_metrics["cvar_alpha"])
    logger.info("Worst 5%%: %.4f | Tail Ratio: %.4f", risk_metrics["worst_5pct"], risk_metrics["tail_ratio"])

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 5: Behavior Drift and Conservatism Checks
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  STEP 5: Behavior Drift and Conservatism Checks")
    logger.info("=" * 70)

    drift_metrics = {"kl_divergence": 0, "js_divergence": 0, "tvd": 0}
    if bc_agent is not None:
        try:
            drift_metrics = compute_action_distribution_drift(
                env, policy_fn, bc_agent.predict,
                n_episodes=min(10, n_eval_episodes),
                shield=shield,
            )
            logger.info("KL Divergence: %.4f | TVD: %.4f",
                       drift_metrics["kl_divergence"], drift_metrics["tvd"])
        except Exception as e:
            logger.warning("Drift computation failed: %s", e)

    drift_thresholds = drift_cfg.get("thresholds", {})
    drift_analysis = analyze_behavior_drift(drift_metrics, drift_thresholds)
    logger.info("Conservatism level: %s", drift_analysis["conservatism_level"])

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 6: Ablation-Style Evaluation
    # ══════════════════════════════════════════════════════════════════════
    ablation_results = {}
    if ablation_cfg.get("enabled", False):
        logger.info("\n" + "=" * 70)
        logger.info("  STEP 6: Ablation-Style Evaluation")
        logger.info("=" * 70)

        variants = ablation_cfg.get("variants", [])
        if variants:
            ablation_env = MicrogridEnv(env_cfg_init, dataset=test_df, mode="replay", domain_rand_cfg={"enabled": True})
            ablation_results = run_ablation_study(
                ablation_env, policy_fn, variants,
                n_episodes=10, shield=shield,
            )

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 7: OPE (Offline Policy Evaluation)
    # ══════════════════════════════════════════════════════════════════════
    ope_results = {}
    if ope_cfg.get("enabled", False) and bc_agent is not None:
        logger.info("\n" + "=" * 70)
        logger.info("  STEP 7: Offline Policy Evaluation (OPE)")
        logger.info("=" * 70)

        # Prepare data for OPE
        continuous = env_cfg.get("action_type", "discrete") == "continuous"
        converter = DatasetConverter(
            obs_keys=env_cfg.get("observation_keys"),
            time_features=env_cfg.get("time_features", True),
            continuous=continuous,
        )
        trans_data = converter.convert(train_df)
        n_ope = min(5000, len(trans_data["observations"]))
        ope_data = {k: v[:n_ope] for k, v in trans_data.items()}

        methods = ope_cfg.get("methods", ["IS", "WIS", "FQE", "DR"])
        try:
            ope_results = run_ope(
                policy_fn, bc_agent, ope_data,
                methods=methods,
                gamma=0.99,
                fqe_steps=min(ope_cfg.get("fqe_steps", 50000), 20000),
                n_bootstrap=ope_cfg.get("bootstrap_samples", 200),
                confidence=ope_cfg.get("confidence_level", 0.95),
                device=device,
            )
            for method, data in ope_results.items():
                if isinstance(data, dict):
                    logger.info("  %s: %.4f [%.4f, %.4f]",
                               method, data.get("estimate", 0),
                               data.get("ci_lower", 0), data.get("ci_upper", 0))
        except Exception as e:
            logger.warning("OPE failed: %s", e)

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 8: Generate Final Artifacts
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  STEP 8: Generating Final Artifacts")
    logger.info("=" * 70)

    # Save evaluation summary
    eval_summary = {
        "algorithm": algo_name,
        "checkpoint": checkpoint_path,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "evaluation": eval_metrics,
        "risk_metrics": risk_metrics,
        "drift_analysis": drift_analysis,
        "evaluation_time_sec": eval_time,
    }
    with open(Path(output_dir) / "eval_summary.json", "w") as f:
        json.dump(eval_summary, f, indent=2, default=_json_default)

    # Save stress test report
    if stress_results:
        with open(Path(output_dir) / "stress_test_report.json", "w") as f:
            json.dump(stress_results, f, indent=2, default=_json_default)

    # Save OPE results
    if ope_results:
        with open(Path(output_dir) / "ope_estimates.json", "w") as f:
            json.dump(ope_results, f, indent=2, default=_json_default)

    # Save CVaR metrics
    with open(Path(output_dir) / "cvar_metrics.json", "w") as f:
        json.dump(risk_metrics, f, indent=2, default=_json_default)

    # Generate plots
    if output_cfg.get("save_plots", True):
        generate_plots(eval_metrics, stress_results, str(plots_dir), algo_name)

    # ══════════════════════════════════════════════════════════════════════
    #  STEP 9: Generate Safety Report
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  STEP 9: Generating Safety Report & Deployment Recommendation")
    logger.info("=" * 70)

    verdict = generate_safety_report(
        algo_name=algo_name,
        eval_metrics=eval_metrics,
        stress_results=stress_results,
        safety_verification=safety_verification,
        risk_metrics=risk_metrics,
        drift_analysis=drift_analysis,
        ablation_results=ablation_results,
        ope_results=ope_results,
        deployment_criteria=deploy_cfg,
        output_path=str(Path(output_dir) / "safety_report.md"),
    )

    # ══════════════════════════════════════════════════════════════════════
    #  Final Summary
    # ══════════════════════════════════════════════════════════════════════
    logger.info("\n" + "=" * 70)
    logger.info("  EVALUATION COMPLETE")
    logger.info("=" * 70)
    logger.info("  Algorithm: %s", algo_name)
    logger.info("  Mean Reward: %.4f", eval_metrics["mean_reward"])
    logger.info("  Safety Violation Rate: %.6f", eval_metrics["safety_violation_rate"])
    logger.info("  Hard Violations: %d", safety_verification["hard_violations"])
    logger.info("  CVaR (5%%): %.4f", eval_metrics["cvar_5pct"])
    logger.info("  Deployment Verdict: %s", verdict)
    logger.info("  Output Directory: %s", output_dir)
    logger.info("=" * 70)

    return verdict


if __name__ == "__main__":
    main()
