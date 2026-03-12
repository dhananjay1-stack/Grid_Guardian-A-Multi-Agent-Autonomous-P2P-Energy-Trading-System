#!/usr/bin/env python3
"""
train_rl_agents.py — Production-Grade, Safety-First RL Training Pipeline
for Grid-Guardian.

Supports:
  - BC, DQN, PPO, SAC, DDPG, CQL, BCQ, BRAC, DT algorithms
  - offline / online / hybrid training modes
  - Safety shield (clip / fallback / reject)
  - Domain randomization & forecast-noise injection
  - Offline Policy Evaluation (IS, WIS, FQE, DR)
  - Stress testing & safety evaluation
  - TorchScript / ONNX packaging for Raspberry Pi 5 deployment
  - TensorBoard & optional W&B logging
  - Fully reproducible via seed management + run manifests

Usage:
    python train_rl_agents.py --config configs/train_rl.yaml --algo CQL --mode offline --seed 42
    python train_rl_agents.py --config configs/train_rl.yaml --algo DT --mode offline --device cuda:0
    python train_rl_agents.py --pack-policy --model-path models/CQL/best.pt --out edge/policy_pack/
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import yaml

# ── Project imports ──────────────────────────────────────────────────────────
from env.microgrid_env import MicrogridEnv, DISCRETE_ACTION_MAP
from env.safety_shield import SafetyShield, SafetyConfig
from data_utils.replay_buffer import (
    DatasetConverter, ReplayBuffer, TrajectoryBuilder, BehaviorDataset, dataset_hash,
)
from agents.bc_agent import BCAgent
from agents.classical_rl import DQNAgent, SACAgent, PPOAgent, DDPGAgent
from agents.offline_rl import CQLAgent, BCQAgent, BRACAgent
from agents.decision_transformer import DTAgent
from evaluation.evaluator import (
    evaluate_policy, stress_test, save_eval_summary, plot_learning_curves,
    compute_action_distribution_drift,
)
from evaluation.ope import run_ope
from model_packaging.exporter import (
    export_torchscript, export_onnx, NormalizationPipeline, save_model_card,
)

__version__ = "1.0.0"
logger = logging.getLogger("train_rl")

# ─────────────────────────────────────────────────────────────────────────────
#  Reproducibility
# ─────────────────────────────────────────────────────────────────────────────
def set_seed(seed: int, deterministic: bool = True):
    os.environ["PYTHONHASHSEED"] = str(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False
        os.environ["CUBLAS_WORKSPACE_CONFIG"] = ":4096:8"
    logger.info("Random seed set to %d (deterministic=%s)", seed, deterministic)


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
        cfg.setdefault("algo", {})["name"] = args.algo
    if args.mode:
        cfg.setdefault("training", {})["mode"] = args.mode
    if args.seed is not None:
        cfg.setdefault("training", {})["seed"] = args.seed
    if args.device:
        cfg.setdefault("training", {})["device"] = args.device
    if args.total_timesteps:
        cfg.setdefault("training", {})["total_steps"] = int(float(args.total_timesteps))
    if args.eval_every:
        cfg.setdefault("training", {})["eval_every"] = int(args.eval_every)
    if args.eval_episodes:
        cfg.setdefault("training", {})["eval_episodes"] = int(args.eval_episodes)
    if args.log_dir:
        cfg.setdefault("logging", {})["log_dir"] = args.log_dir
    if args.num_envs:
        cfg.setdefault("training", {})["num_envs"] = int(args.num_envs)
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
#  Agent factory
# ─────────────────────────────────────────────────────────────────────────────
def create_agent(algo_name: str, obs_dim: int, act_dim: int,
                 cfg: Dict, device: str, bc_agent=None):
    algo_cfg = cfg.get("algo", {})
    hidden = algo_cfg.get("network", {}).get("hidden_sizes", [256, 256])
    lr = algo_cfg.get("lr", 3e-4)
    gamma = algo_cfg.get("gamma", 0.99)
    tau = algo_cfg.get("tau", 0.005)
    continuous = cfg.get("env", {}).get("action_type", "discrete") == "continuous"

    bp_cfg = cfg.get("training", {}).get("behavior_penalty", {})
    kl_lambda = bp_cfg.get("lambda_kl", 0.0) if bp_cfg.get("enabled", False) else 0.0

    if algo_name == "BC":
        return BCAgent(obs_dim, act_dim, hidden, lr, continuous, device)
    elif algo_name == "DQN":
        return DQNAgent(obs_dim, act_dim, lr, gamma, tau, hidden, device, bc_agent, kl_lambda)
    elif algo_name == "PPO":
        ppo = algo_cfg.get("ppo", {})
        return PPOAgent(obs_dim, act_dim, lr, gamma,
                        ppo.get("gae_lambda", 0.95),
                        ppo.get("clip_range", 0.2),
                        ppo.get("ent_coef", 0.01),
                        hidden, device, ppo.get("n_epochs", 10))
    elif algo_name == "SAC":
        return SACAgent(obs_dim, act_dim if continuous else 2, lr, gamma, tau,
                        hidden, device, behavior_policy=bc_agent, kl_lambda=kl_lambda)
    elif algo_name == "DDPG":
        return DDPGAgent(obs_dim, act_dim if continuous else 2, lr, gamma, tau,
                         hidden, device)
    elif algo_name == "CQL":
        cql = algo_cfg.get("cql", {})
        return CQLAgent(obs_dim, act_dim, lr, gamma, tau,
                        cql.get("alpha", 1.0), cql.get("n_action_samples", 10),
                        hidden, device)
    elif algo_name == "BCQ":
        bcq = algo_cfg.get("bcq", {})
        return BCQAgent(obs_dim, act_dim, lr, gamma, tau,
                        bcq.get("threshold", 0.3), hidden, device)
    elif algo_name == "BRAC":
        return BRACAgent(obs_dim, act_dim, lr, gamma, tau,
                         kl_lambda if kl_lambda > 0 else 1.0,
                         hidden, device, bc_agent)
    elif algo_name == "DT":
        dt = algo_cfg.get("dt", {})
        return DTAgent(
            obs_dim, act_dim,
            d_model=dt.get("d_model", 256),
            n_heads=dt.get("n_heads", 8),
            n_layers=dt.get("n_layers", 6),
            context_length=cfg.get("env", {}).get("history_length", 24),
            dropout=dt.get("dropout", 0.1),
            lr=dt.get("lr", 1e-4),
            weight_decay=dt.get("weight_decay", 1e-4),
            warmup_steps=dt.get("warmup_steps", 1000),
            continuous=continuous,
            device=device,
        )
    else:
        raise ValueError(f"Unknown algorithm: {algo_name}")


# ─────────────────────────────────────────────────────────────────────────────
#  TensorBoard setup
# ─────────────────────────────────────────────────────────────────────────────
def setup_logging(cfg: Dict, algo: str, run_id: str):
    log_cfg = cfg.get("logging", {})
    log_dir = Path(log_cfg.get("log_dir", "./logs")) / algo / run_id
    log_dir.mkdir(parents=True, exist_ok=True)

    writer = None
    if log_cfg.get("tensorboard", True):
        try:
            from torch.utils.tensorboard import SummaryWriter
            writer = SummaryWriter(str(log_dir))
            logger.info("TensorBoard logging to %s", log_dir)
        except ImportError:
            logger.warning("tensorboard not installed; skipping")

    # ── optional W&B ─────────────────────────────────────────────────
    wandb_run = None
    if log_cfg.get("wandb", False):
        try:
            import wandb
            wandb_project = log_cfg.get("wandb_project", "grid-guardian-rl")
            wandb_run = wandb.init(
                project=wandb_project,
                name=run_id,
                config=cfg,
                group=algo,
                reinit=True,
            )
            logger.info("W&B logging enabled: project=%s  run=%s", wandb_project, run_id)
        except ImportError:
            logger.warning("wandb not installed; pip install wandb to enable")
        except Exception as e:
            logger.warning("W&B init failed: %s", e)

    return writer, str(log_dir), wandb_run


# ─────────────────────────────────────────────────────────────────────────────
#  Training loops
# ─────────────────────────────────────────────────────────────────────────────
def train_offline(agent, algo_name: str, replay_buffer: ReplayBuffer,
                  traj_data: Optional[Dict] = None,
                  env=None, shield=None, cfg: Dict = {},
                  writer=None, log_dir: str = "./logs",
                  wandb_run=None):
    """Offline training loop for all algorithms."""
    training_cfg = cfg.get("training", {})
    total_steps = int(float(training_cfg.get("total_steps", 200000)))
    eval_every = int(training_cfg.get("eval_every", 10000))
    eval_episodes = int(training_cfg.get("eval_episodes", 20))
    batch_size = cfg.get("algo", {}).get("batch_size", 256)

    history = {"train_loss": [], "eval_rewards": [], "safety_violations": []}
    best_reward = -float("inf")
    model_dir = Path(f"models/{algo_name}/run_{training_cfg.get('seed', 42)}")
    model_dir.mkdir(parents=True, exist_ok=True)

    if algo_name == "DT":
        if traj_data is None:
            raise ValueError("DT requires trajectory data")
        n_epochs = max(1, total_steps // max(len(traj_data["states"]), 1))
        n_epochs = min(n_epochs, 200)  # cap epochs
        logger.info("DT training: %d epochs, %d trajectories", n_epochs, len(traj_data["states"]))
        for epoch in range(n_epochs):
            loss = agent.train_epoch(traj_data, batch_size=min(batch_size, 64))
            history["train_loss"].append(loss)
            if writer:
                writer.add_scalar("train/loss", loss, epoch)
            if wandb_run:
                wandb_run.log({"train/loss": loss, "epoch": epoch})

            if (epoch + 1) % max(1, n_epochs // 10) == 0:
                logger.info("DT epoch %d/%d  loss=%.6f", epoch + 1, n_epochs, loss)

            # periodic eval
            if env is not None and (epoch + 1) % max(1, n_epochs // 5) == 0:
                def _dt_policy(obs):
                    L = agent.context_length
                    s = np.tile(obs, (L, 1))
                    a = np.zeros(L, dtype=np.int64)
                    r = np.zeros(L, dtype=np.float32)
                    t = np.arange(L)
                    return agent.predict(s, a, r, t)
                metrics = evaluate_policy(env, _dt_policy, eval_episodes, shield)
                history["eval_rewards"].append(metrics["mean_reward"])
                history["safety_violations"].append(metrics["safety_violation_rate"])
                if writer:
                    writer.add_scalar("eval/mean_reward", metrics["mean_reward"], epoch)
                    writer.add_scalar("eval/safety_rate", metrics["safety_violation_rate"], epoch)
                if metrics["mean_reward"] > best_reward:
                    best_reward = metrics["mean_reward"]
                    agent.save(str(model_dir / "checkpoint_best.pt"))
        agent.save(str(model_dir / "checkpoint_final.pt"))

    elif algo_name == "BC":
        bc_data = {"observations": np.array(replay_buffer.observations),
                   "actions": np.array(replay_buffer.actions)}
        bc_history = agent.train(bc_data, epochs=min(total_steps // 1000, 100),
                                  batch_size=batch_size)
        history["train_loss"] = bc_history["train_loss"]
        agent.save(str(model_dir / "checkpoint_best.pt"))

    else:
        # Standard offline RL (CQL, BCQ, BRAC, DQN, PPO, SAC, DDPG)
        logger.info("Offline training: %d steps, batch=%d", total_steps, batch_size)
        for step in range(total_steps):
            if len(replay_buffer) < batch_size:
                break
            batch = replay_buffer.sample(batch_size)
            loss = agent.train_step(batch)
            history["train_loss"].append(loss)

            if writer and step % 100 == 0:
                writer.add_scalar("train/loss", loss, step)
            if wandb_run and step % 100 == 0:
                wandb_run.log({"train/loss": loss, "step": step})

            if step > 0 and step % eval_every == 0:
                logger.info("Step %d/%d  loss=%.6f", step, total_steps, loss)
                if env is not None:
                    metrics = evaluate_policy(env, agent.predict, eval_episodes, shield)
                    history["eval_rewards"].append(metrics["mean_reward"])
                    history["safety_violations"].append(metrics["safety_violation_rate"])
                    if writer:
                        writer.add_scalar("eval/mean_reward", metrics["mean_reward"], step)
                        writer.add_scalar("eval/safety_rate", metrics["safety_violation_rate"], step)
                    if metrics["mean_reward"] > best_reward:
                        best_reward = metrics["mean_reward"]
                        agent.save(str(model_dir / "checkpoint_best.pt"))

        agent.save(str(model_dir / "checkpoint_final.pt"))

    return history, str(model_dir)


# ─────────────────────────────────────────────────────────────────────────────
#  Run manifest
# ─────────────────────────────────────────────────────────────────────────────
def _get_git_commit() -> str:
    """Try to get current git commit hash."""
    try:
        import subprocess
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(Path(__file__).parent))
        return result.stdout.strip()[:12] if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def _json_default(obj):
    """JSON serializer for numpy/torch types."""
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return str(obj)


def create_run_manifest(cfg: Dict, algo: str, run_id: str,
                        dataset_path: str, model_dir: str) -> Dict:
    manifest = {
        "run_id": run_id,
        "algorithm": algo,
        "mode": cfg.get("training", {}).get("mode", "offline"),
        "seed": cfg.get("training", {}).get("seed", 42),
        "total_steps": cfg.get("training", {}).get("total_steps", 200000),
        "dataset_path": dataset_path,
        "dataset_hash": dataset_hash(dataset_path) if Path(dataset_path).exists() else "N/A",
        "config_hash": hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12],
        "model_dir": model_dir,
        "git_commit": _get_git_commit(),
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "python_version": sys.version,
        "torch_version": torch.__version__,
        "platform": platform.platform(),
        "device": cfg.get("training", {}).get("device", "cpu"),
    }
    return manifest


# ─────────────────────────────────────────────────────────────────────────────
#  Pack-policy mode
# ─────────────────────────────────────────────────────────────────────────────
def pack_policy(args, cfg):
    """Export trained model to TorchScript + ONNX for edge deployment."""
    model_path = args.model_path
    out_dir = args.out or "edge/policy_pack"
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    algo = args.algo or cfg.get("algo", {}).get("name", "CQL")
    env_cfg = cfg.get("env", {})
    obs_keys = env_cfg.get("observation_keys", [])
    obs_dim = len(obs_keys) + (4 if env_cfg.get("time_features", True) else 0) + (1 if env_cfg.get("neighbor_balance", True) else 0)
    act_dim = len(env_cfg.get("discrete_actions", list(range(7)))) if env_cfg.get("action_type", "discrete") == "discrete" else env_cfg.get("continuous_action_dim", 2)

    # re-create agent and load checkpoint
    device = "cpu"
    agent = create_agent(algo, obs_dim, act_dim, cfg, device)

    if algo == "DT":
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        agent.model.load_state_dict(ckpt["model"])
        policy_net = agent.model
        # DT is sequence-based; for edge we'd need a simpler wrapper
        logger.warning("DT export: exporting underlying model; inference requires history handling")
    elif algo == "BC":
        agent.load(model_path)
        policy_net = agent.net
    elif algo in ("DQN", "CQL", "BCQ", "BRAC", "PPO"):
        agent.load(model_path)
        if hasattr(agent, "q"):
            policy_net = agent.q
        elif hasattr(agent, "net"):
            policy_net = agent.net
        else:
            policy_net = agent.q
    elif algo in ("SAC", "DDPG"):
        agent.load(model_path)
        policy_net = agent.policy if hasattr(agent, "policy") else agent.actor
    else:
        raise ValueError(f"Pack not supported for {algo}")

    # normalization
    norm = None
    norm_path = Path(out_dir) / "norm_params.npz"
    if norm_path.exists():
        norm = NormalizationPipeline.load(str(norm_path))

    # export
    ts_path = str(Path(out_dir) / f"{algo.lower()}_policy.torchscript")
    onnx_path = str(Path(out_dir) / f"{algo.lower()}_policy.onnx")

    if algo != "DT":  # DT has complex input; skip tracing
        export_torchscript(policy_net, obs_dim, ts_path, norm)
        try:
            export_onnx(policy_net, obs_dim, onnx_path, norm)
        except Exception as e:
            logger.warning("ONNX export failed: %s", e)

    # quantisation
    if args.pack_quantize:
        try:
            from model_packaging.exporter import quantize_model
            q_path = str(Path(out_dir) / f"{algo.lower()}_policy_quant.torchscript")
            quantize_model(ts_path, q_path)
        except Exception as e:
            logger.warning("Quantization failed: %s", e)

    save_model_card(out_dir, algo, obs_dim, act_dim, obs_keys, {})
    logger.info("Policy packed to %s", out_dir)


# ─────────────────────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Grid-Guardian RL Training Pipeline v" + __version__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="configs/train_rl.yaml", help="YAML config path")
    parser.add_argument("--mode", choices=["offline", "online", "hybrid"], default=None)
    parser.add_argument("--algo", choices=["SAC", "PPO", "DDPG", "DQN", "CQL", "BCQ", "BRAC", "DT", "BC"], default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--total-timesteps", type=float, default=None)
    parser.add_argument("--eval-every", type=int, default=None)
    parser.add_argument("--eval-episodes", type=int, default=None)
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--num-envs", type=int, default=None)
    parser.add_argument("--prefill-buffer", action="store_true", default=False)
    parser.add_argument("--reproducible", action="store_true")
    parser.add_argument("--pack-policy", action="store_true", help="Export model for edge")
    parser.add_argument("--pack-quantize", action="store_true")
    parser.add_argument("--model-path", default=None, help="Model checkpoint for packing")
    parser.add_argument("--out", default=None, help="Output dir for packing")
    parser.add_argument("--ope-methods", default=None, help="Comma-separated OPE methods")
    parser.add_argument("--dry-run", action="store_true")
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

    algo_name = cfg.get("algo", {}).get("name", "CQL")
    training_cfg = cfg.get("training", {})
    env_cfg = cfg.get("env", {})
    safety_cfg = cfg.get("safety", {})
    dr_cfg = cfg.get("domain_randomization", {})
    ope_cfg = cfg.get("ope", {})
    pkg_cfg = cfg.get("packaging", {})

    seed = training_cfg.get("seed", 42)
    device = training_cfg.get("device", "cpu")
    mode = training_cfg.get("mode", "offline")

    logger.info("=" * 70)
    logger.info("  Grid-Guardian RL Training Pipeline v%s", __version__)
    logger.info("  Algorithm: %s | Mode: %s | Seed: %d | Device: %s",
                algo_name, mode, seed, device)
    logger.info("=" * 70)

    # ── pack-policy mode ─────────────────────────────────────────────────
    if args.pack_policy:
        if not args.model_path:
            logger.error("--model-path required for --pack-policy")
            sys.exit(1)
        pack_policy(args, cfg)
        return

    # ── reproducibility ──────────────────────────────────────────────────
    set_seed(seed, args.reproducible or training_cfg.get("reproducible", True))

    run_id = f"{algo_name}_{seed}_{int(time.time())}"

    if args.dry_run:
        logger.info("DRY RUN — would train %s in %s mode for %s steps",
                     algo_name, mode, training_cfg.get("total_steps", "200k"))
        return

    # ── load datasets ────────────────────────────────────────────────────
    dataset_path = env_cfg.get("dataset_path", "data/partitioned/train.csv")
    train_df = load_dataset(dataset_path)
    val_df = None
    test_df = None
    val_path = env_cfg.get("val_dataset_path")
    test_path = env_cfg.get("test_dataset_path")
    if val_path and Path(val_path).exists():
        val_df = load_dataset(val_path)
    if test_path and Path(test_path).exists():
        test_df = load_dataset(test_path)

    # ── build environment ────────────────────────────────────────────────
    env_cfg_init = dict(env_cfg)
    env_cfg_init["seed"] = seed
    env = MicrogridEnv(env_cfg_init, dataset=train_df, mode="replay",
                       domain_rand_cfg=dr_cfg)
    eval_env = MicrogridEnv(env_cfg_init, dataset=val_df if val_df is not None else train_df,
                            mode="replay", domain_rand_cfg={})

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
            log_incidents=safety_cfg.get("log_incidents", True),
        )
        shield = SafetyShield(shield_config, discrete_action_map=DISCRETE_ACTION_MAP)
        logger.info("Safety shield enabled: mode=%s", shield_config.shield_mode)

    # ── convert dataset to transitions ───────────────────────────────────
    continuous = env_cfg.get("action_type", "discrete") == "continuous"
    converter = DatasetConverter(
        obs_keys=env_cfg.get("observation_keys"),
        time_features=env_cfg.get("time_features", True),
        continuous=continuous,
    )
    logger.info("Converting dataset to transitions...")
    trans_data = converter.convert(train_df)
    logger.info("Transitions: %d", len(trans_data["observations"]))

    # ── replay buffer ────────────────────────────────────────────────────
    replay_buffer = ReplayBuffer(capacity=len(trans_data["observations"]) + 100000, seed=seed)
    replay_buffer.add_batch(trans_data)
    logger.info("Replay buffer: %d transitions", len(replay_buffer))

    # ── BC baseline (needed for KL penalty & OPE) ────────────────────────
    logger.info("Training BC baseline for behavior policy estimation...")
    bc_agent = BCAgent(obs_dim, act_dim, [256, 256], 3e-4, continuous, device)
    bc_data = BehaviorDataset(
        obs_keys=env_cfg.get("observation_keys"),
        time_features=env_cfg.get("time_features", True),
        continuous=continuous,
    ).build(train_df)
    bc_agent.train(bc_data, epochs=30, batch_size=256)
    bc_model_dir = Path(f"models/BC/run_{seed}")
    bc_model_dir.mkdir(parents=True, exist_ok=True)
    bc_agent.save(str(bc_model_dir / "checkpoint_best.pt"))
    logger.info("BC baseline trained and saved")

    # ── normalization pipeline ───────────────────────────────────────────
    norm = NormalizationPipeline.fit(trans_data["observations"])

    # ── create agent ─────────────────────────────────────────────────────
    agent = create_agent(algo_name, obs_dim, act_dim, cfg, device, bc_agent)
    logger.info("Agent created: %s", algo_name)

    # ── trajectory data for DT ───────────────────────────────────────────
    traj_data = None
    if algo_name == "DT":
        logger.info("Building trajectory sequences for Decision Transformer...")
        builder = TrajectoryBuilder(
            context_length=env_cfg.get("history_length", 24),
            gamma=cfg.get("algo", {}).get("gamma", 0.99),
            obs_keys=env_cfg.get("observation_keys"),
            time_features=env_cfg.get("time_features", True),
            continuous=continuous,
        )
        traj_data = builder.build(train_df)
        logger.info("Trajectories: %d sequences of length %d",
                     len(traj_data["states"]), env_cfg.get("history_length", 24))

    # ── online / hybrid mode gate ────────────────────────────────────────
    if mode in ("online", "hybrid"):
        logger.warning(
            "Online / hybrid training requires a live simulation loop that is "
            "not yet implemented. Falling back to offline training with the "
            "provided dataset.  To train online, extend train_online() and "
            "provide a real-time data source."
        )
        mode = "offline"  # graceful degradation

    # ── TensorBoard / W&B ────────────────────────────────────────────────
    writer, log_dir, wandb_run = setup_logging(cfg, algo_name, run_id)

    # ── training ─────────────────────────────────────────────────────────
    logger.info("Starting %s training in %s mode...", algo_name, mode)
    t0 = time.time()

    history, model_dir = train_offline(
        agent, algo_name, replay_buffer, traj_data,
        eval_env, shield, cfg, writer, log_dir,
        wandb_run=wandb_run,
    )

    elapsed = time.time() - t0
    logger.info("Training complete in %.1f seconds", elapsed)

    # ── final evaluation ─────────────────────────────────────────────────
    logger.info("Running final evaluation...")
    if algo_name == "DT":
        def policy_fn(obs):
            L = agent.context_length
            s = np.tile(obs, (L, 1))
            a = np.zeros(L, dtype=np.int64)
            r = np.zeros(L, dtype=np.float32)
            t = np.arange(L)
            return agent.predict(s, a, r, t)
    else:
        policy_fn = agent.predict

    final_metrics = evaluate_policy(eval_env, policy_fn, 
                                     training_cfg.get("eval_episodes", 20), shield)
    logger.info("Final eval: mean_reward=%.4f  safety_rate=%.4f  CVaR=%.4f",
                final_metrics["mean_reward"],
                final_metrics["safety_violation_rate"],
                final_metrics["cvar_5pct"])

    # ── action distribution drift ────────────────────────────────────────
    try:
        drift_result = compute_action_distribution_drift(
            eval_env, policy_fn, bc_agent.predict,
            n_episodes=min(training_cfg.get("eval_episodes", 20), 10),
            shield=shield,
        )
        final_metrics["action_drift"] = drift_result
        logger.info("Action distribution drift: kl=%.4f  js=%.4f  tvd=%.4f",
                     drift_result.get("kl_divergence", 0),
                     drift_result.get("js_divergence", 0),
                     drift_result.get("tvd", 0))
    except Exception as e:
        logger.warning("Action drift computation failed: %s", e)

    # ── stress tests ─────────────────────────────────────────────────────
    logger.info("Running stress tests...")
    test_env = MicrogridEnv(env_cfg_init,
                            dataset=test_df if test_df is not None else train_df,
                            mode="replay", domain_rand_cfg=dr_cfg)
    stress_episodes = 5 if algo_name != "DT" else 2  # DT is slow on CPU
    stress_results = {}
    for scenario in ["cloud_ramp", "grid_outage", "sensor_dropout"]:
        try:
            res = stress_test(test_env, policy_fn,
                              scenarios=[scenario],
                              n_episodes=stress_episodes, shield=shield)
            stress_results.update(res)
            m = res[scenario]
            logger.info("  %s: reward=%.4f  safety_rate=%.6f",
                         scenario, m["mean_reward"], m["safety_violation_rate"])
        except Exception as e:
            logger.warning("  %s: FAILED — %s", scenario, e)
            stress_results[scenario] = {"error": str(e)}

    # ── OPE ──────────────────────────────────────────────────────────────
    ope_results = {}
    if ope_cfg.get("enabled", False) or args.ope_methods:
        methods = args.ope_methods.split(",") if args.ope_methods else ope_cfg.get("methods", ["IS", "WIS"])
        logger.info("Running OPE: %s", methods)
        # Use a subset for speed
        n_ope = min(5000, len(trans_data["observations"]))
        ope_data = {k: v[:n_ope] for k, v in trans_data.items()}
        try:
            ope_results = run_ope(
                policy_fn, bc_agent, ope_data,
                methods=methods,
                gamma=cfg.get("algo", {}).get("gamma", 0.99),
                fqe_steps=min(ope_cfg.get("fqe_steps", 50000), 10000),
                n_bootstrap=ope_cfg.get("bootstrap_samples", 200),
                confidence=ope_cfg.get("confidence_level", 0.95),
                device=device,
            )
        except Exception as e:
            logger.warning("OPE failed: %s", e)

    # ── save results ─────────────────────────────────────────────────────
    eval_dir = Path(f"eval/{algo_name}")
    eval_dir.mkdir(parents=True, exist_ok=True)
    all_metrics = {
        "final_eval": final_metrics,
        "stress_tests": stress_results,
        "training_time_sec": elapsed,
    }
    save_eval_summary(all_metrics, str(eval_dir / "eval_summary.json"))
    plot_learning_curves(history, str(eval_dir / "eval_plots"))

    if ope_results:
        ope_dir = Path(f"ope/{algo_name}")
        ope_dir.mkdir(parents=True, exist_ok=True)
        with open(ope_dir / "ope_estimates.json", "w") as f:
            json.dump(ope_results, f, indent=2, default=_json_default)
        logger.info("OPE results saved to %s", ope_dir)

    # ── normalization save ───────────────────────────────────────────────
    norm.save(str(Path(model_dir) / "norm_params.npz"))

    # ── packaging ────────────────────────────────────────────────────────
    if pkg_cfg.get("torchscript", False) or pkg_cfg.get("onnx", False):
        pack_dir = pkg_cfg.get("output_dir", "./edge/policy_pack")
        Path(pack_dir).mkdir(parents=True, exist_ok=True)
        norm.save(str(Path(pack_dir) / "norm_params.npz"))

        if algo_name not in ("DT",):
            if hasattr(agent, "q"):
                policy_net = agent.q
            elif hasattr(agent, "net"):
                policy_net = agent.net
            elif hasattr(agent, "policy"):
                policy_net = agent.policy
            else:
                policy_net = agent.actor if hasattr(agent, "actor") else None

            # Wrap models that return tuples (PPO returns logits+value, SAC returns mu+logstd)
            if policy_net is not None and algo_name in ("PPO",):
                class _PPOActionOnly(nn.Module):
                    def __init__(self, net):
                        super().__init__()
                        self.net = net
                    def forward(self, x):
                        logits, _ = self.net(x)
                        return logits
                policy_net = _PPOActionOnly(policy_net)
            elif policy_net is not None and algo_name in ("SAC",):
                class _SACMeanOnly(nn.Module):
                    def __init__(self, net):
                        super().__init__()
                        self.net = net
                    def forward(self, x):
                        mu, _ = self.net(x)
                        return mu
                policy_net = _SACMeanOnly(policy_net)

            if policy_net is not None:
                if pkg_cfg.get("torchscript", False):
                    try:
                        export_torchscript(policy_net, obs_dim,
                                           str(Path(pack_dir) / f"{algo_name.lower()}_policy.torchscript"),
                                           norm)
                    except Exception as e:
                        logger.warning("TorchScript export failed: %s", e)
                if pkg_cfg.get("onnx", False):
                    try:
                        export_onnx(policy_net, obs_dim,
                                    str(Path(pack_dir) / f"{algo_name.lower()}_policy.onnx"),
                                    norm)
                    except Exception as e:
                        logger.warning("ONNX export failed: %s", e)

                save_model_card(pack_dir, algo_name, obs_dim, act_dim,
                                env_cfg.get("observation_keys", []),
                                final_metrics)

    # ── run manifest ─────────────────────────────────────────────────────
    manifest = create_run_manifest(cfg, algo_name, run_id, dataset_path, model_dir)
    manifest["final_metrics"] = final_metrics
    manifest["elapsed_sec"] = elapsed

    exp_dir = Path(f"experiments/{run_id}")
    exp_dir.mkdir(parents=True, exist_ok=True)
    with open(exp_dir / "run_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2, default=_json_default)
    with open(exp_dir / "config_used.yaml", "w") as f:
        yaml.dump(cfg, f, sort_keys=False)

    if writer:
        writer.close()
    if wandb_run:
        try:
            wandb_run.log({"final/mean_reward": final_metrics["mean_reward"],
                           "final/safety_rate": final_metrics["safety_violation_rate"]})
            wandb_run.finish()
        except Exception:
            pass

    # ── safety shield summary ────────────────────────────────────────────
    if shield:
        summary = shield.get_incident_summary()
        logger.info("Safety shield summary: %s", summary)

    logger.info("=" * 70)
    logger.info("  TRAINING COMPLETE — %s", algo_name)
    logger.info("  Final reward: %.4f | Safety rate: %.6f",
                final_metrics["mean_reward"], final_metrics["safety_violation_rate"])
    logger.info("  Model: %s", model_dir)
    logger.info("  Logs:  %s", log_dir)
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
