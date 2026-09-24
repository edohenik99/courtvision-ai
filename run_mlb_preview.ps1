[CmdletBinding()]
param(
    [string]$Date,
    [string]$HitsSources,
    [string]$HrPredictions
)

$ErrorActionPreference = 'Stop'
$previewPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $previewPython -PathType Leaf)) {
    Write-Error 'MLB_PREVIEW_PYTHON_UNAVAILABLE: canonical .venv interpreter is missing.'
    exit 2
}
$previewArguments = @('-B', '-m', 'courtvision.sports.mlb.research_preview_sources')
if ($Date) { $previewArguments += @('--date', $Date) }
if ($HitsSources) { $previewArguments += @('--hits-sources', $HitsSources) }
if ($HrPredictions) { $previewArguments += @('--hr-predictions', $HrPredictions) }
Push-Location $PSScriptRoot
try {
    & $previewPython @previewArguments
    $previewExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $previewExitCode
