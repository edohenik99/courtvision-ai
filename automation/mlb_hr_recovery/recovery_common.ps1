Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-RecoveryNoReparsePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (($Path -split '[\\/]') -contains '..') { throw 'Parent traversal is not a bound path.' }
    $cursor = [IO.Path]::GetFullPath($Path)
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'A bound path contains a symlink or junction.'
            }
        }
        $parent = [IO.Path]::GetDirectoryName($cursor)
        if ($parent -eq $cursor) { break }
        $cursor = $parent
    }
}

function Get-RecoveryBoundFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-f]{64}$')][string]$Sha256
    )
    Assert-RecoveryNoReparsePath -Path $Path
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Bound input must be a regular file.'
    }
    if ((Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Sha256) {
        throw 'Bound input digest mismatch.'
    }
    return $item
}

function Initialize-RecoveryRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigurationPath,
        [Parameter(Mandatory = $true)][string]$ConfigurationSha256
    )
    $configurationFile = Get-RecoveryBoundFile -Path $ConfigurationPath -Sha256 $ConfigurationSha256
    $unvalidated = Get-Content -LiteralPath $configurationFile.FullName -Raw | ConvertFrom-Json
    $repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
    if ([IO.Path]::GetFullPath([string]$unvalidated.repository_root) -cne $repo) {
        throw 'Configuration repository differs from the executing versioned automation.'
    }
    $pythonExecutable = [string]$unvalidated.python_executable
    $powershellExecutable = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
    Assert-RecoveryNoReparsePath -Path $powershellExecutable
    if (-not (Test-Path -LiteralPath $powershellExecutable -PathType Leaf)) {
        throw 'The expected Windows system PowerShell executable is unavailable.'
    }
    Assert-RecoveryNoReparsePath -Path $pythonExecutable
    if (-not [IO.Path]::IsPathRooted($pythonExecutable) -or -not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
        throw 'An explicit existing Python interpreter is required.'
    }
    $bootstrap = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from tools.mlb_hr_recovery_contract import main; raise SystemExit(main())'
    $validated = & $pythonExecutable -I -B -c $bootstrap $repo validate-runtime `
        --configuration $configurationFile.FullName --configuration-sha256 $ConfigurationSha256
    if ($LASTEXITCODE -ne 0) { throw 'Recovery runtime/control preflight failed.' }
    $recoveryConfig = ($validated | Out-String) | ConvertFrom-Json
    $trialRoot = [string]$recoveryConfig.trial_root
    $controlId = [string]$recoveryConfig.control_id
    $controlDir = [string]$recoveryConfig.control_dir
    $expectedCommit = [string]$recoveryConfig.expected_commit
    $cutoverOperatingDate = [string]$recoveryConfig.cutover_operating_date
    $expectedControlManifestSha256 = [string]$recoveryConfig.control_manifest_sha256
    $automationRoot = $PSScriptRoot
    $evidenceRoot = [string]$recoveryConfig.evidence_root
    $resultsCsv = [string]$recoveryConfig.results_csv
    $snapshotDir = [string]$recoveryConfig.odds_directory
    $canonicalSnapshotRoot = Join-Path $repo 'data\theoddsapi\live_hr_snapshots'
    if ([IO.Path]::GetFullPath($snapshotDir) -cne $canonicalSnapshotRoot -or
        [IO.Path]::GetFullPath($resultsCsv) -cne (Join-Path $canonicalSnapshotRoot 'live_hr_results.csv')) {
        throw 'Configured source/result paths differ from the reviewed pinned pipeline contract.'
    }
    $env:PYTHONPATH = $repo
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Set-Location -LiteralPath $repo
}

function Assert-RecoveryOperatingDate {
    param([Parameter(Mandatory = $true)][string]$OperatingDate)
    $parsed = [datetime]::ParseExact($OperatingDate, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture)
    $today = [TimeZoneInfo]::ConvertTimeFromUtc([datetime]::UtcNow, [TimeZoneInfo]::FindSystemTimeZoneById('Eastern Standard Time')).ToString('yyyy-MM-dd')
    if ($OperatingDate -cne $today -or $OperatingDate -lt $cutoverOperatingDate) {
        throw 'Prediction/closing operating date must be the current Toronto date after cutover.'
    }
}

function Assert-RecoveryPublishedEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$PredictionsCsv,
        [Parameter(Mandatory = $true)][string]$OperatingDate
    )
    $validated = & $pythonExecutable -B -m tools.mlb_hr_recovery_contract validate-publication `
        --configuration $ConfigurationPath --configuration-sha256 $ConfigurationSha256 `
        --predictions-csv $PredictionsCsv --operating-date $OperatingDate
    if ($LASTEXITCODE -ne 0) { throw 'Published artifact/control/ledger identity rejected.' }
    return (($validated | Out-String) | ConvertFrom-Json)
}

function New-RecoveryTaskArguments {
    param(
        [Parameter(Mandatory = $true)][string]$Runner,
        [Parameter(Mandatory = $true)][string]$OperatingDate
    )
    foreach ($argument in @($Runner, $OperatingDate, $ConfigurationPath, $ConfigurationSha256)) {
        if ($argument -match '["\r\n]') { throw 'Unsafe task argument.' }
    }
    if ((Split-Path -Parent ([IO.Path]::GetFullPath($Runner))) -cne $automationRoot) {
        throw 'Child runner must belong to this exact versioned automation directory.'
    }
    if ((Split-Path -Leaf $Runner) -cnotin @('run_mlb_hr_dynamic_prediction.ps1', 'run_mlb_hr_dynamic_closing.ps1')) {
        throw 'Child runner must be the canonical prediction or closing runner.'
    }
    return ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -OperatingDate "{1}" -ConfigurationPath "{2}" -ConfigurationSha256 "{3}"' -f $Runner, $OperatingDate, $ConfigurationPath, $ConfigurationSha256)
}

function Assert-RecoveryNewTask {
    param(
        [Parameter(Mandatory = $true)][string]$TaskName,
        [Parameter(Mandatory = $true)][string]$TaskPath,
        [Parameter(Mandatory = $true)][string]$Execute,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )
    if ($TaskPath.Length -ne 1 -or [int][char]$TaskPath[0] -ne 92 -or
        $TaskName -cnotmatch '^CourtVision MLB HR Dynamic (Prediction|Closing) (\d{8}) mlb-hr-control-v1-[0-9a-f]{20}$' -or
        -not $TaskName.EndsWith($controlId, [StringComparison]::Ordinal) -or
        $WorkingDirectory -cne $repo -or $Execute -cne $powershellExecutable -or
        -not $Arguments.Contains($ConfigurationSha256)) {
        throw 'Task identity is not the explicit intended dated control action.'
    }
    $kind = $Matches[1]
    $taskDate = [datetime]::ParseExact($Matches[2], 'yyyyMMdd', [Globalization.CultureInfo]::InvariantCulture).ToString('yyyy-MM-dd')
    if ($taskDate -cne $OperatingDate) { throw 'Task date differs from the intended operating date.' }
    $intendedRunner = Join-Path $automationRoot ('run_mlb_hr_dynamic_{0}.ps1' -f $kind.ToLowerInvariant())
    $intendedArguments = New-RecoveryTaskArguments -Runner $intendedRunner -OperatingDate $OperatingDate
    if ($Arguments -cne $intendedArguments) { throw 'Task arguments differ from the exact canonical runner/configuration/date.' }
    # Enumerating the exact root distinguishes an absent name from access denied.
    $existing = @(Get-ScheduledTask -TaskPath $TaskPath -ErrorAction Stop | Where-Object { $_.TaskName -eq $TaskName })
    if ($existing.Count -ne 0) {
        throw 'Task name collision: retained definitions must never be replaced or activated.'
    }
    return 'MISSING_NO_ACTION'
}

function New-RecoveryEnrichedOdds {
    param(
        [Parameter(Mandatory = $true)][string]$OddsPath,
        [Parameter(Mandatory = $true)][object]$Schedule
    )
    $revisionRoot = Join-Path $evidenceRoot 'input_revisions'
    [void][IO.Directory]::CreateDirectory($revisionRoot)
    $revisionId = [guid]::NewGuid().ToString('N')
    $schedulePath = Join-Path $revisionRoot "schedule_$revisionId.json"
    $payload = [Text.UTF8Encoding]::new($false).GetBytes(($Schedule | ConvertTo-Json -Depth 100))
    $stream = [IO.File]::Open($schedulePath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($payload, 0, $payload.Length) } finally { $stream.Dispose() }
    $oddsDigest = (Get-FileHash -LiteralPath $OddsPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $scheduleDigest = (Get-FileHash -LiteralPath $schedulePath -Algorithm SHA256).Hash.ToLowerInvariant()
    # Model source references are scoped to the trial or repository, never an external pointer.
    $inputRoot = Join-Path $trialRoot 'recovery_input_revisions'
    [void][IO.Directory]::CreateDirectory($inputRoot)
    $enrichedPath = Join-Path $inputRoot "odds_$revisionId.csv"
    $result = & $pythonExecutable -B -m tools.mlb_hr_recovery_contract enrich-odds `
        --odds-path $OddsPath --odds-sha256 $oddsDigest `
        --schedule-path $schedulePath --schedule-sha256 $scheduleDigest `
        --operating-date $OperatingDate --output $enrichedPath --require-complete-slate
    if ($LASTEXITCODE -ne 0) { throw 'Official schedule/odds identity binding failed.' }
    $revision = ($result | Out-String) | ConvertFrom-Json
    # Python owns schedule candidate matching. Verify its explicit, digest-bound
    # complete-slate receipt instead of matching provider timestamps a second time.
    $requiredFields = @('success', 'path', 'sha256', 'rows', 'source_odds_sha256',
        'schedule_sha256', 'operating_date', 'match_tolerance_seconds',
        'complete_slate_required', 'provider_events', 'canonical_game_bindings',
        'eligible_official_games', 'ambiguous_bindings', 'unmatched_bindings',
        'research_only', 'approval_status', 'eligible_for_betting', 'eligible_for_official_pick')
    foreach ($field in $requiredFields) {
        if ($null -eq $revision -or $field -cnotin $revision.PSObject.Properties.Name) {
            throw 'Schedule binding receipt is incomplete.'
        }
    }
    foreach ($field in @('rows', 'match_tolerance_seconds', 'provider_events',
        'canonical_game_bindings', 'eligible_official_games', 'ambiguous_bindings', 'unmatched_bindings')) {
        if ($revision.$field -isnot [int] -and $revision.$field -isnot [long]) {
            throw 'Schedule binding receipt requires integer counts and tolerance.'
        }
    }
    if ($revision.success -isnot [bool] -or -not $revision.success -or
        $revision.complete_slate_required -isnot [bool] -or -not $revision.complete_slate_required -or
        $revision.path -cne $enrichedPath -or $revision.source_odds_sha256 -cne $oddsDigest -or
        $revision.schedule_sha256 -cne $scheduleDigest -or $revision.operating_date -cne $OperatingDate -or
        $revision.match_tolerance_seconds -ne 120 -or $revision.provider_events -le 0 -or
        $revision.rows -lt $revision.provider_events -or
        $revision.provider_events -ne $revision.canonical_game_bindings -or
        $revision.canonical_game_bindings -ne $revision.eligible_official_games -or
        $revision.ambiguous_bindings -ne 0 -or $revision.unmatched_bindings -ne 0) {
        throw 'Schedule binding receipt does not prove the complete canonical slate.'
    }
    if ($revision.research_only -isnot [bool] -or -not $revision.research_only -or
        $revision.approval_status -cne 'not_approved' -or
        $revision.eligible_for_betting -isnot [bool] -or $revision.eligible_for_betting -or
        $revision.eligible_for_official_pick -isnot [bool] -or $revision.eligible_for_official_pick) {
        throw 'Schedule binding receipt crossed the research boundary.'
    }
    return (Get-RecoveryBoundFile -Path $revision.path -Sha256 $revision.sha256)
}
