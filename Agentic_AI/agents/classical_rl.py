"""
Classical RL agents — DQN, PPO, SAC, DDPG.

Wraps stable-baselines3 for clean integration with the Grid-Guardian pipeline.
Also provides a thin custom DQN for offline training with behaviour penalty.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Custom lightweight DQN (supports offline + KL penalty)
# ─────────────────────────────────────────────────────────────────────────────
class QNetwork(nn.Module):
    def __init__(self, obs_dim: int, act_dim: int, hidden: list = [256, 256]):
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


class DQNAgent:
    """Minimal DQN agent supporting offline training with optional KL penalty."""

    def __init__(self, obs_dim: int, act_dim: int,
                 lr: float = 3e-4, gamma: float = 0.99, tau: float = 0.005,
                 hidden: list = [256, 256], device: str = "cpu",
                 behavior_policy=None, kl_lambda: float = 0.0):
        self.device = torch.device(device)
        self.gamma = gamma
        self.tau = tau
        self.act_dim = act_dim
        self.kl_lambda = kl_lambda
        self.behavior_policy = behavior_policy

        self.q = QNetwork(obs_dim, act_dim, hidden).to(self.device)
        self.q_target = QNetwork(obs_dim, act_dim, hidden).to(self.device)
        self.q_target.load_state_dict(self.q.state_dict())
        self.optimizer = optim.Adam(self.q.parameters(), lr=lr)
        self._step = 0

    def predict(self, obs: np.ndarray, epsilon: float = 0.0) -> int:
        if np.random.random() < epsilon:
            return np.random.randint(self.act_dim)
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            return int(self.q(t).argmax(dim=-1).item())

    def train_step(self, batch: Dict[str, np.ndarray]) -> float:
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.long).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        q_vals = self.q(obs).gather(1, acts.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            q_next = self.q_target(next_obs).max(dim=-1).values
            targets = rews + self.gamma * q_next * (1 - dones)

        loss = nn.functional.mse_loss(q_vals, targets)

        # KL penalty (offline safety)
        if self.kl_lambda > 0 and self.behavior_policy is not None:
            log_pi = torch.log_softmax(self.q(obs), dim=-1)
            with torch.no_grad():
                beh_logits = self.behavior_policy.net(obs)
                log_beta = torch.log_softmax(beh_logits, dim=-1)
            kl = (log_pi.exp() * (log_pi - log_beta)).sum(dim=-1).mean()
            loss = loss + self.kl_lambda * kl

        self.optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(self.q.parameters(), 1.0)
        self.optimizer.step()

        # soft target update
        self._step += 1
        if self._step % 2 == 0:
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
#  SAC Agent (custom lightweight)
# ─────────────────────────────────────────────────────────────────────────────
class GaussianPolicy(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden=[256, 256]):
        super().__init__()
        layers = []
        d = obs_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            d = h
        self.trunk = nn.Sequential(*layers)
        self.mu = nn.Linear(d, act_dim)
        self.log_std = nn.Linear(d, act_dim)

    def forward(self, obs):
        h = self.trunk(obs)
        mu = self.mu(h)
        log_std = self.log_std(h).clamp(-20, 2)
        return mu, log_std

    def sample(self, obs):
        mu, log_std = self.forward(obs)
        std = log_std.exp()
        dist = torch.distributions.Normal(mu, std)
        x = dist.rsample()
        action = torch.tanh(x)
        log_prob = dist.log_prob(x) - torch.log(1 - action.pow(2) + 1e-6)
        return action, log_prob.sum(-1)


class SACAgent:
    """Soft Actor-Critic for continuous actions."""

    def __init__(self, obs_dim, act_dim, lr=3e-4, gamma=0.99, tau=0.005,
                 hidden=[256, 256], device="cpu", alpha=0.2,
                 behavior_policy=None, kl_lambda=0.0):
        self.device = torch.device(device)
        self.gamma = gamma
        self.tau = tau
        self.alpha = alpha
        self.kl_lambda = kl_lambda
        self.behavior_policy = behavior_policy

        self.policy = GaussianPolicy(obs_dim, act_dim, hidden).to(self.device)
        self.q1 = QNetwork(obs_dim + act_dim, 1, hidden).to(self.device)
        self.q2 = QNetwork(obs_dim + act_dim, 1, hidden).to(self.device)
        self.q1_target = QNetwork(obs_dim + act_dim, 1, hidden).to(self.device)
        self.q2_target = QNetwork(obs_dim + act_dim, 1, hidden).to(self.device)
        self.q1_target.load_state_dict(self.q1.state_dict())
        self.q2_target.load_state_dict(self.q2.state_dict())

        self.policy_opt = optim.Adam(self.policy.parameters(), lr=lr)
        self.q1_opt = optim.Adam(self.q1.parameters(), lr=lr)
        self.q2_opt = optim.Adam(self.q2.parameters(), lr=lr)

    def predict(self, obs):
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            a, _ = self.policy.sample(t)
            return a.squeeze(0).cpu().numpy()

    def train_step(self, batch):
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.float32).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        # Q targets
        with torch.no_grad():
            na, nlp = self.policy.sample(next_obs)
            q1_n = self.q1_target(torch.cat([next_obs, na], -1)).squeeze(-1)
            q2_n = self.q2_target(torch.cat([next_obs, na], -1)).squeeze(-1)
            q_n = torch.min(q1_n, q2_n) - self.alpha * nlp
            target = rews + self.gamma * (1 - dones) * q_n

        sa = torch.cat([obs, acts], -1)
        q1_loss = nn.functional.mse_loss(self.q1(sa).squeeze(-1), target)
        q2_loss = nn.functional.mse_loss(self.q2(sa).squeeze(-1), target)

        self.q1_opt.zero_grad(); q1_loss.backward(); self.q1_opt.step()
        self.q2_opt.zero_grad(); q2_loss.backward(); self.q2_opt.step()

        # policy
        a, lp = self.policy.sample(obs)
        q1_pi = self.q1(torch.cat([obs, a], -1)).squeeze(-1)
        q2_pi = self.q2(torch.cat([obs, a], -1)).squeeze(-1)
        policy_loss = (self.alpha * lp - torch.min(q1_pi, q2_pi)).mean()

        self.policy_opt.zero_grad(); policy_loss.backward(); self.policy_opt.step()

        # soft update
        for p, tp in zip(self.q1.parameters(), self.q1_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)
        for p, tp in zip(self.q2.parameters(), self.q2_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)

        return (q1_loss.item() + q2_loss.item()) / 2

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"policy": self.policy.state_dict(),
                     "q1": self.q1.state_dict(), "q2": self.q2.state_dict()}, path)

    def load(self, path):
        ckpt = torch.load(path, map_location=self.device, weights_only=True)
        self.policy.load_state_dict(ckpt["policy"])
        self.q1.load_state_dict(ckpt["q1"])
        self.q2.load_state_dict(ckpt["q2"])


# ─────────────────────────────────────────────────────────────────────────────
#  PPO Agent (discrete, custom lightweight)
# ─────────────────────────────────────────────────────────────────────────────
class PPONetwork(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden=[256, 256]):
        super().__init__()
        layers = []
        d = obs_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            d = h
        self.shared = nn.Sequential(*layers)
        self.pi = nn.Linear(d, act_dim)
        self.v = nn.Linear(d, 1)

    def forward(self, obs):
        h = self.shared(obs)
        return self.pi(h), self.v(h)


class PPOAgent:
    """Minimal PPO agent for discrete actions."""

    def __init__(self, obs_dim, act_dim, lr=3e-4, gamma=0.99,
                 gae_lambda=0.95, clip_range=0.2, ent_coef=0.01,
                 hidden=[256, 256], device="cpu", n_epochs=10):
        self.device = torch.device(device)
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_range = clip_range
        self.ent_coef = ent_coef
        self.n_epochs = n_epochs
        self.act_dim = act_dim

        self.net = PPONetwork(obs_dim, act_dim, hidden).to(self.device)
        self.optimizer = optim.Adam(self.net.parameters(), lr=lr)

    def predict(self, obs):
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            logits, _ = self.net(t)
            return torch.distributions.Categorical(logits=logits).sample().item()

    def train_step(self, batch):
        """Single offline PPO update (from batch of transitions)."""
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.long).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        # compute returns using 1-step TD targets as proxy for offline setting
        with torch.no_grad():
            old_logits, old_v = self.net(obs)
            _, next_v = self.net(next_obs)
            old_dist = torch.distributions.Categorical(logits=old_logits)
            old_lp = old_dist.log_prob(acts)
            # 1-step TD return as target
            returns = rews + self.gamma * next_v.squeeze(-1) * (1 - dones)
            advantages = returns - old_v.squeeze(-1)
            # normalize advantages
            advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        total_loss = 0.0
        for _ in range(self.n_epochs):
            logits, values = self.net(obs)
            dist = torch.distributions.Categorical(logits=logits)
            lp = dist.log_prob(acts)
            ratio = (lp - old_lp).exp()
            clipped = torch.clamp(ratio, 1 - self.clip_range, 1 + self.clip_range)
            policy_loss = -torch.min(ratio * advantages, clipped * advantages).mean()
            value_loss = nn.functional.mse_loss(values.squeeze(-1), returns.detach())
            entropy = dist.entropy().mean()
            loss = policy_loss + 0.5 * value_loss - self.ent_coef * entropy

            self.optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(self.net.parameters(), 0.5)
            self.optimizer.step()
            total_loss += loss.item()

        return total_loss / self.n_epochs

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.net.state_dict(), path)

    def load(self, path):
        self.net.load_state_dict(torch.load(path, map_location=self.device, weights_only=True))


# ─────────────────────────────────────────────────────────────────────────────
#  DDPG Agent (continuous)
# ─────────────────────────────────────────────────────────────────────────────
class DDPGActor(nn.Module):
    def __init__(self, obs_dim, act_dim, hidden=[256, 256]):
        super().__init__()
        layers = []
        d = obs_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.ReLU()]
            d = h
        layers.append(nn.Linear(d, act_dim))
        layers.append(nn.Tanh())
        self.net = nn.Sequential(*layers)

    def forward(self, obs):
        return self.net(obs)


class DDPGAgent:
    def __init__(self, obs_dim, act_dim, lr=1e-3, gamma=0.99, tau=0.005,
                 hidden=[256, 256], device="cpu", noise_std=0.1):
        self.device = torch.device(device)
        self.gamma = gamma
        self.tau = tau
        self.noise_std = noise_std
        self.act_dim = act_dim

        self.actor = DDPGActor(obs_dim, act_dim, hidden).to(self.device)
        self.actor_target = DDPGActor(obs_dim, act_dim, hidden).to(self.device)
        self.actor_target.load_state_dict(self.actor.state_dict())
        self.critic = QNetwork(obs_dim + act_dim, 1, hidden).to(self.device)
        self.critic_target = QNetwork(obs_dim + act_dim, 1, hidden).to(self.device)
        self.critic_target.load_state_dict(self.critic.state_dict())

        self.actor_opt = optim.Adam(self.actor.parameters(), lr=lr)
        self.critic_opt = optim.Adam(self.critic.parameters(), lr=lr)

    def predict(self, obs, explore=False):
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0).to(self.device)
            a = self.actor(t).squeeze(0).cpu().numpy()
            if explore:
                a += np.random.normal(0, self.noise_std, size=a.shape)
            return np.clip(a, -1, 1)

    def train_step(self, batch):
        obs = torch.as_tensor(batch["observations"], dtype=torch.float32).to(self.device)
        acts = torch.as_tensor(batch["actions"], dtype=torch.float32).to(self.device)
        rews = torch.as_tensor(batch["rewards"], dtype=torch.float32).to(self.device)
        next_obs = torch.as_tensor(batch["next_observations"], dtype=torch.float32).to(self.device)
        dones = torch.as_tensor(batch["dones"], dtype=torch.float32).to(self.device)

        with torch.no_grad():
            na = self.actor_target(next_obs)
            q_next = self.critic_target(torch.cat([next_obs, na], -1)).squeeze(-1)
            target = rews + self.gamma * (1 - dones) * q_next

        q_val = self.critic(torch.cat([obs, acts], -1)).squeeze(-1)
        critic_loss = nn.functional.mse_loss(q_val, target)
        self.critic_opt.zero_grad(); critic_loss.backward(); self.critic_opt.step()

        a_pred = self.actor(obs)
        actor_loss = -self.critic(torch.cat([obs, a_pred], -1)).mean()
        self.actor_opt.zero_grad(); actor_loss.backward(); self.actor_opt.step()

        for p, tp in zip(self.actor.parameters(), self.actor_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)
        for p, tp in zip(self.critic.parameters(), self.critic_target.parameters()):
            tp.data.copy_(self.tau * p.data + (1 - self.tau) * tp.data)

        return critic_loss.item()

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"actor": self.actor.state_dict(),
                     "critic": self.critic.state_dict()}, path)

    def load(self, path):
        ckpt = torch.load(path, map_location=self.device, weights_only=True)
        self.actor.load_state_dict(ckpt["actor"])
        self.critic.load_state_dict(ckpt["critic"])
