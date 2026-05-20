# CourtVision System Architecture Audit

Audit date: 2026-05-18

Scope: live CourtVision runtime entrypoints, prediction pipeline, post-run workflow, and runtime/history artifacts. This audit is based on repository/source inspection only. No full daily pipeline was run.

## 1. Executive Summary

CourtVision is a daily NBA betting-operations system. In plain English, it fetches slate data, player props, team/player baselines, injury information, and contextual signals; turns them into candidate betting rows; scores and filters those rows; writes operator boards; sizes eligible bets with conservative Kelly staking; then tracks, grades, and summarizes the results.

The live production spine is:

1. Operator runs `run_today.bat` or `run_today.ps1`.
2. `run_today.ps1` calls `courtvision_ai.py`.
3. `courtvision_ai.py` creates `CourtVisionAI`, fetches/normalizes data, and delegates candidate construction to `courtvision/pipeline/predict_pipeline.py`.
4. `PredictionPipeline` builds candidates, scores them, and uses `courtvision/selection/operator_boards.py` plus `courtvision/runtime_audit.py` to produce operator-safe boards.
5. `courtvision_ai.py` writes runtime artifacts through `courtvision/runtime_outputs.py`.
6. Post-run scripts validate, stake, track, grade, and write operator summaries/cards.

The strongest architectural features are the explicit operator entrypoint, past-date guard, artifact overwrite guards, centralized `EliteThresholds`, identity quarantine, odds/game-status gates, and rich audit artifacts.

The main architectural risk is that the canonical runtime still depends on a very large god file, `courtvision_ai.py`, which owns provider access, orchestration, legacy branches, context attachment, history hooks, and output writing. There are also duplicated provider abstractions, duplicated selection/scoring concepts, and multiple warning-only audit paths that can let risky conditions continue unless the operator reads the reports carefully.

## 2. System Start Points

### Operator Entrypoints

`run_today.bat` is the Windows double-click/terminal wrapper. It resolves its own directory and delegates to PowerShell:

- Script: `run_today.bat`
- Delegates to: `run_today.ps1`
- Default date behavior: if no argument is passed, it uses the current date in `yyyy-MM-dd`.
- Pass-through flags: `-VerboseMode`, `-ForcePastDate`.

`run_today.ps1` is the real operator runner. It handles slate-date validation, logging, baseline checks, canonical runtime invocation, validation, Kelly staking, grading, summaries, and final operator card display.

Key `run_today.ps1` responsibilities:

- Validates `-Date` format.
- Blocks closed-slate regeneration unless `-ForcePastDate` is explicit.
- Creates logs under `outputs/runtime/logs/`.
- Chooses Python from `.venv/Scripts/python.exe` or `py -3.13`.
- Ensures `pandas` is importable.
- Runs baseline fit only when `outputs/model/player_baselines.csv` or `outputs/model/team_baselines.csv` is missing.
- Runs prediction via `courtvision_ai.py`.
- Runs validation, Kelly, grading, summaries, and operator card scripts.

### Canonical Runtime Entrypoint

`courtvision_ai.py` is the canonical live runtime entrypoint.

Important symbols:

- `courtvision_ai.py:335` - `class BallDontLieClient`
- `courtvision_ai.py:1631` - `class ProviderClientAdapter`
- `courtvision_ai.py:1722` - `class CourtVisionAI`
- `courtvision_ai.py:3984` - `CourtVisionAI.fit`
- `courtvision_ai.py:4022` - `CourtVisionAI.predict`
- `courtvision_ai.py:8766` - `_write_cli_outputs`
- `courtvision_ai.py:9039` - `_build_arg_parser`
- `courtvision_ai.py:9060` - `main`

`run_today.ps1` calls `courtvision_ai.py` in two ways:

```powershell
# Baseline fit, only when baseline CSVs are missing.
courtvision_ai.py --fit-only --verbose-outputs

# Daily prediction, always in the normal operator run.
courtvision_ai.py --prediction-date <YYYY-MM-DD> --predict-only --verbose-outputs
```

The exact call sites are in `run_today.ps1` around the baseline block and prediction block:

- `run_today.ps1:247-260` checks baseline files and invokes `courtvision_ai.py --fit-only --verbose-outputs`.
- `run_today.ps1:270` invokes `courtvision_ai.py --prediction-date $Date --predict-only --verbose-outputs`.

### Compatibility Entrypoint

`scripts/run_daily.py` is not the canonical runtime. Its own docstring says `courtvision_ai.py` stays canonical and that the wrapper is intentionally thin.

Important symbols:

- `scripts/run_daily.py:22` - `parse_args`
- `scripts/run_daily.py:30` - `main`

It imports `CourtVisionAI`, calls `ai.predict(args.prediction_date)`, writes compatibility boards using `courtvision.pipeline.runner.save_prediction_boards`, optionally sends Telegram, and writes a manifest. It is operator-convenience compatibility code, not the live `run_today.ps1` path.

## 3. End-to-End Workflow

1. Operator command
   - User runs `run_today.bat [YYYY-MM-DD]` or `run_today.ps1 -Date YYYY-MM-DD`.
   - `run_today.bat` delegates to `run_today.ps1`.

2. Date and safety gate
   - `run_today.ps1` parses the slate date.
   - Past dates are blocked unless `-ForcePastDate` is supplied.
   - Closed-slate warnings explicitly tell the operator that regeneration can overwrite outputs.

3. Runtime logging setup
   - `run_today.ps1` writes logs under `outputs/runtime/logs/`:
     - `run_today_<date>.log`
     - `validation_<date>.log`
     - `grading_<date>.log`

4. Baseline check
   - `run_today.ps1` checks:
     - `outputs/model/player_baselines.csv`
     - `outputs/model/team_baselines.csv`
   - If either is missing, it runs `courtvision_ai.py --fit-only --verbose-outputs`.
   - `CourtVisionAI.fit` fetches historical stats, normalizes them, and writes model baseline CSVs.

5. Canonical prediction runtime
   - `run_today.ps1` runs:
     - `courtvision_ai.py --prediction-date <date> --predict-only --verbose-outputs`
   - `courtvision_ai.py:main` creates `CourtVisionAI` and calls `CourtVisionAI.predict`.

6. Data fetch
   - `CourtVisionAI.predict` loads baselines and calibration.
   - It fetches games through the active provider client.
   - It fetches odds, injuries, recent schedule, and team context.
   - Provider code is split between `courtvision_ai.py` internal clients and package clients under `courtvision/clients/`.

7. Normalization
   - Games: `courtvision/data/normalization.py:normalize_games_frame` and `CourtVisionAI._normalize_games`.
   - Odds: `courtvision/data/normalization.py:normalize_odds_frame`, `courtvision/data/bdl_odds_adapter.py:normalize_bdl_player_props`, and `CourtVisionAI._normalize_odds`.
   - Injuries: `courtvision/data/normalization.py:normalize_injuries_frame` and `CourtVisionAI._normalize_injuries`.

8. Prediction pipeline
   - `CourtVisionAI.predict` constructs `PredictionConfig` and calls `PredictionPipeline(config).run(...)`.
   - Core pipeline symbols:
     - `courtvision/pipeline/predict_pipeline.py:108` - `PredictionConfig`
     - `courtvision/pipeline/predict_pipeline.py:136` - `PredictionResult`
     - `courtvision/pipeline/predict_pipeline.py:167` - `PredictionPipeline`
     - `courtvision/pipeline/predict_pipeline.py:245` - `PredictionPipeline.run`
     - `courtvision/pipeline/predict_pipeline.py:886` - `_build_candidate_universe`

