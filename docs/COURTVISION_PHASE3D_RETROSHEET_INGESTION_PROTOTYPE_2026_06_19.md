# CourtVision Phase 3D: Retrosheet Ingestion Prototype

Date: 2026-06-19

Validation completed: 2026-06-20

## What was added

Phase 3D adds a narrow local-file Retrosheet-style historical ingestion path:

- `courtvision/sports/mlb/data/retrosheet_ingestion.py`
  - local UTF-8 CSV reading for game and event fixtures;
  - required-column and row-value validation;
  - immutable `RetrosheetGameRow` and `RetrosheetEventRow` normalization;
  - explicit completed/postponed/suspended/unknown status handling;
  - explicit or narrowly mapped home-run labels;
  - deterministic row serialization and event raw-row hashing;
  - Phase 3B `MLBSourceManifest` generation and validation; and
  - explicit-only raw, normalized JSONL, and manifest writes.
- `scripts/mlb_ingest_retrosheet.py`
  - games, events, or combined local-file mode;
  - dry-run by default; and
  - output only with `--write-output` and `--out-dir`.
- `tests/fixtures/mlb/retrosheet_games_sample.csv`
  - four synthetic game rows covering completed, postponed, suspended, and
    unknown status plus a doubleheader number.
- `tests/fixtures/mlb/retrosheet_events_sample.csv`
  - two synthetic event rows: one home run and one non-home run.
- `tests/test_mlb_retrosheet_ingestion.py`
  - fixture, validation, status, label, serialization, manifest, checksum,
    output, CLI, and no-network coverage.

## Prototype-only boundary

This phase creates historical game/outcome ingestion and provenance contracts
only. It does not join Retrosheet with Statcast, construct batter-game
opportunities, compute rolling features, create training data, build a model,
or connect normalized rows to the MLB report pipeline or NBA runtime.

Normalized rows use only historical/public source labels. The generated Phase
3B manifest remains `not_approved`, with its existing default-deny safety flags
false. No recommendation, probability, expected-value, selection-tier, unit,
stake, bankroll, or Kelly field is present in normalized game or event rows.

## Supported local game fixture fields

Required columns:

- `game_id`
- `game_date`
- `home_team`
- `away_team`
- `game_status`
- `source_type`

Optional columns:

- `game_number`
- `venue_name`
- `home_score`
- `away_score`

`source_type` must be `historical` or `public`. All files included in one
ingestion manifest must use the same source type.

## Supported local event fixture fields

Required columns:

- `game_id`
- `game_date`
- `inning`
- `batting_team`
- `fielding_team`
- `batter_id`
- `batter_name`
- `pitcher_id`
- `pitcher_name`
- `event_type`
- `is_home_run`
- `source_type`

Optional columns:

- `event_text`
- `rbi`

The `is_home_run` column must exist. Its row value may be blank only when the
documented event-type fallback is intended.

## Normalized game row schema

Each immutable game row contains:

- `sport = MLB`, `league = MLB`, and `source = retrosheet`;
- source type, game ID/date, home and away teams;
- optional game number, venue, home score, and away score;
- normalized game status;
- as-of date; and
- collection timestamp.

`is_completed` is a derived object property for status checks, not a persisted
feature. It is true only when `game_status == completed`.

## Normalized event row schema

Each immutable event row contains:

- `sport = MLB`, `league = MLB`, and `source = retrosheet`;
- source type and game ID/date;
- inning, batting team, and fielding team;
- batter and pitcher identifiers and names;
- event type, optional event text, home-run label, and optional RBI;
- as-of date and collection timestamp; and
- SHA-256 of the stable raw-row representation.

No feature, probability, scoring, or decision field is added.

## Manifest behavior

Every successful parse creates one validated in-memory Phase 3B
`MLBSourceManifest` with:

- `source_name = retrosheet`;
- uniform `source_type = historical` or `public` from the input rows;
- `data_domain = retrosheet`;
- minimum and maximum `game_date` across supplied games/events;
- caller-supplied or UTC collection time;
- caller-supplied as-of date, or the latest input game date;
- raw input root or explicitly written raw directory;
- optional explicitly written normalized directory;
- schema version `1.0`;
- input-file checksums from the Phase 3B `compute_file_sha256` helper;
- deterministic aggregate checksum when both files are supplied;
- combined row count, file count, per-file records, generator, and warnings.

