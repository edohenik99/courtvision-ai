# CourtVision MLB HR Research Baseline

RESEARCH ONLY - NOT A VALIDATED BETTING PICK

This document describes the first CourtVision MLB home-run prediction baseline.
It is a research workflow only. It must not feed official picks, Elite gates,
Kelly sizing, bankroll logic, dashboard recommendations, or ROI claims.

## Current State

Repository inspected for this update on `feat/mlb-hr-prospective-research` at
commit `49234d31599205ba61ea2fbd26b1a69bf3364b48`.

The local live HR archive currently provides:

- Canonical live odds:
  `C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_props_master.csv`
- Canonical strict results:
  `C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_results.csv`
- Date-scoped graded results:
  `C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_grades_YYYYMMDD.csv`
- Existing collector and nightly ops documentation:
  `C:\dev\Sport_Project1\docs\mlb_live_hr_collector.md`
  and `C:\dev\Sport_Project1\docs\mlb_live_hr_daily_ops.md`

The baseline code lives in:

```text
C:\dev\Sport_Project1\courtvision\sports\mlb\training\hr_research_baseline.py
```

## Data Flow

1. Build one canonical feature row per event, normalized player, market, point,
   and selected pregame snapshot.
2. Select one sportsbook row using best available decimal odds.
3. Join strict outcomes by `event_id + normalized_player_name` for training.
4. Exclude void, void-candidate, manual-review, unresolved, non-final, and
   ambiguous identity rows.
5. Train a pure-Python logistic-regression baseline.
6. Write immutable timestamped model bundles.
7. Generate timestamped daily research predictions from pregame snapshots only.
8. Resolve player identity from a versioned local cache/provider CSV when
   available, quarantining conflicts instead of guessing.
9. Append predictions and later settlements to a prospective ledger without
   mutating original probabilities.
10. Capture closing-line evidence separately, using only latest valid
    pre-start snapshots.

## Feature Definitions

Available and used now:

- American odds
- Decimal odds
- Raw sportsbook implied probability
- Best available American/decimal/implied price
- Number of bookmakers
- Market implied-probability mean, min, max, and dispersion
- American-odds min, max, and dispersion
- Hours before game
- Selected sportsbook one-hot category in the model

Available but incomplete:

- Home and away teams are present, but player team/opponent is not available
  in the live HR odds archive.
- Player identity is normalized by name. A research-only identity resolver can
  fill a separate cache/report from reviewed local MLBAM evidence, but player
  IDs are not available in the current live archive itself.

Missing from the live baseline:

- Batter season/recent HR rates
- Statcast rolling hard-hit/barrel indicators
- Batter handedness and pitcher handedness
- Platoon splits
- Starting pitcher HR/contact indicators
- Ballpark factor
- Weather
- Lineup confirmation and batting order
- Expected plate appearances
- Team implied runs
- Bullpen, rest, and injury indicators

## Snapshot Rule

Training uses:

```text
latest_snapshot_strictly_before_game_start_per_event_normalized_player_market_point
```

Daily prediction uses:

```text
latest_snapshot_at_or_before_prediction_timestamp_and_before_game_start
```

If a game has already begun at prediction time, the predictor excludes it and
does not create a pregame prediction row.

## Operating Date Semantics

The `--date` argument means the CourtVision operating date in
`America/Toronto`, not the raw UTC calendar date from the provider timestamp.
The research tooling parses offset-bearing timestamps, converts them with
`ZoneInfo("America/Toronto")`, and scopes rows by the Toronto local date. It
does not use the computer's local timezone and does not hard-code a fixed
`-04:00` offset, so EST and EDT transitions are handled by timezone data.

Source timestamps remain UTC and are not rewritten. Artifacts distinguish:

- `commence_time` and `commence_time_utc`: immutable UTC game-start timestamp.
- `game_date_utc`: UTC calendar date from `commence_time_utc`.
- `game_date_operating`: CourtVision operating date in `America/Toronto`.
- `game_date`: current research operating date, equal to
  `game_date_operating`.
- `operating_timezone`: currently `America/Toronto`.

