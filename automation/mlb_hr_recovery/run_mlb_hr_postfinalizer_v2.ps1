[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigurationPath,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-f]{64}$')]
    [string]$ConfigurationSha256,
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^\d{4}-\d{2}-\d{2}$")]
    [string]$Date,

    [Parameter(Mandatory = $true)]
    [string]$FinalizerSuccessMarker,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^mlb-hr-control-v1-[0-9a-f]{20}$")]
    [string]$ControlId,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[0-9a-f]{40}$")]
    [string]$ExpectedCommit,

    [Parameter(Mandatory = $true)]
    [string]$AuthorizationReceipt,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^cvfa-v1-[0-9a-f]{64}$")]
    [string]$AuthorizationId,

    [Parameter(Mandatory = $true)]
    [string]$ExecutionReceipt
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$requestedCommit = $ExpectedCommit
$requestedControl = $ControlId
. (Join-Path $PSScriptRoot "recovery_common.ps1")
. Initialize-RecoveryRuntime -ConfigurationPath $ConfigurationPath -ConfigurationSha256 $ConfigurationSha256
$evidenceRoot = Join-Path (Join-Path $evidenceRoot $controlId) $ConfigurationSha256
Assert-RecoveryNoReparsePath -Path $evidenceRoot
. (Join-Path $PSScriptRoot "recovery_completion.ps1")
if ($requestedCommit -cne $expectedCommit -or $requestedControl -cne $controlId) { throw "Caller commit/control differs from immutable recovery configuration." }

$authorizationTool = Join-Path $repo "tools\courtvision_pinned_finalizer_contract.py"

