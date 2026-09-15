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
    [string[]]$Date = @(),

    [ValidateRange(1,24)]
    [int]$BufferHours = 4,

    [ValidateRange(0,7)]
    [int]$LookbackDays = 1,

    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "recovery_common.ps1")
. Initialize-RecoveryRuntime -ConfigurationPath $ConfigurationPath -ConfigurationSha256 $ConfigurationSha256
$evidenceRoot = Join-Path (Join-Path $evidenceRoot $controlId) $ConfigurationSha256
Assert-RecoveryNoReparsePath -Path $evidenceRoot
. (Join-Path $PSScriptRoot "recovery_completion.ps1")



$finalizerScript = Join-Path `
    $automationRoot `
    "run_mlb_hr_pinned_finalizer.ps1"

$authorizationTool = Join-Path `
    $repo `
    "tools\courtvision_pinned_finalizer_contract.py"

$authorizationRoot = Join-Path `
    $evidenceRoot `
    "state\finalizer_authorizations"

$postFinalizerScript = Join-Path `
    $automationRoot `
    "run_mlb_hr_postfinalizer_v2.ps1"

$stateRoot = Join-Path `
    $evidenceRoot `
    "state\nightly_controller"

$logRoot = Join-Path `
    $evidenceRoot `
    "logs"

if (-not $DryRun) {
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
}

$torontoTZ = [System.TimeZoneInfo]::FindSystemTimeZoneById(
    "Eastern Standard Time"
)

$torontoNow = [System.TimeZoneInfo]::ConvertTimeFromUtc(
    [datetime]::UtcNow,
    $torontoTZ
)

$runStamp = $torontoNow.ToString("yyyyMMdd_HHmmss")
$controllerRunId = "cvctr-v1-$([guid]::NewGuid().ToString('N'))"

$logFile = Join-Path `
    $logRoot `
    "nightly_controller_$runStamp.log"

function Write-Log {
    param([string]$Message)

    $line = "$(Get-Date -Format o) $Message"

    Write-Host $line

    if (-not $DryRun) {
        $line |
            Add-Content `
                -LiteralPath $logFile `
                -Encoding UTF8
    }
}

function Write-JsonAtomic {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $temporary = "$Path.tmp.$PID"

    try {
        $Value |
            ConvertTo-Json -Depth 10 |
            Set-Content `
                -LiteralPath $temporary `
                -Encoding UTF8

        Move-Item `
            -LiteralPath $temporary `
            -Destination $Path `
            -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item `
                -LiteralPath $temporary `
                -Force `
                -ErrorAction SilentlyContinue
        }
    }
}

function Read-State {
    param([string]$Path)

    if (-not (
        Test-Path `
            -LiteralPath $Path `
            -PathType Leaf
    )) {
        return $null
    }

    return (
        Get-Content `
            -LiteralPath $Path `
            -Raw |
        ConvertFrom-Json
    )
}

function Get-TargetDates {

    if ($Date.Count -gt 0) {

        $validated = @()

        foreach ($value in $Date) {

            try {
                $parsed = [datetime]::ParseExact(
                    $value,
                    "yyyy-MM-dd",
                    [System.Globalization.CultureInfo]::InvariantCulture
                )
            }
            catch {
                throw "Invalid -Date value: $value"
            }

            $validated += $parsed.ToString("yyyy-MM-dd")
        }

        return @(
            $validated |
                Sort-Object -Unique
        )
    }

    $dates = @()

    for ($offset = 0; $offset -le $LookbackDays; $offset++) {
        $dates += $torontoNow.Date.
            AddDays(-$offset).
            ToString("yyyy-MM-dd")
    }

    return @(
        $dates |
            Sort-Object
    )
}

function Get-MLBSchedule {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OperatingDate
    )

    $uri = (
        "https://statsapi.mlb.com/api/v1/schedule" +
        "?sportId=1&date=$OperatingDate"
    )

    try {
    if ($LocalScheduleJson) {
        $scheduleFile = Get-RecoveryBoundFile -Path $LocalScheduleJson -Sha256 $LocalScheduleSha256
        $response = Get-Content -LiteralPath $scheduleFile.FullName -Raw | ConvertFrom-Json
    }
    else {
        if (-not $recoveryConfig.allow_provider_collection) { throw "Provider schedule access is disabled; a bound local schedule is required." }
        $response = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 30
    }
    }
    catch {
        throw (
            "MLB StatsAPI request failed for " +
            "${OperatingDate}: $($_.Exception.Message)"
        )
    }

    $games = @()

    foreach ($dateEntry in @($response.dates)) {

        if ([string]$dateEntry.date -ne $OperatingDate) {
            continue
        }

        $games += @($dateEntry.games)
    }

    return @($games)
}

