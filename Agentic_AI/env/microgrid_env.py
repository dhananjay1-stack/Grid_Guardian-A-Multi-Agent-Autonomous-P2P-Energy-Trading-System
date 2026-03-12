"""
Grid-Guardian Microgrid Gym Environment.

Implements Gymnasium API with:
 - Dataset-driven replay (offline mode)
 - Vectorized env support
 - Fault injection (cloud ramps, outages, EV spikes)
 - Safety-shield wrapper integration
 - Domain randomization
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import gymnasium as gym
import numpy as np
import pandas as pd
from gymnasium import spaces

logger = logging.getLogger(__name__)

# ── constants ────────────────────────────────────────────────────────────────
OBS_KEYS_DEFAULT = [
    "soc_kwh", "soc_capacity_kwh", "pv_gen_kw", "load_kw", "net_kw",
    "battery_power_kw", "price_signal",
    "forecast_irradiance_1h", "forecast_irradiance_3h", "forecast_temp_1h",
    "actual_irradiance_wm2", "voltage_v", "current_a",
]

DISCRETE_ACTION_MAP = {
    0: ("charge_small",    +1.0, False),
    1: ("charge_large",    +3.0, False),
    2: ("idle",             0.0, False),
    3: ("discharge_small", -1.0, False),
    4: ("discharge_large", -3.0, False),
    5: ("offer_sell",      -1.5, True),
    6: ("offer_hold",       0.0, True),
}


class MicrogridEnv(gym.Env):
    """
    Gym-like microgrid environment driven by the Grid-Guardian dataset.

    Modes
    -----
    * **replay** (default): steps through the dataset row-by-row; the
      agent observes the *recorded* state and the reward is computed from
      the dataset reward column (with optional recomputation).
    * **sim**: lightweight simulation where SoC evolves according to
      the chosen action.  Used for online / hybrid training.
    """

    metadata = {"render_modes": ["human"]}

    # ── construction ─────────────────────────────────────────────────────
    def __init__(
        self,
        cfg: Dict[str, Any],
        dataset: Optional[pd.DataFrame] = None,
        mode: str = "replay",
        household_id: Optional[str] = None,
        domain_rand_cfg: Optional[Dict] = None,
    ):
        super().__init__()
        self.cfg = cfg
        self.mode = mode
        self.household_id = household_id
        self.domain_rand_cfg = domain_rand_cfg or {}
        self._rng = np.random.default_rng(cfg.get("seed", 42))

        # ── observation keys ─────────────────────────────────────────────
        self.obs_keys: List[str] = cfg.get("observation_keys", OBS_KEYS_DEFAULT)
        self.use_time_features = cfg.get("time_features", True)
        self.use_neighbor = cfg.get("neighbor_balance", True)
        self.history_length = cfg.get("history_length", 24)

        # ── action space ─────────────────────────────────────────────────
        action_type = cfg.get("action_type", "discrete")
        if action_type == "discrete":
            self.action_space = spaces.Discrete(len(DISCRETE_ACTION_MAP))
            self._continuous = False
        else:
            lo = np.array(cfg.get("continuous_action_low", [-3.0, 0.0]), dtype=np.float32)
            hi = np.array(cfg.get("continuous_action_high", [3.0, 10.0]), dtype=np.float32)
            self.action_space = spaces.Box(lo, hi, dtype=np.float32)
            self._continuous = True

        # ── observation space (computed after dataset load) ──────────────
        self._obs_dim = len(self.obs_keys)
        if self.use_time_features:
            self._obs_dim += 4          # sin/cos hour, sin/cos dow
        if self.use_neighbor:
            self._obs_dim += 1          # mean neighbour net_kw
        self.observation_space = spaces.Box(
            -np.inf, np.inf, shape=(self._obs_dim,), dtype=np.float32
        )

        # ── safety constraints ───────────────────────────────────────────
        safety = cfg.get("safety", {})
        self.soc_min_frac = safety.get("soc_min_frac", 0.10)
        self.soc_max_frac = safety.get("soc_max_frac", 0.95)
        self.max_charge_kw = safety.get("max_charge_kw", 3.0)
        self.max_discharge_kw = safety.get("max_discharge_kw", 3.0)
        self.max_grid_draw_kw = safety.get("max_grid_draw_kw", 5.0)

        # ── reward weights ───────────────────────────────────────────────
        rw = cfg.get("reward", {})
        self.w_cost = rw.get("cost_savings_weight", 1.0)
        self.w_stab = rw.get("stability_bonus_weight", 0.1)
        self.w_deg  = rw.get("battery_deg_cost_weight", 0.05)
        self.safety_penalty = rw.get("safety_penalty", -10.0)

        # ── load dataset ─────────────────────────────────────────────────
        self._raw_df: Optional[pd.DataFrame] = None
        self._episodes: List[pd.DataFrame] = []
        if dataset is not None:
            self.load_dataset(dataset)

        # ── episode state ────────────────────────────────────────────────
        self._ep_idx = 0
        self._step_idx = 0
        self._ep_df: Optional[pd.DataFrame] = None
        self._soc: float = 0.0
        self._soc_cap: float = 4.0
        self._done = False

        # domain-rand perturbations (set per episode)
        self._dr_irr_scale = 1.0
        self._dr_forecast_shift = 0.0
        self._dr_inv_eff = 0.90
        self._dr_latency_steps = 0
        self._dr_sensor_dropout_mask: Optional[np.ndarray] = None

    # ── dataset loading ──────────────────────────────────────────────────
    def load_dataset(self, df: pd.DataFrame):
        """Load and split dataset into per-household episodes."""
        self._raw_df = df.copy()
        if "ts" in self._raw_df.columns:
            self._raw_df["ts"] = pd.to_datetime(self._raw_df["ts"], utc=True)
        households = sorted(self._raw_df["household_id"].unique())
        if self.household_id:
            households = [self.household_id]
        self._episodes = []
        for hh in households:
            hh_df = self._raw_df[self._raw_df["household_id"] == hh].sort_values("ts").reset_index(drop=True)
            self._episodes.append(hh_df)
        # pre-compute neighbour balance per timestep
        if self.use_neighbor and len(households) > 1:
            ts_mean = self._raw_df.groupby("ts")["net_kw"].mean()
            self._ts_mean_net = ts_mean
        else:
            self._ts_mean_net = None

    # ── observation construction ─────────────────────────────────────────
    def _make_obs(self, row: pd.Series) -> np.ndarray:
        vals = []
        for k in self.obs_keys:
            v = row.get(k, 0.0)
            vals.append(float(v) if not pd.isna(v) else 0.0)

        # apply domain-randomisation noise
        vals = self._apply_domain_rand(vals, row)

        if self.use_time_features:
            ts = row.get("ts", pd.Timestamp("2025-01-01", tz="UTC"))
            if isinstance(ts, str):
                ts = pd.Timestamp(ts)
            hour_frac = ts.hour + ts.minute / 60.0
            dow = ts.dayofweek
            vals.extend([
                np.sin(2 * np.pi * hour_frac / 24),
                np.cos(2 * np.pi * hour_frac / 24),
                np.sin(2 * np.pi * dow / 7),
                np.cos(2 * np.pi * dow / 7),
            ])
        if self.use_neighbor:
            if self._ts_mean_net is not None and row.get("ts") in self._ts_mean_net.index:
                vals.append(float(self._ts_mean_net.loc[row["ts"]]))
            else:
                vals.append(0.0)
        return np.array(vals, dtype=np.float32)

    def _apply_domain_rand(self, vals: list, row: pd.Series) -> list:
        """Apply per-episode domain-randomization perturbations."""
        if not self.domain_rand_cfg.get("enabled", False):
            return vals
        # sensor dropout
        if self._dr_sensor_dropout_mask is not None:
            for i in range(len(self.obs_keys)):
                if self._dr_sensor_dropout_mask[i]:
                    vals[i] = 0.0
        # irradiance scale + forecast shift
        for i, k in enumerate(self.obs_keys):
            if k == "actual_irradiance_wm2":
                vals[i] *= self._dr_irr_scale
            if k.startswith("forecast_irradiance"):
                vals[i] = vals[i] * self._dr_irr_scale + self._dr_forecast_shift
        # tariff / price shift
        for i, k in enumerate(self.obs_keys):
            if k == "price_signal":
                vals[i] += self._dr_tariff_shift
        # latency: use an observation from a previous step if latency > 0
        if self._dr_latency_steps > 0 and hasattr(self, '_obs_buffer'):
            lag = min(self._dr_latency_steps, len(self._obs_buffer))
            if lag > 0:
                return self._obs_buffer[-lag]  # return a delayed obs
        return vals

    # ── reward computation ───────────────────────────────────────────────
    def _compute_reward(self, row: pd.Series, action_kw: float, safety_violated: bool) -> float:
        """Compute reward from row + chosen action."""
        if self.mode == "replay":
            base_reward = float(row.get("reward", 0.0))
        else:
            # sim-mode reward
            price = float(row.get("price_signal", 5.0))
            cost = action_kw * price / 12.0   # 5-min fraction of hour
            stability = -abs(float(row.get("net_kw", 0.0)))
            deg = -abs(action_kw) * 0.001
            base_reward = (
                self.w_cost * (-cost) +
                self.w_stab * stability +
                self.w_deg  * deg
            )
        if safety_violated:
            base_reward += self.safety_penalty
        return base_reward

    # ── safety check ─────────────────────────────────────────────────────
    def check_safety(self, action_kw: float) -> Tuple[float, bool]:
        """Clip action to safety bounds; return (clipped_kw, violated)."""
        violated = False
        capped = action_kw
        # power limits
        capped = np.clip(capped, -self.max_discharge_kw, self.max_charge_kw)
        # SoC bounds (5-min step)
        new_soc = self._soc + capped * (5 / 60)
        soc_lo = self.soc_min_frac * self._soc_cap
        soc_hi = self.soc_max_frac * self._soc_cap
        if new_soc < soc_lo:
            capped = (soc_lo - self._soc) / (5 / 60)
            violated = True
        elif new_soc > soc_hi:
            capped = (soc_hi - self._soc) / (5 / 60)
            violated = True
        if capped != action_kw:
            violated = True
        return float(capped), violated

    # ── domain-rand sampling (per-episode) ───────────────────────────────
    def _sample_domain_rand(self):
        dr = self.domain_rand_cfg
        if not dr.get("enabled", False):
            self._dr_params = {}
            return
        self._dr_irr_scale = 1.0 + self._rng.normal(0, dr.get("irradiance_noise_std", 0.08))
        self._dr_forecast_shift = self._rng.normal(0, dr.get("forecast_noise_std", 0.10)) * 100
        eff_lo, eff_hi = dr.get("inverter_eff_range", [0.85, 0.95])
        self._dr_inv_eff = self._rng.uniform(eff_lo, eff_hi)
        lat_lo, lat_hi = dr.get("latency_ms", [0, 100])
        self._dr_latency_ms = float(self._rng.uniform(lat_lo, lat_hi))
        self._dr_latency_steps = max(0, int(self._dr_latency_ms / (5 * 60 * 1000)))
        dropout_p = dr.get("sensor_dropout_prob", 0.02)
        self._dr_sensor_dropout_mask = self._rng.random(len(self.obs_keys)) < dropout_p
        # tariff / price shift
        tariff_range = dr.get("tariff_shift_range", [0.0, 0.0])
        self._dr_tariff_shift = self._rng.uniform(tariff_range[0], tariff_range[1])
        # store per-episode DR params for manifest recording
        self._dr_params = {
            "irr_scale": float(self._dr_irr_scale),
            "forecast_shift": float(self._dr_forecast_shift),
            "inv_eff": float(self._dr_inv_eff),
            "latency_ms": float(self._dr_latency_ms),
            "tariff_shift": float(self._dr_tariff_shift),
            "sensor_dropout_mask": self._dr_sensor_dropout_mask.tolist(),
        }

    # ── Gymnasium API ────────────────────────────────────────────────────
    def reset(self, *, seed=None, options=None) -> Tuple[np.ndarray, Dict]:
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        if not self._episodes:
            raise RuntimeError("No dataset loaded. Call load_dataset() or pass dataset= in __init__.")
        self._ep_idx = self._rng.integers(0, len(self._episodes))
        self._ep_df = self._episodes[self._ep_idx].copy()
        self._step_idx = 0
        row = self._ep_df.iloc[0]
        self._soc = float(row.get("soc_kwh", 2.0))
        self._soc_cap = float(row.get("soc_capacity_kwh", 4.0))
        self._done = False
        self._obs_buffer: list = []  # for latency simulation
        self._sample_domain_rand()
        obs = self._make_obs(row)
        self._obs_buffer.append(list(obs[:len(self.obs_keys)]))
        info = {"household_id": str(row.get("household_id", "")),
                "ts": str(row.get("ts", "")),
                "event_flag": str(row.get("event_flag", "normal")),
                "dr_params": getattr(self, '_dr_params', {})}
        return obs, info

    def step(self, action) -> Tuple[np.ndarray, float, bool, bool, Dict]:
        if self._done:
            raise RuntimeError("Episode is done; call reset().")

        # decode action
        if self._continuous:
            action_kw = float(action[0])
        else:
            action_kw = DISCRETE_ACTION_MAP[int(action)][1]

        # safety check (env-level)
        action_kw, violated = self.check_safety(action_kw)

        # advance
        self._step_idx += 1
        truncated = self._step_idx >= len(self._ep_df)
        if truncated:
            self._step_idx = len(self._ep_df) - 1
            self._done = True

        row = self._ep_df.iloc[self._step_idx]

        # update SoC (sim mode) or use dataset SoC (replay)
        if self.mode == "sim":
            self._soc += action_kw * (5 / 60) * self._dr_inv_eff
            self._soc = np.clip(self._soc,
                                self.soc_min_frac * self._soc_cap,
                                self.soc_max_frac * self._soc_cap)
        else:
            self._soc = float(row.get("soc_kwh", self._soc))

        reward = self._compute_reward(row, action_kw, violated)
        obs = self._make_obs(row)
        # keep obs buffer for latency simulation
        if hasattr(self, '_obs_buffer'):
            self._obs_buffer.append(list(obs[:len(self.obs_keys)]))
            if len(self._obs_buffer) > 50:
                self._obs_buffer = self._obs_buffer[-50:]
        terminated = False  # no natural termination for microgrid
        info = {
            "household_id": str(row.get("household_id", "")),
            "ts": str(row.get("ts", "")),
            "event_flag": str(row.get("event_flag", "normal")),
            "safety_violation": violated,
            "soc": self._soc,
            "action_kw": action_kw,
            "estimated_grid_draw": float(row.get("net_kw", 0.0)) - action_kw,
            "behavior_log_prob": float("nan"),  # filled by BC agent post-hoc for OPE
        }
        return obs, reward, terminated, truncated or self._done, info

    @property
    def num_steps_in_episode(self) -> int:
        return len(self._ep_df) if self._ep_df is not None else 0


# ── Fault-injection wrapper ──────────────────────────────────────────────────
class FaultInjectionWrapper(gym.Wrapper):
    """Injects cloud-ramp, outage, and EV-spike events stochastically."""

    def __init__(self, env: MicrogridEnv, cloud_prob: float = 0.01,
                 outage_prob: float = 0.002, ev_spike_prob: float = 0.005):
        super().__init__(env)
        self.cloud_prob = cloud_prob
        self.outage_prob = outage_prob
        self.ev_spike_prob = ev_spike_prob
        self._rng = np.random.default_rng(42)

    def step(self, action):
        obs, reward, term, trunc, info = self.env.step(action)
        # inject faults
        r = self._rng.random()
        if r < self.cloud_prob:
            info["event_flag"] = "cloud_ramp"
            # reduce irradiance in obs
            obs = obs.copy()
            for i, k in enumerate(self.env.obs_keys):
                if "irradiance" in k:
                    obs[i] *= 0.3
        elif r < self.cloud_prob + self.outage_prob:
            info["event_flag"] = "grid_outage"
            reward -= 2.0
        elif r < self.cloud_prob + self.outage_prob + self.ev_spike_prob:
            info["event_flag"] = "ev_arrival"
            # spike load
            for i, k in enumerate(self.env.obs_keys):
                if k == "load_kw":
                    obs[i] *= 2.5
        return obs, reward, term, trunc, info


# ── Vectorized env factory ───────────────────────────────────────────────────
def make_vec_env(cfg: Dict, dataset: pd.DataFrame, n_envs: int = 4,
                 mode: str = "replay", domain_rand_cfg: Optional[Dict] = None):
    """Create vectorized environments using gymnasium's SyncVectorEnv."""
    def _make(idx):
        def _init():
            env_cfg = dict(cfg)
            env_cfg["seed"] = cfg.get("seed", 42) + idx
            e = MicrogridEnv(env_cfg, dataset=dataset, mode=mode,
                             domain_rand_cfg=domain_rand_cfg)
            return e
        return _init
    return gym.vector.SyncVectorEnv([_make(i) for i in range(n_envs)])
