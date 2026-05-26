#!/usr/bin/env pwsh
# Run today's slate with validation
param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$VerboseMode,
    [switch]$ForcePastDate
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

# ---------------------------------------------------------------------------
# Closed-slate lifecycle guard
# Validate Date format and reject past-date full-pipeline reruns unless the
# caller has explicitly opted in with -ForcePastDate.
# This block runs before any log file, output directory, or pipeline stage is
# touched so that no mutation can occur for a closed slate.
# ---------------------------------------------------------------------------
if ($Date -notmatch '^\d{4}-\d{2}-\d{2}$') {
    Write-Host ""
    Write-Host "[ERROR] Date '$Date' does not match required format YYYY-MM-DD." -ForegroundColor Red
    Write-Host "        Example: .\run_today.ps1 -Date 2026-05-16" -ForegroundColor Red
    exit 1
}

try {
    $slateDate = [datetime]::ParseExact($Date, 'yyyy-MM-dd', $null).Date
} catch {
    Write-Host ""
    Write-Host "[ERROR] '$Date' is not a valid calendar date." -ForegroundColor Red
    exit 1
}

$todayDate = (Get-Date).Date
$ForceOutputs = $false

if ($slateDate -lt $todayDate -and -not $ForcePastDate) {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Red
    Write-Host "[BLOCKED] Past-date full generation prevented" -ForegroundColor Red
    Write-Host "========================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Slate date : $Date" -ForegroundColor Yellow
    Write-Host "  Today      : $($todayDate.ToString('yyyy-MM-dd'))" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  run_today.ps1 is for the CURRENT day's slate only." -ForegroundColor White
    Write-Host "  Running the full pipeline on a closed slate can overwrite dated" -ForegroundColor White
    Write-Host "  boards, recompute settled stakes, and mutate historical" -ForegroundColor White
    Write-Host "  operator artifacts." -ForegroundColor White
    Write-Host ""
    Write-Host "  For closed slates, use dedicated grading/repair-only workflows." -ForegroundColor Cyan
    Write-Host "  Do not refresh summaries or operator artifacts unless that is intentional." -ForegroundColor Cyan
    Write-Host "  Start with:" -ForegroundColor Cyan
    Write-Host "    python scripts\grade_completed_picks.py" -ForegroundColor Cyan
    Write-Host "    python scripts\repair_pending_grades.py" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  To intentionally regenerate a historical slate (expert use only):" -ForegroundColor Yellow
    Write-Host "    .\run_today.ps1 -Date $Date -ForcePastDate" -ForegroundColor Yellow
    Write-Host "  WARNING: -ForcePastDate overwrites all existing outputs for that date." -ForegroundColor Yellow
    Write-Host ""
    exit 2
}

$protectedPredictionArtifacts = @(
    "outputs\runtime\operator\elite_board_$Date.csv",
    "outputs\runtime\operator\full_market_board_$Date.csv",
    "outputs\runtime\operator\sgp_board_$Date.csv"
)
$existingPredictionArtifacts = @(
    $protectedPredictionArtifacts | Where-Object { Test-Path -LiteralPath $_ }
)