function Get-GameDisposition {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Game
    )

    $abstract = [string]$Game.status.abstractGameState
    $detailed = [string]$Game.status.detailedState
    $coded = [string]$Game.status.codedGameState

    if (
        $abstract.Equals(
            "Final",
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        return [PSCustomObject]@{
            Terminal = $true
            Kind     = "final"
            Abstract = $abstract
            Detailed = $detailed
            Coded    = $coded
        }
    }

    if (
        $detailed -in @(
            "Postponed",
            "Cancelled",
            "Canceled"
        )
    ) {
        return [PSCustomObject]@{
            Terminal = $true
            Kind     = "no_play_terminal"
            Abstract = $abstract
            Detailed = $detailed
            Coded    = $coded
        }
    }

    return [PSCustomObject]@{
        Terminal = $false
        Kind     = "not_terminal"
        Abstract = $abstract
        Detailed = $detailed
        Coded    = $coded
    }
}

function Assert-Repository {

    Set-Location -LiteralPath $repo

    $dirty = @(git status --porcelain)

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Git status."
    }

    if ($dirty.Count -ne 0) {
        throw "Repository is dirty."
    }

    $head = (git rev-parse HEAD).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve Git HEAD."
    }

    if ($head -ne $expectedCommit) {
        throw "Unexpected Git commit: $head"
    }

    return $head
}

function Test-FinalizerMarker {
    param(
        [string]$Path,
        [string]$OperatingDate
    )

    if (-not (
        Test-Path `
            -LiteralPath $Path `
            -PathType Leaf
    )) {
        return $false
    }

    try {
        $marker = Read-RecoveryCompletionMarker -Path $Path -OperatingDate $OperatingDate -Kind finalizer
    }
    catch {
        return $false
    }

    return (
        [string]$marker.schema_version -eq "mlb-hr-recovery-completion-v1" -and
        [string]$marker.configuration_sha256 -ceq $ConfigurationSha256 -and
        [string]$marker.control_id -ceq $controlId -and
        [string]$marker.operating_date -eq $OperatingDate -and
        [bool]$marker.success -eq $true -and
        [int]$marker.exit_code -eq 0 -and
        [string]$marker.repository_commit -eq $expectedCommit -and
        [string]$marker.authorization_id -match "^cvfa-v1-[0-9a-f]{64}$" -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$marker.authorization_receipt
        ) -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$marker.execution_receipt
        )
    )
}


function New-ProspectiveEvidenceResult {
    param(
        [Parameter(Mandatory = $true)]
        [bool]$Valid,

        [Parameter(Mandatory = $true)]
        [string]$Reason,

        [string]$ManifestPath = "",
        [string]$PredictionsPath = "",
        [string]$PredictionRunId = "",
        [int]$PredictionCount = 0,
        [string]$ManifestSha256 = "",
        [string]$PredictionsSha256 = ""
    )

    return [PSCustomObject]@{
        Valid             = $Valid
        Reason            = $Reason
        ManifestPath      = $ManifestPath
        PredictionsPath   = $PredictionsPath
        PredictionRunId   = $PredictionRunId
        PredictionCount   = $PredictionCount
        ManifestSha256    = $ManifestSha256
        PredictionsSha256 = $PredictionsSha256
    }
}

