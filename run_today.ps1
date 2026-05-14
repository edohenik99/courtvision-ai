#!/usr/bin/env pwsh
# Run today's slate with validation
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$VerboseMode
)

# Use Continue rather than Stop. PowerShell otherwise turns *any* stderr line
# from native commands (including normal Python diagnostic output) into a fatal
# NativeCommandError, which aborts the script even when the underlying python
# process exits 0. We check $LASTEXITCODE explicitly after every native call.
$ErrorActionPreference = "Continue"

# Prefer the automatic $PSScriptRoot when the script is dot-sourced or run
# normally; fall back to deriving it from $MyInvocation when it's empty
# (e.g. when the file is piped or invoked via Invoke-Expression).
$ScriptRoot = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $ScriptRoot

# Ensure logs directory exists
$LogsDir = "outputs\runtime\logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$RunLog = Join-Path $LogsDir "run_today_$Date.log"
$ValidationLog = Join-Path $LogsDir "validation_$Date.log"
$GradeLog = Join-Path $LogsDir "grading_$Date.log"

"=== CourtVision Run $Date - Started at $(Get-Date) ===" | Out-File $RunLog -Append
"=== CourtVision Validation $Date - Started at $(Get-Date) ===" | Out-File $ValidationLog -Append
"=== CourtVision Grading $Date - Started at $(Get-Date) ===" | Out-File $GradeLog -Append

$ValidateRuntimeScript = Join-Path $ScriptRoot "scripts\validate_runtime_outputs.py"
$KellyStakesScript = Join-Path $ScriptRoot "scripts\run_kelly_stakes.py"
$PostRunTrackingScript = Join-Path $ScriptRoot "scripts\post_run_tracking.py"
$GradeCompletedScript = Join-Path $ScriptRoot "scripts\grade_completed_picks.py"
$MarketShadowScript = Join-Path $ScriptRoot "scripts\market_shadow_grading.py"
$DailySummaryScript = Join-Path $ScriptRoot "scripts\write_daily_summary.py"
$QualitySummaryScript = Join-Path $ScriptRoot "scripts\write_quality_summary.py"
$CompletionStateAuditScript = Join-Path $ScriptRoot "scripts\write_completion_state_audit.py"
$OperatorCardScript = Join-Path $ScriptRoot "scripts\write_operator_card.py"

# Bankroll override: read $env:COURTVISION_BANKROLL when set, else default.
$KellyBankroll = if ($env:COURTVISION_BANKROLL) { $env:COURTVISION_BANKROLL } else { "1000" }

function Write-LogLine {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $Message,
        [switch] $AlsoConsole
    )
    $Message | Out-File $Path -Append
    if ($AlsoConsole) {
        Write-Host $Message
    }
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory)] [string] $LogPath,
        [Parameter(Mandatory)] [string] $Exe,
        [Parameter(Mandatory)] [object[]] $Arguments,
        [switch] $StreamToConsole
    )

    if ($StreamToConsole) {
        & $Exe @Arguments 2>&1 |
            Tee-Object -FilePath $LogPath -Append |
            ForEach-Object { Write-Host $_ }
        $exitCode = $LASTEXITCODE
    } else {
        & $Exe @Arguments 2>&1 | Out-File -FilePath $LogPath -Append
        $exitCode = $LASTEXITCODE
    }

    if ($null -eq $exitCode) {
        return 0
    }
    return [int]$exitCode
}

function Show-LogTail {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [int] $Lines = 50
    )
    if (Test-Path $Path) {
        Write-Host "Last $Lines log lines:" -ForegroundColor Gray
        Get-Content -Path $Path -Tail $Lines | ForEach-Object { Write-Host $_ }
    } else {
        Write-Host "Log file was not created: $Path" -ForegroundColor Yellow
    }
}