For example, `2026-07-15T00:01:00Z` converts to
`2026-07-14 20:01:00 America/Toronto`, so it belongs to operating date
`2026-07-14`. Running with `--date 2026-07-15` would hide that local July 14
event and is not an acceptable workaround.

Timezone-naive timestamps are rejected. Invalid timestamps fail the run instead
of being interpreted as UTC by assumption.

## Special-Event Eligibility

The standard prospective ledger is for ordinary MLB club games only. Event
eligibility is classified before prediction rows are created:

- `regular_season_eligible`: both teams match the deterministic MLB club
  allowlist, or a future authoritative event-type field explicitly says
  regular season.
- `special_event_quarantined`: the event is explicitly special/exhibition, or
  the team pair is `National League` versus `American League`.
- `event_type_unknown` or `manual_review_required`: the current evidence is
  insufficient to include the event in ordinary regular-season evidence.

The current All-Star detection rule is deliberately narrow and auditable:
`home_team` and `away_team` equal `National League` and `American League` in
either order. Those rows are preserved in `excluded_rows.csv` and exclusion
counts with reason `special_event_out_of_distribution`. They are not silently
discarded, do not enter `predictions.csv`, do not append to the standard
prospective ledger, and do not count toward regular-season promotion gates.

If a future special-event research mode is added, it must write to a separate
output directory and ledger/evidence category, be labelled out-of-distribution,
and never count toward regular-season promotion gates.

## Leakage Protections

- Feature timestamps must be before game start.
- Training features are built before joining labels.
- Result fields are separated from prediction-time features.
- Prediction mode does not read the results file.
- Training chooses one snapshot per player-game market.
- Training chooses one best sportsbook row per feature row.
- Model artifacts record source file hashes, source paths, commit SHA, feature
  schema version, row counts, and exclusions.

## Commands

Run from PowerShell:

```powershell
cd C:\dev\Sport_Project1
```

Audit the current local live archive:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline audit-data `
  --odds-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_props_master.csv `
  --results-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_results.csv
```

Build immutable feature artifacts:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline build-features `
  --odds-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_props_master.csv `
  --results-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_results.csv `
  --output-dir C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\features\YYYYMMDD_HHMMSS
```

Train the research baseline:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline train `
  --features-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\features\YYYYMMDD_HHMMSS\feature_rows.csv `
  --output-root C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\models `
  --model-version research-v1
```

Generate current-day research predictions:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline predict `
  --model-dir C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\models\MODEL_ID `
  --odds-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_props_master.csv `
  --date YYYY-MM-DD `
  --output-dir C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\predictions\YYYYMMDD_HHMMSS
```

Append predictions to the research ledger:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline append-ledger `
  --predictions-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\predictions\YYYYMMDD_HHMMSS\predictions.csv `
  --ledger-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\prospective_ledger.csv
```

Append settlements without mutating original prediction records:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline settle-ledger `
  --ledger-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\prospective_ledger.csv `
  --results-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_results.csv
```

Report promotion gates:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline report-gates `
  --features-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\features\YYYYMMDD_HHMMSS\feature_rows.csv `
  --ledger-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\prospective_ledger.csv
```

Resolve research identities from local evidence:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline resolve-identities `
  --predictions-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\predictions\YYYYMMDD_HHMMSS\predictions.csv `
  --identity-source-csv C:\approved-inputs\mlb_player_identity.csv `
  --identity-cache-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\identity_cache.csv `
  --output-dir C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\identity\YYYYMMDD_HHMMSS
```

Run one manual prospective research day with explicit paths:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline run-daily-research `
  --date YYYY-MM-DD `
  --model-dir C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\models\MODEL_ID `
  --output-root C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\daily_runs `
  --ledger-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\prospective_ledger.csv `
  --odds-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_props_master.csv `
  --identity-cache-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\identity_cache.csv
