# CourtVision Full System Audit - Executive Summary

Audit date: 2026-07-11  
Repository: `C:\dev\Sport_Project1`  
Audited commit: `811a07869a14bc865eec74977e6ab741ad0a0f14`  
Scope: read-only technical audit, except for new documentation in this directory.

## Bottom Line

CourtVision is not one finished product today. It is a partially orchestrated sports prediction and research system with one canonical NBA operator workflow, a separate MLB home-run odds collection and grading workflow, several MLB research/backtest pipelines, and additional cross-sport scaffolding. The strongest current implementation is operational plumbing: data collection, board generation, artifact writing, validation reports, result settlement helpers, and audit logs. The weakest current implementation is proof that generated picks are trustworthy, prospectively profitable, fully automated, and safely tied to immutable official pick identities.

Confidence: Confirmed from `docs/ADR_001_CANONICAL_RUNTIME.md`, `run_today.ps1`, `courtvision_ai.py`, `tools/theoddsapi_live_hr_collector.py`, `tools/courtvision_mlb_nightly_pipeline.py`, and recent logs under `outputs/runtime/logs/` and `data/theoddsapi/live_hr_snapshots/automation_logs/`.

## What CourtVision Is Today

| Area | Current state | Evidence | Confidence |
| --- | --- | --- | --- |
| NBA | Production-facing operator workflow, centered on `run_today.bat -> run_today.ps1 -> courtvision_ai.py`, with package-owned prediction/selection modules. | `docs/ADR_001_CANONICAL_RUNTIME.md`, `run_today.ps1`, `courtvision_ai.py`, `courtvision/pipeline/predict_pipeline.py` | Confirmed |
| MLB HR live odds | The Odds API collector for MLB batter home-run alternate markets, currently building a research dataset and market-observation history. | `tools/theoddsapi_live_hr_collector.py`, `docs/mlb_live_hr_collector.md`, `data/theoddsapi/live_hr_snapshots/run_log.csv` | Confirmed |
| MLB HR results/grading | Hybrid finalization workflow: MLB StatsAPI fills final home-run outcomes when possible; manual workbook/status handling remains for unresolved cases. | `tools/fill_live_hr_results_from_mlb_statsapi.py`, `tools/check_live_hr_results_coverage.py`, `tools/grade_live_hr_results.py` | Confirmed |
| MLB modelling | Historical data, contracts, dataset builders, readiness audits, and research scoring exist. No evidence that live MLB HR official picks are produced today. | `courtvision/sports/mlb/`, `scripts/mlb_*`, MLB HR docs | High confidence |
| Other sports | WNBA, NFL, and NHL packages/scaffolding exist, but are research/planned rather than production-facing. | `docs/ADR_001_CANONICAL_RUNTIME.md`, `courtvision/sports/` | High confidence |
| Alana | No repository evidence of an Alana integration. Telegram delivery hooks exist. | `rg -i "alana"` returned no matches; Telegram matches in `courtvision_ai.py` and `courtvision/application.py` | Confirmed |

## Current Maturity

| Capability | Score 0-5 | Evidence | Next requirement |
| --- | ---: | --- | --- |
| Repository organization | 3 | Clear package/script/docs/test structure, but many overlapping scripts and old docs. | Consolidated entrypoint registry and retired legacy paths. |
| Data ingestion | 4 | NBA BallDontLie and MLB HR The Odds API/StatsAPI clients implemented; recent logs exist. | Provider health, quota, and fallback monitoring. |
| Prediction capability | 3 | NBA prediction artifacts and MLB research models exist. | Prospective validation and model registry. |
| Official pick generation | 3 | NBA elite board and Kelly flow exist; latest observed NBA run produced no picks. | Immutable official pick identity and forward trial. |
| MLB HR official picks | 1 | Live odds and grading exist, but no official pick selector. | Build model-to-official-pick layer. |
| Result collection | 3 | MLB HR StatsAPI filler and NBA grading scripts exist. | Fully reconciled result status lifecycle. |
| Grading | 3 | NBA and MLB grading scripts exist. | End-to-end evidence ledger coverage and idempotent settlement. |
| Automation | 3 | Daily/3:30 MLB wrappers and NBA/evidence wrappers exist. | Scheduler verification, locking, alerting, reproducible environment. |
| Monitoring | 2 | Logs and operator cards exist. | Central run status, alerting, retention, dashboards. |
| Reproducibility | 2 | Manifests and logs exist, but ignored local artifacts and mutable `main` pulls matter. | Pin code/data/model versions for every run. |
| Testing | 3 | 293 test files and targeted MLB/NBA tests exist. This audit did not run tests. | CI signal tied to canonical pipelines and schedules. |
| Deployment readiness | 2 | Windows scripts work locally but assume paths/interpreters. | Environment abstraction and deployment runbook. |

## What Works

