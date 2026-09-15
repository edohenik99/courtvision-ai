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
    [string]$ExecutionReceiptPath
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

$contractTool = Join-Path $repo "tools\courtvision_pinned_finalizer_contract.py"
$finalizerScript = Join-Path $repo "tools\run_courtvision_mlb_nightly_pipeline.ps1"

if (-not $recoveryConfig.allow_provider_collection) { throw "Provider-backed finalizer execution is disabled; use fixture receipt rehearsal." }

# Claim is atomic and one-time. Nothing under the repository runtime roots is
# created until the claimed receipt and actual clean Git identity both verify.
$claimText = (
    & $pythonExecutable -B $contractTool claim `
        --authorization-receipt $AuthorizationReceipt `
        --authorization-id $AuthorizationId `
        --expected-commit $ExpectedCommit `
        --operating-date $Date `
        --control-id $ControlId 2>&1 |
    Out-String
).Trim()

if ($LASTEXITCODE -ne 0) {
    throw "Pinned finalizer authorization claim failed: $claimText"
}

$claim = $claimText | ConvertFrom-Json
if (-not [bool]$claim.success) {
    throw "Pinned finalizer authorization claim returned success=false."
}

$claimedReceipt = [string]$claim.receipt_path

& $powershellExecutable `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $finalizerScript `
    -Date $Date `
    -ExpectedCommit $ExpectedCommit `
    -AuthorizationReceipt $claimedReceipt `
    -AuthorizationId $AuthorizationId `
    -ControlId $ControlId `
    -ExecutionReceiptPath $ExecutionReceiptPath `
    -PythonExecutable $pythonExecutable

exit $LASTEXITCODE