An as-of date before the latest input row is rejected. Phase 3B validation runs
before any requested output is written.

## No-network-by-default policy

The module has no URL builder, HTTP client, download function, or remote mode.
It can read only caller-supplied local CSV paths. The CLI likewise exposes only
local-file arguments. Automated coverage replaces `urllib.request.urlopen`
with a failing sentinel and confirms ordinary ingestion does not call it.

Neither the API nor CLI creates output in default/dry-run mode. API writes
require individual write flags and an output directory. CLI writes require
`--write-output --out-dir ...`; raw copies additionally require
`--include-raw-copy`.

Example dry run:

```powershell
py -3.13 scripts/mlb_ingest_retrosheet.py --games-csv tests/fixtures/mlb/retrosheet_games_sample.csv --events-csv tests/fixtures/mlb/retrosheet_events_sample.csv --out-dir <temp/output> --dry-run
```

## Game-status rules

- `completed` is the only status treated as completed.
- `postponed` remains postponed and is not completed.
- `suspended` remains suspended and is not completed.
- `unknown` remains unknown and is not completed.
- Any other non-empty status fails closed to `unknown` rather than completed.
- A blank status is invalid.

Scores are preserved only when supplied. They do not override game status.

## Home-run label rules

- A non-blank `is_home_run` value is authoritative and accepts only
  true/false, 1/0, or yes/no forms.
- When the value is blank, normalized event types `home_run`, `homer`, and `hr`
  map to true.
- Every other event type maps to false when the explicit label is blank.
- The ingestion code does not infer a label from event text or other rows.

## Leakage boundaries

- Dates must use ISO `YYYY-MM-DD` form.
- Missing required headers or required row values fail with context.
- Malformed non-empty integer and boolean values are not coerced.
- Only fields supplied in the same game or event row are normalized.
- No future game, prior game, rolling, same-game future, or joined value is
  computed.
- No batter-game training row or model input is generated.
- All rows and manifests remain historical/research artifacts.

## Compatibility confirmation

No model, training pipeline, MLB HR scoring weight, selection gate, provider
priority, odds normalization, bankroll behavior, wager sizing, NBA runtime
internal, dashboard asset, or existing run script was changed. The keyless MLB
sample provider and all Phase 0 through Phase 3C behavior remain unchanged.

## Commands run and exact results

```powershell
py -3.13 -m py_compile courtvision\sports\mlb\data\retrosheet_ingestion.py scripts\mlb_ingest_retrosheet.py tests\test_mlb_retrosheet_ingestion.py
py -3.13 -m pytest tests\test_mlb_retrosheet_ingestion.py tests\test_mlb_data_manifest.py tests\test_mlb_statcast_ingestion.py -q --basetemp=.pytest_tmp_phase3d_targeted
```

Result: compilation succeeded; `48 passed in 0.62s`.

```powershell
py -3.13 -m black --check courtvision\sports\mlb\data\retrosheet_ingestion.py scripts\mlb_ingest_retrosheet.py tests\test_mlb_retrosheet_ingestion.py
py -3.13 -m ruff check courtvision\sports\mlb\data\retrosheet_ingestion.py scripts\mlb_ingest_retrosheet.py tests\test_mlb_retrosheet_ingestion.py
```

Result: neither optional formatter/linter package is installed in the Python
3.13 environment (`No module named black`; `No module named ruff`). No package
was installed and no formatting command modified files.

```powershell
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-19 --provider sample
```

Result: exit code 0; the keyless sample report rendered three research-only
rows without a live API call.

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Result: `2911 passed, 31 xfailed in 248.76s (0:04:08)`.

```powershell
py -3.13 scripts/mlb_ingest_retrosheet.py --games-csv tests/fixtures/mlb/retrosheet_games_sample.csv --events-csv tests/fixtures/mlb/retrosheet_events_sample.csv --out-dir .pytest_tmp_phase3d_cli_should_not_exist --dry-run
```

Result: exit code 0; four games and two events were reported in dry-run mode,
all output paths were null, and the output directory did not exist afterward.

No live API call or dataset download occurred during implementation or
validation.

## Next recommended step

With separate approval, validate this narrow adapter against one deliberately
selected, small, locally supplied Retrosheet export and document header, ID,
status, event-code, and doubleheader differences. Keep that work offline and
schema-focused; defer Statcast joins, batter-game construction, rolling
features, training data, and modeling to later explicitly scoped phases.