| Working area | Evidence |
| --- | --- |
| NBA canonical daily run can complete safely on no-slate days and produce no-bet artifacts. | `outputs/runtime/logs/run_today_2026-07-08.log`, `operator_card_2026-07-08.txt` |
| NBA elite/full-market/Kelly outputs exist for historical successful runs. | `outputs/runtime/operator/elite_board_2026-05-10.csv`, `kelly_stakes_2026-05-10.csv` |
| MLB HR collector has recent successful run-log rows and writes snapshots/master data. | `data/theoddsapi/live_hr_snapshots/run_log.csv` |
| MLB HR nightly finalizer recently completed for 2026-07-08 through 2026-07-10. | `mlb_nightly_summary_20260711_033003.txt` |
| MLB HR result coverage checks gate grading by date. | `tools/check_live_hr_results_coverage.py`, nightly summary coverage blocks |
| Research and evidence workflows have explicit contracts and runbooks. | `docs/EVIDENCE_*`, `docs/COURTVISION_MLB_HR_*` |

## What Does Not Yet Work As A Trustworthy Live-Pick Platform

| Gap | Why it matters | Evidence |
| --- | --- | --- |
| MLB HR odds rows are not official picks. | Treating every bookmaker/player observation as a bet can create false performance claims. | `tools/theoddsapi_live_hr_collector.py`, `tools/grade_live_hr_results.py` |
| No universal immutable official pick ID. | Result matching, repeats, and bankroll attribution are fragile without one. | NBA history uses composite fields; MLB grades key by event/player plus odds row fields. |
| Prospective performance evidence is sparse/incomplete. | Bankroll-facing confidence needs forward evidence, not just code paths. | `data/history/evidence_ledger.csv`, `pick_history.csv` stale relative to later outputs. |
| Automation depends on local Windows scripts and mutable branch pulls. | Repeatability and failure diagnosis are weaker. | `tools/run_live_hr_daily_auto.ps1`, `tools/run_courtvision_mlb_nightly_pipeline.ps1` |
| Configuration docs and active config diverge. | Operators may configure inactive providers or miss active env vars. | `docs/ENV_CONFIG_AUDIT.md`, `.env.example`, `courtvision_ai.py` |

## Most Important Pipelines

| Pipeline | Trigger | Final output | Operational status |
| --- | --- | --- | --- |
| NBA daily prediction/operator run | `run_today.bat` or `run_today.ps1` | Operator boards, summaries, optional Kelly, histories | Operational with limitations |
| NBA Kelly staking | Called by `run_today.ps1` when elite board has rows | `outputs/runtime/operator/kelly_stakes_DATE.csv` | Operational for NBA elite `player_points` only |
| NBA grading/reporting | `run_today.ps1`, `scripts/nightly_grade_and_refresh.py`, grading scripts | Pick history, grading logs, operator reports | Partially operational |
| MLB HR live odds collection | `tools/run_live_hr_daily_auto.ps1` or collector CLI | Snapshot CSV, master CSV, run log | Verified operational as data collection |
| MLB HR nightly results/finalization | `tools/run_courtvision_mlb_nightly_pipeline.ps1` | Workbook/results/coverage/grade summaries | Verified operational for recent dates |
| MLB HR research/backtest | `scripts/mlb_*`, `courtvision/sports/mlb/training/*` | Historical datasets, readiness, backtests | Implemented research pipeline |
| Evidence export/trial | `tools/run_courtvision_evidence_daily.ps1`, evidence scripts | Evidence manifests/ledger | Partially operational |

## Direct Answers To The Highest-Risk Questions

| Question | Answer |
| --- | --- |
| Does CourtVision currently generate trustworthy live picks? | Not proven. NBA can generate controlled elite/Kelly outputs, but recent forward evidence is insufficient. MLB HR does not currently issue official picks. |
| Does CourtVision automatically grade results? | Partly. NBA and MLB grading scripts exist. MLB HR finalization is hybrid because unresolved player/result statuses can require manual review. |
| Is there a closed feedback loop? | Partial. Outputs, histories, grading, and evidence artifacts exist, but the loop is not yet complete enough to support bankroll deployment claims. |
| Does the 3:30 a.m. job collect new HR odds? | No evidence that it does. The consolidated 3:30 workflow finalizes results and grading; the live odds collector is a separate daily collection job. |
| Can every collected HR prop be graded today? | No. Rows need completed `actual_home_runs`, `game_status`, valid statuses, and successful event/player matching. |

## Biggest Risks

| ID | Severity | Area | Finding | Recommendation |
| --- | --- | --- | --- | --- |
| F-001 | Critical | Picks/performance | Market observations can be mistaken for official picks. | Introduce immutable official pick table and never grade observations as picks. |
| F-002 | Critical | Evidence | Trustworthy prospective performance is not established. | Run a controlled paper trial with frozen code/data/model artifacts. |
| F-003 | Critical | Bankroll | Kelly path exists before enough evidence is proven. | Keep bankroll disabled until forward evidence gates pass. |
| F-004 | High | MLB HR | Hybrid result settlement leaves unresolved/void-candidate rows. | Add reconciliation queue and explicit settlement states. |
| F-005 | High | Automation | Scheduled jobs pull mutable `main` and use local assumptions. | Pin commit/environment per run and add locks/alerts. |

## Immediate Next Steps

1. Create a single official pick schema with immutable `pick_id`, sport, market, odds source, model version, selection timestamp, and settlement state.
2. Separate market-observation grading from pick-performance grading in reports and filenames.
3. Finish the MLB HR result reconciliation workflow and make unresolved states first-class.
4. Run a no-bankroll forward paper trial for NBA and MLB HR candidate logic.
5. Harden automation with scheduler verification, locks, failure alerts, and pinned run manifests.

