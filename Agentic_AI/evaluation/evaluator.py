"""
Evaluation & stress testing module.

Provides:
 - evaluate_policy     : run N episodes and collect metrics
 - stress_test         : adversarial scenarios (cloud ramps, outages, sensor dropout)
 - compute_safety_metrics : safety_violation rate, SoC violations, CVaR
 - plot_learning_curves
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def evaluate_policy(
    env,
    policy_fn: Callable,
    n_episodes: int = 20,
    shield=None,
    max_steps: int = 10000,
) -> Dict[str, Any]:
    """
    Run *n_episodes* in env and collect metrics.

    Parameters
    ----------
    env : MicrogridEnv
    policy_fn : callable(obs) → action
    shield : SafetyShield or None
    """
    ep_rewards = []
    ep_lengths = []
    safety_violations = 0
    soc_violations = 0
    total_steps = 0
    energy_sold = 0.0
    energy_bought = 0.0
    episodes_with_any_violation = 0

    for ep in range(n_episodes):
        obs, info = env.reset()
        ep_reward = 0.0
        ep_len = 0
        done = False
        ep_had_violation = False

        while not done and ep_len < max_steps:
            action = policy_fn(obs)
            if shield is not None:
                soc = getattr(env, "_soc", 2.0)
                soc_cap = getattr(env, "_soc_cap", 4.0)
                continuous = getattr(env, "_continuous", False)
                action, intervened, reason = shield(action, soc, soc_cap, continuous=continuous)
                if intervened:
                    safety_violations += 1
                    ep_had_violation = True

            obs, reward, terminated, truncated, info = env.step(action)
            done = terminated or truncated
            ep_reward += reward
            ep_len += 1

            # track energy flows
            action_kw = info.get("action_kw", 0.0)
            if action_kw > 0:
                energy_bought += action_kw * (5 / 60)
            else:
                energy_sold += abs(action_kw) * (5 / 60)

            if info.get("safety_violation", False):
                soc_violations += 1

        ep_rewards.append(ep_reward)
        ep_lengths.append(ep_len)
        total_steps += ep_len
        if ep_had_violation:
            episodes_with_any_violation += 1

    ep_rewards = np.array(ep_rewards)
    safety_rate = safety_violations / max(total_steps, 1)
    cvar_5 = float(np.percentile(ep_rewards, 5))

    return {
        "mean_reward": float(ep_rewards.mean()),
        "std_reward": float(ep_rewards.std()),
        "min_reward": float(ep_rewards.min()),
        "max_reward": float(ep_rewards.max()),
        "median_reward": float(np.median(ep_rewards)),
        "cvar_5pct": cvar_5,
        "safety_violation_rate": safety_rate,
        "soc_violations": soc_violations,
        "total_steps": total_steps,
        "n_episodes": n_episodes,
        "mean_ep_length": float(np.mean(ep_lengths)),
        "energy_sold_kwh": energy_sold,
        "energy_bought_kwh": energy_bought,
        "episodes_with_violation": episodes_with_any_violation,
    }


def stress_test(
    env,
    policy_fn: Callable,
    scenarios: List[str] = ["cloud_ramp", "grid_outage", "sensor_dropout"],
    n_episodes: int = 10,
    shield=None,
) -> Dict[str, Dict]:
    """Run adversarial stress scenarios and collect metrics per scenario."""
    results = {}
    for scenario in scenarios:
        logger.info("Stress test: %s", scenario)
        # configure environment for adversarial scenario
        # Temporarily modify domain randomization for stress
        orig_dr = dict(env.domain_rand_cfg) if hasattr(env, "domain_rand_cfg") else {}
        if scenario == "cloud_ramp":
            env.domain_rand_cfg["enabled"] = True
            env.domain_rand_cfg["irradiance_noise_std"] = 0.5  # extreme
        elif scenario == "grid_outage":
            env.domain_rand_cfg["enabled"] = True
            env.domain_rand_cfg["sensor_dropout_prob"] = 0.2
        elif scenario == "sensor_dropout":
            env.domain_rand_cfg["enabled"] = True
            env.domain_rand_cfg["sensor_dropout_prob"] = 0.3

        metrics = evaluate_policy(env, policy_fn, n_episodes, shield)
        results[scenario] = metrics

        # restore
        env.domain_rand_cfg = orig_dr

    return results


def compute_action_distribution_drift(
    env_or_current: Any,
    policy_fn_or_behavior: Any = None,
    behavior_policy_fn: Any = None,
    n_actions: int = 7,
    n_episodes: int = 10,
    shield=None,
) -> Dict:
    """Measure divergence between two policies' action distributions.

    Can be called in two ways:
    1. ``compute_action_distribution_drift(env, policy_fn, behavior_fn, ...)``
       — runs both policies in *env* for *n_episodes* and compares actions.
    2. ``compute_action_distribution_drift(current_actions, behavior_actions)``
       — legacy: pass two ``np.ndarray`` of action indices directly.

    Returns a dict with ``kl_divergence``, ``js_divergence``, ``tvd``.
    """
    eps = 1e-8

    if isinstance(env_or_current, np.ndarray):
        # legacy call: arrays directly
        current_actions = env_or_current.astype(int)
        behavior_actions = policy_fn_or_behavior.astype(int)
    else:
        # env-based call
        env = env_or_current
        new_policy = policy_fn_or_behavior
        beh_policy = behavior_policy_fn
        current_actions, behavior_actions = [], []
        for _ in range(n_episodes):
            obs, _ = env.reset()
            done = False
            while not done:
                a_new = int(new_policy(obs))
                a_beh = int(beh_policy(obs))
                if shield is not None:
                    soc = getattr(env, "_soc", 2.0)
                    cap = getattr(env, "_soc_cap", 4.0)
                    a_new, _, _ = shield(a_new, soc, cap)
                    a_beh, _, _ = shield(a_beh, soc, cap)
                current_actions.append(a_new)
                behavior_actions.append(a_beh)
                obs, _, term, trunc, _ = env.step(a_new)
                done = term or trunc
        current_actions = np.array(current_actions, dtype=int)
        behavior_actions = np.array(behavior_actions, dtype=int)

    p = np.bincount(current_actions, minlength=n_actions).astype(float)
    q = np.bincount(behavior_actions, minlength=n_actions).astype(float)
    p = p / (p.sum() + eps)
    q = q / (q.sum() + eps)
    kl = float(np.sum(p * np.log((p + eps) / (q + eps))))
    # Jensen-Shannon
    m = 0.5 * (p + q)
    js = float(0.5 * np.sum(p * np.log((p + eps) / (m + eps))) +
               0.5 * np.sum(q * np.log((q + eps) / (m + eps))))
    # Total Variation Distance
    tvd = float(0.5 * np.sum(np.abs(p - q)))

    return {"kl_divergence": kl, "js_divergence": js, "tvd": tvd,
            "n_current": len(current_actions), "n_behavior": len(behavior_actions)}


def _json_default(obj):
    """JSON serializer for numpy types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return str(obj)


