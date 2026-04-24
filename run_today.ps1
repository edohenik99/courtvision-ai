#!/usr/bin/env pwsh
# Run today's slate with validation
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"

if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $PSScriptRoot

$ValidateRuntimeScript = Join-Path $PSScriptRoot "scripts\validate_runtime_outputs.py"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "CourtVision Runner - $Date" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Check for required baselines
$playerBaselines = "outputs\model\player_baselines.csv"
$teamBaselines = "outputs\model\team_baselines.csv"

$baselinesExist = (Test-Path $playerBaselines) -and (Test-Path $teamBaselines)

if (-not $baselinesExist) {
    Write-Host "`n[WARNING] Model baselines missing. Running fit first..." -ForegroundColor Yellow
    python courtvision_ai.py --fit-only --verbose-outputs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[ERROR] FIT FAILED (exit code: $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Fit completed" -ForegroundColor Green
}

# Run pipeline
Write-Host "`n[1/3] Running pipeline..." -ForegroundColor Yellow
python courtvision_ai.py --prediction-date $Date --predict-only --verbose-outputs
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] PIPELINE FAILED (exit code: $LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# Verify elite board exists
Write-Host "`n[2/3] Validating outputs..." -ForegroundColor Yellow
$eliteBoard = "outputs\runtime\operator\elite_board_$Date.csv"
$auditSummary = "outputs\runtime\operator\elite_pipeline_audit_summary_$Date.json"

if (-not (Test-Path $eliteBoard)) {
    Write-Host "[ERROR] Elite board not found: $eliteBoard" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Elite board exists" -ForegroundColor Green

if (-not (Test-Path $auditSummary)) {
    Write-Host "[ERROR] Audit summary not found: $auditSummary" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] Audit summary exists" -ForegroundColor Green

$validationPassed = $true

# Caps, directional checks, final summary, and preview (no fragile inline Python)
python $ValidateRuntimeScript $Date
if ($LASTEXITCODE -ne 0) {
    $validationPassed = $false
}

# Auto-grade if validation passed and games may have been played
if ($validationPassed) {
    Write-Host "`n[3/3] Checking for grading..." -ForegroundColor Yellow
    
    # Check if picks file exists for grading
    $picksFile = "outputs\runtime\history\picks_$Date.csv"
    $gradedFile = "outputs\runtime\history\graded_picks_$Date.csv"
    
    if ((Test-Path $picksFile) -and -not (Test-Path $gradedFile)) {
        Write-Host "  Running auto-grade for $Date..." -ForegroundColor White
        python courtvision_ai.py --grade-date $Date --verbose-outputs 2>&1 | ForEach-Object {
            if ($_ -match "graded|hit_rate|roi|letter_grade") {
                Write-Host "    $_" -ForegroundColor White
            }
        }
        
        if (Test-Path $gradedFile) {
            Write-Host "  [OK] Grading complete" -ForegroundColor Green
            
            python $ValidateRuntimeScript $Date --grading-summary | ForEach-Object { Write-Host $_ }
        } else {
            Write-Host "  [INFO] Games not yet completed or already graded" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [OK] No grading needed (already graded or no picks)" -ForegroundColor Green
    }
}

if ($validationPassed) {
    Write-Host "`n[SUCCESS] ALL VALIDATIONS PASSED" -ForegroundColor Green
    Write-Host "========================================" -ForegroundColor Green
    exit 0
} else {
    Write-Host "`n[FAILED] VALIDATION FAILED" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    exit 1
}
