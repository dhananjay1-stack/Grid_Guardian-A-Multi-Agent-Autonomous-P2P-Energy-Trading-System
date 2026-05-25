"""
Offline Policy Evaluation (OPE) module.

Implements:
 - Importance Sampling (IS)
 - Weighted Importance Sampling (WIS)
 - Per-Decision Importance Sampling (PDIS)
 - Fitted Q-Evaluation (FQE)
 - Doubly Robust (DR)

All estimators produce point estimates and bootstrap confidence intervals.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _bootstrap_ci(values: np.ndarray, n_boot: int = 200,
                  confidence: float = 0.95) -> Tuple[float, float, float]:
    """Return (mean, ci_lower, ci_upper) via bootstrap."""
    rng = np.random.default_rng(42)
    means = []
    for _ in range(n_boot):
        sample = rng.choice(values, size=len(values), replace=True)
        means.append(sample.mean())
    means = np.array(means)
    alpha = 1 - confidence
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return float(np.mean(values)), lo, hi


# ─────────────────────────────────────────────────────────────────────────────
#  IS / WIS
# ─────────────────────────────────────────────────────────────────────────────
def importance_sampling(
    eval_log_probs: np.ndarray,     # log π_e(a|s) for each transition
    behavior_log_probs: np.ndarray, # log π_β(a|s) for each transition
    rewards: np.ndarray,
    episode_starts: np.ndarray,     # boolean: True at episode start
    gamma: float = 0.99,
    weighted: bool = False,
    n_bootstrap: int = 200,
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """
    Importance Sampling / Weighted IS estimator.

    Returns dict with estimate, ci_lower, ci_upper, individual episode values.
    """
    # split into episodes
    ep_starts = np.where(episode_starts)[0]
    ep_ends = np.append(ep_starts[1:], len(rewards))

    ep_values = []
    ep_weights = []
    for s, e in zip(ep_starts, ep_ends):
        log_ratios = eval_log_probs[s:e] - behavior_log_probs[s:e]
        # cumulative product of ratios
        cum_log_ratio = np.cumsum(log_ratios)
        rho = np.exp(cum_log_ratio)
        discounts = gamma ** np.arange(e - s)
        ep_return = np.sum(discounts * rewards[s:e])
        total_rho = rho[-1] if len(rho) > 0 else 1.0
        ep_values.append(total_rho * ep_return)
        ep_weights.append(total_rho)

    ep_values = np.array(ep_values)
    ep_weights = np.array(ep_weights)

    if weighted:
        estimate = float(np.sum(ep_values) / max(np.sum(ep_weights), 1e-10))
    else:
        estimate = float(np.mean(ep_values))

    mean, lo, hi = _bootstrap_ci(ep_values, n_bootstrap, confidence)

    return {
        "method": "WIS" if weighted else "IS",
        "estimate": estimate,
        "mean": mean,
        "ci_lower": lo,
        "ci_upper": hi,
        "n_episodes": len(ep_values),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Per-Decision IS
# ─────────────────────────────────────────────────────────────────────────────
def per_decision_is(
    eval_log_probs: np.ndarray,
    behavior_log_probs: np.ndarray,
    rewards: np.ndarray,
    episode_starts: np.ndarray,
    gamma: float = 0.99,
    n_bootstrap: int = 200,
    confidence: float = 0.95,
) -> Dict[str, Any]:
    ep_starts = np.where(episode_starts)[0]
    ep_ends = np.append(ep_starts[1:], len(rewards))
    ep_values = []
    for s, e in zip(ep_starts, ep_ends):
        log_ratios = eval_log_probs[s:e] - behavior_log_probs[s:e]
        cum_log = np.cumsum(log_ratios)
        rho_t = np.exp(cum_log)
        discounts = gamma ** np.arange(e - s)
        pdis = np.sum(rho_t * discounts * rewards[s:e])
        ep_values.append(pdis)
    ep_values = np.array(ep_values)
    mean, lo, hi = _bootstrap_ci(ep_values, n_bootstrap, confidence)
    return {"method": "PDIS", "estimate": mean, "ci_lower": lo, "ci_upper": hi,
            "n_episodes": len(ep_values)}


# ─────────────────────────────────────────────────────────────────────────────
#  FQE — Fitted Q-Evaluation
# ─────────────────────────────────────────────────────────────────────────────
class FQENetwork(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden=[256, 256]):
        super().__init__()
        layers = []
        d = obs_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            d = h
        layers.append(nn.Linear(d, act_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x)


def fitted_q_evaluation(
    observations: np.ndarray,
    actions: np.ndarray,
    rewards: np.ndarray,
    next_observations: np.ndarray,
    dones: np.ndarray,
    eval_policy_fn,                 # callable: obs → action (int)
    gamma: float = 0.99,
    n_steps: int = 50000,
    batch_size: int = 256,
    lr: float = 3e-4,
    hidden: list = [256, 256],
    device: str = "cpu",
    n_bootstrap: int = 200,
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """
    Fitted Q-Evaluation: learn Q^π_e by iterating Bellman operator.
    Returns estimated policy value and confidence intervals.
    """
    dev = torch.device(device)
    obs_dim = observations.shape[1]
    act_dim = int(actions.max()) + 1

    q = FQENetwork(obs_dim, act_dim, hidden).to(dev)
    q_target = FQENetwork(obs_dim, act_dim, hidden).to(dev)
    q_target.load_state_dict(q.state_dict())
    optimizer = optim.Adam(q.parameters(), lr=lr)

    n = len(observations)
    rng = np.random.default_rng(42)

    for step in range(n_steps):
        idxs = rng.integers(0, n, size=batch_size)
        obs = torch.as_tensor(observations[idxs], dtype=torch.float32).to(dev)
        act = torch.as_tensor(actions[idxs], dtype=torch.long).to(dev)
        rew = torch.as_tensor(rewards[idxs], dtype=torch.float32).to(dev)
        nobs = torch.as_tensor(next_observations[idxs], dtype=torch.float32).to(dev)
        done = torch.as_tensor(dones[idxs], dtype=torch.float32).to(dev)

        q_val = q(obs).gather(1, act.unsqueeze(1)).squeeze(1)

        with torch.no_grad():
            # next action under eval policy
            nobs_np = next_observations[idxs]
            next_acts = np.array([eval_policy_fn(nobs_np[i]) for i in range(len(nobs_np))])
            na = torch.as_tensor(next_acts, dtype=torch.long).to(dev)
            q_next = q_target(nobs).gather(1, na.unsqueeze(1)).squeeze(1)
            target = rew + gamma * q_next * (1 - done)

        loss = nn.functional.mse_loss(q_val, target)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if step % 1000 == 0:
            for p, tp in zip(q.parameters(), q_target.parameters()):
                tp.data.copy_(0.005 * p.data + 0.995 * tp.data)

    # estimate value at initial states
    q.eval()
    with torch.no_grad():
        all_obs = torch.as_tensor(observations, dtype=torch.float32).to(dev)
        all_q = q(all_obs)
        # get policy actions
        policy_acts = np.array([eval_policy_fn(observations[i]) for i in range(len(observations))])
        pa = torch.as_tensor(policy_acts, dtype=torch.long).to(dev)
        values = all_q.gather(1, pa.unsqueeze(1)).squeeze(1).cpu().numpy()

    mean, lo, hi = _bootstrap_ci(values, n_bootstrap, confidence)
    return {"method": "FQE", "estimate": mean, "ci_lower": lo, "ci_upper": hi,
            "_q_network": q, "_device": dev}


# ─────────────────────────────────────────────────────────────────────────────
#  Doubly Robust
# ─────────────────────────────────────────────────────────────────────────────
def doubly_robust(
    eval_log_probs: np.ndarray,
    behavior_log_probs: np.ndarray,
    rewards: np.ndarray,
    episode_starts: np.ndarray,
    q_values: np.ndarray,          # Q^π_e(s, a_data) from FQE
    v_values: np.ndarray,          # V^π_e(s) = Q(s, π_e(s)) from FQE
    gamma: float = 0.99,
    n_bootstrap: int = 200,
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """Doubly Robust estimator combining IS and FQE."""
    ep_starts = np.where(episode_starts)[0]
    ep_ends = np.append(ep_starts[1:], len(rewards))
    ep_values = []

    for s, e in zip(ep_starts, ep_ends):
        log_ratios = eval_log_probs[s:e] - behavior_log_probs[s:e]
        rho_t = np.exp(np.cumsum(log_ratios))
        rho_prev = np.concatenate([[1.0], rho_t[:-1]])
        T = e - s
        dr = 0.0
        for t in range(T):
            discount = gamma ** t
            dr += discount * (rho_t[t] * (rewards[s + t] + gamma * v_values[s + t] - q_values[s + t])
                              + rho_prev[t] * v_values[s + t])
        ep_values.append(dr)

    ep_values = np.array(ep_values)
    mean, lo, hi = _bootstrap_ci(ep_values, n_bootstrap, confidence)
    return {"method": "DR", "estimate": mean, "ci_lower": lo, "ci_upper": hi,
            "n_episodes": len(ep_values)}


# ─────────────────────────────────────────────────────────────────────────────
#  OPE Runner — run all configured methods
# ─────────────────────────────────────────────────────────────────────────────
def run_ope(
    eval_policy_fn,
    behavior_policy,
    data: Dict[str, np.ndarray],
    methods: List[str] = ["IS", "WIS", "FQE", "DR"],
    gamma: float = 0.99,
    fqe_steps: int = 50000,
    n_bootstrap: int = 200,
    confidence: float = 0.95,
    device: str = "cpu",
    eval_agent=None,
) -> Dict[str, Dict]:
    """Run all requested OPE methods and return results dict.

    Args:
        eval_agent: Optional agent object with .q or .net attribute for
                    computing actual action distributions (softmax over Q-values).
    """
    obs = data["observations"]
    acts = data["actions"]
    rews = data["rewards"]
    next_obs = data["next_observations"]
    dones = data["dones"]

    # compute episode starts
    episode_starts = np.zeros(len(obs), dtype=bool)
    episode_starts[0] = True
    for i in range(1, len(dones)):
        if dones[i - 1]:
            episode_starts[i] = True

    n_episodes = int(episode_starts.sum())
    logger.info("OPE data: %d transitions, %d episodes", len(obs), n_episodes)

    # compute log probs
    behavior_policy.net.eval()
    beh_device = next(behavior_policy.net.parameters()).device
    with torch.no_grad():
        obs_t = torch.as_tensor(obs, dtype=torch.float32).to(beh_device)
        acts_t = torch.as_tensor(acts, dtype=torch.long).to(beh_device)
        beh_lp = behavior_policy.net.get_log_probs(obs_t, acts_t).cpu().numpy()

    # eval policy log probs — use actual policy distribution if available
    act_dim = int(acts.max()) + 1
    eval_lp = np.zeros_like(beh_lp)

    # Try to get actual Q-values / logits from the agent
    q_net = None
    if eval_agent is not None:
        if hasattr(eval_agent, 'q'):
            q_net = eval_agent.q
        elif hasattr(eval_agent, 'net'):
            q_net = eval_agent.net

    if q_net is not None:
        # Use actual softmax distribution over Q-values
        q_net.eval()
        q_device = next(q_net.parameters()).device
        with torch.no_grad():
            obs_batch = torch.as_tensor(obs, dtype=torch.float32).to(q_device)
            # Process in chunks to avoid OOM
            chunk_size = 4096
            for start in range(0, len(obs), chunk_size):
                end = min(start + chunk_size, len(obs))
                q_vals = q_net(obs_batch[start:end])
                log_probs = torch.log_softmax(q_vals, dim=-1)
                acts_chunk = torch.as_tensor(acts[start:end], dtype=torch.long).to(q_device)
                eval_lp[start:end] = log_probs.gather(
                    1, acts_chunk.unsqueeze(1)
                ).squeeze(1).cpu().numpy()
    else:
        # Fallback: approximate via predicted actions with smoothed distribution
        for i in range(len(obs)):
            pred = eval_policy_fn(obs[i])
            if pred == acts[i]:
                eval_lp[i] = np.log(0.9)
            else:
                eval_lp[i] = np.log(0.1 / max(1, act_dim - 1))

    results = {}

    # Run each method independently so partial results survive failures
    if "IS" in methods:
        try:
            results["IS"] = importance_sampling(eval_lp, beh_lp, rews, episode_starts,
                                                 gamma, False, n_bootstrap, confidence)
        except Exception as e:
            logger.warning("IS failed: %s", e)

    if "WIS" in methods:
        try:
            results["WIS"] = importance_sampling(eval_lp, beh_lp, rews, episode_starts,
                                                  gamma, True, n_bootstrap, confidence)
        except Exception as e:
            logger.warning("WIS failed: %s", e)

    # Always include PDIS as it's more stable than trajectory-level IS
    if "PDIS" in methods or "IS" in methods:
        try:
            results["PDIS"] = per_decision_is(eval_lp, beh_lp, rews, episode_starts,
                                               gamma, n_bootstrap, confidence)
        except Exception as e:
            logger.warning("PDIS failed: %s", e)

    fqe_result = None
    if "FQE" in methods or "DR" in methods:
        try:
            fqe_result = fitted_q_evaluation(
                obs, acts, rews, next_obs, dones, eval_policy_fn,
                gamma, fqe_steps, device=device,
                n_bootstrap=n_bootstrap, confidence=confidence)
            results["FQE"] = fqe_result
        except Exception as e:
            logger.warning("FQE failed: %s", e)

    if "DR" in methods and fqe_result is not None:
        try:
            # compute per-state Q(s,a) and V(s) from FQE network
            fqe_net = fqe_result.get("_q_network")
            fqe_dev = fqe_result.get("_device", torch.device(device))
            if fqe_net is not None:
                fqe_net.eval()
                with torch.no_grad():
                    all_obs_t = torch.as_tensor(obs, dtype=torch.float32).to(fqe_dev)
                    all_acts_t = torch.as_tensor(acts, dtype=torch.long).to(fqe_dev)
                    all_q = fqe_net(all_obs_t)
                    q_vals = all_q.gather(1, all_acts_t.unsqueeze(1)).squeeze(1).cpu().numpy()
                    # V(s) = Q(s, pi_e(s))
                    pi_acts = np.array([eval_policy_fn(obs[i]) for i in range(len(obs))])
                    pi_t = torch.as_tensor(pi_acts, dtype=torch.long).to(fqe_dev)
                    v_vals = all_q.gather(1, pi_t.unsqueeze(1)).squeeze(1).cpu().numpy()
            else:
                q_vals = np.full(len(obs), fqe_result["estimate"])
                v_vals = np.full(len(obs), fqe_result["estimate"])
            results["DR"] = doubly_robust(eval_lp, beh_lp, rews, episode_starts,
                                           q_vals, v_vals, gamma, n_bootstrap, confidence)
        except Exception as e:
            logger.warning("DR failed: %s", e)

    # clean internal keys before returning
    for v in results.values():
        v.pop("_q_network", None)
        v.pop("_device", None)

    logger.info("OPE results: %s",
                {k: f"{v['estimate']:.4f} [{v['ci_lower']:.4f}, {v['ci_upper']:.4f}]"
                 for k, v in results.items()})
    return results
