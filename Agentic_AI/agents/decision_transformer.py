"""
Decision Transformer — sequence-model offline RL.

Architecture: GPT-style causal transformer that maps
  (returns-to-go, states, actions)  →  next action

Training: supervised on trajectories from the dataset.
Inference: autoregressively generate actions conditioned on
  desired return-to-go, then apply safety shield.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
#  Positional encoding
# ─────────────────────────────────────────────────────────────────────────────
class SinusoidalPE(nn.Module):
    def __init__(self, d_model: int, max_len: int = 2048):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(0, max_len).unsqueeze(1).float()
        div = torch.exp(torch.arange(0, d_model, 2).float() * -(math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))

    def forward(self, x):
        return x + self.pe[:, :x.size(1)]


# ─────────────────────────────────────────────────────────────────────────────
#  Decision Transformer
# ─────────────────────────────────────────────────────────────────────────────
class DecisionTransformer(nn.Module):
    """
    GPT-style Decision Transformer.

    Input sequence (interleaved): [R̂_1, s_1, a_1, R̂_2, s_2, a_2, ...]
    Predicts: a_t at each position.
    """

    def __init__(
        self,
        state_dim: int,
        act_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 6,
        context_length: int = 24,
        dropout: float = 0.1,
        continuous: bool = False,
    ):
        super().__init__()
        self.state_dim = state_dim
        self.act_dim = act_dim
        self.d_model = d_model
        self.context_length = context_length
        self.continuous = continuous

        # embeddings
        self.state_embed = nn.Linear(state_dim, d_model)
        self.rtg_embed = nn.Linear(1, d_model)
        if continuous:
            self.action_embed = nn.Linear(act_dim, d_model)
        else:
            self.action_embed = nn.Embedding(act_dim, d_model)
        self.pos_embed = nn.Embedding(3 * context_length, d_model)  # 3 tokens per step
        self.timestep_embed = nn.Embedding(context_length + 1, d_model)

        self.ln = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)

        # transformer blocks
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model, nhead=n_heads, dim_feedforward=4 * d_model,
            dropout=dropout, batch_first=True, activation="gelu",
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=n_layers)

        # prediction heads
        if continuous:
            self.action_head = nn.Linear(d_model, act_dim)
        else:
            self.action_head = nn.Linear(d_model, act_dim)

    def forward(self, states, actions, rtg, timesteps, masks=None):
        """
        Parameters
        ----------
        states   : (B, L, state_dim)
        actions  : (B, L) discrete or (B, L, act_dim) continuous
        rtg      : (B, L)
        timesteps: (B, L)
        masks    : (B, L)

        Returns
        -------
        action_preds : (B, L, act_dim) — logits or mean actions
        """
        B, L = states.shape[:2]
        device = states.device

        # embed each modality
        s_emb = self.state_embed(states)                 # (B, L, d)
        r_emb = self.rtg_embed(rtg.unsqueeze(-1))        # (B, L, d)
        if self.continuous:
            a_emb = self.action_embed(actions.float())   # (B, L, d)
        else:
            a_emb = self.action_embed(actions.long())    # (B, L, d)

        t_emb = self.timestep_embed(torch.clamp(timesteps, 0, self.context_length).long())

        s_emb = s_emb + t_emb
        a_emb = a_emb + t_emb
        r_emb = r_emb + t_emb

        # interleave: [R̂_1, s_1, a_1, R̂_2, s_2, a_2, ...]
        token_seq = torch.zeros(B, 3 * L, self.d_model, device=device)
        token_seq[:, 0::3] = r_emb
        token_seq[:, 1::3] = s_emb
        token_seq[:, 2::3] = a_emb

        # positional embedding
        pos_ids = torch.arange(3 * L, device=device).unsqueeze(0).expand(B, -1)
        pos_ids = torch.clamp(pos_ids, 0, 3 * self.context_length - 1)
        token_seq = token_seq + self.pos_embed(pos_ids)
        token_seq = self.ln(self.dropout(token_seq))

        # causal mask
        causal_mask = nn.Transformer.generate_square_subsequent_mask(3 * L, device=device)

        # attention mask for padding
        if masks is not None:
            # expand mask to 3×L
            attn_mask_1d = masks.repeat_interleave(3, dim=1)  # (B, 3L)
            key_padding_mask = (attn_mask_1d == 0)
        else:
            key_padding_mask = None

        out = self.transformer(token_seq, mask=causal_mask,
                               src_key_padding_mask=key_padding_mask)

        # extract action predictions from state positions (index 1::3)
        state_out = out[:, 1::3]  # (B, L, d)
        action_preds = self.action_head(state_out)

        return action_preds


# ─────────────────────────────────────────────────────────────────────────────
#  DT Agent wrapper
# ─────────────────────────────────────────────────────────────────────────────
class DTAgent:
    """Decision Transformer trainer & policy."""

    def __init__(
        self,
        state_dim: int,
        act_dim: int,
        d_model: int = 256,
        n_heads: int = 8,
        n_layers: int = 6,
        context_length: int = 24,
        dropout: float = 0.1,
        lr: float = 1e-4,
        weight_decay: float = 1e-4,
        warmup_steps: int = 1000,
        continuous: bool = False,
        device: str = "cpu",
    ):
        self.device = torch.device(device)
        self.context_length = context_length
        self.continuous = continuous
        self.act_dim = act_dim
        self.warmup_steps = warmup_steps
        self._step = 0

        self.model = DecisionTransformer(
            state_dim, act_dim, d_model, n_heads, n_layers,
            context_length, dropout, continuous
        ).to(self.device)

        self.optimizer = optim.AdamW(self.model.parameters(), lr=lr,
                                      weight_decay=weight_decay)
        self.scheduler = optim.lr_scheduler.LambdaLR(
            self.optimizer,
            lr_lambda=lambda step: min(1.0, (step + 1) / max(warmup_steps, 1))
        )

        if continuous:
            self.loss_fn = nn.MSELoss()
        else:
            self.loss_fn = nn.CrossEntropyLoss()

    def train_epoch(self, traj_data: Dict[str, np.ndarray],
                    batch_size: int = 64) -> float:
        """Train for one epoch over trajectory data."""
        states = torch.as_tensor(traj_data["states"], dtype=torch.float32)
        if self.continuous:
            actions = torch.as_tensor(traj_data["actions"], dtype=torch.float32)
        else:
            actions = torch.as_tensor(traj_data["actions"], dtype=torch.long)
        rtg = torch.as_tensor(traj_data["returns_to_go"], dtype=torch.float32)
        timesteps = torch.as_tensor(traj_data["timesteps"], dtype=torch.long)
        masks = torch.as_tensor(traj_data["masks"], dtype=torch.float32)

        ds = TensorDataset(states, actions, rtg, timesteps, masks)
        dl = DataLoader(ds, batch_size=batch_size, shuffle=True)

        self.model.train()
        total_loss, n = 0.0, 0
        for s, a, r, t, m in dl:
            s, a, r, t, m = [x.to(self.device) for x in (s, a, r, t, m)]
            preds = self.model(s, a, r, t, m)

            if self.continuous:
                loss = self.loss_fn(preds, a)
            else:
                B, L, D = preds.shape
                loss = self.loss_fn(preds.reshape(B * L, D), a.reshape(B * L))

            self.optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.optimizer.step()
            self.scheduler.step()
            self._step += 1
            total_loss += loss.item() * len(s)
            n += len(s)

        return total_loss / max(n, 1)

    def predict(self, states_history: np.ndarray,
                actions_history: np.ndarray,
                rtg_history: np.ndarray,
                timesteps: np.ndarray) -> int | np.ndarray:
        """
        Autoregressive inference: given history, predict the next action.

        Parameters
        ----------
        states_history  : (L, state_dim)
        actions_history : (L,) or (L, act_dim)
        rtg_history     : (L,)
        timesteps       : (L,)
        """
        self.model.eval()
        with torch.no_grad():
            s = torch.as_tensor(states_history, dtype=torch.float32).unsqueeze(0).to(self.device)
            if self.continuous:
                a = torch.as_tensor(actions_history, dtype=torch.float32).unsqueeze(0).to(self.device)
            else:
                a = torch.as_tensor(actions_history, dtype=torch.long).unsqueeze(0).to(self.device)
            r = torch.as_tensor(rtg_history, dtype=torch.float32).unsqueeze(0).to(self.device)
            t = torch.as_tensor(timesteps, dtype=torch.long).unsqueeze(0).to(self.device)

            preds = self.model(s, a, r, t)  # (1, L, act_dim)
            last_pred = preds[0, -1]        # (act_dim,)

            if self.continuous:
                return last_pred.cpu().numpy()
            return int(last_pred.argmax().item())

    def save(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model": self.model.state_dict(),
            "optimizer": self.optimizer.state_dict(),
            "step": self._step,
        }, path)
        logger.info("DT model saved to %s", path)

    def load(self, path: str):
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.model.load_state_dict(ckpt["model"])
        if "optimizer" in ckpt:
            self.optimizer.load_state_dict(ckpt["optimizer"])
        self._step = ckpt.get("step", 0)
        logger.info("DT model loaded from %s", path)
