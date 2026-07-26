# Automation And Operations

## Automation Inventory

| Task | Schedule | Entry Point | Collects Data | Finalizes Results | Generates Picks | Grades Picks | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NBA daily operator run | Manual/schedulable; no active scheduler verified by this audit | `run_today.bat` / `run_today.ps1` | Yes, NBA provider data | Can grade completed prior picks | Yes, NBA boards | Yes/partial | `run_today.ps1`, 2026-07-08 logs |
| CourtVision Nightly Grader | Installer defines daily 02:00 | `scripts/install_nightly_grader.ps1` -> `scripts/nightly_grade_and_refresh.py` | No new odds | Yes for NBA histories/reports | No | Yes | installer script |
| MLB Live HR Daily | Docs say daily 11:30 | `tools/run_live_hr_daily_auto.ps1` | Yes, The Odds API HR odds | No | No | No | docs, wrapper, run log |
| MLB HR Finalizer / consolidated nightly | Docs say 03:30 | `tools/run_courtvision_mlb_nightly_pipeline.ps1` | No new odds | Yes, MLB StatsAPI results | No | Yes, observations | docs, 2026-07-11 summary |
| Legacy MLB HR finalizer | Prior/alternate 03:30 style | `tools/run_live_hr_final_auto.ps1` | No | Yes | No | Yes | script |
| Evidence daily | Manual/schedulable | `tools/run_courtvision_evidence_daily.ps1` | Optional via `run_today` | No/exports evidence | Conditional | No | script/docs |
| Evidence grading | Manual/schedulable | `tools/run_courtvision_evidence_grading.ps1` | No | Updates closing lines/results | No | Evidence updates | script/docs |

Note: this audit did not query or mutate Windows Scheduled Tasks. It relies on installer scripts, docs, and existing logs.

## Daily MLB HR Collection

Command chain:

```text
tools/run_live_hr_daily_auto.ps1
  -> git checkout main / git pull
  -> local duplicate guard using run_log/master
  -> python tools/theoddsapi_live_hr_collector.py --quiet
  -> python tools/run_live_hr_daily_check.py
```

Operational characteristics:

| Item | Behavior |
| --- | --- |
| Working directory | Hard-coded `C:\dev\Sport_Project1` in wrapper. |
| Data collection | Yes, The Odds API HR odds. |
| Credit control | Same-day guard, max events 12, logs credits. |
| Logs | `data/theoddsapi/live_hr_snapshots/automation_logs/`. |
| Risk | Mutable git pull, hard-coded path, no explicit concurrency lock, paid API credits. |

## 03:30 MLB HR Finalization

Consolidated command chain:

```text
tools/run_courtvision_mlb_nightly_pipeline.ps1
  -> python tools/courtvision_mlb_nightly_pipeline.py --run-id ... --lookback-days ...
       -> run_live_hr_daily_check.py
       -> generate_live_hr_results_workbook.py --preserve-results
       -> fill_live_hr_results_from_mlb_statsapi.py --date DATE
       -> export_live_hr_results_from_workbook.py --overwrite
       -> check_live_hr_results_coverage.py --date DATE
       -> grade_live_hr_results.py --date DATE
       -> summarize_live_hr_grades.py --date DATE
```

What exit code zero proves:

| It proves | It does not prove |
| --- | --- |
| The orchestrator completed its scripted steps for selected dates. | Every historical date is graded. |
| Ready dates were graded. | Incomplete dates have no unresolved rows. |
| Logs/summaries were written. | The outputs are official pick performance. |
| The pipeline did not hit an unhandled fatal error. | API quotas, identity matches, and future schedules are safe indefinitely. |

Recent evidence: `mlb_nightly_summary_20260711_033003.txt` reports success, no warnings/errors, and graded 2026-07-08 through 2026-07-10.

## NBA `run_today` Operations

Command chain:

```text
run_today.bat DATE
  -> run_today.ps1 -Date DATE
       -> courtvision_ai.py --prediction-date DATE --predict-only --verbose-outputs
       -> validate_runtime_outputs.py
       -> audit_full_market_sanity.py
       -> audit_candidate_quality_drift.py
       -> run_kelly_stakes.py when elite rows exist
       -> post_run_tracking.py / grade_completed_picks.py / market_shadow_grading.py when applicable
       -> write_shadow_artifacts.py / write_daily_summary.py / write_quality_summary.py
       -> write_completion_state_audit.py / write_operator_card.py / write_artifact_manifest.py
```

Operational characteristics:

| Item | Behavior |
| --- | --- |
| Past-date safety | Blocks full generation for protected past dates unless `--force-past-date`. |
| Python selection | Uses `.venv\Scripts\python.exe` if present, else `py -3.13`. |
| Bankroll env | Defaults `COURTVISION_BANKROLL=1000`, `COURTVISION_MODE=betting`. |
| Kelly | Skipped when elite board is empty. |
| Latest observed run | 2026-07-08 completed as `NO BET`, no games/odds. |

## Missing Automation Stages

| Missing/weak stage | Impact |
| --- | --- |
| Central scheduler status inventory | Hard to prove which Windows tasks are installed and last succeeded without querying host state. |
| Cross-process locks | Duplicate collectors/finalizers can race if scheduled twice. |
| Alerting | Failures can sit in logs without notification. |
| Run manifest with pinned commit/env/model/data | Reproducibility depends on mutable `main` and local state. |
| Official pick settlement queue | Pick grading and observation grading are not cleanly separated. |
| Log retention policy | Generated logs/data can grow unbounded. |

