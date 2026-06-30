# CourtVision MLB HR Historical Input Pack Contract

Status: research-only, local-file-only, default-deny. A valid pack is eligible
to enter the historical dataset builder; it is not approved for betting,
production scoring, EV, Kelly sizing, Elite selection, or runtime promotion.

This is the canonical contract for the first genuinely aligned MLB HR
historical build. The preflight reads and hashes local files. It does not fetch,
repair, normalize in place, or write anything.

## Required pack layout

Use a new staging directory outside existing `data/manual`, `outputs`, history,
and runtime directories:

```text
<pack_dir>/
  input_pack_manifest.json
  statcast.csv
  retrosheet_games.csv
  retrosheet_events.csv
  weather.csv
  ballpark_factors.csv
  hr_odds_snapshot.csv
```

All seven files are required. Filenames are fixed. Manifest paths must be the
relative filenames above; absolute paths and paths outside the pack are
rejected.

## Required columns

Additional columns may be retained. Every listed column must be present.
Columns described as join-critical must also contain a value in every relevant
row.

### `statcast.csv`

```text
game_date, game_pk, player_name, batter, pitcher, events, description,
stand, p_throws, home_team, away_team, inning, inning_topbot, pitch_type,
launch_speed, launch_angle, hit_distance_sc, bb_type
```

`game_pk`, `game_date`, `batter`, `player_name`, `pitcher`, `home_team`, and
`away_team` are join-critical. `game_id` is not accepted as a substitute in a
real pack: retain the native numeric Statcast `game_pk`.

### `retrosheet_games.csv`

```text
game_id, game_date, home_team, away_team, game_number, venue_name,
home_score, away_score, game_status, source_type
```

Every included game must be `completed`, have a venue, and have one unique
`(game_id, game_date)` identity.

### `retrosheet_events.csv`

```text
game_id, game_date, inning, batting_team, fielding_team, batter_id,
batter_name, pitcher_id, pitcher_name, event_type, event_text, is_home_run,
rbi, source_type
```

These are transformed Retrosheet-derived label rows, not an unmodified raw
Retrosheet event file. `is_home_run` is the outcome label. The file must cover
every Statcast batter-game identity included in the pack.

### `weather.csv`

```text
game_id, game_date, event_start_time, venue_name, temperature, wind_speed,
wind_direction, source_name, source_type, collected_at, as_of_date
```

The ingestion path also accepts latitude, longitude, wind-out direction,
humidity, precipitation, and roof status. They are recommended but not
pack-required headers.

### `ballpark_factors.csv`

```text
venue_name, team, park_factor_hr, source_name, source_type, data_version,
collected_at, as_of_date
```

Handedness factors, altitude, field dimensions, and roof type are recommended.
`park_factor_hr` must contain a finite positive value.

### `hr_odds_snapshot.csv`

```text
game_date, game_id, player_id, player_name, team, opponent, market_type,
sportsbook, american_odds, odds_collected_at, event_start_time, home_team,
away_team, provider, source_type
```

Decimal odds, market label, and selection name are optional. The market must
normalize to `home_run`. Every row must be a unique pregame snapshot no more
than 24 hours before the matching event start.

## Alignment rules

### Dates

- The manifest start/end must equal the minimum and maximum Retrosheet game
  dates.
- Statcast, Retrosheet label, and weather game coverage must exactly match the
  Retrosheet game set. Rows outside that set fail.
- Odds may cover a subset of labeled batters, but every odds date and game must
  be inside the game set.
- Each source manifest entry must declare the date range actually present in
  its CSV. The ballpark entry declares the pack game range because it is static
  context for those games.

### Game and team identity

- `game_pk`/`game_id` is the same positive numeric Statcast game identifier in
  every file. Do not put a native Retrosheet game ID in `game_id`.
- Doubleheaders remain distinct through distinct Statcast game IDs; retain
  `game_number` as supporting provenance.
- Team codes are uppercase 2-3 letter codes and must match exactly across
  Statcast, Retrosheet, and odds rows.
- Retrosheet event batting/fielding teams must be the game's home/away pair.
- No fuzzy game or team matching is performed.

### Player identity

- `batter`, `batter_id`, and odds `player_id` are the same positive numeric
  MLBAM player ID.
- `pitcher` and `pitcher_id` are positive numeric MLBAM player IDs.
- Raw Retrosheet player IDs must be converted through a verified local
  crosswalk before packing. Retain that crosswalk and its provenance outside
  the immutable pack; never guess a match.
