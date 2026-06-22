# CourtVision Phase 3E: Historical Weather Ingestion Prototype

Date: 2026-06-19

Validation completed: 2026-06-20

## What was added

Phase 3E adds a narrow, local-file historical weather ingestion path for MLB
home-run research:

- `courtvision/sports/mlb/data/weather_ingestion.py`
  - local UTF-8 CSV reading and required-column validation;
  - immutable `MLBWeatherObservationRow` normalization;
  - safe optional numeric, date, and timezone-aware datetime parsing;
  - deterministic serialization and raw-row hashing;
  - explicit weather completeness warnings;
  - mapping into the existing Phase 2B `MLBWeatherContext`;
  - Phase 3B `MLBSourceManifest` generation and validation; and
  - explicit-only raw, normalized JSONL, and manifest writes.
- `scripts/mlb_ingest_weather.py`
  - local-file mode only;
  - dry-run by default; and
  - output only with `--write-output` and `--out-dir`.
- `tests/fixtures/mlb/weather_sample.csv`
  - three synthetic historical observations covering open, unknown, and closed
    roof labels plus missing optional and core weather values.
- `tests/test_mlb_weather_ingestion.py`
  - fixture, validation, serialization, context mapping, manifest, checksum,
    output, CLI, and no-network coverage.

## Prototype-only boundary

This phase provides historical weather ingestion and provenance contracts only.
It does not fetch a weather API, download a dataset, join weather to Retrosheet
or Statcast, calculate an environmental home-run feature, create training data,
build a model, or connect normalized rows to the MLB report pipeline.

Normalized rows are historical research artifacts. Mapping a row into
`MLBWeatherContext` does not make the observation a pregame forecast or change
its source type. The generated Phase 3B manifest retains its default-deny
safety fields and remains `not_approved`.

## Supported local fixture fields

Required columns (values for numeric weather fields may be empty):

- `game_date`
- `venue_name`
- `temperature`
- `wind_speed`
- `wind_direction`
- `source_name`
- `source_type`
- `collected_at`

Optional columns:

- `game_id`
- `event_start_time`
- `latitude`
- `longitude`
- `wind_out_to_field`
- `humidity`
- `precipitation`
- `roof_status`
- `as_of_date`

`source_type` must be `historical`, `public`, `manual`, or `sample`. All rows
in one input must have the same source name and source type so one manifest
cannot blur provenance classes.

## Normalized weather row schema

Each immutable `MLBWeatherObservationRow` contains:

- `sport = MLB`, `league = MLB`, source name, and source type;
- optional game ID, game date, optional event start, and venue;
- optional latitude and longitude;
- optional temperature, wind speed, raw wind direction, and raw
  wind-out-to-field label;
- optional humidity, precipitation, and raw roof-status label;
- optional as-of date and required collection timestamp;
- SHA-256 of the stable raw-row representation;
- computed data quality; and
- explicit warnings.

Serialization is deterministic. No probability, expected-value, unit, stake,
Kelly, bankroll, selection-tier, or recommendation field is present.

## Mapping to `MLBWeatherContext`

`weather_row_to_context(row)` maps only fields already supported by the Phase
2B contract: game ID, venue, temperature, wind, humidity, roof status, source
type, collection time, data quality, and warnings.

- The historical row's source type is preserved.
- The historical-not-forecast warning is preserved.
- Missing temperature and wind stay `None` and remain explicit in warnings.
- A missing game ID maps to an empty context identity, with a warning, rather
  than inventing an ID; existing context completeness checks can fail closed.
- `unknown` roof status stays `unknown`; no roof state is guessed.
- Precipitation, coordinates, game date, event time, and as-of date remain on
  the normalized observation because Phase 2B has no fields for them.

## Manifest behavior

Every successful parse creates one validated in-memory Phase 3B
`MLBSourceManifest` with:

- source name and uniform source type from the CSV;
- `data_domain = weather`;
- minimum and maximum input game dates;
- latest input collection timestamp;
- latest supplied as-of date, or `None` when none is supplied;
- local input path or explicitly written raw-copy path;
- optional explicitly written normalized path;
- schema version `1.0`;
- checksum from the Phase 3B `compute_file_sha256` helper;
- row count, file count, per-file provenance, generator, and warnings.

Phase 3B validation runs before requested output is written.

## No-network-by-default policy

The module has no HTTP client, URL builder, download function, remote provider,
or API credential path. It can read only a caller-supplied local CSV. The CLI
likewise exposes only local-file arguments. Automated coverage replaces
`urllib.request.urlopen` with a failing sentinel and confirms ingestion does
not call it.