if ($existingPredictionArtifacts.Count -gt 0 -and -not $ForceOutputs) {
    Write-Host ""
    Write-Host "PROTECTED NO-OP: prediction artifacts already exist for $Date. No boards regenerated." -ForegroundColor Yellow
    Write-Host "Existing prediction artifacts:" -ForegroundColor Yellow
    foreach ($artifactPath in $existingPredictionArtifacts) {
        Write-Host "  $artifactPath" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "Use reporting-only refresh commands for summaries/cards; do not rerun the prediction pipeline." -ForegroundColor Cyan
    Write-Host "  py -3.13 scripts\write_shadow_artifacts.py --prediction-date $Date --closed-slate-safe" -ForegroundColor Cyan
    Write-Host "  py -3.13 scripts\write_daily_summary.py --prediction-date $Date --no-board-annotation-write" -ForegroundColor Cyan
    Write-Host "  py -3.13 scripts\write_quality_summary.py --prediction-date $Date --no-board-annotation-write" -ForegroundColor Cyan
    Write-Host "  py -3.13 scripts\write_operator_card.py --prediction-date $Date --force" -ForegroundColor Cyan
    Write-Host "  py -3.13 scripts\write_artifact_manifest.py --prediction-date $Date" -ForegroundColor Cyan
    exit 0
}

if ($ForcePastDate -and $slateDate -lt $todayDate) {
    Write-Host ""
    Write-Host "[WARNING] -ForcePastDate active: regenerating closed slate $Date." -ForegroundColor Yellow
    Write-Host "          All existing outputs for $Date will be overwritten." -ForegroundColor Yellow
    Write-Host ""
}
# ---------------------------------------------------------------------------
# End closed-slate lifecycle guard
# ---------------------------------------------------------------------------

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
$FullMarketSanityAuditScript = Join-Path $ScriptRoot "scripts\audit_full_market_sanity.py"
$CandidateQualityDriftAuditScript = Join-Path $ScriptRoot "scripts\audit_candidate_quality_drift.py"
$ShadowArtifactsScript = Join-Path $ScriptRoot "scripts\write_shadow_artifacts.py"
$DailySummaryScript = Join-Path $ScriptRoot "scripts\write_daily_summary.py"
$QualitySummaryScript = Join-Path $ScriptRoot "scripts\write_quality_summary.py"
$CompletionStateAuditScript = Join-Path $ScriptRoot "scripts\write_completion_state_audit.py"
$OperatorCardScript = Join-Path $ScriptRoot "scripts\write_operator_card.py"
$ArtifactManifestScript = Join-Path $ScriptRoot "scripts\write_artifact_manifest.py"

# Bankroll override: read $env:COURTVISION_BANKROLL when set, else default.
$KellyBankroll = if ($env:COURTVISION_BANKROLL) { $env:COURTVISION_BANKROLL } else { "1000" }
$CourtVisionMode = if ($env:COURTVISION_MODE) { $env:COURTVISION_MODE } else { "betting" }
$ForcePastDateValue = if ($ForcePastDate.IsPresent) { "true" } else { "false" }
$ForceOutputsValue = if ($ForceOutputs) { "true" } else { "false" }

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

function New-OperatorSafetyLine {
    param(
        [string] $ArtifactManifestStatus = "pending",
        [string] $FatalMissing = "pending"
    )
    return "[SAFETY] prediction_date=$Date COURTVISION_MODE=$CourtVisionMode ForcePastDate=$ForcePastDateValue ForceOutputs=$ForceOutputsValue KellyBankroll=$KellyBankroll artifact_manifest_status=$ArtifactManifestStatus fatal_missing=$FatalMissing"
}

function Write-OperatorSafetyLine {
    param(
        [Parameter(Mandatory)] [string[]] $LogPaths,
        [string] $ArtifactManifestStatus = "pending",
        [string] $FatalMissing = "pending",
        [string] $ForegroundColor = "Cyan"
    )
    $line = New-OperatorSafetyLine -ArtifactManifestStatus $ArtifactManifestStatus -FatalMissing $FatalMissing
    foreach ($path in $LogPaths) {
        $line | Out-File $path -Append
    }
    Write-Host $line -ForegroundColor $ForegroundColor
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
Write-OperatorSafetyLine -LogPaths @($RunLog) -ArtifactManifestStatus "pending" -FatalMissing "pending" -ForegroundColor "Cyan"

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

# Primary operator boards live under outputs\runtime\operator.
# Verbose/debug boards, including stat_only_board, live under outputs\runtime\optional.
$operatorDir = "outputs\runtime\operator"
$optionalDir = "outputs\runtime\optional"
$eliteOperatorCsv = Join-Path $operatorDir "elite_board_$Date.csv"
$fullMarketOperatorCsv = Join-Path $operatorDir "full_market_board_$Date.csv"
$statOnlyOptionalCsv = Join-Path $optionalDir "stat_only_board_$Date.csv"
$operatorCardPath = Join-Path $operatorDir "operator_card_$Date.txt"
$artifactManifestTextPath = Join-Path $operatorDir "artifact_manifest_$Date.txt"
$kellyOutputCsv = Join-Path $operatorDir "kelly_stakes_$Date.csv"
$completionAuditTextPath = Join-Path $operatorDir "completion_state_audit_$Date.txt"
$completionAuditJsonPath = "outputs\runtime\diagnostics\completion_state_audit_$Date.json"
$artifactManifestJsonPath = "outputs\runtime\diagnostics\artifact_manifest_$Date.json"
$fullMarketSanityAuditJsonPath = "outputs\runtime\diagnostics\full_market_sanity_audit_$Date.json"
$candidateQualityDriftAuditJsonPath = "outputs\runtime\diagnostics\candidate_quality_drift_audit_$Date.json"

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

$statOnlyCount = Get-CsvRowCount $statOnlyOptionalCsv
if ($statOnlyCount -lt 0) {
    Write-LogLine -Path $ValidationLog -Message "Stat only (optional): [MISSING] $statOnlyOptionalCsv" -AlsoConsole:$VerboseMode
    $statOnlyCount = 0
} else {
    Write-LogLine -Path $ValidationLog -Message "Stat only (optional): $statOnlyCount rows" -AlsoConsole:$VerboseMode
}

$totalPicks = $eliteCount + $fullMarketCount + $statOnlyCount
Write-LogLine -Path $ValidationLog -Message "Total board rows generated: $totalPicks" -AlsoConsole:$VerboseMode

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
Write-Host "[START] Full-Market Sanity Audit" -ForegroundColor Yellow
if (-not (Test-Path $FullMarketSanityAuditScript)) {
    Write-LogLine -Path $ValidationLog -Message "[WARNING] Full-market sanity audit script not found: $FullMarketSanityAuditScript" -AlsoConsole:$VerboseMode
    Write-Host "[WARN] Full-market sanity audit skipped; script not found: $FullMarketSanityAuditScript" -ForegroundColor Yellow
} else {
    "`n--- Full-market sanity audit ---" | Out-File $ValidationLog -Append
    $fullMarketSanityExitCode = Invoke-LoggedCommand `
        -LogPath $ValidationLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @($FullMarketSanityAuditScript, "--prediction-date", $Date)) `
        -StreamToConsole:$VerboseMode

    if ($fullMarketSanityExitCode -ne 0) {
        Write-LogLine -Path $ValidationLog -Message "[WARNING] Full-market sanity audit crashed or exited nonzero (exit code: $fullMarketSanityExitCode)." -AlsoConsole:$VerboseMode
        Write-Host "[WARN] Full-market sanity audit crashed or exited nonzero; continuing daily run." -ForegroundColor Yellow
    } elseif (-not (Test-Path $fullMarketSanityAuditJsonPath)) {
        Write-LogLine -Path $ValidationLog -Message "[WARNING] Full-market sanity audit JSON output not found: $fullMarketSanityAuditJsonPath" -AlsoConsole:$VerboseMode
        Write-Host "[WARN] Full-market sanity audit did not write JSON output; continuing daily run." -ForegroundColor Yellow
    } else {
        try {
            $fullMarketSanityPayload = Get-Content -Path $fullMarketSanityAuditJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $fullMarketSanityStatus = [string]$fullMarketSanityPayload.status
            $fullMarketSanityFailures = [int]$fullMarketSanityPayload.failure_count
            $fullMarketSanityWarnings = [int]$fullMarketSanityPayload.warning_count
            $passingFullMarketSanityStatuses = @("PASS", "PASS_NO_SLATE", "PASS_WITH_WARNINGS")
            if ($passingFullMarketSanityStatuses -contains $fullMarketSanityStatus) {
                Write-LogLine -Path $ValidationLog -Message "[OK] Full-market sanity audit status: $fullMarketSanityStatus warnings=$fullMarketSanityWarnings failures=$fullMarketSanityFailures" -AlsoConsole:$VerboseMode
                Write-Host "[OK] Full-market sanity audit status: $fullMarketSanityStatus" -ForegroundColor Green
            } else {
                Write-LogLine -Path $ValidationLog -Message "[WARNING] Full-market sanity audit status: $fullMarketSanityStatus warnings=$fullMarketSanityWarnings failures=$fullMarketSanityFailures" -AlsoConsole:$VerboseMode
                Write-Host "[WARN] Full-market sanity audit status: $fullMarketSanityStatus; continuing daily run." -ForegroundColor Yellow
            }
        } catch {
            Write-LogLine -Path $ValidationLog -Message "[WARNING] Could not read full-market sanity audit JSON: $fullMarketSanityAuditJsonPath error=$($_.Exception.Message)" -AlsoConsole:$VerboseMode
            Write-Host "[WARN] Could not read full-market sanity audit JSON; continuing daily run." -ForegroundColor Yellow
        }
    }
}

Write-Host ""
Write-Host "[START] Candidate Quality Drift Audit" -ForegroundColor Yellow
if (-not (Test-Path $CandidateQualityDriftAuditScript)) {
    Write-LogLine -Path $ValidationLog -Message "[WARNING] Candidate quality drift audit script not found: $CandidateQualityDriftAuditScript" -AlsoConsole:$VerboseMode
    Write-Host "[WARN] Candidate quality drift audit skipped; script not found: $CandidateQualityDriftAuditScript" -ForegroundColor Yellow
} else {
    "`n--- Candidate quality drift audit ---" | Out-File $ValidationLog -Append
    $candidateQualityDriftExitCode = Invoke-LoggedCommand `
        -LogPath $ValidationLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @($CandidateQualityDriftAuditScript, "--prediction-date", $Date)) `
        -StreamToConsole:$VerboseMode

    if ($candidateQualityDriftExitCode -ne 0) {
        Write-LogLine -Path $ValidationLog -Message "[WARNING] Candidate quality drift audit crashed or exited nonzero (exit code: $candidateQualityDriftExitCode)." -AlsoConsole:$VerboseMode
        Write-Host "[WARN] Candidate quality drift audit crashed or exited nonzero; continuing daily run." -ForegroundColor Yellow
    } elseif (-not (Test-Path $candidateQualityDriftAuditJsonPath)) {
        Write-LogLine -Path $ValidationLog -Message "[WARNING] Candidate quality drift audit JSON output not found: $candidateQualityDriftAuditJsonPath" -AlsoConsole:$VerboseMode
        Write-Host "[WARN] Candidate quality drift audit did not write JSON output; continuing daily run." -ForegroundColor Yellow
    } else {
        try {
            $candidateQualityDriftPayload = Get-Content -Path $candidateQualityDriftAuditJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $candidateQualityDriftStatus = [string]$candidateQualityDriftPayload.status
            $candidateQualityDriftFailures = [int]$candidateQualityDriftPayload.failure_count
            $candidateQualityDriftWarnings = [int]$candidateQualityDriftPayload.warning_count
            $cleanCandidateQualityDriftStatuses = @("PASS", "PASS_NO_SLATE")
            if ($cleanCandidateQualityDriftStatuses -contains $candidateQualityDriftStatus) {
                Write-LogLine -Path $ValidationLog -Message "[OK] Candidate quality drift audit status: $candidateQualityDriftStatus warnings=$candidateQualityDriftWarnings failures=$candidateQualityDriftFailures" -AlsoConsole:$VerboseMode
                Write-Host "[OK] Candidate quality drift audit status: $candidateQualityDriftStatus" -ForegroundColor Green
            } elseif ($candidateQualityDriftStatus -eq "PASS_WITH_WARNINGS") {
                Write-LogLine -Path $ValidationLog -Message "[WARNING] Candidate quality drift audit status: $candidateQualityDriftStatus warnings=$candidateQualityDriftWarnings failures=$candidateQualityDriftFailures" -AlsoConsole:$VerboseMode
                Write-Host "[WARN] Candidate quality drift audit status: $candidateQualityDriftStatus; continuing daily run." -ForegroundColor Yellow
            } else {
                Write-LogLine -Path $ValidationLog -Message "[WARNING] Candidate quality drift audit status: $candidateQualityDriftStatus warnings=$candidateQualityDriftWarnings failures=$candidateQualityDriftFailures" -AlsoConsole:$VerboseMode
                Write-Host "[WARN] Candidate quality drift audit status: $candidateQualityDriftStatus; continuing daily run." -ForegroundColor Yellow
            }
        } catch {
            Write-LogLine -Path $ValidationLog -Message "[WARNING] Could not read candidate quality drift audit JSON: $candidateQualityDriftAuditJsonPath error=$($_.Exception.Message)" -AlsoConsole:$VerboseMode
            Write-Host "[WARN] Could not read candidate quality drift audit JSON; continuing daily run." -ForegroundColor Yellow
        }
    }
}

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
Write-Host "[START] Phase 4B Shadow Artifacts" -ForegroundColor Yellow
if (-not (Test-Path $ShadowArtifactsScript)) {
    Write-LogLine -Path $GradeLog -Message "[WARNING] Phase 4B shadow artifact script not found: $ShadowArtifactsScript" -AlsoConsole:$VerboseMode
    Write-Host "[WARN] Phase 4B shadow artifacts skipped; script not found: $ShadowArtifactsScript" -ForegroundColor Yellow
} else {
    "`n--- Phase 4B shadow artifacts ---" | Out-File $GradeLog -Append
    $shadowArtifactsExitCode = Invoke-LoggedCommand `
        -LogPath $GradeLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @($ShadowArtifactsScript, "--prediction-date", $Date)) `
        -StreamToConsole:$VerboseMode
    if ($shadowArtifactsExitCode -ne 0) {
        Write-LogLine -Path $GradeLog -Message "[WARNING] Phase 4B shadow artifact writer failed or exited nonzero (exit code: $shadowArtifactsExitCode). Continuing daily run because these artifacts are shadow-only." -AlsoConsole:$VerboseMode
        Write-Host "[WARN] Phase 4B shadow artifact writer failed; continuing daily run." -ForegroundColor Yellow
    } else {
        Write-LogLine -Path $GradeLog -Message "[OK] Phase 4B shadow artifact step completed." -AlsoConsole:$VerboseMode
        Write-Host "[OK] Phase 4B shadow artifact step completed." -ForegroundColor Green
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
    -Arguments ($PyArgsPrefix + @(
        $OperatorCardScript,
        "--prediction-date", $Date,
        "--runtime-mode", $CourtVisionMode,
        "--force-past-date", $ForcePastDateValue,
        "--force-outputs", $ForceOutputsValue,
        "--kelly-bankroll", $KellyBankroll
    )) `
    -StreamToConsole:$VerboseMode
if ($operatorCardExitCode -ne 0) {
    Stop-StageFailure -Stage "Operator Card" -ExitCode $operatorCardExitCode -LogPath $GradeLog
}
if (-not (Test-Path $operatorCardPath)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Operator card output not found: $operatorCardPath" -AlsoConsole:$VerboseMode
    Stop-StageFailure -Stage "Operator Card" -ExitCode 1 -LogPath $GradeLog
}
Write-Host "[OK] Operator card written to $operatorCardPath" -ForegroundColor Green

Write-Host ""
Write-Host "[START] Artifact Manifest" -ForegroundColor Yellow
$artifactManifestStatusForBanner = "unavailable"
$artifactFatalMissingForBanner = "unknown"
$artifactManifestBannerColor = "Yellow"
$artifactManifestFatalFailure = $false
if (-not (Test-Path $ArtifactManifestScript)) {
    Write-LogLine -Path $GradeLog -Message "[ERROR] Artifact manifest safety failure: script not found: $ArtifactManifestScript" -AlsoConsole:$VerboseMode
    Write-Host "[FAIL] Artifact manifest safety failure: script not found." -ForegroundColor Red
    Write-Host "       Artifact manifest unavailable; refusing to mark run complete." -ForegroundColor Red
    $artifactManifestStatusForBanner = "script_missing"
    $artifactManifestBannerColor = "Red"
    $artifactManifestFatalFailure = $true
} else {
    "`n--- Artifact manifest ---" | Out-File $GradeLog -Append
    $artifactManifestExitCode = Invoke-LoggedCommand `
        -LogPath $GradeLog `
        -Exe $PyExe `
        -Arguments ($PyArgsPrefix + @($ArtifactManifestScript, "--prediction-date", $Date)) `
        -StreamToConsole:$VerboseMode

    if ($artifactManifestExitCode -ne 0) {
        Write-LogLine -Path $GradeLog -Message "[ERROR] Artifact manifest safety failure: writer failed or exited nonzero (exit code: $artifactManifestExitCode)." -AlsoConsole:$VerboseMode
        Write-Host "[FAIL] Artifact manifest safety failure: writer failed or exited nonzero." -ForegroundColor Red
        Write-Host "       Artifact manifest unavailable; refusing to mark run complete." -ForegroundColor Red
        $artifactManifestStatusForBanner = "writer_failed"
        $artifactManifestBannerColor = "Red"
        $artifactManifestFatalFailure = $true
    } elseif (-not (Test-Path $artifactManifestJsonPath)) {
        Write-LogLine -Path $GradeLog -Message "[ERROR] Artifact manifest safety failure: JSON output not found: $artifactManifestJsonPath" -AlsoConsole:$VerboseMode
        Write-Host "[FAIL] Artifact manifest safety failure: JSON output not found." -ForegroundColor Red
        Write-Host "       Artifact manifest unavailable; refusing to mark run complete." -ForegroundColor Red
        $artifactManifestStatusForBanner = "json_missing"
        $artifactManifestBannerColor = "Red"
        $artifactManifestFatalFailure = $true
    } elseif (-not (Test-Path $artifactManifestTextPath)) {
        Write-LogLine -Path $GradeLog -Message "[ERROR] Artifact manifest safety failure: TXT output not found: $artifactManifestTextPath" -AlsoConsole:$VerboseMode
        Write-Host "[FAIL] Artifact manifest safety failure: TXT output not found." -ForegroundColor Red
        Write-Host "       Artifact manifest unavailable; refusing to mark run complete." -ForegroundColor Red
        $artifactManifestStatusForBanner = "txt_missing"
        $artifactManifestBannerColor = "Red"
        $artifactManifestFatalFailure = $true
    } else {
        try {
            $artifactManifestPayload = Get-Content -Path $artifactManifestJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $artifactManifestStatus = [string]$artifactManifestPayload.status
            $artifactFatalMissing = 0
            if ($null -ne $artifactManifestPayload.missing_by_severity -and $null -ne $artifactManifestPayload.missing_by_severity.fatal) {
                $artifactFatalMissing = [int]$artifactManifestPayload.missing_by_severity.fatal
            }
            Write-LogLine -Path $GradeLog -Message "[OK] Artifact manifest JSON: $artifactManifestJsonPath" -AlsoConsole:$VerboseMode
            Write-LogLine -Path $GradeLog -Message "[OK] Artifact manifest TXT: $artifactManifestTextPath" -AlsoConsole:$VerboseMode
            $artifactManifestStatusForBanner = $artifactManifestStatus
            $artifactFatalMissingForBanner = [string]$artifactFatalMissing
            if ($artifactFatalMissing -gt 0) {
                $artifactManifestBannerColor = "Red"
                $artifactManifestFatalFailure = $true
                Write-LogLine -Path $GradeLog -Message "[ERROR] Artifact manifest safety failure: status=$artifactManifestStatus fatal_missing=$artifactFatalMissing" -AlsoConsole:$VerboseMode
                Write-Host "[FAIL] Artifact manifest safety failure: fatal_missing=$artifactFatalMissing" -ForegroundColor Red
                Write-Host "       Fatal operator artifacts are missing; refusing to mark run complete." -ForegroundColor Red
                Write-Host "       Review $artifactManifestTextPath" -ForegroundColor Red
            } elseif ($artifactManifestStatus -eq "warning_missing") {
                $artifactManifestBannerColor = "Yellow"
                Write-LogLine -Path $GradeLog -Message "[WARNING] Artifact manifest warning_missing allowed: fatal_missing=0" -AlsoConsole:$VerboseMode
                Write-Host "[WARN] Artifact manifest warning_missing allowed: fatal_missing=0" -ForegroundColor Yellow
                Write-Host "       Nonfatal artifacts may be absent; review $artifactManifestTextPath" -ForegroundColor Yellow
            } else {
                $artifactManifestBannerColor = "Green"
                Write-LogLine -Path $GradeLog -Message "[OK] Artifact manifest status: $artifactManifestStatus fatal_missing=0" -AlsoConsole:$VerboseMode
                Write-Host "[OK] Artifact manifest written to $artifactManifestTextPath and $artifactManifestJsonPath" -ForegroundColor Green
            }
        } catch {
            Write-LogLine -Path $GradeLog -Message "[ERROR] Artifact manifest safety failure: could not read artifact manifest JSON: $artifactManifestJsonPath error=$($_.Exception.Message)" -AlsoConsole:$VerboseMode
            Write-Host "[FAIL] Artifact manifest safety failure: could not read artifact manifest JSON." -ForegroundColor Red
            Write-Host "       Artifact manifest read failed; refusing to mark run complete." -ForegroundColor Red
            $artifactManifestStatusForBanner = "read_failed"
            $artifactManifestBannerColor = "Red"
            $artifactManifestFatalFailure = $true
        }
    }
}
Write-OperatorSafetyLine -LogPaths @($RunLog, $GradeLog) -ArtifactManifestStatus $artifactManifestStatusForBanner -FatalMissing $artifactFatalMissingForBanner -ForegroundColor $artifactManifestBannerColor
if ($artifactManifestFatalFailure) {
    Stop-StageFailure -Stage "Artifact Manifest" -ExitCode 1 -LogPath $GradeLog
}

"=== Completed successfully at $(Get-Date) ===" | Out-File $RunLog -Append
Write-Host ""
Get-Content -Path $operatorCardPath -Encoding UTF8 | ForEach-Object { Write-Host $_ }
exit 0
