"""
Safety Shield — modular action-constraint wrapper.

Modes
-----
* **clip**     : clip proposed action to safe range (SoC bounds, power limits).
* **fallback** : replace with a rule-based safe fallback action.
* **reject**   : reject the unsafe action, return last safe action, log incident.

The shield is *independent* of the agent and can be inserted between any
policy and any MicrogridEnv.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import gymnasium as gym
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class SafetyConfig:
    soc_min_frac: float = 0.10
    soc_max_frac: float = 0.95
    max_charge_kw: float = 3.0
    max_discharge_kw: float = 3.0
    max_grid_draw_kw: float = 5.0
    shield_mode: str = "clip"       # clip | fallback | reject
    log_incidents: bool = True
    max_log_entries: int = 50000     # cap to prevent unbounded memory growth


def build_per_household_shields(
    households: Dict[str, Dict],
    discrete_action_map: Optional[dict] = None,
) -> Dict[str, "SafetyShield"]:
    """Create one SafetyShield per household from a config dict.

    Parameters
    ----------
    households : dict
        Mapping ``household_id -> {soc_min_frac, soc_max_frac, ...}``.
        Each value is passed to ``SafetyConfig``.  Keys not in
        ``SafetyConfig`` are silently ignored.
    discrete_action_map : dict, optional
        Passed to every ``SafetyShield``.

    Returns
    -------
    dict[str, SafetyShield]
        One shield per household.
    """
    shields: Dict[str, SafetyShield] = {}
    for hid, hcfg in households.items():
        valid = {k: v for k, v in hcfg.items() if k in SafetyConfig.__dataclass_fields__}
        shields[hid] = SafetyShield(SafetyConfig(**valid), discrete_action_map)
    logger.info("Built per-household safety shields for %s", list(shields.keys()))
    return shields


class SafetyShield:
    """
    Stateless action-safety filter.

    Parameters
    ----------
    cfg : SafetyConfig or dict
        Safety constraint settings.
    discrete_action_map : dict, optional
        Mapping from discrete action index to (name, kw, is_offer).
    """

    def __init__(self, cfg, discrete_action_map: Optional[dict] = None):
        if isinstance(cfg, dict):
            cfg = SafetyConfig(**{k: v for k, v in cfg.items()
                                  if k in SafetyConfig.__dataclass_fields__})
        self.cfg = cfg
        self.discrete_action_map = discrete_action_map
        self.incident_log: List[Dict[str, Any]] = []
        self._incident_count: int = 0
        self._last_safe_action: Any = None

    # ── main entry ───────────────────────────────────────────────────────
    def __call__(
        self,
        action,
        soc: float,
        soc_capacity: float,
        continuous: bool = False,
        price_signal: float = 5.0,
    ) -> Tuple[Any, bool, str]:
        """
        Filter *action* through the safety shield.

        Returns
        -------
        safe_action : same type as *action*
            The (possibly modified) action.
        intervened : bool
            True if the shield changed or rejected the action.
        reason : str
            Human-readable explanation (empty if no intervention).
        """
        # decode to kw
        if continuous:
            action_kw = float(action[0])
        elif self.discrete_action_map is not None:
            action_kw = self.discrete_action_map[int(action)][1]
        else:
            action_kw = float(action)

        safe_kw, reason = self._check(action_kw, soc, soc_capacity)
        intervened = (safe_kw != action_kw) or bool(reason)

        if intervened:
            mode = self.cfg.shield_mode

            if mode == "clip":
                result = self._encode(safe_kw, continuous, action)
            elif mode == "fallback":
                fallback_kw = self._fallback_action(soc, soc_capacity, price_signal)
                result = self._encode(fallback_kw, continuous, action)
                reason = f"fallback override: {reason}"
            elif mode == "reject":
                if self._last_safe_action is not None:
                    result = self._last_safe_action
                else:
                    result = self._encode(0.0, continuous, action)  # idle
                reason = f"rejected: {reason}"
            else:
                result = self._encode(safe_kw, continuous, action)

            if self.cfg.log_incidents:
                if len(self.incident_log) < self.cfg.max_log_entries:
                    self.incident_log.append({
                        "mode": mode,
                        "original_kw": action_kw,
                        "safe_kw": safe_kw,
                        "soc": soc,
                        "reason": reason,
                    })
                self._incident_count += 1
                if self._incident_count % 10000 == 0:
                    logger.info("Safety shield: %d incidents total", self._incident_count)
        else:
            result = action

        self._last_safe_action = result
        return result, intervened, reason

    # ── internal checks ──────────────────────────────────────────────────
    def _check(self, action_kw: float, soc: float, soc_cap: float) -> Tuple[float, str]:
        reasons = []
        capped = action_kw

        # power limits
        if capped > self.cfg.max_charge_kw:
            capped = self.cfg.max_charge_kw
            reasons.append(f"charge capped to {self.cfg.max_charge_kw} kW")
        if capped < -self.cfg.max_discharge_kw:
            capped = -self.cfg.max_discharge_kw
            reasons.append(f"discharge capped to {self.cfg.max_discharge_kw} kW")

        # SoC bounds (5-min step → dt = 5/60 h)
        dt_h = 5.0 / 60.0
        new_soc = soc + capped * dt_h
        soc_lo = self.cfg.soc_min_frac * soc_cap
        soc_hi = self.cfg.soc_max_frac * soc_cap
        if new_soc < soc_lo:
            capped = (soc_lo - soc) / dt_h
            reasons.append(f"SoC would drop below {soc_lo:.2f} kWh")
        elif new_soc > soc_hi:
            capped = (soc_hi - soc) / dt_h
            reasons.append(f"SoC would exceed {soc_hi:.2f} kWh")

        return capped, "; ".join(reasons)

    def _fallback_action(self, soc: float, soc_cap: float, price: float) -> float:
        """Rule-based safe fallback: charge when price low & SoC low; else idle."""
        soc_frac = soc / soc_cap if soc_cap > 0 else 0.5
        if soc_frac < 0.3 and price < 5.0:
            return min(1.5, self.cfg.max_charge_kw)     # gentle charge
        elif soc_frac > 0.8 and price > 6.0:
            return max(-1.0, -self.cfg.max_discharge_kw)  # gentle sell
        return 0.0  # idle

    def _encode(self, kw: float, continuous: bool, original_action):
        """Re-encode kw back to the action representation."""
        if continuous:
            a = np.array(original_action, dtype=np.float32).copy()
            a[0] = kw
            return a
        if self.discrete_action_map is not None:
            # find nearest discrete action
            best, best_dist = 2, float("inf")  # default=idle
            for idx, (_, k, _) in self.discrete_action_map.items():
                d = abs(k - kw)
                if d < best_dist:
                    best, best_dist = idx, d
            return best
        return kw

    # ── utilities ────────────────────────────────────────────────────────
    def reset_log(self):
        self.incident_log.clear()
        self._incident_count = 0

    def get_incident_summary(self) -> Dict:
        return {
            "total_incidents": self._incident_count,
            "logged_incidents": len(self.incident_log),
            "modes": {m: sum(1 for e in self.incident_log if e["mode"] == m)
                      for m in ("clip", "fallback", "reject")},
        }


# ── Gym wrapper version ─────────────────────────────────────────────────────
class SafetyShieldWrapper(gym.ActionWrapper):
    """Gymnasium ActionWrapper that applies the SafetyShield to every step."""

    def __init__(self, env, shield: SafetyShield):
        super().__init__(env)
        self.shield = shield

    def action(self, action):
        soc = getattr(self.env, "_soc", 2.0)
        soc_cap = getattr(self.env, "_soc_cap", 4.0)
        continuous = getattr(self.env, "_continuous", False)
        price = 5.0  # default
        safe_action, intervened, reason = self.shield(
            action, soc, soc_cap, continuous=continuous, price_signal=price
        )
        return safe_action
