$ErrorActionPreference = "Stop"

$RepoPath = "C:\dev\Sport_Project1"
$SnapshotPath = Join-Path $RepoPath "data\theoddsapi\live_hr_snapshots"
$LogDirectory = Join-Path $SnapshotPath "final_automation_logs"
$TargetDate = (Get-Date).AddDays(-1).ToString("yyyy-MM-dd")

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogPath = Join-Path $LogDirectory ("live_hr_final_auto_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    Write-Host "> $Executable $($CommandArguments -join ' ')"
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($CommandArguments -join ' ')"
    }
}

function Invoke-CapturedCheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    Write-Host "> $Executable $($CommandArguments -join ' ')"
    $Output = @(& $Executable @CommandArguments 2>&1)
    $CommandExitCode = $LASTEXITCODE
    $Output | ForEach-Object { Write-Host $_ }

    if ($CommandExitCode -ne 0) {
        throw "Command failed with exit code ${CommandExitCode}: $Executable $($CommandArguments -join ' ')"
    }

    return ($Output -join [Environment]::NewLine)
}

$ExitCode = 0
Start-Transcript -Path $LogPath -Append | Out-Null

try {
    Write-Host "CourtVision MLB Live HR final automation started at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')."
    Write-Host "Log file: $LogPath"
    Write-Host "Target date: $TargetDate"
    Set-Location -LiteralPath $RepoPath

    Invoke-CheckedCommand -Executable "git" -CommandArguments @("checkout", "main")
    Invoke-CheckedCommand -Executable "git" -CommandArguments @("pull", "origin", "main")
    Invoke-CheckedCommand -Executable "python" -CommandArguments @(".\tools\run_live_hr_daily_check.py")
    Invoke-CheckedCommand -Executable "python" -CommandArguments @(
        ".\tools\generate_live_hr_results_workbook.py",
        "--overwrite",
        "--preserve-results"
    )
    Invoke-CheckedCommand -Executable "python" -CommandArguments @(
        ".\tools\fill_live_hr_results_from_mlb_statsapi.py",
        "--date",
        $TargetDate
    )
    Invoke-CheckedCommand -Executable "python" -CommandArguments @(
        ".\tools\export_live_hr_results_from_workbook.py",
        "--overwrite"
    )

    $CoverageOutput = Invoke-CapturedCheckedCommand `
        -Executable "python" `
        -CommandArguments @(
            ".\tools\check_live_hr_results_coverage.py",
            "--date",
            $TargetDate
        )

    if ($CoverageOutput -match "Ready to grade:\s*YES") {
        Invoke-CheckedCommand -Executable "python" -CommandArguments @(
            ".\tools\grade_live_hr_results.py",
            "--date",
            $TargetDate
        )
        Invoke-CheckedCommand -Executable "python" -CommandArguments @(
            ".\tools\summarize_live_hr_grades.py",
            "--date",
            $TargetDate
        )
        Write-Host "CourtVision MLB Live HR final automation completed with grading and summary."
    }
    elseif ($CoverageOutput -match "Ready to grade:\s*NO") {
        Write-Host "Results incomplete; skipping grader."
        Write-Host "Grade summary skipped because grading was skipped."
        Write-Host "CourtVision MLB Live HR final automation completed successfully."
    }
    else {
        throw "Coverage checker did not report a recognized Ready to grade status."
    }
}
catch {
    $ExitCode = 1
    Write-Error "CourtVision MLB Live HR final automation failed: $($_.Exception.Message)" -ErrorAction Continue
}
finally {
    Stop-Transcript | Out-Null
}

exit $ExitCode
