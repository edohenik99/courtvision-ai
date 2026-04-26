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

# Use a private $ScriptRoot variable instead of reassigning the automatic
# $PSScriptRoot (which PSScriptAnalyzer flags as PSAvoidAssignmentToAutomaticVariable).
# Prefer the automatic $PSScriptRoot when the script is dot-sourced or run
# normally; fall back to deriving it from $MyInvocation when it's empty
# (e.g. when the file is piped or invoked via Invoke-Expression).
$ScriptRoot = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $ScriptRoot

$ValidateRuntimeScript = Join-Path $ScriptRoot "scripts\validate_runtime_outputs.py"
$KellyStakesScript = Join-Path $ScriptRoot "scripts\run_kelly_stakes.py"
$PostRunTrackingScript = Join-Path $ScriptRoot "scripts\post_run_tracking.py"
$GradeCompletedScript = Join-Path $ScriptRoot "scripts\grade_completed_picks.py"
$MarketShadowScript = Join-Path $ScriptRoot "scripts\market_shadow_grading.py"
$DailySummaryScript = Join-Path $ScriptRoot "scripts\write_daily_summary.py"

# Bankroll override: read $env:COURTVISION_BANKROLL when set, else default.
$KellyBankroll = if ($env:COURTVISION_BANKROLL) { $env:COURTVISION_BANKROLL } else { "1000" }

# Resolve the Python interpreter explicitly to avoid Windows picking up a
# bare `python` that points at a 3.14 install with missing dependencies.
# Order: project venv -> py launcher 3.13 -> hard error.
$VenvPython = Join-Path $ScriptRoot ".venv\Scripts\python.exe"
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

# Robust CSV row counter.
# Bug fix: the previous implementation used
#   (Get-Content <file> | Measure-Object).Line
# which is *not* populated unless `-Line` is passed. The result was $null,
# and `$null -le 1` evaluates to $true, so every board was reported as
# 0 picks even when the CSV contained 10+ rows. We now count non-empty
# data lines explicitly and never count the header as a pick.
function Get-CsvRowCount {
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return -1 }   # -1 sentinel = file missing
    $lines = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    if ($null -eq $lines -or $lines.Count -le 1) { return 0 }
    # Skip the header (line 0) and any trailing blank lines.
    $dataLines = $lines | Select-Object -Skip 1 | Where-Object { $_ -and $_.Trim().Length -gt 0 }
    return @($dataLines).Count
}

# The pipeline always writes to outputs\runtime\operator\<board>_$Date.csv.
# (The legacy outputs\runtime\boards\$Date\*.csv layout has been removed.)
$operatorDir = "outputs\runtime\operator"
$eliteOperatorCsv = Join-Path $operatorDir "elite_board_$Date.csv"
$fullMarketOperatorCsv = Join-Path $operatorDir "full_market_board_$Date.csv"
$statOnlyOperatorCsv = Join-Path $operatorDir "stat_only_board_$Date.csv"

Write-Host "`n  Board Summary:" -ForegroundColor Cyan

$eliteCount = Get-CsvRowCount $eliteOperatorCsv
if ($eliteCount -lt 0) {
    Write-Host "    Elite board:      [MISSING] $eliteOperatorCsv" -ForegroundColor Yellow
    $eliteCount = 0
} else {
    Write-Host "    Elite board:      $eliteCount picks ($eliteOperatorCsv)" -ForegroundColor $(if ($eliteCount -gt 0) { "Green" } else { "Yellow" })
    if ($eliteCount -eq 0) {
        Write-Host "    [WARNING] Elite board is empty (all candidates filtered out)" -ForegroundColor Yellow
    }
}

$fullMarketCount = Get-CsvRowCount $fullMarketOperatorCsv
if ($fullMarketCount -lt 0) {
    Write-Host "    Full market:      [MISSING] $fullMarketOperatorCsv" -ForegroundColor Yellow
    $fullMarketCount = 0
} else {
    Write-Host "    Full market:      $fullMarketCount picks" -ForegroundColor $(if ($fullMarketCount -gt 0) { "Green" } else { "Yellow" })
}

$statOnlyCount = Get-CsvRowCount $statOnlyOperatorCsv
if ($statOnlyCount -lt 0) {
    Write-Host "    Stat only:        [MISSING] $statOnlyOperatorCsv" -ForegroundColor Yellow
    $statOnlyCount = 0
} else {
    Write-Host "    Stat only:        $statOnlyCount picks" -ForegroundColor $(if ($statOnlyCount -gt 0) { "Green" } else { "Yellow" })
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

# ---------------------------------------------------------------------------
# Kelly stakes: pipeline -> validation -> KELLY -> grading
# Reads outputs\runtime\operator\elite_board_<Date>.csv and writes
# outputs\runtime\operator\kelly_stakes_<Date>.csv. Failure here does not
# abort grading, but the operator gets a clear console error and a non-zero
# exit code is preserved at the end of the run.
# ---------------------------------------------------------------------------
$kellyOutputCsv = Join-Path $operatorDir "kelly_stakes_$Date.csv"
$kellyExitCode = 0
if ($validationPassed -and $eliteCount -gt 0) {
    Write-Host "`n[2.5/3] Computing Kelly stakes (bankroll=$KellyBankroll)..." -ForegroundColor Yellow
    & $PyExe @PyArgsPrefix $KellyStakesScript --prediction-date $Date --bankroll $KellyBankroll
    $kellyExitCode = $LASTEXITCODE
    if ($kellyExitCode -ne 0) {
        Write-Host "  [ERROR] Kelly stakes step failed (exit code: $kellyExitCode)" -ForegroundColor Red
        "[ERROR] Kelly stakes step failed (exit code: $kellyExitCode) at $(Get-Date)" | Out-File $RunLog -Append
        $validationPassed = $false
    } elseif (-not (Test-Path $kellyOutputCsv)) {
        Write-Host "  [ERROR] Kelly stakes output not found: $kellyOutputCsv" -ForegroundColor Red
        $kellyExitCode = 1
        $validationPassed = $false
    } else {
        $kellyRows = Get-CsvRowCount $kellyOutputCsv
        Write-Host "  [OK] Kelly stakes: $kellyRows rows -> $kellyOutputCsv" -ForegroundColor Green
    }
} elseif ($eliteCount -le 0) {
    Write-Host "`n[2.5/3] Skipping Kelly: elite board is empty." -ForegroundColor Yellow
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

        if (Test-Path $MarketShadowScript) {
            "`n--- Market shadow grading ---" | Out-File $GradeLog -Append
            try {
                & $PyExe @PyArgsPrefix $MarketShadowScript --prediction-date $Date 2>&1 | Tee-Object -FilePath $GradeLog -Append
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  [WARNING] Market shadow grading failed (exit code: $LASTEXITCODE)" -ForegroundColor Yellow
                }
            } catch {
                "Market shadow grading exception: " + $_.Exception.Message | Out-File $GradeLog -Append
            }
        }

        if (Test-Path $DailySummaryScript) {
            "`n--- Daily summary ---" | Out-File $GradeLog -Append
            try {
                & $PyExe @PyArgsPrefix $DailySummaryScript --prediction-date $Date 2>&1 | Tee-Object -FilePath $GradeLog -Append
                if ($LASTEXITCODE -ne 0) {
                    Write-Host "  [WARNING] Daily summary failed (exit code: $LASTEXITCODE)" -ForegroundColor Yellow
                }
            } catch {
                "Daily summary exception: " + $_.Exception.Message | Out-File $GradeLog -Append
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