function Stop-StageFailure {
    param(
        [Parameter(Mandatory)] [string] $Stage,
        [int] $ExitCode = 1,
        [Parameter(Mandatory)] [string] $LogPath
    )
    if ($ExitCode -eq 0) {
        $ExitCode = 1
    }
    Write-Host ""
    Write-Host "[FAIL] $Stage failed." -ForegroundColor Red
    Write-Host "Exit code: $ExitCode" -ForegroundColor Red
    Write-Host "Details: $LogPath" -ForegroundColor Gray
    Show-LogTail -Path $LogPath -Lines 50
    "=== Failed stage: $Stage exit_code=$ExitCode at $(Get-Date) ===" | Out-File $RunLog -Append
    exit $ExitCode
}

# Robust CSV row counter. Counts non-empty data rows and never counts headers.
function Get-CsvRowCount {
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return -1 }   # -1 sentinel = file missing
    $lines = @(Get-Content -Path $Path -ErrorAction SilentlyContinue)
    if ($null -eq $lines -or $lines.Count -le 1) { return 0 }
    $dataLines = $lines | Select-Object -Skip 1 | Where-Object { $_ -and $_.Trim().Length -gt 0 }
    return @($dataLines).Count
}

# Resolve the Python interpreter explicitly to avoid Windows picking up a
# bare `python` that points at a 3.14 install with missing dependencies.
# Order: project venv -> py launcher 3.13 -> hard error.
$VenvPython = Join-Path $ScriptRoot ".venv\Scripts\python.exe"
$PyExe = $null
$PyArgsPrefix = @()

if (Test-Path $VenvPython) {
    $PyExe = $VenvPython
    Write-LogLine -Path $RunLog -Message "[python] using project venv: $VenvPython" -AlsoConsole:$VerboseMode
} else {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $py313Probe = & py -3.13 -c "import sys; print(sys.version)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $py313Probe) {
            $PyExe = $pyLauncher.Source
            $PyArgsPrefix = @("-3.13")
            Write-LogLine -Path $RunLog -Message "[python] using py launcher: py -3.13 ($($py313Probe.Trim()))" -AlsoConsole:$VerboseMode
        }
    }
}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "CourtVision Runner - $Date" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Log file: $RunLog" -ForegroundColor Gray

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

# Check for required baselines
$playerBaselines = "outputs\model\player_baselines.csv"
$teamBaselines = "outputs\model\team_baselines.csv"
$baselinesExist = (Test-Path $playerBaselines) -and (Test-Path $teamBaselines)

if (-not $baselinesExist) {
    Write-Host ""
    Write-Host "[START] Fit Baselines" -ForegroundColor Yellow
    $fitExitCode = Invoke-LoggedCommand `
        -LogPath $RunLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @("courtvision_ai.py", "--fit-only", "--verbose-outputs")) `
        -StreamToConsole:$VerboseMode
    if ($fitExitCode -ne 0) {
        Stop-StageFailure -Stage "Fit Baselines" -ExitCode $fitExitCode -LogPath $RunLog
    }
    Write-Host "[OK] Fit complete. Full log saved to $RunLog" -ForegroundColor Green
}

Write-Host ""
Write-Host "[START] Pipeline" -ForegroundColor Yellow
$pipelineExitCode = Invoke-LoggedCommand `
    -LogPath $RunLog `
    -Exe $PyExe `
    -Arguments ($PyArgsPrefix + @("courtvision_ai.py", "--prediction-date", $Date, "--predict-only", "--verbose-outputs")) `
    -StreamToConsole:$VerboseMode
if ($pipelineExitCode -ne 0) {
    Stop-StageFailure -Stage "Pipeline" -ExitCode $pipelineExitCode -LogPath $RunLog
}
Write-Host "[OK] Pipeline complete. Full log saved to $RunLog" -ForegroundColor Green

