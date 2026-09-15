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





$logDir = (Join-Path $evidenceRoot "logs")
$stateDir = (Join-Path $evidenceRoot "state")

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

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
    "dynamic_closing_$dayStamp.log"

$doneMarker = Join-Path `
    $stateDir `
    "closing_$dayStamp.done"

function Write-Log {
    param([string]$Message)

    "$(Get-Date -Format o) $Message" |
        Tee-Object `
            -FilePath $logFile `
            -Append
}

try {

    Write-Log "START operating_date=$today"

    # -------------------------------------------------------
    # 1. Never deliberately capture closing twice
    # -------------------------------------------------------

    if (Test-Path -LiteralPath $doneMarker) {
        $null = Read-RecoveryCompletionMarker -Path $doneMarker -OperatingDate $OperatingDate -Kind closing
        Write-Log "SKIP already_completed marker=$doneMarker"
        exit 0
    }

    Set-Location $repo

    # -------------------------------------------------------
    # 2. Frozen Git preflight
    # -------------------------------------------------------

    $dirty = @(git status --porcelain)

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Git status."
    }

    if ($dirty.Count -gt 0) {
        throw "Repository is dirty. Closing capture refused."
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
    # 3. Find today's one committed nonzero prediction file
    # -------------------------------------------------------

    $dateDir = Join-Path `
        $controlDir `
        "dates\$today"

    if (-not (Test-Path $dateDir)) {
        throw "Today's prospective prediction directory does not exist."
    }

    $predictionCandidates = @(
        Get-ChildItem `
            -Path $dateDir `
            -Recurse `
            -Filter "predictions.csv" `
            -File `
            -ErrorAction SilentlyContinue |
        ForEach-Object {

            try {

                $rows = @(Import-Csv $_.FullName)

                if ($rows.Count -gt 0) {
                    $_
                }

            }
            catch {
                # Ignore unreadable candidate here;
                # ambiguity checks below fail closed.
            }
        }
    )

    if ($predictionCandidates.Count -eq 0) {
        throw "No committed nonzero predictions.csv exists for today."
    }

    if ($predictionCandidates.Count -gt 1) {
        throw "More than one nonzero prediction artifact exists for today. Closing capture refused."
    }

    $predictionsCsv = $predictionCandidates[0].FullName

    $null = Assert-RecoveryPublishedEvidence -PredictionsCsv $predictionsCsv -OperatingDate $OperatingDate

    Write-Log "PREDICTIONS $predictionsCsv"

    $predictionRows = @(Import-Csv $predictionsCsv)

    # -------------------------------------------------------
    # 4. Identify earliest predicted event
    # -------------------------------------------------------

    $earliestPrediction = $predictionRows |
        Sort-Object {
            ([datetimeoffset]::Parse($_.commence_time_utc)).UtcDateTime
        } |
        Select-Object -First 1

    $earliestStartUtc = (
        [datetimeoffset]::Parse(
            $earliestPrediction.commence_time_utc
        )
    ).UtcDateTime

    $earliestEventId = $earliestPrediction.event_id

    $minutesBeforeEarliest = (
        $earliestStartUtc - [datetime]::UtcNow
    ).TotalMinutes

    Write-Log (
        "EARLIEST event={0} game_utc={1:o} minutes_before={2:N1}" -f `
        $earliestEventId,
        $earliestStartUtc,
        $minutesBeforeEarliest
    )

    # Collector itself excludes games under 30 minutes.
    # Give ourselves additional safety margin.
    if ($minutesBeforeEarliest -lt 35) {
        throw "Too late for valid full-slate closing collection."
    }

    # -------------------------------------------------------
    # 5. Record snapshots before fresh collection
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
    # 6. Fresh closing-time Odds API snapshot
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
        throw "Closing odds collector failed with exit code $collectorExit."
    }


        $newSnapshots = @(Get-ChildItem -LiteralPath $snapshotDir -Filter "live_hr_props_${dayStamp}_*.csv" -File -ErrorAction Stop | Where-Object { $beforeSnapshots -notcontains $_.FullName })
        if ($newSnapshots.Count -ne 1) { throw "Collector must produce exactly one new immutable snapshot; ambiguous or missing source." }
        $freshSnapshot = $newSnapshots[0]
    }
    if (-not $freshSnapshot) {
        throw "Collector completed but no new standalone closing snapshot was found."
    }

    Write-Log "FRESH_CLOSING_SNAPSHOT $($freshSnapshot.FullName)"

    $freshRows = @(Import-Csv $freshSnapshot.FullName)

    if ($freshRows.Count -eq 0) {
        throw "Fresh closing snapshot is empty."
    }

    # -------------------------------------------------------
    # 8. Require every published prediction event to remain
    #    represented in the closing-time provider snapshot.
    # -------------------------------------------------------

    $expectedEventIds = @(
        $predictionRows |
            ForEach-Object {
                [string]$_.event_id
            } |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_)
            } |
            Sort-Object -Unique
    )

    $closingEventIds = @(
        $freshRows |
            ForEach-Object {
                [string]$_.event_id
            } |
            Where-Object {
                -not [string]::IsNullOrWhiteSpace($_)
            } |
            Sort-Object -Unique
    )

    $missingEventIds = @(
        $expectedEventIds |
            Where-Object {
                $_ -notin $closingEventIds
            }
    )

    if ($missingEventIds.Count -ne 0) {
        throw (
            "Fresh closing snapshot omitted published events: " +
            ($missingEventIds -join ",")
        )
    }

    Write-Log (
        "FRESH_CLOSING_OK rows={0} expected_events={1} missing_events=0" -f `
        $freshRows.Count,
        $expectedEventIds.Count
    )

    # -------------------------------------------------------
    # 9. Capture prospective closing evidence ONCE
    # -------------------------------------------------------

    $captureText = (
        & $pythonExecutable -B `
            -m courtvision.sports.mlb.training.hr_research_baseline `
            capture-prospective-closing `
            --control-dir $controlDir `
            --predictions-csv $predictionsCsv `
            --odds-csv $freshSnapshot.FullName `
            --trial-root $trialRoot |
        Out-String
    ).Trim()

    $captureExit = $LASTEXITCODE

    Write-Log "CAPTURE $captureText"

    if ($captureExit -ne 0) {
        throw "Closing capture failed with exit code $captureExit."
    }

    $capture = $captureText | ConvertFrom-Json

    if (-not $capture.success) {
        throw "Closing capture returned success=false."
    }

    # -------------------------------------------------------
    # 10. Write external completion marker
    # -------------------------------------------------------

    Write-RecoveryCompletionMarker -Path $doneMarker -OperatingDate $OperatingDate -Kind closing -Details @{
        predictions_csv = $predictionsCsv
        closing_snapshot = $freshSnapshot.FullName
        captured_at = (Get-Date -Format o)
        result = $capture
    }
    Write-Log (
        "SUCCESS examined={0} appended={1} same_book={2} consensus={3} missing={4}" -f `
        $capture.predictions_examined,
        $capture.closing_rows_appended,
        $capture.same_book_count,
        $capture.consensus_count,
        $capture.missing_count
    )

    exit 0
}
catch {

    Write-Log "FAILED $($_.Exception.Message)"
    exit 1
}
