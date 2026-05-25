"""CQL Network architecture for inference."""
import torch
import torch.nn as nn


class CQLNetwork(nn.Module):
    """Conservative Q-Learning network."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: list = None):
        super().__init__()
        if hidden is None:
            hidden = [256, 256]

        layers = []
        d = obs_dim
        for h in hidden:
            layers.extend([nn.Linear(d, h), nn.ReLU()])
            d = h
        layers.append(nn.Linear(d, act_dim))

        self.q = nn.Sequential(*layers)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.q(obs)
