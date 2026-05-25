"""BC Network architecture for inference."""
import torch
import torch.nn as nn


class BCNetwork(nn.Module):
    """Behavior Cloning MLP network."""

    def __init__(self, obs_dim: int, act_dim: int, hidden: list = None):
        super().__init__()
        if hidden is None:
            hidden = [256, 256]

        layers = []
        in_dim = obs_dim
        for h in hidden:
            layers.extend([nn.Linear(in_dim, h), nn.ReLU()])
            in_dim = h

        self.trunk = nn.Sequential(*layers)
        self.head = nn.Linear(in_dim, act_dim)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.head(self.trunk(obs))