# The pipeline always writes to outputs\runtime\operator\<board>_$Date.csv.
$operatorDir = "outputs\runtime\operator"
$eliteOperatorCsv = Join-Path $operatorDir "elite_board_$Date.csv"
$fullMarketOperatorCsv = Join-Path $operatorDir "full_market_board_$Date.csv"
$statOnlyOperatorCsv = Join-Path $operatorDir "stat_only_board_$Date.csv"
$operatorCardPath = Join-Path $operatorDir "operator_card_$Date.txt"
$kellyOutputCsv = Join-Path $operatorDir "kelly_stakes_$Date.csv"
$completionAuditTextPath = Join-Path $operatorDir "completion_state_audit_$Date.txt"
$completionAuditJsonPath = "outputs\runtime\diagnostics\completion_state_audit_$Date.json"

Write-Host ""
Write-Host "[START] Validate Outputs" -ForegroundColor Yellow
$eliteBoard = "outputs\runtime\operator\elite_board_$Date.csv"
$auditSummary = "outputs\runtime\operator\elite_pipeline_audit_summary_$Date.json"

if (-not (Test-Path $eliteBoard)) {
    Write-LogLine -Path $ValidationLog -Message "[ERROR] Elite board not found: $eliteBoard" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Validate Outputs" -ExitCode 1 -LogPath $ValidationLog
}
Write-LogLine -Path $ValidationLog -Message "[OK] Elite board exists: $eliteBoard" -AlsoConsole:$VerboseMode

if (Test-Path $auditSummary) {
    Write-LogLine -Path $ValidationLog -Message "[OK] Audit summary exists: $auditSummary" -AlsoConsole:$VerboseMode
} else {
    Write-LogLine -Path $ValidationLog -Message "[WARNING] Audit summary not found: $auditSummary" -AlsoConsole:$VerboseMode
}

$eliteCount = Get-CsvRowCount $eliteOperatorCsv
if ($eliteCount -lt 0) {
    Write-LogLine -Path $ValidationLog -Message "Elite board: [MISSING] $eliteOperatorCsv" -AlsoConsole:$VerboseMode
    $eliteCount = 0
} else {
    Write-LogLine -Path $ValidationLog -Message "Elite board: $eliteCount picks" -AlsoConsole:$VerboseMode
}

$fullMarketCount = Get-CsvRowCount $fullMarketOperatorCsv
if ($fullMarketCount -lt 0) {
    Write-LogLine -Path $ValidationLog -Message "Full market: [MISSING] $fullMarketOperatorCsv" -AlsoConsole:$VerboseMode
    $fullMarketCount = 0
} else {
    Write-LogLine -Path $ValidationLog -Message "Full market: $fullMarketCount picks" -AlsoConsole:$VerboseMode
}

$statOnlyCount = Get-CsvRowCount $statOnlyOperatorCsv
if ($statOnlyCount -lt 0) {
    Write-LogLine -Path $ValidationLog -Message "Stat only: [MISSING] $statOnlyOperatorCsv" -AlsoConsole:$VerboseMode
    $statOnlyCount = 0
} else {
    Write-LogLine -Path $ValidationLog -Message "Stat only: $statOnlyCount picks" -AlsoConsole:$VerboseMode
}

$totalPicks = $eliteCount + $fullMarketCount + $statOnlyCount
Write-LogLine -Path $ValidationLog -Message "Total picks generated: $totalPicks" -AlsoConsole:$VerboseMode

$validationExitCode = Invoke-LoggedCommand `
    -LogPath $ValidationLog `
    -Exe $PyExe `
    -Arguments ($PyArgsPrefix + @($ValidateRuntimeScript, $Date)) `
    -StreamToConsole:$VerboseMode
if ($validationExitCode -ne 0) {
    Stop-StageFailure -Stage "Validate Outputs" -ExitCode $validationExitCode -LogPath $ValidationLog
}
Write-Host "[OK] Validation passed. Details saved to $ValidationLog" -ForegroundColor Green