- Names are normalized only for punctuation, case, whitespace, and
  `Last, First` ordering. The normalized name must agree for a canonical ID.
- The unique Statcast and Retrosheet `(game_id, game_date, batter_id)` sets must
  be identical. Odds identities must belong to that set.

### Weather and ballpark

- Exactly one weather row is required per game, keyed by exact game ID and
  date.
- Weather venue must equal the Retrosheet venue after conservative punctuation
  and case normalization.
- Weather must include event start, temperature, wind speed, and wind
  direction; missing values fail.
- The ballpark table contains exactly the normalized venues used by the pack:
  one row per venue, no missing or unrelated venue rows.

### Odds/context

- The odds file is required and must contain at least one valid row. It need
  not quote every labeled batter.
- Every odds row must match an exact game and labeled player. Player team and
  opponent must match that batter's Retrosheet event context; home/away teams
  must match the game.
- Odds and weather event-start timestamps must be identical instants.
- Snapshots at/after first pitch or more than 24 hours before first pitch fail.
- Multiple sportsbooks are allowed; duplicate game/player/sportsbook snapshots
  are not.

## Real, fixture, and sample rules

A real pack has `source_classification: "real"` at the pack and source-entry
levels. Every source is marked required and loaded. Sample, fixture, mock,
synthetic, dummy, fake, example, test, or placeholder identities/provenance are
rejected. IDs such as `b001` and `p001` are rejected. Weather and ballpark
`source_type=sample` is rejected.

Repository fixtures remain test inputs, even when they demonstrate valid
alignment. Copying or renaming sample bytes does not make them real. A real
pack must contain independently collected historical source bytes and truthful
provider labels.

Minimum accepted counts are:

| Source | Minimum |
|---|---:|
| Statcast rows | 2 |
| Retrosheet games | 1 |
| Retrosheet event rows | 2 |
| Weather rows | 1 |
| Ballpark rows | 1 |
| Odds rows | 1 |
| Distinct labeled batter-games | 2 |

These are format/preflight floors, not model-readiness thresholds.

## Input manifest contract

`input_pack_manifest.json` is an immutable source manifest. Its top-level
fields are:

```json
{
  "manifest_version": "mlb-hr-historical-input-pack-v1",
  "mode": "historical_input_pack",
  "created_at": "<ISO-8601 datetime with offset>",
  "source_classification": "real",
  "dataset_date_range_start": "YYYY-MM-DD",
  "dataset_date_range_end": "YYYY-MM-DD",
  "approval_status": "not_approved",
  "eligible_for_betting": false,
  "kelly_eligible": false,
  "sources": []
}
```

There is exactly one source entry for each key below:

```text
statcast
retrosheet_games
retrosheet_events
weather
ballpark_factors
odds_snapshot
```

Each entry requires:

```json
{
  "source_name": "statcast",
  "provider_label": "<truthful provider/export label>",
  "source_type": "local_file",
  "source_classification": "real",
  "path": "statcast.csv",
  "sha256": "<64 lowercase hex characters>",
  "byte_size": 123,
  "parsed_row_count": 123,
  "created_at": "<ISO-8601 datetime with offset>",
  "date_range_start": "YYYY-MM-DD",
  "date_range_end": "YYYY-MM-DD",
  "required_or_optional": "required",
  "loaded_successfully": true
}
```

Preflight recomputes every hash and byte size, parses every CSV, compares row
counts and ranges, and then runs semantic joins. It never repairs a stale
manifest.

## Automated candidate staging

The staging builder accepts six already-local source files: Statcast,
Retrosheet/game labels, the validated batter-game crosswalk, weather, ballpark
factors, and odds/context. It performs no fetches. The output directory must be
new or empty, its parent must already exist, and no component may be an output,
history, runtime, manual-data, or cache folder.

The combined Retrosheet/game-label source has one row per batter-game and must
carry native Retrosheet game, batter, and team IDs plus game metadata, canonical
MLBAM `pitcher_id`, pitcher name, inning, outcome label, event text, RBI, and
source type. The builder obtains canonical game, batter, and team fields only
through the validated crosswalk. The label pitcher must occur in Statcast for
the same canonical batter-game. Weather and odds may retain
`retrosheet_game_id` and `retrosheet_batter_id` audit columns, but any supplied
canonical IDs and context must agree exactly with the crosswalk.

Run from the repository root after creating the staging parent:

