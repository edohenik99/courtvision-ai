#!/usr/bin/env pwsh
param()

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$taskName = "CourtVision Nightly Grader"
$pythonCmd = "python"
$taskRun = "cmd /c cd /d `"$repoRoot`" && $pythonCmd scripts\nightly_grade_and_refresh.py"

Write-Host "Installing scheduled task: $taskName" -ForegroundColor Cyan
schtasks /Create /F /TN "$taskName" /SC DAILY /ST 02:00 /TR "$taskRun" | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "[FAIL] Unable to install scheduled task." -ForegroundColor Red
    exit 1
}

Write-Host "[OK] Installed: $taskName" -ForegroundColor Green
Write-Host "Run command: $taskRun" -ForegroundColor Gray
Write-Host "Schedule: Daily at 02:00 local time" -ForegroundColor Gray

