[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ScratchRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scratchPath = (Resolve-Path -LiteralPath $ScratchRoot).Path.TrimEnd([char[]]'\/')
$testRoot = Join-Path $scratchPath ('powershell_contracts_' + [guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testRoot)
$sourceRoot = Split-Path -Parent $PSScriptRoot
$sourceAutomation = Join-Path (Join-Path $sourceRoot 'automation') 'mlb_hr_recovery'
$commonPath = Join-Path $sourceAutomation 'recovery_common.ps1'
$completionPath = Join-Path $sourceAutomation 'recovery_completion.ps1'
$sourceHashes = @{}
foreach ($path in @($commonPath, $completionPath)) {
    $sourceHashes[$path] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
}
. $commonPath
. $completionPath

$script:Cases = [Collections.Generic.List[object]]::new()
$script:TaskMutationCalls = 0
$script:TaskReadCalls = 0
$script:Tasks = @()
$script:TaskReadFailure = $false
$script:PythonMode = 'valid'
$script:PythonCalls = [Collections.Generic.List[object]]::new()
$script:EnrichmentOverrides = @{}
$script:EnrichmentOmittedField = $null

function Assert-Test {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw "ASSERTION FAILED: $Message" }
}

function Assert-Throws {
    param([scriptblock]$Body, [string]$Pattern)
    $caught = $null
    try { & $Body | Out-Null } catch { $caught = $_ }
    if ($null -eq $caught) { throw 'ASSERTION FAILED: expected rejection, received success.' }
    if ($Pattern -and $caught.Exception.Message -notmatch $Pattern) {
        throw "ASSERTION FAILED: rejection did not match '$Pattern': $($caught.Exception.Message)"
    }
}

function Test-ContractCase {
    param([string]$Name, [scriptblock]$Body)
    try {
        & $Body | Out-Null
        $script:Cases.Add([pscustomobject]@{ name = $Name; status = 'pass' })
    }
    catch {
        $script:Cases.Add([pscustomobject]@{
            name = $Name; status = 'fail'; error = $_.Exception.Message
            location = $_.ScriptStackTrace
        })
    }
}

function Write-NewFixture {
    param([string]$Path, [string]$Text)
    $full = [IO.Path]::GetFullPath($Path)
    $prefix = $scratchPath + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Fixture write escaped the supplied scratch root.'
    }
    [void][IO.Directory]::CreateDirectory((Split-Path -Parent $full))
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text)
    $stream = [IO.File]::Open($full, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
}

# These definitions shadow all task mutation APIs for this test process.
function Register-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Set-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Enable-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Disable-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Start-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Stop-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Unregister-ScheduledTask { $script:TaskMutationCalls++; throw 'TASK MUTATION FORBIDDEN' }
function Get-ScheduledTask {
    [CmdletBinding()]param([string]$TaskPath)
    $script:TaskReadCalls++
    Assert-Test ($TaskPath -ceq ([string][char]92)) 'Task lookup must retain the exact root path.'
    if ($script:TaskReadFailure) { throw 'mock task access denied' }
    return $script:Tasks
}

