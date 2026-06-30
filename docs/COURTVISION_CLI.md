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
