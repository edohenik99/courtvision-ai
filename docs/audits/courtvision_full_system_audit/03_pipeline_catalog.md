# Pipeline Catalog

This catalog distinguishes active runtime pipelines from research, support, and planned flows. Operational status means "supported by code plus recent outputs/logs" when stated; it does not mean profitable or bankroll-ready.

## Pipeline Inventory

| Pipeline | Trigger | Main Steps | Final Output | Automated | Operational Status |
| --- | --- | --- | --- | --- | --- |
| NBA daily operator run | `run_today.bat DATE` or `run_today.ps1 -Date DATE` | closed-slate guard -> `courtvision_ai.py --predict-only` -> validation -> Kelly if elite rows -> tracking/grading/reporting | `outputs/runtime/operator/*`, logs, histories | Manual or schedulable | Operational with limitations |
| NBA provider ingestion | Inside `courtvision_ai.py` / package pipeline | fetch games, odds, injuries from BallDontLie -> normalize odds | candidate inputs and diagnostics | Part of NBA run | Operational when provider/date has data |
| NBA candidate/scoring/selection | `courtvision/pipeline/predict_pipeline.py` | build candidates -> score/confidence/quality -> source/live gates -> elite/full-market/near/watchlist | Operator boards | Part of NBA run | Operational with limitations |
| NBA Kelly staking | `scripts/run_kelly_stakes.py`, called by `run_today.ps1` | read elite board -> points-only lock -> Kelly fraction/caps -> stake CSV | `kelly_stakes_DATE.csv` | Conditional | Operational for NBA `player_points` elite only |
| NBA grading/history | `post_run_tracking.py`, `grade_completed_picks.py`, `nightly_grade_and_refresh.py` | record pending -> grade completed games -> refresh reports | `data/history/*`, logs | Partly automated | Partially operational |
| NBA shadow/research reporting | `market_shadow_grading.py`, `write_shadow_artifacts.py`, `write_research_artifacts.py` | grade/report non-live lanes | shadow/research artifacts | Part of NBA run | Supporting, not picks |
| Evidence daily export | `tools/run_courtvision_evidence_daily.ps1` | clean-tree/main checks -> optional run_today -> export manifest/ledger | `data/history/evidence_*` | Wrapper exists | Partially operational |
| Evidence result/CLV update | `tools/run_courtvision_evidence_grading.ps1` | update closing lines/results under guard flags | evidence ledger/results | Wrapper exists | Supporting |
| MLB HR live odds collection | `tools/run_live_hr_daily_auto.ps1` or collector CLI | same-day guard -> The Odds API events/odds -> flatten Over 0.5 HR markets -> snapshot/master/run_log | `data/theoddsapi/live_hr_snapshots/*` | Intended daily | Verified operational as collection |
| MLB HR daily data check | `tools/run_live_hr_daily_check.py` | read master/run logs -> row/duplicate/book/date checks | console/log validation | Called by wrappers | Operational/offline |
| MLB HR nightly finalization | `tools/run_courtvision_mlb_nightly_pipeline.ps1` | select completed dates -> workbook -> StatsAPI fill -> export -> coverage -> grade/summarize | result CSVs, grade CSVs, summaries, JSON/TXT run summary | Intended 03:30 | Verified recent operational |
| MLB HR legacy finalizer | `tools/run_live_hr_final_auto.ps1` | yesterday-only daily check/workbook/fill/export/coverage/grade | final logs/results | Could be scheduled | Superseded by consolidated pipeline |
| MLB HR research report | `courtvision/sports/mlb/hr_report.py` | odds/context provider -> HR report render | report output | Manual | Supporting/research |
| MLB historical collection | `scripts/mlb_ingest_*`, `scripts/courtvision_collect_sources.py` | ingest Statcast/Retrosheet/weather/ballpark | raw packs/manifests | Manual | Research support |
| MLB HR dataset/backtest/readiness | `scripts/mlb_*`, `courtvision/sports/mlb/training/*` | build datasets -> leakage/readiness -> temporal split/backtest -> frozen predictions | research datasets/reports | Manual | Implemented research |
| Dashboard/reporting | `courtvision_streamlit_app.py`, `streamlit_app.py`, `scripts/dashboard.py`, `scripts/serve_dashboard.py` | read generated artifacts/histories | local UI | Manual | Supporting |
| Cross-sport scaffolding | package modules under WNBA/NFL/NHL | provider/projection skeletons | no current live pick output | No | Planned/research |
| Alana integration | None found | N/A | N/A | No | Not implemented |

## NBA Daily Operator Pipeline

Business purpose: generate NBA operator-ready boards and, when strict gates permit, Kelly stake recommendations for elite `player_points` picks.

Trigger: `run_today.bat [DATE] [--verbose] [--force-past-date]`, which calls `run_today.ps1`.

Data-flow chain:

`BallDontLie -> CourtVisionAI provider fetch -> normalized games/odds/injuries -> candidate generation -> scoring/confidence/quality -> source/live/market/context gates -> elite/full_market/near/watchlist boards -> Kelly if elite rows -> histories/grading/reports`

Step flow:

| Step | Component | Input | Processing | Output |
| --- | --- | --- | --- | --- |
| 1 | `run_today.bat` | date args | argument parsing and PowerShell dispatch | `run_today.ps1` invocation |
| 2 | `run_today.ps1` | date/env | closed-slate overwrite guard, interpreter selection, log setup | runtime logs |
| 3 | `courtvision_ai.py` | prediction date, `.env` | BallDontLie smoke/fetch, package pipeline delegation | operator/research artifacts |
| 4 | `courtvision/pipeline/predict_pipeline.py` | games/odds/injuries/baselines | candidate generation, context, scoring, selection | `elite_df`, `full_market_df`, diagnostics |
| 5 | `scripts/validate_runtime_outputs.py` and audits | generated boards | schema/content/data-quality validation | validation logs |
| 6 | `scripts/run_kelly_stakes.py` | elite board and bankroll | points-only lock, quarter-Kelly, exposure caps | `kelly_stakes_DATE.csv` |
| 7 | tracking/grading/report writers | boards/results/history | update histories, shadow, summaries, operator card, manifest | daily reports |

Operational status: Operational with limitations. Evidence includes no-slate successful completion on 2026-07-08 and historical elite/Kelly outputs for 2026-05-10 and 2026-05-13. Trustworthy live-pick performance is not proven.

## MLB HR Live Odds Collection Pipeline

Business purpose: collect pregame MLB 1+ HR market observations for research/evaluation.

Trigger: `tools/run_live_hr_daily_auto.ps1` or `python tools/theoddsapi_live_hr_collector.py`.

Data-flow chain:

`The Odds API events -> event filter -> event odds endpoint -> Over 0.5 batter HR rows -> timestamped snapshot -> master append/dedupe -> daily check`

Decision logic:

| Rule | Code evidence |
| --- | --- |
| Market | `MARKETS = "batter_home_runs_alternate"` |
| Bookmakers requested | `draftkings,fanduel,betmgm,bet365,fanatics,espnbet,betrivers` |
| Max events | `MAX_EVENTS_PER_RUN = 12` |
| Pregame filter | `MIN_MINUTES_BEFORE_GAME = 30` |
| Same-day guard | `already_ran_today()` reads successful `run_log.csv` rows |
| Dedupe key | `snapshot_date + event_id + bookmaker_key + market + player + side + point` |

Operational status: Verified operational as data collection. Recent run log shows 2026-07-10 success with 12 events scanned, 861 rows saved, and remaining credits recorded.

Final destination: `live_hr_props_master.csv` and data-quality logs. It does not create official picks.

## MLB HR Nightly Results And Grading Pipeline

Business purpose: finalize completed MLB HR market observations and produce grade/performance summaries for research.

Trigger: `tools/run_courtvision_mlb_nightly_pipeline.ps1`, intended 03:30 local according to `docs/mlb_live_hr_daily_ops.md`.

Data-flow chain:

`HR master -> results workbook -> MLB StatsAPI schedule/boxscore -> filled actual_home_runs/game_status/result_reason -> strict results CSV -> coverage checker -> grader -> markdown summary`

Step flow:

| Step | Component | Input | Output |
| --- | --- | --- | --- |
| 1 | `courtvision_mlb_nightly_pipeline.py` | lookback/date args | selected completed dates |
| 2 | `run_live_hr_daily_check.py` | master/run log | preflight validation |
| 3 | `generate_live_hr_results_workbook.py` | master | `live_hr_results_workbook.csv` |
| 4 | `fill_live_hr_results_from_mlb_statsapi.py` | workbook/date | filled/preserved result statuses |
| 5 | `export_live_hr_results_from_workbook.py` | workbook | strict `live_hr_results.csv` |
| 6 | `check_live_hr_results_coverage.py` | strict results + master | ready/not-ready coverage |
| 7 | `grade_live_hr_results.py` | master + strict results | `live_hr_grades_YYYYMMDD.csv` |
| 8 | `summarize_live_hr_grades.py` | grade CSV | markdown performance summary |

Operational status: Verified recent operational. `mlb_nightly_summary_20260711_033003.txt` reports success and processed/graded 2026-07-08, 2026-07-09, and 2026-07-10.

Important limitation: these are market-observation grades, not official CourtVision pick grades.

## MLB Historical Research Pipeline

Business purpose: build and validate historical datasets for future MLB HR modelling.

Trigger: manual `scripts/mlb_*` commands.

Data-flow chain:

`Statcast/Retrosheet/Chadwick/weather/ballpark -> staging/raw manifests -> dataset builder -> leakage/readiness audits -> temporal split/backtest/frozen predictions -> validation promotion`

Operational status: Implemented research pipeline. Evidence includes many `docs/COURTVISION_MLB_HR_*` contracts and scripts such as `mlb_build_hr_local_dataset.py`, `mlb_write_hr_temporal_split.py`, and `mlb_audit_hr_validation_promotion.py`.

Final destination: research artifacts and readiness reports, not live picks.

## Evidence Pipeline

Business purpose: support a forward evidence trial and result/closing-line updates.

Trigger: `tools/run_courtvision_evidence_daily.ps1`, `tools/run_courtvision_evidence_grading.ps1`, and evidence scripts.

Operational status: Partially operational. Guarded wrappers exist, but `data/history/evidence_ledger.csv` is sparse and this audit did not run evidence commands.

## Dashboard/Reporting Pipeline

Business purpose: view generated outputs and histories.

Trigger: Streamlit scripts/manual commands.

Operational status: Supporting. The audit did not run dashboards because that can create caches or depend on local state.

