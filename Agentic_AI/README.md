# Grid-Guardian — Agentic AI RL Training Pipeline

Production-grade, safety-first reinforcement learning pipeline for microgrid energy management. Trains offline RL agents on the Grid-Guardian dataset, enforces hard safety constraints via a configurable shield, and packages policies for Raspberry Pi 5 edge deployment.

---

## Quick Start

```bash
# 1. Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/macOS

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run tests
python -m pytest tests/ -v

# 4. Train CQL agent (offline)
python train_rl_agents.py --config configs/train_rl.yaml --algo CQL --mode offline --seed 42

# 5. Train Decision Transformer
python train_rl_agents.py --config configs/train_rl.yaml --algo DT --mode offline --device cpu

# 6. Pack best model for edge
python train_rl_agents.py --pack-policy --model-path models/CQL/run_42/checkpoint_best.pt --algo CQL
```

---

## Supported Algorithms

| Algorithm | Type | Key Feature |
|-----------|------|-------------|
| **BC** | Supervised | Behavior cloning baseline; also used for OPE behavior policy |
| **DQN** | Value-based | Offline with optional KL penalty to behavior |
| **PPO** | Policy gradient | Discrete, clipped surrogate with TD targets |
| **SAC** | Actor-critic | Continuous, entropy-regularized |
| **DDPG** | Actor-critic | Continuous, deterministic |
| **CQL** | Offline RL | Conservative Q-Learning with logsumexp penalty |
| **BCQ** | Offline RL | Batch-Constrained Q-learning with generative model |
| **BRAC** | Offline RL | Behavior Regularized Actor-Critic |
| **DT** | Sequence model | Decision Transformer (GPT-style, returns-to-go conditioning) |

---

## Project Structure

```
├── train_rl_agents.py          # Main CLI entry point
├── configs/
│   └── train_rl.yaml           # Full YAML config (env, algo, safety, OPE, packaging)
├── env/
│   ├── microgrid_env.py        # Gymnasium environment (replay + sim modes)
│   └── safety_shield.py        # Safety shield (clip / fallback / reject)
├── agents/
│   ├── bc_agent.py             # Behavior Cloning
│   ├── classical_rl.py         # DQN, PPO, SAC, DDPG
│   ├── offline_rl.py           # CQL, BCQ, BRAC
│   └── decision_transformer.py # Decision Transformer
├── data_utils/
│   └── replay_buffer.py        # Dataset converter, replay buffer, trajectory builder
├── evaluation/
│   ├── evaluator.py            # Eval, stress tests, plotting
│   └── ope.py                  # IS/WIS/FQE/DR offline policy evaluation
├── model_packaging/
│   └── exporter.py             # TorchScript, ONNX, quantization, model cards
├── edge/
│   ├── edge_inference.py       # Minimal Pi 5 inference script
│   └── README.edge.md          # Edge deployment guide
├── tests/
│   └── test_rl_pipeline.py     # 29 pytest unit + integration tests
├── data/
│   └── partitioned/            # train.csv, val.csv, test.csv
├── Dockerfile                  # CPU image
├── Dockerfile.gpu              # GPU image (CUDA 12.1)
├── Dockerfile.edge             # Minimal ARM64 edge image
├── environment.yml             # Conda environment
└── requirements.txt            # pip dependencies
```

---

## CLI Reference

```
python train_rl_agents.py [OPTIONS]

Options:
  --config PATH             YAML config file (default: configs/train_rl.yaml)
  --mode {offline,online,hybrid}
  --algo {SAC,PPO,DDPG,DQN,CQL,BCQ,BRAC,DT,BC}
  --seed INT                Random seed (default: 42)
  --device {cpu,cuda:0,...}  Compute device
  --total-timesteps FLOAT   Total training steps (e.g., 1e6)
  --eval-every INT          Evaluate every N steps
  --eval-episodes INT       Episodes per evaluation
  --log-dir PATH            Logging directory
  --num-envs INT            Parallel environments
  --prefill-buffer          Pre-fill buffer with dataset
  --reproducible            Set deterministic seeds & cudnn
  --pack-policy             Export model to TorchScript/ONNX
  --pack-quantize           Apply post-training quantization
  --model-path PATH         Checkpoint path for packing
  --out PATH                Output dir for packing
  --ope-methods CSV         Comma-separated OPE methods (IS,WIS,FQE,DR)
  --dry-run                 Print config and exit
```

---

## Safety Shield

The safety shield is a modular action-constraint wrapper inserted between policy and environment:

| Mode | Behavior |
|------|----------|
| `clip` | Clip action to safe thresholds (SoC bounds, power limits) |
| `fallback` | Replace with rule-based safe fallback (charge when price low & SoC low; discharge when high) |
| `reject` | Reject and return last known safe action; log incident |

Configurable via YAML:
```yaml
safety:
  shield_enabled: true
  shield_mode: clip          # clip | fallback | reject
  soc_min_frac: 0.10
  soc_max_frac: 0.95
  max_charge_kw: 3.0
  max_discharge_kw: 3.0
```

---

## Offline Policy Evaluation (OPE)

Four OPE estimators with bootstrap confidence intervals:

- **IS** — Importance Sampling
- **WIS** — Weighted Importance Sampling  
- **FQE** — Fitted Q-Evaluation (neural network)
- **DR** — Doubly Robust (combines IS + FQE)

```bash
python train_rl_agents.py --config configs/train_rl.yaml --algo CQL --ope-methods IS,WIS,FQE,DR
```

OPE results saved to `ope/<algo>/ope_estimates.json`.

---

## Domain Randomization

Applied per-episode during training and evaluation:

```yaml
domain_randomization:
  enabled: true
  forecast_noise_std: 0.10
  irradiance_noise_std: 0.05
  inverter_eff_range: [0.85, 0.95]
  latency_ms_range: [0, 200]
  sensor_dropout_prob: 0.02
```

---

## Outputs

After a training run:

```
models/<algo>/run_<seed>/     # Checkpoints (.pt)
logs/<algo>/<run_id>/         # TensorBoard logs
eval/<algo>/eval_summary.json # Final + stress test metrics
eval/<algo>/eval_plots/       # Learning curves (PNG)
ope/<algo>/ope_estimates.json # IS/WIS/FQE/DR estimates
experiments/<run_id>/         # run_manifest.json + config_used.yaml
edge/policy_pack/             # TorchScript, ONNX, norm, model card
```

---

## Edge Deployment (Raspberry Pi 5)

```bash
# Build ARM64 Docker image
docker buildx build --platform linux/arm64 -f Dockerfile.edge -t grid-guardian-edge .

# Run inference
python edge/edge_inference.py \
  --model edge/policy_pack/cql_policy.torchscript \
  --norm edge/policy_pack/norm_params.npz \
  --obs "2.5,4,0.5,0.3,0.2,0.0,5.0,100,200,20,150,230,1.0" \
  --soc 2.5 --safety
```

See [edge/README.edge.md](edge/README.edge.md) for full deployment guide.

---

## Docker

```bash
# CPU training
docker build -t grid-guardian -f Dockerfile .
docker run --rm -v $(pwd)/data:/app/data grid-guardian \
  python train_rl_agents.py --algo CQL --total-timesteps 50000

# GPU training
docker build -t grid-guardian-gpu -f Dockerfile.gpu .
docker run --gpus all --rm grid-guardian-gpu \
  python train_rl_agents.py --algo CQL --device cuda:0
```

---

## Testing

```bash
# Run full test suite (29 tests)
python -m pytest tests/ -v

# Test specific component
python -m pytest tests/test_rl_pipeline.py::TestSafetyShield -v
python -m pytest tests/test_rl_pipeline.py::TestIntegrationSmoke -v
```

---

## Recommended Hyperparameters

| Algorithm | Key Settings | Notes |
|-----------|-------------|-------|
| CQL | `alpha=1.0`, `lr=3e-4`, `batch=256` | Start conservative; increase alpha if policy is too cautious |
| BCQ | `threshold=0.3`, `lr=3e-4` | Lower threshold = more constrained to data |
| DT | `d_model=256`, `n_layers=6`, `n_heads=8`, `context=24` | Use warmup scheduler; small batch (32-64) |
| DQN | `lr=3e-4`, `gamma=0.99`, `tau=0.005` | Add KL penalty for offline safety |
| PPO | `clip=0.2`, `ent_coef=0.01`, `n_epochs=10` | Offline PPO uses TD return estimates |

---

## Dataset

The pipeline expects the partitioned Grid-Guardian dataset:
- **train.csv** — 39,741 rows (3 households, 5-min timesteps, Jan-Mar 2025)
- **val.csv** — 12,096 rows
- **test.csv** — 25,923 rows

Observations: 18-dim vector (13 sensor features + 4 time sin/cos + 1 neighbor balance)
Actions: 7 discrete (charge_small, charge_large, idle, discharge_small, discharge_large, offer_sell, offer_hold)

---

## License

Internal project — Grid-Guardian team.
