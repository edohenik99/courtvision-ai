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
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
    [string]$OperatingDate
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "recovery_common.ps1")
. Initialize-RecoveryRuntime -ConfigurationPath $ConfigurationPath -ConfigurationSha256 $ConfigurationSha256
$evidenceRoot = Join-Path (Join-Path $evidenceRoot $controlId) $ConfigurationSha256
Assert-RecoveryNoReparsePath -Path $evidenceRoot
. (Join-Path $PSScriptRoot "recovery_completion.ps1")



$shadowScheduler = Join-Path `
    $automationRoot `
    "schedule_mlb_hr_shadow_cluster_closing.ps1"
$postPublishHelper = Join-Path `
    $automationRoot `
    "post_publish_shadow_scheduler.ps1"

. $postPublishHelper

$logDir = Join-Path $evidenceRoot "logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

$torontoTZ = [System.TimeZoneInfo]::FindSystemTimeZoneById(
    "Eastern Standard Time"
)

$torontoNow = [System.TimeZoneInfo]::ConvertTimeFromUtc(
    [datetime]::UtcNow,
    $torontoTZ
)

Assert-RecoveryOperatingDate -OperatingDate $OperatingDate

$today = $OperatingDate
$dayStamp = $OperatingDate.Replace("-", "")

$logFile = Join-Path `
    $logDir `
    "dynamic_prediction_$dayStamp.log"

function Write-Log {
    param([string]$Message)

    "$(Get-Date -Format o) $Message" |
        Tee-Object -FilePath $logFile -Append
}

