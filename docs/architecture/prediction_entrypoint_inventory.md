# Prediction entrypoint inventory

Last verified: 2026-07-25

## Scope and classification method

This inventory covers non-test source that either creates model-derived
predictions, exposes a caller that can trigger creation, writes a canonical
prediction artifact, or was identified as a nearby collection, settlement,
grading, reporting, history, diagnostics, or compatibility path. Generated
outputs, fixtures, caches, virtual environments, `.git`, and
`.claude/worktrees` are excluded. `.claude/worktrees` was not modified.

Each file has exactly one classification. “Creates probabilities” means new
model probabilities or projections are calculated for a requested date.
“Creates picks/boards” means a model-derived candidate, pick, watchlist, or
operator board is assembled. “Canonical artifacts” means the NBA dated
prediction package or the immutable MLB `predictions.csv` bundle.

| File | Classification | Public command or caller | Creates probabilities | Creates picks/boards | Writes canonical artifacts | Calls `CourtVisionAI.predict()` | Lifecycle | Recommended disposition |
|---|---|---|---:|---:|---:|---:|---:|---|
| `courtvision_ai.py` | `prediction-production` | `python courtvision_ai.py predict --sport nba --mode production --prediction-date DATE`; compatibility flags remain | Yes, through active NBA implementation | Yes | Yes, through prediction publication transaction | Compatibility method only; internal adapter calls `_predict_internal` | Yes | Supported CLI and NBA adapter; keep model logic unchanged and keep application orchestration in `courtvision/prediction/`. |
| `courtvision/prediction/contracts.py` | `prediction-production` | Imported by all prediction entrypoints | No | No | No | No | Result carries outcome | Canonical request/result/engine contracts. |
| `courtvision/prediction/application.py` | `prediction-production` | `PredictionApplicationService.run()` | No | No | Coordinates | No | Owns begin/commit/failure | Sole application orchestration layer. |
| `courtvision/prediction/registry.py` | `prediction-production` | `PredictionEngineRegistry` | No | No | No | No | No | Explicit sport/mode engine allowlist. |
| `courtvision/prediction/publication.py` | `prediction-production` | Publisher adapters and atomic write helpers | No | No | Yes | No | Provides hashes to lifecycle/manifest | Sole low-level prediction publication implementation. |
| `courtvision/prediction/__init__.py` | `prediction-production` | Package exports | No | No | No | No | No | Stable canonical package surface. |
| `courtvision/pipeline/predict_pipeline.py` | `prediction-production` | Called by the NBA adapter; `run_prediction_pipeline()` is an in-memory research/test helper | Yes | Yes | No | No | No | One active package-owned NBA calculation pipeline; it must not become a public writer. |
| `courtvision_ai.py:CourtVisionAI._predict_internal` | `prediction-production` | Only `_NBAPredictionEngine.execute()` | Yes | Yes | No canonical CLI package; diagnostics/history remain existing behavior | No | Observer callback only | Transitional active NBA engine implementation. Do not call outside the approved adapter. |
| `courtvision_streamlit_app.py` | `prediction-wrapper` | Streamlit “Run Predictions” action | No | No | Delegates | No | Displays status | Keep UI-only. Duplicate clicks, cross-process conflicts, overwrites, degraded lifecycle, and publication failures are surfaced. |
| `run_today.ps1` | `prediction-wrapper` | `.\run_today.ps1 -Date DATE` | No | No | Delegates | No | Through CLI | Operational wrapper. Existing-artifact no-op and reporting-only refresh guidance remain authoritative. |
| `run_today.bat` | `prediction-wrapper` | `run_today.bat [DATE]` | No | No | Delegates to PowerShell | No | Through CLI | Retain Windows convenience wrapper. |
| `scripts/run_daily.py` | `prediction-wrapper` | `python scripts/run_daily.py --prediction-date DATE` | No | No | Delegates to supported CLI | No | Through CLI | Deprecated compatibility wrapper; no independent publisher. |
| `courtvision/application.py` | `prediction-wrapper` | `CourtVisionApplication.run_prediction()` | No | No | Application manifest only | No direct public call; compatibility adapter uses internal engine seam | Yes | Retain for imports/grading compatibility with `DeprecationWarning`; canonical service owns prediction. |
| `courtvision/pipeline/runner.py` | `prediction-wrapper` | Deprecated `save_prediction_boards()` and legacy pipeline manifests | No | No | Compatibility publication only, via approved helpers | No | No | Retain for compatibility/tests; not an approved live entrypoint. |
| `courtvision/pipeline/contracts.py` | `test-support` | Legacy pipeline result/manifest types | No | No | No | No | No | Retain until non-prediction grading manifest users migrate. |
| `courtvision/pipeline/stages.py` | `test-support` | Legacy `PipelineRunner` stages | No | No | No | No | No | Retain for grading compatibility. |
| `courtvision/engine.py` | `legacy-unused` | `CourtVisionPro.predict()` import compatibility | Yes if manually invoked | Yes | Writes legacy `outputs/` reports/history | No | No | Explicitly non-canonical per ADR-001; emits deprecation warning. Do not promote or delete without a separate parity decision. |
| `courtvision/sports/nba/runtime.py` | `legacy-unused` | Re-export of `CourtVisionPro` | No itself | No | No | No | No | Import compatibility only. |
| `courtvision/sports/nba/projection.py` | `test-support` | Offline projection helper | Yes, deterministic projection value | No | No | No | No | Calculation helper, not a dated prediction workflow. |
| `courtvision/reporting/text_report.py` | `legacy-unused` | `CourtVisionPro` report writer | No | Formats legacy board | Legacy paths only | No | No | Keep only for `CourtVisionPro` compatibility. |
| `courtvision/runtime_outputs.py` | `prediction-production` | `OutputLayoutPolicy.prediction_paths()` | No | No | Defines NBA canonical paths | No | No | Approved path policy; no independent writes. |
| `courtvision/artifact_guard.py` | `prediction-production` | Date and overwrite guards | No | No | Guards only | No | No | Shared guard used by publication. |
| `courtvision/lifecycle/publication.py` | `prediction-production` | Loaded by `ShadowPredictionLifecycle` | No | No | Lifecycle evidence only | No | Yes | Structured identity is application/sport/mode/run scoped; no CLI-specific actor assumption. |
| `courtvision/shadow_lifecycle.py` | `prediction-production` | Feature-flagged hook loader | No | No | Lifecycle only | No | Yes | Preserve import isolation and degraded behavior. |
| `courtvision/sports/mlb/training/hr_research_baseline.py` | `prediction-research` | `predict`, `run-daily-research`, and `generate_daily_research_predictions()`; also reached by `courtvision_ai.py predict --sport mlb --mode research` | Yes | Creates immutable research prediction rows | Yes, MLB-only research directory | No | Yes when enabled | Approved MLB research engine and compatibility commands. Settlement/report subcommands remain outside prediction execution. |
| `courtvision/sports/mlb/hr_pipeline.py` | `test-support` | Offline sample/fixture research pipeline | No calibrated probability; produces research scores | Research watchlist only | No canonical prediction artifact | No | No | Keep offline/sample-only. It is not the daily logistic prediction engine. |
| `courtvision/sports/mlb/hr_prop_engine.py` | `test-support` | Called by offline HR fixture pipeline | No; score only | Research labels | No | No | No | Model-score helper, not a public prediction entrypoint. |
| `courtvision/sports/mlb/projection.py` | `test-support` | Placeholder `MLBProjectionModel.project()` | Projection placeholder | No | No | No | No | Not approved for production or daily MLB HR prediction. |
| `courtvision/sports/wnba/projection.py` | `test-support` | Placeholder projection API | Projection placeholder | No | No | No | No | No live provider or prediction workflow. |
| `courtvision/sports/nfl/projection.py` | `test-support` | Placeholder projection API | Projection placeholder | No | No | No | No | No live provider or prediction workflow. |
| `courtvision/operations.py` | `grading` | Restricted facade used by downstream scripts | No; `predict()` fails closed | No | No | No | No | Temporary parity facade for provider/grading helpers. Remove remaining legacy-runtime composition only in a separate grading extraction. |
| `scripts/backfill_grading.py` | `grading` | Backfill CLI/functions | No | No | No | No | No | Use restricted operations facade; may fetch completed stats only. |
| `scripts/grade_market_shadow_history.py` | `grading` | Shadow-history grading CLI | No | No | No | No | No | Provider/stat lookup only; cannot enter prediction service. |
| `scripts/market_shadow_grading.py` | `grading` | Market shadow grading builder | No | No | No | No | No | Remains downstream of existing prediction artifacts. |
| `scripts/grade_completed_picks.py` | `grading` | Completed-pick grading command | No | No | No | No | No | Keep outside prediction service. |
| `scripts/run_grading.py` | `grading` | Grading compatibility CLI | No | No | No | No | No | May write grading manifest only. |
| `scripts/nightly_grade_and_refresh.py` | `grading` | Nightly grader/report refresh | No | No | No | No | No | Must never invoke a prediction engine. |
| `scripts/history_tracking.py` | `history` | Post-run/history update functions | No | No | No | No | No | Restricted operations facade; preserves graded fields and history guards. |
| `scripts/prefill_actual_feedback.py` | `history` | Closed-slate actual feedback prefill | No | No | No | No | No | Completed-game/stat access only. |
| `scripts/update_game_results_history.py` | `history` | Game-result history CLI | No | No | No | No | No | Provider game fetch only. |
| `scripts/post_run_tracking.py` | `history` | Post-run tracking command | No | No | No | No | No | Consumes already-published artifacts. |
| `scripts/backfill_market_shadow_history.py` | `history` | Shadow-history reconstruction | No | No | No | No | No | Must not regenerate dated predictions. |
| `courtvision/sports/nba/player_points_settlement.py` | `settlement` | NBA research settlement API | No | No | No | No | No | Keep outside prediction service. |
| `courtvision/sports/nba/player_points_settlement_closing_binding.py` | `settlement` | Closing-line binding | No | No | No | No | No | Existing-prediction evidence only. |
| `courtvision/sports/mlb/training/hr_one_shot_test_executor.py` | `settlement` | One-shot frozen test evaluation | No new live probabilities | No new live picks | Test result artifacts only | No | No | Do not route through prediction service. |
| `scripts/mlb_plan_hr_one_shot_test_evaluation.py` | `test-support` | Evaluation plan command | No | No | No | No | No | Planning only. |
| `scripts/mlb_dry_run_hr_evaluator.py` | `test-support` | Dry-run evaluator | No new daily predictions | No | No | No | No | Evaluation only. |
| `scripts/mlb_dry_run_hr_frozen_predictions.py` | `test-support` | Frozen-prediction dry run | Uses frozen values | No new live picks | Test artifacts only | No | No | Excluded from live architecture enforcement. |
| `scripts/mlb_dry_run_hr_research_backtest.py` | `test-support` | Historical backtest | Historical estimates only | Backtest rows | Backtest artifacts only | No | No | Not a current-day prediction entrypoint. |
| `scripts/courtvision_collect_sources.py` | `collection` | Multi-source collection CLI | No | No | No | No | No | Collection boundary remains independent. |
| `scripts/courtvision_collector_doctor.py` | `diagnostics` | Collector doctor | No | No | No | No | No | Diagnostics only. |
| `courtvision/data_collection/core.py` | `collection` | Collector orchestration | No | No | No | No | No | No dependency on prediction service. |
| `courtvision/data_collection/registry.py` | `collection` | Collector registry | No | No | No | No | No | Separate from prediction engine registry. |
| `courtvision/sports/mlb/data_collection/adapter.py` | `collection` | MLB collection adapter | No | No | No | No | No | Collection only. |
| `courtvision/sports/mlb/data/odds_snapshot_ingestion.py` | `collection` | Local odds ingestion | No | No | No | No | No | Input custody only. |
| `scripts/mlb_ingest_statcast.py` | `collection` | Statcast ingestion CLI | No | No | No | No | No | Input collection only. |
| `scripts/mlb_ingest_retrosheet.py` | `collection` | Retrosheet ingestion CLI | No | No | No | No | No | Input collection only. |
| `scripts/mlb_ingest_weather.py` | `collection` | Weather ingestion CLI | No | No | No | No | No | Input collection only. |
| `scripts/mlb_ingest_ballpark_factors.py` | `collection` | Ballpark ingestion CLI | No | No | No | No | No | Input collection only. |
| `scripts/audit_full_market_sanity.py` | `diagnostics` | Sanity audit CLI | No | No | No | No | No | Read-only prediction artifact audit. |
| `scripts/audit_candidate_quality_drift.py` | `diagnostics` | Quality drift audit | No | No | No | No | No | Read-only/model-monitoring output only. |
| `scripts/validate_runtime_outputs.py` | `diagnostics` | Runtime validator | No | No | No | No | No | Validation only. |
| `scripts/verify_elite_output.py` | `diagnostics` | Elite CSV validator | No | No | No | No | No | Validation only. |
| `scripts/refresh_closed_slate_reports.py` | `reporting` | Reporting-only refresh | No | No | No | No | No | Explicit alternative to prediction reruns. |
| `scripts/refresh_historical_operator_cards.py` | `reporting` | Historical card refresh | No | No | No | No | No | Does not recreate model predictions. |
| `scripts/write_operator_card.py` | `reporting` | Operator-card renderer | No | No | No | No | No | Consumes existing boards. |
| `scripts/write_daily_summary.py` | `reporting` | Daily summary writer | No | No | No | No | No | Reporting-only. |
| `scripts/write_quality_summary.py` | `reporting` | Quality summary writer | No | No | No | No | No | Reporting-only. |
| `scripts/run_kelly_stakes.py` | `reporting` | Kelly calculation from an existing elite board | No | No | No prediction artifacts | No | No | Bankroll-facing but downstream; deliberately outside prediction generation. |

## Result

The only approved active calculation engines are:

1. NBA production: `_NBAPredictionEngine` over
   `CourtVisionAI._predict_internal()` and the package
   `PredictionPipeline`.
2. MLB research: `_MLBHRResearchPredictionEngine` over
   `_generate_daily_research_predictions_internal()`.

The only approved application orchestrator is
`courtvision.prediction.application.PredictionApplicationService`. All public
live/research callers either invoke it or delegate to a caller that does.
