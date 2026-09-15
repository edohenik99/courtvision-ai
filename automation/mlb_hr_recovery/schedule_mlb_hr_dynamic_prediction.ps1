[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ConfigurationSha256,
    [string]$LocalOddsCsv,
    [string]$LocalOddsSha256,
    [string]$LocalScheduleJson,
    [string]$LocalScheduleSha256,
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$OperatingDate,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "recovery_common.ps1")
. Initialize-RecoveryRuntime -ConfigurationPath $ConfigurationPath -ConfigurationSha256 $ConfigurationSha256
$evidenceRoot = Join-Path (Join-Path $evidenceRoot $controlId) $ConfigurationSha256
Assert-RecoveryNoReparsePath -Path $evidenceRoot
. (Join-Path $PSScriptRoot "recovery_completion.ps1")

$runner = (Join-Path $automationRoot "run_mlb_hr_dynamic_prediction.ps1")

$logDir = (Join-Path $evidenceRoot "logs")
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$torontoTZ = [System.TimeZoneInfo]::FindSystemTimeZoneById(
    "Eastern Standard Time"
)

$torontoNow = [System.TimeZoneInfo]::ConvertTimeFromUtc(
    [datetime]::UtcNow,
    $torontoTZ
)

if (-not $OperatingDate) {
    $OperatingDate = $torontoNow.ToString("yyyy-MM-dd")
}

$null = [datetime]::ParseExact(
    $OperatingDate,
    "yyyy-MM-dd",
    [System.Globalization.CultureInfo]::InvariantCulture
)

Assert-RecoveryOperatingDate -OperatingDate $OperatingDate

$dayStamp = $OperatingDate.Replace("-", "")

$logFile = Join-Path `
    $logDir `
    "dynamic_scheduler_$dayStamp.log"

function Write-Log {
    param([string]$Message)

    "$(Get-Date -Format o) $Message" |
        Tee-Object -FilePath $logFile -Append
}

function Assert-FrozenRepository {

    $dirty = @(
        git --no-optional-locks `
            -C $repo `
            status --porcelain=v1 --untracked-files=all
    )

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read frozen repository status."
    }

    if ($dirty.Count -ne 0) {
        throw "Frozen repository is dirty."
    }

    $head = (
        git --no-optional-locks `
            -C $repo `
            rev-parse HEAD
    ).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve frozen repository HEAD."
    }

    if ($head -ne $expectedCommit) {
        throw "Unexpected frozen repository commit: $head"
    }

    return $head
}

function Get-OfficialGames {

    $uri = (
        "https://statsapi.mlb.com/api/v1/schedule" +
        "?sportId=1&date=$OperatingDate"
    )

    if ($LocalScheduleJson) {
        $scheduleFile = Get-RecoveryBoundFile -Path $LocalScheduleJson -Sha256 $LocalScheduleSha256
        $schedule = Get-Content -LiteralPath $scheduleFile.FullName -Raw | ConvertFrom-Json
    }
    else {
        if (-not $recoveryConfig.allow_provider_collection) { throw "Provider schedule access is disabled; a bound local schedule is required." }
        $schedule = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 30
    }

    $games = @()

    foreach ($dateEntry in @($schedule.dates)) {

        if ([string]$dateEntry.date -ne $OperatingDate) {
            continue
        }

        foreach ($game in @($dateEntry.games)) {
            if ([string]$game.gameType -cne "R") { continue }

            $detailed =
                [string]$game.status.detailedState

            if (
                $detailed -in @(
                    "Cancelled",
                    "Postponed"
                )
            ) {
                continue
            }

            $games += $game
        }
    }

    return @($games)
}

try {

    Write-Log (
        "START operating_date=$OperatingDate dry_run=$([bool]$DryRun)"
    )

    $head = Assert-FrozenRepository

    Write-Log "GIT_OK commit=$head"

    $games = @(Get-OfficialGames)

    Write-Log "OFFICIAL_SCHEDULE games=$($games.Count)"

    if ($games.Count -eq 0) {
        Write-Log "NO_GAMES operating_date=$OperatingDate"
        exit 0
    }

    $earliestStartUtc = @(
        $games |
            ForEach-Object {
                (
                    [datetimeoffset]::Parse(
                        [string]$_.gameDate
                    )
                ).UtcDateTime
            } |
            Sort-Object
    )[0]

    $nowUtc = [datetime]::UtcNow

    $minutesBeforeEarliest = (
        $earliestStartUtc -
        $nowUtc
    ).TotalMinutes

    Write-Log (
        "EARLIEST_GAME_UTC={0:o} minutes_before={1:N1}" -f `
        $earliestStartUtc,
        $minutesBeforeEarliest
    )

    if ($minutesBeforeEarliest -lt 35) {
        Write-Log "SKIP_TOO_LATE"
        exit 0
    }

    $desiredRunUtc =
        $earliestStartUtc.AddMinutes(-90)

    if ($desiredRunUtc -le $nowUtc.AddMinutes(1)) {
        $runUtc = $nowUtc.AddMinutes(1)
        $mode = "RUN_ASAP"
    }
    else {
        $runUtc = $desiredRunUtc
        $mode = "TARGET_90_MIN"
    }

    $runLocal =
        [System.TimeZoneInfo]::ConvertTimeFromUtc(
            $runUtc,
            [System.TimeZoneInfo]::Local
        )

    $dynamicTaskName =
        "CourtVision MLB HR Dynamic Prediction $dayStamp $controlId"

    $actionArguments = New-RecoveryTaskArguments -Runner $runner -OperatingDate $OperatingDate
    $taskExecutable = $powershellExecutable
    $taskPath = [string][char]92
    $null = Assert-RecoveryNewTask -TaskName $dynamicTaskName -TaskPath $taskPath -Execute $taskExecutable -Arguments $actionArguments -WorkingDirectory $repo
    if ($DryRun) {
        Write-Log (
            "DRY_RUN would_schedule task={0} mode={1} run_local={2} action={3}" -f `
            $dynamicTaskName,
            $mode,
            $runLocal,
            $actionArguments
        )

        exit 0
    }

    if (-not $recoveryConfig.allow_task_registration) { throw "Task registration is disabled in recovery configuration." }

    $action = New-ScheduledTaskAction `
        -Execute $taskExecutable `
        -WorkingDirectory $repo `
        -Argument $actionArguments

    $trigger = New-ScheduledTaskTrigger `
        -Once `
        -At $runLocal

    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 2 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -WakeToRun

    $settings.Enabled = $false

    Register-ScheduledTask `
        -TaskPath $taskPath `
        -TaskName $dynamicTaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Explicit-date fresh MLB HR research prediction snapshot near first pitch." `
        |
        Out-Null

    Write-Log (
        "SCHEDULED operating_date={0} mode={1} run_local={2} first_game_utc={3:o}" -f `
        $OperatingDate,
        $mode,
        $runLocal,
        $earliestStartUtc
    )

    exit 0
}
catch {

    Write-Log "FAILED $($_.Exception.Message)"
    exit 1
}
