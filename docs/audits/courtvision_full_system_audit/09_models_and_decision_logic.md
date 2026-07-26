# Models And Decision Logic

## Model Inventory

| Model | Sport/Market | Training Data | Inference Entry Point | Active | Validation Evidence |
| --- | --- | --- | --- | --- | --- |
| NBA player/team baselines and calibration | NBA player props/team markets | Historical NBA stats/history stores | `courtvision_ai.py`, `courtvision/pipeline/predict_pipeline.py` | Yes | Runtime logs show baseline rows and calibrated markets |
| NBA runtime scoring/selection heuristics | NBA operator picks | Candidate rows, baselines, odds, context | `runtime_scoring.py`, `runtime_selection.py`, `selection/*` | Yes | Historical elite/full-market outputs and tests |
| NBA Kelly sizing | NBA elite `player_points` | Elite board, odds, confidence, bankroll | `scripts/run_kelly_stakes.py`, `courtvision/betting/kelly.py` | Conditional | Kelly outputs for 2026-05-10/05-13 |
| MLB HR research scorer | MLB HR props | Odds/context/power/pitch/weather/ballpark inputs | `courtvision/sports/mlb/hr_prop_engine.py`, `hr_pipeline.py` | Research | MLB HR contracts/backtest scripts |
| MLB HR historical/backtest pipeline | MLB HR modelling research | Statcast, Retrosheet, weather, ballpark, odds snapshots | `scripts/mlb_*`, `courtvision/sports/mlb/training/*` | Research | Readiness/backtest docs and tests |
| WNBA/NFL/NHL scaffolding | Other sports | Not established as live | sport packages | No | ADR says research/planned |

## Predictive Modelling Vs Collection

| Activity | Example | Is it a prediction? |
| --- | --- | --- |
| Odds collection | `theoddsapi_live_hr_collector.py` stores player/book/price rows. | No |
| Result filling | StatsAPI boxscore HR counts. | No |
| Observation grading | Grade every HR observation against actual HR count. | No official pick prediction unless selected first |
| Candidate scoring | NBA projection/edge/confidence logic. | Yes, active for NBA |
| Research scoring | MLB HR `hr_prop_engine` and backtest scripts. | Research prediction, not live official pick |
| Kelly sizing | Stake sizing after selection. | Not prediction; bankroll/risk layer |

## NBA Decision Logic

| Logic | Evidence | Description |
| --- | --- | --- |
| Confidence/quality thresholds | `runtime_scoring.py` | Defaults include confidence 0.65 and quality 82.0 for elite eligibility. |
| Player minutes/edge requirements | `runtime_scoring.py` | Player candidates need minutes/edge/confidence requirements. |
| Points-only elite policy | `PredictionConfig.elite_market_mode`, selectors | Elite board defaults to `player_points`. |
| Live/source gate | `operator_boards.py`, `docs/live_vs_shadow_map.md` | Live picks must have sufficient source/live market evidence. |
| Strong OVER guard | `runtime_selection.py` | Blocks risky player-points OVERs under calibration/evidence concerns. |
| Context high-caution gate | live-vs-shadow map and Kelly script | Blocks/skips high-risk context scenarios. |
| Exposure caps | `pipeline_selectors.py`, Kelly script | Limits concentration. |
| Identity quarantine/dedupe | `operator_boards.py` | Avoids duplicate or unsafe betting identities. |

## Kelly Logic

| Item | Behavior |
| --- | --- |
| Eligible source | Elite board only. |
| Eligible market | NBA `player_points` only in current active lock. |
| Inputs | odds, confidence, side edge/edge, bankroll, market/context fields. |
| Formula | Quarter-Kelly with max stake fraction and daily exposure cap. |
| Default bankroll | `COURTVISION_BANKROLL`, defaulted to 1000 in `run_today.ps1`. |
| Default exposure | `COURTVISION_MAX_DAILY_EXPOSURE` or default cap in script. |
| Failure behavior | Missing/empty input fatal if called directly; `run_today` skips when elite count is zero. |

## MLB HR Decision Logic

| Layer | Current behavior |
| --- | --- |
| Live collector | No model. Collects bookmaker observations. |
| Results finalizer | No model. Settles observations against MLB StatsAPI. |
| Grader | No model. Compares actual HR count to point/side and odds. |
| Research scorer | Rule/research scoring exists in MLB package. |
| Official selector | Not found as active live HR pick layer. |
| Bankroll/Kelly | No active MLB HR bankroll integration found. |

## Leakage And Bias Risks

| Risk | Area | Current mitigation/evidence | Remaining concern |
| --- | --- | --- | --- |
| Look-ahead bias | MLB historical research | Leakage/readiness docs/scripts exist. | Need verified backtest outputs and frozen feature snapshots. |
| Survivorship bias | Player datasets | Crosswalk/readiness scripts exist. | Need promotion criteria before live use. |
| Result leakage | Closed-slate NBA reruns | `run_today.ps1` closed-slate guard. | `--force-past-date` can override. |
| Market-observation bias | MLB HR grades | Date-scoped grader and void exclusions. | Observations still not picks. |
| Overfitting thresholds | NBA gates | Hard-coded thresholds plus audit docs. | Need evidence-backed threshold governance. |

