"""
Offline RL agents — Conservative Q-Learning (CQL) and Batch-Constrained Q (BCQ).

These algorithms are designed to safely learn from a fixed dataset without
online interaction, avoiding overestimation of out-of-distribution actions.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  CQL — Conservative Q-Learning
# ─────────────────────────────────────────────────────────────────────────────
class CQLAgent:
    """
    CQL (discrete).

    Adds a conservative penalty that minimises Q-values for OOD actions
    while maximising Q for dataset actions:
        loss += α * (logsumexp(Q(s,·)) − Q(s,a_data))
    """

    def __init__(self, obs_dim: int, act_dim: int,
                 lr: float = 3e-4, gamma: float = 0.99, tau: float = 0.005,
                 alpha: float = 1.0, n_action_samples: int = 10,
                 hidden: list = [256, 256], device: str = "cpu"):
        self.device = torch.device(device)
        self.gamma = gamma
        self.tau = tau
        self.alpha = alpha
        self.n_action_samples = n_action_samples
        self.act_dim = act_dim

        self.q = self._build_q(obs_dim, act_dim, hidden).to(self.device)
        self.q_target = self._build_q(obs_dim, act_dim, hidden).to(self.device)
        self.q_target.load_state_dict(self.q.state_dict())
        self.optimizer = optim.Adam(self.q.parameters(), lr=lr)

    @staticmethod
    def _build_q(obs_dim, act_dim, hidden):
        layers = []
        d = obs_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            d = h
        layers.append(nn.Linear(d, act_dim))
        return nn.Sequential(*layers)

    def predict(self, obs: np.ndarray) -> int:
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            return int(self.q(t).argmax(dim=-1).item())

    def train_step(self, batch: Dict[str, np.ndarray]) -> float:
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.long).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        # standard Bellman
        q_vals = self.q(obs)
        q_a = q_vals.gather(1, acts.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            q_next = self.q_target(next_obs).max(dim=-1).values
            targets = rews + self.gamma * q_next * (1 - dones)
        td_loss = nn.functional.mse_loss(q_a, targets)

        # CQL conservative penalty
        logsumexp = torch.logsumexp(q_vals, dim=-1).mean()
        data_q = q_a.mean()
        cql_loss = self.alpha * (logsumexp - data_q)

        loss = td_loss + cql_loss
        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.q.parameters(), 1.0)
        self.optimizer.step()

        # soft update
        for p, tp in zip(self.q.parameters(), self.q_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)

        return loss.item()

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.q.state_dict(), path)

    def load(self, path: str):
        self.q.load_state_dict(torch.load(path, map_location=self.device, weights_only=True))
        self.q_target.load_state_dict(self.q.state_dict())


# ─────────────────────────────────────────────────────────────────────────────
#  BCQ — Batch-Constrained Q-Learning (discrete)
# ─────────────────────────────────────────────────────────────────────────────
class BCQAgent:
    """
    Discrete BCQ.

    Learns a generative model G(s) over the action distribution in the
    data, and constrains Q to only consider actions with G(s,a) > threshold.
    """

    def __init__(self, obs_dim: int, act_dim: int,
                 lr: float = 3e-4, gamma: float = 0.99, tau: float = 0.005,
                 threshold: float = 0.3, hidden: list = [256, 256],
                 device: str = "cpu"):
        self.device = torch.device(device)
        self.gamma = gamma
        self.tau = tau
        self.threshold = threshold
        self.act_dim = act_dim

        self.q = self._build_net(obs_dim, act_dim, hidden).to(self.device)
        self.q_target = self._build_net(obs_dim, act_dim, hidden).to(self.device)
        self.q_target.load_state_dict(self.q.state_dict())

        # generative model (action probabilities given state)
        self.gen = self._build_net(obs_dim, act_dim, hidden).to(self.device)

        self.q_opt = optim.Adam(self.q.parameters(), lr=lr)
        self.gen_opt = optim.Adam(self.gen.parameters(), lr=lr)

    @staticmethod
    def _build_net(obs_dim, act_dim, hidden):
        layers = []
        d = obs_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            d = h
        layers.append(nn.Linear(d, act_dim))
        return nn.Sequential(*layers)

    def predict(self, obs: np.ndarray) -> int:
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            probs = torch.softmax(self.gen(t), dim=-1)
            q_vals = self.q(t)
            # mask out low-probability actions
            mask = (probs / probs.max(dim=-1, keepdim=True).values) >= self.threshold
            q_masked = q_vals.clone()
            q_masked[~mask] = -1e8
            return int(q_masked.argmax(dim=-1).item())

    def train_step(self, batch: Dict[str, np.ndarray]) -> float:
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.long).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        # train generative model
        gen_logits = self.gen(obs)
        gen_loss = nn.functional.cross_entropy(gen_logits, acts)
        self.gen_opt.zero_grad()
        gen_loss.backward()
        self.gen_opt.step()

        # BCQ Q-learning
        q_a = self.q(obs).gather(1, acts.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            next_probs = torch.softmax(self.gen(next_obs), dim=-1)
            next_q = self.q_target(next_obs)
            mask = (next_probs / next_probs.max(dim=-1, keepdim=True).values) >= self.threshold
            next_q[~mask] = -1e8
            q_next = next_q.max(dim=-1).values
            targets = rews + self.gamma * q_next * (1 - dones)

        q_loss = nn.functional.mse_loss(q_a, targets)
        self.q_opt.zero_grad()
        q_loss.backward()
        nn.utils.clip_grad_norm_(self.q.parameters(), 1.0)
        self.q_opt.step()

        for p, tp in zip(self.q.parameters(), self.q_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)

        return (q_loss.item() + gen_loss.item()) / 2

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"q": self.q.state_dict(), "gen": self.gen.state_dict()}, path)

    def load(self, path: str):
        ckpt = torch.load(path, map_location=self.device, weights_only=True)
        self.q.load_state_dict(ckpt["q"])
        self.gen.load_state_dict(ckpt["gen"])
        self.q_target.load_state_dict(self.q.state_dict())


# ─────────────────────────────────────────────────────────────────────────────
#  BRAC — Behavior Regularized Actor-Critic (discrete, value-penalty variant)
# ─────────────────────────────────────────────────────────────────────────────
class BRACAgent:
    """
    Simplified BRAC (value penalty).
    Adds KL(π || πβ) penalty to Q-learning, using a pretrained BC model as πβ.
    """

    def __init__(self, obs_dim: int, act_dim: int,
                 lr: float = 3e-4, gamma: float = 0.99, tau: float = 0.005,
                 kl_lambda: float = 1.0, hidden: list = [256, 256],
                 device: str = "cpu", behavior_policy=None):
        self.device = torch.device(device)
        self.gamma = gamma
        self.tau = tau
        self.kl_lambda = kl_lambda
        self.act_dim = act_dim
        self.behavior_policy = behavior_policy

        self.q = CQLAgent._build_q(obs_dim, act_dim, hidden).to(self.device)
        self.q_target = CQLAgent._build_q(obs_dim, act_dim, hidden).to(self.device)
        self.q_target.load_state_dict(self.q.state_dict())
        self.optimizer = optim.Adam(self.q.parameters(), lr=lr)

    def predict(self, obs):
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            return int(self.q(t).argmax(dim=-1).item())

    def train_step(self, batch):
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.long).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        q_a = self.q(obs).gather(1, acts.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            q_next = self.q_target(next_obs).max(dim=-1).values
            targets = rews + self.gamma * q_next * (1 - dones)

        td_loss = nn.functional.mse_loss(q_a, targets)

        # KL penalty
        kl_loss = torch.tensor(0.0, device=self.device)
        if self.behavior_policy is not None:
            log_pi = torch.log_softmax(self.q(obs), dim=-1)
            with torch.no_grad():
                beta_logits = self.behavior_policy.net(obs)
                log_beta = torch.log_softmax(beta_logits, dim=-1)
            kl_loss = (log_pi.exp() * (log_pi - log_beta)).sum(dim=-1).mean()

        loss = td_loss + self.kl_lambda * kl_loss
        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.q.parameters(), 1.0)
        self.optimizer.step()

        for p, tp in zip(self.q.parameters(), self.q_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)

        return loss.item()

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.q.state_dict(), path)

    def load(self, path):
        self.q.load_state_dict(torch.load(path, map_location=self.device, weights_only=True))
        self.q_target.load_state_dict(self.q.state_dict())
