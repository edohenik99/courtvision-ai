#!/usr/bin/env pwsh
# Run today's slate with validation
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

# Ensure logs directory exists
$LogsDir = "outputs\runtime\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$RunLog = Join-Path $LogsDir "run_today_$Date.log"
$GradeLog = Join-Path $LogsDir "grading_$Date.log"

# Start logging
"=== CourtVision Run $Date - Started at $(Get-Date) ===" | Out-File $RunLog -Append

# Use Continue rather than Stop. PowerShell otherwise turns *any* stderr line
# from native commands (including normal Python diagnostic output) into a fatal
# NativeCommandError, which aborts the script even when the underlying python
# process exits 0. We check $LASTEXITCODE explicitly after every native call.
$ErrorActionPreference = "Continue"

if (-not $PSScriptRoot) {
    $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $PSScriptRoot

$ValidateRuntimeScript = Join-Path $PSScriptRoot "scripts\validate_runtime_outputs.py"
$PostRunTrackingScript = Join-Path $PSScriptRoot "scripts\post_run_tracking.py"
$GradeCompletedScript = Join-Path $PSScriptRoot "scripts\grade_completed_picks.py"

# Resolve the Python interpreter explicitly to avoid Windows picking up a
# bare `python` that points at a 3.14 install with missing dependencies.
# Order: project venv -> py launcher 3.13 -> hard error.
$VenvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$PyExe = $null
$PyArgsPrefix = @()

if (Test-Path $VenvPython) {
    $PyExe = $VenvPython
    Write-Host "[python] using project venv: $VenvPython" -ForegroundColor Gray
} else {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        # Probe for an installed 3.13 interpreter via the py launcher.
        $py313Probe = & py -3.13 -c "import sys; print(sys.version)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $py313Probe) {
            $PyExe = $pyLauncher.Source
            $PyArgsPrefix = @("-3.13")
            Write-Host "[python] using py launcher: py -3.13 ($($py313Probe.Trim()))" -ForegroundColor Gray
        }
    }
}

if (-not $PyExe) {
    Write-Host "" -ForegroundColor Red
    Write-Host "[ERROR] No usable Python interpreter found." -ForegroundColor Red
    Write-Host "        Expected one of:" -ForegroundColor Red
    Write-Host "          1. .\.venv\Scripts\python.exe (project virtual environment)" -ForegroundColor Red
    Write-Host "          2. py -3.13 (Windows py launcher with Python 3.13 installed)" -ForegroundColor Red
    Write-Host "" -ForegroundColor Red
    Write-Host "        To create the project venv:" -ForegroundColor Red
    Write-Host "          py -3.13 -m venv .venv" -ForegroundColor Red
    Write-Host "          .\.venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Red
    Write-Host "" -ForegroundColor Red
    "=== Aborted: no usable Python interpreter at $(Get-Date) ===" | Out-File $RunLog -Append
    exit 1
}

# Quick dependency probe: pandas is the load-bearing import. Failing here
# gives a clearer message than the eventual `ModuleNotFoundError` mid-run.
& $PyExe @PyArgsPrefix -c "import pandas" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "" -ForegroundColor Red
    Write-Host "[ERROR] Selected interpreter is missing required dependencies (pandas)." -ForegroundColor Red
    Write-Host "        Interpreter: $PyExe $PyArgsPrefix" -ForegroundColor Red
    Write-Host "        Install with:" -ForegroundColor Red
    Write-Host "          $PyExe $PyArgsPrefix -m pip install -r requirements.txt" -ForegroundColor Red
    "=== Aborted: missing dependencies at $(Get-Date) ===" | Out-File $RunLog -Append
    exit 1
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "CourtVision Runner - $Date" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Log file: $RunLog" -ForegroundColor Gray

# Check for required baselines
$playerBaselines = "outputs\model\player_baselines.csv"
$teamBaselines = "outputs\model\team_baselines.csv"

