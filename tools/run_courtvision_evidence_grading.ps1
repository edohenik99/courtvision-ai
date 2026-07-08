#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ClosingLinesCsv,

    [Parameter(Mandatory)]
    [ValidateNotNullOrEmpty()]
    [string]$ResultsCsv,

    [switch]$DryRun,
    [switch]$AllowUnmatched,
    [switch]$AllowExisting
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDate = Get-Date -Format "yyyy-MM-dd"
$logTimestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDirectory = Join-Path $repoRoot "data\history\evidence\grading_logs\$logDate"
$transcriptPath = Join-Path $logDirectory "courtvision_evidence_grading_$logTimestamp.log"
$transcriptStarted = $false
$exitCode = 0

function Assert-NativeSuccess {
    param(
        [Parameter(Mandatory)]
        [string]$Description
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Add-SharedUpdateArguments {
    param(
        [Parameter(Mandatory)]
        [object[]]$Arguments
    )

    if ($DryRun) {
        $Arguments += "--dry-run"
    }
    if ($AllowUnmatched) {
        $Arguments += "--allow-unmatched"
    }
    if ($AllowExisting) {
        $Arguments += "--allow-existing"
    }
    return $Arguments
}

try {
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    Start-Transcript -Path $transcriptPath -Append | Out-Null
    $transcriptStarted = $true

    Set-Location $repoRoot
    $mode = if ($DryRun) { "dry-run" } else { "real update" }
    Write-Host "CourtVision evidence grading workflow ($mode)" -ForegroundColor Cyan
    Write-Host "Transcript: $transcriptPath"

    $closingArguments = @(
        (Join-Path $repoRoot "scripts\update_evidence_closing_lines.py"),
        "--closing-lines-csv", $ClosingLinesCsv
    )
    $closingArguments = Add-SharedUpdateArguments -Arguments $closingArguments
    Write-Host ""
    Write-Host "Closing-line update counts:" -ForegroundColor Cyan
    & python @closingArguments
    Assert-NativeSuccess "update_evidence_closing_lines.py"

    $resultArguments = @(
        (Join-Path $repoRoot "scripts\update_evidence_results.py"),
        "--results-csv", $ResultsCsv
    )
    $resultArguments = Add-SharedUpdateArguments -Arguments $resultArguments
    Write-Host ""
    Write-Host "Result update counts:" -ForegroundColor Cyan
    & python @resultArguments
    Assert-NativeSuccess "update_evidence_results.py"

    Write-Host ""
    Write-Host "SUCCESS: CourtVision evidence grading workflow completed ($mode)." -ForegroundColor Green
    Write-Host "Transcript: $transcriptPath"
} catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "FAILURE: CourtVision evidence grading workflow did not complete." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "Transcript: $transcriptPath"
} finally {
    if ($transcriptStarted) {
        Stop-Transcript | Out-Null
    }
    Set-Location $repoRoot
}

exit $exitCode