9. Candidate universe
   - `PredictionPipeline._build_candidate_universe` builds player and team market candidates.
   - `courtvision/data/candidates.py:score_player_markets` joins odds to baselines and creates market-side candidates via injected callbacks.
   - Candidate rows receive projections, side edge, confidence, quality score, identity fields, injury fields, game status, odds freshness data, and diagnostic rejection reasons.

10. Scoring
   - `courtvision/scoring/candidate_scoring.py:compute_selection_score` computes score components.
   - `courtvision/scoring/candidate_scoring.py:CandidateScoringPolicy` attaches quality, confidence, edge, and elite metadata.
   - `courtvision/runtime_scoring.py` is a compatibility wrapper around the newer scoring modules.

11. Selection
   - `PredictionPipeline.run` defines nested selectors:
     - `select_elite_board`
     - `select_top_per_market`
   - It then calls `courtvision/selection/operator_boards.py:build_operator_boards`.
   - Full-market rows are filtered for operator-active live markets, identity quarantine, unsupported active markets, milestone rows, and duplicate betting identity.
   - Elite rows are filtered further by allowed markets, quality/confidence thresholds, runtime audit gates, and exposure caps.

12. Board writing
   - `courtvision_ai.py:_write_cli_outputs` applies `BoardAuditPolicy`, schema enforcement, artifact guards, and output layout.
   - `courtvision/runtime_outputs.py:OutputLayoutPolicy` maps outputs into `operator`, `diagnostics`, `research`, and optional verbose lanes.
   - Protected operator boards are:
     - `elite_board_<date>.csv`
     - `full_market_board_<date>.csv`
     - `sgp_board_<date>.csv`

13. Validation
   - `run_today.ps1` checks board row counts and audit summary availability.
   - It runs `scripts/validate_runtime_outputs.py <date>`.
   - It runs warning-path audits:
     - `scripts/audit_full_market_sanity.py --prediction-date <date>`
     - `scripts/audit_candidate_quality_drift.py --prediction-date <date>`

14. Kelly
   - If elite row count is greater than zero, `run_today.ps1` runs:
     - `scripts/run_kelly_stakes.py --prediction-date <date> --bankroll <bankroll>`
   - `scripts/run_kelly_stakes.py` reads `outputs/runtime/operator/elite_board_<date>.csv`.
   - It writes `outputs/runtime/operator/kelly_stakes_<date>.csv`.
   - Kelly is player-points locked unless explicitly changed in code.

15. Grading and history tracking
   - If there are picks, `run_today.ps1` runs:
     - `scripts/post_run_tracking.py --prediction-date <date> --grade-pending`
   - Optional grading/report scripts:
     - `scripts/grade_completed_picks.py`
     - `scripts/market_shadow_grading.py --prediction-date <date>`
   - Long-lived history is under `data/history/`.

16. Summaries
   - `run_today.ps1` runs:
     - `scripts/write_daily_summary.py --prediction-date <date>`
     - `scripts/write_quality_summary.py --prediction-date <date>`
     - `scripts/write_completion_state_audit.py --prediction-date <date>`

17. Operator card
   - `run_today.ps1` runs:
     - `scripts/write_operator_card.py --prediction-date <date>`
   - It then prints `outputs/runtime/operator/operator_card_<date>.txt`.

## 4. Architecture Diagram

```text
Operator Layer
  run_today.bat
    -> run_today.ps1
       - date guard
       - logs
       - baseline check
       - validation
       - Kelly
       - grading/history
       - summaries/operator card

Runtime Orchestration Layer
  courtvision_ai.py
    - CourtVisionAI.fit
    - CourtVisionAI.predict
    - _write_cli_outputs
    - provider adapter glue
    - context attachment
    - artifact writing

Provider/Data Layer
  courtvision/clients/
    - ProviderManager
    - SportsDataIOClient
    - BalldontlieClient
  courtvision_ai.py
    - BallDontLieClient
    - ProviderClientAdapter
  courtvision/data/
    - normalization.py
    - bdl_odds_adapter.py
    - candidates.py

Prediction Pipeline Layer
  courtvision/pipeline/predict_pipeline.py
    - PredictionConfig
    - PredictionPipeline.run
    - _build_candidate_universe
  courtvision/injuries/
    - InjuryEngine
    - realism.py
    - volatility.py
  courtvision/context/
    - player_identity.py
    - game_context.py
    - manual_player_context.py
  courtvision/scoring/
    - candidate_scoring.py
    - edge.py
    - confidence.py
    - penalties.py

Selection/Board Layer
  courtvision/selection/operator_boards.py
    - build_operator_boards
    - assign_candidate_lanes
  courtvision/runtime_audit.py
    - BoardAuditPolicy
    - EliteTelemetry
    - get_elite_rejection_reason
    - projected_kelly_skip_reason
  courtvision/runtime_outputs.py
    - OutputLayoutPolicy

Audit/Reporting Layer
  scripts/validate_runtime_outputs.py
  scripts/audit_full_market_sanity.py
  scripts/audit_candidate_quality_drift.py
  scripts/write_daily_summary.py
  scripts/write_quality_summary.py
  scripts/write_completion_state_audit.py
  scripts/write_operator_card.py
  courtvision/reporting/*

History/Feedback Layer
  scripts/post_run_tracking.py
  scripts/history_tracking.py
  scripts/grade_completed_picks.py
  scripts/repair_pending_grades.py
  scripts/market_shadow_grading.py
  data/history/*.csv
```

## 5. Module Responsibility Map

