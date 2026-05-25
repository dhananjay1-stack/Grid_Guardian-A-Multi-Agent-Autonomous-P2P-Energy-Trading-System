# ══════════════════════════════════════════════════════════════════════════════
# Grid-Guardian — W&B Setup + Config Update
# ══════════════════════════════════════════════════════════════════════════════
# Run from PowerShell AFTER setup_gpu_env.ps1:
#   .\gpu_venv\Scripts\Activate.ps1
#   .\setup_wandb.ps1
# ══════════════════════════════════════════════════════════════════════════════

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\Admin\Grid_Guardian\Agentic_AI"

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Grid-Guardian — W&B Integration Setup" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan

# Step 1: Login to W&B
Write-Host "`n[1/2] Logging into Weights & Biases..." -ForegroundColor Green
Write-Host "  You'll be prompted for your API key." -ForegroundColor White
Write-Host "  Get it from: https://wandb.ai/authorize" -ForegroundColor Yellow
wandb login

if ($LASTEXITCODE -ne 0) {
    Write-Host "WARNING: W&B login failed. You can retry with: wandb login --relogin" -ForegroundColor Yellow
} else {
    Write-Host "  W&B login successful!" -ForegroundColor Green
}

# Step 2: Update config to enable W&B
Write-Host "`n[2/2] Updating config to enable W&B logging..." -ForegroundColor Green
$configPath = "configs\train_rl.yaml"
$content = Get-Content $configPath -Raw
$updated = $content -replace 'wandb: false', 'wandb: true'
Set-Content $configPath $updated -NoNewline
Write-Host "  Updated $configPath : wandb: false -> wandb: true" -ForegroundColor White

Write-Host "`n═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  W&B Setup Complete!" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Project: grid-guardian-rl" -ForegroundColor White
Write-Host "  Dashboard: https://wandb.ai" -ForegroundColor White
Write-Host "`n  Now run: .\run_gpu_fullscale.ps1" -ForegroundColor Yellow
Write-Host ""