$baselinesExist = (Test-Path $playerBaselines) -and (Test-Path $teamBaselines)

if (-not $baselinesExist) {
    Write-Host "`n[WARNING] Model baselines missing. Running fit first..." -ForegroundColor Yellow
    & $PyExe @PyArgsPrefix courtvision_ai.py --fit-only --verbose-outputs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "`n[ERROR] FIT FAILED (exit code: $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
    Write-Host "  [OK] Fit completed" -ForegroundColor Green
}

# Run pipeline with tee to both console and log
Write-Host "`n[1/3] Running pipeline..." -ForegroundColor Yellow
Write-Host "  (Output also saved to: $RunLog)" -ForegroundColor Gray

# Run pipeline - capture output without treating stderr as fatal
$pipelineOutput = & $PyExe @PyArgsPrefix courtvision_ai.py --prediction-date $Date --predict-only --verbose-outputs 2>&1
$pipelineExitCode = $LASTEXITCODE

# Tee output to both console and log
$pipelineOutput | Tee-Object -FilePath $RunLog -Append

if ($pipelineExitCode -ne 0) {
    "`n[ERROR] PIPELINE FAILED (exit code: $pipelineExitCode)" | Out-File $RunLog -Append
    Write-Host "`n[ERROR] PIPELINE FAILED (exit code: $pipelineExitCode)" -ForegroundColor Red
    Write-Host "See full log: $RunLog" -ForegroundColor Gray
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

if (Test-Path $auditSummary) {
    Write-Host "  [OK] Audit summary exists" -ForegroundColor Green
} else {
    Write-Host "  [WARNING] Audit summary not found: $auditSummary" -ForegroundColor Yellow
}

# Check board row counts from runtime operator outputs
$boardsDir = "outputs\runtime\boards\$Date"
$operatorDir = "outputs\runtime\operator"
$eliteCsv = Join-Path $boardsDir "elite.csv"
$fullMarketCsv = Join-Path $boardsDir "full_market.csv"
$statOnlyCsv = Join-Path $boardsDir "stat_only.csv"

# Also check operator dir (actual save location)
$eliteOperatorCsv = Join-Path $operatorDir "elite_board_$Date.csv"
$fullMarketOperatorCsv = Join-Path $operatorDir "full_market_board_$Date.csv"
$statOnlyOperatorCsv = Join-Path $operatorDir "stat_only_board_$Date.csv"

Write-Host "`n  Board Summary:" -ForegroundColor Cyan

$eliteCount = 0
$fullMarketCount = 0
$statOnlyCount = 0

# Check operator files (actual location)
if (Test-Path $eliteOperatorCsv) {
    $eliteLines = (Get-Content $eliteOperatorCsv | Measure-Object).Line
    $eliteCount = if ($eliteLines -le 1) { 0 } else { $eliteLines - 1 }  # Handle empty files
    Write-Host "    Elite board:      $eliteCount picks ($eliteOperatorCsv)" -ForegroundColor $(if ($eliteCount -gt 0) { "Green" } else { "Yellow" })
    if ($eliteCount -eq 0) {
        Write-Host "    [WARNING] Elite board is empty (all candidates filtered out)" -ForegroundColor Yellow
    }
} else {
    Write-Host "    Elite board:      [MISSING] $eliteOperatorCsv" -ForegroundColor Yellow
    $eliteCount = 0
}

if (Test-Path $fullMarketOperatorCsv) {
    $fullLines = (Get-Content $fullMarketOperatorCsv | Measure-Object).Line
    $fullMarketCount = if ($fullLines -le 1) { 0 } else { $fullLines - 1 }
    Write-Host "    Full market:      $fullMarketCount picks" -ForegroundColor $(if ($fullMarketCount -gt 0) { "Green" } else { "Yellow" })
} else {
    Write-Host "    Full market:      [MISSING] $fullMarketOperatorCsv" -ForegroundColor Yellow
    $fullMarketCount = 0
}

if (Test-Path $statOnlyOperatorCsv) {
    $statLines = (Get-Content $statOnlyOperatorCsv | Measure-Object).Line
    $statOnlyCount = if ($statLines -le 1) { 0 } else { $statLines - 1 }
    Write-Host "    Stat only:        $statOnlyCount picks" -ForegroundColor $(if ($statOnlyCount -gt 0) { "Green" } else { "Yellow" })
} else {
    Write-Host "    Stat only:        [MISSING] $statOnlyOperatorCsv" -ForegroundColor Yellow
    $statOnlyCount = 0
}

# Summary line
$totalPicks = $eliteCount + $fullMarketCount + $statOnlyCount
Write-Host "`n  Total picks generated: $totalPicks" -ForegroundColor $(if ($totalPicks -gt 0) { "Green" } else { "Red" })

$validationPassed = $true

# Caps, directional checks, final summary, and preview (no fragile inline Python)
& $PyExe @PyArgsPrefix $ValidateRuntimeScript $Date
if ($LASTEXITCODE -ne 0) {
    $validationPassed = $false
}

# Persist picks and grade pending history if validation passed and picks exist
if ($validationPassed) {
    if ($totalPicks -eq 0) {
        Write-Host "`n[WARNING] No picks generated; skipping post-run tracking/grading for today" -ForegroundColor Yellow
        "No picks generated - skipped grading at $(Get-Date)" | Out-File $GradeLog -Append
    } else {
        Write-Host "`n[3/3] Checking for grading..." -ForegroundColor Yellow
        Write-Host "  (Output saved to: $GradeLog)" -ForegroundColor Gray
        
        try {
            & $PyExe @PyArgsPrefix $PostRunTrackingScript --prediction-date $Date --grade-pending 2>&1 | Tee-Object -FilePath $GradeLog -Append
            $gradeExitCode = $LASTEXITCODE
        } catch {
            $gradeExitCode = 1
            $errorMsg = "Grading exception: " + $_.Exception.Message
            $errorMsg | Out-File $GradeLog -Append
            Write-Error $errorMsg
        }
        
        if ($gradeExitCode -ne 0) {
            "[ERROR] Post-run tracking failed (exit code: $gradeExitCode) at $(Get-Date)" | Out-File $GradeLog -Append
            Write-Host "  [ERROR] Post-run tracking failed (exit code: $gradeExitCode)" -ForegroundColor Red
            Write-Host "  See full log: $GradeLog" -ForegroundColor Gray
            exit 1
        }
        
        # Optional extra pass for older pending picks if manually invoked later.
        if (Test-Path $GradeCompletedScript) {
            "`n--- Additional grading pass ---" | Out-File $GradeLog -Append
            try {
                & $PyExe @PyArgsPrefix $GradeCompletedScript 2>&1 | Tee-Object -FilePath $GradeLog -Append
            } catch {
                "Grade script exception: " + $_.Exception.Message | Out-File $GradeLog -Append
            }
        }
    }
}

# Final summary
if ($validationPassed) {
    Write-Host "`n[SUCCESS] ALL VALIDATIONS PASSED" -ForegroundColor Green
    Write-Host "  Log files:" -ForegroundColor Gray
    Write-Host "    - Run log: $RunLog" -ForegroundColor Gray
    if ($totalPicks -gt 0) {
        Write-Host "    - Grade log: $GradeLog" -ForegroundColor Gray
    }
    Write-Host "========================================" -ForegroundColor Green
    "=== Completed successfully at $(Get-Date) ===" | Out-File $RunLog -Append
    exit 0
} else {
    Write-Host "`n[FAILED] VALIDATION FAILED" -ForegroundColor Red
    Write-Host "  See log: $RunLog" -ForegroundColor Gray
    Write-Host "========================================" -ForegroundColor Red
    "=== Failed at $(Get-Date) ===" | Out-File $RunLog -Append
    exit 1
}
