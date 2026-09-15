$script:PostPublishShadowSchedulerRoot = $PSScriptRoot

function Get-ShadowSchedulerPublicationValidation {
    param(
        [Parameter(Mandatory = $true)]
        [psobject]$Publication,

        [Parameter(Mandatory = $true)]
        [ValidatePattern('^\d{4}-\d{2}-\d{2}$')]
        [string]$OperatingDate
    )

    function Invalid-Publication {
        param([string]$Reason)

        return [pscustomobject]@{
            IsValid = $false
            Reason = $Reason
            TriggerMode = ""
            RequiresReplayVerification = $false
        }
    }

    $success = $Publication.PSObject.Properties["success"]
    if ($null -eq $success -or $success.Value -isnot [bool] -or -not $success.Value) {
        return Invalid-Publication "publication success is not true"
    }

    if ([string]$Publication.status -cne "completed") {
        return Invalid-Publication "publication status is not completed"
    }

    $dryRun = $Publication.PSObject.Properties["dry_run"]
    if ($null -ne $dryRun -and $dryRun.Value -is [bool] -and $dryRun.Value) {
        return Invalid-Publication "publication is a dry run"
    }

    if ([string]$Publication.operating_date -cne $OperatingDate) {
        return Invalid-Publication "publication operating date does not match"
    }

    $predictionCount = 0
    if (-not [int]::TryParse(
        [string]$Publication.prediction_count,
        [ref]$predictionCount
    ) -or $predictionCount -le 0) {
        return Invalid-Publication "publication prediction_count is not positive"
    }

    if ([string]::IsNullOrWhiteSpace([string]$Publication.prediction_run_id)) {
        return Invalid-Publication "publication prediction_run_id is missing"
    }

    if ([string]::IsNullOrWhiteSpace([string]$Publication.control_id)) {
        return Invalid-Publication "publication control_id is missing"
    }

    if ([string]::IsNullOrWhiteSpace([string]$Publication.run_path)) {
        return Invalid-Publication "publication run_path is missing"
    }

    $runPath = [string]$Publication.run_path
    $runParts = @($runPath -split '/')
    if (
        $runPath.Contains("\") -or
        $runParts.Count -ne 5 -or
        $runParts[0] -cne "controls" -or
        [string]::IsNullOrWhiteSpace($runParts[1]) -or
        $runParts[1] -cne [string]$Publication.control_id -or
        $runParts[2] -cne "dates" -or
        $runParts[3] -cne $OperatingDate -or
        $runParts[4] -cne [string]$Publication.prediction_run_id -or
        @($runParts | Where-Object { $_ -in @(".", "..") }).Count -gt 0
    ) {
        return Invalid-Publication "publication run_path does not match control/date/run identity"
    }

    if ([string]$Publication.prediction_manifest_digest -cnotmatch '^[0-9a-f]{64}$') {
        return Invalid-Publication "publication manifest digest is invalid"
    }

    $replayed = $Publication.PSObject.Properties["replayed_existing_run"]
    if ($null -eq $replayed -or $replayed.Value -isnot [bool]) {
        return Invalid-Publication "publication replay status is ambiguous"
    }

    $ledgerRows = 0
    if (-not [int]::TryParse(
        [string]$Publication.ledger_rows_appended,
        [ref]$ledgerRows
    )) {
        return Invalid-Publication "publication ledger rows are invalid"
    }

    if (-not $replayed.Value) {
        if ($ledgerRows -ne $predictionCount) {
            return Invalid-Publication "fresh publication ledger rows do not match predictions"
        }
        $triggerMode = "fresh_publication"
        $requiresReplayVerification = $false
    }
    else {
        if ($ledgerRows -ne 0) {
            return Invalid-Publication "replayed publication ledger rows are not zero"
        }
        $triggerMode = "verified_replay"
        $requiresReplayVerification = $true
    }

    return [pscustomobject]@{
        IsValid = $true
        Reason = ""
        TriggerMode = $triggerMode
        RequiresReplayVerification = $requiresReplayVerification
    }
}

function Invoke-ShadowSchedulerAfterPublication {
    throw 'Supplemental shadow scheduling is disabled for the minimal recovery architecture.'
}