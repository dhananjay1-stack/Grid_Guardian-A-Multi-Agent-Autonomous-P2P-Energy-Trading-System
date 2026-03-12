# Grid-Guardian Edge Deployment — Raspberry Pi 5

## Overview

This directory contains the minimal runtime for deploying Grid-Guardian
trained policies on a Raspberry Pi 5 (ARM64).

## Files

| File | Description |
|------|-------------|
| `edge_inference.py` | Standalone inference script |
| `policy_pack/` | Exported models (TorchScript / ONNX) |
| `policy_pack/norm_params.npz` | Normalization parameters |
| `policy_pack/model_card.json` | Model metadata |

## Quick Start

### 1. Install dependencies (on Pi)

```bash
pip install numpy torch --index-url https://download.pytorch.org/whl/cpu
pip install onnxruntime
```

### 2. Run inference

```bash
python edge_inference.py \
  --model policy_pack/cql_policy.torchscript \
  --norm policy_pack/norm_params.npz \
  --obs "[2.0, 4, 1.5, 0.8, 0.7, 0.0, 5.0, 200, 180, 25, 400, 230, 1.2, 0.5, 0.87, 0.3, -0.2]" \
  --soc 2.0 --soc_cap 4.0 --safety
```

### 3. ONNX Runtime (faster on ARM64)

```bash
python edge_inference.py \
  --model policy_pack/cql_policy.onnx \
  --norm policy_pack/norm_params.npz \
  --obs "[2.0, 4, 1.5, 0.8, 0.7, 0.0, 5.0, 200, 180, 25, 400, 230, 1.2, 0.5, 0.87, 0.3, -0.2]" \
  --safety
```

## Docker Deployment

```bash
docker build -f Dockerfile.edge -t grid-guardian-edge .
docker run --rm grid-guardian-edge python edge/edge_inference.py --help
```

## Quantization (optional)

Post-training dynamic quantization reduces model size ~4× and speeds
inference on ARM. The quantized model is saved alongside the full-precision
model when `--pack-quantize` is used during training:

```bash
python train_rl_agents.py --pack-policy --pack-quantize \
  --model-path models/CQL/run_42/checkpoint_best.pt \
  --out edge/policy_pack/
```

## ARM64 Cross-Build

For cross-compiling PyTorch for Raspberry Pi 5:

```bash
# On x86 host with Docker
docker buildx build --platform linux/arm64 -f Dockerfile.edge -t grid-guardian-edge:arm64 .
```

## Input Schema

| Index | Feature | Range |
|-------|---------|-------|
| 0 | soc_kwh | 0 – 10 |
| 1 | soc_capacity_kwh | 4 / 6 / 10 |
| 2 | pv_gen_kw | 0 – 5 |
| 3 | load_kw | 0 – 3 |
| 4 | net_kw | -3 – 3 |
| 5 | battery_power_kw | -3 – 3 |
| 6 | price_signal | 3 – 8 |
| 7 | forecast_irradiance_1h | 0 – 1000 |
| 8 | forecast_irradiance_3h | 0 – 1000 |
| 9 | forecast_temp_1h | 5 – 45 |
| 10 | actual_irradiance_wm2 | 0 – 1200 |
| 11 | voltage_v | 220 – 240 |
| 12 | current_a | 0 – 15 |
| 13–16 | sin/cos time features | -1 – 1 |
| 17 | neighbor_balance | -5 – 5 |

## Output Actions

| Index | Name | kW |
|-------|------|----|
| 0 | charge_small | +1.0 |
| 1 | charge_large | +3.0 |
| 2 | idle | 0.0 |
| 3 | discharge_small | -1.0 |
| 4 | discharge_large | -3.0 |
| 5 | offer_sell | -1.5 |
| 6 | offer_hold | 0.0 |

## Safety

The edge inference script includes a minimal safety clip function that
enforces SoC bounds and power limits. Always use `--safety` flag in
production.
