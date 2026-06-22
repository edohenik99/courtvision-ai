# CourtVision Phase 3C: Statcast Ingestion Prototype

Date: 2026-06-19  
Validation completed: 2026-06-20

## What was added

Phase 3C adds a narrow Baseball Savant / Statcast historical ingestion path:

- `courtvision/sports/mlb/data/statcast_ingestion.py`
  - guarded Statcast query parameter and URL builders;
  - local UTF-8 CSV reading and required-column validation;
  - immutable `MLBStatcastEventRow` normalization;
  - deterministic row hashing and JSON serialization;
  - Phase 3B `MLBSourceManifest` generation and validation;
  - explicit-only raw, normalized JSONL, and manifest writes; and
  - a default-deny download function with a 31-day guard.
- `scripts/mlb_ingest_statcast.py`
  - local-file mode only;
  - dry-run by default; and
  - normalized/manifest output only with `--write-output` and `--out-dir`.
- `tests/fixtures/mlb/statcast_sample.csv`
  - two small synthetic fixture rows: one home run and one non-home run.
- `tests/test_mlb_statcast_ingestion.py`
  - fixture parsing, validation, normalization, manifest, output, CLI, and
    mocked/default-deny network coverage.

## Prototype-only boundary

This phase creates historical ingestion and provenance contracts only. The
normalized rows are not connected to the MLB report pipeline or any NBA
runtime. No training set, model, probability, expected-value calculation,
selection classification, stake sizing, or runtime promotion is produced.

The ingestion manifest remains `not_approved`, with `eligible_for_betting` and
`kelly_eligible` both false under the existing Phase 3B safety contract.

## Supported local CSV fields

Required columns:

- `game_date`
- `game_pk` or `game_id`
- `player_name`
- `batter`
- `pitcher`
- `events`
- `description`
- `stand`
- `p_throws`
- `home_team`
- `away_team`
- `inning`
- `inning_topbot`
- `pitch_type`
- `launch_speed`
- `launch_angle`
- `hit_distance_sc`
- `bb_type`

Optional columns used when present:

- `estimated_ba_using_speedangle`
- `estimated_woba_using_speedangle`
- `woba_value`
- `barrel`

`barrel` is read only when supplied. Phase 3C does not derive it.

## Normalized row schema

Each immutable row contains source identity, game and player identifiers,
event label, handedness, teams, inning context, pitch type, launch metrics,
batted-ball type, optional expected statistics, supplied barrel label, raw-row
SHA-256, `as_of_date`, and `collected_at`.

The home-run label is exactly `events == "home_run"`. Empty optional numeric
values become `None`; invalid non-empty numeric values fail with row context.
Serialization uses sorted-key compact JSON and ISO date/time strings.

## Manifest behavior

Every successful local parse creates an in-memory Phase 3B
`MLBSourceManifest` with:

- `source_name = baseball_savant_statcast`;
- `source_type = historical`;
- `data_domain = statcast`;
- minimum and maximum fixture `game_date` values;
- caller-supplied or UTC collection time;
- caller-supplied `as_of_date`, or the latest input game date;
- local raw path and optional normalized output path;
- schema version `1.0`;
- exact input-file SHA-256 from `compute_file_sha256`;
- row and file counts, generator identity, and research warnings.

The Phase 3B validation helper runs before any requested output is written.
An `as_of_date` before the latest input event is rejected.

## Network and output policy

Parsing local CSV never calls the network. URL construction also performs no
request. `download_statcast_csv` rejects calls unless `allow_network=True`,
requires start and end dates plus an output path, requires the output parent to
already exist, and refuses an inclusive range over 31 days unless
`confirm_large_range=True` is supplied.

The CLI deliberately exposes no network mode. Neither the API nor CLI creates
output in dry-run mode. API file writes require individual write flags and an
output directory. CLI output requires `--write-output --out-dir ...`; a raw
copy additionally requires `--include-raw-copy`.

Example dry run:

```powershell
py -3.13 scripts/mlb_ingest_statcast.py --input-csv tests/fixtures/mlb/statcast_sample.csv --out-dir <temp/output> --dry-run
```

## Data-quality and leakage rules

- Missing required headers fail before row normalization.
- Missing/invalid required values identify the CSV row and field.
- Dates must use ISO `YYYY-MM-DD` form.
- Optional numeric blanks remain `None`; malformed values are not coerced.
- Only supplied event data is normalized.
- No rolling, prior-game, same-game future, or other feature is computed.
- No model or training artifact is created.
- All normalized data and manifests remain historical research artifacts.

## Compatibility confirmation

No scoring weights, selection gates, provider priority, odds normalization,
bankroll behavior, wager sizing, NBA runtime internals, or existing run scripts
were changed. The existing keyless MLB sample provider and report behavior are
unchanged.

## Commands run and exact results

```powershell
py -3.13 -m py_compile courtvision\sports\mlb\data\statcast_ingestion.py scripts\mlb_ingest_statcast.py tests\test_mlb_statcast_ingestion.py
py -3.13 -m pytest tests\test_mlb_statcast_ingestion.py tests\test_mlb_data_manifest.py -q --basetemp=.pytest_tmp_phase3c_targeted
```

Result: `32 passed in 0.46s` before the final as-of-date guard test was added.

```powershell
py -3.13 scripts/mlb_ingest_statcast.py --input-csv tests/fixtures/mlb/statcast_sample.csv --out-dir .pytest_tmp_phase3c_cli_should_not_exist --dry-run
py -3.13 -m pytest tests/test_mlb_statcast_ingestion.py tests/test_mlb_data_manifest.py -q --basetemp=.pytest_tmp_phase3c_targeted_2
```

Result: dry-run reported two rows and created no output directory;
`33 passed in 0.44s`.

```powershell
py -3.13 -m pytest tests/test_mlb_hr_research_pipeline.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py tests/test_research_artifact_contract.py tests/test_provider_registry.py tests/test_normalized_odds_quote.py tests/test_sport_registry.py tests/test_nba_backwards_compatibility.py tests/test_mlb_hr_adapters.py tests/test_mlb_hr_prop_engine.py -q --basetemp=.pytest_tmp_phase3c_cross_phase
```

Result: `109 passed in 2.67s`.

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: the sample report completed without a key or network dependency;
`2896 passed, 31 xfailed in 244.37s (0:04:04)`.

No live API call or large dataset download occurred during validation.

## Next recommended step

Phase 3C-next should test a deliberately selected, small real Statcast export
outside the automated suite, compare its headers and null patterns with this
contract, and record any schema drift before considering partitioned ingestion
or leakage-safe historical feature work.