$executionText = (
    & $pythonExecutable -B $authorizationTool validate-execution `
        --results-csv $resultsCsv `
        --authorization-receipt $AuthorizationReceipt `
        --authorization-id $AuthorizationId `
        --execution-receipt $ExecutionReceipt `
        --expected-commit $ExpectedCommit `
        --operating-date $Date `
        --control-id $ControlId 2>&1 |
    Out-String
).Trim()

if ($LASTEXITCODE -ne 0) {
    throw "Pinned execution receipt rejected before post-finalizer mutation: $executionText"
}

$execution = $executionText | ConvertFrom-Json
if (
    -not [bool]$execution.success -or
    [string]$execution.executing_commit -ne $ExpectedCommit
) {
    throw "Pinned execution receipt did not prove the expected commit."
}




$logDir = Join-Path $evidenceRoot "logs"
$statusDir = Join-Path $evidenceRoot "status"
$stateDir = Join-Path $evidenceRoot "state"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
New-Item -ItemType Directory -Path $statusDir -Force | Out-Null
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null

$parsedDate = [datetime]::ParseExact(
    $Date,
    "yyyy-MM-dd",
    [System.Globalization.CultureInfo]::InvariantCulture
)

$dayStamp = $parsedDate.ToString("yyyyMMdd")

$logFile = Join-Path `
    $logDir `
    "postfinalizer_v2_$dayStamp.log"

$statusFile = Join-Path `
    $statusDir `
    "prospective_status_$dayStamp.json"

$doneMarker = Join-Path `
    $stateDir `
    "postfinalizer_v2_$dayStamp.done"

function Write-Log {
    param([string]$Message)

    "$(Get-Date -Format o) $Message" |
        Tee-Object -FilePath $logFile -Append
}

try {

    Write-Log "START operating_date=$Date"

    if (Test-Path -LiteralPath $doneMarker) {
        $null = Read-RecoveryCompletionMarker -Path $doneMarker -OperatingDate $Date -Kind postfinalizer
        Write-Log "SKIP already_completed marker=$doneMarker"
        exit 0
    }

    Set-Location -LiteralPath $repo

    # ------------------------------------------------------------
    # Frozen repository preflight
    # ------------------------------------------------------------

    $dirty = @(git status --porcelain)

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read Git status."
    }

    if ($dirty.Count -ne 0) {
        throw "Repository is dirty. Post-finalizer refused."
    }

    $head = (git rev-parse HEAD).Trim()

    if ($LASTEXITCODE -ne 0) {
        throw "Unable to resolve Git HEAD."
    }

    if ($head -ne $expectedCommit) {
        throw "Unexpected Git commit: $head"
    }

    Write-Log "GIT_OK commit=$head"

    # ------------------------------------------------------------
    # Frozen control preflight
    # ------------------------------------------------------------

    if (-not (
        Test-Path `
            -LiteralPath $controlDir `
            -PathType Container
    )) {
        throw "Control directory not found: $controlDir"
    }

    $controlManifest = Join-Path `
        $controlDir `
        "control_manifest_v1.json"

    if (-not (
        Test-Path `
            -LiteralPath $controlManifest `
            -PathType Leaf
    )) {
        throw "Control manifest not found: $controlManifest"
    }

    Write-Log "CONTROL_OK control_id=$controlId"

    # ------------------------------------------------------------
    # Require explicit synchronous-finalizer evidence
    # ------------------------------------------------------------

    if (-not (
        Test-Path `
            -LiteralPath $FinalizerSuccessMarker `
            -PathType Leaf
    )) {
        throw "Finalizer success marker not found: $FinalizerSuccessMarker"
    }

    $marker = Read-RecoveryCompletionMarker -Path $FinalizerSuccessMarker -OperatingDate $Date -Kind finalizer

    if ([string]$marker.operating_date -ne $Date) {
        throw (
            "Finalizer marker operating date mismatch. " +
            "Expected=$Date Actual=$($marker.operating_date)"
        )
    }

    if ([int]$marker.exit_code -ne 0) {
        throw "Finalizer marker does not record exit code 0."
    }

    if ([string]$marker.repository_commit -ne $expectedCommit) {
        throw "Finalizer marker repository commit mismatch."
    }

    if ([string]$marker.authorization_id -ne $AuthorizationId) {
        throw "Finalizer marker authorization identity mismatch."
    }

    if ([string]$marker.execution_receipt -ne $ExecutionReceipt) {
        throw "Finalizer marker execution receipt mismatch."
    }

    if (-not [bool]$marker.success) {
        throw "Finalizer marker does not record success=true."
    }

    Write-Log (
        "FINALIZER_OK completed_at={0} marker={1}" -f `
        $marker.completed_at,
        $FinalizerSuccessMarker
    )

    # ------------------------------------------------------------
    # Strict results
    # ------------------------------------------------------------

    if (-not (
        Test-Path `
            -LiteralPath $resultsCsv `
            -PathType Leaf
    )) {
        throw "Results CSV not found: $resultsCsv"
    }

    Write-Log "RESULTS_OK path=$resultsCsv"

    # ------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------

    Write-Log "SETTLEMENT_START"

    $settlementText = (
        & $pythonExecutable -B `
            -m courtvision.sports.mlb.training.hr_research_baseline `
            settle-prospective-paper-day `
            --control-dir $controlDir `
            --results-csv $resultsCsv `
            --trial-root $trialRoot `
            --executing-commit $execution.executing_commit `
            --authorization-id $AuthorizationId `
            2>&1 |
        Out-String
    ).Trim()

    $settlementExit = $LASTEXITCODE

    Write-Log "SETTLEMENT $settlementText"

    if ($settlementExit -ne 0) {
        throw (
            "Settlement failed with exit code " +
            "$settlementExit."
        )
    }

    $settlement = $settlementText | ConvertFrom-Json

    if (-not $settlement.success) {
        throw "Settlement returned success=false."
    }

    if (
        [string]$settlement.settlement_executing_commit -ne
            [string]$execution.executing_commit -or
        [string]$settlement.settlement_authorization_id -ne
            $AuthorizationId
    ) {
        throw "Settlement did not record the pinned execution identity."
    }

    if ([int]$settlement.conflicting_settlements -ne 0) {
        throw (
            "Settlement conflicts detected: " +
            "$($settlement.conflicting_settlements)"
        )
    }

    Write-Log (
        "SETTLEMENT_OK appended={0} skipped={1} pending={2} conflicts={3}" -f `
        $settlement.settlements_appended,
        $settlement.skipped_existing_settlements,
        $settlement.pending_predictions,
        $settlement.conflicting_settlements
    )

    # ------------------------------------------------------------
    # Prospective status
    # ------------------------------------------------------------

    Write-Log "STATUS_START"

    $statusText = (
        & $pythonExecutable -B `
            -m courtvision.sports.mlb.training.hr_research_baseline `
            report-prospective-status `
            --control-dir $controlDir `
            --trial-root $trialRoot `
            2>&1 |
        Out-String
    ).Trim()

    $statusExit = $LASTEXITCODE

    Write-Log "STATUS $statusText"

    if ($statusExit -ne 0) {
        throw (
            "Prospective status failed with exit code " +
            "$statusExit."
        )
    }

    $status = $statusText | ConvertFrom-Json

    if (
        $status.schema_version -ne `
        "mlb-hr-prospective-status-v1"
    ) {
        throw "Unexpected prospective status schema."
    }

    if (-not $status.research_only) {
        throw "Research-only boundary violated."
    }

    if ($status.eligible_for_betting) {
        throw "Unexpected betting eligibility."
    }

    if ($status.eligible_for_official_pick) {
        throw "Unexpected OfficialPick eligibility."
    }

    if (
        $status.artifact_integrity.status -ne `
        "valid"
    ) {
        throw "Artifact integrity is not valid."
    }

    if (
        [int]$status.artifact_integrity.finding_count `
        -ne 0
    ) {
        throw "Artifact integrity findings detected."
    }

    $statusText |
        Set-Content `
            -LiteralPath $statusFile `
            -Encoding UTF8

    Write-Log (
        "STATUS_OK predictions={0} settled={1} pending={2} positives={3} games={4} dates={5} closing={6:N4} calibration={7:N6} pnl={8}" -f `
        $status.counts.committed_predictions,
        $status.counts.settled_predictions,
        $status.counts.pending_predictions,
        $status.counts.positive_hr_outcomes,
        $status.counts.unique_games,
        $status.counts.prospective_operating_dates,
        $status.closing_line_coverage.coverage_rate,
        $status.metrics.calibration_error,
        $status.metrics.flat_one_unit_profit_loss
    )

    # ------------------------------------------------------------
    # Completion evidence
    # ------------------------------------------------------------

    Write-RecoveryCompletionMarker -Path $doneMarker -OperatingDate $Date -Kind postfinalizer -Details @{
        operating_date        = $Date
        completed_at          = (Get-Date -Format o)
        repository_commit     = [string]$settlement.settlement_executing_commit
        control_id            = $controlId
        authorization_id      = $AuthorizationId
        authorization_receipt = $AuthorizationReceipt
        execution_receipt     = $ExecutionReceipt
        finalizer_marker      = $FinalizerSuccessMarker
        settlements_appended  = [int]$settlement.settlements_appended
        pending_predictions   = [int]$settlement.pending_predictions
        committed_predictions = [int]$status.counts.committed_predictions
        settled_predictions   = [int]$status.counts.settled_predictions
        artifact_integrity    = [string]$status.artifact_integrity.status
        status_file           = $statusFile
    }

    Write-Log "SUCCESS status_file=$statusFile"

    exit 0
}
catch {

    Write-Log "FAILED $($_.Exception.Message)"
    exit 1
}