function Invoke-RecoveryTestPython {
    $arguments = @($args | ForEach-Object { [string]$_ })
    $script:PythonCalls.Add($arguments)
    $global:LASTEXITCODE = 0
    if ($arguments -contains 'enrich-odds') {
        Assert-Test ($arguments -contains '--require-complete-slate') 'Canonical enrichment must require the entire eligible slate.'
        if ($script:PythonMode -eq 'enrichment_failure') {
            $global:LASTEXITCODE = 1
            return '{"success":false}'
        }
        $values = @{}
        foreach ($name in @('odds-path', 'odds-sha256', 'schedule-path', 'schedule-sha256', 'operating-date', 'output')) {
            $index = [Array]::IndexOf($arguments, '--' + $name)
            Assert-Test ($index -ge 0 -and $index + 1 -lt $arguments.Count) "Missing enrichment argument: $name"
            $values[$name] = $arguments[$index + 1]
        }
        Assert-Test ((Get-FileHash -LiteralPath $values['odds-path'] -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $values['odds-sha256']) 'Provider source digest was not forwarded.'
        Assert-Test ((Get-FileHash -LiteralPath $values['schedule-path'] -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $values['schedule-sha256']) 'Preserved schedule digest was not forwarded.'
        Write-NewFixture -Path $values['output'] -Text "event_id,commence_time,game_pk,game_type,official_commence_time_utc,schedule_start_drift_seconds`nsynthetic-event,2030-08-06T23:01:00Z,12345,R,2030-08-06T23:00:00Z,60`n"
        $receipt = @{
            success = $true; path = $values['output']
            sha256 = (Get-FileHash -LiteralPath $values['output'] -Algorithm SHA256).Hash.ToLowerInvariant()
            rows = 1; source_odds_sha256 = $values['odds-sha256']; schedule_sha256 = $values['schedule-sha256']
            operating_date = $values['operating-date']; match_tolerance_seconds = 120; complete_slate_required = $true
            provider_events = 1; canonical_game_bindings = 1; eligible_official_games = 1
            ambiguous_bindings = 0; unmatched_bindings = 0
            research_only = $true; approval_status = 'not_approved'; eligible_for_betting = $false; eligible_for_official_pick = $false
        }
        foreach ($key in $script:EnrichmentOverrides.Keys) { $receipt[$key] = $script:EnrichmentOverrides[$key] }
        if ($script:EnrichmentOmittedField) { $receipt.Remove($script:EnrichmentOmittedField) }
        return ($receipt | ConvertTo-Json -Compress -Depth 5)
    }
    if ($arguments -contains 'validate-publication') {
        $index = [Array]::IndexOf($arguments, '--predictions-csv')
        if ($script:PythonMode -eq 'publication_failure' -or $index -lt 0 -or
            -not (Test-Path -LiteralPath $arguments[$index + 1] -PathType Leaf)) {
            $global:LASTEXITCODE = 2
            return '{"success":false}'
        }
        return '{"success":true}'
    }
    if ($arguments -notcontains 'report-prospective-health') {
        throw 'Unexpected Python operation in shared PowerShell contract test.'
    }
    if ($script:PythonMode -eq 'health_failure') {
        $global:LASTEXITCODE = 2
        return '{"success":false}'
    }
    $returnedControl = if ($script:PythonMode -eq 'wrong_health_control') { 'wrong-control' } else { $controlId }
    $integrity = if ($script:PythonMode -eq 'invalid_health') { 'findings' } else { 'valid' }
    return (@{
        schema_version = 'mlb-hr-prospective-health-v1'
        control = @{ control_id = $returnedControl; artifact_integrity_status = $integrity }
    } | ConvertTo-Json -Compress -Depth 5)
}

# Explicit synthetic shared state; Initialize-RecoveryRuntime is never invoked.
$repo = Join-Path $testRoot 'repository with spaces'
$automationRoot = Join-Path (Join-Path $repo 'automation') 'mlb_hr_recovery'
$trialRoot = Join-Path $testRoot 'disposable_trial'
$evidenceRoot = Join-Path $testRoot 'disposable_evidence'
$controlId = 'mlb-hr-control-v1-' + ('a' * 20)
$expectedCommit = 'b' * 40
$expectedControlManifestSha256 = 'c' * 64
$ConfigurationSha256 = 'd' * 64
$ConfigurationPath = Join-Path $testRoot 'explicit configuration.json'
$OperatingDate = '2030-08-06'
$cutoverOperatingDate = '2030-08-01'
$powershellExecutable = Join-Path $testRoot 'system-powershell.fixture'
$pythonExecutable = 'Invoke-RecoveryTestPython'
$predictionRunner = Join-Path $automationRoot 'run_mlb_hr_dynamic_prediction.ps1'
$closingRunner = Join-Path $automationRoot 'run_mlb_hr_dynamic_closing.ps1'
$shadowRunner = Join-Path $automationRoot 'run_mlb_hr_shadow_cluster_closing.ps1'
foreach ($path in @($ConfigurationPath, $powershellExecutable, $predictionRunner, $closingRunner, $shadowRunner)) {
    Write-NewFixture -Path $path -Text '# Disposable shared-contract fixture; never execute.'
}
$taskName = "CourtVision MLB HR Dynamic Prediction 20300806 $controlId"
$rootTaskPath = [string][char]92
$taskArguments = New-RecoveryTaskArguments -Runner $predictionRunner -OperatingDate $OperatingDate

function Invoke-IntendedTaskCheck {
    param([string]$Name = $taskName, [string]$Execute = $powershellExecutable,
          [string]$Arguments = $taskArguments, [string]$WorkingDirectory = $repo,
          [string]$TaskPath = $rootTaskPath)
    return Assert-RecoveryNewTask -TaskName $Name -TaskPath $TaskPath -Execute $Execute `
        -Arguments $Arguments -WorkingDirectory $WorkingDirectory
}

Test-ContractCase 'exact quoted dated control configuration arguments' {
    $expected = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -OperatingDate "{1}" -ConfigurationPath "{2}" -ConfigurationSha256 "{3}"' -f $predictionRunner, $OperatingDate, $ConfigurationPath, $ConfigurationSha256
    Assert-Test ($taskArguments -ceq $expected) 'Task argument quoting or identity changed.'
    Assert-Test ((Invoke-IntendedTaskCheck) -ceq 'MISSING_NO_ACTION') 'Absent task did not produce read-only no action.'
}
Test-ContractCase 'canonical closing action is independently accepted' {
    $closingArgs = New-RecoveryTaskArguments -Runner $closingRunner -OperatingDate $OperatingDate
    Assert-Test ((Invoke-IntendedTaskCheck -Name "CourtVision MLB HR Dynamic Closing 20300806 $controlId" -Arguments $closingArgs) -ceq 'MISSING_NO_ACTION') 'Canonical closing identity failed.'
}
Test-ContractCase 'disabled collision remains unchanged and disabled' {
    $script:Tasks = @([pscustomobject]@{ TaskName = $taskName; TaskPath = $rootTaskPath; State = 'Disabled'; Enabled = $false })
    $before = $script:Tasks | ConvertTo-Json -Compress
    try {
        Assert-Throws { Invoke-IntendedTaskCheck } 'collision'
        Assert-Test (($script:Tasks | ConvertTo-Json -Compress) -ceq $before) 'Disabled task definition changed.'
    } finally { $script:Tasks = @() }
}
Test-ContractCase 'unavailable task query never means missing' {
    $script:TaskReadFailure = $true
    try { Assert-Throws { Invoke-IntendedTaskCheck } 'access denied' }
    finally { $script:TaskReadFailure = $false }
}
Test-ContractCase 'stale dated identity rejects' {
    Assert-Throws { Invoke-IntendedTaskCheck -Name "CourtVision MLB HR Dynamic Prediction 20300805 $controlId" } 'date'
}
Test-ContractCase 'wrong control identity rejects' {
    Assert-Throws { Invoke-IntendedTaskCheck -Name ('CourtVision MLB HR Dynamic Prediction 20300806 mlb-hr-control-v1-' + ('e' * 20)) } 'identity'
}
Test-ContractCase 'shadow runner arguments reject' {
    Assert-Throws { New-RecoveryTaskArguments -Runner $shadowRunner -OperatingDate $OperatingDate } 'canonical'
    $shadowArgs = $taskArguments.Replace($predictionRunner, $shadowRunner)
    Assert-Throws { Invoke-IntendedTaskCheck -Arguments $shadowArgs } 'arguments'
}
Test-ContractCase 'wrong executable rejects even when absolute' {
    Assert-Throws { Invoke-IntendedTaskCheck -Execute (Join-Path $testRoot 'other-powershell.fixture') } 'identity'
}
Test-ContractCase 'altered arguments retaining valid configuration digest reject' {
    Assert-Throws { Invoke-IntendedTaskCheck -Arguments ($taskArguments + ' -ExtraArgument true') } 'arguments'
}
Test-ContractCase 'wrong action date rejects' {
    Assert-Throws { Invoke-IntendedTaskCheck -Arguments $taskArguments.Replace('2030-08-06', '2030-08-05') } 'arguments'
}
Test-ContractCase 'wrong working directory and task path reject' {
    Assert-Throws { Invoke-IntendedTaskCheck -WorkingDirectory $testRoot } 'identity'
    Assert-Throws { Invoke-IntendedTaskCheck -TaskPath ($rootTaskPath + 'legacy' + $rootTaskPath) } 'identity'
}
Test-ContractCase 'legacy expired operating date rejects' {
    Assert-Throws { Assert-RecoveryOperatingDate -OperatingDate '1970-01-01' } 'current Toronto date'
}
Test-ContractCase 'local digest checks succeed and reject changed digest or missing input' {
    $bound = Join-Path $testRoot 'bound-input.txt'
    Write-NewFixture -Path $bound -Text 'retained fixture evidence'
    $sha = (Get-FileHash -LiteralPath $bound -Algorithm SHA256).Hash.ToLowerInvariant()
    Assert-Test ((Get-RecoveryBoundFile -Path $bound -Sha256 $sha).FullName -ceq $bound) 'Exact local digest failed.'
    Assert-Throws { Get-RecoveryBoundFile -Path $bound -Sha256 ('0' * 64) } 'digest mismatch'
    Assert-Throws { Get-RecoveryBoundFile -Path (Join-Path $testRoot 'missing-input.txt') -Sha256 $sha } 'does not exist|Cannot find'
}

$enrichmentOdds = Join-Path $testRoot 'preserved-provider.csv'
Write-NewFixture -Path $enrichmentOdds -Text "event_id,commence_time,home_team,away_team,market,side,point`nsynthetic-event,2030-08-06T23:01:00Z,Toronto Blue Jays,New York Yankees,batter_home_runs,Over,0.5`n"
$enrichmentSourceSha = (Get-FileHash -LiteralPath $enrichmentOdds -Algorithm SHA256).Hash
$enrichmentSchedule = @{
    dates = @(@{ date = $OperatingDate; games = @(@{
        gamePk = 12345; gameType = 'R'; gameDate = '2030-08-06T23:00:00Z'
        teams = @{ home = @{ team = @{ name = 'Toronto Blue Jays' } }; away = @{ team = @{ name = 'New York Yankees' } } }
    }) })
}
Test-ContractCase 'canonical Python drift receipt is accepted without a second time matcher' {
    $result = New-RecoveryEnrichedOdds -OddsPath $enrichmentOdds -Schedule $enrichmentSchedule
    $rows = @(Import-Csv -LiteralPath $result.FullName)
    Assert-Test ($rows.Count -eq 1 -and $rows[0].commence_time -ceq '2030-08-06T23:01:00Z') 'Provider timestamp changed.'
    Assert-Test ($rows[0].official_commence_time_utc -ceq '2030-08-06T23:00:00Z' -and $rows[0].schedule_start_drift_seconds -ceq '60') 'Authoritative time evidence was not retained.'
    Assert-Test ((Get-FileHash -LiteralPath $enrichmentOdds -Algorithm SHA256).Hash -ceq $enrichmentSourceSha) 'Preserved source bytes changed.'
}
Test-ContractCase 'Python identity rejection cannot produce an accepted revision' {
    $script:PythonMode = 'enrichment_failure'
    try { Assert-Throws { New-RecoveryEnrichedOdds -OddsPath $enrichmentOdds -Schedule $enrichmentSchedule } 'identity binding failed' }
    finally { $script:PythonMode = 'valid' }
}
foreach ($field in @('success', 'source_odds_sha256', 'schedule_sha256', 'complete_slate_required',
    'provider_events', 'eligible_official_games', 'match_tolerance_seconds', 'research_only')) {
    Test-ContractCase "enrichment receipt rejects missing $field" {
        $script:EnrichmentOmittedField = $field
        try { Assert-Throws { New-RecoveryEnrichedOdds -OddsPath $enrichmentOdds -Schedule $enrichmentSchedule } 'receipt is incomplete' }
        finally { $script:EnrichmentOmittedField = $null }
    }
}
foreach ($case in @(
    @{ field = 'success'; value = $false },
    @{ field = 'success'; value = 'true' },
    @{ field = 'path'; value = $enrichmentOdds },
    @{ field = 'source_odds_sha256'; value = ('0' * 64) },
    @{ field = 'schedule_sha256'; value = ('0' * 64) },
    @{ field = 'operating_date'; value = '2030-08-05' },
    @{ field = 'match_tolerance_seconds'; value = 121 },
    @{ field = 'match_tolerance_seconds'; value = '120' },
    @{ field = 'complete_slate_required'; value = $false },
    @{ field = 'complete_slate_required'; value = 'true' },
    @{ field = 'rows'; value = 0 },
    @{ field = 'provider_events'; value = 0 },
    @{ field = 'canonical_game_bindings'; value = 2 },
    @{ field = 'eligible_official_games'; value = 2 },
    @{ field = 'ambiguous_bindings'; value = 1 },
    @{ field = 'unmatched_bindings'; value = 1 },
    @{ field = 'unmatched_bindings'; value = $null },
    @{ field = 'research_only'; value = $false },
    @{ field = 'approval_status'; value = 'approved' },
    @{ field = 'eligible_for_betting'; value = $true },
    @{ field = 'eligible_for_official_pick'; value = $true }
)) {
    Test-ContractCase "enrichment receipt rejects invalid $($case.field)=$($case.value)" {
        $script:EnrichmentOverrides = @{}
        $script:EnrichmentOverrides[$case.field] = $case.value
        try { Assert-Throws { New-RecoveryEnrichedOdds -OddsPath $enrichmentOdds -Schedule $enrichmentSchedule } 'Schedule binding receipt' }
        finally { $script:EnrichmentOverrides = @{} }
    }
}
Test-ContractCase 'enriched artifact digest is verified before returning its path' {
    $script:EnrichmentOverrides = @{ sha256 = ('0' * 64) }
    try { Assert-Throws { New-RecoveryEnrichedOdds -OddsPath $enrichmentOdds -Schedule $enrichmentSchedule } 'digest mismatch' }
    finally { $script:EnrichmentOverrides = @{} }
}

function New-CompletionFixture {
    param([string]$Name, [string]$Kind = 'postfinalizer')
    $script:controlDir = Join-Path (Join-Path $trialRoot $Name) $controlId
    $evidenceName = if ($Kind -eq 'closing') { 'closing_lines.csv' } else { 'prospective_ledger.csv' }
    $evidence = Join-Path $controlDir $evidenceName
    Write-NewFixture -Path $evidence -Text "prediction_id,status`nfixture-1,valid`n"
    $predictions = Join-Path $controlDir 'predictions.csv'
    Write-NewFixture -Path $predictions -Text "prediction_id`nfixture-1`n"
    $marker = Join-Path $controlDir ($Kind + '-completion.json')
    Write-RecoveryCompletionMarker -Path $marker -OperatingDate $OperatingDate -Kind $Kind `
        -Details @{ predictions_csv = $predictions; research_only = $true }
    return [pscustomobject]@{ Marker = $marker; Evidence = $evidence; Predictions = $predictions; Kind = $Kind }
}

foreach ($kind in @('postfinalizer', 'finalizer', 'closing')) {
    Test-ContractCase "valid immutable $kind marker and CreateNew protection" {
        $fixture = New-CompletionFixture -Name ('valid-' + $kind) -Kind $kind
        $before = (Get-FileHash -LiteralPath $fixture.Marker -Algorithm SHA256).Hash
        $marker = Read-RecoveryCompletionMarker -Path $fixture.Marker -OperatingDate $OperatingDate -Kind $kind
        Assert-Test ($marker.control_id -ceq $controlId -and $marker.kind -ceq $kind) 'Completion identity was not retained.'
        Assert-Throws { Write-RecoveryCompletionMarker -Path $fixture.Marker -OperatingDate $OperatingDate -Kind $kind -Details @{} } 'exists|exist'
        Assert-Test ((Get-FileHash -LiteralPath $fixture.Marker -Algorithm SHA256).Hash -ceq $before) 'CreateNew attempt changed prior marker.'
    }
}
foreach ($field in @('control_id', 'operating_date', 'configuration_sha256', 'repository_commit', 'control_manifest_sha256')) {
    Test-ContractCase "completion rejects wrong $field" {
        $fixture = New-CompletionFixture -Name ('wrong-' + $field)
        $marker = Get-Content -LiteralPath $fixture.Marker -Raw | ConvertFrom-Json
        $marker.$field = 'mismatched-fixture-value'
        $changed = Join-Path $controlDir 'mismatched-marker.json'
        Write-NewFixture -Path $changed -Text ($marker | ConvertTo-Json -Depth 10)
        Assert-Throws { Read-RecoveryCompletionMarker -Path $changed -OperatingDate $OperatingDate -Kind 'postfinalizer' } 'intended date/control/configuration'
    }
}
Test-ContractCase 'completion rejects corrupt JSON' {
    $fixture = New-CompletionFixture -Name 'corrupt-json'
    $invalid = Join-Path $controlDir 'corrupt-marker.json'
    Write-NewFixture -Path $invalid -Text '{malformed'
    Assert-Throws { Read-RecoveryCompletionMarker -Path $invalid -OperatingDate $OperatingDate -Kind 'postfinalizer' } 'JSON|Json|parse|Unexpected|Invalid object passed in'
}
Test-ContractCase 'completion rejects corrupted evidence prefix' {
    $fixture = New-CompletionFixture -Name 'corrupt-prefix'
    $stream = [IO.File]::Open($fixture.Evidence, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try { $stream.WriteByte([byte][char]'X') } finally { $stream.Dispose() }
    Assert-Throws { Read-RecoveryCompletionMarker -Path $fixture.Marker -OperatingDate $OperatingDate -Kind 'postfinalizer' } 'changed since completion'
}
Test-ContractCase 'completion permits append-only canonical evidence' {
    $fixture = New-CompletionFixture -Name 'append-only'
    [IO.File]::AppendAllText($fixture.Evidence, "fixture-2,valid`n", [Text.UTF8Encoding]::new($false))
    $marker = Read-RecoveryCompletionMarker -Path $fixture.Marker -OperatingDate $OperatingDate -Kind 'postfinalizer'
    Assert-Test ($marker.evidence_size -lt (Get-Item -LiteralPath $fixture.Evidence).Length) 'Appended evidence was not tested.'
}
Test-ContractCase 'completion rejects missing canonical evidence without deletion' {
    $fixture = New-CompletionFixture -Name 'missing-evidence-source'
    $script:controlDir = Join-Path (Join-Path $trialRoot 'missing-evidence-target') $controlId
    [void][IO.Directory]::CreateDirectory($controlDir)
    Assert-Throws { Read-RecoveryCompletionMarker -Path $fixture.Marker -OperatingDate $OperatingDate -Kind 'postfinalizer' } 'Could not find|does not exist|Cannot find|not find'
}
Test-ContractCase 'closing completion rejects missing published evidence' {
    $fixture = New-CompletionFixture -Name 'missing-publication' -Kind 'closing'
    $marker = Get-Content -LiteralPath $fixture.Marker -Raw | ConvertFrom-Json
    $marker.predictions_csv = Join-Path $controlDir 'missing-predictions.csv'
    $changed = Join-Path $controlDir 'missing-publication-marker.json'
    Write-NewFixture -Path $changed -Text ($marker | ConvertTo-Json -Depth 10)
    Assert-Throws { Read-RecoveryCompletionMarker -Path $changed -OperatingDate $OperatingDate -Kind 'closing' } 'Published artifact'
}
foreach ($mode in @('health_failure', 'wrong_health_control', 'invalid_health')) {
    Test-ContractCase "completion fails closed on $mode" {
        $fixture = New-CompletionFixture -Name $mode
        $script:PythonMode = $mode
        try { Assert-Throws { Read-RecoveryCompletionMarker -Path $fixture.Marker -OperatingDate $OperatingDate -Kind 'postfinalizer' } 'health failed|integrity is not valid' }
        finally { $script:PythonMode = 'valid' }
    }
}
Test-ContractCase 'shared source bytes and every task remain unchanged' {
    Assert-Test ($script:TaskMutationCalls -eq 0) 'A task mutation API was invoked.'
    Assert-Test ($script:TaskReadCalls -gt 0) 'No actual shared task lookup was exercised.'
    Assert-Test ($script:PythonCalls.Count -gt 0) 'No actual shared health invocation was exercised.'
    Assert-Test ((Get-FileHash -LiteralPath $enrichmentOdds -Algorithm SHA256).Hash -ceq $enrichmentSourceSha) 'Enrichment changed the preserved provider source.'
    foreach ($path in @($commonPath, $completionPath)) {
        Assert-Test ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ceq $sourceHashes[$path]) 'Shared source file changed.'
    }
}

$failed = @($script:Cases | Where-Object { $_.status -eq 'fail' })
$reportPath = Join-Path $testRoot 'powershell_contract_results.json'
$report = [ordered]@{
    schema_version = 'mlb-recovery-powershell-contract-tests-v1'
    status = $(if ($failed.Count -eq 0) { 'PASS' } else { 'FAIL' })
    passed = $script:Cases.Count - $failed.Count; failed = $failed.Count
    task_mutation_calls = $script:TaskMutationCalls; task_read_calls = $script:TaskReadCalls
    python_calls_mocked = $script:PythonCalls.Count; production_runtime_executed = $false
    source_hashes = $sourceHashes; generated_root = $testRoot
    cases = $script:Cases.ToArray()
}
Write-NewFixture -Path $reportPath -Text ($report | ConvertTo-Json -Depth 20)
[pscustomobject]@{ status = $report.status; passed = $report.passed; failed = $report.failed; report = $reportPath } | ConvertTo-Json -Compress
if ($failed.Count -gt 0 -or $script:TaskMutationCalls -ne 0) {
    throw "PowerShell recovery contracts failed; inspect $reportPath"
}
