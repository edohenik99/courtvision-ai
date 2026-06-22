# CourtVision Phase 4B: Fixture HR Dataset Builder

Date: 2026-06-19  
Scope: fixture-backed MLB home-run batter-game dataset construction only

## What was added

Phase 4B adds `courtvision/sports/mlb/training/hr_dataset_builder.py` and focused
tests in `tests/test_mlb_hr_dataset_builder.py`.

The builder:

- accepts already-parsed Phase 3C Statcast rows;
- accepts already-parsed Phase 3D Retrosheet game and event rows;
- accepts already-parsed Phase 3E weather rows;
- accepts already-parsed Phase 3F ballpark rows;
- emits immutable Phase 4A `MLBHRBatterGameRow` values;
- emits validated `MLBHRDatasetMetadata` and `MLBHRDatasetBuildResult` values;
- records warnings, skipped source rows, missing-context counts, leakage status,
  and training/backtest eligibility counts;
- exposes optional CSV and metadata JSON writers that write only to an explicit
  existing destination directory and never write by default.

No command-line script was added. Keeping the API and tests focused avoids a
new run-script surface in this production-risky repository.

## Why this is fixture-only

`build_fixture_hr_batter_game_dataset()` requires the five existing local CSV
fixture names. It calls the existing local ingestion functions without output
flags. It has no download path, live provider call, production directory
default, model execution, or training execution.

The repository fixtures intentionally do not all describe the same games and
venues. Partial joins therefore remain partial: the builder emits explicit
missing-weather and missing-ballpark warnings and leaves values null rather
than manufacturing context.

## Join and row construction rules

The opportunity set is the union of Retrosheet batter events and Statcast
batter events. Events are grouped by normalized string `game_id`, `game_date`,
and batter/player ID. A missing game ID, date, or player ID prevents that source
row from becoming an opportunity and records a skipped-row diagnostic.

Retrosheet game context joins on `game_id` plus `game_date`. Retrosheet batter
events provide batting team and opponent when available. Statcast supplies
home/away identity and deterministic inning-half team context only when the
Retrosheet event is absent. Venue comes from the Retrosheet game; an exact-game
weather observation may supply a missing venue.

Weather first joins on `game_id` plus `game_date`. Only when that is absent does
it fall back to a unique normalized `venue_name` plus `game_date` match.
Ambiguous weather matches are rejected as context and warned. Ballpark context
joins on the existing deterministic normalized venue name. Duplicate normalized
ballpark inputs fail closed.

Phase 3B manifests have no dedicated manifest-ID field. For the fixture loader,
the builder preserves the stable identity available from each manifest as
`source_name:checksum`. The lower-level builder also accepts caller-supplied
source manifest IDs unchanged.

## Outcome labels

An observed batter-game has `label_available=True`. `hit_hr_today` is true when
either Retrosheet or Statcast marks at least one observed event as a home run.
`home_run_count` is the maximum source-specific HR count, not the sum, so the
same event represented by both sources is not double-counted. A disagreement is
warned. `label_source` records `retrosheet`, `statcast`, or
`retrosheet+statcast`.

Retrosheet game status is authoritative for completion. An absent Retrosheet
game has unknown completion status even when Statcast supplies an event.

## Same-game leakage controls

Same-game Statcast data is used only for batter-game identity, label creation,
and cross-source label checks. Launch speed, launch angle, barrel state,
distance, handedness, pitch type, and all other same-game Statcast values are
not copied into Phase 4A pregame feature fields.

Rolling hitter and pitcher features remain null. Weather and static ballpark
context are the only attached pregame feature fields in this phase.

When weather supplies an event start time, `feature_as_of` is a conservative
same-date midnight cutoff with matching timezone semantics and must be strictly
earlier than the start. The Phase 4A assertion is run during dataset validation.
Without an event start time, `feature_as_of` stays null, leakage status remains
`not_checked`, a warning is recorded, and eligibility fails closed.

Outcome fields remain in the Phase 4A label namespace and are not copied into
the feature namespace.

## Missing data and eligibility

Missing optional weather or ballpark context does not block row construction.
It produces explicit warnings and null feature values. Missing event start time
also warns and prevents leakage approval. Missing required batter-game identity
blocks row creation and is recorded in `skipped_rows`.

Training and backtest eligibility require all of the following:

- an explicitly completed Retrosheet game;
- an available observed outcome label;
- a passed pregame feature timestamp check;
- valid required Phase 4A row fields.

Postponed and suspended games are ineligible. Unknown or absent game status is
ineligible. Training and backtest flags are descriptive research-dataset gates;
this phase does not execute training or backtesting.

## Default-deny behavior

Every row, metadata object, and build result remains:

- `eligible_for_betting=False`;
- `kelly_eligible=False`;
- `approval_status="not_approved"`.

No stake, unit, expected-value, fair-probability, recommendation, bankroll, or
selection-tier field was added.

## Validation performed

Commands run exactly:

```powershell
py -3.13 -m py_compile courtvision/sports/mlb/training/hr_dataset_builder.py
py -3.13 -m pytest tests/test_mlb_hr_dataset_builder.py tests/test_mlb_hr_dataset_schema.py tests/test_mlb_statcast_ingestion.py tests/test_mlb_retrosheet_ingestion.py tests/test_mlb_weather_ingestion.py tests/test_mlb_ballpark_factor_ingestion.py -q
py -3.13 -m ruff check courtvision/sports/mlb/training/hr_dataset_builder.py courtvision/sports/mlb/training/__init__.py tests/test_mlb_hr_dataset_builder.py
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
py -3.13 -m pytest tests/test_mlb_hr_dataset_builder.py tests/test_mlb_hr_dataset_schema.py -q
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Results:

- Python compilation passed.
- Targeted Phase 3C through Phase 4B tests: `95 passed in 0.89s`.
- Ruff was unavailable in the Python 3.13 environment: `No module named ruff`.
- The keyless MLB sample report completed successfully and printed three
  research-only sample rows.
- Final builder/schema check after the CSV-header edge-case adjustment:
  `36 passed in 0.37s`.
- Final full suite: `2980 passed, 31 xfailed in 244.83s (0:04:04)`.

The first full-suite invocation was interrupted by the command runner's
two-minute timeout while tests were still executing. The exact same command was
rerun with sufficient time and produced the successful result above.

## Scope confirmation

No real data was downloaded, read, joined, or written. No fixture was modified.
No model or historical training process was built or started. No MLB HR scoring
weight, selection gate, production promotion, bankroll behavior, Kelly behavior,
provider behavior, NBA runtime internal, dashboard, scheduled entrypoint, or
environment/credential file was changed.

## Next recommended step

After explicit approval of a later phase, define a fixture-only pregame feature
snapshot contract that can prove rolling windows use observations strictly
before each game. Keep feature calculation separate from Phase 4B labels and do
not begin model training until that leakage boundary has its own tests and
review.
