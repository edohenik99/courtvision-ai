# CourtVision Phase 4A: MLB HR Batter-Game Dataset Schema

Date: 2026-06-19

Validation completed: 2026-06-21

## What was added

Phase 4A adds a schema-and-contract boundary for future historical MLB home-run
research rows:

- `courtvision/sports/mlb/training/hr_dataset_schema.py`
  - immutable `MLBHRBatterGameRow`, `MLBHRDatasetMetadata`, and
    `MLBHRDatasetManifest` structures;
  - immutable validation results and a schema-specific validation error;
  - explicit identity, pregame-feature, odds-context, outcome-label, and
    provenance field groups;
  - row, metadata, and manifest validation;
  - deterministic row IDs and JSON/CSV-ready serialization; and
  - schema-level leakage checks and default-deny approval fields.
- `courtvision/sports/mlb/training/__init__.py`
  - the public exports for the Phase 4A contracts.
- `tests/test_mlb_hr_dataset_schema.py`
  - 22 focused tests covering immutability, validation, leakage boundaries,
    default-deny behavior, metadata/manifest consistency, and serialization.

## Why this phase is schema-only

The module declares the row shape future phases must populate. It performs no
file reads or writes, data acquisition, provider calls, source joins, feature
calculation, label calculation, dataset building, backtesting, model training,
prediction, scoring, selection, or runtime integration.

This keeps the leakage and provenance contract reviewable before any builder
can combine Statcast, Retrosheet, weather, ballpark, lineup, pitcher, or odds
sources. The manifest describes a dataset contract but does not materialize
rows.

## Canonical row schema

`MLBHRBatterGameRow` represents exactly one batter and one game. It is a frozen,
slotted dataclass with a stable flat column order for future JSON and CSV use.
Its fields are divided into five exported groups:

- Identity: MLB sport and league, schema/dataset versions, row/game/date/start
  identity, doubleheader number, batter/team/game identity, venue, batting
  order, lineup status, and probable-pitcher identity/status.
- Pregame features: nullable hitter rolling/to-date values, pitcher
  rolling/to-date values and pitch mix, handedness/platoon context, weather,
  roof, ballpark factors, and altitude.
- Odds context: nullable sportsbook/provider identity, market availability,
  quoted odds and implied market probability, collection/as-of timestamps, and
  a pregame-freshness flag.
- Outcome labels: nullable HR indicator/count, plate appearances, game
  completion, label source/availability, and label as-of timestamp.
- Provenance and safety: feature as-of time, source manifest IDs, source types,
  data quality, warnings, missing required fields, leakage status, research
  eligibility flags, and default-deny approval fields.

Odds are nullable market context only. Their presence does not approve a row,
selection, wager, sizing decision, or production use.

## Metadata and manifest schema

`MLBHRDatasetMetadata` contains dataset ID, MLB sport/league, `home_run` market
type, schema version, generation timestamp, date range, source manifest IDs,
row count, generator identity, historical/research mode, warnings, and the same
default-deny approval fields.

`MLBHRDatasetManifest` wraps validated metadata and records the row IDs plus the
exact canonical row, feature, and label field declarations. Validation requires
its row-ID count to match metadata and rejects duplicate row IDs or altered
schema declarations. It contains no rows, joined data, model artifact, or
runtime configuration.

## Feature versus label separation

`PREGAME_FEATURE_FIELD_NAMES` and `OUTCOME_LABEL_FIELD_NAMES` are disjoint and
validated as separate namespaces. `row_feature_dict()` serializes only pregame
features and nullable odds context; `row_label_dict()` serializes only outcome
labels. Labels remain top-level canonical CSV columns but are never included in
the feature serializer.

`assert_no_label_leakage()` rejects any outcome-label key embedded in the
structured/JSON pitcher pitch-mix feature namespace. Outcome labels are
historical results only and are not computed in this phase.

## Required and optional fields

A valid row requires:

- `sport = MLB`, `league = MLB`, and a non-empty schema version;
- non-empty row ID and game ID;
- a valid ISO-compatible game date;
- non-empty player ID and player name;
- a supported lineup status; and
- a supported probable-pitcher status.

Every pregame feature, odds-context value, and outcome label is nullable. Missing
optional pregame features do not invalidate a row; validation reports them as
warnings and the row carries an explicit data-quality value. Missing
`feature_as_of` or source-manifest provenance is also reported as a validation
warning. A non-empty `missing_required_fields` declaration invalidates the row.

Metadata requires a dataset ID, schema version, valid generation timestamp,
valid date range, non-negative row count, generator identity, MLB sport/league,
`home_run` market type, and historical or research mode.

## Leakage-control and validation rules

Validation fails closed when:

- sport or league is not MLB;
- a required row, game, player, schema, name, or date value is missing;
- a date or timestamp has an invalid ISO representation;
- lineup, probable-pitcher, dataset-mode, or leakage status is unsupported;
- `feature_as_of` is equal to or after a known `event_start_time`;
- feature and event timestamps cannot be safely compared;
- an outcome-label key is embedded in a feature namespace;
- required-field missingness is declared;
- a numeric schema value is malformed or non-finite;
- the canonical row schema contains a forbidden decision/sizing field; or
- a row, metadata object, or manifest claims production, betting, or Kelly
  approval.

`dataset_row_id()` hashes the stable MLB/game/player/market identity and is
deterministic. Row, metadata, and manifest JSON uses sorted keys. CSV mappings
use canonical dataclass field order and stable JSON encoding for collection or
mapping cells.

## Default-deny approval behavior

Rows, metadata, and manifests default to:

- `approval_status = not_approved`;
- `eligible_for_betting = False`; and
- `kelly_eligible = False`.

Validation rejects any other value. The row has no stake, unit, expected-value,
fair-probability, bankroll, Kelly-fraction, selection-tier, or recommendation
field. `implied_probability` is retained only as the explicitly requested
nullable market-odds context.

## Compatibility and intentional non-changes

No dataset join, builder, historical training run, model, live API call, dataset
download, score, threshold, selection gate, odds normalization, bankroll/Kelly
behavior, wager sizing, result/ROI logic, recalibration, dashboard/UI asset,
provider-fetching path, run script, scheduled entrypoint, NBA runtime internal,
or existing Phase 0 through Phase 3F behavior was changed.

MLB remains historical/research-only. Keyless MLB sample mode and existing NBA
behavior remain intact.

## Commands run and exact results

```powershell
py -3.13 -m pytest tests\test_mlb_hr_dataset_schema.py -q
```

Result: `22 passed in 0.17s` on the final post-documentation rerun.

```powershell
py -3.13 -m pytest tests\test_mlb_hr_dataset_schema.py tests\test_mlb_ballpark_factor_ingestion.py tests\test_mlb_weather_ingestion.py tests\test_mlb_retrosheet_ingestion.py tests\test_mlb_statcast_ingestion.py tests\test_mlb_data_manifest.py tests\test_mlb_fixture_provider.py tests\test_mlb_provider_contracts.py tests\test_mlb_research_context.py tests\test_mlb_hr_research_pipeline.py tests\test_mlb_module.py tests\test_mlb_research_safety.py tests\test_sport_registry.py tests\test_normalized_odds_quote.py tests\test_provider_registry.py tests\test_research_artifact_contract.py tests\test_nba_backwards_compatibility.py -q
```

Result: `204 passed in 3.42s`.

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; three research-only sample rows rendered without a key or
live API call.

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: `2966 passed, 31 xfailed in 246.40s (0:04:06)`.

No live API call, dataset download, or dataset join was performed. The Phase 4A
module has no file-writing path; validation artifacts were limited to pytest's
temporary directory.

## Next recommended step

With separate approval, define a Phase 4B source-to-column mapping and temporal
join specification using these canonical fields. That specification should name
the source key, granularity, as-of rule, missingness policy, and leakage test for
every field before implementing any dataset builder or historical join.
