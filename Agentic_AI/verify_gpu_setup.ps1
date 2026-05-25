# ══════════════════════════════════════════════════════════════════════════════
# Grid-Guardian — Pre-Training Verification (GPU)
# ══════════════════════════════════════════════════════════════════════════════
# Run AFTER setup_gpu_env.ps1 and BEFORE run_gpu_fullscale.ps1
# Verifies: GPU, all imports, tests pass, quickstart runs
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\Admin\Grid_Guardian\Agentic_AI"

# Activate venv
if (-not $env:VIRTUAL_ENV) {
    .\gpu_venv\Scripts\Activate.ps1
}

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Grid-Guardian — Pre-Training Verification" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan

# ── Check 1: GPU ─────────────────────────────────────────────────────────────
Write-Host "`n[1/4] GPU Verification..." -ForegroundColor Green
python -c @"
import torch
assert torch.cuda.is_available(), 'CUDA not available!'
print(f'  GPU:  {torch.cuda.get_device_name(0)}')
print(f'  VRAM: {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB')
print(f'  CUDA: {torch.version.cuda}')
# Allocate and test
t = torch.randn(2048, 2048, device='cuda')
_ = t @ t
print(f'  GPU compute test: PASSED')
del t; torch.cuda.empty_cache()
"@
if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAILED: GPU not available" -ForegroundColor Red
    exit 1
}

# ── Check 2: Imports ─────────────────────────────────────────────────────────
Write-Host "`n[2/4] Import Verification..." -ForegroundColor Green
python -c @"
import sys
failures = []
for mod in ['torch', 'gymnasium', 'numpy', 'pandas', 'scipy', 'sklearn',
            'matplotlib', 'yaml', 'tensorboard', 'onnx', 'onnxruntime',
            'stable_baselines3', 'tianshou', 'wandb', 'optuna', 'tqdm']:
    try:
        __import__(mod)
        print(f'  {mod:25s} OK')
    except ImportError as e:
        failures.append(mod)
        print(f'  {mod:25s} MISSING ({e})')

# Project modules
for mod in ['env.microgrid_env', 'env.safety_shield', 'agents.bc_agent',
            'agents.classical_rl', 'agents.offline_rl', 'agents.decision_transformer',
            'data_utils.replay_buffer', 'evaluation.evaluator', 'evaluation.ope',
            'model_packaging.exporter']:
    try:
        __import__(mod)
        print(f'  {mod:25s} OK')
    except Exception as e:
        failures.append(mod)
        print(f'  {mod:25s} FAILED ({e})')

if failures:
    print(f'\n  WARNING: {len(failures)} modules failed: {failures}')
else:
    print(f'\n  All imports passed!')
"@

# ── Check 3: Unit Tests ─────────────────────────────────────────────────────
Write-Host "`n[3/4] Running unit tests..." -ForegroundColor Green
python -m pytest tests/test_rl_pipeline.py -v --tb=short 2>&1 | Select-Object -Last 25

# ── Check 4: Quick Smoke Test (5 steps) ────────────────────────────────────
Write-Host "`n[4/4] Quick smoke test (CQL, 5 steps, GPU)..." -ForegroundColor Green
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo CQL `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 100 `
    --eval-episodes 2 `
    --seed 42 `
    --dry-run 2>&1 | Select-Object -Last 15

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Verification Complete!" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  If all checks passed, run: .\run_gpu_fullscale.ps1" -ForegroundColor Yellow
Write-Host ""
