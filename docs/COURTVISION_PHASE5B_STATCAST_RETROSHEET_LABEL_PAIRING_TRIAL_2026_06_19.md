# CourtVision Phase 5B: Statcast + Retrosheet Label Pairing Trial

## Status

Phase 5B adds a controlled, local-file-only label pairing mode to
`scripts/mlb_build_hr_local_dataset.py`. It is historical research only,
default-deny, and not production approved.

No API call, download, or website scrape is part of this mode. The caller must
provide all three CSV files from local storage. The mode does not build or train
a model and does not change MLB HR scoring or any NBA runtime behavior.

## What was added

The new `--label-pairing-trial` mode:

- parses the local Statcast CSV with the Phase 3C ingestion path;
- parses the local Retrosheet games and events CSVs with the Phase 3D path;
- builds Retrosheet-labeled batter-game rows with the Phase 4B builder;
- uses Retrosheet games for venue and completed-game status;
- uses Retrosheet events exclusively for `hit_hr_today` and
  `home_run_count`;
- runs the Phase 4C leakage audit;
- prints source, label, eligibility, audit, and pairing-quality counts;
- writes nothing unless `--output-dir` is explicitly supplied.

Retrosheet event opportunities anchor the labeled rows. Statcast identities are
compared to Retrosheet identities using exact game ID, game date, and player ID.
The trial never guesses a cross-source identity match. An unmatched Statcast row
is reported as unmatched and is not allowed to supply an outcome label.

This exact-match posture matters for the repository fixtures: the sample
Statcast file contains NYY/ARI identities while the sample Retrosheet files
contain TOR/BOS identities. The fixture trial therefore builds the two safe
Retrosheet-labeled rows and reports both Statcast rows as lacking corresponding
Retrosheet batter-game labels.

## Commands

General local-file command:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv path\to\statcast.csv `
  --retrosheet-games-csv path\to\retrosheet_games.csv `
  --retrosheet-events-csv path\to\retrosheet_events.csv `
  --label-pairing-trial
```

Repository fixture example:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --label-pairing-trial
```

## Optional output pack

Add an explicit output directory to write the five-file research pack:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --label-pairing-trial `
  --output-dir C:\temp\label_pairing_pack
```

The directory contains:

```text
dataset.csv
metadata.json
audit.json
source_manifest.json
build_summary.txt
```

Existing targets are refused unless `--overwrite` is passed. Individual output
file flags are intentionally unavailable in this trial. Tests write packs only
under pytest temporary directories.

## Built rows and partial context

Each safe row contains a stable batter-game identity, Retrosheet outcome label,
Retrosheet game status, source manifests, and default-deny status. The fixture
trial result is:

- Statcast rows: 2;
- Retrosheet games: 4;
- Retrosheet events: 2;
- batter-game rows: 2;
- HR-positive rows: 1;
- HR-negative rows: 1;
- label-available rows: 2;
- completed-game rows: 2;
- training-eligible rows: 0;
- backtest-eligible rows: 0.

Weather and ballpark files are deliberately outside Phase 5B. Missing values
remain null and produce visible warnings; no weather, venue, or ballpark value is
invented. `venue_name` is still populated from the Retrosheet game record when
available. Without weather event timing, the pregame feature cutoff cannot be
verified, so the fixture rows remain ineligible for training and backtesting.

Retrosheet labels are necessary because Statcast event parsing alone does not
establish the controlled game/event label context required by this phase. The
Phase 5A Statcast-only mode therefore continues to build zero dataset rows.

## Pairing quality checks

Console output and `source_manifest.json` expose counts for:

- unmatched Statcast games;
- unmatched Retrosheet games;
- unmatched exact batter identities;
- Retrosheet events without a resulting batter-game row;
- Statcast rows without a corresponding Retrosheet batter-game label;
- duplicate batter-game row IDs;
- missing player IDs;
- missing game IDs;
- missing game dates.

Nonzero mismatch counts are warnings. Required identities that cannot be parsed
safely remain ingestion errors; they are not repaired or inferred.

## Leakage audit and default-deny behavior

Every trial runs the Phase 4C leakage audit. The fixture run completed with zero
errors, ten warnings, and `audit passed = true`. The warnings preserve missing
weather, ballpark, event timing, and other optional context as visible gaps.

The dataset, metadata, and audit report all remain default-deny:

- `eligible_for_betting = false`;
- `kelly_eligible = false`;
- `approval_status = not_approved`.

These artifacts are historical research only and do not grant production use.

## Validation performed

The following commands were run from `C:\dev\Sport_Project1`:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv tests\fixtures\mlb\statcast_sample.csv --statcast-trial

py -3.13 scripts/mlb_build_hr_local_dataset.py --statcast-csv tests\fixtures\mlb\statcast_sample.csv --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv --label-pairing-trial

py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures

py -3.13 scripts/mlb_inspect_fixture_stats.py

py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample

py -3.13 -m pytest tests/test_mlb_build_hr_local_dataset.py tests/test_mlb_hr_dataset_builder.py tests/test_mlb_hr_leakage_audit.py --basetemp=<fresh_absolute_temp_path> -q

py -3.13 -m pytest tests --basetemp=C:\Users\edohe\AppData\Local\Temp\cv-pytest-phase5b-full-b2dac163-54ed-443e-83d6-832fff18c37e -q
```

Results:

- all five CLI compatibility commands exited 0;
- focused Phase 5B/builder/audit tests: `65 passed in 1.19s`;
- full suite: `3035 passed, 31 xfailed in 249.85s (0:04:09)`.

The first full-suite process window was limited to 120 seconds and expired while
pytest was still running; the listed fresh-temp full-suite rerun is the completed
result.

## Scope confirmation and next step

Phase 5B made no API, download, scrape, model, training, MLB HR scoring,
bankroll-facing, production-promotion, run-script, dashboard, provider, or NBA
runtime changes. Phase 0 through Phase 5A behavior, including keyless MLB sample
mode, remains intact.

The recommended next step is Phase 5C: a controlled weather + ballpark context
pairing trial using caller-provided local files.
