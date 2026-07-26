# Script Reference

This reference covers the 130 operational scripts/wrappers found in root, `scripts/`, `tools/`, `courtvision/cli/`, and `courtvision/sports/mlb/hr_report.py`. Status is evidence-based when tied to current call chains/logs; otherwise it is categorized by source code role and documentation.

## Status Legend

| Status | Meaning |
| --- | --- |
| Active | Connected to the canonical NBA path, current MLB HR path, or current evidence wrapper. |
| Supporting | Used for reporting, validation, dashboards, or helper operations. |
| Research | MLB/NBA research, validation, backtest, or exploratory tooling. |
| Legacy | Compatibility or older path superseded by newer scripts. |
| Unknown | Present but not enough evidence to classify as active. |

## High-Impact Script Inventory

| Script | Sport/System | Purpose | Input | Output | Trigger | Downstream Consumer | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `run_today.bat` | NBA runtime | Batch wrapper for canonical daily run. | date args | PowerShell invocation | operator/manual | `run_today.ps1` | Active |
| `run_today.ps1` | NBA runtime | Orchestrates predict, validate, Kelly, tracking, grading, reports. | date/env/runtime files | logs and `outputs/runtime/*` | operator/manual | operator/evidence/history | Active |
| `courtvision_ai.py` | NBA runtime | Canonical `CourtVisionAI` monolith; provider fetch, prediction, output, Telegram. | BallDontLie/env/date/baselines | boards, diagnostics, summaries | `run_today.ps1` or CLI | reports/history/Kelly | Active |
| `scripts/validate_runtime_outputs.py` | NBA validation | Validates runtime output schemas and presence. | generated boards | validation log/status | `run_today.ps1` | operator gate | Active |
| `scripts/audit_full_market_sanity.py` | NBA validation | Audits full-market board sanity. | full-market board | validation report | `run_today.ps1` | operator gate | Active |
| `scripts/audit_candidate_quality_drift.py` | NBA validation | Checks candidate-quality drift. | candidate/runtime artifacts | audit report | `run_today.ps1` | operator diagnostics | Active |
| `scripts/run_kelly_stakes.py` | NBA bankroll | Converts eligible elite board rows into capped quarter-Kelly stakes. | elite board, bankroll env | Kelly CSV | `run_today.ps1` conditional | operator card/history | Active |
| `scripts/post_run_tracking.py` | NBA history | Tracks generated picks and pending grades. | boards/results | `data/history/pick_history.csv` | `run_today.ps1` | grading/reports | Active |
| `scripts/grade_completed_picks.py` | NBA grading | Grades completed picks where result data exists. | pick history/game results | graded history/logs | `run_today.ps1`, nightly grader | reports/evidence | Active |
| `scripts/market_shadow_grading.py` | NBA shadow | Grades non-live market-shadow lanes. | shadow artifacts/results | shadow history | `run_today.ps1` | shadow reports | Active |
| `scripts/write_operator_card.py` | NBA reporting | Writes operator decision card. | boards, quality, Kelly | `operator_card_DATE.txt` | `run_today.ps1` | operator | Active |
| `scripts/write_daily_summary.py` | NBA reporting | Writes daily summary. | boards/audits | summary text | `run_today.ps1` | operator/evidence | Active |
| `scripts/write_quality_summary.py` | NBA reporting | Writes quality JSON. | diagnostics | quality summary | `run_today.ps1` | operator/evidence | Active |
| `scripts/write_artifact_manifest.py` | NBA reporting | Writes required artifact manifest. | generated files | manifest JSON/TXT | `run_today.ps1` | operator gate | Active |
| `scripts/nightly_grade_and_refresh.py` | NBA automation | Nightly grading/report refresh entrypoint. | histories/runtime outputs | refreshed reports | scheduled-task installer | operator/history | Supporting |
| `scripts/install_nightly_grader.ps1` | NBA automation | Installs Windows Scheduled Task for nightly grader. | repo path | scheduled task | manual admin | Windows scheduler | Supporting |
| `tools/theoddsapi_live_hr_collector.py` | MLB HR | Collects The Odds API HR market observations. | `THE_ODDS_API_KEY`, events/odds endpoints | snapshot CSV, master CSV, run log | daily wrapper/manual | daily check/workbook/grader | Active |
| `tools/run_live_hr_daily_auto.ps1` | MLB HR automation | Daily guarded collector wrapper. | local repo/env | collector/check logs | scheduled/manual | HR master | Active |
| `tools/run_live_hr_daily_check.py` | MLB HR validation | Offline data-quality check for HR master/run log. | master/run log | console/log validation | wrappers/manual | operations | Active |
| `tools/validate_live_hr_data.py` | MLB HR validation | Validates live HR snapshot/master quality. | HR CSVs | validation output | manual/checks | operations | Supporting |
| `tools/generate_live_hr_results_workbook.py` | MLB HR results | Builds/preserves human-editable result workbook. | HR master, existing workbook | workbook CSV | nightly pipeline/manual | filler/exporter | Active |
| `tools/fill_live_hr_results_from_mlb_statsapi.py` | MLB HR results | Fills HR outcomes from MLB schedule/boxscore. | workbook/date, MLB StatsAPI | filled workbook + diagnostics | nightly pipeline/manual | exporter/coverage | Active |
| `tools/export_live_hr_results_from_workbook.py` | MLB HR results | Exports strict result CSV from workbook. | workbook | `live_hr_results.csv` | nightly pipeline/manual | coverage/grader | Active |
| `tools/check_live_hr_results_coverage.py` | MLB HR grading gate | Determines ready-to-grade coverage by date. | master/results | coverage report/status | nightly pipeline/manual | grader | Active |
| `tools/grade_live_hr_results.py` | MLB HR grading | Grades HR market observations against results. | master/results/date | grade CSV | nightly pipeline | summaries | Active |
| `tools/summarize_live_hr_grades.py` | MLB HR reporting | Summarizes HR grade CSVs. | grade CSV | markdown summary | nightly pipeline | reports | Active |
| `tools/courtvision_mlb_nightly_pipeline.py` | MLB HR automation | Consolidated nightly finalizer/orchestrator. | HR master/workbook/results | summary JSON/TXT, grades | PS wrapper/manual | reports | Active |
| `tools/run_courtvision_mlb_nightly_pipeline.ps1` | MLB HR automation | PowerShell wrapper for consolidated nightly pipeline. | date/lookback args | automation log | scheduled/manual | Python orchestrator | Active |
| `tools/run_live_hr_final_auto.ps1` | MLB HR automation | Older yesterday-only finalizer. | HR data/date | final logs/grades | scheduled/manual | result/grader scripts | Legacy |
| `tools/diagnose_live_hr_missing_results.py` | MLB HR results | Diagnoses missing/unresolved HR results. | HR workbook/results/master | diagnostic report | manual | operations | Supporting |
| `tools/generate_live_hr_daily_report.py` | MLB HR reporting | Generates daily HR report. | HR data | report | manual | operator/research | Supporting |
| `tools/generate_live_hr_results_template.py` | MLB HR results | Earlier template generator for HR results. | HR master | template CSV | manual | workbook workflow | Legacy/supporting |
| `tools/run_courtvision_evidence_daily.ps1` | Evidence | Guarded evidence daily wrapper. | date/trial/config args | evidence artifacts | manual/scheduled | evidence ledger | Active/supporting |
| `tools/run_courtvision_evidence_grading.ps1` | Evidence | Guarded closing-line/result evidence update wrapper. | date/trial flags | evidence updates | manual | evidence ledger | Supporting |
| `courtvision/sports/mlb/hr_report.py` | MLB HR research | CLI report path for HR odds/context. | odds/context providers | report output | manual | research | Supporting |
| `courtvision_streamlit_app.py` | UI | Streamlit app. | generated artifacts/histories | local dashboard | manual | operator/user | Supporting |
| `streamlit_app.py` | UI | Alternate Streamlit entrypoint. | generated artifacts | local dashboard | manual | operator/user | Supporting |
| `scripts/dashboard.py` | UI | Dashboard script. | histories/outputs | dashboard output | manual | operator/user | Supporting |
| `scripts/serve_dashboard.py` | UI | Serves dashboard. | dashboard files | local server | manual | user | Supporting |