| File/Module | Responsibility | Inputs | Outputs | Risk Level |
|---|---|---|---|---|
| `run_today.bat` | Convenience wrapper that delegates to PowerShell. | Optional date argument. | Calls `run_today.ps1`. | Low |
| `run_today.ps1` | Operator orchestration: date guard, logs, fit check, predict run, validation, Kelly, grading, summaries, card. | Date, bankroll env, existing baselines, runtime scripts. | Logs, post-run script outputs, printed operator card. | High |
| `courtvision_ai.py` | Canonical runtime, provider glue, model fit, prediction orchestration, context attachment, output writing. | Provider APIs, baselines, odds, games, injuries, env flags. | Model files, runtime boards, diagnostics, research outputs, logs/history hooks. | Critical |
| `scripts/run_daily.py` | Compatibility shim around `CourtVisionAI.predict`; writes legacy/manifest outputs. | Prediction date, out dir, optional Telegram flag. | `outputs/boards/<date>/...`, manifest. | Medium |
| `courtvision/pipeline/predict_pipeline.py` | Main modular prediction pipeline: injury context, candidate universe, selection, telemetry, readiness audits. | Games, odds, player baselines, team baselines, injuries. | `PredictionResult`, telemetry CSV/JSON, readiness diagnostics. | Critical |
| `courtvision/data/normalization.py` | Normalizes stats, games, odds, injuries into pipeline schemas. | Raw provider DataFrames. | Normalized DataFrames. | High |
| `courtvision/data/bdl_odds_adapter.py` | Converts BallDontLie player prop payloads into canonical odds rows. | Raw BDL prop rows. | Normalized prop rows plus unresolved reasons. | High |
| `courtvision/data/candidates.py` | Joins odds to baselines and emits player-market candidates using injected scoring/build callbacks. | Odds, baselines, games, projection/scoring callbacks. | Candidate rows and rejection diagnostics. | Critical |
| `courtvision/clients/provider_manager.py` | Multi-provider priority/fallback manager. | Provider settings/env. | Games, players, stats, injuries, odds. | High |
| `courtvision/clients/sportsdataio_client.py` | SportsDataIO API client. | SportsDataIO API key, target date/player ids. | Provider model objects/data. | High |
| `courtvision/clients/balldontlie_client.py` | Package BallDontLie client. | BDL API key, target date/player ids. | Provider model objects/data. | High |
| `courtvision/context/player_identity.py` | Canonical identity resolver and quarantine metadata. | Baselines, odds/candidate rows, active games. | Canonical player ids, conflict/quarantine fields. | Critical |
| `courtvision/context/game_context.py` | Passive game/team context and identity quarantine helpers. | Games, odds, candidates, team ratings. | Context fields, reports, diagnostics. | High |
| `courtvision/context/manual_player_context.py` | Loads and attaches operator manual context. | `config/manual_player_context_<date>.csv`. | Manual context columns, diagnostics JSON. | Medium |
| `courtvision/injuries/injury_engine.py` | Injury context construction and projection/confidence adjustments. | Injury rows, games, baselines, candidates. | Injury context, injury-adjusted candidate fields. | High |
| `courtvision/injuries/realism.py` | Player-points realism dampening. | Candidate rows. | Dampened projection/confidence metadata. | High |
| `courtvision/market/quality.py` | Market aliases, canonical player names, market quality helpers, partial fill markets. | Market strings, candidate rows. | Normalized market fields and quality flags. | Medium |
| `courtvision/scoring/candidate_scoring.py` | Selection score, quality score, elite metadata. | Candidate row metrics. | Scoring metadata and elite flags. | Critical |
| `courtvision/runtime_scoring.py` | Backward-compatible scoring facade. | Candidate rows. | Delegated scoring metadata. | Medium |
| `courtvision/runtime_selection.py` | Runtime gates: strong-over calibration, game status, odds freshness, legacy elite rejection wrapper. | Candidate/board rows. | Gate decisions/reasons. | Critical |
| `courtvision/selection/operator_boards.py` | Live operator board construction, active-market filter, identity quarantine filter, duplicate betting identity dedupe, lane assignment. | Candidate DataFrame and selector callbacks. | Elite board, full-market board, selection trace. | Critical |
| `courtvision/runtime_audit.py` | Board audit metadata, elite rejection reasons, Kelly skip reasons, elite telemetry. | Candidate/board rows, current time. | Audit columns, elite telemetry files, skip reasons. | Critical |
| `courtvision/runtime_outputs.py` | Central output path layout for prediction/grading artifacts. | Runtime root, date, verbose flag. | File paths for operator/diagnostics/research/optional lanes. | High |
| `courtvision/artifact_guard.py` | Date and overwrite guards for generated artifacts. | Requested date, output path, force flag. | Exceptions or write logs. | High |
| `scripts/validate_runtime_outputs.py` | Validates elite board existence, exposure caps, directional edge sanity, preview. | Elite board and elite audit summary. | Console/log validation status. | High |
| `scripts/run_kelly_stakes.py` | Conservative Kelly staking from elite board. | Elite board, bankroll. | `kelly_stakes_<date>.csv`. | Critical |
| `courtvision/betting/kelly.py` | Quarter-Kelly fraction calculation with confidence threshold and 2 percent cap. | Edge, decimal odds, confidence. | Stake fraction/recommendation. | Critical |
| `scripts/post_run_tracking.py` | Persists daily picks and shadow rows, optionally grades pending rows. | Operator boards, Kelly stakes, history. | `data/history/*`, `outputs/runtime/history/*`. | Critical |
| `scripts/history_tracking.py` | Long-lived pick/shadow/performance history persistence and grading. | Operator boards, Kelly stakes, result feedback, history CSVs. | Pick history, market shadow history, performance summaries, graded runtime CSVs. | Critical |
| `scripts/grade_completed_picks.py` | CLI wrapper for pending pick grading. | `data/history/pick_history.csv`, result feedback. | Updated pick history and `graded_picks_<date>.csv`. | High |
| `scripts/repair_pending_grades.py` | Repairs stale pending or missing actual values across histories. | History CSVs, result feedback. | Updated histories, pending repair audit JSON/TXT. | High |
| `scripts/market_shadow_grading.py` | Grades/aggregates full-market board as diagnostic shadow history. | Full-market board, graded picks/history. | Market shadow report and JSON. | Medium |
| `scripts/write_daily_summary.py` | Daily operator summary plus auxiliary reports/watchlists/history updates. | Boards, Kelly, diagnostics, history. | `daily_summary_<date>.txt`, watchlists, paper Kelly, promotion/correlation/team reports. | High |
| `courtvision/reporting/quality_summary.py` | Large quality/audit report writer and quality history updater. | Boards, diagnostics, Kelly, history. | `quality_summary_<date>.txt/json`, quality history, many shadow/audit reports. | High |
| `courtvision/reporting/completion_state_audit.py` | Compares real, shadow, and paper completion states. | Daily/quality summaries, pending repair audit, histories. | Completion audit JSON/TXT. | Medium |
| `scripts/write_operator_card.py` | Final daily operator card. | Required boards, quality summary, board diagnostics, completion audit, market shadow, injury/game diagnostics. | `operator_card_<date>.txt`. | High |

## 6. Data Flow

### Games

Games are fetched by `CourtVisionAI.predict` through the provider client. The monolithic runtime can use `ProviderClientAdapter` and package `ProviderManager`, but also retains its own `BallDontLieClient`. Games are normalized by `CourtVisionAI._normalize_games` and `courtvision/data/normalization.py:normalize_games_frame`. The normalized game data feeds:

- Candidate game/team lookup in `PredictionPipeline._build_candidate_universe`.
- Game status and game datetime fields.
- Identity quarantine checks in `courtvision/context/player_identity.py` and `courtvision/context/game_context.py`.
- Operator card slate summary.

### Odds

Odds are fetched by `CourtVisionAI.predict` and normalized by `CourtVisionAI._normalize_odds`, `normalize_odds_frame`, and for BallDontLie player props, `normalize_bdl_player_props`. Valid odds rows feed `score_player_markets`.

Important odds fields moving through the system:

- `player_id`
- `player_name`
- `team_abbr`
- `market_type`
- `selection`
- `line`
- `odds`
- `bookmaker`/`vendor`
- `game_id`
- `updated_at`
- `is_live_market`
- `is_synthetic_line`

Odds freshness is diagnosed in the candidate universe and enforced for elite/Kelly through runtime audit gates.

### Player Baselines

`CourtVisionAI.fit` writes `outputs/model/player_baselines.csv`. `CourtVisionAI.predict` requires it before prediction. Player baselines feed:

- Player projection features.
- Player minutes and stat averages.
- Identity resolver data.
- Candidate confidence/scoring.
- Full-market readiness gates.

### Team Baselines

`CourtVisionAI.fit` writes `outputs/model/team_baselines.csv`. Team baselines feed:

- Team context.
- Game/team market projections.
- Passive team strength and matchup context.
- Operator context reporting.

### Injuries

Injuries are fetched through SDK/provider paths in `CourtVisionAI.predict`. If the SDK path is unusable, `client.get_injuries(prediction_date)` is used as fallback. Normalized injuries feed:

- `courtvision/injuries/injury_engine.py:InjuryEngine.build_context`
- `InjuryEngine.apply_context`
- Injury diagnostics and injury context report.
- Elite audit gates for injury/minutes/realism flags.

### Manual Context

Manual context is loaded from `config/manual_player_context_<date>.csv` by `courtvision/context/manual_player_context.py:load_manual_player_context`. It is attached by `CourtVisionAI._attach_manual_player_context` after the package pipeline returns boards.

Important boundary: manual context is documented as passive context attachment. It can influence final safety presentation and diagnostics, but it is not cleanly inside the core candidate-building boundary.

### Candidate Rows

