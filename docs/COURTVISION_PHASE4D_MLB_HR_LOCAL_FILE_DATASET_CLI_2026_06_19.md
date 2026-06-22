# CourtVision Phase 4D: MLB HR Local-File Dataset CLI

Date: 2026-06-19

## What was added

Phase 4D adds `scripts/mlb_build_hr_local_dataset.py`, a dry-run-first command-line interface that builds MLB home-run batter-game historical research rows from repository fixtures or explicitly supplied local CSV files.

The CLI uses the existing Phase 3 Statcast, Retrosheet, weather, and ballpark ingestion modules. It passes their normalized rows to the existing Phase 4B source-row builder and runs the Phase 4C leakage audit on the resulting rows. It prints input counts, dataset counts, audit counts, approval status, missing-source warnings, and the first five rows.

Tests were added in `tests/test_mlb_build_hr_local_dataset.py` for keyless fixture execution, summary and audit output, dry-run behavior, explicit outputs, overwrite protection, missing and malformed paths, partial-input handling, default-deny artifacts, local-file mode, network isolation, and restricted human-facing language.

## Why this is local-file only

This phase is a controlled bridge from fixture-only construction to historical files already held by the user. The command has no fetch or download option and calls only local ingestion functions. It does not create a data directory and does not call a network function.

## Supported commands

Fixture mode:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures
```

Explicit local CSV mode:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv path\to\statcast.csv `
  --retrosheet-games-csv path\to\retrosheet_games.csv `
  --retrosheet-events-csv path\to\retrosheet_events.csv `
  --weather-csv path\to\weather.csv `
  --ballpark-csv path\to\ballpark_factors.csv
```

Optional outputs:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures `
  --output-csv C:\existing\directory\dataset.csv `
  --audit-json C:\existing\directory\audit.json `
  --metadata-json C:\existing\directory\metadata.json
```

Partial local inputs:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv path\to\statcast.csv `
  --allow-partial
```

## Dry-run and optional-output behavior

The default is an in-memory dry run. It prints a summary and up to five dataset rows but writes no files. Output files are created only for flags explicitly supplied by the caller. Output parent directories must already exist, so the CLI does not create a data directory or other directory tree.

All requested destinations are checked before any output is written. Existing files are refused unless `--overwrite` is present, and requested output paths must be distinct.

## Partial-input behavior

Without `--allow-partial`, local-file mode requires all five CSV arguments. With `--allow-partial`, each absent source is printed as a warning, available sources are parsed, and the builder receives empty rows for absent sources. Missing game, weather, venue, or ballpark context remains missing; the CLI does not fabricate it. The leakage audit still runs on every row that can be built.

A partial invocation can still fail safely if the supplied sources do not contain enough dated batter-game information for the Phase 4B builder contract.

## Leakage audit and default-deny behavior

Every successful build is passed to `audit_hr_batter_game_rows`. The console reports audit errors, warnings, and pass status. An audit JSON file is written only when `--audit-json` is explicitly supplied.

Dataset rows, dataset metadata, and audit reports retain `approval_status: not_approved` and the existing default-deny eligibility fields. Phase 4D does not grant production approval or alter any existing gate.

## Exact validation commands run

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures
py -3.13 scripts/mlb_inspect_fixture_stats.py
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample
py -3.13 -m py_compile scripts/mlb_build_hr_local_dataset.py tests/test_mlb_build_hr_local_dataset.py
$base = Join-Path $env:TEMP ('courtvision-phase4d-targeted-' + [guid]::NewGuid().ToString('N')); py -3.13 -m pytest tests/test_mlb_build_hr_local_dataset.py -q --basetemp=$base
py -3.13 -m pytest tests --basetemp=C:\Users\edohe\AppData\Local\Temp\courtvision-phase4d-final-8d214f1fcabe415687bb31f1f600e074 -q
```

The focused Phase 4D result was:

```text
9 passed in 0.39s
```

The final full-suite result was:

```text
3019 passed, 31 xfailed in 241.59s (0:04:01)
```

The fixture CLI built four rows, reported two training-eligible and two backtest-eligible historical rows, and completed the audit with zero errors and visible warnings. The fixture inspector and the keyless MLB sample report both exited successfully.

## Scope confirmation

No APIs were called and no data was downloaded. No model was built or trained. MLB HR scoring, thresholds, selection logic, bankroll or Kelly behavior, provider fetching, odds handling, and NBA runtime internals were not changed. Existing keyless MLB sample behavior was preserved. No generated files or runtime outputs were added.

## Next recommended step

Use the Phase 4D CLI against a small, user-provided historical CSV bundle in a temporary output directory, review join coverage and leakage warnings, and document any source-format mismatches before proposing another phase. Keep that exercise local, historical, and default-deny; model work and production promotion remain out of scope.
