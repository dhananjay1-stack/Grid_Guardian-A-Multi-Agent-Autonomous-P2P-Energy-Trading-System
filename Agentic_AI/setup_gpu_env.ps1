# ══════════════════════════════════════════════════════════════════════════════
# Grid-Guardian — GPU Environment Setup (RTX 4060, Windows 11)
# ══════════════════════════════════════════════════════════════════════════════
# Run from PowerShell: .\setup_gpu_env.ps1
# This creates a fresh Python 3.10 venv with CUDA-enabled PyTorch + all deps
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\Admin\Grid_Guardian\Agentic_AI"

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Grid-Guardian GPU Environment Setup" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan

# ── Step 1: Find Python 3.10 ─────────────────────────────────────────────────
Write-Host "`n[1/6] Locating Python 3.10..." -ForegroundColor Green
$pythonPath = "C:\Users\Admin\AppData\Local\Programs\Python\Python310\python.exe"
if (-not (Test-Path $pythonPath)) {
    # Fallback: try system PATH
    $pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not (Test-Path $pythonPath)) {
    Write-Host "ERROR: Python 3.10 not found. Install from https://www.python.org/downloads/release/python-31011/" -ForegroundColor Red
    exit 1
}
$ver = & $pythonPath --version 2>&1
Write-Host "  Found: $ver at $pythonPath" -ForegroundColor White

# ── Step 2: Create virtual environment ────────────────────────────────────────
Write-Host "`n[2/6] Creating virtual environment (gpu_venv)..." -ForegroundColor Green
if (Test-Path "gpu_venv") {
    Write-Host "  gpu_venv already exists. Removing..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "gpu_venv"
}
& $pythonPath -m venv gpu_venv
if (-not (Test-Path "gpu_venv\Scripts\python.exe")) {
    Write-Host "ERROR: venv creation failed" -ForegroundColor Red
    exit 1
}
Write-Host "  Created gpu_venv successfully" -ForegroundColor White

# ── Step 3: Upgrade pip ──────────────────────────────────────────────────────
Write-Host "`n[3/6] Upgrading pip..." -ForegroundColor Green
& gpu_venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel 2>&1

# ── Step 4: Install PyTorch with CUDA ─────────────────────────────────────────
# Your RTX 4060 supports CUDA 12.x. nvidia-smi shows CUDA 13.1 driver,
# which is backward compatible. Using PyTorch with CUDA 12.4 (latest stable).
Write-Host "`n[4/6] Installing PyTorch with CUDA 12.4..." -ForegroundColor Green
Write-Host "  This may take several minutes..." -ForegroundColor Yellow
& gpu_venv\Scripts\pip.exe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
if ($LASTEXITCODE -ne 0) {
    Write-Host "  CUDA 12.4 failed, trying CUDA 12.1..." -ForegroundColor Yellow
    & gpu_venv\Scripts\pip.exe install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
}

# ── Step 5: Install all project dependencies ─────────────────────────────────
Write-Host "`n[5/6] Installing project dependencies..." -ForegroundColor Green

# Core dependencies (excluding torch which is already installed)
& gpu_venv\Scripts\pip.exe install `
    "numpy>=1.24" `
    "pandas>=2.0" `
    "pyarrow>=12.0" `
    "pyyaml>=6.0" `
    "tqdm>=4.65" `
    "requests>=2.28" `
    "scipy>=1.10" `
    "scikit-learn>=1.2" `
    "matplotlib>=3.7" `
    "openpyxl>=3.1"

# Data generation
& gpu_venv\Scripts\pip.exe install `
    "pvlib>=0.10" `
    "cryptography>=41.0"

# RL & Deep Learning (torch already installed above)
& gpu_venv\Scripts\pip.exe install `
    "gymnasium>=0.29" `
    "tensorboard>=2.13" `
    "onnx>=1.14" `
    "onnxruntime>=1.15"

# Optional: Advanced RL frameworks (Step 3 from manual steps)
Write-Host "  Installing optional RL frameworks (stable-baselines3, tianshou, etc.)..."
& gpu_venv\Scripts\pip.exe install `
    "stable-baselines3>=2.0" `
    "sb3-contrib>=2.0" `
    "tianshou>=0.5" `
    "transformers>=4.30" `
    "datasets>=2.14"

# Hyperparameter tuning
& gpu_venv\Scripts\pip.exe install `
    "optuna>=3.3" `
    "hydra-core>=1.3"

# Experiment tracking (Step 2 from manual steps)
& gpu_venv\Scripts\pip.exe install "wandb>=0.15"

# Testing
& gpu_venv\Scripts\pip.exe install "pytest>=7.0"

# ── Step 6: Verify installation ──────────────────────────────────────────────
Write-Host "`n[6/6] Verifying installation..." -ForegroundColor Green
& gpu_venv\Scripts\python.exe -c @"
import torch
import gymnasium
import numpy as np
import pandas as pd

print(f'Python:      {__import__("sys").version.split()[0]}')
print(f'PyTorch:     {torch.__version__}')
print(f'CUDA avail:  {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'CUDA ver:    {torch.version.cuda}')
    print(f'GPU:         {torch.cuda.get_device_name(0)}')
    print(f'VRAM:        {torch.cuda.get_device_properties(0).total_mem / 1024**3:.1f} GB')
    # Quick GPU test
    x = torch.randn(1000, 1000, device='cuda')
    y = torch.matmul(x, x)
    print(f'GPU compute: OK (matmul test passed)')
else:
    print('WARNING: CUDA not available! Training will fall back to CPU.')

print(f'Gymnasium:   {gymnasium.__version__}')
print(f'NumPy:       {np.__version__}')
print(f'Pandas:      {pd.__version__}')

# Check optional libs
for lib in ['stable_baselines3', 'tianshou', 'wandb', 'tensorboard', 'onnx', 'onnxruntime']:
    try:
        mod = __import__(lib)
        ver = getattr(mod, '__version__', 'OK')
        print(f'{lib:20s} {ver}')
    except ImportError:
        print(f'{lib:20s} NOT INSTALLED')
"@

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "`nNext steps:" -ForegroundColor Yellow
Write-Host "  1. Activate:  .\gpu_venv\Scripts\Activate.ps1" -ForegroundColor White
Write-Host "  2. W&B login: wandb login" -ForegroundColor White
Write-Host "  3. Run tests: python -m pytest tests/test_rl_pipeline.py -v" -ForegroundColor White
Write-Host "  4. Train:     .\run_gpu_fullscale.ps1" -ForegroundColor White
Write-Host ""
