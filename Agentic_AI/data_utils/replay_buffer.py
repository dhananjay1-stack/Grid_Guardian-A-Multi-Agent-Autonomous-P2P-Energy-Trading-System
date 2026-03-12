"""
Data utilities — replay buffer, trajectory builder, dataset→transition converter.

Provides:
 - DatasetConverter : CSV → (s, a, r, s', done, info) transitions
 - ReplayBuffer     : step-based buffer with prioritised & reservoir sampling
 - TrajectoryBuilder: rolling trajectories for Decision Transformer
 - BehaviorDataset  : (s, a) pairs for BC training
"""
from __future__ import annotations

import hashlib
import json
import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── observation key list (matches env) ───────────────────────────────────────
OBS_KEYS = [
    "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
    "battery_power_kw", "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2", "voltage_v", "current_a",
]

TIME_FEATURE_DIM = 4   # sin/cos hour, sin/cos dow
NEIGHBOR_DIM = 1


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _row_to_obs(row: pd.Series, obs_keys: List[str],
                time_features: bool = True,
                neighbor_mean: float = 0.0) -> np.ndarray:
    """Convert a single DataFrame row to a flat observation vector."""
    vals = [float(row.get(k, 0.0)) if not pd.isna(row.get(k, 0.0)) else 0.0
            for k in obs_keys]
    if time_features:
        ts = row.get("ts")
        if isinstance(ts, str):
            ts = pd.Timestamp(ts)
        if ts is not None:
            h = ts.hour + ts.minute / 60.0
            d = ts.dayofweek
        else:
            h, d = 0.0, 0.0
        vals += [np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24),
                 np.sin(2 * np.pi * d / 7),  np.cos(2 * np.pi * d / 7)]
    vals.append(neighbor_mean)
    return np.array(vals, dtype=np.float32)


def _action_from_row(row: pd.Series) -> int:
    """Infer discrete action index from dataset row."""
    bp = float(row.get("battery_power_kw", 0.0))
    has_offer = not pd.isna(row.get("offer_id"))
    if has_offer and bp < 0:
        return 5   # offer_sell
    if has_offer:
        return 6   # offer_hold
    if bp >= 2.5:
        return 1   # charge_large
    if bp >= 0.5:
        return 0   # charge_small
    if bp <= -2.5:
        return 4   # discharge_large
    if bp <= -0.5:
        return 3   # discharge_small
    return 2       # idle


def _continuous_action_from_row(row: pd.Series) -> np.ndarray:
    bp = float(row.get("battery_power_kw", 0.0))
    price = float(row.get("offer_price", 0.0)) if not pd.isna(row.get("offer_price")) else 0.0
    return np.array([bp, price], dtype=np.float32)