Write-Host ""
Write-Host "[START] Kelly" -ForegroundColor Yellow
"`n--- Kelly ---" | Out-File $GradeLog -Append
if ($eliteCount -gt 0) {
    $kellyExitCode = Invoke-LoggedCommand `
        -LogPath $GradeLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @($KellyStakesScript, "--prediction-date", $Date, "--bankroll", $KellyBankroll)) `
        -StreamToConsole:$VerboseMode
    if ($kellyExitCode -ne 0) {
        Stop-StageFailure -Stage "Kelly" -ExitCode $kellyExitCode -LogPath $GradeLog
    } elseif (-not (Test-Path $kellyOutputCsv)) {
        Write-LogLine -Path $GradeLog -Message "[ERROR] Kelly stakes output not found: $kellyOutputCsv" -AlsoConsole:$VerboseMode
        Stop-StageFailure -Stage "Kelly" -ExitCode 1 -LogPath $GradeLog
    } else {
        $kellyRows = Get-CsvRowCount $kellyOutputCsv
        Write-LogLine -Path $GradeLog -Message "[OK] Kelly stakes rows: $kellyRows -> $kellyOutputCsv" -AlsoConsole:$VerboseMode
        Write-Host "[OK] Kelly complete. Stakes written to $kellyOutputCsv" -ForegroundColor Green
    }
} else {
    Write-LogLine -Path $GradeLog -Message "[SKIP] Elite board is empty. Kelly skipped." -AlsoConsole:$VerboseMode
    Write-Host "[SKIP] Elite board is empty. Kelly skipped." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[START] Grading" -ForegroundColor Yellow
$gradingWarnings = 0
if ($totalPicks -eq 0) {
    Write-LogLine -Path $GradeLog -Message "[SKIP] No picks generated; skipped post-run tracking/grading for today." -AlsoConsole:$VerboseMode
    Write-Host "[SKIP] No picks generated. Grading skipped." -ForegroundColor Yellow
} else {
    "`n--- Post-run tracking ---" | Out-File $GradeLog -Append
    $gradeExitCode = Invoke-LoggedCommand `
        -LogPath $GradeLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @($PostRunTrackingScript, "--prediction-date", $Date, "--grade-pending")) `
        -StreamToConsole:$VerboseMode
    if ($gradeExitCode -ne 0) {
        Stop-StageFailure -Stage "Grading" -ExitCode $gradeExitCode -LogPath $GradeLog
    }

    if (Test-Path $GradeCompletedScript) {
        "`n--- Additional grading pass ---" | Out-File $GradeLog -Append
        $gradeCompletedExitCode = Invoke-LoggedCommand `
            -LogPath $GradeLog `
            -Exe $PyExe `
            -Arguments ($PyArgsPrefix + @($GradeCompletedScript)) `
            -StreamToConsole:$VerboseMode
        if ($gradeCompletedExitCode -ne 0) {
            $gradingWarnings += 1
            Write-LogLine -Path $GradeLog -Message "[WARNING] Additional grading pass failed (exit code: $gradeCompletedExitCode)." -AlsoConsole:$VerboseMode
        }
    }

    if (Test-Path $MarketShadowScript) {
        "`n--- Market shadow grading ---" | Out-File $GradeLog -Append
        $marketShadowExitCode = Invoke-LoggedCommand `
            -LogPath $GradeLog `
            -Exe $PyExe `
            -Arguments ($PyArgsPrefix + @($MarketShadowScript, "--prediction-date", $Date)) `
            -StreamToConsole:$VerboseMode
        if ($marketShadowExitCode -ne 0) {
            $gradingWarnings += 1
            Write-LogLine -Path $GradeLog -Message "[WARNING] Market shadow grading failed (exit code: $marketShadowExitCode)." -AlsoConsole:$VerboseMode
        }
    }

    if ($gradingWarnings -gt 0) {
        Write-Host "[WARN] Grading completed with warnings. Full log saved to $GradeLog" -ForegroundColor Yellow
    } else {
        Write-Host "[OK] Grading complete. Full log saved to $GradeLog" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "[START] Daily + Quality Summaries" -ForegroundColor Yellow
if (-not (Test-Path $DailySummaryScript)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Daily summary script not found: $DailySummaryScript" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Daily + Quality Summaries" -ExitCode 1 -LogPath $GradeLog
}
"`n--- Daily summary ---" | Out-File $GradeLog -Append
$dailySummaryExitCode = Invoke-LoggedCommand `
    -LogPath $GradeLog `
    -Exe $PyExe `
    -Arguments ($PyArgsPrefix + @($DailySummaryScript, "--prediction-date", $Date)) `
    -StreamToConsole:$VerboseMode
if ($dailySummaryExitCode -ne 0) {
    Stop-StageFailure -Stage "Daily + Quality Summaries" -ExitCode $dailySummaryExitCode -LogPath $GradeLog
}

if (-not (Test-Path $QualitySummaryScript)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Quality summary script not found: $QualitySummaryScript" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Daily + Quality Summaries" -ExitCode 1 -LogPath $GradeLog
}
"`n--- Quality summary ---" | Out-File $GradeLog -Append
$qualitySummaryExitCode = Invoke-LoggedCommand `
    -LogPath $GradeLog `
    -Exe $PyExe `
    -Arguments ($PyArgsPrefix + @($QualitySummaryScript, "--prediction-date", $Date)) `
    -StreamToConsole:$VerboseMode
if ($qualitySummaryExitCode -ne 0) {
    Stop-StageFailure -Stage "Daily + Quality Summaries" -ExitCode $qualitySummaryExitCode -LogPath $GradeLog
}

Write-Host "[STEP] Writing completion state audit" -ForegroundColor Yellow
if (-not (Test-Path $CompletionStateAuditScript)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Completion state audit script not found: $CompletionStateAuditScript" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Completion State Audit" -ExitCode 1 -LogPath $GradeLog
}
"`n--- Completion state audit ---" | Out-File $GradeLog -Append
$completionAuditExitCode = Invoke-LoggedCommand `
    -LogPath $GradeLog `
    -Exe $PyExe `
    -Arguments ($PyArgsPrefix + @($CompletionStateAuditScript, "--prediction-date", $Date)) `
    -StreamToConsole:$VerboseMode
if ($completionAuditExitCode -ne 0) {
    Stop-StageFailure -Stage "Completion State Audit" -ExitCode $completionAuditExitCode -LogPath $GradeLog
}
if (-not (Test-Path $completionAuditTextPath)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Completion state audit text output not found: $completionAuditTextPath" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Completion State Audit" -ExitCode 1 -LogPath $GradeLog
}
if (-not (Test-Path $completionAuditJsonPath)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Completion state audit JSON output not found: $completionAuditJsonPath" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Completion State Audit" -ExitCode 1 -LogPath $GradeLog
}
Write-Host "[OK] Summaries written." -ForegroundColor Green

Write-Host ""
Write-Host "[START] Operator Card" -ForegroundColor Yellow
if (-not (Test-Path $OperatorCardScript)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Operator card script not found: $OperatorCardScript" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Operator Card" -ExitCode 1 -LogPath $GradeLog
}
"`n--- Operator card ---" | Out-File $GradeLog -Append
$operatorCardExitCode = Invoke-LoggedCommand `
    -LogPath $GradeLog `
    -Exe $PyExe `
    -Arguments ($PyArgsPrefix + @($OperatorCardScript, "--prediction-date", $Date)) `
    -StreamToConsole:$VerboseMode
if ($operatorCardExitCode -ne 0) {
    Stop-StageFailure -Stage "Operator Card" -ExitCode $operatorCardExitCode -LogPath $GradeLog
}
if (-not (Test-Path $operatorCardPath)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Operator card output not found: $operatorCardPath" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Operator Card" -ExitCode 1 -LogPath $GradeLog
}
Write-Host "[OK] Operator card written to $operatorCardPath" -ForegroundColor Green

"=== Completed successfully at $(Get-Date) ===" | Out-File $RunLog -Append
Write-Host ""
Get-Content -Path $operatorCardPath -Encoding UTF8 | ForEach-Object { Write-Host $_ }
exit 0