try {

    Write-Log "START operating_date=$today"

    Set-Location $repo

    # -------------------------------------------------------
    # 1. Recover supplemental scheduling for an existing completed day
    # -------------------------------------------------------

    $dateDir = Join-Path $controlDir "dates\$today"

    if (Test-Path $dateDir) {

        $existingCandidates = @(Get-ChildItem -LiteralPath $dateDir -Recurse -Filter "predictions.csv" -File -ErrorAction Stop)
        if ($existingCandidates.Count -gt 1) { throw "Ambiguous prediction publication; refusing arbitrary replay selection." }
        $existingPrediction = if ($existingCandidates.Count -eq 1) { $existingCandidates[0] } else { $null }
        if ($existingPrediction) {
            $null = Assert-RecoveryPublishedEvidence -PredictionsCsv $existingPrediction.FullName -OperatingDate $OperatingDate
            $existingRunDir = $existingPrediction.Directory.FullName
            $existingManifestPath = Join-Path `
                $existingRunDir `
                "prediction_manifest_v1.json"
            $existingManifest = Get-Content `
                -LiteralPath $existingManifestPath `
                -Raw |
                ConvertFrom-Json
            $existingManifestDigest = (
                Get-FileHash `
                    -LiteralPath $existingManifestPath `
                    -Algorithm SHA256
            ).Hash.ToLowerInvariant()
            $existingRunPath = "controls/$controlId/dates/$today/$($existingManifest.prediction_run_id)"
            $replayPublication = [pscustomobject]@{
                success = $true
                status = "completed"
                control_id = [string]$existingManifest.control_id
                operating_date = $today
                prediction_count = [int]$existingManifest.prediction_count
                ledger_rows_appended = 0
                prediction_run_id = [string]$existingManifest.prediction_run_id
                run_path = $existingRunPath
                prediction_manifest_digest = $existingManifestDigest
                replayed_existing_run = $true
            }

            $replayValidation = Get-ShadowSchedulerPublicationValidation -Publication $replayPublication -OperatingDate $today
            if (-not $replayValidation.IsValid) { throw "Existing publication contract is invalid: $($replayValidation.Reason)" }

            Write-Log (
                "REPLAY already_published={0} run_id={1} predictions={2}" -f `
                $existingPrediction.FullName,
                $replayPublication.prediction_run_id,
                $replayPublication.prediction_count
            )
            if ($recoveryConfig.supplemental_shadow_enabled) {
            $null = Invoke-ShadowSchedulerAfterPublication `
                -Publication $replayPublication `
                -OperatingDate $today `
                -RepositoryRoot $repo `
                -ExpectedCommit $expectedCommit `
                -SchedulerPath $shadowScheduler `
                -Logger {
                    param([string]$Message)
                    Write-Log $Message
                }
            }
            exit 0
        }
    }

    # -------------------------------------------------------
    # 2. Frozen Git preflight
    # -------------------------------------------------------

    $dirty = @(git status --porcelain)

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Git working-tree status."
    }

    if ($dirty.Count -gt 0) {
        throw "Repository is dirty. Prediction refused."
    }

    $head = (git rev-parse HEAD).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve Git HEAD."
    }

    if ($head -ne $expectedCommit) {
        throw "Unexpected Git commit: $head"
    }

    Write-Log "GIT_OK commit=$head"

    # -------------------------------------------------------
    # 3. Resolve official MLB operating-date slate
    #
    # Scheduling is no longer dependent on a paid morning
    # Odds API snapshot.  The free MLB schedule is authoritative
    # for the expected number and start-time multiset of games.
    # -------------------------------------------------------

    $scheduleUri = (
        "https://statsapi.mlb.com/api/v1/schedule" +
        "?sportId=1&date=$OperatingDate"
    )

    if ($LocalScheduleJson) {
        $scheduleFile = Get-RecoveryBoundFile -Path $LocalScheduleJson -Sha256 $LocalScheduleSha256
        $schedule = Get-Content -LiteralPath $scheduleFile.FullName -Raw | ConvertFrom-Json
    }
    else {
        if (-not $recoveryConfig.allow_provider_collection) { throw "Provider schedule access is disabled; a bound local schedule is required." }
        $schedule = Invoke-RestMethod -Uri $scheduleUri -Method Get -TimeoutSec 30
    }

    $officialGames = @()

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

            $officialGames += $game
        }
    }

    if ($officialGames.Count -eq 0) {
        throw "Official MLB schedule contains no eligible games for $OperatingDate."
    }

    $officialStartKeys = @(
        $officialGames |
            ForEach-Object {
                (
                    [datetimeoffset]::Parse(
                        [string]$_.gameDate
                    )
                ).UtcDateTime.ToString(
                    "yyyy-MM-ddTHH:mm"
                )
            } |
            Sort-Object
    )

    $earliestStartUtc = @(
        $officialGames |
            ForEach-Object {
                (
                    [datetimeoffset]::Parse(
                        [string]$_.gameDate
                    )
                ).UtcDateTime
            } |
            Sort-Object
    )[0]

    $minutesBeforeEarliest = (
        $earliestStartUtc -
        [datetime]::UtcNow
    ).TotalMinutes

    Write-Log (
        "OFFICIAL_SCHEDULE games={0} earliest_utc={1:o} minutes_before={2:N1}" -f `
        $officialGames.Count,
        $earliestStartUtc,
        $minutesBeforeEarliest
    )

    if ($minutesBeforeEarliest -lt 35) {
        Write-Log "SKIP_TOO_LATE under_35_minutes_before_first_game"
        exit 0
    }

    # -------------------------------------------------------
    # 4. Record current snapshots before fresh collection
    # -------------------------------------------------------

    $beforeSnapshots = @(
        Get-ChildItem `
            -Path $snapshotDir `
            -Filter "live_hr_props_${dayStamp}_*.csv" `
            -File `
            -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    )

    # -------------------------------------------------------
    # 5. Take a FRESH prediction-time odds snapshot
    # -------------------------------------------------------

    if ($LocalOddsCsv) {
        $freshSnapshot = Get-RecoveryBoundFile -Path $LocalOddsCsv -Sha256 $LocalOddsSha256
    }
    else {
        if (-not $recoveryConfig.allow_provider_collection) { throw "Odds API collection is disabled; a bound local odds file is required." }
    Write-Log "COLLECTOR_START"

    $collectorText = (
        & $pythonExecutable -B `
            .\tools\theoddsapi_live_hr_collector.py `
            --operating-date $OperatingDate `
            --max-events 20 `
            --quiet `
            --force 2>&1 |
        Out-String
    ).Trim()

    $collectorExit = $LASTEXITCODE

    Write-Log "COLLECTOR $collectorText"

    if ($collectorExit -ne 0) {
        throw "Odds collector failed with exit code $collectorExit."
    }


        $newSnapshots = @(Get-ChildItem -LiteralPath $snapshotDir -Filter "live_hr_props_${dayStamp}_*.csv" -File -ErrorAction Stop | Where-Object { $beforeSnapshots -notcontains $_.FullName })
        if ($newSnapshots.Count -ne 1) { throw "Collector must produce exactly one new immutable snapshot; ambiguous or missing source." }
        $freshSnapshot = $newSnapshots[0]
    }
    if (-not $freshSnapshot) {
        throw "Collector completed but no new standalone snapshot was found."
    }

    $freshSnapshot = New-RecoveryEnrichedOdds -OddsPath $freshSnapshot.FullName -Schedule $schedule

    Write-Log "FRESH_SNAPSHOT $($freshSnapshot.FullName)"

    $freshRows = @(Import-Csv $freshSnapshot.FullName)

    if ($freshRows.Count -eq 0) {
        throw "Fresh snapshot contains no rows."
    }

    # -------------------------------------------------------
    # 7. Fail closed unless provider slate matches official
    #    MLB schedule at the event/start-time level.
    # -------------------------------------------------------

    $providerEventRows = @(
        $freshRows |
            Group-Object event_id |
            ForEach-Object {
                $_.Group |
                    Select-Object -First 1
            }
    )

    $providerStartKeys = @(
        $providerEventRows |
            ForEach-Object {
                (
                    [datetimeoffset]::Parse(
                        [string]$_.commence_time
                    )
                ).UtcDateTime.ToString(
                    "yyyy-MM-ddTHH:mm"
                )
            } |
            Sort-Object
    )

    if (
        $providerEventRows.Count -ne
        $officialGames.Count
    ) {
        throw (
            "Fresh provider slate does not match official MLB game count. " +
            "Official=$($officialGames.Count) " +
            "Provider=$($providerEventRows.Count)"
        )
    }

    $officialStartGroups = @(
        $officialStartKeys |
            Group-Object |
            Sort-Object Name |
            ForEach-Object {
                "$($_.Name)|$($_.Count)"
            }
    )

    $providerStartGroups = @(
        $providerStartKeys |
            Group-Object |
            Sort-Object Name |
            ForEach-Object {
                "$($_.Name)|$($_.Count)"
            }
    )

    $startDifferences = @(
        Compare-Object `
            -ReferenceObject $officialStartGroups `
            -DifferenceObject $providerStartGroups
    )

    if ($startDifferences.Count -ne 0) {
        throw (
            "Fresh provider slate start-time multiset does not match " +
            "the official MLB schedule."
        )
    }

    Write-Log (
        "FRESH_SNAPSHOT_OFFICIAL_SCHEDULE_OK rows={0} events={1}" -f `
        $freshRows.Count,
        $providerEventRows.Count
    )

    # -------------------------------------------------------
    # 8. Dry run
    # -------------------------------------------------------

    $dryText = (
        & $pythonExecutable -B `
            -m courtvision.sports.mlb.training.hr_research_baseline `
            run-prospective-paper-day `
            --date $today `
            --control-dir $controlDir `
            --odds-csv $freshSnapshot.FullName `
            --trial-root $trialRoot `
            --repository-root $repo `
            --dry-run |
        Out-String
    ).Trim()

    $dryExit = $LASTEXITCODE

    Write-Log "DRY_RUN $dryText"

    if ($dryExit -ne 0) {
        throw "Dry run failed with exit code $dryExit."
    }

    $dry = $dryText | ConvertFrom-Json

    if (-not $dry.success) {
        throw "Dry run returned success=false."
    }

    if ([int]$dry.prediction_count -le 0) {
        Write-Log "NO_PREDICTIONS"
        exit 0
    }

    Write-Log (
        "DRY_RUN_OK predictions={0} exclusions={1}" -f `
        $dry.prediction_count,
        $dry.exclusion_count
    )

    # -------------------------------------------------------
    # 9. Publish frozen prospective predictions
    # -------------------------------------------------------

    $publishText = (
        & $pythonExecutable -B `
            -m courtvision.sports.mlb.training.hr_research_baseline `
            run-prospective-paper-day `
            --date $today `
            --control-dir $controlDir `
            --odds-csv $freshSnapshot.FullName `
            --trial-root $trialRoot `
            --repository-root $repo |
        Out-String
    ).Trim()

    $publishExit = $LASTEXITCODE

    Write-Log "PUBLISH $publishText"

    if ($publishExit -ne 0) {
        throw "Publication failed with exit code $publishExit."
    }

    $published = $publishText | ConvertFrom-Json

    if (-not $published.success) {
        throw "Publication returned success=false."
    }

    $publicationValidation = Get-ShadowSchedulerPublicationValidation `
        -Publication $published `
        -OperatingDate $today

    if (-not $publicationValidation.IsValid) {
        throw "Publication contract invalid: $($publicationValidation.Reason)"
    }

    Write-Log (
        "SUCCESS run_id={0} predictions={1} ledger_rows={2}" -f `
        $published.prediction_run_id,
        $published.prediction_count,
        $published.ledger_rows_appended
    )

    if ($recoveryConfig.supplemental_shadow_enabled) {
    try {
        $null = Invoke-ShadowSchedulerAfterPublication `
            -Publication $published `
            -OperatingDate $today `
            -RepositoryRoot $repo `
            -ExpectedCommit $expectedCommit `
            -SchedulerPath $shadowScheduler `
            -Logger {
                param([string]$Message)
                Write-Log $Message
            }
    }
    catch {
        Write-Log (
            "SHADOW_SCHEDULER_FAILED operating_date={0} error={1}" -f `
            $today,
            $_.Exception.Message
        )
    }
    }

    exit 0
}
catch {

    Write-Log "FAILED $($_.Exception.Message)"
    exit 1
}
