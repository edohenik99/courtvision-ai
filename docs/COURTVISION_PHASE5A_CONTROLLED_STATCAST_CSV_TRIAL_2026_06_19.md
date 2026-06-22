# CourtVision Phase 5A: Controlled Statcast CSV Trial

## Scope

Phase 5A adds an isolated `--statcast-trial` mode to
`scripts/mlb_build_hr_local_dataset.py`. It validates one user-provided local
Baseball Savant / Statcast CSV through the existing Phase 3 Statcast ingestion
code and reports coverage without constructing an HR batter-game dataset.

This is a local-file bridge from repository fixtures to a small real Statcast
CSV. The user selects the file. The command contains no API call, download,
web request, or scraping behavior. It does not invoke the existing guarded
Statcast download helper.

## Commands

User-provided local CSV trial:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv path\to\statcast.csv --statcast-trial
```

Repository fixture trial:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv tests\fixtures\mlb\statcast_sample.csv --statcast-trial
```

Optional trial pack:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv path\to\statcast.csv --statcast-trial --output-dir path\to\statcast_trial_pack
```

`--allow-partial` is not required. Partial-input behavior is intrinsic to the
explicit trial mode.

## Trial behavior

The command loads only `--statcast-csv` and uses
`ingest_local_statcast_csv`. It prints:

- parsed Statcast row count;
- detected date range;
- unique game count;
- unique batter count;
- HR event count from the parsed event field;
- missing required Statcast column warning count;
- the first five parsed rows using a narrow preview field set; and
- the explicit partial-context and research-only safety notices.

The preview contains only parsed source values. It does not fabricate teams,
venues, weather, ballpark factors, labels, odds, or additional pitcher
context.

Malformed inputs continue to fail through the existing strict Statcast
ingestion contract. Missing required headers are also emitted as clear column
warnings before that failure.

## What is and is not built

Without `--output-dir`, the trial writes no files.

With `--output-dir`, a successful Statcast-only trial writes exactly:

```text
statcast_preview.json
source_manifest.json
build_summary.txt
```

It does not create `dataset.csv`, `metadata.json`, or `audit.json`. The trial
branch returns before Retrosheet, weather, and ballpark ingestion and before
the HR dataset builder or leakage audit.

The source manifest records:

- `source_name = statcast`;
- `source_type = local_file`;
- resolved path, existence, byte size, and SHA-256;
- parsed row count and detected date range;
- unique game and batter counts;
- HR event count;
- successful-load state; and
- ingestion, column, and partial-context warnings.

Full HR batter-game rows require Retrosheet game/event context because those
files supply the controlled game identity and event-label pairing needed for
the historical dataset contract. Phase 5A therefore says both:

```text
Dataset rows require Retrosheet game/event context
dataset rows not built without Retrosheet labels
```

It does not infer or manufacture those labels from partial Statcast context.

## Default-deny safeguards

- `--statcast-trial` requires an explicit `--statcast-csv` path.
- Other local source inputs are rejected in trial mode.
- Dataset, audit, and metadata output flags are rejected in trial mode.
- Only the three trial-pack destinations are preflighted and written.
- Existing trial-pack targets require explicit `--overwrite`.
- A missing path fails before ingestion.
- A malformed CSV fails through the strict existing parser.
- Human-facing trial output is limited to historical research, local trial,
  partial-context, and not-production-approved language.

## Validation

Pre-change focused baseline:

```powershell
$base = Join-Path $env:TEMP ('courtvision-phase5a-baseline-' + [guid]::NewGuid().ToString('N'))
py -3.13 -m pytest tests\test_mlb_build_hr_local_dataset.py tests\test_mlb_statcast_ingestion.py -q --basetemp=$base
```

Result: `26 passed in 0.72s`.

Post-change focused validation:

```powershell
$base = Join-Path $env:TEMP ('courtvision-phase5a-targeted-' + [guid]::NewGuid().ToString('N'))
py -3.13 -m pytest tests\test_mlb_build_hr_local_dataset.py tests\test_mlb_statcast_ingestion.py -q --basetemp=$base
```

Result: `31 passed in 0.92s`.

Required runtime commands:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures
py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv tests\fixtures\mlb\statcast_sample.csv --statcast-trial
py -3.13 scripts/mlb_inspect_fixture_stats.py
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample
```

All four returned exit code `0`. The trial reported 2 parsed rows, a
2025-04-01 through 2025-04-02 date range, 2 unique games, 2 unique batters,
1 HR event, 0 missing-column warnings, and 0 HR batter-game rows.

Full suite with a fresh absolute temp path:

```powershell
$base = Join-Path $env:TEMP ('courtvision-phase5a-full-' + [guid]::NewGuid().ToString('N'))
py -3.13 -m pytest tests --basetemp=$base -q
```

Successful run result:

```text
3030 passed, 31 xfailed in 240.87s (0:04:00)
```

An earlier invocation of the same full-suite command was terminated by its
120-second command wrapper before pytest completed; it was rerun unchanged
with a longer wrapper timeout and the fresh path shown by the successful run.

## Change confirmation

Phase 5A changed only the local dataset CLI, its focused tests, and this
document. It made no API, download, scraping, model, training, MLB HR scoring,
selection-threshold, bankroll, wager-sizing, provider, authentication, NBA
runtime, dashboard, workflow, or production-promotion changes. Existing
fixture/local-dataset behavior, keyless MLB sample behavior, and NBA
compatibility remain covered by the passing full suite.

## Recommended next step

Phase 5B: a controlled Retrosheet label-pairing trial using explicit local
files, with point-in-time identity checks and no production promotion.
