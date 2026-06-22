# CourtVision Phase 5C: Weather + Ballpark Context Pairing Trial

Date: 2026-06-19  
Status: historical research only; not production approved; default-deny

## What was added

Phase 5C adds `--context-pairing-trial` to
`scripts/mlb_build_hr_local_dataset.py`. The mode reads five explicit local CSV
files through the existing Statcast, Retrosheet, weather, and ballpark ingestion
modules; builds Retrosheet-labeled MLB HR batter-game rows through the existing
dataset builder; attaches weather and ballpark context; runs the Phase 4C
leakage audit; and prints context-pairing quality counts.

The controlled fixture trial produced two labeled batter-game rows: one HR
positive and one HR negative. Both rows received weather and ballpark context.

## Local files only

The mode requires explicit paths for all five inputs. It has no API, download,
scraping, or network path. No files are written unless `--output-dir` is
provided. The repository fixtures are local static inputs used only for the
controlled test.

## Context pairing command

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv path\to\statcast.csv `
  --retrosheet-games-csv path\to\retrosheet_games.csv `
  --retrosheet-events-csv path\to\retrosheet_events.csv `
  --weather-csv path\to\weather.csv `
  --ballpark-csv path\to\ballpark_factors.csv `
  --context-pairing-trial
```

## Fixture example

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --weather-csv tests\fixtures\mlb\weather_sample.csv `
  --ballpark-csv tests\fixtures\mlb\ballpark_factors_sample.csv `
  --context-pairing-trial
```

Fixture result:

- Statcast rows: 2
- Retrosheet games: 4
- Retrosheet events: 2
- Weather rows: 3
- Ballpark rows: 3
- Labeled batter-game rows: 2
- HR-positive rows: 1
- HR-negative rows: 1
- Weather-attached rows: 2
- Ballpark-attached rows: 2
- Full-context rows: 2
- Audit: 0 errors, 6 warnings, passed
- Approval status: `not_approved`

## Optional output pack

Add the following argument only when artifacts are wanted:

```powershell
--output-dir path\to\context_pairing_pack
```

The directory receives:

```text
dataset.csv
metadata.json
audit.json
source_manifest.json
build_summary.txt
```

Existing pack targets are refused unless `--overwrite` is passed. Dataset,
metadata, audit, and source-manifest artifacts retain the historical,
research-only, default-deny contract.

## Weather join rules

The existing builder applies deterministic matching in this order:

1. Exact `game_id + game_date`.
2. If no exact match exists, normalized `venue_name + game_date` from the
   Retrosheet game row.
3. Exactly one candidate is required. Multiple candidates are ambiguous and
   leave weather missing.
4. Missing weather is warned and never fabricated.

Retrosheet supplies `venue_name` when present. Weather does not use outcome
labels. A safe match can populate `weather_temperature`,
`weather_wind_speed`, `weather_wind_direction`, `roof_status`, and
`weather_source_type` together with the other existing weather schema fields.

## Ballpark join rules

The Retrosheet game venue is normalized with the existing Unicode, whitespace,
case, and punctuation normalization helper. The result is matched to one
normalized ballpark venue. Ambiguous duplicate normalized ballpark identities
are rejected by the existing ingestion contract because a unique venue identity
cannot be established safely. Missing ballpark context is warned and never
fabricated.

A safe match can populate `park_factor_hr`, `venue_name`,
`ballpark_source_type`, `altitude`, and the other existing ballpark schema
fields. Ballpark context does not use outcome labels.

## Pairing quality checks

The console summary, build summary, and source manifest report:

- unmatched weather rows;
- games missing weather;
- games missing ballpark;
- unmatched venue names;
- duplicate weather matches;
- duplicate ballpark matches;
- weather date mismatches;
- ballpark venue normalization mismatches;
- labeled rows missing weather;
- labeled rows missing ballpark; and
- rows with full context.

The fixture includes unmatched weather and venue examples, so those conditions
remain visible as warnings. Missing or ambiguous context does not fabricate
values and does not fail the whole run. Required input paths and identities that
cannot be parsed safely still fail clearly.

## Labels, leakage audit, and default-deny behavior

Retrosheet events remain the outcome-label owner in the Phase 5C trial. Statcast
is parsed and counted, while same-game Statcast outcomes are kept outside the
label namespace. Weather and ballpark inputs only supply context fields.

The Phase 4C audit runs after the rows are built. Every row and emitted artifact
remains historical research only with:

- `eligible_for_betting = false`
- `kelly_eligible = false`
- `approval_status = not_approved`

No production promotion or recommendation is produced.

## Validation commands and exact results

The requested compatibility commands were run exactly as follows and each
returned exit code 0:

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

py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures

py -3.13 scripts/mlb_inspect_fixture_stats.py

py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample
```

Focused validation:

```powershell
py -3.13 -m pytest tests\test_mlb_build_hr_local_dataset.py --basetemp=<fresh_absolute_temp_path> -q
```

Exact result: `29 passed in 1.09s`.

Full validation used a fresh dynamically generated absolute temp path:

```powershell
$temp = Join-Path $env:TEMP ('cv_phase5c_full_' + [guid]::NewGuid().ToString('N'))
py -3.13 -m pytest tests --basetemp=$temp -q
```

The first full-suite run completed with
`1 failed, 3038 passed, 31 xfailed in 262.97s (0:04:22)`. The sole failure was
the unrelated randomized
`TestCLVCorrelation.test_positive_clv_correlates_with_hits` experimental test.
It passed immediately when rerun in isolation (`1 passed in 0.65s`), without
any code change. A second full-suite run with another fresh temp path passed:
`3039 passed, 31 xfailed in 249.42s (0:04:09)`.

## Scope confirmation

Phase 5C made no API calls and added no download or scraping behavior. It made
no model or training implementation, MLB HR scoring change, bankroll or Kelly
behavior change, production promotion, provider change, dashboard change,
run-script change, or NBA runtime change. Phase 0 through Phase 5B behavior,
keyless MLB sample mode, and existing NBA behavior were preserved.

## Next recommended step

Phase 5D: controlled local odds snapshot pairing trial, preserving the same
local-only, leakage-audited, historical-research, and default-deny boundaries.
