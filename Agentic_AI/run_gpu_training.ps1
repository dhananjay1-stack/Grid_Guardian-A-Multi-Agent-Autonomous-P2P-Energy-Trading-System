# Grid-Guardian Full GPU Training Pipeline
# Runs all algorithms on RTX 4060 (8GB VRAM)
# Usage: .\run_gpu_training.ps1
#
# Algorithms: CQL (1M), DT (1M), BCQ (500K), BRAC (500K),
#             DQN (500K), SAC (200K), PPO (200K)

$ErrorActionPreference = "Continue"

# Activate virtual environment
Write-Host "`n===== Activating Virtual Environment =====" -ForegroundColor Cyan
.\venv\Scripts\Activate.ps1

# Verify GPU
Write-Host "`n===== Verifying GPU =====" -ForegroundColor Cyan
python -c "import torch; print(f'PyTorch {torch.__version__}'); print(f'CUDA: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0)}' if torch.cuda.is_available() else 'NO GPU')"

# ── Step 1: CQL Full-Scale Training (1M steps) ─────────────────────
Write-Host "`n===== [1/8] CQL Full-Scale Training (1M steps, GPU) =====" -ForegroundColor Green
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
    --ope-methods IS,WIS,PDIS,FQE,DR

# ── Step 2: DT Full-Scale Training (GPU) ───────────────────────────
Write-Host "`n===== [2/8] DT Full-Scale Training (GPU) =====" -ForegroundColor Green
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
    --ope-methods IS,WIS

# ── Step 3: BCQ Training (500K steps) ──────────────────────────────
Write-Host "`n===== [3/8] BCQ Training (500K steps, GPU) =====" -ForegroundColor Green
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

# ── Step 4: BRAC Training (500K steps) ────────────────────────────
Write-Host "`n===== [4/8] BRAC Training (500K steps, GPU) =====" -ForegroundColor Green
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

# ── Step 5: DQN Training (500K steps) ─────────────────────────────
Write-Host "`n===== [5/8] DQN Training (500K steps, GPU) =====" -ForegroundColor Green
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

# ── Step 6: SAC Training (200K steps) ─────────────────────────────
Write-Host "`n===== [6/8] SAC Training (200K steps, GPU) =====" -ForegroundColor Green
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

# ── Step 7: PPO Training (200K steps) ─────────────────────────────
Write-Host "`n===== [7/8] PPO Training (200K steps, GPU) =====" -ForegroundColor Green
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

# ── Step 8: Pack Best Policies for Edge ───────────────────────────
Write-Host "`n===== [8/8] Packaging Best Policies =====" -ForegroundColor Green

$algos = @("CQL", "BCQ", "DQN")
foreach ($algo in $algos) {
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
        Write-Host "  $algo checkpoint not found, skipping" -ForegroundColor Yellow
    }
}

Write-Host "`n===== ALL TRAINING COMPLETE! =====" -ForegroundColor Cyan
Write-Host "Results:" -ForegroundColor White
Write-Host "  Models:       models/{CQL,DT,BCQ,BRAC,DQN,SAC,PPO}/" -ForegroundColor White
Write-Host "  Eval + Plots: eval/{algo}/eval_summary.json + eval_plots/" -ForegroundColor White
Write-Host "  OPE:          ope/{algo}/ope_estimates.json" -ForegroundColor White
Write-Host "  Logs:         logs/{algo}/  (TensorBoard)" -ForegroundColor White
Write-Host "  Experiments:  experiments/{run_id}/" -ForegroundColor White
Write-Host "  Edge Pack:    edge/policy_pack/" -ForegroundColor White
Write-Host "`nTo view TensorBoard: tensorboard --logdir logs/" -ForegroundColor Yellow
