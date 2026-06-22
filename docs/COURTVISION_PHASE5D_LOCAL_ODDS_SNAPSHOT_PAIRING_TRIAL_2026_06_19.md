# CourtVision Phase 5D: Local Odds Snapshot Pairing Trial

Date: 2026-06-19  
Status: historical research only; market reference only; not production approved; default-deny

## What was added

Phase 5D adds a focused immutable local CSV ingester at
`courtvision/sports/mlb/data/odds_snapshot_ingestion.py` and an explicit
`--odds-pairing-trial` mode in `scripts/mlb_build_hr_local_dataset.py`.

The mode parses the existing local Statcast, Retrosheet, weather, and ballpark
inputs, parses a caller-provided local odds snapshot, builds the existing
Retrosheet-labeled batter-game rows, attaches only deterministic odds market
references, runs the Phase 4C leakage audit, and reports pairing quality.

The controlled fixture trial produced two labeled rows: one HR-positive and one
HR-negative. Both received weather, ballpark, and odds context. A third odds
row remained unmatched to prove default-deny quality reporting.

## Local files only

The trial has no API, download, scraping, or network path. Every input must be
an explicit local CSV path. No files are written unless `--output-dir` is
provided.

## Trial command

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv path\to\statcast.csv `
  --retrosheet-games-csv path\to\retrosheet_games.csv `
  --retrosheet-events-csv path\to\retrosheet_events.csv `
  --weather-csv path\to\weather.csv `
  --ballpark-csv path\to\ballpark_factors.csv `
  --odds-csv path\to\mlb_hr_odds_snapshot.csv `
  --odds-pairing-trial
```

Fixture example:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --weather-csv tests\fixtures\mlb\weather_sample.csv `
  --ballpark-csv tests\fixtures\mlb\ballpark_factors_sample.csv `
  --odds-csv tests\fixtures\mlb\hr_odds_snapshot_sample.csv `
  --odds-pairing-trial
