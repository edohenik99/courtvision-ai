#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$TrialId,

    [Parameter(Mandatory)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$PredictionDate,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ConfigHash,

    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$RunDate,

    [switch]$AllowMissingArtifacts,
    [switch]$DryRunEvidenceExport,
    [switch]$SkipCourtVisionRun,
    [string]$Notes = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDate = Get-Date -Format "yyyy-MM-dd"
$logTimestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDirectory = Join-Path $repoRoot "data\history\evidence\run_logs\$logDate"
$transcriptPath = Join-Path $logDirectory "courtvision_evidence_daily_$logTimestamp.log"
$transcriptStarted = $false
$exitCode = 0

# This is the repository's documented, narrow Day 0 exception. Ignored local
# evidence, runtime outputs, and transcript logs do not appear in git status.
$allowedLocalOnlyStatusLines = @(
    "?? docs/CODEX_INVESTOR_AUDIT_2026_07_07.md"
)

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory)]
        [string]$Description
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Assert-CleanWorkingTree {
    param(
        [Parameter(Mandatory)]
        [string]$Stage
    )

    $statusLines = @(& git status --short --untracked-files=all)
    Assert-NativeSuccess "git status ($Stage)"
    $disallowed = @($statusLines | Where-Object {
        $_ -notin $allowedLocalOnlyStatusLines
    })

    if ($disallowed.Count -gt 0) {
        throw "Git working tree is not clean at $Stage. Disallowed changes:`n$($disallowed -join "`n")"
    }

    if ($statusLines.Count -gt 0) {
        Write-Host "Allowed local-only file present: docs/CODEX_INVESTOR_AUDIT_2026_07_07.md" -ForegroundColor Yellow
    } else {
        Write-Host "Git working tree is clean ($Stage)." -ForegroundColor Green
    }
}

try {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Start-Transcript -Path $transcriptPath -Append | Out-Null
    $transcriptStarted = $true

    Set-Location $repoRoot
    Write-Host "CourtVision daily evidence workflow" -ForegroundColor Cyan
    Write-Host "Transcript: $transcriptPath"
    Write-Host "Trial: $TrialId | Prediction date: $PredictionDate"

    $currentBranch = (& git branch --show-current).Trim()
    Assert-NativeSuccess "git branch --show-current"
    if ($currentBranch -ne "main") {
        throw "Expected git branch 'main'; current branch is '$currentBranch'."
    }
    Write-Host "Confirmed branch: main" -ForegroundColor Green

    # Check both sides of the fast-forward pull so local work is never hidden by
    # the update and the runtime starts from an auditable checkout.
    Assert-CleanWorkingTree "before pull"
    Write-Host "Pulling latest main..." -ForegroundColor Cyan
    & git pull --ff-only origin main
    Assert-NativeSuccess "git pull --ff-only origin main"
    Assert-CleanWorkingTree "after pull"

    if ($SkipCourtVisionRun) {
        Write-Host "Skipping the canonical CourtVision run; exporting existing dated artifacts." -ForegroundColor Yellow
    } else {
        Write-Host "Running canonical CourtVision command for $PredictionDate..." -ForegroundColor Cyan
        & (Join-Path $repoRoot "run_today.bat") $PredictionDate
        Assert-NativeSuccess "run_today.bat"
    }

    $exportArguments = @(
        (Join-Path $repoRoot "scripts\export_run_to_evidence.py"),
        "--trial-id", $TrialId,
        "--prediction-date", $PredictionDate,
        "--config-hash", $ConfigHash
    )
    if ($RunDate) {
        $exportArguments += @("--run-date", $RunDate)
    }
    if ($Notes) {
        $exportArguments += @("--notes", $Notes)
    }
    if ($AllowMissingArtifacts) {
        $exportArguments += "--allow-missing-artifacts"
    }
    if ($DryRunEvidenceExport) {
        $exportArguments += "--dry-run"
    }

    $exportMode = if ($DryRunEvidenceExport) { "dry-run" } else { "real append" }
    Write-Host "Running evidence export ($exportMode)..." -ForegroundColor Cyan
    & python @exportArguments
    Assert-NativeSuccess "export_run_to_evidence.py"

    Write-Host ""
    Write-Host "SUCCESS: CourtVision daily evidence workflow completed." -ForegroundColor Green
    Write-Host "Evidence export mode: $exportMode"
    Write-Host "Transcript: $transcriptPath"
} catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "FAILURE: CourtVision daily evidence workflow did not complete." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Transcript: $transcriptPath"
} finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    Set-Location $repoRoot
}

exit $exitCode
