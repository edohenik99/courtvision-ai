# CourtVision Phase 4E: MLB HR Dataset Reproducibility Pack

Date: 2026-06-19

## What was added

Phase 4E extends `scripts/mlb_build_hr_local_dataset.py` with:

```powershell
--output-dir path\to\build_pack
```

An explicitly requested output directory receives a complete traceable build pack for the fixture or local-file inputs. The existing Phase 4D dry run and explicit `--output-csv`, `--audit-json`, and `--metadata-json` flags remain supported.

Phase 4E tests were added to `tests/test_mlb_build_hr_local_dataset.py` for pack contents, source provenance, SHA-256 values, parsed counts, overwrite protection, default-deny artifacts, deterministic fixture output, local-file source typing, flag compatibility, and restricted human-facing language.

## Why reproducibility comes first

Real historical CSVs can vary in coverage, schema, provenance, and collection time. A reproducibility pack records the exact source paths and bytes used before any future analysis is considered. This gives later work a stable answer to “which inputs produced these rows?” and keeps missing context and audit warnings visible.

This phase remains local-file only. It does not fetch or download data and does not build or train a model.

## Output pack contents

`--output-dir` writes exactly:

```text
dataset.csv
metadata.json
audit.json
source_manifest.json
build_summary.txt
```

- `dataset.csv` contains the validated Phase 4B batter-game rows.
- `metadata.json` contains the existing dataset ID, schema, date range, source IDs, row count, build time, and default-deny fields.
- `audit.json` contains the Phase 4C leakage audit and default-deny fields.
- `source_manifest.json` records file-level provenance and the dataset/audit identifiers.
- `build_summary.txt` gives a human-readable count, audit, output-path, and safety summary.

## Source manifest fields

Each supplied input has one manifest entry. Fixture mode has five entries; local-file mode has one entry for every provided CSV.

Each entry includes:

- `source_name`
- `source_type` (`fixture` or `local_file`)
- absolute `path`
- `file_exists`
- `byte_size`
- `sha256`
- `parsed_row_count`
- `required_or_optional`
- `loaded_successfully`
- `warnings`

The top-level manifest also includes its manifest version, build mode and timestamp, dataset schema/version/ID, dataset row count, audit result, and default-deny safety fields.

## Checksum and determinism behavior

SHA-256 is computed directly from each input file. Retrosheet game and event CSVs receive separate hashes and parsed counts even though they share one ingestion result.

Fixture Statcast and Retrosheet collection time is pinned to the fixture provenance date so repeated fixture builds produce byte-identical `dataset.csv` files. Build metadata retains the actual per-run timestamp. Audit JSON is deterministic for identical fixture rows apart from `checked_at`; stable metadata fields such as dataset ID and schema version remain identical.

Local-file builds retain their actual build timestamp because it is part of their traceability record.

## Commands

Fixture pack:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures `
  --output-dir C:\temp\courtvision_mlb_fixture_pack
```

Local CSV pack:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv path\to\statcast.csv `
  --retrosheet-games-csv path\to\retrosheet_games.csv `
  --retrosheet-events-csv path\to\retrosheet_events.csv `
  --weather-csv path\to\weather.csv `
  --ballpark-csv path\to\ballpark_factors.csv `
  --output-dir C:\temp\courtvision_mlb_local_pack
```

Overwrite an existing pack’s target files:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures `
  --output-dir C:\temp\courtvision_mlb_fixture_pack `
  --overwrite
```

## Dry-run, overwrite, and flag compatibility

Without `--output-dir` or an explicit Phase 4D output flag, the command remains an in-memory dry run and writes nothing.

If the output directory does not exist, the CLI creates it. If any target pack file already exists, the command refuses the build before writing unless `--overwrite` is supplied. Unrelated files in an existing output directory are left untouched.

`--output-dir` is intentionally mutually exclusive with `--output-csv`, `--audit-json`, and `--metadata-json`. This prevents one invocation from splitting related artifacts across ambiguous destinations. The Phase 4D explicit flags continue to work when `--output-dir` is absent.

## Partial inputs

Phase 4D partial-input behavior is preserved. `--allow-partial` is still required when fewer than all five local paths are provided. Only provided files receive source-manifest entries, those entries are marked optional, missing context is not fabricated, and the audit still runs when the available dated inputs can build a dataset result.

## Default-deny behavior

Dataset rows, metadata, audit output, source manifest, and the human-readable summary all retain `approval_status = not_approved`. Existing machine-readable safety fields remain false. The summary explicitly states `historical research only` and `not production approved`.

No production promotion or behavioral gate change occurs in this phase.

## Exact validation commands run

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures --output-dir C:\Users\edohe\AppData\Local\Temp\courtvision-phase4e-pack-3d1abe96b8af4006941737f5f06fb2c7
py -3.13 scripts/mlb_inspect_fixture_stats.py
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample
py -3.13 -m py_compile scripts/mlb_build_hr_local_dataset.py tests/test_mlb_build_hr_local_dataset.py
$base = Join-Path $env:TEMP ('courtvision-phase4e-targeted-' + [guid]::NewGuid().ToString('N')); py -3.13 -m pytest tests/test_mlb_build_hr_local_dataset.py -q --basetemp=$base
$base = Join-Path $env:TEMP ('courtvision-phase4e-related-' + [guid]::NewGuid().ToString('N')); py -3.13 -m pytest tests/test_mlb_build_hr_local_dataset.py tests/test_mlb_inspect_fixture_stats.py tests/test_mlb_hr_leakage_audit.py tests/test_mlb_hr_dataset_builder.py tests/test_mlb_hr_dataset_schema.py tests/test_mlb_statcast_ingestion.py tests/test_mlb_retrosheet_ingestion.py tests/test_mlb_weather_ingestion.py tests/test_mlb_ballpark_factor_ingestion.py tests/test_nba_backwards_compatibility.py -q --basetemp=$base
py -3.13 -m pytest tests --basetemp=C:\Users\edohe\AppData\Local\Temp\courtvision-phase4e-full-3fccf8bce0f746c182953c190cdf1311 -q
```

Exact results:

```text
Focused Phase 4E: 15 passed in 0.70s
Related Phase 3/4 and NBA compatibility: 142 passed in 3.24s
Full suite: 3025 passed, 31 xfailed in 246.11s (0:04:06)
```

The generated fixture pack contained all five target files, five source entries with valid 64-character SHA-256 hashes, the expected parsed counts, four dataset rows, and a passing audit with zero errors and 16 visible warnings.

## Scope confirmation

No live APIs were called and no data was downloaded. No model was built or trained. MLB HR scoring, thresholds, selection gates, provider fetching, odds behavior, bankroll or Kelly behavior, and NBA runtime internals were not changed. Existing keyless MLB sample mode and all Phase 0 through Phase 4D behavior were preserved. No generated pack was written inside the repository.

## Next recommended step

Phase 5A: run a controlled trial with a user-provided Statcast CSV in a pytest or user-selected temporary directory, produce a Phase 4E reproducibility pack, and review schema compatibility, coverage, and audit warnings before accepting any broader historical input set.
