"""
Behavior Cloning (BC) agent — supervised baseline.

Trains a policy network π(a|s) by minimizing cross-entropy (discrete) or
MSE (continuous) loss against the dataset's behavior policy.
Also serves as the reference behavior policy for KL penalties and OPE.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

logger = logging.getLogger(__name__)


class BCNetwork(nn.Module):
    """Simple MLP for behaviour cloning."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: list = [256, 256],
                 continuous: bool = False):
        super().__init__()
        self.continuous = continuous
        layers = []
        in_dim = obs_dim
        for h in hidden:
            layers += [nn.Linear(in_dim, h), nn.ReLU()]
            in_dim = h
        self.trunk = nn.Sequential(*layers)
        self.head = nn.Linear(in_dim, act_dim)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        """Return logits (discrete) or mean action (continuous)."""
        return self.head(self.trunk(obs))

    def get_action(self, obs: np.ndarray) -> int | np.ndarray:
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            out = self.forward(t)
            if self.continuous:
                return out.squeeze(0).numpy()
            return int(out.argmax(dim=-1).item())

    def get_log_probs(self, obs: torch.Tensor, acts: torch.Tensor) -> torch.Tensor:
        """Return log π_β(a|s) for given (s, a) pairs."""
        logits = self.forward(obs)
        if self.continuous:
            # treat as Gaussian with fixed σ=1
            return -0.5 * ((logits - acts) ** 2).sum(dim=-1)
        log_probs = torch.log_softmax(logits, dim=-1)
        return log_probs.gather(1, acts.unsqueeze(-1).long()).squeeze(-1)


class BCAgent:
    """Behaviour Cloning trainer & policy."""

    def __init__(self, obs_dim: int, act_dim: int,
                 hidden: list = [256, 256],
                 lr: float = 3e-4,
                 continuous: bool = False,
                 device: str = "cpu"):
        self.device = torch.device(device)
        self.continuous = continuous
        self.act_dim = act_dim
        self.net = BCNetwork(obs_dim, act_dim, hidden, continuous).to(self.device)
        self.optimizer = optim.Adam(self.net.parameters(), lr=lr)
        if continuous:
            self.loss_fn = nn.MSELoss()
        else:
            self.loss_fn = nn.CrossEntropyLoss()

    def train(self, data: Dict[str, np.ndarray],
              epochs: int = 50, batch_size: int = 256,
              val_data: Optional[Dict[str, np.ndarray]] = None) -> Dict:
        obs_t = torch.as_tensor(data["observations"], dtype=torch.float32)
        if self.continuous:
            act_t = torch.as_tensor(data["actions"], dtype=torch.float32)
        else:
            act_t = torch.as_tensor(data["actions"], dtype=torch.long)
        ds = TensorDataset(obs_t, act_t)
        dl = DataLoader(ds, batch_size=batch_size, shuffle=True)

        history = {"train_loss": [], "val_loss": []}
        best_loss = float("inf")

        for epoch in range(epochs):
            self.net.train()
            total = 0.0
            n = 0
            for ob, ac in dl:
                ob, ac = ob.to(self.device), ac.to(self.device)
                pred = self.net(ob)
                loss = self.loss_fn(pred, ac)
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()
                total += loss.item() * len(ob)
                n += len(ob)
            avg = total / max(n, 1)
            history["train_loss"].append(avg)

            # validation
            if val_data is not None:
                vloss = self._eval_loss(val_data)
                history["val_loss"].append(vloss)
                if vloss < best_loss:
                    best_loss = vloss
            if (epoch + 1) % 10 == 0:
                logger.info("BC epoch %d/%d  train_loss=%.4f", epoch + 1, epochs, avg)

        return history

    def _eval_loss(self, data):
        self.net.eval()
        with torch.no_grad():
            obs = torch.as_tensor(data["observations"], dtype=torch.float32).to(self.device)
            if self.continuous:
                act = torch.as_tensor(data["actions"], dtype=torch.float32).to(self.device)
            else:
                act = torch.as_tensor(data["actions"], dtype=torch.long).to(self.device)
            pred = self.net(obs)
            return self.loss_fn(pred, act).item()

    def predict(self, obs: np.ndarray) -> int | np.ndarray:
        return self.net.get_action(obs)

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({"state_dict": self.net.state_dict(),
                     "obs_dim": self.net.trunk[0].in_features,
                     "act_dim": self.net.head.out_features,
                     "continuous": self.continuous}, path)
        logger.info("BC model saved to %s", path)

    def load(self, path: str):
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.net.load_state_dict(ckpt["state_dict"])
        logger.info("BC model loaded from %s", path)