```

Use `--dry-run` to print the same summary without writing artifacts or
appending the ledger. If a completed nonzero prediction run for the same
date/model already exists, the runner returns that run unless `--force` is
supplied. A prior `completed_no_predictions` run is not a permanent lock: a
later source fingerprint or corrected code path may create a new valid run
without `--force`. Idempotency protects completed prediction evidence, not
empty preflight states.

Verify prediction artifacts and optional ledger linkage:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline verify-predictions `
  --predictions-root C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\daily_runs\YYYY-MM-DD\RUN_ID\predictions `
  --ledger-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\prospective_ledger.csv
```

Capture closing-line evidence:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline capture-closing-lines `
  --predictions-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\daily_runs\YYYY-MM-DD\RUN_ID\predictions\predictions.csv `
  --odds-csv C:\dev\Sport_Project1\data\theoddsapi\live_hr_snapshots\live_hr_props_master.csv `
  --output-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\closing_lines.csv
```

Build the prospective trial report:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline report-trial `
  --ledger-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\prospective_ledger.csv `
  --closing-lines-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\closing_lines.csv `
  --identity-cache-csv C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\identity_cache.csv
```

Print the advanced feature readiness matrix:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline feature-readiness
```

## Artifact Locations

### Prospective paper trial v1

The prospective paper trial is a separate, opt-in research lifecycle. It never
creates official picks, wagers, stakes, bankroll decisions, Kelly values, or
automatic promotion. Every control, prediction, ledger, closing, settlement,
status artifact, and compact CLI result is constrained to:

- `sport: MLB`
- `market: batter_home_runs`
- `research_only: true`
- `approval_status: not_approved`
- `eligible_for_betting: false`
- `eligible_for_official_pick: false`

Activate a control from one explicit, complete model bundle. There is no
default model, mutable `current` pointer, legacy fallback, rehearsal fallback,
or in-place repair:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline `
  activate-prospective-control `
  --model-dir <EXPLICIT_MODEL_BUNDLE> `
  --trial-root <EXPLICIT_TRIAL_ROOT> `
  --repository-root <REPOSITORY_ROOT>
```

The bundle must contain `model.json`, `metadata.json`, `metrics.json`,
`model_card.md`, and `bundle_manifest.json`. Activation validates their
recorded hashes, training interval, feature schema, model version, and clean Git
provenance, then publishes an immutable control directory named from a
deterministic control digest. Creation time, absolute paths, and filesystem
metadata are not control-identity inputs. An identical replay is a byte- and
mtime-preserving no-op; a conflict fails closed.

Run one prospective Toronto operating date from the frozen control and an
explicit odds snapshot:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline `
  run-prospective-paper-day `
  --date YYYY-MM-DD `
  --control-dir <EXPLICIT_CONTROL_DIR> `
  --odds-csv <EXPLICIT_ODDS_CSV> `
  --trial-root <EXPLICIT_TRIAL_ROOT> `
  --repository-root <REPOSITORY_ROOT> `
  [--identity-cache-csv <EXPLICIT_IDENTITY_CACHE>] `
  [--dry-run]
```

The run revalidates the control, model bundle, repository state, source-file
stability, strict pregame timestamps, identity policy, and the complete
eligible source population. It stages and validates `predictions.csv`,
`excluded_rows.csv`, `prediction_manifest_v1.json`, and `run_summary_v1.json`, then
publishes atomically and appends the canonical ledger. A run is admissible only
after its ledger linkage re-reads successfully. Zero-prediction runs remain
auditable, and a later changed source snapshot may create a new run.

Capture closing evidence only for an already committed v1 prediction artifact:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline `
  capture-prospective-closing `
  --control-dir <EXPLICIT_CONTROL_DIR> `
  --predictions-csv <EXPLICIT_PREDICTIONS_CSV> `
  --odds-csv <EXPLICIT_ODDS_CSV> `
  --trial-root <EXPLICIT_TRIAL_ROOT>
```

Closing capture accepts observations strictly before game start, prefers the
same sportsbook, otherwise applies the existing consensus fallback, and marks
missing evidence explicitly. It cannot import legacy, rehearsal, historical,
post-hoc lifecycle, or grade-derived rows.

Settle from an explicit strict-results file:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline `
  settle-prospective-paper-day `
  --control-dir <EXPLICIT_CONTROL_DIR> `
  --results-csv <EXPLICIT_RESULTS_CSV> `
  --trial-root <EXPLICIT_TRIAL_ROOT>
```