Neither API nor CLI creates output in default/dry-run mode. API writes require
individual write flags plus an output directory. CLI writes require
`--write-output --out-dir ...`; a raw copy additionally requires
`--include-raw-copy`.

Example dry run:

```powershell
py -3.13 scripts/mlb_ingest_weather.py --input-csv tests/fixtures/mlb/weather_sample.csv --out-dir <temp/output> --dry-run
```

## Weather data-quality rules

- Missing required headers or required text values fail with row context.
- Dates must use ISO `YYYY-MM-DD` form.
- Collection timestamps and supplied event starts must be valid ISO datetimes
  with a UTC offset.
- Empty numeric values become `None`; malformed or non-finite numeric values
  fail rather than being coerced.
- Wind direction, wind-out label, and roof status are stripped but otherwise
  preserved as supplied.
- Missing temperature, wind speed, or wind direction produces an explicit
  warning and `data_quality = incomplete_historical`.
- Rows with those three core fields use
  `data_quality = complete_historical`; this describes field completeness only,
  not source accuracy or fitness for downstream use.
- Missing game ID is permitted but warned because a later, explicitly scoped
  venue/date match would be required.

## Historical-versus-forecast boundary

Every row carries the warning `Historical weather observation; not a pregame
forecast.` The ingestion path does not reinterpret `public`, `manual`, or
`sample` provenance as forecast timing. It does not contain forecast fetch,
issue-time, or forecast-horizon logic. Historical collection timestamps cannot
be used as evidence that a value was available before a game.

## Leakage boundaries

- A supplied as-of date before its game date is rejected.
- No game identity, roof state, wind direction, or missing measurement is
  inferred.
- No weather row is joined to outcome, event, batter, pitcher, Statcast, or
  Retrosheet data.
- No same-game, future-game, prior-game, rolling, or aggregated value is
  computed.
- No weather home-run boost, model feature, training row, probability, score,
  or decision output is generated.
- Rows and manifests remain historical/research artifacts.

## Compatibility confirmation

No model, training pipeline, MLB HR scoring weight, threshold, selection gate,
provider fetching, API authentication, data-source priority, odds normalization,
bankroll behavior, Kelly behavior, wager sizing, NBA runtime internal,
dashboard asset, existing run script, Retrosheet adapter, or Statcast adapter
was changed. Keyless MLB sample mode and Phase 0 through Phase 3D behavior are
preserved.

## Commands run and exact results

```powershell
py -3.13 -m py_compile courtvision/sports/mlb/data/weather_ingestion.py scripts/mlb_ingest_weather.py
py -3.13 -m pytest tests/test_mlb_weather_ingestion.py -q
```

Result: compilation succeeded; `14 passed in 0.31s`.

```powershell
py -3.13 -m pytest tests/test_mlb_weather_ingestion.py tests/test_mlb_retrosheet_ingestion.py tests/test_mlb_statcast_ingestion.py tests/test_mlb_data_manifest.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py tests/test_mlb_hr_research_pipeline.py tests/test_mlb_module.py tests/test_mlb_research_safety.py tests/test_sport_registry.py tests/test_normalized_odds_quote.py tests/test_provider_registry.py tests/test_research_artifact_contract.py tests/test_nba_backwards_compatibility.py -q
```

Result: `163 passed in 3.15s`.

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; the keyless sample report rendered three research-only
rows without a live API call.

```powershell
py -3.13 scripts/mlb_ingest_weather.py --input-csv tests/fixtures/mlb/weather_sample.csv --out-dir .pytest_tmp_weather_cli --dry-run
```

Result: exit code 0; three historical rows were reported, all output paths
were null, and the output directory did not exist afterward.

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: `2925 passed, 31 xfailed in 239.32s (0:03:59)`.

The first full-suite attempt used a 120-second command-runner limit and stopped
at 124 seconds before pytest emitted a quiet summary. The exact pytest command
above was immediately rerun with a longer runner limit and produced the passing
result.

No live API call or dataset download occurred during implementation or
validation.

## Next recommended step

With separate approval, validate this adapter against one deliberately chosen,
small, locally supplied historical weather export. Document unit conventions,
station-to-venue matching, observation timing, roof semantics, missingness,
and source-specific schema differences. Keep joins, feature computation,
training-data construction, modeling, scoring, and runtime integration out of
that validation step.