Candidate rows are built by `PredictionPipeline._build_candidate_universe` and `score_player_markets`. They carry:

- Market identity.
- Projection.
- Line and odds.
- Selection/side.
- Edge and side edge.
- Confidence.
- Quality/selection score.
- Injury context.
- Game context/status.
- Identity quarantine fields.
- Live/synthetic/source-lane fields.
- Elite and rejection diagnostics.

Candidate rows are then filtered into operator boards.

### Elite Board

The elite board is the final stake-facing pick board.

Created by:

- `PredictionPipeline.run` through `select_elite_board`.
- `courtvision/selection/operator_boards.py:build_operator_boards`.
- Final context safety in `CourtVisionAI._apply_elite_context_safety_gate`.
- Final write in `courtvision_ai.py:_write_cli_outputs`.

Written to:

- `outputs/runtime/operator/elite_board_<date>.csv`

It must respect market locks, runtime audit gates, identity quarantine, quality/confidence thresholds, and team/game exposure caps.

### Full Market Board

The full-market board is diagnostic/operator context, not staking input by default.

Created by:

- `build_operator_boards` via `select_top_per_market`.
- Includes active operator markets after live/source/identity filters.

Written to:

- `outputs/runtime/operator/full_market_board_<date>.csv`

It feeds:

- Market shadow grading.
- Daily summary.
- Quality summary.
- Operator card preview.
- Promotion/readiness/audit reports.

### Stat-Only Board

The stat-only board is optional/debug output, not a primary operator board. Its canonical path comes from `courtvision/runtime_outputs.py:54-61`, where `stat_only_board_<date>.csv` is mapped to the `optional` lane when `verbose_outputs=True`. `courtvision_ai.py:8956-8957` writes `stat_only_board` only through that layout.

Canonical layout:

- `outputs/runtime/optional/stat_only_board_<date>.csv`

`run_today.ps1` should treat this as an optional/debug artifact when logging row counts. The primary operator board lane remains reserved for elite, full-market, SGP, Kelly, summary, and card artifacts.

### Kelly Stakes

Kelly stakes are calculated only after an elite board exists and has rows.

Created by:

- `scripts/run_kelly_stakes.py`
- `courtvision/betting/kelly.py:compute_kelly_fraction`

Input:

- `outputs/runtime/operator/elite_board_<date>.csv`

Output:

- `outputs/runtime/operator/kelly_stakes_<date>.csv`

The staking script enforces player-points market lock, identity quarantine, context caution, odds/confidence/edge requirements, daily exposure cap, per-pick cap, and manual-review routing.

### Pick History

Real picks are persisted by `scripts/post_run_tracking.py`, which calls `scripts/history_tracking.py:persist_daily_picks`.

Outputs:

- `outputs/runtime/history/picks_<date>.csv`
- `data/history/pick_history.csv`

Pick history is updated by grading and repair scripts. It is long-lived and must not be overwritten casually.

### Grading History

Grading operates on `data/history/pick_history.csv` and runtime result feedback.

Created/updated by:

- `scripts/history_tracking.py:grade_completed_picks`
- `scripts/grade_completed_picks.py`
- `scripts/repair_pending_grades.py`

Outputs include:

- `outputs/runtime/history/graded_picks_<date>.csv`
- `data/history/performance_summary.csv`
- `data/history/performance_by_market.csv`
- `data/history/performance_by_selection.csv`
- `data/history/performance_by_edge_bucket.csv`
- `data/history/performance_by_qualification_reason.csv`
- `data/history/performance_context_cross_slate.csv`

## 7. Candidate Funnel

The live candidate funnel is layered. Some gates are construction-time filters, some are diagnostics, and some are final elite/Kelly gates.

### 1. Provider and Raw Market Support

Odds are fetched, normalized, and mapped to canonical market names. `courtvision/data/bdl_odds_adapter.py:normalize_bdl_player_props` preserves unresolved rows with `unresolved_reason`; `filter_valid_odds` keeps resolved odds rows.

Unsupported raw markets and milestone-style rows are rejected before operator boards.

### 2. Baseline Match

`courtvision/data/candidates.py:score_player_markets` matches odds rows to player baselines by player id first, then normalized name/team where possible. It emits match-rate diagnostics such as baseline intersection and active-board match rate.

### 3. Projection Validity

`PredictionPipeline._build_candidate_universe` computes a projection for single-stat and combo markets. Candidate construction rejects rows without usable projection support, missing line/odds data, or invalid market state.

### 4. Full-Market Readiness Gates

`PredictionPipeline.FULL_MARKET_READINESS_GATES` applies minimum minutes and confidence for non-points markets:

- `player_rebounds`: 24 minutes, 0.60 confidence.
- `player_assists`: 24 minutes, 0.60 confidence.
- Combo markets: 28 minutes, 0.70 confidence.

Rows can be held out or marked not ready when confidence/minutes support is insufficient.

### 5. Injury Context

`InjuryEngine.build_context` and `InjuryEngine.apply_context` attach injury and teammate/opponent context. Injury context can adjust projections/confidence and emit safety flags that later feed elite rejection.

### 6. Identity Quarantine

`CanonicalPlayerIdentityResolver` annotates candidates with canonical identity and conflict fields. `game_context.py` defines quarantine reasons:

- `outside_team_identity`
- `stale_team_identity`
- `game_not_bettable`

`build_operator_boards` removes identity-quarantined rows from operator boards. Kelly also checks `is_identity_quarantined` and writes `identity_quarantine` skip actions.

### 7. Side Edge and Directional Edge

Candidate rows calculate:

- `edge`
- `edge_pct`
- `side_edge`
- `side_edge_pct`

`score_candidate_fn` inside `PredictionPipeline._build_candidate_universe` requires positive side edge, minimum edge, and minimum confidence. `scripts/validate_runtime_outputs.py` later checks directional sanity for elite rows: overs must have positive edge and unders must have negative edge under the legacy interpretation.

### 8. Confidence

Confidence is generated/adjusted from projection support, market quality, injuries, volatility, and scoring policy. Candidate scoring uses confidence as a main component, and elite admission applies `EliteThresholds.default().confidence` plus market-specific confidence thresholds.

### 9. Quality Score and Selection Score

`compute_selection_score` blends edge, confidence, and quality:

- Edge component: 60 percent.
- Confidence component: 30 percent.
- Quality component: 10 percent.

`CandidateScoringPolicy.apply_scoring_metadata` attaches selection/quality fields and elite metadata.

### 10. Live Market Gate

`build_operator_boards` requires live operator market characteristics:

- `is_live_market=True`
- non-synthetic line/source metadata
- acceptable qualification/source lane

This gate keeps synthetic/debug candidates out of operator boards.

### 11. Active Operator Market Gate

`courtvision/selection/operator_boards.py` defines active markets. Current active operator markets include:

- `player_points`
- `player_rebounds`
- `player_assists`
- point/rebound/assist combo markets

Unsupported active operator markets are dropped and summarized.

### 12. Duplicate Betting Identity Dedupe

`build_operator_boards` dedupes duplicate betting identities, keeping the strongest row by context validity, live source, selection score, and quality. This protects against double-counting the same bet across provider/book variants.

### 13. Elite Allowed Market Gate

`PredictionConfig.elite_market_mode` defaults to `points_only`. Unless environment/config explicitly allows more, elite admission is limited to player points. This is reinforced again by Kelly, which currently stakes player-points rows only.

### 14. Elite Runtime Audit Gates

`courtvision/runtime_audit.py:get_elite_rejection_reason` applies final safety checks including:

