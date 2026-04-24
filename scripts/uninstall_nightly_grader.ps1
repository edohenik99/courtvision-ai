#!/usr/bin/env pwsh
param()

$ErrorActionPreference = "Stop"

$taskName = "CourtVision Nightly Grader"

Write-Host "Removing scheduled task: $taskName" -ForegroundColor Cyan
schtasks /Delete /F /TN "$taskName" | Out-Null

if ($LASTEXITCODE -ne 0) {
    Write-Host "[INFO] Task may not exist or could not be removed." -ForegroundColor Yellow
    exit 0
}

Write-Host "[OK] Removed: $taskName" -ForegroundColor Green

