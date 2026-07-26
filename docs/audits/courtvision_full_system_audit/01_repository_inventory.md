# Repository Inventory

Audit basis: read-only shell inspection, static source inspection, current docs, existing logs, and current data artifacts. No tests, production scripts, live APIs, package installs, git state changes, or pipeline runs were performed.

## Git State

| Item | Value | Confidence |
| --- | --- | --- |
| Branch | `main` | Confirmed by `git branch --show-current` |
| Commit | `811a07869a14bc865eec74977e6ab741ad0a0f14` | Confirmed by `git rev-parse HEAD` |
| Short commit | `811a078 Add consolidated MLB nightly pipeline and robust HR result handling` | Confirmed by `git log` |
| Initial working tree | Clean before audit docs were created | Confirmed by `git status --porcelain=v1 -uall` |
| Allowed audit modification | New directory `docs/audits/courtvision_full_system_audit/` only | Confirmed by user request |

Recent relevant commits show heavy work on MLB nightly finalization and evidence automation:

| Commit | Subject |
| --- | --- |
| `811a078` | Add consolidated MLB nightly pipeline and robust HR result handling |
| `d8f047b` | feat: add guarded evidence automation scripts |
| `82f067b` | docs: add evidence forward trial runbook |
| `4250d5d` | feat: add evidence closing-line and result updaters |
| `723edea` | feat: add evidence closing-line updater |

## Repository Shape

| Area | Role | Evidence | Status |
| --- | --- | --- | --- |
| `courtvision/` | Main package: providers, pipeline, selection, scoring, sports modules. | 289 Python modules counted in package scan. | Active/supporting |
| `courtvision_ai.py` | Canonical NBA operator runtime and large monolith. | `docs/ADR_001_CANONICAL_RUNTIME.md`, CLI args in file. | Active |
| `run_today.bat`, `run_today.ps1` | Canonical NBA daily runner wrappers. | Direct call chain to `courtvision_ai.py`. | Active |
| `scripts/` | NBA reporting, grading, research, evidence, MLB historical scripts, dashboard scripts. | 91 scripts in current operational-script filter. | Mixed active/supporting/research |
| `tools/` | MLB HR live collection/finalization/evidence wrappers. | 18 scripts/wrappers in current filter. | Active/supporting |
| `data/` | Local histories, manual datasets, MLB HR live data. | CSV/JSON stores including `data/history/` and `data/theoddsapi/`. | Generated/local |
| `outputs/` | Runtime artifacts, logs, operator boards, summaries. | `outputs/runtime/...` | Generated |
| `courtvision-raw/` | Large ignored raw MLB historical collections. | Collection manifests with Statcast/Retrosheet/weather files. | Raw/research |
| `docs/` | Architecture, runbooks, contracts, audits, plans. | 100+ docs observed. | Mixed current/stale/planned |
| `tests/` | Pytest suite and fixtures. | 293 test files counted. | Supporting |
| `.env`, `.env.example` | Local credentials and example config. | `.env` present; `.env.example` documents provider keys. | Sensitive/config |

## Size And File Composition

Static inventory excluding `.git`, `.venv`, Python caches, pytest caches, and temporary pytest dirs found approximately:

| Metric | Value |
| --- | ---: |
| Files | 12,867 |
| Approximate size | 3.14 GB |
| Python scripts/modules | 995 |
| Operational scripts/wrappers inventoried | 130 |
| Package modules in `courtvision/` | 289 |
| Test files under `tests/` | 293 |
| CSV files | 5,786 |
| JSON files | 3,458 |
| TXT files | 2,218 |
| Markdown files | 139 |
| PowerShell scripts | 11 |
| Batch files | 4 |

Largest local data/output areas:

| Directory | Approximate role | Approximate size |
| --- | --- | ---: |
| `courtvision-raw/` | MLB historical raw data | 2.82 GB |
| `outputs/` | Runtime/generated artifacts | 172.84 MB |
| `data/` | Histories, live HR data, manual datasets | 88.17 MB |
| `tests_artifacts/` | Test artifacts | 34.44 MB |
| `courtvision/` | Source package | 4.23 MB |

## Languages, Runtime, Dependencies

| Item | Evidence | Notes |
| --- | --- | --- |
| Primary language | Python | `pyproject.toml`, 995 `.py` files |
| Shell/runtime | Windows PowerShell and Batch | `run_today.ps1`, `tools/*.ps1`, `.bat` wrappers |
| App/reporting | Streamlit | `requirements.txt`, `courtvision_streamlit_app.py`, dashboard scripts |
| Python version | `>=3.11`; local runner prefers `.venv`, then `py -3.13` | `pyproject.toml`, `run_today.ps1` |
| Package entry point | `courtvision = courtvision.cli.main:main` | `pyproject.toml` |
| Required dependencies | `pandas`, `requests`, `pytest`, `streamlit`, `python-dotenv`, `balldontlie` | `requirements.txt` |
| Optional collector dependencies | `pybaseball`, `python-dateutil`, `meteostat` | `pyproject.toml` |

## External Services And APIs

