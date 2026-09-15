# Immutable completion markers bind one control/configuration and canonical evidence.

function Get-RecoveryEvidencePrefixDigest {
    param([string]$Path, [long]$Length)
    Assert-RecoveryNoReparsePath -Path $Path
    $bytes = [IO.File]::ReadAllBytes($Path)
    if ($Length -le 0 -or $Length -gt $bytes.Length -or $Length -gt [int]::MaxValue) {
        throw 'Canonical evidence is missing or shorter than its completion marker.'
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($bytes, 0, [int]$Length))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Read-RecoveryCompletionMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OperatingDate,
        [Parameter(Mandatory = $true)][ValidateSet('closing', 'postfinalizer', 'finalizer')][string]$Kind
    )
    Assert-RecoveryNoReparsePath -Path $Path
    $marker = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($marker.schema_version -cne 'mlb-hr-recovery-completion-v1' -or
        $marker.kind -cne $Kind -or $marker.operating_date -cne $OperatingDate -or
        $marker.control_id -cne $controlId -or $marker.repository_commit -cne $expectedCommit -or
        $marker.configuration_sha256 -cne $ConfigurationSha256 -or
        $marker.control_manifest_sha256 -cne $expectedControlManifestSha256 -or
        ($marker.evidence_size -isnot [int] -and $marker.evidence_size -isnot [long]) -or
        [string]$marker.evidence_sha256 -cnotmatch '^[0-9a-f]{64}$') {
        throw 'Completion marker does not match the intended date/control/configuration.'
    }
    $filename = if ($Kind -eq 'closing') { 'closing_lines.csv' } else { 'prospective_ledger.csv' }
    $evidencePath = Join-Path $controlDir $filename
    $observed = Get-RecoveryEvidencePrefixDigest -Path $evidencePath -Length $marker.evidence_size
    if ($observed -cne $marker.evidence_sha256) { throw 'Canonical evidence changed since completion.' }
    if ($Kind -eq 'closing') {
        $null = Assert-RecoveryPublishedEvidence -PredictionsCsv $marker.predictions_csv -OperatingDate $OperatingDate
    }
    $healthText = & $pythonExecutable -B -m courtvision.sports.mlb.training.hr_research_baseline report-prospective-health --control-dir $controlDir --trial-root $trialRoot
    if ($LASTEXITCODE -ne 0) { throw 'Canonical prospective health failed during completion verification.' }
    $health = ($healthText | Out-String) | ConvertFrom-Json
    if ($health.schema_version -cne "mlb-hr-prospective-health-v1" -or $health.control.control_id -cne $controlId -or
        $health.control.artifact_integrity_status -cne 'valid') {
        throw 'Canonical prospective integrity is not valid.'
    }
    return $marker
}

function Write-RecoveryCompletionMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$OperatingDate,
        [Parameter(Mandatory = $true)][ValidateSet('closing', 'postfinalizer', 'finalizer')][string]$Kind,
        [Parameter(Mandatory = $true)][hashtable]$Details
    )
    $filename = if ($Kind -eq 'closing') { 'closing_lines.csv' } else { 'prospective_ledger.csv' }
    $evidencePath = Join-Path $controlDir $filename
    Assert-RecoveryNoReparsePath -Path $Path
    $length = (Get-Item -LiteralPath $evidencePath -ErrorAction Stop).Length
    $payload = @{}
    foreach ($key in $Details.Keys) { $payload[$key] = $Details[$key] }
    $payload.schema_version = 'mlb-hr-recovery-completion-v1'
    $payload.kind = $Kind
    $payload.operating_date = $OperatingDate
    $payload.control_id = $controlId
    $payload.repository_commit = $expectedCommit
    $payload.configuration_sha256 = $ConfigurationSha256
    $payload.control_manifest_sha256 = $expectedControlManifestSha256
    $payload.evidence_size = $length
    $payload.evidence_sha256 = Get-RecoveryEvidencePrefixDigest -Path $evidencePath -Length $length
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($payload | ConvertTo-Json -Depth 20))
    $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush() }
    finally { $stream.Dispose() }
}
