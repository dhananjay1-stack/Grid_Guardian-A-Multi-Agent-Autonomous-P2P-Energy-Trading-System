"""
Grid-Guardian RL Pipeline — pytest test suite.

Unit tests:
 - Environment step & reset
 - Safety shield (clip, fallback, reject)
 - Replay buffer add & sample
 - Dataset → trajectory conversion
 - BC training step (smoke)
 - DT training step (smoke)
 - OPE functions
 - Edge inference

Integration test:
 - Small smoke run (each algorithm, 500 steps)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from env.microgrid_env import MicrogridEnv, DISCRETE_ACTION_MAP
from env.safety_shield import SafetyShield, SafetyConfig
from data_utils.replay_buffer import (
    DatasetConverter, ReplayBuffer, TrajectoryBuilder, BehaviorDataset,
)
from agents.bc_agent import BCAgent
from agents.classical_rl import DQNAgent, PPOAgent, SACAgent, DDPGAgent
from agents.offline_rl import CQLAgent, BCQAgent, BRACAgent
from agents.decision_transformer import DTAgent
from evaluation.ope import importance_sampling
from evaluation.evaluator import evaluate_policy, stress_test, compute_action_distribution_drift
from env.safety_shield import SafetyShield, SafetyConfig, build_per_household_shields


# ─────────────────────────────────────────────────────────────────────────────
#  Fixtures
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def toy_dataset():
    """Small synthetic dataset (100 rows, 1 household)."""
    rng = np.random.default_rng(42)
    n = 100
    ts = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    df = pd.DataFrame({
        "ts": ts,
        "household_id": "test_hh_01",
        "pv_gen_kw": rng.uniform(0, 2, n),
        "load_kw": rng.uniform(0.1, 1.5, n),
        "net_kw": rng.uniform(-1, 1, n),
        "soc_kwh": rng.uniform(1, 3, n),
        "soc_capacity_kwh": 4,
        "battery_power_kw": rng.uniform(-2, 2, n),
        "price_signal": rng.choice([3, 5, 8], n),
        "forecast_irradiance_1h": rng.uniform(0, 500, n),
        "forecast_irradiance_3h": rng.uniform(0, 500, n),
        "forecast_temp_1h": rng.uniform(10, 35, n),
        "actual_irradiance_wm2": rng.uniform(0, 800, n),
        "voltage_v": 230,
        "current_a": rng.uniform(0.5, 5, n),
        "offer_id": np.nan,
        "offered_kwh": np.nan,
        "offer_price": np.nan,
        "trade_id": np.nan,
        "commit_hash": np.nan,
        "event_flag": np.where(rng.random(n) < 0.05, "cloud_ramp", "normal"),
        "reward": rng.uniform(-0.5, 0.1, n),
        "safety_violation": False,
    })
    return df


@pytest.fixture
def env_cfg():
    return {
        "observation_keys": [
            "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
            "battery_power_kw", "price_signal",
            "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
            "actual_irradiance_wm2", "voltage_v", "current_a",
        ],
        "time_features": True,
        "neighbor_balance": True,
        "action_type": "discrete",
        "safety": {"soc_min_frac": 0.10, "soc_max_frac": 0.95,
                    "max_charge_kw": 3.0, "max_discharge_kw": 3.0},
        "reward": {},
        "history_length": 12,
        "seed": 42,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Environment
# ─────────────────────────────────────────────────────────────────────────────
class TestEnvironment:

    def test_reset(self, env_cfg, toy_dataset):
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        obs, info = env.reset(seed=42)
        assert obs.shape == (18,)  # 13 obs + 4 time + 1 neighbor
        assert "household_id" in info

    def test_step(self, env_cfg, toy_dataset):
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        obs, _ = env.reset(seed=42)
        action = 2  # idle
        obs2, reward, term, trunc, info = env.step(action)
        assert obs2.shape == obs.shape
        assert isinstance(reward, float)
        assert isinstance(info, dict)

    def test_episode_terminates(self, env_cfg, toy_dataset):
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        obs, _ = env.reset(seed=42)
        done = False
        steps = 0
        while not done:
            obs, _, term, trunc, _ = env.step(2)
            done = term or trunc
            steps += 1
        assert steps > 0
        assert steps <= len(toy_dataset) + 1

    def test_discrete_actions(self, env_cfg, toy_dataset):
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        obs, _ = env.reset()
        for a in range(7):
            env.reset()
            _, _, _, _, info = env.step(a)
            assert "action_kw" in info


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Safety Shield
# ─────────────────────────────────────────────────────────────────────────────
class TestSafetyShield:

    def test_clip_mode(self):
        cfg = SafetyConfig(soc_min_frac=0.10, soc_max_frac=0.95,
                           max_charge_kw=3.0, max_discharge_kw=3.0,
                           shield_mode="clip")
        shield = SafetyShield(cfg, DISCRETE_ACTION_MAP)
        # action that would charge over limit: soc=3.7, cap=4 → max=3.8
        action, intervened, reason = shield(1, soc=3.7, soc_capacity=4.0)  # charge_large=+3kW
        assert isinstance(action, int)
        # should clip because 3.7 + 3.0*(5/60) = 3.95 > 0.95*4=3.8
        assert intervened

    def test_fallback_mode(self):
        cfg = SafetyConfig(shield_mode="fallback")
        shield = SafetyShield(cfg, DISCRETE_ACTION_MAP)
        action, intervened, reason = shield(1, soc=3.7, soc_capacity=4.0)
        assert intervened
        assert "fallback" in reason

    def test_reject_mode(self):
        cfg = SafetyConfig(shield_mode="reject")
        shield = SafetyShield(cfg, DISCRETE_ACTION_MAP)
        # first call (no last safe action stored)
        action, intervened, reason = shield(1, soc=3.7, soc_capacity=4.0)
        assert intervened
        assert "rejected" in reason

    def test_safe_action_passes(self):
        cfg = SafetyConfig(shield_mode="clip")
        shield = SafetyShield(cfg, DISCRETE_ACTION_MAP)
        action, intervened, reason = shield(2, soc=2.0, soc_capacity=4.0)  # idle
        assert not intervened
        assert reason == ""


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Replay Buffer
# ─────────────────────────────────────────────────────────────────────────────
class TestReplayBuffer:

    def test_add_and_sample(self):
        buf = ReplayBuffer(capacity=100, seed=42)
        for i in range(50):
            buf.add(np.zeros(4), 0, 1.0, np.zeros(4), False)
        assert len(buf) == 50
        batch = buf.sample(16)
        assert batch["observations"].shape == (16, 4)
        assert batch["rewards"].shape == (16,)

    def test_capacity_overflow(self):
        buf = ReplayBuffer(capacity=10, seed=42)
        for i in range(20):
            buf.add(np.ones(2) * i, 0, float(i), np.ones(2), False)
        assert len(buf) == 10

    def test_prioritised_sample(self):
        buf = ReplayBuffer(capacity=100, seed=42)
        for i in range(50):
            buf.add(np.zeros(4), 0, 1.0, np.zeros(4), False, priority=float(i + 1))
        batch = buf.sample(16, prioritized=True)
        assert batch["observations"].shape == (16, 4)

    def test_save_load(self, tmp_path):
        buf = ReplayBuffer(capacity=100, seed=42)
        for i in range(10):
            buf.add(np.ones(3) * i, i % 3, float(i), np.ones(3), i == 9)
        path = str(tmp_path / "buf.pkl")
        buf.save(path)
        buf2 = ReplayBuffer()
        buf2.load(path)
        assert len(buf2) == len(buf)


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Dataset conversion
# ─────────────────────────────────────────────────────────────────────────────
class TestDatasetConverter:

    def test_convert(self, toy_dataset):
        conv = DatasetConverter(time_features=True, continuous=False)
        data = conv.convert(toy_dataset)
        assert "observations" in data
        assert "actions" in data
        assert len(data["observations"]) == len(toy_dataset) - 1  # n-1 transitions

    def test_trajectory_builder(self, toy_dataset):
        builder = TrajectoryBuilder(context_length=12, gamma=0.99)
        traj = builder.build(toy_dataset)
        assert "states" in traj
        assert traj["states"].ndim == 3
        assert traj["states"].shape[1] == 12


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: BC Agent (smoke)
# ─────────────────────────────────────────────────────────────────────────────
class TestBCAgent:

    def test_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        agent = BCAgent(obs_dim, 7, [64, 64], 1e-3, False, "cpu")
        history = agent.train(data, epochs=2, batch_size=32)
        assert len(history["train_loss"]) == 2
        action = agent.predict(data["observations"][0])
        assert 0 <= action < 7

    def test_save_load(self, toy_dataset, tmp_path):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        agent = BCAgent(obs_dim, 7, [64, 64], 1e-3, False, "cpu")
        agent.train(data, epochs=1, batch_size=32)
        path = str(tmp_path / "bc.pt")
        agent.save(path)
        agent2 = BCAgent(obs_dim, 7, [64, 64], 1e-3, False, "cpu")
        agent2.load(path)
        assert agent2.predict(data["observations"][0]) == agent.predict(data["observations"][0])


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: DT Agent (smoke)
# ─────────────────────────────────────────────────────────────────────────────
class TestDTAgent:

    def test_train_step(self, toy_dataset):
        builder = TrajectoryBuilder(context_length=12, gamma=0.99)
        traj = builder.build(toy_dataset)
        state_dim = traj["states"].shape[2]
        agent = DTAgent(state_dim, 7, d_model=32, n_heads=2, n_layers=2,
                        context_length=12, lr=1e-3, device="cpu")
        loss = agent.train_epoch(traj, batch_size=4)
        assert loss > 0

    def test_predict(self, toy_dataset):
        builder = TrajectoryBuilder(context_length=12, gamma=0.99)
        traj = builder.build(toy_dataset)
        state_dim = traj["states"].shape[2]
        agent = DTAgent(state_dim, 7, d_model=32, n_heads=2, n_layers=2,
                        context_length=12, lr=1e-3, device="cpu")
        agent.train_epoch(traj, batch_size=4)
        s = traj["states"][0]
        a = traj["actions"][0]
        r = traj["returns_to_go"][0]
        t = traj["timesteps"][0]
        action = agent.predict(s, a, r, t)
        assert 0 <= action < 7


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Offline RL agents (smoke)
# ─────────────────────────────────────────────────────────────────────────────
class TestOfflineAgents:

    def test_cql_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        agent = CQLAgent(obs_dim, 7, hidden=[64, 64], device="cpu")
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)

    def test_bcq_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        agent = BCQAgent(obs_dim, 7, hidden=[64, 64], device="cpu")
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)

    def test_dqn_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        agent = DQNAgent(obs_dim, 7, hidden=[64, 64], device="cpu")
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)

    def test_ppo_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        agent = PPOAgent(obs_dim, 7, hidden=[64, 64], device="cpu", n_epochs=2)
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: OPE
# ─────────────────────────────────────────────────────────────────────────────
class TestOPE:

    def test_importance_sampling(self):
        n = 100
        eval_lp = np.random.randn(n) * 0.1
        beh_lp = np.random.randn(n) * 0.1
        rewards = np.random.randn(n) * 0.5
        ep_starts = np.zeros(n, dtype=bool)
        ep_starts[0] = True
        ep_starts[50] = True

        result = importance_sampling(eval_lp, beh_lp, rewards, ep_starts)
        assert "estimate" in result
        assert "ci_lower" in result
        assert "ci_upper" in result
        assert result["n_episodes"] == 2


# ─────────────────────────────────────────────────────────────────────────────
#  Integration: smoke run
# ─────────────────────────────────────────────────────────────────────────────
class TestIntegrationSmoke:
    """
    End-to-end smoke tests (tiny dataset + 500 steps) for each algorithm.
    """

    @pytest.fixture
    def setup(self, toy_dataset, env_cfg):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        act_dim = 7
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        shield = SafetyShield(SafetyConfig(shield_mode="clip"), DISCRETE_ACTION_MAP)
        return {
            "data": data, "obs_dim": obs_dim, "act_dim": act_dim,
            "buf": buf, "env": env, "shield": shield,
            "toy_dataset": toy_dataset, "env_cfg": env_cfg,
        }

    @pytest.mark.parametrize("algo", ["BC", "CQL", "BCQ", "DQN", "PPO"])
    def test_smoke_algo(self, algo, setup):
        obs_dim = setup["obs_dim"]
        act_dim = setup["act_dim"]
        buf = setup["buf"]
        env = setup["env"]

        if algo == "BC":
            agent = BCAgent(obs_dim, act_dim, [64, 64], 1e-3, False, "cpu")
            agent.train(setup["data"], epochs=2, batch_size=32)
        elif algo == "CQL":
            agent = CQLAgent(obs_dim, act_dim, hidden=[64, 64], device="cpu")
            for _ in range(50):
                agent.train_step(buf.sample(32))
        elif algo == "BCQ":
            agent = BCQAgent(obs_dim, act_dim, hidden=[64, 64], device="cpu")
            for _ in range(50):
                agent.train_step(buf.sample(32))
        elif algo == "DQN":
            agent = DQNAgent(obs_dim, act_dim, hidden=[64, 64], device="cpu")
            for _ in range(50):
                agent.train_step(buf.sample(32))
        elif algo == "PPO":
            agent = PPOAgent(obs_dim, act_dim, hidden=[64, 64], device="cpu", n_epochs=2)
            for _ in range(50):
                agent.train_step(buf.sample(32))

        # evaluate
        metrics = evaluate_policy(env, agent.predict, n_episodes=2,
                                   shield=setup["shield"])
        assert "mean_reward" in metrics
        assert metrics["n_episodes"] == 2

    def test_smoke_dt(self, setup):
        builder = TrajectoryBuilder(context_length=12, gamma=0.99)
        traj = builder.build(setup["toy_dataset"])
        state_dim = traj["states"].shape[2]
        agent = DTAgent(state_dim, 7, d_model=32, n_heads=2, n_layers=2,
                        context_length=12, lr=1e-3, device="cpu")
        for _ in range(3):
            agent.train_epoch(traj, batch_size=4)

        def dt_policy(obs):
            L = 12
            s = np.tile(obs, (L, 1))
            a = np.zeros(L, dtype=np.int64)
            r = np.zeros(L, dtype=np.float32)
            t = np.arange(L)
            return agent.predict(s, a, r, t)

        metrics = evaluate_policy(setup["env"], dt_policy, n_episodes=2,
                                   shield=setup["shield"])
        assert "mean_reward" in metrics


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: SAC / DDPG / BRAC agents (smoke)
# ─────────────────────────────────────────────────────────────────────────────
class TestAdditionalAgents:

    def test_sac_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        act_dim = 2  # continuous
        agent = SACAgent(obs_dim, act_dim, hidden=[64, 64], device="cpu")
        buf = ReplayBuffer(1000, seed=42)
        # SAC needs continuous actions; fake them
        cont_data = dict(data)
        cont_data["actions"] = np.random.randn(len(data["actions"]), act_dim).astype(np.float32)
        buf.add_batch(cont_data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)

    def test_ddpg_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        act_dim = 2  # continuous
        agent = DDPGAgent(obs_dim, act_dim, hidden=[64, 64], device="cpu")
        buf = ReplayBuffer(1000, seed=42)
        cont_data = dict(data)
        cont_data["actions"] = np.random.randn(len(data["actions"]), act_dim).astype(np.float32)
        buf.add_batch(cont_data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)

    def test_brac_train_step(self, toy_dataset):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        bc = BCAgent(obs_dim, 7, [64, 64], 1e-3, False, "cpu")
        bc.train(data, epochs=1, batch_size=32)
        agent = BRACAgent(obs_dim, 7, hidden=[64, 64], device="cpu",
                          behavior_policy=bc)
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        batch = buf.sample(32)
        loss = agent.train_step(batch)
        assert isinstance(loss, float)


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Reservoir Sampling
# ─────────────────────────────────────────────────────────────────────────────
class TestReservoirSampling:

    def test_reservoir_add(self):
        buf = ReplayBuffer(capacity=10, seed=42)
        for i in range(100):
            buf.reservoir_add(np.ones(2) * i, 0, float(i), np.ones(2), False)
        assert len(buf) == 10
        assert buf._total_seen == 100

    def test_reservoir_uniform_coverage(self):
        """Over many runs, reservoir sampling should give roughly uniform coverage."""
        counts = np.zeros(50)
        for trial in range(200):
            buf = ReplayBuffer(capacity=10, seed=trial)
            for i in range(50):
                buf.reservoir_add(np.array([i]), 0, float(i), np.array([i]), False)
            for obs in buf.observations:
                counts[int(obs[0])] += 1
        # each item should appear with roughly equal probability
        # mean count = 200*10/50 = 40; allow wide tolerance
        assert counts.min() > 5, f"Some items never sampled: {counts}"


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Per-Household Safety
# ─────────────────────────────────────────────────────────────────────────────
class TestPerHouseholdSafety:

    def test_build_per_household_shields(self):
        households = {
            "hh_01": {"soc_min_frac": 0.15, "soc_max_frac": 0.90, "shield_mode": "clip"},
            "hh_02": {"soc_min_frac": 0.20, "soc_max_frac": 0.85, "shield_mode": "fallback"},
        }
        shields = build_per_household_shields(households, DISCRETE_ACTION_MAP)
        assert len(shields) == 2
        assert shields["hh_01"].cfg.soc_min_frac == 0.15
        assert shields["hh_02"].cfg.shield_mode == "fallback"

    def test_different_limits(self):
        households = {
            "a": {"soc_min_frac": 0.05, "max_charge_kw": 5.0},
            "b": {"soc_min_frac": 0.30, "max_charge_kw": 1.0},
        }
        shields = build_per_household_shields(households, DISCRETE_ACTION_MAP)
        assert shields["a"].cfg.max_charge_kw == 5.0
        assert shields["b"].cfg.max_charge_kw == 1.0


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Action Drift
# ─────────────────────────────────────────────────────────────────────────────
class TestActionDrift:

    def test_compute_drift(self, toy_dataset, env_cfg):
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        shield = SafetyShield(SafetyConfig(shield_mode="clip"), DISCRETE_ACTION_MAP)

        def random_policy(obs):
            return np.random.randint(0, 7)

        result = compute_action_distribution_drift(
            env, random_policy, random_policy, n_episodes=2, shield=shield,
        )
        assert "kl_divergence" in result
        assert "js_divergence" in result
        assert "tvd" in result


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Stress Tests
# ─────────────────────────────────────────────────────────────────────────────
class TestStressTests:

    def test_stress_test_runs(self, toy_dataset, env_cfg):
        env = MicrogridEnv(env_cfg, dataset=toy_dataset, domain_rand_cfg={})
        shield = SafetyShield(SafetyConfig(shield_mode="clip"), DISCRETE_ACTION_MAP)

        def idle_policy(obs):
            return 2  # idle

        results = stress_test(env, idle_policy,
                              scenarios=["cloud_ramp", "grid_outage", "sensor_dropout"],
                              n_episodes=1, shield=shield)
        assert len(results) == 3
        for scenario, m in results.items():
            assert "mean_reward" in m
            assert "safety_violation_rate" in m


# ─────────────────────────────────────────────────────────────────────────────
#  Unit tests: Edge Inference
# ─────────────────────────────────────────────────────────────────────────────
class TestEdgeInference:

    def test_safety_clip(self):
        from edge.edge_inference import safety_clip
        # should clip charge when SoC near max
        result = safety_clip(3.0, soc=3.7, soc_cap=4.0,
                             soc_min_frac=0.10, soc_max_frac=0.95)
        assert result <= 3.0
        # charging 3kW for 5min → delta=0.25kWh; 3.7+0.25=3.95 > 0.95*4=3.8
        assert result < 3.0

    def test_safety_clip_discharge(self):
        from edge.edge_inference import safety_clip
        result = safety_clip(-3.0, soc=0.5, soc_cap=4.0)
        # 0.5 + (-3.0)*(5/60) = 0.5-0.25 = 0.25 < 0.4 (soc_min=0.1*4)
        assert result > -3.0

    def test_infer_with_random_model(self, tmp_path):
        """Create a tiny TorchScript model and run inference."""
        model = torch.nn.Linear(18, 7)
        ts_path = str(tmp_path / "test_policy.torchscript")
        traced = torch.jit.trace(model, torch.randn(1, 18))
        traced.save(ts_path)

        from edge.edge_inference import load_torchscript, infer
        m, mtype = load_torchscript(ts_path)
        obs = np.random.randn(18).astype(np.float32)
        result = infer(m, mtype, obs)
        assert "action_index" in result
        assert 0 <= result["action_index"] < 7
        assert "action_name" in result


# ─────────────────────────────────────────────────────────────────────────────
#  Integration: SAC / DDPG / BRAC smoke runs
# ─────────────────────────────────────────────────────────────────────────────
class TestIntegrationAdditional:

    @pytest.fixture
    def setup(self, toy_dataset, env_cfg):
        conv = DatasetConverter(time_features=True)
        data = conv.convert(toy_dataset)
        obs_dim = data["observations"].shape[1]
        act_dim = 7
        buf = ReplayBuffer(1000, seed=42)
        buf.add_batch(data)
        env = MicrogridEnv(env_cfg, dataset=toy_dataset)
        shield = SafetyShield(SafetyConfig(shield_mode="clip"), DISCRETE_ACTION_MAP)
        bc = BCAgent(obs_dim, act_dim, [64, 64], 1e-3, False, "cpu")
        bc.train(data, epochs=1, batch_size=32)
        return {
            "data": data, "obs_dim": obs_dim, "act_dim": act_dim,
            "buf": buf, "env": env, "shield": shield, "bc": bc,
        }

    def test_smoke_brac(self, setup):
        agent = BRACAgent(setup["obs_dim"], setup["act_dim"],
                          hidden=[64, 64], device="cpu",
                          behavior_policy=setup["bc"])
        for _ in range(50):
            agent.train_step(setup["buf"].sample(32))
        metrics = evaluate_policy(setup["env"], agent.predict, n_episodes=2,
                                   shield=setup["shield"])
        assert "mean_reward" in metrics

    def test_smoke_sac(self, setup):
        act_dim = 2
        # Build continuous-action buffer
        cont_buf = ReplayBuffer(1000, seed=42)
        cont_data = dict(setup["data"])
        cont_data["actions"] = np.random.randn(len(setup["data"]["actions"]), act_dim).astype(np.float32)
        cont_buf.add_batch(cont_data)
        agent = SACAgent(setup["obs_dim"], act_dim, hidden=[64, 64], device="cpu")
        for _ in range(50):
            agent.train_step(cont_buf.sample(32))
        # SAC continuous → predict returns array; wrap for discrete env
        metrics = evaluate_policy(setup["env"],
                                   lambda obs: int(np.clip(agent.predict(obs)[0], 0, 6)),
                                   n_episodes=2, shield=setup["shield"])
        assert "mean_reward" in metrics

    def test_smoke_ddpg(self, setup):
        act_dim = 2
        cont_buf = ReplayBuffer(1000, seed=42)
        cont_data = dict(setup["data"])
        cont_data["actions"] = np.random.randn(len(setup["data"]["actions"]), act_dim).astype(np.float32)
        cont_buf.add_batch(cont_data)
        agent = DDPGAgent(setup["obs_dim"], act_dim, hidden=[64, 64], device="cpu")
        for _ in range(50):
            agent.train_step(cont_buf.sample(32))
        metrics = evaluate_policy(setup["env"],
                                   lambda obs: int(np.clip(agent.predict(obs)[0], 0, 6)),
                                   n_episodes=2, shield=setup["shield"])
        assert "mean_reward" in metrics