| Provider | Purpose | Used by | Credentials | Quota/cost risk | Current status |
| --- | --- | --- | --- | --- | --- |
| BallDontLie | Canonical NBA games/stats/odds/injuries path. | `courtvision_ai.py`, BDL adapters | `BALLDONTLIE_API_KEY` | Paid/subscription risk | Active in latest NBA log smoke check |
| The Odds API | MLB HR live odds; NBA research/smoke tooling. | `tools/theoddsapi_live_hr_collector.py`, smoke scripts | `THE_ODDS_API_KEY` | Credit consumption; logged by collector | Active for MLB HR data collection |
| MLB StatsAPI | MLB schedule/boxscore finalization. | `tools/fill_live_hr_results_from_mlb_statsapi.py` | None observed | Service availability/rate risk | Active finalization source |
| API-NBA | NBA stats-only research path. | `scripts/smoke_api_nba.py`, docs | `API_NBA_KEY` / `API_SPORTS_KEY` | Subscription risk | Research-only by docs |
| SportsDataIO | Alternate/non-canonical provider path. | Provider-manager modules/tests | `SPORTSDATAIO_API_KEY` | Subscription risk | Non-canonical |
| Retrosheet/Chadwick | MLB historical labels/identity support. | MLB historical scripts | Files/downloads | Data availability/versioning | Research support |
| pybaseball/Baseball Savant | Statcast historical data. | MLB ingest scripts | None observed | Download volume/rate | Research support |
| Meteostat/NOAA/weather | Weather context. | MLB weather ingest scripts | Optional | Data availability | Research support |
| Telegram | Optional delivery of top plays. | `courtvision_ai.py`, `courtvision/application.py` | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Delivery only | Optional |
| Alana | No code evidence found. | None | None | N/A | Not implemented |

## Operational Script Inventory Summary

This audit inventoried 130 operational scripts/wrappers in root, `scripts/`, `tools/`, `courtvision/cli/`, and `courtvision/sports/mlb/hr_report.py`. The detailed script reference is in `04_script_reference.md`.

| Script group | Count | Role | Status |
| --- | ---: | --- | --- |
| Root wrappers/apps/tests | 9 | Canonical runners, Streamlit apps, quick diagnostics. | Mixed active/supporting |
| `courtvision/cli/` | 3 | Package CLI entrypoint. | Supporting |
| `scripts/` | 91 | NBA runtime reports, evidence, grading, research, MLB historical tooling. | Mixed active/research/legacy |
| `tools/` | 18 | MLB HR live odds/result/grading/evidence automation. | Active/supporting |
| MLB HR package report | 1 | Report CLI for MLB HR research/odds. | Supporting |

## Important Data Stores

| Dataset | Producer | Consumer | Format | Update pattern | Current role |
| --- | --- | --- | --- | --- | --- |
| `data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv` | MLB HR collector | Daily check, workbook, grader | CSV | Append then dedupe | MLB HR market observations |
| `data/theoddsapi/live_hr_snapshots/run_log.csv` | MLB HR collector/wrapper | Daily guard/audit | CSV | Append | API-credit/run evidence |
| `data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv` | Workbook generator, manual edits, StatsAPI filler | Exporter/coverage/grader | CSV | Overwrite with preserve-results | Hybrid result workbench |
| `data/theoddsapi/live_hr_snapshots/live_hr_results.csv` | Exporter | Coverage/grader | CSV | Overwrite with flag | Strict settlement input |
| `data/history/pick_history.csv` | NBA tracking/grading | Reports/evidence | CSV | Append/update | NBA pick history |
| `data/history/market_shadow_history.csv` | Shadow grading | Shadow reports | CSV | Append/update | NBA market shadow record |
| `outputs/runtime/operator/*` | `run_today.ps1` chain | Operators/evidence | CSV/TXT/JSON | Date-scoped writes | Daily operator artifacts |
| `courtvision-raw/mlb/*` | MLB collection/staging scripts | Research/backtests | CSV/JSON manifests | Versioned raw packs | MLB historical data |

## Current Documentation State

Current/high-signal docs:

| Doc | Why it matters |
| --- | --- |
| `docs/ADR_001_CANONICAL_RUNTIME.md` | Defines canonical NBA runtime and sport promotion status. |
| `docs/live_vs_shadow_map.md` | Separates live picks, shadow lanes, watchlists, and Kelly. |
| `docs/ENV_CONFIG_AUDIT.md` | Maps active vs confusing env vars. |
| `docs/mlb_live_hr_collector.md` | Describes MLB HR collector guardrails. |
| `docs/mlb_live_hr_daily_ops.md` | Describes daily collection and consolidated nightly finalization. |
| `docs/api_nba_research_mode_audit.md` | Confirms API-NBA research-only status. |

Known doc risks:

| Risk | Evidence |
| --- | --- |
| Some docs are plans/contracts rather than implemented behavior. | Many `COURTVISION_PHASE*` and MLB HR contract docs. |
| `.env.example` includes inactive/confusing provider priority settings. | `docs/ENV_CONFIG_AUDIT.md` and `.env.example`. |
| Older audits may describe earlier system state. | Dates in `docs/audits/`, pre-July docs. |
| Test-pass claims in older docs were not re-run during this audit. | Audit obeyed read-only/no-run constraints. |