# ─────────────────────────────────────────────────────────────────────────────
#  DatasetConverter
# ─────────────────────────────────────────────────────────────────────────────
class DatasetConverter:
    """Convert partitioned CSV to arrays of (s, a, r, s', done)."""

    def __init__(self, obs_keys: Optional[List[str]] = None,
                 time_features: bool = True,
                 continuous: bool = False):
        self.obs_keys = obs_keys or OBS_KEYS
        self.time_features = time_features
        self.continuous = continuous

    def convert(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        """Return dict with keys: observations, actions, rewards, next_observations, dones."""
        if "ts" in df.columns:
            df = df.copy()
            df["ts"] = pd.to_datetime(df["ts"], utc=True)

        households = sorted(df["household_id"].unique())
        ts_mean = df.groupby("ts")["net_kw"].mean() if len(households) > 1 else None

        all_obs, all_act, all_rew, all_next, all_done = [], [], [], [], []

        for hh in households:
            hh_df = df[df["household_id"] == hh].sort_values("ts").reset_index(drop=True)
            for i in range(len(hh_df) - 1):
                row = hh_df.iloc[i]
                nxt = hh_df.iloc[i + 1]
                nm = float(ts_mean.loc[row["ts"]]) if ts_mean is not None and row["ts"] in ts_mean.index else 0.0
                obs = _row_to_obs(row, self.obs_keys, self.time_features, nm)
                obs_n = _row_to_obs(nxt, self.obs_keys, self.time_features, nm)
                if self.continuous:
                    act = _continuous_action_from_row(row)
                else:
                    act = _action_from_row(row)
                rew = float(row.get("reward", 0.0))
                all_obs.append(obs)
                all_act.append(act)
                all_rew.append(rew)
                all_next.append(obs_n)
                all_done.append(False)
            # mark last step as done
            if len(hh_df) > 0:
                all_done[-1] = True

        return {
            "observations": np.array(all_obs, dtype=np.float32),
            "actions": np.array(all_act),
            "rewards": np.array(all_rew, dtype=np.float32),
            "next_observations": np.array(all_next, dtype=np.float32),
            "dones": np.array(all_done, dtype=bool),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  ReplayBuffer
# ─────────────────────────────────────────────────────────────────────────────
class ReplayBuffer:
    """Fixed-size replay buffer with uniform / prioritised / reservoir sampling."""

    def __init__(self, capacity: int = 500_000, seed: int = 42):
        self.capacity = capacity
        self._rng = np.random.default_rng(seed)
        self.observations: List[np.ndarray] = []
        self.actions: List[Any] = []
        self.rewards: List[float] = []
        self.next_observations: List[np.ndarray] = []
        self.dones: List[bool] = []
        self.priorities: List[float] = []
        self._pos = 0
        self._full = False
        self._total_seen: int = 0   # total items ever presented (for reservoir)

    def add(self, obs, action, reward, next_obs, done, priority=1.0):
        if len(self.observations) < self.capacity:
            self.observations.append(obs)
            self.actions.append(action)
            self.rewards.append(reward)
            self.next_observations.append(next_obs)
            self.dones.append(done)
            self.priorities.append(priority)
        else:
            idx = self._pos % self.capacity
            self.observations[idx] = obs
            self.actions[idx] = action
            self.rewards[idx] = reward
            self.next_observations[idx] = next_obs
            self.dones[idx] = done
            self.priorities[idx] = priority
            self._full = True
        self._pos += 1
        self._total_seen += 1

    def reservoir_add(self, obs, action, reward, next_obs, done, priority=1.0):
        """Reservoir sampling (Algorithm R): each item has equal probability
        of being retained regardless of stream length."""
        self._total_seen += 1
        if len(self.observations) < self.capacity:
            self.observations.append(obs)
            self.actions.append(action)
            self.rewards.append(reward)
            self.next_observations.append(next_obs)
            self.dones.append(done)
            self.priorities.append(priority)
        else:
            j = self._rng.integers(0, self._total_seen)
            if j < self.capacity:
                self.observations[j] = obs
                self.actions[j] = action
                self.rewards[j] = reward
                self.next_observations[j] = next_obs
                self.dones[j] = done
                self.priorities[j] = priority

    def add_batch(self, data: Dict[str, np.ndarray]):
        """Add a dict of arrays (from DatasetConverter) to the buffer."""
        n = len(data["observations"])
        for i in range(n):
            self.add(
                data["observations"][i],
                data["actions"][i],
                data["rewards"][i],
                data["next_observations"][i],
                data["dones"][i],
            )

    def sample(self, batch_size: int, prioritized: bool = False) -> Dict[str, np.ndarray]:
        n = len(self.observations)
        if prioritized:
            probs = np.array(self.priorities[:n], dtype=np.float64)
            probs /= probs.sum()
            idxs = self._rng.choice(n, size=batch_size, p=probs)
        else:
            idxs = self._rng.integers(0, n, size=batch_size)
        return {
            "observations": np.array([self.observations[i] for i in idxs]),
            "actions": np.array([self.actions[i] for i in idxs]),
            "rewards": np.array([self.rewards[i] for i in idxs]),
            "next_observations": np.array([self.next_observations[i] for i in idxs]),
            "dones": np.array([self.dones[i] for i in idxs]),
        }

    def __len__(self):
        return len(self.observations)

    # ── persistence ──────────────────────────────────────────────────────
    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump({
                "observations": self.observations,
                "actions": self.actions,
                "rewards": self.rewards,
                "next_observations": self.next_observations,
                "dones": self.dones,
                "priorities": self.priorities,
            }, f)
        logger.info("Replay buffer saved to %s (%d transitions)", path, len(self))

    def load(self, path: str):
        with open(path, "rb") as f:
            d = pickle.load(f)
        self.observations = d["observations"]
        self.actions = d["actions"]
        self.rewards = d["rewards"]
        self.next_observations = d["next_observations"]
        self.dones = d["dones"]
        self.priorities = d.get("priorities", [1.0] * len(self.observations))
        self._total_seen = d.get("_total_seen", len(self.observations))
        logger.info("Replay buffer loaded from %s (%d transitions)", path, len(self))


# ─────────────────────────────────────────────────────────────────────────────
#  TrajectoryBuilder  (for Decision Transformer)
# ─────────────────────────────────────────────────────────────────────────────
class TrajectoryBuilder:
    """Build rolling trajectory sequences with returns-to-go for DT."""

    def __init__(self, context_length: int = 24, gamma: float = 0.99,
                 obs_keys: Optional[List[str]] = None,
                 time_features: bool = True, continuous: bool = False):
        self.context_length = context_length
        self.gamma = gamma
        self.obs_keys = obs_keys or OBS_KEYS
        self.time_features = time_features
        self.continuous = continuous

    def build(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        """
        Returns dict with:
            states   : (N, L, obs_dim)
            actions  : (N, L) or (N, L, act_dim)
            returns_to_go : (N, L)
            timesteps: (N, L)
            masks    : (N, L)  — 1 for real, 0 for padding
        """
        if "ts" in df.columns:
            df = df.copy()
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
        households = sorted(df["household_id"].unique())
        ts_mean = df.groupby("ts")["net_kw"].mean() if len(households) > 1 else None

        all_states, all_actions, all_rtg, all_ts, all_masks = [], [], [], [], []

        for hh in households:
            hh_df = df[df["household_id"] == hh].sort_values("ts").reset_index(drop=True)
            # build observations and actions for full episode
            obs_list, act_list, rew_list = [], [], []
            for i in range(len(hh_df)):
                row = hh_df.iloc[i]
                nm = float(ts_mean.loc[row["ts"]]) if ts_mean is not None and row["ts"] in ts_mean.index else 0.0
                obs_list.append(_row_to_obs(row, self.obs_keys, self.time_features, nm))
                if self.continuous:
                    act_list.append(_continuous_action_from_row(row))
                else:
                    act_list.append(_action_from_row(row))
                rew_list.append(float(row.get("reward", 0.0)))

            obs_arr = np.array(obs_list)
            act_arr = np.array(act_list)
            rew_arr = np.array(rew_list)

            # compute returns-to-go
            rtg = np.zeros_like(rew_arr)
            rtg[-1] = rew_arr[-1]
            for t in range(len(rew_arr) - 2, -1, -1):
                rtg[t] = rew_arr[t] + self.gamma * rtg[t + 1]

            # rolling windows
            L = self.context_length
            for start in range(0, len(hh_df) - L + 1, L // 2):
                end = start + L
                if end > len(hh_df):
                    break
                all_states.append(obs_arr[start:end])
                all_actions.append(act_arr[start:end])
                all_rtg.append(rtg[start:end])
                all_ts.append(np.arange(start, end))
                all_masks.append(np.ones(L))

        if not all_states:
            obs_dim = len(self.obs_keys) + (TIME_FEATURE_DIM if self.time_features else 0) + NEIGHBOR_DIM
            return {
                "states": np.zeros((0, self.context_length, obs_dim), dtype=np.float32),
                "actions": np.zeros((0, self.context_length), dtype=np.int64),
                "returns_to_go": np.zeros((0, self.context_length), dtype=np.float32),
                "timesteps": np.zeros((0, self.context_length), dtype=np.int64),
                "masks": np.zeros((0, self.context_length), dtype=np.float32),
            }

        return {
            "states": np.array(all_states, dtype=np.float32),
            "actions": np.array(all_actions),
            "returns_to_go": np.array(all_rtg, dtype=np.float32),
            "timesteps": np.array(all_ts, dtype=np.int64),
            "masks": np.array(all_masks, dtype=np.float32),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  BehaviorDataset  (for BC and KL penalties)
# ─────────────────────────────────────────────────────────────────────────────
class BehaviorDataset:
    """Supervised (s, a) pairs for behaviour cloning."""

    def __init__(self, obs_keys: Optional[List[str]] = None,
                 time_features: bool = True, continuous: bool = False):
        self.obs_keys = obs_keys or OBS_KEYS
        self.time_features = time_features
        self.continuous = continuous

    def build(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        if "ts" in df.columns:
            df = df.copy()
            df["ts"] = pd.to_datetime(df["ts"], utc=True)
        households = sorted(df["household_id"].unique())
        ts_mean = df.groupby("ts")["net_kw"].mean() if len(households) > 1 else None
        obs_list, act_list = [], []
        for hh in households:
            hh_df = df[df["household_id"] == hh].sort_values("ts").reset_index(drop=True)
            for i in range(len(hh_df)):
                row = hh_df.iloc[i]
                nm = float(ts_mean.loc[row["ts"]]) if ts_mean is not None and row["ts"] in ts_mean.index else 0.0
                obs_list.append(_row_to_obs(row, self.obs_keys, self.time_features, nm))
                if self.continuous:
                    act_list.append(_continuous_action_from_row(row))
                else:
                    act_list.append(_action_from_row(row))
        return {
            "observations": np.array(obs_list, dtype=np.float32),
            "actions": np.array(act_list),
        }


# ── Dataset hash utility ────────────────────────────────────────────────────
def dataset_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]
