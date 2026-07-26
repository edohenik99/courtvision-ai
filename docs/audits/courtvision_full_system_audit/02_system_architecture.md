# System Architecture

## Architecture Classification

CourtVision is a mixture of:

| Classification | Applies? | Evidence | Confidence |
| --- | --- | --- | --- |
| One unified application | Partly | Package modules and `courtvision_ai.py` share core concepts. | Medium |
| Multiple loosely connected pipelines | Yes | NBA `run_today`, MLB HR tools, evidence tools, and MLB research scripts have separate triggers and data stores. | Confirmed |
| Collection of scripts | Yes | 130 operational scripts/wrappers inventoried. | Confirmed |
| Partially orchestrated system | Yes | `run_today.ps1` and `tools/courtvision_mlb_nightly_pipeline.py` orchestrate multi-step workflows. | Confirmed |
| Production-ready platform | No | Missing official pick identity, forward evidence, monitoring, and reproducible deployment. | High confidence |
| Research prototype | Yes, for MLB and other sports | MLB HR research docs/scripts; WNBA/NFL/NHL scaffolding. | Confirmed |

## Current System Context

```mermaid
flowchart LR
  subgraph Providers["External providers"]
    BDL["BallDontLie NBA"]
    Odds["The Odds API"]
    MLBAPI["MLB StatsAPI"]
    APINBA["API-NBA research"]
    Hist["Statcast / Retrosheet / Chadwick / Weather"]
  end

  subgraph Runtime["CourtVision repo"]
    NBA["NBA canonical runtime\nrun_today.ps1 -> courtvision_ai.py"]
    NBAPkg["courtvision pipeline/selection/scoring modules"]
    MLBCollect["MLB HR collector\ntheoddsapi_live_hr_collector.py"]
    MLBFinal["MLB HR finalizer/grader\ncourtvision_mlb_nightly_pipeline.py"]
    MLBR["MLB research/backtest scripts"]
    Evidence["Evidence/export scripts"]
    Reports["Operator cards, summaries, dashboards"]
  end

  subgraph Stores["Local stores"]
    Outputs["outputs/runtime/*"]
    Histories["data/history/*"]
    HRData["data/theoddsapi/live_hr_snapshots/*"]
    RawMLB["courtvision-raw/mlb/*"]
  end

  BDL -->|"active NBA provider"| NBA
  NBA --> NBAPkg
  NBAPkg --> Outputs
  NBA --> Histories
  Outputs --> Reports
  Histories --> Reports

  Odds -->|"active HR odds collection"| MLBCollect
  MLBCollect --> HRData
  MLBAPI -->|"postgame settlement"| MLBFinal
  HRData --> MLBFinal
  MLBFinal --> HRData

  Hist --> MLBR
  MLBR --> RawMLB
  APINBA -. "research only" .-> Evidence
  Outputs --> Evidence
  Histories --> Evidence
```

## Layered Architecture

| Layer | Components | Inputs | Outputs | Failure points | Operational status |
| --- | --- | --- | --- | --- | --- |
| Provider access | `BallDontLieClient`, The Odds API collector, MLB StatsAPI filler, API-NBA smoke clients | API keys/env vars, endpoint URLs | Provider payloads/normalized rows | Key expiration, quota, schema drift, service outage | NBA/MLB active, API-NBA research-only |
| Raw ingestion | MLB collector, MLB historical ingest scripts | APIs, local CSV packs | Snapshots, raw manifests | Credit exhaustion, partial downloads, duplicate snapshots | Active for HR, research for historical MLB |
| Storage | `outputs/runtime`, `data/history`, `data/theoddsapi`, `courtvision-raw` | Runtime/generated rows | CSV/JSON/TXT artifacts | Local-only generated data, stale ignored files | Operational with reproducibility risk |
| Normalization | `bdl_odds_adapter`, MLB name normalization, candidate builders | Provider rows | Canonical candidate/market rows | Identity mismatch, market naming differences | Active/supporting |
| Feature/scoring | NBA baselines, context/injury modules, runtime scoring, MLB HR research scorer | Historical/runtime context | Projections, edges, confidence, quality | Look-ahead/leakage, stale baselines, hard-coded thresholds | NBA active, MLB research |
| Selection | `operator_boards`, `pipeline_selectors`, runtime selection | Candidate rows | Elite/full-market/near/watchlist boards | Over-filtering, empty elite, unsupported market gates | NBA active |
| Staking | `scripts/run_kelly_stakes.py`, `courtvision/betting/kelly.py` | Elite board, bankroll env | Kelly CSV | Insufficient evidence, cap assumptions, malformed odds | NBA points-only active with guardrails |
| Results | NBA grading scripts, MLB StatsAPI filler/workbook/exporter | Game outcomes, boxscores, manual statuses | Strict result CSVs/histories | Missing players, postponed games, manual unresolved rows | Partial/hybrid |
| Grading | `grade_completed_picks.py`, `grade_live_hr_results.py`, shadow graders | Picks/observations + results | Graded CSVs, summaries | Observation-vs-pick confusion, duplicate identity | Implemented with risks |
| Reporting | Operator cards, daily summaries, quality summaries, dashboards | Boards, histories, logs | TXT/JSON/CSV/Streamlit views | Stale files, generated output churn | Active/supporting |
| Automation | PowerShell wrappers, scheduled-task installers | Local repo, venv/python, env vars | Logs/artifacts | Mutable git pull, no central lock/alert, hard-coded paths | Partial |