- Game status ineligibility.
- Odds freshness/staleness.
- Injury/minutes/confidence/realism flags.
- Game context high-caution over rejection.
- Directional edge validity.
- Unrealistic line checks.
- Heavy favorite odds checks.
- Player-points strong-over calibration guard.

`courtvision/runtime_selection.py` also contains overlapping gate functions, including `game_status_ineligibility_reason`, `odds_stale_ineligibility_reason`, and `passes_player_points_strong_over_calibration`.

### 15. Exposure Caps

Elite board selection enforces:

- Team cap: `EliteThresholds.default().team_cap` = 3.
- Game cap: `EliteThresholds.default().game_cap` = 4.
- Board limit ambiguity: `EliteThresholds.default().board_limit` = 20, while the package-owned nested selector currently falls back to `config.elite_size` else 10. See `docs/ELITE_BOARD_LIMIT_AUDIT.md`.

`PredictionPipeline.run` raises if final elite max game exposure exceeds the cap. `_write_cli_outputs` also hard-validates final elite game exposure before writing.

### 16. Odds Freshness and Game Status

Game status and odds freshness are attached during candidate construction and enforced later by runtime audit. `runtime_selection.py` blocks final/live/postponed/locked/unknown game states and stale odds outside research mode. `runtime_audit.py:projected_kelly_skip_reason` mirrors key protections for staking decisions.

Important risk: `COURTVISION_MODE=research` changes game/odds gate behavior, so operator runs must not accidentally inherit research mode.

## 8. Output Artifact Map

### Model Artifacts

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/model/player_baselines.csv` | `CourtVisionAI.fit` | Player baseline projections/features. |
| `outputs/model/team_baselines.csv` | `CourtVisionAI.fit` | Team baseline/context features. |
| `outputs/model/calibration.json` | Calibration/model code | Calibration rules used by prediction/scoring. |

### Runtime Logs

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/logs/run_today_<date>.log` | `run_today.ps1` | Main daily run log. |
| `outputs/runtime/logs/validation_<date>.log` | `run_today.ps1` | Validation/audit stage log. |
| `outputs/runtime/logs/grading_<date>.log` | `run_today.ps1` | Kelly/grading/summary/card stage log. |
| `outputs/runtime/logs/courtvision_ai.log` | `courtvision_ai.py` logger | Canonical runtime log. |

### Primary Operator Artifacts

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/operator/elite_board_<date>.csv` | `_write_cli_outputs` | Stake-facing elite picks. |
| `outputs/runtime/operator/full_market_board_<date>.csv` | `_write_cli_outputs` | Full diagnostic/operator market board. |
| `outputs/runtime/operator/sgp_board_<date>.csv` | `_write_cli_outputs` | SGP board. |
| `outputs/runtime/operator/top_plays_report_<date>.txt` | `_write_cli_outputs` | Human-readable top plays report. |
| `outputs/runtime/operator/elite_decision_report_<date>.txt` | `_write_cli_outputs` | Elite decision report. |
| `outputs/runtime/operator/elite_pipeline_audit_<date>.csv` | `EliteTelemetry.write_outputs` | Elite candidate/rejection telemetry. |
| `outputs/runtime/operator/elite_pipeline_audit_summary_<date>.json` | `EliteTelemetry.write_outputs` | Elite audit summary and exposure analytics. |
| `outputs/runtime/operator/injury_context_report_<date>.txt` | `CourtVisionAI` injury reporting | Injury context report. |
| `outputs/runtime/operator/game_context_report_<date>.txt` | `write_game_context_outputs` | Game context report. |

### Optional/Verbose Prediction Artifacts

These are written only when `--verbose-outputs` enables the optional lane.

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/optional/top_player_edges_<date>.csv` | `_write_cli_outputs` | Debug top player edges. |
| `outputs/runtime/optional/top_game_edges_<date>.csv` | `_write_cli_outputs` | Debug top game edges. |
| `outputs/runtime/optional/stat_only_board_<date>.csv` | `_write_cli_outputs` | Stat-only/debug board. |
| `outputs/runtime/optional/strike_board_<date>.csv` | `_write_cli_outputs` | Strike/debug board. |
| `outputs/runtime/optional/predictive_lines_board_<date>.csv` | `_write_cli_outputs` | Predictive line board. |
| `outputs/runtime/optional/team_board_<date>.csv` | `_write_cli_outputs` | Team board. |
| `outputs/runtime/optional/near_miss_board_<date>.csv` | `_write_cli_outputs` | Near-miss board. |
| `outputs/runtime/optional/board_diagnostics_<date>.csv` | `_write_cli_outputs` | CSV mirror of board diagnostics. |

### Research Artifacts

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/research/player_predictions_<date>.csv` | `_write_cli_outputs` | Player prediction research output. |
| `outputs/runtime/research/game_predictions_<date>.csv` | `_write_cli_outputs` | Game prediction research output. |
| `outputs/runtime/research/player_edges_<date>.csv` | `_write_cli_outputs` | Player edge research output. |
| `outputs/runtime/research/game_edges_<date>.csv` | `_write_cli_outputs` | Game edge research output. |
| `outputs/runtime/research/model_metrics_<date>.json` | `_write_cli_outputs` | Model metrics. |
| `outputs/runtime/research/grading_results_<date>.csv` | Grading path/output layout | Research grading result rows. |

### Diagnostics Artifacts

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/diagnostics/board_diagnostics_<date>.json` | `_write_cli_outputs` and `BoardAuditPolicy` | Board counts, rejection diagnostics, audit payload. |
| `outputs/runtime/diagnostics/player_points_elite_admission_<date>.csv` | `_write_cli_outputs` | Player-points elite admission rows. |
| `outputs/runtime/diagnostics/player_points_elite_admission_<date>.json` | `_write_cli_outputs` | Player-points admission summary. |
| `outputs/runtime/diagnostics/market_coverage_<date>.json` | `_write_cli_outputs` | Market coverage diagnostics. |
| `outputs/runtime/diagnostics/market_availability_audit_<date>.csv` | `PredictionPipeline._write_market_availability_audit` | Market availability diagnostics. |
| `outputs/runtime/diagnostics/market_availability_audit_<date>.json` | `PredictionPipeline._write_market_availability_audit` | Market availability summary. |
| `outputs/runtime/diagnostics/market_performance_readiness_<date>.json` | `PredictionPipeline._write_market_performance_readiness` | Market readiness gate diagnostics. |
| `outputs/runtime/diagnostics/manual_context_<date>.json` | `write_manual_context_diagnostics` | Manual context diagnostics. |
| `outputs/runtime/diagnostics/game_context_<date>.json` | `write_game_context_outputs` | Game context diagnostics. |
| `outputs/runtime/diagnostics/injury_context_diagnostics_<date>.json` | `CourtVisionAI` injury diagnostics | Injury context diagnostics. |
| `outputs/runtime/diagnostics/grading_summary_<date>.json` | Grading/reporting | Grading summary. |
| `outputs/runtime/diagnostics/player_points_calibration_<date>.json` | Grading/output layout | Player-points calibration. |