def save_eval_summary(metrics: Dict, path: str):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, default=_json_default)
    logger.info("Eval summary saved to %s", path)


def plot_learning_curves(history: Dict[str, list], save_dir: str):
    """Plot training loss / reward curves and save PNGs."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib not available; skipping plots")
        return

    Path(save_dir).mkdir(parents=True, exist_ok=True)

    if history.get("train_loss"):
        plt.figure(figsize=(10, 4))
        plt.plot(history["train_loss"], label="train_loss")
        if history.get("val_loss"):
            plt.plot(history["val_loss"], label="val_loss")
        plt.xlabel("Epoch / Step")
        plt.ylabel("Loss")
        plt.title("Training Loss")
        plt.legend()
        plt.tight_layout()
        plt.savefig(Path(save_dir) / "loss_curve.png", dpi=100)
        plt.close()

    if history.get("eval_rewards"):
        plt.figure(figsize=(10, 4))
        plt.plot(history["eval_rewards"], label="eval_reward")
        plt.xlabel("Evaluation #")
        plt.ylabel("Mean Reward")
        plt.title("Evaluation Reward")
        plt.legend()
        plt.tight_layout()
        plt.savefig(Path(save_dir) / "reward_curve.png", dpi=100)
        plt.close()

    if history.get("safety_violations"):
        plt.figure(figsize=(10, 4))
        plt.plot(history["safety_violations"], label="violation_rate")
        plt.xlabel("Evaluation #")
        plt.ylabel("Violation Rate")
        plt.title("Safety Violation Rate per Checkpoint")
        plt.legend()
        plt.tight_layout()
        plt.savefig(Path(save_dir) / "safety_curve.png", dpi=100)
        plt.close()

    logger.info("Learning curves saved to %s", save_dir)