Settlement joins by event ID plus normalized player identity. Only strict final
results create a final append-only settlement. Pending, unresolved,
manual-review, or void-candidate states cannot become wins or losses. Unit
return uses the original captured price only when that price is complete and
valid; settlement never sizes a wager.

Build the read-only prospective report:

```powershell
python -m courtvision.sports.mlb.training.hr_research_baseline `
  report-prospective-status `
  --control-dir <EXPLICIT_CONTROL_DIR> `
  --trial-root <EXPLICIT_TRIAL_ROOT>
```

The report verifies immutable evidence without creating or repairing files. It
separates controls and model versions and reports operating dates, games,
predictions, players, positive outcomes, identity coverage, closing coverage,
calibration and performance only when measurable, gate progress, and artifact
integrity findings. Passing gates is evidence for human review only and never
promotes a model or creates an official pick.

Build the stricter cumulative and daily trial-health report:

```powershell
py -3.13 -m courtvision.sports.mlb.training.hr_research_baseline `
  report-prospective-health `
  --control-dir <EXPLICIT_CONTROL_DIR> `
  --trial-root <EXPLICIT_TRIAL_ROOT>
```

This command is also strictly read-only. It reuses the prospective control,
immutable prediction/ledger linkage, closing-evidence readers, cumulative
metrics, and gate calculations. It emits deterministic JSON with the frozen
control identity and research boundaries; cumulative prediction, settlement,
identity, and closing evidence; measurable performance; every existing
prospective gate; and one sorted evidence record per supported operating date.
Explicit missing closing rows remain distinct from predictions that have no
closing record, and usable closing coverage includes only verified same-book
or consensus rows. Malformed, conflicting, inconsistent, or integrity-invalid
evidence blocks the report instead of being presented as trusted health data.
The command does not create or repair trial artifacts and does not access a
provider or the network.

Prospective v1 uses explicit schemas rather than silently extending older
records: `mlb-hr-prospective-control-v1`,
`mlb-hr-prospective-prediction-v1`,
`mlb-hr-prospective-prediction-manifest-v1`,
`mlb-hr-prospective-run-summary-v1`, `mlb-hr-prospective-ledger-v2`,
`mlb-hr-prospective-closing-v2`, `mlb-hr-prospective-status-v1`,
`mlb-hr-prospective-health-v1`, and `mlb-hr-prospective-trial-lock-v1`. Older
schemas remain readable by their existing tooling but are never upgraded in
place or treated as prospective v1.

All mutating prospective commands use one exclusive owner-verified trial-store
lock. Visible, malformed, inaccessible, or ownership-conflicting lock states
fail closed; operators must investigate them rather than delete or repair them
automatically.

Recommended generated artifact root:

```text
C:\dev\Sport_Project1\outputs\research\mlb_hr_baseline\
```

These are runtime/research artifacts and should not be committed unless the
repository policy is explicitly changed.

Feature build artifacts:

- `feature_rows.csv`
- `exclusions.csv`
- `manifest.json`
- `audit_summary.md`

Model bundle artifacts:

- `model.json`
- `metadata.json`
- `metrics.json`
- `model_card.md`
- `bundle_manifest.json`

Prediction artifacts:

- `predictions.csv`
- `excluded_rows.csv`
- `manifest.json`
- `manifest.json` embeds source hashes, operating timezone, date semantics,
  event-eligibility counts, and exclusion counts.

Daily manual run artifacts:

- `predictions\predictions.csv`
- `predictions\excluded_rows.csv`
- `predictions\manifest.json`
- `identity\identity_resolution.csv`
- `identity\identity_report.json`
- `identity\identity_report.md`
- `run_summary.json`
- `run_summary.md`

Prospective ledger:

- Append-only prediction records
- Append-only settlement records
- Original model probabilities are never rewritten by settlement

Closing-line evidence:

