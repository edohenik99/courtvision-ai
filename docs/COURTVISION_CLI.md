# CourtVision CLI v1

The CourtVision CLI is a thin entrypoint over the existing collector dependency
doctor and sport-agnostic raw collection framework. It does not train models or
enable predictions, EV, Kelly, Elite selection, staking, betting, or production
gates.

## Install locally

From the repository root, install CourtVision in editable mode:

```powershell
py -m pip install -e .
```

To install the optional MLB collector dependencies at the same time:

```powershell
py -m pip install -e ".[collector-mlb]"
```

Raw data should stay outside git. `--output-raw-dir` defaults to
`./courtvision-raw`, but an explicit external directory is recommended for a
real collection.

## Run the dependency doctor

```powershell
courtvision doctor
courtvision doctor --json
```

The doctor reads installed-package metadata only. It does not import collector
packages, fetch sports data, or write operational files.

## Run an MLB dry-run

The shortest season-wide plan is:

```powershell
courtvision collect mlb --season 2025 --dry-run
```

An explicit date range and raw-data destination can also be supplied:

```powershell
courtvision collect mlb --season 2025 --start-date 2025-06-01 --end-date 2025-06-07 --output-raw-dir C:\courtvision-raw --fetch-statcast --dry-run
```

Even with `--fetch-statcast`, dry-run only validates the plan; it does not call
Statcast or create the output directory. Missing required ballpark-factor and
licensed-odds inputs are reported as blockers.

## Collect MLB historical weather (Collector v1.3.2)

Meteostat weather collection requires a local Retrosheet game log and an
approved stadium mapping CSV. The mapping must contain `park_id`, `latitude`,
`longitude`, and `timezone` (an IANA timezone such as `America/New_York`). An
optional `stadium_name`, `elevation_m`, and `roof_type` may also be supplied.

```powershell
courtvision collect mlb --season 2025 --start-date 2025-04-01 --end-date 2025-04-30 --retrosheet-path C:\approved-inputs\gl2025.txt --fetch-weather --weather-provider meteostat --stadium-map-path C:\approved-inputs\retrosheet_stadiums.csv --output-raw-dir C:\courtvision-raw --collection-id v2025-april-weather --dry-run
```

Remove `--dry-run` only after reviewing the plan. A live run writes an
immutable hourly weather CSV, `weather_diagnostics.csv`, and
`weather_missing_report.csv`. The manifest records each file hash plus missing
counts, rate, and reason counts. Every
Retrosheet park ID in the requested range must have a mapping. Headered
Retrosheet game-info files use `starttime` when present; legacy `glYYYY.txt`
logs use documented 13:00 local (day) or 19:00 local (night/unknown) reference
times and record that approximation as a manifest warning.

For each game, the collector tries nearby stations in distance order until one
returns hourly observations. It attempts at most five stations by default; use
`--max-station-attempts` to set a different positive limit. Diagnostics and the
missing report retain the attempted and selected station provenance.

## Fail-closed sports in v1

NBA, NFL, NHL, and WNBA are registered commands so the interface is stable, but
their collection adapters are intentionally registry stubs. Their approved
source contracts and acquisition adapters are not implemented yet, so these
commands return a clear fail-closed error and write nothing:

```powershell
courtvision collect nba --season 2025 --dry-run
courtvision collect nfl --season 2025 --dry-run
courtvision collect nhl --season 2025 --dry-run
courtvision collect wnba --season 2025 --dry-run
```

This boundary prevents an unsupported provider, scraper, or unlicensed source
from being selected implicitly.