### Validation and Audit Scripts

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/operator/full_market_sanity_audit_<date>.txt` | `scripts/audit_full_market_sanity.py` | Human-readable full-market sanity audit. |
| `outputs/runtime/diagnostics/full_market_sanity_audit_<date>.json` | `scripts/audit_full_market_sanity.py` | Full-market sanity payload. |
| `outputs/runtime/operator/candidate_quality_drift_audit_<date>.txt` | `scripts/audit_candidate_quality_drift.py` | Human-readable quality drift audit. |
| `outputs/runtime/diagnostics/candidate_quality_drift_audit_<date>.json` | `scripts/audit_candidate_quality_drift.py` | Quality drift payload. |

### Kelly and Daily Operator Reports

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/operator/kelly_stakes_<date>.csv` | `scripts/run_kelly_stakes.py` | Kelly stake rows and skip reasons. |
| `outputs/runtime/operator/daily_summary_<date>.txt` | `scripts/write_daily_summary.py` | Daily operator summary. |
| `outputs/runtime/operator/quality_summary_<date>.txt` | `scripts/write_quality_summary.py` | Quality/audit summary. |
| `outputs/runtime/operator/quality_summary_<date>.json` | `scripts/write_quality_summary.py` | Quality summary payload. |
| `outputs/runtime/operator/quality_history.csv` | `quality_summary.update_quality_history_from_summary` | Cross-slate quality history. |
| `outputs/runtime/operator/quality_history.jsonl` | `quality_summary.update_quality_history_from_summary` | JSONL quality history. |
| `outputs/runtime/operator/completion_state_audit_<date>.txt` | `scripts/write_completion_state_audit.py` | Completion state audit report. |
| `outputs/runtime/diagnostics/completion_state_audit_<date>.json` | `scripts/write_completion_state_audit.py` | Completion audit payload. |
| `outputs/runtime/operator/operator_card_<date>.txt` | `scripts/write_operator_card.py` | Final daily operator card. |

### Daily Summary Auxiliary Reports

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/operator/high_caution_over_watchlist_<date>.csv` | `write_high_caution_over_watchlist` | High-caution over watchlist. |
| `outputs/runtime/operator/combo_under_watchlist_<date>.csv` | `write_combo_under_watchlist` | Combo under watchlist. |
| `outputs/runtime/operator/promotion_readiness_report_<date>.txt/csv` | `write_promotion_readiness_report` | Shadow promotion readiness. |
| `outputs/runtime/operator/paper_kelly_simulation_<date>.txt/csv` | `write_paper_kelly_simulation` | Paper Kelly simulation. |
| `outputs/runtime/operator/paper_kelly_performance_report_<date>.txt/csv` | `write_paper_kelly_performance_report` | Paper Kelly performance report. |
| `outputs/runtime/operator/correlation_exposure_report_<date>.txt/csv` | `write_correlation_exposure_report` | Correlation exposure report. |
| `outputs/runtime/operator/team_distribution_report_<date>.txt/csv` | `write_team_distribution_report` | Team distribution/exposure report. |
| `outputs/runtime/operator/same_opponent_under_warnings_<date>.csv` | `same_opponent_rematch` reporting | Same-opponent under warnings. |

### Quality Summary Shadow/Audit Reports

`courtvision/reporting/quality_summary.py` also writes or references many audit-only/shadow artifacts. Major patterns include:

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/diagnostics/fragility_survivability_shadow_eval_<date>.json` | `write_shadow_eval_report` | Fragility survivability shadow eval. |
| `outputs/runtime/operator/fragility_survivability_shadow_eval_<date>.txt` | `write_shadow_eval_report` | Operator text for fragility eval. |
| `outputs/runtime/diagnostics/fragility_outcome_validation_<date>.json` | `write_fragility_outcome_validation` | Fragility outcome validation. |
| `outputs/runtime/operator/fragility_outcome_validation_<date>.txt` | `write_fragility_outcome_validation` | Operator text for fragility validation. |
| `outputs/runtime/diagnostics/fragility_shadow_policy_simulation_<date>.json` | `write_policy_simulation_report` | Fragility policy simulation. |
| `outputs/runtime/operator/fragility_shadow_policy_simulation_<date>.txt` | `write_policy_simulation_report` | Operator text for simulation. |
| `outputs/runtime/diagnostics/projection_calibration_shadow_<date>.json` | `write_projection_calibration_report` | Projection calibration shadow analytics. |
| `outputs/runtime/operator/projection_calibration_shadow_<date>.txt` | `write_projection_calibration_report` | Operator text for projection calibration. |
| `outputs/runtime/diagnostics/projection_bias_attribution_<date>.json` | `write_projection_bias_attribution` | Projection bias attribution. |
| `outputs/runtime/operator/projection_bias_attribution_<date>.txt` | `write_projection_bias_attribution` | Operator text for projection bias. |
| `outputs/runtime/diagnostics/edge_inflation_research_<date>.json` | `write_edge_inflation_research` | Edge inflation research. |
| `outputs/runtime/operator/edge_inflation_research_<date>.txt` | `write_edge_inflation_research` | Operator text for edge inflation. |
| `outputs/runtime/diagnostics/edge_containment_shadow_validation_<date>.json` | `write_edge_containment_shadow_validation` | Edge containment shadow validation. |
| `outputs/runtime/operator/edge_containment_shadow_validation_<date>.txt` | `write_edge_containment_shadow_validation` | Operator text for edge containment. |
| `outputs/runtime/operator/edge_containment_review_flags_<date>.csv` | `write_edge_containment_review_flags` | Edge containment review flags. |
| `outputs/runtime/diagnostics/edge_containment_forward_tracker_<date>.json` | `write_edge_containment_forward_tracker` | Edge containment forward tracker. |
| `outputs/runtime/operator/edge_containment_forward_tracker_<date>.txt` | `write_edge_containment_forward_tracker` | Operator tracker text. |
| `outputs/runtime/diagnostics/edge_containment_hold_control_<date>.json` | `write_hold_control_artifacts` | Edge containment hold control. |
| `outputs/runtime/operator/edge_containment_hold_review_flags_<date>.csv` | `write_hold_control_artifacts` | Hold-control review flags. |
| `outputs/runtime/diagnostics/combo_projection_math_audit_<date>.json` | `write_combo_projection_math_audit` | Combo projection math audit. |
| `outputs/runtime/operator/combo_projection_math_audit_<date>.txt` | `write_combo_projection_math_audit` | Combo projection audit text. |
| `outputs/runtime/diagnostics/player_points_inflation_audit_<date>.json` | `write_player_points_inflation_audit` | Player-points inflation audit. |
| `outputs/runtime/operator/player_points_inflation_audit_<date>.txt` | `write_player_points_inflation_audit` | Inflation audit text. |
| `outputs/runtime/diagnostics/minutes_availability_audit_<date>.json` | `write_minutes_availability_audit` | Minutes availability audit. |
| `outputs/runtime/operator/minutes_availability_audit_<date>.txt` | `write_minutes_availability_audit` | Minutes availability text. |
| `outputs/runtime/diagnostics/actual_minutes_source_audit_<date>.json` | `write_actual_minutes_source_audit` | Actual minutes source audit. |
| `outputs/runtime/operator/actual_minutes_source_audit_<date>.txt` | `write_actual_minutes_source_audit` | Actual minutes source text. |
| `outputs/runtime/diagnostics/minutes_error_shadow_audit_<date>.json` | `write_minutes_error_shadow_audit` | Minutes error shadow audit. |
| `outputs/runtime/operator/minutes_error_shadow_audit_<date>.txt` | `write_minutes_error_shadow_audit` | Minutes error text. |
| `outputs/runtime/operator/low_line_over_minutes_guard_review_<date>.csv/json/txt` | Low-line guard reporting | Review-only low-line over guard audit. |
| `outputs/runtime/operator/low_line_over_minutes_guard_policy_simulation_<date>.csv/json/txt` | Low-line guard policy simulation | Simulation-only low-line guard report. |
| `outputs/runtime/operator/low_line_over_minutes_guard_outcome_<date>.csv/json/txt` | Low-line guard outcome reporting | Outcome validation. |
| `outputs/runtime/operator/low_line_over_minutes_guard_missed_winner_attribution_<date>.csv/json/txt` | Low-line guard attribution | Missed-winner attribution. |