function Test-ProspectiveEvidence {
    param(
        [Parameter(Mandatory = $true)]
        [ValidatePattern("^\d{4}-\d{2}-\d{2}$")]
        [string]$OperatingDate
    )

    $controlDir = Join-Path $trialRoot "controls\$controlId"

    $controlManifestPath = Join-Path `
        $controlDir `
        "control_manifest_v1.json"

    if (-not (
        Test-Path `
            -LiteralPath $controlManifestPath `
            -PathType Leaf
    )) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "control_manifest_missing"
        )
    }

    $observedControlManifestSha = (
        Get-FileHash `
            -LiteralPath $controlManifestPath `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    if (
        $observedControlManifestSha -ne
        $expectedControlManifestSha256
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "control_manifest_hash_mismatch"
        )
    }

    $dateDir = Join-Path `
        $controlDir `
        "dates\$OperatingDate"

    if (-not (
        Test-Path `
            -LiteralPath $dateDir `
            -PathType Container
    )) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "date_directory_missing"
        )
    }

    try {
        $manifests = @(
            Get-ChildItem `
                -LiteralPath $dateDir `
                -Recurse `
                -Filter "prediction_manifest_v1.json" `
                -File `
                -ErrorAction Stop
        )
    }
    catch {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "manifest_discovery_failed"
        )
    }

    if ($manifests.Count -ne 1) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason (
                    "manifest_count_{0}" -f
                    $manifests.Count
                )
        )
    }

    $manifestFile = $manifests[0]

    $runDir = $manifestFile.Directory.FullName

    $runParent = (
        Split-Path `
            -Path $runDir `
            -Parent
    )

    $expectedDatePath = (
        [System.IO.Path]::GetFullPath($dateDir)
    ).TrimEnd("\")

    $actualParentPath = (
        [System.IO.Path]::GetFullPath($runParent)
    ).TrimEnd("\")

    if (-not [string]::Equals(
        $expectedDatePath,
        $actualParentPath,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "manifest_not_direct_child"
        )
    }

    try {
        $manifest = (
            Get-Content `
                -LiteralPath $manifestFile.FullName `
                -Raw `
                -ErrorAction Stop |
            ConvertFrom-Json `
                -ErrorAction Stop
        )
    }
    catch {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "manifest_json_invalid"
        )
    }

    if (
        [string]$manifest.schema_version -cne
        "mlb-hr-prospective-prediction-manifest-v1"
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "schema_version_mismatch"
        )
    }

    if (
        [string]$manifest.operating_date -cne
        $OperatingDate
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "operating_date_mismatch"
        )
    }

    if (
        [string]$manifest.control_id -cne
        $controlId
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "control_id_mismatch"
        )
    }

    if (-not [bool]$manifest.research_only) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "research_only_not_true"
        )
    }

    $propertyNames = @(
        $manifest.PSObject.Properties.Name
    )

    if (
        $propertyNames -notcontains
        "eligible_for_betting"
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "betting_eligibility_missing"
        )
    }

    if ([bool]$manifest.eligible_for_betting) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "betting_eligibility_true"
        )
    }

    if (
        $propertyNames -notcontains
        "eligible_for_official_pick"
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "official_pick_eligibility_missing"
        )
    }

    if ([bool]$manifest.eligible_for_official_pick) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "official_pick_eligibility_true"
        )
    }

    try {
        $predictionCount = (
            [int]$manifest.prediction_count
        )
    }
    catch {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "prediction_count_invalid"
        )
    }

    if ($predictionCount -le 0) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "prediction_count_not_positive"
        )
    }

    $predictionRunId = (
        [string]$manifest.prediction_run_id
    )

    if (
        $predictionRunId -cnotmatch
        "^hrv1-[0-9a-f]{16}$"
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "prediction_run_id_invalid"
        )
    }

    if (
        $manifestFile.Directory.Name -cne
        $predictionRunId
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "run_directory_identity_mismatch"
        )
    }

    $predictionsPath = Join-Path `
        $runDir `
        "predictions.csv"

    if (-not (
        Test-Path `
            -LiteralPath $predictionsPath `
            -PathType Leaf
    )) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "predictions_csv_missing"
        )
    }

    try {
        $predictionRows = @(
            Import-Csv `
                -LiteralPath $predictionsPath `
                -ErrorAction Stop
        )
    }
    catch {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "predictions_csv_invalid"
        )
    }

    if (
        $predictionRows.Count -ne
        $predictionCount
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "prediction_count_row_mismatch"
        )
    }

    $manifestPredictionsSha = (
        [string]$manifest.predictions_csv_sha256
    ).ToLowerInvariant()

    if (
        $manifestPredictionsSha -cnotmatch
        "^[0-9a-f]{64}$"
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "predictions_sha256_missing_or_invalid"
        )
    }

    $actualPredictionsSha = (
        Get-FileHash `
            -LiteralPath $predictionsPath `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    if (
        $actualPredictionsSha -ne
        $manifestPredictionsSha
    ) {
        return (
            New-ProspectiveEvidenceResult `
                -Valid $false `
                -Reason "predictions_sha256_mismatch"
        )
    }

    $manifestSha = (
        Get-FileHash `
            -LiteralPath $manifestFile.FullName `
            -Algorithm SHA256
    ).Hash.ToLowerInvariant()

    return (
        New-ProspectiveEvidenceResult `
            -Valid $true `
            -Reason "valid" `
            -ManifestPath $manifestFile.FullName `
            -PredictionsPath $predictionsPath `
            -PredictionRunId $predictionRunId `
            -PredictionCount $predictionCount `
            -ManifestSha256 $manifestSha `
            -PredictionsSha256 $actualPredictionsSha
    )
}

function Invoke-DateController {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OperatingDate
    )

    Write-Log "DATE_START operating_date=$OperatingDate"

    $parsedOperatingDate = [datetime]::ParseExact(
        $OperatingDate,
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture
    )

    $parsedCutoverDate = [datetime]::ParseExact(
        $cutoverOperatingDate,
        "yyyy-MM-dd",
        [System.Globalization.CultureInfo]::InvariantCulture
    )

    if ($parsedOperatingDate -lt $parsedCutoverDate) {
        Write-Log (
            "SKIP_PRE_CUTOVER operating_date=$OperatingDate " +
            "cutover_date=$cutoverOperatingDate"
        )

        return
    }

    $prospectiveEvidence = (
        Test-ProspectiveEvidence `
            -OperatingDate $OperatingDate
    )

    if (-not [bool]$prospectiveEvidence.Valid) {
        Write-Log (
            "SKIP_NO_PROSPECTIVE_EVIDENCE " +
            "operating_date=$OperatingDate " +
            "reason=$($prospectiveEvidence.Reason)"
        )

        return
    }

    Write-Log (
        "PROSPECTIVE_EVIDENCE_OK " +
        "operating_date=$OperatingDate " +
        "run_id=$($prospectiveEvidence.PredictionRunId) " +
        "predictions=$($prospectiveEvidence.PredictionCount) " +
        "manifest_sha256=$($prospectiveEvidence.ManifestSha256) " +
        "predictions_sha256=$($prospectiveEvidence.PredictionsSha256)"
    )

    $dayStamp = $OperatingDate.Replace("-", "")

    $stateFile = Join-Path `
        $stateRoot `
        "nightly_controller_$dayStamp.json"

    $finalizerMarker = Join-Path `
        $stateRoot `
        "finalizer_success_$dayStamp.json"

    $postStateRoot = Join-Path `
        $evidenceRoot `
        "state"

    $postDoneMarker = Join-Path `
        $postStateRoot `
        "postfinalizer_v2_$dayStamp.done"

    if (
        Test-Path `
            -LiteralPath $postDoneMarker `
            -PathType Leaf
    ) {
        $null = Read-RecoveryCompletionMarker -Path $postDoneMarker -OperatingDate $OperatingDate -Kind postfinalizer
        Write-Log (
            "DATE_COMPLETE operating_date=$OperatingDate " +
            "post_marker=$postDoneMarker"
        )

        return
    }

    $games = @(
        Get-MLBSchedule `
            -OperatingDate $OperatingDate
    )

    if ($games.Count -eq 0) {
        Write-Log "NO_GAMES operating_date=$OperatingDate"
        return
    }

    $gameRows = @()

    foreach ($game in $games) {

        $disposition = Get-GameDisposition -Game $game

        $away = [string]$game.teams.away.team.name
        $homeTeam = [string]$game.teams.home.team.name

        $gameRows += [PSCustomObject]@{
            GamePk   = [string]$game.gamePk
            Matchup  = "$away @ $homeTeam"
            Terminal = [bool]$disposition.Terminal
            Kind     = [string]$disposition.Kind
            Abstract = [string]$disposition.Abstract
            Detailed = [string]$disposition.Detailed
            Coded    = [string]$disposition.Coded
        }
    }

    $terminalCount = @(
        $gameRows |
            Where-Object {
                $_.Terminal
            }
    ).Count

    $nonTerminal = @(
        $gameRows |
            Where-Object {
                -not $_.Terminal
            }
    )

    Write-Log (
        "SCHEDULE operating_date=$OperatingDate " +
        "games=$($gameRows.Count) " +
        "terminal=$terminalCount " +
        "nonterminal=$($nonTerminal.Count)"
    )

    foreach ($row in $nonTerminal) {
        Write-Log (
            "WAIT_GAME game_pk=$($row.GamePk) " +
            "matchup='$($row.Matchup)' " +
            "abstract='$($row.Abstract)' " +
            "detailed='$($row.Detailed)' " +
            "coded='$($row.Coded)'"
        )
    }

    $allTerminal = (
        $gameRows.Count -gt 0 -and
        $nonTerminal.Count -eq 0
    )

    $state = Read-State -Path $stateFile

    if (-not $allTerminal) {

        if (
            $null -ne $state -and
            -not [string]::IsNullOrWhiteSpace(
                [string]$state.observed_all_terminal_at_utc
            )
        ) {
            Write-Log (
                "TERMINAL_STATE_REVOKED " +
                "operating_date=$OperatingDate"
            )

            if (-not $DryRun) {
                Write-JsonAtomic `
                    -Path $stateFile `
                    -Value ([PSCustomObject]@{
                        operating_date = $OperatingDate
                        observed_all_terminal_at_utc = $null
                        last_checked_at_utc = [datetime]::UtcNow.ToString("o")
                        completed = $false
                    })
            }
        }

        Write-Log (
            "WAIT_NOT_ALL_TERMINAL " +
            "operating_date=$OperatingDate"
        )

        return
    }

    Write-Log "ALL_TERMINAL operating_date=$OperatingDate"

    $observedUtc = $null

    if (
        $null -ne $state -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$state.observed_all_terminal_at_utc
        )
    ) {
        $observedUtc = [datetime]::Parse(
            [string]$state.observed_all_terminal_at_utc
        ).ToUniversalTime()
    }

    if ($null -eq $observedUtc) {

        $observedUtc = [datetime]::UtcNow

        Write-Log (
            "FIRST_ALL_TERMINAL_OBSERVATION " +
            "operating_date=$OperatingDate " +
            "observed_utc=$($observedUtc.ToString('o'))"
        )

        if ($DryRun) {
            Write-Log "DRY_RUN would_persist_terminal_observation"
            return
        }

        Write-JsonAtomic `
            -Path $stateFile `
            -Value ([PSCustomObject]@{
                operating_date = $OperatingDate
                observed_all_terminal_at_utc = $observedUtc.ToString("o")
                last_checked_at_utc = $observedUtc.ToString("o")
                completed = $false
            })

        return
    }

    $eligibleUtc = $observedUtc.AddHours($BufferHours)
    $now = [datetime]::UtcNow

    if ($now -lt $eligibleUtc) {

        $remaining = [math]::Ceiling(
            ($eligibleUtc - $now).TotalMinutes
        )

        Write-Log (
            "BUFFER_WAIT operating_date=$OperatingDate " +
            "eligible_utc=$($eligibleUtc.ToString('o')) " +
            "minutes_remaining=$remaining"
        )

        return
    }

    Write-Log (
        "BUFFER_COMPLETE operating_date=$OperatingDate " +
        "eligible_utc=$($eligibleUtc.ToString('o'))"
    )

    if ($DryRun) {
        Write-Log "DRY_RUN would_run_finalizer_then_postfinalizer"
        return
    }

    $head = Assert-Repository

    if (-not (
        Test-Path `
            -LiteralPath $finalizerScript `
            -PathType Leaf
    )) {
        throw "Finalizer script not found: $finalizerScript"
    }

    if (-not (
        Test-Path `
            -LiteralPath $postFinalizerScript `
            -PathType Leaf
    )) {
        throw "Post-finalizer V2 not found: $postFinalizerScript"
    }

    if (-not (
        Test-Path `
            -LiteralPath $authorizationTool `
            -PathType Leaf
    )) {
        throw "Pinned authorization tool not found: $authorizationTool"
    }

    $markerExists = Test-Path `
        -LiteralPath $finalizerMarker `
        -PathType Leaf

    $hasValidMarker = Test-FinalizerMarker `
        -Path $finalizerMarker `
        -OperatingDate $OperatingDate

    if ($markerExists -and -not $hasValidMarker) {
        throw (
            "Existing Finalizer success marker is invalid: " +
            $finalizerMarker
        )
    }

    $authorizationId = ""
    $claimedAuthorizationReceipt = ""
    $executionReceipt = ""
    $execution = $null

    if (-not $hasValidMarker) {

        $controlManifestPath = Join-Path $controlDir "control_manifest_v1.json"

        $issueText = (
            & $pythonExecutable -B $authorizationTool issue `
                --authorization-root $authorizationRoot `
                --repo-root $repo `
                --expected-commit $expectedCommit `
                --operating-date $OperatingDate `
                --control-id $controlId `
                --prediction-run-id $prospectiveEvidence.PredictionRunId `
                --prediction-manifest $prospectiveEvidence.ManifestPath `
                --predictions-csv $prospectiveEvidence.PredictionsPath `
                --control-manifest $controlManifestPath `
                --controller-run-id $controllerRunId `
                --controller-script $PSCommandPath 2>&1 |
            Out-String
        ).Trim()

        if ($LASTEXITCODE -ne 0) {
            throw "Finalizer authorization issue failed: $issueText"
        }

        $authorization = $issueText | ConvertFrom-Json
        if (-not [bool]$authorization.success) {
            throw "Finalizer authorization returned success=false."
        }

        $authorizationId = [string]$authorization.authorization_id
        $pendingAuthorizationReceipt = [string]$authorization.receipt_path
        $claimedAuthorizationReceipt = Join-Path `
            (Join-Path $authorizationRoot "claimed") `
            (Split-Path $pendingAuthorizationReceipt -Leaf)
        $executionReceipt = Join-Path `
            $stateRoot `
            "finalizer_execution_${dayStamp}_$authorizationId.json"

        if (
            [string]$authorization.payload.pinned_commit -ne $head -or
            [string]$authorization.payload.control_id -ne $controlId -or
            [string]$authorization.payload.operating_date -ne $OperatingDate -or
            [string]$authorization.payload.prediction_run_id -ne
                [string]$prospectiveEvidence.PredictionRunId -or
            [string]$authorization.payload.prediction_manifest_sha256 -ne
                [string]$prospectiveEvidence.ManifestSha256 -or
            [string]$authorization.payload.predictions_csv_sha256 -ne
                [string]$prospectiveEvidence.PredictionsSha256
        ) {
            throw "Issued finalizer authorization did not preserve evidence identity."
        }

        Write-Log (
            "FINALIZER_START operating_date=$OperatingDate " +
            "authorization_id=$authorizationId commit=$head"
        )

        & $powershellExecutable `
            -NoProfile `
            -ExecutionPolicy Bypass `
            -File $finalizerScript `
            -ConfigurationPath $ConfigurationPath `
            -ConfigurationSha256 $ConfigurationSha256 `
            -Date $OperatingDate `
            -ExpectedCommit $expectedCommit `
            -AuthorizationReceipt $pendingAuthorizationReceipt `
            -AuthorizationId $authorizationId `
            -ControlId $controlId `
            -ExecutionReceiptPath $executionReceipt

        $finalizerExit = $LASTEXITCODE

        Write-Log (
            "FINALIZER_EXIT operating_date=$OperatingDate " +
            "exit_code=$finalizerExit"
        )

        if ($finalizerExit -ne 0) {
            throw (
                "Finalizer failed for $OperatingDate " +
                "with exit code $finalizerExit."
            )
        }

        $executionText = (
            & $pythonExecutable -B $authorizationTool validate-execution `
                --results-csv $resultsCsv `
                --authorization-receipt $claimedAuthorizationReceipt `
                --authorization-id $authorizationId `
                --execution-receipt $executionReceipt `
                --expected-commit $expectedCommit `
                --operating-date $OperatingDate `
                --control-id $controlId 2>&1 |
            Out-String
        ).Trim()

        if ($LASTEXITCODE -ne 0) {
            throw "Finalizer execution receipt rejected: $executionText"
        }

        $execution = $executionText | ConvertFrom-Json
        if (
            -not [bool]$execution.success -or
            [string]$execution.executing_commit -ne $head
        ) {
            throw "Finalizer execution identity did not match controller identity."
        }

        Write-RecoveryCompletionMarker `
            -OperatingDate $OperatingDate -Kind finalizer `
            -Path $finalizerMarker `
            -Details @{
                operating_date     = $OperatingDate
                completed_at       = (Get-Date -Format o)
                success            = $true
                exit_code          = 0
                repository_commit  = [string]$execution.executing_commit
                finalizer_script   = $finalizerScript
                authorization_id   = $authorizationId
                authorization_receipt = $claimedAuthorizationReceipt
                execution_receipt  = $executionReceipt
                controller_run_id  = $controllerRunId
                control_id         = $controlId
                prediction_run_id  = [string]$prospectiveEvidence.PredictionRunId
                prediction_manifest_sha256 = [string]$prospectiveEvidence.ManifestSha256
                predictions_csv_sha256 = [string]$prospectiveEvidence.PredictionsSha256
                controller_version = "4C1N.4E.2H"
            }

        Write-Log (
            "FINALIZER_MARKER_WRITTEN " +
            "path=$finalizerMarker"
        )
    }
    else {
        $existingMarker = (
            Get-Content -LiteralPath $finalizerMarker -Raw |
            ConvertFrom-Json
        )
        $authorizationId = [string]$existingMarker.authorization_id
        $claimedAuthorizationReceipt = [string]$existingMarker.authorization_receipt
        $executionReceipt = [string]$existingMarker.execution_receipt

        $executionText = (
            & $pythonExecutable -B $authorizationTool validate-execution `
                --results-csv $resultsCsv `
                --authorization-receipt $claimedAuthorizationReceipt `
                --authorization-id $authorizationId `
                --execution-receipt $executionReceipt `
                --expected-commit $expectedCommit `
                --operating-date $OperatingDate `
                --control-id $controlId 2>&1 |
            Out-String
        ).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "Existing finalizer execution receipt rejected: $executionText"
        }
        $execution = $executionText | ConvertFrom-Json

        Write-Log (
            "FINALIZER_ALREADY_SUCCESSFUL " +
            "marker=$finalizerMarker"
        )
    }

    Write-Log (
        "POSTFINALIZER_START " +
        "operating_date=$OperatingDate"
    )

    & $powershellExecutable `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $postFinalizerScript `
        -ConfigurationPath $ConfigurationPath `
        -ConfigurationSha256 $ConfigurationSha256 `
        -Date $OperatingDate `
        -FinalizerSuccessMarker $finalizerMarker `
        -ControlId $controlId `
        -ExpectedCommit $expectedCommit `
        -AuthorizationReceipt $claimedAuthorizationReceipt `
        -AuthorizationId $authorizationId `
        -ExecutionReceipt $executionReceipt

    $postExit = $LASTEXITCODE

    Write-Log (
        "POSTFINALIZER_EXIT operating_date=$OperatingDate " +
        "exit_code=$postExit"
    )

    if ($postExit -ne 0) {
        throw (
            "Post-finalizer failed for $OperatingDate " +
            "with exit code $postExit."
        )
    }

    Write-JsonAtomic `
        -Path $stateFile `
        -Value ([PSCustomObject]@{
            operating_date = $OperatingDate
            observed_all_terminal_at_utc = $observedUtc.ToString("o")
            eligible_at_utc = $eligibleUtc.ToString("o")
            completed_at_utc = [datetime]::UtcNow.ToString("o")
            completed = $true
            repository_commit = [string]$execution.executing_commit
            finalizer_marker = $finalizerMarker
            postfinalizer_marker = $postDoneMarker
        })

    Write-Log "DATE_SUCCESS operating_date=$OperatingDate"
}

$lockStream = $null

try {

    if (-not $DryRun) {

        $lockPath = Join-Path `
            $stateRoot `
            ".nightly_controller.lock"

        try {
            $lockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
        }
        catch {
            Write-Log "SKIP controller_lock_busy"
            exit 0
        }
    }

    Write-Log (
        "START dry_run=$DryRun " +
        "buffer_hours=$BufferHours " +
        "lookback_days=$LookbackDays"
    )

    $targets = @(Get-TargetDates)

    Write-Log (
        "TARGET_DATES " +
        ($targets -join ",")
    )

    $hadFailure = $false

    foreach ($target in $targets) {

        try {
            Invoke-DateController `
                -OperatingDate $target
        }
        catch {
            Write-Log (
                "DATE_FAILED operating_date=$target " +
                "error='$($_.Exception.Message)'"
            )

            $hadFailure = $true

            if ($Date.Count -gt 0) {
                throw
            }
        }
    }

    if ($hadFailure) {
        throw "One or more target dates failed."
    }

    Write-Log "SUCCESS controller_cycle_complete"

    exit 0
}
catch {

    Write-Log "FAILED $($_.Exception.Message)"
    exit 1
}
finally {

    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
