[CmdletBinding()]
param(
    [string[]]$Date = @(),
    [int]$LookbackDays = 3,
    [switch]$DryRun,
    [switch]$SkipGit
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $RepoPath ".git") -PathType Container)) {
    throw "Resolved repository root is not a git repository: $RepoPath"
}

$SnapshotPath = Join-Path $RepoPath "data\theoddsapi\live_hr_snapshots"
$LogDirectory = Join-Path $SnapshotPath "automation_logs"
$RunId = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDirectory ("mlb_nightly_pipeline_{0}.log" -f $RunId)

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null

function Get-SecretValues {
    $secretNamePattern = "(API_KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)"
    Get-ChildItem Env: |
        Where-Object {
            $_.Value -and
            $_.Value.Length -ge 4 -and
            $_.Name -match $secretNamePattern
        } |
        ForEach-Object { $_.Value } |
        Sort-Object -Unique
}

$Script:SecretValues = @(Get-SecretValues)

function Mask-Text {
    param([AllowNull()][object]$Value)

    $Text = [string]$Value
    foreach ($Secret in $Script:SecretValues) {
        if ([string]::IsNullOrWhiteSpace($Secret)) {
            continue
        }
        $Text = $Text.Replace($Secret, "[MASKED]")
    }
    $Text = [regex]::Replace($Text, "(?i)(apiKey=)[^\s&]+", '$1[MASKED]')
    $Text = [regex]::Replace($Text, "(?i)(api_key=)[^\s&]+", '$1[MASKED]')
    return $Text
}

function Format-CommandForLog {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    $Parts = @($Executable) + $CommandArguments
    $Formatted = $Parts | ForEach-Object {
        if ($_ -match "\s") {
            '"' + $_ + '"'
        }
        else {
            $_
        }
    }
    return (Mask-Text ($Formatted -join " "))
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    Write-Host ("> " + (Format-CommandForLog -Executable $Executable -CommandArguments $CommandArguments))
    $Output = @(& $Executable @CommandArguments 2>&1)
    $CommandExitCode = $LASTEXITCODE
    foreach ($Line in $Output) {
        Write-Host (Mask-Text $Line)
    }
    if ($CommandExitCode -ne 0) {
        throw "Command failed with exit code ${CommandExitCode}: $(Format-CommandForLog -Executable $Executable -CommandArguments $CommandArguments)"
    }
}

$ExitCode = 0
$TranscriptStarted = $false

try {
    Start-Transcript -Path $LogPath -Append | Out-Null
    $TranscriptStarted = $true

    Write-Host "CourtVision MLB nightly pipeline started at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')."
    Write-Host "Repository root: $RepoPath"
    Write-Host "Log file: $LogPath"
    Write-Host "Run ID: $RunId"
    Write-Host "Dry run: $DryRun"
    Set-Location -LiteralPath $RepoPath

    $PipelineArguments = @(
        ".\tools\courtvision_mlb_nightly_pipeline.py",
        "--run-id",
        $RunId,
        "--lookback-days",
        [string]$LookbackDays
    )

    if ($DryRun) {
        $PipelineArguments += "--dry-run"
    }

    if ($SkipGit) {
        $PipelineArguments += "--skip-git"
    }

    foreach ($TargetDate in $Date) {
        $PipelineArguments += @("--date", $TargetDate)
    }

    Invoke-CheckedCommand -Executable "python" -CommandArguments $PipelineArguments
    Write-Host "CourtVision MLB nightly pipeline completed successfully."
}
catch {
    $ExitCode = 1
    Write-Error "CourtVision MLB nightly pipeline failed: $($_.Exception.Message)" -ErrorAction Continue
}
finally {
    if ($TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
}

exit $ExitCode
