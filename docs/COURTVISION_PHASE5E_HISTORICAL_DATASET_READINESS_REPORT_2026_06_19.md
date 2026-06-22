# CourtVision Phase 5E: MLB HR Historical Dataset Readiness Report

Date: 2026-06-21  
Scope: local-file historical research reporting only

## What was added

Phase 5E adds `courtvision/sports/mlb/training/hr_dataset_readiness.py`. The
module provides immutable readiness metrics, issues, and reports; deterministic
JSON and text serialization; and explicit-path writers with overwrite
protection. It evaluates an already-built MLB home-run batter-game dataset and
does not build features, fit a model, or alter rows.

`scripts/mlb_build_hr_local_dataset.py` now prints a readiness summary for the
fixture, label-pairing, context-pairing, and odds-pairing modes. Two optional
flags write standalone reports:

```powershell
--readiness-report-json path\to\readiness.json
--readiness-report-txt path\to\readiness.txt
```

When `--output-dir` is explicitly requested for a dataset build pack, the pack
now also contains `readiness.json` and `readiness_summary.txt`. The existing
dataset, metadata, audit, source-manifest, and build-summary files are
unchanged. No report file is written during the default dry run.

## Why this gate exists

A successful row join is not evidence that a historical dataset is suitable
for research. Before a larger user-provided dataset can support later research,
CourtVision must make label balance, completed-game coverage, context coverage,
local odds coverage, provenance, duplicate identities, and leakage safety
visible. This report supplies that checkpoint without performing modeling.

## Readiness statuses and fixture policy

- `NOT_READY`: one or more blocking defects exist.
- `READY_FOR_LARGER_HISTORICAL_BUILD`: row construction is structurally usable,
  but the sample or coverage is insufficient for real research runs.
- `READY_FOR_TRAINING_RESEARCH`: at least 1,000 rows meet label, completed-game,
  eligibility, identity, provenance, leakage, and context coverage gates.
- `READY_FOR_BACKTEST_RESEARCH`: the training-research conditions also hold,
  with sufficient local odds coverage, backtest eligibility, and safe pregame
  odds timestamps.

The minimum research row count is 1,000. Context and local-odds coverage gates
are 80%, while completed-game and row-eligibility coverage must be at least
95%. Repository fixtures are intentionally tiny. A clean fixture can therefore
be `READY_FOR_LARGER_HISTORICAL_BUILD`, but cannot reach either real research
status. Every status remains historical-research-only and `not_approved`.

## Metrics and issues

The report counts dataset rows, available labels, positive and negative HR
labels, completed games, training/backtest-eligible rows, weather context,
ballpark context, local odds references, full context, and full context plus
odds. It also calculates label, weather, ballpark, full-context, odds, and
full-context-plus-odds coverage rates.

Data-quality checks cover required row/game/player/date/schema identity,
invalid dates, duplicate row IDs, duplicate player-game identities, missing
teams/opponents/venues, labels on incomplete game statuses, missing context,
unmatched local odds, and unsafe odds timestamps.

Blocking issues include empty datasets, missing required identity, invalid
dates, duplicate identities, missing labels, missing positive or negative label
classes, and Phase 4C leakage errors. Missing weather, ballpark, local odds,
team/opponent/venue context, provenance detail, or research-scale row volume is
reported as a warning and reduces the score. Warning-only fixture deficiencies
do not falsely turn the fixture into a real research dataset.

## Leakage, context, odds, and provenance behavior

The builder's Phase 4C leakage audit is passed into the readiness report. Audit
error and warning totals are preserved, and any leakage error forces
`NOT_READY`.

Weather and ballpark attachment are measured separately and together. Values
are never inferred when a local join is missing. Local odds remain nullable
market references only. Coverage, unmatched rows, and known timestamp-safety
failures are reported; missing odds are not fabricated.

Source provenance accepts the build pack's source list, individual immutable
source manifests, or dataset-level manifest identifiers. It counts manifests,
checksums, and source row counts when those details are available, and emits
warnings for incomplete supplied manifest details.

## Examples

Fixture dry run:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures
```

Local odds-pairing trial:

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

The fixture report produced 4 rows, 4 available labels, 2 full-context rows,
0 odds-attached rows, no leakage errors, and status
`READY_FOR_LARGER_HISTORICAL_BUILD`. The odds trial produced 2 rows, 2
available labels, 2 full-context-plus-odds rows, no leakage errors, and the same
tiny-sample status.

## Validation

The following acceptance commands were run from `C:\dev\Sport_Project1`:

```powershell
py -3.13 scripts/mlb_build_hr_local_dataset.py --fixtures

py -3.13 scripts/mlb_build_hr_local_dataset.py `
  --statcast-csv tests\fixtures\mlb\statcast_sample.csv `
  --retrosheet-games-csv tests\fixtures\mlb\retrosheet_games_sample.csv `
  --retrosheet-events-csv tests\fixtures\mlb\retrosheet_events_sample.csv `
  --weather-csv tests\fixtures\mlb\weather_sample.csv `
  --ballpark-csv tests\fixtures\mlb\ballpark_factors_sample.csv `
  --odds-csv tests\fixtures\mlb\hr_odds_snapshot_sample.csv `
  --odds-pairing-trial

py -3.13 scripts/mlb_inspect_fixture_stats.py

py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample

py -3.13 -m pytest tests `
  --basetemp=C:\Users\edohe\AppData\Local\Temp\cv_phase5e_final_full_6cb967ba6dc5493a93bce04710f377e6 `
  -q
```

All four CLI/inspector commands exited successfully. The exact full-suite
result was:

```text
3069 passed, 31 xfailed in 241.94s (0:04:01)
```

A targeted Phase 5E/CLI/builder/leakage run also completed with `89 passed in
1.81s`.

## Safety confirmation and next step

Phase 5E made no live API calls, downloads, website requests, model builds,
training runs, MLB HR scoring changes, bankroll-facing changes, provider
changes, or NBA runtime changes. Keyless MLB sample mode remains intact. The
readiness report and all produced artifacts remain default-deny and not
production approved.

The recommended next step is Phase 6A: a dry run using real user-provided,
multi-file historical CSV inputs. That phase should reuse this readiness gate
before any modeling work is considered.