### History Artifacts

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/runtime/history/picks_<date>.csv` | `persist_daily_picks` | Runtime copy of current elite picks. |
| `outputs/runtime/history/graded_picks_<date>.csv` | `grade_completed_picks` | Runtime graded picks for date. |
| `outputs/runtime/history/result_feedback.csv` | Feedback/result ingestion path | Actual result feedback used by grading. |
| `data/history/pick_history.csv` | `persist_daily_picks`, grading/repair | Long-lived real-pick history. |
| `data/history/market_shadow_history.csv` | `persist_market_shadow_history` | Long-lived full-market shadow history. |
| `data/history/market_readiness_summary.csv` | `update_market_readiness_summary` | Market readiness rollup. |
| `data/history/paper_kelly_history.csv` | `persist_paper_kelly_history` | Long-lived paper Kelly history. |
| `data/history/performance_summary.csv` | `update_performance_summaries` | Daily performance summary. |
| `data/history/performance_by_market.csv` | `update_performance_summaries` | Performance by market. |
| `data/history/performance_by_selection.csv` | `update_performance_summaries` | Performance by side. |
| `data/history/performance_by_edge_bucket.csv` | `update_performance_summaries` | Performance by edge bucket. |
| `data/history/performance_by_qualification_reason.csv` | `update_performance_summaries` | Performance by qualification reason. |
| `data/history/performance_context_cross_slate.csv` | `update_performance_summaries` | Cross-slate context performance. |
| `data/history/prediction_history.csv` | `CourtVisionAI` history hook | Prediction history. |
| `data/history/manual_review_history.csv` | Manual review tooling | Operator manual-review decisions. |
| `data/history/game_results.csv` | Result feedback/data flow | Game result data. |

### Compatibility Outputs

| Pattern | Creator | Purpose |
|---|---|---|
| `outputs/boards/<date>/...` | `scripts/run_daily.py` via `save_prediction_boards` | Compatibility board package. |
| `outputs/manifests/prediction_<date>.json` | `scripts/run_daily.py` via `write_manifest` | Compatibility manifest. |

## 9. Critical Invariants

1. `courtvision_ai.py` is the canonical runtime entrypoint. Operator scripts may wrap it, but should not fork live prediction behavior.

2. `run_today.ps1` is the canonical operator workflow. `scripts/run_daily.py` is a compatibility shim and should not become a parallel production path without an explicit architecture decision.

3. Closed slates must not be regenerated unless intentionally forced. `-ForcePastDate` is dangerous because it can overwrite date-scoped outputs and alter historical interpretation.

4. Grading and history artifacts must be preserved. Files under `data/history/` are long-lived state, not disposable runtime output.

5. Player identity must be stable. Identity quarantine must run before operator board use and before staking.

6. Full-market board rows are diagnostic unless explicitly eligible. Full-market rows must not feed Kelly by accident.

7. Kelly must read from the elite board, not the full-market board.

8. Elite board must respect exposure caps:
   - Team cap: 3.
   - Game cap: 4.
   - Board limit: 20.

9. Game status and odds freshness must gate stake-facing outputs. Research-mode bypasses must not leak into operator runs.

10. Player-points strong-over calibration guard must remain enforced until deliberately recalibrated.

11. Synthetic, partial-fill, stale, or unsupported market rows must not become stakeable without explicit promotion.

12. Manual context and game context must remain visible in diagnostics and card output; silent context loss is a safety issue.

13. Artifact date must match requested prediction date. `guard_prediction_artifact_date` should remain in all date-scoped write paths.

14. Protected operator board overwrites must require explicit force flags and should be logged.

15. Operator card final decision must treat full-market preview rows as diagnostic only, especially on `NO BET` days.

## 10. Architecture Risks and Technical Debt

### Critical Risks

1. `courtvision_ai.py` is a god file.
   - It owns provider clients, runtime orchestration, fit/predict, fallback paths, legacy prediction logic, context attachment, board writing, history hooks, Telegram, CLI, and utility functions.
   - It is too large and too powerful for a bankroll-facing runtime. Any change risks accidental side effects.

2. Provider abstraction is transitional and duplicated.
   - `courtvision_ai.py` contains `BallDontLieClient`.
   - `courtvision_ai.py` also contains `ProviderClientAdapter`.
   - `courtvision/clients/balldontlie_client.py` contains `BalldontlieClient`.
   - `courtvision/clients/provider_manager.py` contains `ProviderManager`.
   - `courtvision/clients/sportsdataio_client.py` contains `SportsDataIOClient`.
   - This makes it hard to prove which client behavior is live and which is legacy or fallback.

3. Candidate and selection logic is split across too many layers.
   - Candidate construction is in `predict_pipeline.py` nested closures plus `data/candidates.py`.
   - Scoring is in `scoring/*`, with `runtime_scoring.py` as a facade.
   - Selection is in `selection/operator_boards.py`, nested `select_elite_board`, `runtime_audit.py`, and `runtime_selection.py`.
   - The result is correct-looking but hard to reason about end-to-end.

4. Elite gates are duplicated/overlapping.
   - `runtime_audit.py:get_elite_rejection_reason` is the final active elite rejection function.
   - `runtime_selection.py:get_elite_rejection_reason` also exists.
   - Game status and odds freshness helpers exist in `runtime_selection.py`, while the live elite/Kelly gate path is in `runtime_audit.py`.
   - This creates drift risk when one gate is updated and another is not.

5. Warning-only audit paths can let risky output continue.
   - `run_today.ps1` treats `audit_full_market_sanity.py` and `audit_candidate_quality_drift.py` failures as warnings.
   - That may be appropriate for diagnostics, but bankroll-facing workflows need a clearly documented severity policy.

### High Risks

6. Artifact overwrite safety is uneven.
   - Protected board writes use guards.
   - Kelly and operator card also use overwrite guards.
   - Some summary/report writers intentionally rewrite or mutate board annotations/history.
   - `-ForcePastDate` plus force flags can alter closed-slate operator history.

7. Optional board lane drift.
   - `OutputLayoutPolicy` intentionally writes verbose/debug boards such as `stat_only_board_<date>.csv` to `outputs/runtime/optional/`.
   - Operator scripts and docs should keep treating stat-only output as optional/debug, not as a primary operator board.

8. `COURTVISION_MODE=research` changes safety behavior.
   - `runtime_selection.py` game/odds gating behaves differently in research mode.
   - This is powerful, but dangerous if the environment leaks into an operator run.

9. Manual/game context attachment happens after the package pipeline returns.
   - `CourtVisionAI._attach_manual_player_context` and `_attach_game_context` run after initial package pipeline board construction.
   - Final context safety catches some risks, but the boundary is not clean. Context should ideally be first-class candidate input before selection.

10. Candidate row shape is too broad.
   - Candidate rows carry projections, odds, identity, injury, context, selection, Kelly-like fields, diagnostics, and reporting metadata.
   - This weakly typed wide-row style is fragile and makes schema regressions likely.

11. Kelly-like calculations occur before Kelly.
   - Candidate construction computes stake/recommended bet metadata.
   - The real stake-facing script is `scripts/run_kelly_stakes.py`.
   - Having pre-Kelly values on candidate rows can confuse operator/reporting semantics.

12. History mutation is spread across multiple scripts.
   - `post_run_tracking.py`, `history_tracking.py`, `grade_completed_picks.py`, `repair_pending_grades.py`, `write_daily_summary.py`, and paper Kelly reporting can all affect history-like outputs.
   - This increases risk of accidental closed-slate mutation.

13. Quality summary has become a report orchestrator god module.
   - `courtvision/reporting/quality_summary.py` imports and triggers many shadow/audit modules.
   - It now owns summary writing, history writing, fragility audits, edge containment, calibration shadows, minutes audits, and completion audit hooks.
   - It is becoming the reporting equivalent of `courtvision_ai.py`.

### Medium Risks

14. Legacy paths remain imported or adjacent to live paths.
   - `CourtVisionAI.predict` still contains a legacy pipeline branch behind `COURTVISION_ENABLE_LEGACY_PIPELINE`.
   - `courtvision/selection/boards.py` and `courtvision/market/value_engine.py` appear older/less central than `operator_boards.py`.
   - These paths add cognitive load and increase accidental-use risk.

15. Output taxonomy is large and hard to audit.
   - `operator`, `diagnostics`, `research`, `optional`, `history`, and compatibility `outputs/boards` all exist.
   - Some reports write JSON to diagnostics and TXT/CSV to operator; others write CSVs only to operator.
   - A single artifact manifest would make daily completeness easier to verify.

16. Script fragility.
   - `run_today.ps1` is necessarily procedural and stateful.
   - It mixes validation, orchestration, logging, and recovery advice in one file.
   - It is readable but brittle as the post-run workflow grows.

17. Silent fallback behavior can hide provider/data quality problems.
   - Provider manager fallback is useful, but provider quality and source-of-truth diagnostics must be prominent.
   - Fallback from SDK injuries to HTTP injuries is practical but should always remain visible in diagnostics.

## 11. Recommended Refactor Roadmap

### Phase A: Documentation and Audit Visibility

1. Add a canonical runtime map to `docs/` and keep this audit updated.
2. Add a daily artifact manifest written by `run_today.ps1` or `_write_cli_outputs`.
3. Document severity policy for every audit:
   - Fatal
   - Warning
   - Informational
   - Shadow only
4. Keep the `stat_only_board` optional-lane contract covered by tests and reflected in operator comments.
5. Add a "runtime mode" banner to operator logs/card showing `COURTVISION_MODE`, provider, and force flags.

### Phase B: Provider Abstraction Cleanup

1. Make `courtvision/clients/provider_manager.py` the only provider facade used by live runtime.
2. Move `courtvision_ai.py:BallDontLieClient` behavior into package clients or delete it after parity tests.
3. Make odds, injuries, games, and stats provider results return typed/domain objects or stable DataFrames with schema validators.
4. Require provider source and fallback reason in all operator diagnostics.

### Phase C: Candidate Funnel Modularization

1. Extract `PredictionPipeline._build_candidate_universe` nested callbacks into named services:
   - Projection builder.
   - Edge calculator.
   - Candidate scorer.
   - Candidate gate evaluator.
   - Candidate diagnostics builder.
2. Consolidate elite rejection logic into one module. Prefer `runtime_audit.py` or a new `selection/gates.py`.
3. Make game-status and odds-freshness gates first-class candidate/elite gates with shared reason codes.
4. Remove pre-Kelly stake metadata from candidate construction or clearly rename it as non-operative diagnostics.

### Phase D: Artifact and History Safety

1. Centralize all date-scoped artifact writes behind `artifact_guard.py`.
2. Add a closed-slate mutation guard for `data/history/` and board annotation rewrites.
3. Split "write new date" from "repair historical date" commands.
4. Make `run_today.ps1 -ForcePastDate` require a second explicit flag for history mutation.
5. Write a machine-readable run manifest containing all artifacts, checksums, row counts, and status.

### Phase E: Testing and CI Hardening

1. Add contract tests for candidate row schemas.
2. Add golden-slate tests for:
   - Identity quarantine.
   - Odds freshness.
   - Game status.
   - Exposure caps.
   - Player-points strong-over calibration guard.
3. Add tests proving full-market rows cannot feed Kelly unless explicitly eligible.
4. Add tests for `COURTVISION_MODE=research` versus operator mode.
5. Add integration tests for `run_today.ps1` stages using fixture data, not live providers.
6. Add artifact-overwrite tests for closed slates and history repair scripts.

## 12. Commands Used

Only read-only repository/source inspection commands were used, except for creating this requested Markdown report.

```powershell
git status --short --branch
Get-ChildItem -Force
rg --files
Get-Content AGENTS.md
Get-Content run_today.bat
Get-Content run_today.ps1
Get-Content scripts\run_daily.py
Get-Content courtvision_ai.py
Get-Content courtvision\runtime_outputs.py
Get-Content courtvision\config\__init__.py
Get-Content courtvision\betting\kelly.py
Get-Content courtvision\artifact_guard.py
Get-Content scripts\validate_runtime_outputs.py
Get-Content scripts\run_kelly_stakes.py
Get-Content scripts\post_run_tracking.py
Get-Content scripts\grade_completed_picks.py
Get-Content scripts\repair_pending_grades.py
Get-Content scripts\market_shadow_grading.py
Get-Content scripts\write_daily_summary.py
Get-Content scripts\write_quality_summary.py
Get-Content scripts\write_completion_state_audit.py
Get-Content scripts\write_operator_card.py
rg -n "^(class|def|    def)" courtvision_ai.py courtvision scripts -g "*.py"
rg -n "courtvision_ai.py|--fit-only|--predict-only|validate_runtime_outputs|run_kelly_stakes|post_run_tracking|grade_completed_picks|market_shadow_grading|write_daily_summary|write_quality_summary|write_completion_state_audit|write_operator_card|audit_full_market_sanity|audit_candidate_quality_drift|ForcePastDate|Baseline" run_today.ps1
rg -n "stat_only_board|optional|operator" courtvision_ai.py courtvision\runtime_outputs.py run_today.ps1 scripts -g "*.py"
rg -n "pick_history|market_shadow_history|performance_summary|graded_picks|result_feedback|paper_kelly_history|market_readiness_summary|to_csv\(|write_text\(|Path\()" scripts\history_tracking.py
rg -n "quality_summary|QUALITY_HISTORY|write_text\(|to_csv\(|operator /|diagnostics /|history" courtvision\reporting\quality_summary.py
rg -n "completion_state_audit|write_text\(|json_path|text_path|operator|diagnostics" courtvision\reporting\completion_state_audit.py
rg -n "watchlist_path_for_date|report_paths_for_date|history_path|write_.*report|to_csv\(|write_text\(|operator /|diagnostics /|history_root" courtvision\reporting\*.py
Get-ChildItem outputs\model, outputs\runtime\operator, outputs\runtime\diagnostics, outputs\runtime\logs, data\history -Force -ErrorAction SilentlyContinue
Get-ChildItem docs -Force -ErrorAction SilentlyContinue
```

No tests were run. No full daily pipeline was run.

## 13. Final Verdict

Architecture grade: C+

CourtVision has a serious production mindset: it has explicit operator orchestration, protected date-scoped board writes, closed-slate warnings, identity quarantine, elite exposure caps, odds/game-status gates, Kelly staking isolation, rich diagnostics, and long-lived performance tracking. Those are exactly the right instincts for a bankroll-facing prediction system.

The risk is architectural concentration and drift. `courtvision_ai.py` and `quality_summary.py` are both too large. Provider access is duplicated. Selection and rejection logic is split across nested closures, `operator_boards.py`, `runtime_audit.py`, and `runtime_selection.py`. History mutation is spread across several scripts. The system is operationally capable, but the boundaries are weak enough that future changes can accidentally bypass an important safety gate.

The next fix should be boundary cleanup, not model tuning: make one provider facade live, consolidate elite/Kelly gate reasons, extract the candidate funnel into named modules, and add a daily artifact manifest with fatal/warning severity policy. That will make the system safer before any further bankroll-facing behavior changes.