## Component Diagram

```mermaid
flowchart TB
  Runner["run_today.bat / run_today.ps1"]
  Monolith["courtvision_ai.py\nCourtVisionAI"]
  Pipeline["courtvision.pipeline.predict_pipeline"]
  Select["courtvision.selection.*"]
  Score["courtvision.runtime_scoring / runtime_selection"]
  Kelly["scripts/run_kelly_stakes.py\ncourtvision.betting.kelly"]
  Report["scripts/write_* reports"]
  Grade["scripts/grade_completed_picks.py\nscripts/market_shadow_grading.py"]
  BDL["BallDontLie"]

  Runner --> Monolith
  Monolith --> BDL
  Monolith --> Pipeline
  Pipeline --> Score
  Pipeline --> Select
  Select -->|"elite board"| Kelly
  Monolith -->|"operator artifacts"| Report
  Runner --> Grade
  Runner --> Report

  HRWrap["tools/run_live_hr_daily_auto.ps1"]
  HRCollector["tools/theoddsapi_live_hr_collector.py"]
  HRMaster["live_hr_props_master.csv"]
  NightWrap["tools/run_courtvision_mlb_nightly_pipeline.ps1"]
  NightPy["tools/courtvision_mlb_nightly_pipeline.py"]
  HRFill["fill_live_hr_results_from_mlb_statsapi.py"]
  HRCover["check_live_hr_results_coverage.py"]
  HRGrade["grade_live_hr_results.py"]
  HRSum["summarize_live_hr_grades.py"]

  HRWrap --> HRCollector --> HRMaster
  NightWrap --> NightPy
  HRMaster --> NightPy
  NightPy --> HRFill
  NightPy --> HRCover
  NightPy --> HRGrade
  NightPy --> HRSum
```

## Current Data Flow

```mermaid
flowchart LR
  BDL["BallDontLie NBA games/odds/injuries"]
  NBAFetch["CourtVisionAI provider fetch"]
  Cand["Candidate builder + scoring"]
  Boards["elite/full_market/near/watchlist"]
  Kelly["Kelly stakes when elite exists"]
  NBAHist["pick/shadow/history stores"]
  NBAReports["operator card + summaries"]

  BDL --> NBAFetch --> Cand --> Boards --> Kelly
  Boards --> NBAReports
  Boards --> NBAHist

  OddsAPI["The Odds API MLB batter_home_runs_alternate"]
  HRSnap["timestamped HR snapshot"]
  HRMaster["deduped HR master"]
  HRBook["results workbook"]
  MLBStats["MLB StatsAPI schedule/boxscore"]
  HRResults["strict results CSV"]
  HRGrades["grade CSV + markdown summary"]

  OddsAPI --> HRSnap --> HRMaster --> HRBook
  MLBStats --> HRBook --> HRResults --> HRGrades
  HRMaster --> HRGrades
```

## Automation Diagram

```mermaid
sequenceDiagram
  participant Op as Operator / Scheduler
  participant Daily as run_live_hr_daily_auto.ps1
  participant Collector as theoddsapi_live_hr_collector.py
  participant Check as run_live_hr_daily_check.py
  participant Night as run_courtvision_mlb_nightly_pipeline.ps1
  participant Pipe as courtvision_mlb_nightly_pipeline.py
  participant Stats as MLB StatsAPI
  participant Grade as HR grader/summarizer

  Op->>Daily: Intended daily pregame run
  Daily->>Daily: checkout/pull main, same-day guard
  Daily->>Collector: collect if no success/master date
  Collector->>Collector: write snapshot/master/run_log
  Daily->>Check: offline validation

  Op->>Night: Intended 03:30 finalization
  Night->>Pipe: select completed dates/lookback
  Pipe->>Check: preflight
  Pipe->>Stats: schedule + boxscore for completed games
  Pipe->>Pipe: fill workbook/export results/coverage
  Pipe->>Grade: grade only dates ready to grade
```

## Results And Grading Workflow

```mermaid
flowchart TD
  HRMaster["HR master observations"]
  Workbook["live_hr_results_workbook.csv"]
  Manual["Manual review / workbook status edits"]
  StatsAPI["MLB StatsAPI boxscore homeRuns"]
  Strict["live_hr_results.csv"]
  Coverage["coverage checker\nready_to_grade"]
  Grader["grade_live_hr_results.py"]
  Summary["summary markdown"]

  HRMaster --> Workbook
  StatsAPI --> Workbook
  Manual -. "hybrid/manual flow" .-> Workbook
  Workbook --> Strict
  Strict --> Coverage
  Coverage -->|"ready"| Grader
  HRMaster --> Grader
  Grader --> Summary
  Coverage -. "not ready: skip grading" .-> Manual
```

## Proposed Future Target Architecture

This diagram is proposed, not current.

```mermaid
flowchart LR
  Providers["Typed provider adapters"]
  Raw["Immutable raw event store"]
  Norm["Normalized fact tables\nmarkets, games, players, teams"]
  Feature["Feature store + model registry"]
  Picks["Official picks table\nimmutable pick_id"]
  Settle["Settlement service\nresults + audit trail"]
  Eval["Evaluation + evidence ledger"]
  Risk["Bankroll/risk engine gated by evidence"]
  API["Stable JSON/API surface\nfor dashboard or Alana"]

  Providers --> Raw --> Norm --> Feature --> Picks --> Settle --> Eval
  Eval --> Risk
  Picks --> API
  Settle --> API
  Eval --> API
```

