# CourtVision Phase 3F: Static MLB Ballpark Factor Table Prototype

Date: 2026-06-19

Validation completed: 2026-06-21

## What was added

Phase 3F adds a narrow, local-file ballpark-factor table path for MLB home-run
research:

- `courtvision/sports/mlb/data/ballpark_factors.py`
  - local UTF-8 CSV reading and required-column validation;
  - immutable `MLBBallparkFactorRow` normalization;
  - strict numeric, date, timestamp, source-type, and completeness checks;
  - deterministic serialization, raw-row hashing, venue normalization, and
    venue lookup;
  - explicit duplicate and ambiguous-venue rejection;
  - mapping into the existing Phase 2B `MLBBallparkContext`;
  - Phase 3B `MLBSourceManifest` generation and validation; and
  - explicit-only raw, normalized JSONL, and manifest writes.
- `scripts/mlb_ingest_ballpark_factors.py`
  - local-file mode only;
  - dry-run by default; and
  - output only with `--write-output` and `--out-dir`.
- `tests/fixtures/mlb/ballpark_factors_sample.csv`
  - three synthetic static venues, including a row with missing optional
    handedness and roof fields.
- `tests/test_mlb_ballpark_factor_ingestion.py`
  - fixture, validation, lookup, ambiguity, serialization, context mapping,
    manifest, checksum, output, CLI, and no-network coverage.

The Phase 3B source-type enum now accepts `static`, and
`MLBBallparkContext` has one backward-compatible optional `roof_type` field so
the supplied roof capability remains visible instead of being discarded or
inferred.

## Prototype-only boundary

This phase provides static factor ingestion, provenance, and context mapping
only. It does not download a ballpark dataset, call a provider, join to
Statcast, Retrosheet, weather, lineups, odds, or outcomes, calculate an HR
boost feature, create training data, build a model, or connect normalized rows
to the MLB report pipeline.

The fixture values are synthetic contract examples. A normalized row or
`MLBBallparkContext` is not evidence that a venue value is accurate, current,
or suitable for runtime use. Phase 3B manifests retain their default-deny
safety fields and remain `not_approved`.

## Supported local fixture fields

Required columns:

- `venue_name`
- `park_factor_hr`
- `source_name`
- `source_type`
- `data_version`
- `collected_at`

Optional columns:

- `team`
- `handedness_factor_lhb`
- `handedness_factor_rhb`
- `altitude`
- `left_field_distance`
- `center_field_distance`
- `right_field_distance`
- `roof_type`
- `as_of_date`

The `park_factor_hr` header and a non-empty, positive value are required for a
complete loaded row. Optional numeric cells may be empty and normalize to
`None`. `source_type` must be `static`, `manual`, or `sample`. Every row in one
input must use the same source name, source type, and data version so one
manifest cannot blur provenance or version boundaries.

## Normalized ballpark row schema

Each immutable `MLBBallparkFactorRow` contains:

- `sport = MLB`, `league = MLB`, source name, and source type;
- venue name and optional team;
- required complete-row HR park factor;
- optional LHB and RHB factors;
- optional altitude;
- optional left-, center-, and right-field distances;
- optional roof type;
- data version, optional as-of date, and required collection timestamp;
- SHA-256 of the stable raw-row representation;
- computed static-data completeness; and
- explicit research and missing-field warnings.

Serialization is deterministic. No probability, expected-value, unit, stake,
Kelly, bankroll, selection-tier, or recommendation field is present.

## Mapping to `MLBBallparkContext`

`ballpark_row_to_context(row)` maps only supported research context fields:

- HR factor remains the supplied value, including explicit `None` for an
  independently constructed incomplete row;
- supplied LHB and RHB values become `LHB` and `RHB` mapping entries;
- supplied left-, center-, and right-field distances become `LF`, `CF`, and
  `RF` entries;
- altitude and roof type remain exactly supplied or `None`;
- source type, data version, data quality, and warnings remain visible; and
- context mode remains `research`.

No handedness value, dimension, altitude, roof type, or missing HR factor is
guessed.

## Manifest behavior

Every successful ingestion creates one validated in-memory Phase 3B
`MLBSourceManifest` with:

- source name, uniform source type, and uniform data version from the CSV;
- `data_domain = ballpark`;
- latest collection timestamp;
- latest supplied as-of date, or `None` when absent;
- local input path or explicitly written raw-copy path;
- optional explicitly written normalized path;
- schema version `1.0`;
- checksum from the Phase 3B `compute_file_sha256` helper;
- row count, file count, per-file provenance, generator, and warnings.

Phase 3B validation runs before requested output is written. Adding `static` to
the accepted Phase 3B source types is additive; existing source types and
validation gates are unchanged.

## No-network-by-default policy