## Active MLB HR Scripts

### `tools/theoddsapi_live_hr_collector.py`

Actual behavior: calls The Odds API events endpoint, filters games that are at least 30 minutes before start, scans at most 12 events per run, requests `batter_home_runs_alternate`, flattens Over 0.5 outcomes into player/book/price rows, writes a timestamped snapshot, appends to `live_hr_props_master.csv`, dedupes the master, and appends `run_log.csv`.

Inputs: `THE_ODDS_API_KEY`, optional base URL env, The Odds API sports/event/odds endpoints.  
Outputs: `data/theoddsapi/live_hr_snapshots/live_hr_props_*.csv`, `live_hr_props_master.csv`, `run_log.csv`.  
Rerun safety: same-day success guard unless `--force`; `--force` can consume credits and can create additional immutable snapshots.  
Risks: observations are not official picks; API credits; no process-level lock; master dedupe collapses latest quote per key but snapshots remain.

### `tools/courtvision_mlb_nightly_pipeline.py`

Actual behavior: selects completed MLB dates by explicit date or lookback, optionally pulls git unless skipped, runs preflight daily check, regenerates workbook with preserved results, fills final outcomes from MLB StatsAPI, exports strict results, runs coverage, grades only ready dates, writes summaries, and runs postflight check.

Inputs: HR master/workbook/results, MLB StatsAPI, lookback/date args.  
Outputs: workbook/results/grade CSVs/markdown summaries/JSON and TXT automation summaries.  
Rerun safety: generally designed to preserve result fields; output files are overwritten/regenerated by date.  
Risks: mutable git pull by default, success can include skipped incomplete dates, manual unresolved states remain.