```

Fixture result:

- Statcast rows: 2
- Retrosheet games: 4
- Retrosheet events: 2
- Weather rows: 3
- Ballpark rows: 3
- Odds snapshot rows: 3
- HR batter-game rows: 2
- HR-positive rows: 1
- HR-negative rows: 1
- Weather-attached rows: 2
- Ballpark-attached rows: 2
- Odds-attached rows: 2
- Full-context-plus-odds rows: 2
- Unmatched odds rows: 1
- Rows missing odds: 0
- Audit: 0 errors, 6 warnings, passed
- Approval status: `not_approved`

## Expected odds CSV fields

Required columns are `game_date`, `market_type`, and `american_odds`. The CSV
must also provide one player identity (`player_id`, `player_name`, or
`selection_name`), one source identity (`sportsbook` or `source_name`), and one
timestamp (`odds_collected_at` or `as_of`).

Strongly preferred identity fields are `game_id`, `player_id`, `team`, and
`opponent`. Optional fields are `event_start_time`, `home_team`, `away_team`,
`decimal_odds`, `provider`, `source_type`, `market_label`, `selection_name`, and
`player_name`.

Known home-run market labels normalize to `home_run`. American odds must be an
integer at least `+100` or at most `-100`. Decimal odds are derived from the
American price; a materially inconsistent supplied decimal value is replaced
by the derivation with a warning. Invalid rows are rejected with visible
diagnostics. A file with no valid rows fails clearly.

`implied_probability` is strictly the probability implied by the local market
price. It is not a model probability and is never used to create a decision,
size, or approval.

## Deterministic matching rules

Matching uses the first safe, unique tier:

1. `game_id + player_id + home_run market_type`.
2. `game_date + player_id + team + opponent + home_run market_type`.
3. `game_date + normalized player_name + team + opponent + home_run market_type`.

Explicit team or opponent conflicts reject a candidate. More than one match at
the best tier is ambiguous, so no odds are attached. Missing or unmatched odds
remain neutral null values and are never fabricated.

## Quality checks and freshness policy

Console output and the optional pack report unmatched odds rows, dataset rows
missing odds, duplicate matches, stale snapshots, invalid formats, missing
player IDs, missing game IDs, market mismatches, team/opponent mismatches, and
snapshots collected after event start.

When event start is known, a snapshot is marked fresh only when collected
strictly before the event and no more than 24 hours earlier. Older or
post-start snapshots remain visible market references but are marked stale and
warned. Freshness does not change training/backtest eligibility or any approval
state.

## Populated dataset fields

A safe match can populate `sportsbook`, `odds_provider`,
`hr_market_available`, `american_odds`, `decimal_odds`,
`implied_probability`, `odds_collected_at`, `odds_as_of`,
`odds_is_fresh_for_pregame`, and `odds_manifest_id`.

When no safe match exists, those odds fields remain null. Labels, weather,
ballpark context, eligibility flags, and approval state retain their existing
behavior.

## Optional output pack

Add this only when artifacts are wanted:

```powershell
--output-dir path\to\odds_pairing_pack
```

The directory receives:

```text
dataset.csv
metadata.json
audit.json
source_manifest.json
build_summary.txt
```

Existing targets are refused unless `--overwrite` is passed. The source
manifest records the odds CSV path, SHA-256 checksum, and parsed row count. All
artifacts remain historical research only and default-deny.

## Leakage audit and safety behavior

Retrosheet remains the outcome-label owner. Same-game Statcast outcomes stay
outside the feature namespace. Odds are nullable market context only. The
Phase 4C leakage audit runs after pairing and remains default-deny. Dataset
rows and artifacts remain ineligible for betting and Kelly use, and retain
`approval_status = not_approved`.

No fair probability, edge, expected value, stake, unit size, recommendation,
or production approval is generated.

## Validation commands and exact results

The following compatibility commands each returned exit code 0:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv tests\fixtures\mlb\statcast_sample.csv --statcast-trial

py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --label-pairing-trial

py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --weather-csv tests\fixtures\mlb\weather_sample.csv `
  --ballpark-csv tests\fixtures\mlb\ballpark_factors_sample.csv `
  --context-pairing-trial

py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --weather-csv tests\fixtures\mlb\weather_sample.csv `
  --ballpark-csv tests\fixtures\mlb\ballpark_factors_sample.csv `
  --odds-csv tests\fixtures\mlb\hr_odds_snapshot_sample.csv `
  --odds-pairing-trial

py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures

py -3.13 scripts/mlb_inspect_fixture_stats.py

py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample
```

Focused validation used a fresh absolute pytest temp path:

```powershell
py -3.13 -m pytest `
  tests/test_mlb_hr_dataset_builder.py `
  tests/test_mlb_hr_dataset_schema.py `
  tests/test_mlb_hr_leakage_audit.py `
  tests/test_mlb_statcast_ingestion.py `
  tests/test_mlb_retrosheet_ingestion.py `
  tests/test_mlb_weather_ingestion.py `
  tests/test_mlb_ballpark_factor_ingestion.py `
  tests/test_mlb_odds_snapshot_ingestion.py `
  tests/test_mlb_build_hr_local_dataset.py `
  --basetemp=<fresh_absolute_temp_path> -q
```

Exact final focused result: `164 passed in 2.35s`.

Full validation used another fresh absolute temp path:

```powershell
py -3.13 -m pytest tests --basetemp=<fresh_absolute_temp_path> -q
```

Exact final full result: `3053 passed, 31 xfailed in 244.83s (0:04:04)`.

## Scope confirmation

Phase 5D made no API calls and added no download or scraping behavior. It made
no model or training implementation, MLB HR scoring change, bankroll or Kelly
behavior change, production promotion, provider-fetching change, dashboard
change, run-script change, or NBA runtime refactor. Phase 0 through Phase 5C,
keyless MLB sample mode, and existing NBA behavior were preserved.

## Next recommended step

Phase 5E: historical dataset readiness report.
