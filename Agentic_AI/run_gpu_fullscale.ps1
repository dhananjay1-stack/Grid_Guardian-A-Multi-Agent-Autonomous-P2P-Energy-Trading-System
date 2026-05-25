# ══════════════════════════════════════════════════════════════════════════════
# Grid-Guardian — Full-Scale GPU Training Pipeline (RTX 4060, 8GB VRAM)
# ══════════════════════════════════════════════════════════════════════════════
# Run from PowerShell AFTER setup_gpu_env.ps1:
#   .\gpu_venv\Scripts\Activate.ps1
#   .\run_gpu_fullscale.ps1
#
# Trains all 7 algorithms + OPE (including FQE on GPU) + edge packaging
# Expected total time: ~4-6 hours on RTX 4060
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Continue"
Set-Location "C:\Users\Admin\Grid_Guardian\Agentic_AI"

# ── Activate venv if not already active ──────────────────────────────────────
if (-not $env:VIRTUAL_ENV) {
    Write-Host "Activating gpu_venv..." -ForegroundColor Yellow
    .\gpu_venv\Scripts\Activate.ps1
}

# ── Verify GPU ───────────────────────────────────────────────────────────────
Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Grid-Guardian — Full-Scale GPU Training" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan

python -c "import torch; print(f'PyTorch {torch.__version__} | CUDA: {torch.cuda.is_available()} | GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''N/A''}')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: PyTorch/CUDA not available. Run setup_gpu_env.ps1 first." -ForegroundColor Red
    exit 1
}

$startTime = Get-Date
Write-Host "Training started at: $startTime" -ForegroundColor White

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: CQL Full-Scale (1M steps) — Primary offline RL algorithm
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [1/8] CQL Full-Scale Training (1M steps) =====" -ForegroundColor Green
Write-Host "  OPE: IS, WIS, PDIS, FQE, DR (all methods, GPU has enough VRAM)" -ForegroundColor White
$cqlStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo CQL `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 1000000 `
    --eval-every 50000 `
    --eval-episodes 50 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS,FQE,DR
$cqlEnd = Get-Date
Write-Host "  CQL completed in: $(($cqlEnd - $cqlStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Decision Transformer (1M steps) — With FQE OPE on GPU
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [2/8] DT Full-Scale Training (GPU) =====" -ForegroundColor Green
Write-Host "  OPE: IS, WIS, FQE, DR — FQE now runs on GPU (no OOM)" -ForegroundColor White
$dtStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo DT `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 1000000 `
    --eval-every 50000 `
    --eval-episodes 50 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS,FQE,DR
$dtEnd = Get-Date
Write-Host "  DT completed in: $(($dtEnd - $dtStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: BCQ (500K steps) — Offline RL
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [3/8] BCQ Training (500K steps) =====" -ForegroundColor Green
$bcqStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo BCQ `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 500000 `
    --eval-every 50000 `
    --eval-episodes 30 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS,FQE,DR
$bcqEnd = Get-Date
Write-Host "  BCQ completed in: $(($bcqEnd - $bcqStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: BRAC (500K steps) — Offline RL with behavior regularization
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [4/8] BRAC Training (500K steps) =====" -ForegroundColor Green
$bracStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo BRAC `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 500000 `
    --eval-every 50000 `
    --eval-episodes 30 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS,FQE,DR
$bracEnd = Get-Date
Write-Host "  BRAC completed in: $(($bracEnd - $bracStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: DQN (500K steps) — With behavior penalty for offline use
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [5/8] DQN Training (500K steps) =====" -ForegroundColor Green
$dqnStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo DQN `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 500000 `
    --eval-every 50000 `
    --eval-episodes 30 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS,FQE,DR
$dqnEnd = Get-Date
Write-Host "  DQN completed in: $(($dqnEnd - $dqnStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6: SAC (200K steps) — Offline with behavior penalty
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [6/8] SAC Training (200K steps) =====" -ForegroundColor Green
$sacStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo SAC `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 200000 `
    --eval-every 50000 `
    --eval-episodes 30 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS
$sacEnd = Get-Date
Write-Host "  SAC completed in: $(($sacEnd - $sacStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7: PPO (200K steps) — Offline with behavior penalty
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [7/8] PPO Training (200K steps) =====" -ForegroundColor Green
$ppoStart = Get-Date
python train_rl_agents.py `
    --config configs/train_rl.yaml `
    --algo PPO `
    --mode offline `
    --device cuda:0 `
    --total-timesteps 200000 `
    --eval-every 50000 `
    --eval-episodes 30 `
    --seed 42 `
    --reproducible `
    --ope-methods IS,WIS
$ppoEnd = Get-Date
Write-Host "  PPO completed in: $(($ppoEnd - $ppoStart).ToString('hh\:mm\:ss'))" -ForegroundColor Yellow

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8: Pack best policies for edge deployment
# ══════════════════════════════════════════════════════════════════════════════
Write-Host "`n===== [8/8] Packaging Best Policies =====" -ForegroundColor Green

$packAlgos = @("CQL", "BCQ", "DQN")
foreach ($algo in $packAlgos) {
    $best = "models/$algo/run_42/checkpoint_best.pt"
    if (Test-Path $best) {
        Write-Host "  Packing $algo..." -ForegroundColor White
        python train_rl_agents.py `
            --config configs/train_rl.yaml `
            --pack-policy `
            --algo $algo `
            --model-path $best `
            --out edge/policy_pack/
    } else {
        Write-Host "  $algo checkpoint not found at $best, skipping" -ForegroundColor Yellow
    }
}

# ══════════════════════════════════════════════════════════════════════════════
# Summary
# ══════════════════════════════════════════════════════════════════════════════
$endTime = Get-Date
$totalTime = $endTime - $startTime

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  ALL TRAINING COMPLETE!" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Total time: $($totalTime.ToString('hh\:mm\:ss'))" -ForegroundColor White
Write-Host "" -ForegroundColor White
Write-Host "Results:" -ForegroundColor White
Write-Host "  Models:       models/{CQL,DT,BCQ,BRAC,DQN,SAC,PPO}/" -ForegroundColor White
Write-Host "  Eval + Plots: eval/{algo}/eval_summary.json + eval_plots/" -ForegroundColor White
Write-Host "  OPE:          ope/{algo}/ope_estimates.json" -ForegroundColor White
Write-Host "  Logs:         logs/{algo}/  (TensorBoard)" -ForegroundColor White
Write-Host "  Experiments:  experiments/{run_id}/" -ForegroundColor White
Write-Host "  Edge Pack:    edge/policy_pack/" -ForegroundColor White
Write-Host "" -ForegroundColor White
Write-Host "View TensorBoard:  tensorboard --logdir logs/" -ForegroundColor Yellow
Write-Host "View W&B:          https://wandb.ai (if enabled)" -ForegroundColor Yellow
Write-Host ""