The module has no HTTP client, URL builder, download function, remote provider,
or credential path. It reads only a caller-supplied local CSV. The CLI exposes
only local-file arguments. Automated coverage replaces
`urllib.request.urlopen` with a failing sentinel and confirms ingestion does
not call it.

Neither the API nor CLI creates output in default/dry-run mode. API writes
require individual write flags plus an output directory. CLI writes require
`--write-output --out-dir ...`; a raw copy additionally requires
`--include-raw-copy`.

Example dry run:

```powershell
py -3.13 scripts/mlb_ingest_ballpark_factors.py --input-csv tests/fixtures/mlb/ballpark_factors_sample.csv --out-dir <temp/output> --dry-run
```

## Venue matching rules

- Venue comparison uses Unicode NFKC normalization, surrounding whitespace
  removal, case folding, deterministic `&` to `and` handling, punctuation
  removal, and whitespace collapse.
- No nickname, team, city, sponsor, relocation, or historical-name alias is
  inferred.
- Unknown venues return `None`.
- Two rows that normalize to the same venue key fail clearly before lookup.
- Version-specific duplicate selection is not part of this phase. One local
  file must contain one unambiguous row per normalized venue and one uniform
  data version.

## Data-quality rules

- Missing required headers or required text values fail with row context.
- Collection timestamps must be valid ISO datetimes with a UTC offset.
- As-of dates, when supplied, must use ISO `YYYY-MM-DD` form.
- Empty optional numeric values become `None`; malformed or non-finite values
  fail rather than being coerced.
- The HR factor, handedness factors, and supplied dimensions must be positive.
- Missing optional values produce explicit warnings and
  `data_quality = partial_static`; a fully populated row uses
  `complete_static`.
- `static`, `manual`, and `sample` labels remain unchanged through row,
  context, and manifest serialization.
- Completeness describes populated fields only, not accuracy or runtime
  fitness.

## Leakage boundaries

- No game date, player, pitcher, weather, outcome, event, or odds data is read.
- No current venue name, roof state, dimension, handedness split, or missing
  value is inferred.
- No same-game, future-game, prior-game, rolling, or aggregated value is
  computed.
- No ballpark HR boost, model feature, training row, probability, score, or
  decision output is generated.
- Rows and manifests remain static research artifacts.

## Compatibility confirmation

No model, training pipeline, MLB HR scoring weight, threshold, selection gate,
provider fetching, API authentication, data-source priority, odds
normalization, bankroll behavior, Kelly behavior, wager sizing, NBA runtime
internal, dashboard asset, existing run script, weather adapter, Retrosheet
adapter, or Statcast adapter was changed. Keyless MLB sample mode and Phase 0
through Phase 3E behavior are preserved.

## Commands run and exact results

```powershell
py -3.13 -m py_compile courtvision/sports/mlb/data/ballpark_factors.py scripts/mlb_ingest_ballpark_factors.py courtvision/sports/mlb/research_context.py courtvision/sports/mlb/data_manifest.py
```

Result: compilation succeeded.

```powershell
py -3.13 scripts/mlb_ingest_ballpark_factors.py --input-csv tests/fixtures/mlb/ballpark_factors_sample.csv --out-dir .pytest_tmp_ballpark_cli --dry-run
```

Result: exit code 0; three static rows were reported, all output paths were
null, and the output directory did not exist afterward.

```powershell
py -3.13 -m pytest tests/test_mlb_ballpark_factor_ingestion.py -q
```

Result: `19 passed in 0.37s`; the final post-documentation rerun was
`19 passed in 0.29s`.

```powershell
py -3.13 -m pytest tests/test_mlb_ballpark_factor_ingestion.py tests/test_mlb_weather_ingestion.py tests/test_mlb_retrosheet_ingestion.py tests/test_mlb_statcast_ingestion.py tests/test_mlb_data_manifest.py tests/test_mlb_fixture_provider.py tests/test_mlb_provider_contracts.py tests/test_mlb_research_context.py tests/test_mlb_hr_research_pipeline.py tests/test_mlb_module.py tests/test_mlb_research_safety.py tests/test_sport_registry.py tests/test_normalized_odds_quote.py tests/test_provider_registry.py tests/test_research_artifact_contract.py tests/test_nba_backwards_compatibility.py -q
```

Result: `182 passed in 3.51s`.

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; the keyless sample report rendered three research-only
rows without a live API call.

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: `2944 passed, 31 xfailed in 242.56s (0:04:02)`.

No live API call or dataset download occurred during implementation or
validation. Explicit-write coverage wrote only under a pytest temporary
directory.

## Next recommended step

With separate approval, validate this adapter against one deliberately chosen,
small, locally supplied ballpark-factor table. Document the source's factor
baseline and scale, season and version semantics, park-renaming history,
handedness methodology, altitude and distance units, roof taxonomy, and
missingness. Keep joins, feature computation, training-data construction,
modeling, scoring, and runtime integration out of that validation step.