```powershell
python scripts\mlb_stage_hr_historical_pack.py `
  --statcast-csv C:\source\statcast.csv `
  --retrosheet-labels-csv C:\source\retrosheet_game_labels.csv `
  --crosswalk-csv C:\source\batter_game_crosswalk.csv `
  --weather-csv C:\source\weather.csv `
  --ballpark-csv C:\source\ballpark_factors.csv `
  --odds-context-csv C:\source\odds_context.csv `
  --output-staging-dir C:\courtvision_staging\mlb_hr\2024-04-10
```

Crosswalk validation is the first operation. The builder creates candidate
files only under a temporary child of the requested staging directory, runs
the full input-pack preflight there, and finalizes exactly six CSVs plus
`input_pack_manifest.json` only after it passes. Failures remove builder-owned
temporary files. The manifest records output and input hashes, byte sizes, row
counts, source paths, date ranges, provider labels, and real-source/research-
candidate classifications while keeping all approval and betting flags off.

## Preparing the first real pack manually

1. Choose one narrow completed range. One game day is acceptable for the first
   pack; use the same game set for every source.
2. Create a new staging directory, for example
   `C:\courtvision_staging\mlb_hr\2024-04-10`. Do not stage inside existing
   manual-data, output, history, or runtime directories.
3. Place the original local Statcast export at `statcast.csv`. Keep all pitches
   for the selected completed games and retain native `game_pk` and MLBAM IDs.
4. Transform the already-local Retrosheet data into `retrosheet_games.csv` and
   `retrosheet_events.csv`. Use a verified local game/player crosswalk so the
   exported `game_id`, `batter_id`, and `pitcher_id` are the same numeric IDs
   used by Statcast. Include at least one label row for every Statcast batter in
   every game. If the crosswalk is missing or ambiguous, stop; the pack is not
   ready.
5. Create `weather.csv` from an already-local historical observation source.
   Add the canonical game ID, exact Retrosheet venue, and verified first-pitch
   timestamp manually. Include one row per game.
6. Create `ballpark_factors.csv` from an already-local factor table. Keep only
   venues used by the selected games and record the factor version/as-of date.
7. Create `hr_odds_snapshot.csv` from already-local historical sportsbook or
   provider exports. Add canonical game/player IDs and team context through the
   same verified crosswalk. Retain only pregame HR prices with a known capture
   time.
8. Review the CSVs for exact date, game, team, player, and venue equality. Do
   not solve disagreements with fuzzy matching or invented values.
9. Record immutable file metadata with read-only PowerShell commands:

   ```powershell
   Get-ChildItem C:\courtvision_staging\mlb_hr\2024-04-10\*.csv |
     Select-Object Name, Length

   Get-ChildItem C:\courtvision_staging\mlb_hr\2024-04-10\*.csv |
     Get-FileHash -Algorithm SHA256

   (Import-Csv C:\courtvision_staging\mlb_hr\2024-04-10\statcast.csv).Count
   ```

   Repeat the row-count command for all six CSVs. Derive each dynamic source's
   min/max `game_date`; use the pack range for ballpark factors.
10. Manually create `input_pack_manifest.json` with the exact hashes, sizes,
    parsed row counts, ranges, relative filenames, and truthful provider labels.
11. Run the read-only preflight:

    ```powershell
    py -3.13 scripts\mlb_preflight_hr_historical_pack.py `
      C:\courtvision_staging\mlb_hr\2024-04-10
    ```

    Fix the source/crosswalk outside the validator and regenerate the manifest
    whenever any CSV byte changes. A valid run prints
    `preflight_status: valid`.
12. Exercise the builder without writes:

    ```powershell
    py -3.13 scripts\mlb_build_hr_local_dataset.py `
      --historical-input-pack C:\courtvision_staging\mlb_hr\2024-04-10
    ```

13. Only after the read-only run succeeds, an explicit new staging destination
    may be used for research artifacts:

    ```powershell
    py -3.13 scripts\mlb_build_hr_local_dataset.py `
      --historical-input-pack C:\courtvision_staging\mlb_hr\2024-04-10 `
      --output-dir C:\courtvision_staging\mlb_hr\2024-04-10-build
    ```

The builder re-runs preflight before parsing/building and keeps
`approval_status=not_approved`, `eligible_for_betting=false`, and
`kelly_eligible=false`. Output is a research artifact only.

## Remaining real-pack blockers

The workflow does not supply source data or identity evidence. Before a real
historical backtest, an operator still needs independently collected aligned
exports, verified Retrosheet-to-MLBAM game/player/pitcher mappings, complete
historical pregame odds with capture times, reviewed weather/venue mappings,
and enough season coverage to pass dataset-readiness and leakage review. No
production approval is implied by passing this contract.
