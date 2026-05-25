"""DT Network architecture for inference."""
import torch
import torch.nn as nn


class DTNetwork(nn.Module):
    """
    Simplified Decision Transformer for single-step inference.

    For full sequence inference, use the full DecisionTransformer.
    This is a simplified version for edge deployment.
    """

    def __init__(self, state_dim: int, act_dim: int,
                 d_model: int = 256, n_layers: int = 3):
        super().__init__()
        self.state_dim = state_dim
        self.act_dim = act_dim

        # Simple MLP approximation for single-step
        self.encoder = nn.Sequential(
            nn.Linear(state_dim, d_model),
            nn.ReLU(),
            nn.Linear(d_model, d_model),
            nn.ReLU(),
        )

        layers = []
        for _ in range(n_layers - 1):
            layers.extend([
                nn.Linear(d_model, d_model),
                nn.LayerNorm(d_model),
                nn.ReLU(),
            ])

        self.trunk = nn.Sequential(*layers)
        self.head = nn.Linear(d_model, act_dim)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        x = self.encoder(obs)
        x = self.trunk(x)
        return self.head(x)