- Append-only rows keyed by `prediction_id`
- Same-book latest pre-start snapshot preferred
- Consensus latest pre-start snapshot used only when same-book is missing
- Missing pre-start evidence is explicit
- Post-start evidence is not used

Identity cache:

- Append-only rows with `mapping_version`
- Required fields include sportsbook name, normalized name, MLBAM ID,
  canonical name, status, method, source, timestamps, review status, and
  conflict reason
- Conflicts are quarantined for manual review

## Evaluation

The trainer uses chronological splitting by game date. If the first training
window is one-class, it expands forward chronologically until both classes are
present, or fails closed if that is impossible.

Metrics include:

- Rows, games, players, dates
- Positive outcomes and base HR rate
- Log loss
- Brier score
- ROC AUC when both classes are present
- Calibration buckets and calibration error
- Precision and recall at documented thresholds
- Raw sportsbook implied-probability baseline
- Market comparison labelled as vig-included
- Expanding-window diagnostics for small date samples

## Promotion Gates

Research predictions are not official picks. Candidate review remains blocked
until evidence includes at least:

- 30 prediction dates
- 100 completed games
- 1,000 eligible player-game predictions
- 100 unique players
- 50 positive HR outcomes
- Acceptable missing-data rates
- Acceptable identity-match rates
- Acceptable calibration
- Sufficient closing-line data
- No unresolved leakage findings
- No prediction artifact mutation findings

Special-event and event-type-unknown rows are excluded before ledger append and
therefore do not contribute to these regular-season promotion gates.

Even if those thresholds pass, promotion still requires documented human review
of calibration, market-baseline comparison, closing-line value, and stability by
date, player group, odds range, sportsbook, park, and model version.

## Recovery

- If feature generation fails, inspect schema and missingness in the source CSVs.
- If training fails one-class, collect more final labelled dates.
- If model loading fails integrity checks, do not repair in place; train a new
  timestamped research bundle.
- If prediction append fails on duplicate `prediction_id`, keep the existing
  ledger row and create a new prediction run snapshot instead.
- If prediction artifact verification fails, do not append the ledger from
  that artifact; preserve the failed artifact for audit and create a new run
  only after the source issue is understood.
- If settlement is pending, rerun after strict results are final and coverage is
  complete.
- If source rows exist but predictions remain zero, inspect
  `predictions\excluded_rows.csv`, `predictions\manifest.json`, or the dry-run
  summary fields `source_row_count`, `exclusion_counts`, and
  `event_eligibility_counts`. A condition of `special_event_quarantined` means
  source rows were found for the operating date but kept out of ordinary
  regular-season evidence.

## Advanced Feature Readiness

The current live baseline should not implement richer features until identity
and source coverage are closed. The `feature-readiness` command reports the
matrix below in JSON form.

| Priority | Feature Area | Current Status |
|---:|---|---|
| 1 | Deterministic player identity / MLBAM ID | Historical crosswalk contracts exist, but the live HR archive lacks player IDs. |
| 2 | Lineup, batting order, expected PA | Research context schemas exist; no verified current-day lineup feed is wired into this baseline. |
| 3 | Starting pitcher HR/contact/K/pitch-type indicators | Historical pack fields exist; live probable-pitcher evidence is not supplied. |
| 4 | Batter rolling HR/barrel/hard-hit/fly-ball/exit velocity | Historical Statcast feature builder exists; not available in current live prediction artifacts. |
| 5 | Park, weather, roof, wind, venue context | Ingestion modules exist; exact current-day venue/weather/roof evidence is not in the live baseline. |

Lower-priority but still useful gaps include handedness/platoon, team implied
runs, bullpen workload, team rest, injuries, and licensing/source-review
coverage. None of these should be filled by name guessing, post-start evidence,
or synthetic values.

## Known Limitations

- Current labelled data is too small for official betting claims.
- Player IDs are missing in the live archive.
- Player team/opponent assignment is missing.
- Advanced batter, pitcher, lineup, weather, park, and injury features are not
  available in this live baseline.
- Vig cannot be removed from the retained one-sided Over 0.5 HR prices.
- Closing-line data is captured only by an explicit manual command, not by
  automation.
