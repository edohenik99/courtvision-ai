$ErrorActionPreference = "Stop"

$RepoPath = "C:\dev\Sport_Project1"
$SnapshotPath = Join-Path $RepoPath "data\theoddsapi\live_hr_snapshots"
$LogDirectory = Join-Path $SnapshotPath "automation_logs"
$RunLogPath = Join-Path $SnapshotPath "run_log.csv"
$MasterDataPath = Join-Path $SnapshotPath "live_hr_props_master.csv"
$Today = (Get-Date).Date

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$LogPath = Join-Path $LogDirectory ("live_hr_daily_auto_{0}.log" -f (Get-Date -Format "yyyyMMdd_HHmmss"))

function Test-ValueMatchesToday {
    param(
        [AllowNull()]
        [object]$Value
    )

    $Text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $false
    }

    $Text = $Text.Trim()
    if ($Text -match '^\d{4}-\d{2}-\d{2}$') {
        return $Text -eq $Today.ToString("yyyy-MM-dd")
    }

    $Parsed = [DateTimeOffset]::MinValue
    $Styles = [Globalization.DateTimeStyles]::AllowWhiteSpaces -bor [Globalization.DateTimeStyles]::AssumeLocal
    if (-not [DateTimeOffset]::TryParse(
        $Text,
        [Globalization.CultureInfo]::InvariantCulture,
        $Styles,
        [ref]$Parsed
    )) {
        return $false
    }

    return $Parsed.ToLocalTime().Date -eq $Today
}

function Get-CsvCollectionState {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string[]]$DateColumns,

        [switch]$RequireSuccess
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ Usable = $false; Found = $false }
    }

    try {
        $Rows = @(Import-Csv -LiteralPath $Path)
    }
    catch {
        Write-Warning "Could not read collection guard file '$Path': $($_.Exception.Message)"
        return [pscustomobject]@{ Usable = $false; Found = $false }
    }

    if ($Rows.Count -eq 0) {
        return [pscustomobject]@{ Usable = $false; Found = $false }
    }

    $Headers = @($Rows[0].PSObject.Properties.Name)
    $AvailableDateColumns = @($DateColumns | Where-Object { $Headers -contains $_ })
    if ($AvailableDateColumns.Count -eq 0) {
        return [pscustomobject]@{ Usable = $false; Found = $false }
    }

    $Usable = $false
    foreach ($Row in $Rows) {
        foreach ($Column in $AvailableDateColumns) {
            $Value = $Row.$Column
            if ([string]::IsNullOrWhiteSpace([string]$Value)) {
                continue
            }

            $Parsed = [DateTimeOffset]::MinValue
            $IsDateOnly = ([string]$Value).Trim() -match '^\d{4}-\d{2}-\d{2}$'
            $Styles = [Globalization.DateTimeStyles]::AllowWhiteSpaces -bor [Globalization.DateTimeStyles]::AssumeLocal
            $CanParse = $IsDateOnly -or [DateTimeOffset]::TryParse(
                ([string]$Value).Trim(),
                [Globalization.CultureInfo]::InvariantCulture,
                $Styles,
                [ref]$Parsed
            )
            if (-not $CanParse) {
                continue
            }

            $Usable = $true
            if (-not (Test-ValueMatchesToday -Value $Value)) {
                continue
            }

            if ($RequireSuccess -and (($Headers -notcontains "status") -or $Row.status -ne "success")) {
                continue
            }

            return [pscustomobject]@{ Usable = $true; Found = $true }
        }
    }

    return [pscustomobject]@{ Usable = $Usable; Found = $false }
}

function Test-CollectionExistsToday {
    $RunLogState = Get-CsvCollectionState `
        -Path $RunLogPath `
        -DateColumns @("run_date", "snapshot_time", "collection_date", "collected_at", "timestamp") `
        -RequireSuccess

    if ($RunLogState.Found) {
        Write-Host "Found today's successful collection in run_log.csv."
        return $true
    }

    $MasterState = Get-CsvCollectionState `
        -Path $MasterDataPath `
        -DateColumns @("snapshot_time", "run_date", "collection_date", "collected_at", "timestamp")

    if ($MasterState.Found) {
        Write-Host "Found today's collection in live_hr_props_master.csv."
        return $true
    }

    if (-not $RunLogState.Usable -and -not $MasterState.Usable) {
        Write-Host "No usable local collection date was found; collector is allowed to run."
    }
    else {
        Write-Host "No collection was found for local date $($Today.ToString('yyyy-MM-dd'))."
    }

    return $false
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$CommandArguments
    )

    Write-Host "> $Executable $($CommandArguments -join ' ')"
    & $Executable @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($CommandArguments -join ' ')"
    }
}

$ExitCode = 0
Start-Transcript -Path $LogPath -Append | Out-Null

try {
    Write-Host "CourtVision MLB Live HR daily automation started at $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')."
    Write-Host "Log file: $LogPath"
    Set-Location -LiteralPath $RepoPath

    Invoke-CheckedCommand -Executable "git" -CommandArguments @("checkout", "main")
    Invoke-CheckedCommand -Executable "git" -CommandArguments @("pull", "origin", "main")

    if (Test-CollectionExistsToday) {
        Write-Host "Collection already exists for today; skipping collector."
    }
    else {
        Invoke-CheckedCommand -Executable "python" -CommandArguments @(".\tools\theoddsapi_live_hr_collector.py", "--quiet")
    }

    Invoke-CheckedCommand -Executable "python" -CommandArguments @(".\tools\run_live_hr_daily_check.py")
    Write-Host "CourtVision MLB Live HR daily automation completed successfully."
}
catch {
    $ExitCode = 1
    Write-Error "CourtVision MLB Live HR daily automation failed: $($_.Exception.Message)" -ErrorAction Continue
}
finally {
    Stop-Transcript | Out-Null
}

exit $ExitCode