## Active NBA Runtime Scripts

### `run_today.ps1`

Actual behavior: blocks past-date generation unless forced, sets log paths and bankroll/mode defaults, chooses local Python, runs `courtvision_ai.py --prediction-date DATE --predict-only --verbose-outputs`, validates artifacts, conditionally runs Kelly when elite rows exist, records/grads/generated reports, writes operator card and artifact manifest.

Inputs: date arg, `.env`, baseline/model artifacts, provider data.  
Outputs: `outputs/runtime/logs/*`, `outputs/runtime/operator/*`, research/learning artifacts, histories.  
Rerun safety: protected by closed-slate guard for past dates; can overwrite date-scoped outputs when forced.  
Risks: high-complexity orchestration, local Windows assumptions, bankroll-facing outputs require evidence gates.

### `courtvision_ai.py`

Actual behavior: canonical `CourtVisionAI` runtime. Loads env, initializes BallDontLie client, runs fit/predict/grade/Telegram command modes, delegates active prediction to package pipeline, writes operator and research artifacts, and exposes CLI flags.

Inputs: `.env`, BallDontLie API, historical baselines/calibration, prediction date.  
Outputs: runtime CSV/JSON/TXT artifacts and optional Telegram send.  
Rerun safety: date-scoped outputs; risk if forced on closed slate.  
Risks: large monolith, multiple legacy paths, optional Telegram secrets, provider/API dependency.

## Research And Historical Script Families

| Family | Examples | Purpose | Status |
| --- | --- | --- | --- |
| MLB historical ingest | `mlb_ingest_statcast.py`, `mlb_ingest_retrosheet.py`, `mlb_ingest_weather.py`, `mlb_ingest_ballpark_factors.py` | Build raw historical context packs. | Research |
| MLB dataset/readiness | `mlb_build_hr_local_dataset.py`, `mlb_validate_hr_window_readiness.py`, `mlb_preflight_hr_historical_pack.py` | Validate HR training/evaluation windows. | Research |
| MLB dry-run/backtest | `mlb_dry_run_hr_*`, `mlb_write_hr_temporal_split.py` | Frozen predictions, temporal splits, backtests, handoff. | Research |
| NBA research/reports | `run_research_mode.py`, `build_daily_research_report.py`, `run_projection_quality_review.py` | Analysis and research artifacts. | Supporting/research |
| Evidence scripts | `append_evidence_*`, `update_evidence_*`, `export_run_to_evidence.py` | Forward-trial manifests and settlement updates. | Supporting |
| History repair/backfill | `backfill_*`, `repair_pending_grades.py`, `dedupe_result_feedback.py` | Maintenance of histories/results. | Supporting, rerun carefully |

## Complete Alphabetical Appendix

The following files were included in the operational-script inventory:

```text
courtvision/cli/__init__.py
courtvision/cli/__main__.py
courtvision/cli/main.py
courtvision/sports/mlb/hr_report.py
courtvision_ai.py
courtvision_streamlit_app.py
diagnose_candidates.py
run_tests.bat
run_today.bat
run_today.ps1
scripts/analyze_power_rating_shadow.py
scripts/append_evidence_daily_manifest.py
scripts/append_evidence_ledger_row.py
scripts/audit_candidate_quality_drift.py
scripts/audit_full_market_sanity.py
scripts/audit_projection_source.py
scripts/backfill_grading.py
scripts/backfill_market_shadow_history.py
scripts/backfill_power_rating_context.py
scripts/build_daily_research_report.py
scripts/build_stat_projection_source.py
scripts/courtvision_collect_sources.py
scripts/courtvision_collector_doctor.py
scripts/create_evidence_day0_manifest.py
scripts/dashboard.py
scripts/dedupe_result_feedback.py
scripts/export_run_to_evidence.py
scripts/grade_completed_picks.py
scripts/grade_market_shadow_history.py
scripts/history_tracking.py
scripts/init_evidence_daily_manifest.py
scripts/init_evidence_ledger.py
scripts/install_nightly_grader.ps1
scripts/market_shadow_grading.py
scripts/mlb_audit_hr_backtest_readiness.py
scripts/mlb_audit_hr_test_access.py
scripts/mlb_audit_hr_validation_promotion.py
scripts/mlb_build_hr_feature_pack.py
scripts/mlb_build_hr_local_dataset.py
scripts/mlb_dry_run_hr_crosswalk.py
scripts/mlb_dry_run_hr_evaluator.py
scripts/mlb_dry_run_hr_frozen_predictions.py
scripts/mlb_dry_run_hr_model_handoff.py
scripts/mlb_dry_run_hr_preprocessing.py
scripts/mlb_dry_run_hr_research_backtest.py
scripts/mlb_ingest_ballpark_factors.py
scripts/mlb_ingest_retrosheet.py
scripts/mlb_ingest_statcast.py
scripts/mlb_ingest_weather.py
scripts/mlb_inspect_fixture_stats.py
scripts/mlb_plan_hr_one_shot_test_evaluation.py
scripts/mlb_preflight_hr_historical_pack.py
scripts/mlb_stage_hr_historical_pack.py
scripts/mlb_validate_hr_window_readiness.py
scripts/mlb_validate_stadium_map.py
scripts/mlb_verify_raw_collection.py
scripts/mlb_write_hr_fitted_preprocessing.py
scripts/mlb_write_hr_temporal_split.py
scripts/nightly_grade_and_refresh.py
scripts/out_of_sample_validation.py
scripts/post_run_tracking.py
scripts/pre_game_finalization_guard.py
scripts/prefill_actual_feedback.py
scripts/record_manual_review_decision.py
scripts/refresh_closed_slate_reports.py
scripts/refresh_historical_operator_cards.py
scripts/repair_pending_grades.py
scripts/run_daily.py
scripts/run_edge_validation.py
scripts/run_grading.py
scripts/run_kelly_stakes.py
scripts/run_manual_review_board.py
scripts/run_market_projection_join.py
scripts/run_market_validation.py
scripts/run_odds_improvement_tracker.py
scripts/run_performance_report.py
scripts/run_projection_quality_review.py
scripts/run_quality_gated_research_board.py
scripts/run_research_mode.py
scripts/serve_dashboard.py
scripts/smoke_api_nba.py
scripts/smoke_the_odds_api_nba.py
scripts/uninstall_nightly_grader.ps1
scripts/update_evidence_closing_lines.py
scripts/update_evidence_results.py
scripts/update_game_results_history.py
scripts/validate_historical_cockpit.py
scripts/validate_runtime_outputs.py
scripts/verify_elite_output.py
scripts/write_artifact_manifest.py
scripts/write_bet_readiness_report.py
scripts/write_completion_state_audit.py
scripts/write_daily_summary.py
scripts/write_learning_artifacts.py
scripts/write_learning_brain_report.py
scripts/write_learning_integration_snapshot.py
scripts/write_no_bet_funnel_report.py
scripts/write_operator_card.py
scripts/write_quality_summary.py
scripts/write_rejection_outcome_audit.py
scripts/write_research_artifacts.py
scripts/write_safe_action_discovery_report.py
scripts/write_shadow_artifacts.py
scripts/write_shadow_candidate_lane_performance.py
scripts/write_shadow_candidate_lane_report.py
scripts/write_shadow_rule_proposals.py
scripts/write_threshold_pressure_audit.py
scripts/write_under_visibility_audit.py
streamlit_app.py
test_player_prop_filtering.py
tools/check_live_hr_results_coverage.py
tools/courtvision_mlb_nightly_pipeline.py
tools/diagnose_live_hr_missing_results.py
tools/export_live_hr_results_from_workbook.py
tools/fill_live_hr_results_from_mlb_statsapi.py
tools/generate_live_hr_daily_report.py
tools/generate_live_hr_results_template.py
tools/generate_live_hr_results_workbook.py
tools/grade_live_hr_results.py
tools/run_courtvision_evidence_daily.ps1
tools/run_courtvision_evidence_grading.ps1
tools/run_courtvision_mlb_nightly_pipeline.ps1
tools/run_live_hr_daily_auto.ps1
tools/run_live_hr_daily_check.py
tools/run_live_hr_final_auto.ps1
tools/summarize_live_hr_grades.py
tools/theoddsapi_live_hr_collector.py
tools/validate_live_hr_data.py
validate_caps.py
validate_caps_full.py
```

## Rerun Safety Summary

| Category | Safe to repeat? | Notes |
| --- | --- | --- |
| Pure readers/checkers | Usually | Coverage/check scripts are safest when no output path is overwritten and no live API is called. |
| Collectors | No, not casually | MLB HR collector consumes API credits; `--force` bypasses same-day guard. |
| Result/workbook exporters | Carefully | Regenerate/overwrite outputs; some preserve existing results, some require overwrite flags. |
| NBA `run_today` | Carefully | Date-scoped output writes; closed-slate guard protects past dates unless forced. |
| Backfill/repair scripts | Carefully | They intentionally mutate histories/results. |
| Scheduled-task installers | No for audit | They alter Windows scheduler state. |
| Smoke/API scripts | No for audit | They can call live APIs and consume quota. |

