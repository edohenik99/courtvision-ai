# Evidence Index

Confidence labels:

| Label | Meaning |
| --- | --- |
| Confirmed | Direct code/log/data evidence inspected. |
| High confidence | Multiple repo artifacts support the claim; no conflicting evidence found. |
| Medium confidence | Reasonable inference from code/docs, but not recently executed. |
| Low confidence | Weak evidence or likely stale. |
| Unknown | Cannot be concluded from repo. |

## Major Findings To Evidence

| Finding / claim | Evidence file or command | Code region / function / artifact | Confidence |
| --- | --- | --- | --- |
| `CourtVisionAI` is canonical NBA runtime | `docs/ADR_001_CANONICAL_RUNTIME.md`, `courtvision_ai.py` | `CourtVisionAI`, CLI args | Confirmed |
| `run_today` orchestrates NBA daily flow | `run_today.bat`, `run_today.ps1` | calls `courtvision_ai.py`, validators, Kelly, reports | Confirmed |
| NBA latest observed run was no-bet/no-slate | `outputs/runtime/logs/run_today_2026-07-08.log`, `operator_card_2026-07-08.txt` | games/odds zero, final `NO BET` | Confirmed |
| NBA historical elite/Kelly path produced rows | `elite_board_2026-05-10.csv`, `kelly_stakes_2026-05-10.csv` | historical runtime artifacts | Confirmed |
| Elite defaults include strict thresholds | `courtvision/runtime_scoring.py` | `EliteThresholds.default()` | Confirmed |
| Elite is points-only by default | `courtvision/pipeline/predict_pipeline.py`, selectors | `PredictionConfig.elite_market_mode` | Confirmed |
| Live vs shadow separation exists | `docs/live_vs_shadow_map.md` | live/near/shadow definitions | Confirmed |
| Kelly reads elite board only | `scripts/run_kelly_stakes.py` | input path and market lock | Confirmed |
| MLB HR collector uses The Odds API | `tools/theoddsapi_live_hr_collector.py` | `BASE_URL`, events/odds calls | Confirmed |
| Collector scans max 12 events and skips games <30 minutes | collector constants | `MAX_EVENTS_PER_RUN`, `MIN_MINUTES_BEFORE_GAME` | Confirmed |
| Collector market is batter HR alternate | collector constants | `MARKETS` | Confirmed |
| Collector has same-day guard | collector code | `already_ran_today()` | Confirmed |
| Recent HR collection succeeded | `data/theoddsapi/live_hr_snapshots/run_log.csv` | 2026-07-10 row | Confirmed |
| HR master has 5,200 rows and no latest duplicates | `live_hr_props_master.csv`, nightly postflight log | row count/check output | Confirmed |
| Consolidated nightly finalizer does not collect odds | `tools/courtvision_mlb_nightly_pipeline.py` | subprocess calls omit collector | Confirmed |
| 3:30 job finalizes/results/grades | `docs/mlb_live_hr_daily_ops.md`, PS/Python pipeline | fill/export/coverage/grade steps | Confirmed |
| MLB final scores/results from StatsAPI | `fill_live_hr_results_from_mlb_statsapi.py` | `SCHEDULE_URL`, `BOXSCORE_URL`, `extract_player_home_runs` | Confirmed |
| HR result workflow is hybrid | workbook/filler/status code | `void`, `void_candidate`, manual review statuses | Confirmed |
| HR grader grades observations, not official picks | `grade_live_hr_results.py`, collector schema | odds rows + result key; no official pick schema | High confidence |
| Not every HR row is gradeable | coverage checker, workbook status counts | missing/status/void logic | Confirmed |
| MLB HR live picks are not implemented | absence of active selector plus docs | collector/finalizer are observation/result workflows | High confidence |
| API-NBA is research-only | `docs/api_nba_research_mode_audit.md` | no odds replacement | Confirmed |
| Config docs are confusing | `docs/ENV_CONFIG_AUDIT.md`, `.env.example` | canonical vs noncanonical vars | Confirmed |
| Alana integration absent | `rg -i "alana"` | no matches | Confirmed |
| Telegram exists | `courtvision_ai.py`, `courtvision/application.py` | `send_telegram_top_plays`, env vars | Confirmed |
| Test suite is broad but not run | `rg --files tests`, audit command policy | 293 test files | Confirmed |
| Other sports not production-facing | `docs/ADR_001_CANONICAL_RUNTIME.md`, sport package scan | WNBA/NFL/NHL scaffold | High confidence |

## Commands Used As Evidence

| Command family | Purpose |
| --- | --- |
| `git status`, `git log`, `git branch`, `git rev-parse` | Git orientation. |
| `rg --files` | Inventory scripts, docs, tests, packages. |
| `Get-Content` | Read docs/source/logs/CSV summaries. |
| Static source searches | Find provider/env/API/Alana/Telegram references. |
| Lightweight CSV inspection | Existing local row counts/status counts only. |

No live APIs, production pipelines, package installs, tests, commits, checkouts, merges, resets, stashes, or scheduled-task mutations were performed.

