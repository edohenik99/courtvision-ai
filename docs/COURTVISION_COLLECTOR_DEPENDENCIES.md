# CourtVision Collector Dependencies

Collector dependencies are optional and are never installed by a normal
CourtVision collector run. Install them only from the repository root with an
explicit command.

## Install approved dependencies

Direct pip commands:

```powershell
py -m pip install ".[collector-mlb]"
py -m pip install ".[collector-weather]"
py -m pip install ".[collector-all]"
```

The allowlisted bootstrap command provides the same three choices:

```powershell
py scripts/courtvision_collector_doctor.py --install collector-mlb
py scripts/courtvision_collector_doctor.py --install collector-weather
py scripts/courtvision_collector_doctor.py --install collector-all
```

The bootstrap rejects package names and dependency groups outside that
allowlist. It runs pip only when `--install` is explicitly supplied. Normal
collector runs never invoke pip.

## Run the dependency doctor

```powershell
py scripts/courtvision_collector_doctor.py
py scripts/courtvision_collector_doctor.py --json
```

The doctor reports installed versions, missing packages, and Statcast/weather
dependency readiness. "Available" means the required Python distributions are
installed; it does not claim that a new live acquisition mode is configured.
The doctor reads Python distribution metadata only: it does not import
collector packages, fetch sports data, or write files, caches, history, manual
data, runtime state, or outputs.

## Plan an MLB Statcast collection

Use dry-run first. This validates and prints a plan without importing
`pybaseball`, fetching Statcast, or creating the output directory:

```powershell
py scripts/courtvision_collect_sources.py --sport mlb --season 2025 --start-date 2025-06-01 --end-date 2025-06-07 --output-raw-dir C:\courtvision-raw --collection-id v2025-june-week1 --fetch-statcast --dry-run
```

Remove `--dry-run` only when the date range, destination, source terms, and
required source blockers have been reviewed. A live `--fetch-statcast` run
requires the `collector-mlb` group and performs source acquisition; the doctor
itself never does.

## Use a supplied Statcast CSV fallback

When live Statcast acquisition is unavailable or not approved, supply an
existing local CSV instead. `--statcast-csv` and `--fetch-statcast` are mutually
exclusive.

```powershell
py scripts/courtvision_collect_sources.py --sport mlb --season 2025 --start-date 2025-06-01 --end-date 2025-06-07 --output-raw-dir C:\courtvision-raw --collection-id v2025-june-week1-csv --statcast-csv C:\approved-inputs\statcast.csv --dry-run
```

The supplied file must exist and satisfy the collector's approved CSV
contract. Keep raw data outside git. Ballpark factors and licensed odds remain
required collector inputs. Weather remains a supplied Meteostat/NOAA archive;
this bootstrap does not add a live weather fetch command, relax source
contracts, or enable training, predictions, EV, Kelly, Elite, staking, betting,
or production gates.
