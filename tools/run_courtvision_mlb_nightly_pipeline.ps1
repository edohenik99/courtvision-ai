[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^\d{4}-\d{2}-\d{2}$")]
    [string]$Date,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{40}$")]
    [string]$ExpectedCommit,

    [Parameter(Mandatory = $true)]
    [string]$AuthorizationReceipt,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^cvfa-v1-[0-9a-f]{64}$")]
    [string]$AuthorizationId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^mlb-hr-control-v1-[0-9a-f]{20}$")]
    [string]$ControlId,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionReceiptPath,

    [Parameter(Mandatory = $true)]
    [string]$PythonExecutable,

    [int]$LookbackDays = 3,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not [IO.Path]::IsPathRooted($PythonExecutable) -or
    -not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "An existing absolute PythonExecutable path is required."
}
$PythonExecutable = (Resolve-Path -LiteralPath $PythonExecutable).Path

$RepoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath (Join-Path $RepoPath ".git"))) {
    throw "Resolved repository root is not a git repository: $RepoPath"
}

$ContractTool = Join-Path $RepoPath "tools\courtvision_pinned_finalizer_contract.py"
$ValidationOutput = @(
    & $PythonExecutable -B $ContractTool validate-claimed `
        --authorization-receipt $AuthorizationReceipt `
        --authorization-id $AuthorizationId `
        --expected-commit $ExpectedCommit `
        --operating-date $Date `
        --control-id $ControlId 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw "Pinned finalizer authorization rejected before runtime mutation: $($ValidationOutput -join ' ')"
}
$Authorization = ($ValidationOutput -join "`n") | ConvertFrom-Json
if (-not $Authorization.success -or
    [IO.Path]::GetFullPath([string]$Authorization.payload.source_root) -ne $RepoPath) {
    throw "Authorization source root does not match the executing wrapper."
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
    $PriorErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $Output = @(& $Executable @CommandArguments 2>&1)
        $CommandExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $PriorErrorActionPreference
    }
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
        "-B",
        ".\tools\courtvision_mlb_nightly_pipeline.py",
        "--run-id",
        $RunId,
        "--lookback-days",
        [string]$LookbackDays
    )

    if ($DryRun) {
        $PipelineArguments += "--dry-run"
    }

    $PipelineArguments += @(
        "--date", $Date,
        "--expected-commit", $ExpectedCommit,
        "--authorization-receipt", $AuthorizationReceipt,
        "--authorization-id", $AuthorizationId,
        "--operating-date", $Date,
        "--control-id", $ControlId
    )

    Invoke-CheckedCommand -Executable $PythonExecutable -CommandArguments $PipelineArguments

    if (-not $DryRun) {
        Invoke-CheckedCommand -Executable $PythonExecutable -CommandArguments @(
            "-B",
            $ContractTool,
            "write-execution",
            "--authorization-receipt", $AuthorizationReceipt,
            "--authorization-id", $AuthorizationId,
            "--expected-commit", $ExpectedCommit,
            "--operating-date", $Date,
            "--control-id", $ControlId,
            "--results-csv", (Join-Path $SnapshotPath "live_hr_results.csv"),
            "--output-path", $ExecutionReceiptPath
        )
    }
    else {
        Write-Host "Dry-run output cannot authorize settlement; no execution receipt written."
    }
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
