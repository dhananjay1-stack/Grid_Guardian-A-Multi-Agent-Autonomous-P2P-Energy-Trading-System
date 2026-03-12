#!/usr/bin/env python3
"""
Grid-Guardian — Quickstart: Train → Evaluate → Pack

This script demonstrates the full pipeline in minimal steps:
1. Load the partitioned dataset
2. Train a CQL agent (500 steps for speed)
3. Evaluate with safety-shield
4. Run OPE
5. Export to TorchScript for Raspberry Pi
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import torch

# ── 1. Load data ─────────────────────────────────────────────────────────────
print("=" * 60)
print("  Step 1: Load Dataset")
print("=" * 60)

train_df = pd.read_csv("data/partitioned/train.csv", nrows=5000)
val_df   = pd.read_csv("data/partitioned/val.csv",   nrows=2000)
print(f"  Train: {len(train_df)} rows  |  Val: {len(val_df)} rows")
print(f"  Households: {train_df['household_id'].unique().tolist()}")

# ── 2. Build environment & replay buffer ─────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 2: Build Environment & Buffer")
print("=" * 60)

from env.microgrid_env import MicrogridEnv, DISCRETE_ACTION_MAP
from env.safety_shield import SafetyShield, SafetyConfig
from data_utils.replay_buffer import DatasetConverter, ReplayBuffer

env_cfg = {
    "observation_keys": [
        "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
        "battery_power_kw", "price_signal",
        "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
        "actual_irradiance_wm2", "voltage_v", "current_a",
    ],
    "time_features": True,
    "neighbor_balance": True,
    "action_type": "discrete",
    "safety": {"soc_min_frac": 0.10, "soc_max_frac": 0.95},
    "seed": 42,
}

env = MicrogridEnv(env_cfg, dataset=train_df, mode="replay")
eval_env = MicrogridEnv(env_cfg, dataset=val_df, mode="replay")

obs_dim = env.observation_space.shape[0]    # 18
act_dim = env.action_space.n               # 7
print(f"  Obs dim: {obs_dim}  |  Act dim: {act_dim}")

converter = DatasetConverter(time_features=True, continuous=False)
trans = converter.convert(train_df)
buf = ReplayBuffer(capacity=len(trans["observations"]) + 1000, seed=42)
buf.add_batch(trans)
print(f"  Replay buffer: {len(buf)} transitions")

# ── 3. Safety shield ─────────────────────────────────────────────────────────
shield = SafetyShield(SafetyConfig(shield_mode="clip"), DISCRETE_ACTION_MAP)
print(f"  Safety shield: mode=clip")

# ── 4. Train BC baseline ─────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 3: Train BC Baseline")
print("=" * 60)

from agents.bc_agent import BCAgent
from data_utils.replay_buffer import BehaviorDataset

bc = BCAgent(obs_dim, act_dim, [256, 256], 3e-4, False, "cpu")
bc_data = BehaviorDataset(time_features=True).build(train_df)
bc.train(bc_data, epochs=10, batch_size=256)
print("  BC baseline trained (10 epochs)")

# ── 5. Train CQL agent ──────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 4: Train CQL (500 steps)")
print("=" * 60)

from agents.offline_rl import CQLAgent

cql = CQLAgent(obs_dim, act_dim, hidden=[256, 256], device="cpu")
for step in range(500):
    batch = buf.sample(256)
    loss = cql.train_step(batch)
    if (step + 1) % 100 == 0:
        print(f"  Step {step+1}/500  loss={loss:.6f}")

# ── 6. Evaluate ──────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 5: Evaluate Policy")
print("=" * 60)

from evaluation.evaluator import evaluate_policy, stress_test

metrics = evaluate_policy(eval_env, cql.predict, n_episodes=5, shield=shield)
print(f"  Mean reward:  {metrics['mean_reward']:.4f}")
print(f"  Safety rate:  {metrics['safety_violation_rate']:.4f}")
print(f"  CVaR (5%):    {metrics['cvar_5pct']:.4f}")

# ── 7. Stress test ───────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 6: Stress Tests")
print("=" * 60)

stress = stress_test(eval_env, cql.predict,
                     scenarios=["cloud_ramp", "grid_outage"],
                     n_episodes=2, shield=shield)
for sc, m in stress.items():
    print(f"  {sc}: reward={m['mean_reward']:.4f}  safety={m['safety_violation_rate']:.4f}")

# ── 8. OPE ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 7: Offline Policy Evaluation")
print("=" * 60)

from evaluation.ope import run_ope

n_ope = min(3000, len(trans["observations"]))
ope_data = {k: v[:n_ope] for k, v in trans.items()}
ope_results = run_ope(cql.predict, bc, ope_data,
                      methods=["IS", "WIS"], gamma=0.99,
                      n_bootstrap=50, device="cpu")
for method, r in ope_results.items():
    print(f"  {method}: estimate={r['estimate']:.4f}  CI=[{r['ci_lower']:.4f}, {r['ci_upper']:.4f}]")

# ── 9. Export for Raspberry Pi ───────────────────────────────────────────────
print("\n" + "=" * 60)
print("  Step 8: Export to TorchScript + ONNX")
print("=" * 60)

from model_packaging.exporter import export_torchscript, export_onnx, NormalizationPipeline

norm = NormalizationPipeline.fit(trans["observations"])
os.makedirs("examples/output", exist_ok=True)

try:
    export_torchscript(cql.q, obs_dim, "examples/output/cql_policy.torchscript", norm)
    print("  TorchScript: examples/output/cql_policy.torchscript")
except Exception as e:
    print(f"  TorchScript export: {e}")

try:
    export_onnx(cql.q, obs_dim, "examples/output/cql_policy.onnx", norm)
    print("  ONNX: examples/output/cql_policy.onnx")
except Exception as e:
    print(f"  ONNX export: {e}")

norm.save("examples/output/norm_params.npz")
print("  Normalization: examples/output/norm_params.npz")

# ── Done ─────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  QUICKSTART COMPLETE")
print("  Run 'python train_rl_agents.py --help' for full options")
print("=" * 60)
