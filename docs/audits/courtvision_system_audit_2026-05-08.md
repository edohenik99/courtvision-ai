# CourtVision AI System Audit - 2026-05-08

## Executive Summary

CourtVision is in a much stronger state than it was before the recent grading and manual-review work. The current system has a real production pipeline, live-market filtering, board diagnostics, market shadow history, repaired pending-grade handling, CourtVision Power Rating context, injury context, same-opponent warnings, Kelly output warnings, and a broad regression test suite.

What works well right now:

- Live odds are normalized into explicit over/under candidate rows with player identity, market type, line, odds, source, and unresolved-row diagnostics.
- Runtime scoring separates edge, confidence, quality score, penalties, and selection score into package modules.
- Elite selection is locked down by market policy, quality/confidence checks, directional edge validation, team/game caps, and player-points risk guards.
- Kelly staking is points-only, conservative, capped, and now surfaces manual-review warnings without changing stake math.
- Market shadow history and repair scripts have recent coverage for stale pending rows, graded row preservation, fixture exclusion, and missing actual values.
- CourtVision Power Rating integration is observation-oriented and has strict final-game detection before historical game results are written.
- Daily and quality reports now expose run health, board counts, rejection reasons, context gates, manual review counts, Kelly safety, and history feedback.

What is weak right now:

- Fragility and survivability do not exist as first-class scores. Their ingredients exist, but are scattered across injury, game context, scoring penalties, calibration, same-opponent diagnostics, and Kelly reporting.
- Several concepts are duplicated: candidate scoring vs legacy runtime scoring, runtime Kelly diagnostics vs Kelly stake output, market quality weights vs board audit trust weights, and package pipeline vs the large legacy `courtvision_ai.py` orchestrator.
- Empty `elite_board` artifacts can be schema-thin. A populated `full_market_board_2026-05-08.csv` has 131 columns, while the empty `elite_board_2026-05-08.csv` has 52 context/review columns and omits many base pick columns. That is a compatibility risk for downstream readers that expect stable headers even when no picks qualify.
- CourtVision Power Rating context is strong in diagnostics and quality summary, but it is not yet consistently visible as row-level board fields in the inspected operator board header.
- Market inflation, public over bias, true live betting context, and bankroll risk mode are mostly scaffolds or absent data, not production controls.

What is risky right now:

- Adding another scoring layer directly into selection would be risky because similar dampeners already exist in multiple places and could double-count the same signal.
- A new layer that changes `quality_score`, `confidence`, `edge`, ranking, elite gates, or Kelly math would be hard to attribute without a shadow period.
- The legacy monolith still owns important run behavior and artifact writing. Package modules are cleaner, but the daily runner still enters through `courtvision_ai.py`, so future changes must check both paths.
- Schema drift is the highest near-term operational risk. New diagnostics must be appended without shrinking or reordering required fields, and empty boards need stable headers.

What should not be changed yet:

- Do not change projection math, candidate scoring, quality score, confidence, edge, selection score, elite thresholds, eligibility gates, Kelly math, Kelly eligibility, staking caps, provider priority, or odds normalization.
- Do not promote fragility/survivability into filtering, ranking, or stake sizing until it has run as diagnostics-only and has history attribution.
- Do not rename CourtVision Power Rating or alter the rating result history format while recent final-game detection changes settle.
- Do not remove legacy compatibility wrappers until all daily run entrypoints are package-owned and schema-tested.

## Current Architecture Map

| Module | File path | Responsibility | Main inputs | Main outputs | Downstream consumers |
| --- | --- | --- | --- | --- | --- |
| Daily PowerShell runner | `run_today.ps1` | End-to-end operator run: fit fallback, prediction, validation, Kelly, post-run history, grading, summaries. | Date, model baselines, provider data, env bankroll. | Runtime operator CSV/JSON/TXT artifacts, logs. | Operator, history tracking, Kelly, daily/quality summaries. |
| CLI daily shim | `scripts/run_daily.py` | Thin compatibility runner around `CourtVisionAI.predict`. | Prediction date, output dir, provider key. | Saved board artifacts and manifest. | Manual/alternate daily execution. |
| Legacy orchestrator | `courtvision_ai.py` | Canonical daily entrypoint today; fetches data, delegates into package pipeline, writes many artifacts, attaches reporting metadata. | Provider games/odds/stats, baselines, runtime config. | Player/game predictions, elite/full-market boards, audits, grading reports. | `run_today.ps1`, history scripts, dashboards, reports. |
| Package prediction pipeline | `courtvision/pipeline/predict_pipeline.py` | Builds candidate universe, injury context, market context, scoring, boards, diagnostics. | Games, odds, player/team baselines, injuries, config. | `PredictionResult`, elite/full-market/stat-only frames, selection traces, market readiness. | `courtvision_ai.py`, tests, board writers. |
| Pipeline runner/contracts | `courtvision/pipeline/runner.py`, `courtvision/pipeline/contracts.py` | Manifest and alternate board save helpers. | Prediction outputs. | Pipeline manifest, board files under alternate layout. | `scripts/run_daily.py`, tests. |
| Odds adapter | `courtvision/data/bdl_odds_adapter.py` | Normalizes provider odds into player prop rows and unresolved diagnostics. | Raw provider odds payload/DataFrame. | Normalized odds with `market_type`, `selection`, `line`, `odds`, player identity, source fields. | Prediction pipeline, candidate scoring, market coverage audits. |
| Data normalization | `courtvision/data/normalization.py` | Normalizes games, stats, and older odds frames. | Raw provider games/stats/odds. | DataFrames with canonical fields. | Fetching pipeline, tests, game result updater. |
| Candidate builder | `courtvision/data/candidates.py` | Scores supported player markets from baselines and odds. | Player baselines, odds, injury/context inputs. | Candidate rows with projection, edge, confidence, support status. | Prediction pipeline. |
| Scoring policy | `courtvision/scoring/candidate_scoring.py` | Computes quality score, selection score, and elite candidate eligibility. | Candidate row, edge/confidence helpers, thresholds. | `quality_score`, `selection_score`, `is_elite`, eligibility decisions. | Prediction pipeline, runtime selection, tests. |
| Edge helper | `courtvision/scoring/edge.py` | Directional edge, edge percentage denominator floors, favorite/line bias adjustment. | Projection, line, side, odds, market. | Adjusted edge metrics. | Candidate scoring. |
| Confidence helper | `courtvision/scoring/confidence.py` | Confidence and historical multiplier from minutes, recent form, role, injury, and stability. | Candidate stats, minutes, injury/support signals. | Confidence value and multiplier inputs. | Candidate scoring. |
| Penalty helper | `courtvision/scoring/penalties.py` | Longshot, volatility, and projection realism penalties. | Candidate odds, minutes, edge, confidence, injury. | Penalty points. | Candidate scoring. |
| Runtime selection | `courtvision/runtime_selection.py` | Central selection policies, qualification gates, backfill criteria, player-points risk guards. | Candidate rows and thresholds. | Rejection reasons, gate decisions, selection policy checks. | Prediction pipeline, board audit, tests. |
| Operator board builder | `courtvision/selection/operator_boards.py` | Applies unified live-market mask, milestone filtering, elite/full-market selector callbacks, lane traces. | Prepared candidates. | Elite board, full-market board, selection trace. | Prediction pipeline, diagnostics. |
| Runtime audit | `courtvision/runtime_audit.py` | Enriches board rows and diagnostics with buckets, guard reasons, market trust weights, selection outcomes. | Qualified, elite, full-market, rejected frames. | Board diagnostics JSON/CSV payloads. | Quality summary, operator review, tests. |
| Game context | `courtvision/context/game_context.py` | Rest, pace, defense, playoff, spread/total, context alignment, and caution signals. | Games, team baselines, candidate rows. | Context columns and diagnostic suppression reasons. | Boards, Kelly skip diagnostics, quality summary. |
| CourtVision Power Rating context | `courtvision/context/game_strength.py` | Adds matchup strength, win probability, competitiveness, and blowout risk from current team ratings. | Board rows with team/opponent/home-away; rating map. | `POWER_RATING_CONTEXT_COLUMNS`. | Quality summary, backfill script, shadow reports. |
| CourtVision Power Rating store | `courtvision/ratings/power_ratings_store.py` | Strictly persists final game results and builds current team ratings as of a date. | `data/history/game_results.csv`, provider game rows. | Current rating map, game result history rows. | Game strength context, backfills, tests. |
| Game result updater | `scripts/update_game_results_history.py` | Fetches provider games and appends completed game results for rating history. | Prediction date/window, provider games. | `data/history/game_results.csv`. | Power Rating store. |
| Injury engine | `courtvision/injuries/injury_engine.py` | Builds team/player injury impact context and applies projection/confidence adjustments. | Injury reports, player baselines, active teams. | Injury context and row-level injury metadata. | Prediction pipeline, quality summary. |
| Injury realism | `courtvision/injuries/realism.py` | Observation/control dampeners for player-points overs under fragile injury setups. | Candidate row. | Realism dampener flags/reasons. | Runtime audit and selection guards. |
| Injury volatility | `courtvision/injuries/volatility.py` | Recent form, independent support, and volatility confidence penalty. | Candidate row/history signals. | Confidence penalty/support diagnostics. | Candidate scoring. |
| Market quality | `courtvision/market/quality.py`, `courtvision/market/evaluator.py` | Market aliases, trust weights, matching quality, and quality threshold helpers. | Candidate/market fields. | Market quality weights, quality bands, eligibility diagnostics. | Prediction pipeline, tests. |
| Legacy value engine | `courtvision/market/value_engine.py` | Older object-based play ranking with its own fair probability and stake fraction. | `PlayerProjection`, `MarketProp`. | `RankedPlay` list. | Legacy/tests; should not be extended for new runtime layer. |
| Line movement scaffold | `courtvision/market_intelligence/line_movement.py` | In-memory line movement analyzer for opening/current/closing concepts. | Manual line snapshots. | Movement classification and stability score. | Not integrated into daily production pipeline. |
| Correlation scaffold | `courtvision/portfolio/correlation.py` | Same-player, same-game, stat dependency, and portfolio variance diagnostics. | Play identities. | Correlation matrix and high-correlation records. | Reporting tests/scaffolds, not active staking gates. |
| Kelly stake runner | `scripts/run_kelly_stakes.py` | Reads elite board and writes stake file with eligibility, caps, manual-review actions. | Elite board, bankroll, exposure cap. | `kelly_stakes_<date>.csv`, logs. | Operator, history, quality summary. |
| Kelly math | `courtvision/betting/kelly.py` | Conservative stake fraction calculation. | Edge, decimal odds, confidence. | Stake fraction/recommended bet amount. | Kelly runner and pipeline diagnostics. |
| History tracking | `scripts/history_tracking.py` | Persists daily picks and market shadow history, grades completed picks, updates summaries. | Runtime boards, Kelly files, history CSVs, provider grading. | `pick_history.csv`, `market_shadow_history.csv`, summaries. | Daily runner, grading scripts, reports. |
| Market shadow grading | `scripts/grade_market_shadow_history.py`, `scripts/market_shadow_grading.py` | Grades historical full-market rows, including combo markets. | Market shadow history, provider stats. | Updated market shadow history and reports. | Promotion readiness, market learning. |
| Pending repair | `scripts/repair_pending_grades.py` | Repairs stale completed pending rows and voids/diagnoses ungradeable rows. | Pick, market shadow, paper Kelly histories. | Repaired history rows and audit/report artifacts. | Feedback quality, reports. |
| Daily summary | `scripts/write_daily_summary.py` | Human-readable daily operator report and supplemental reports. | Runtime boards, Kelly, diagnostics, histories. | Daily summary text and report files. | Operator review. |
| Quality summary | `scripts/write_quality_summary.py`, `courtvision/reporting/quality_summary.py` | JSON/CSVL run health, coverage, Kelly safety, warnings, Power Rating context, history. | Runtime artifacts and histories. | `quality_summary_<date>.json`, history CSV/JSONL. | Operator, tests, audit trail. |
| Same-opponent rematch | `courtvision/reporting/same_opponent_rematch.py` | Adds Ajay-style same-opponent player-points warning and manual-review flags. | Pick/market shadow histories and current boards. | Same-opponent fields, manual-review counts. | Boards, daily summary, Kelly. |
| Manual-review decision recorder | `scripts/record_manual_review_decision.py` | Records operator play/skip/reduce decisions for review-required Kelly rows. | Kelly stakes row and operator decision. | `data/history/manual_review_history.csv`. | Future feedback analysis. |
| Runtime output validator | `scripts/validate_runtime_outputs.py` | Validates elite board existence, exposure caps, directional edges, and preview. | Elite board and audit summary. | Console pass/fail. | `run_today.ps1`, tests. |

## Existing Risk-Control Inventory

### Quality Score

- Main implementation: `courtvision/scoring/candidate_scoring.py`.
- Components include confidence, adjusted edge, market type/tier weighting, historical multiplier, penalties, and side bias.
- Board audit also creates `quality_band`.
- Risk: market quality/trust is represented both in scoring and in audit weights; future fragility work could double-count quality unless it reads existing fields instead of recalculating them.

### Confidence

- Main implementation: `courtvision/scoring/confidence.py`.
- Inputs include minutes, recent form, projection-line drift, injury risk, stability, and player profile bucket.
- Runtime pipeline also has a simpler `_compute_confidence` fallback for less-modeled paths.
- Kelly has a separate minimum confidence threshold of `0.55`.
- Risk: confidence is already an aggregate safety signal. Fragility should not lower confidence yet; it should expose separate diagnostics first.

### Edge

- Main implementation: `courtvision/scoring/edge.py`.
- Uses market-specific denominator floors and a favorite/line bias factor.
- Candidate rows expose `edge`, `edge_pct`, `side_edge`, and `side_edge_pct`.
- Elite direction validation and Kelly require positive side edge.
- Risk: both absolute edge and edge percentage are used in different places, so new layers should document exactly which one they read.

### Realism Dampeners

- `courtvision/scoring/penalties.py` adds longshot, volatility, and projection realism penalties.
- `courtvision/injuries/realism.py` flags fragile player-points overs under injury-driven setups.
- `courtvision/runtime_selection.py` includes player-points risk guard and strong-over calibration guard.
- `courtvision/context/game_context.py` can create high caution for over picks when context supports the under.
- Risk: these are conceptually related but not centrally named. This is the natural home for a future fragility input inventory, not a reason to add another hidden penalty immediately.

### Review Flags

- `courtvision/reporting/same_opponent_rematch.py` adds:
  - `same_opponent_recent_games`
  - `same_opponent_last_actual_points`
  - `same_opponent_last_line`
  - `same_opponent_last_selection`
  - `same_opponent_last_result_status`
  - `same_opponent_under_warning`
  - `same_opponent_warning_reason`
  - `manual_review_required`
  - `manual_review_reason`
- `scripts/run_kelly_stakes.py` carries those into Kelly and adds:
  - `recommended_action`
  - `review_status`
  - `stake_policy`
  - `operator_action`
  - `operator_note`
- Risk: manual review is visible but not blocking by design. That is correct for now, but reports must keep making it hard to miss.

### Elite Gates

- `PredictionConfig` defaults: `min_edge=0.5`, `min_confidence=0.35`, `elite_market_mode="points_only"`.
- `EliteThresholds.default()` controls quality/confidence, player minutes, player edge, player confidence, moneyline thresholds, board limit, team cap, and game cap.
- Full-market gates require higher minutes/confidence for combo markets.
- `build_operator_boards` enforces a unified live-market mask and filters unsupported milestone markets.
- `select_elite_board` applies market allowlist, quality/confidence admission, directional validation, and exposure caps.
- Risk: elite admission is already dense. Do not add fragility as another hard elite gate until diagnostics prove it is predictive.

### Kelly Restrictions

- `courtvision/betting/kelly.py` uses conservative sizing with max stake fraction `0.02` and min confidence `0.55`.
- `scripts/run_kelly_stakes.py` locks Kelly to `player_points`, rejects invalid odds, invalid edge/confidence, non-positive edge, unsupported milestone markets, and high-caution over picks.
- Daily exposure default is `0.08` of bankroll.
- Manual-review picks keep stake math intact but get `REVIEW_BEFORE_BET`, `HOLD`, and `DO_NOT_BET_UNTIL_REVIEWED`.
- Risk: the prediction pipeline also computes `stake_fraction` and `recommended_bet` as diagnostics. Kelly output is the authoritative stake file.

### Same-Opponent Rematch Warnings

- Implemented for player-points candidates from `pick_history.csv` first and `market_shadow_history.csv` second.
- Current rule warns when today's player-points under follows a prior same-opponent actual points value above today's line.
- Surfaces in boards, daily summary, quality summary, reports, and Kelly.
- Risk: only player-points under is handled. Other repeat-matchup risks are not modeled yet.

### CourtVision Power Rating

- `courtvision/ratings/power_ratings_store.py` persists only games with explicit final/completed/closed status evidence, valid teams, and positive scores.
- `courtvision/context/game_strength.py` produces matchup strength, win probability, competitiveness, and blowout risk.
- `courtvision/reporting/power_rating_shadow.py` is observation-only attribution.
- Quality summary includes `power_rating_context`.
- Risk: use as context/reporting only for now. Do not let this move picks or stakes until shadow results are reviewed.

### Game Strength Context

- `courtvision/context/game_context.py` already captures opponent defense, pace, rest, playoff, spread, total, and context alignment.
- `context_caution_level` is active in Kelly for high-caution over skips and medium-neutral dampening.
- Risk: defensive resistance exists as a signal, but not as a dedicated opponent-resistance score. That should be exposed diagnostically before gating.

## Gap Analysis

| Capability | Current support | Assessment |
| --- | --- | --- |
| Fragility scoring | Partial ingredients only. | Injury impact, minutes, role bucket, line band, recent form, realism dampeners, context caution, strong-over guard, and same-opponent warning exist, but there is no single `fragility_score`, bucket, or reason list. |
| Survivability scoring | Partial ingredients only. | Edge, confidence, line/odds, context alignment, injury, game strength, and history exist, but no single survivability concept or durable schema exists. |
| Market inflation detection | Mostly missing in production. | `line_movement.py` has an analyzer scaffold, but daily artifacts do not carry opener/closer/consensus/public split history. |
| Bankroll/risk mode | Partial and disconnected. | Kelly caps, exposure summaries, and `OperatorConfig` mode presets exist, but daily prediction and Kelly do not use a single bankroll risk mode end to end. |
| Live betting context | Minimal. | Boards have game status/date/datetime and odds update timestamps, but not current period, clock, live score, possession, or time-sensitive line movement. |
| Parlay leg limits | Partial scaffold. | Correlation and SGP-related modules/reports exist, but there is no active parlay builder with enforced leg limits in the inspected daily output path. |
| Opponent defensive resistance | Partial. | `opponent_def_rating`, `opponent_net_rating`, `defense_context_signal`, and Power Rating context exist. Needs row-level resistance summary if used by operators. |
| Low-line points over suppression | Partial. | Line bands, favorite/line bias, profile buckets, realism dampeners, and strong-over guard exist. No dedicated low-line over suppression diagnostic. |
| Rookie/role-player over suppression | Partial. | `player_profile_bucket`, minutes, usage-like profile rules, and injury volatility exist. Explicit rookie/experience data was not found in the runtime board schema. |
| Public over bias controls | Weak. | Under bias and strong-over calibration exist, but public/handle data is missing. Do not claim public-bias detection without market data. |

## Data Availability Check

| Future feature | Data already available | Data partially available | Data missing | Recommendation |
| --- | --- | --- | --- | --- |
| Fragility diagnostics | Minutes, confidence, edge, injury impact, injury deltas, line band, profile bucket, context caution, same-opponent warning, high-caution over watchlist. | Recent form/support quality for player points. | Explicit rotation volatility, rookie flag, minutes-limit feed quality. | Implement now as diagnostics-only P1 after schema hardening. |
| Survivability diagnostics | Edge/side edge, confidence, quality, line/odds, market trust, context alignment, injury metadata, Power Rating context, historical grading. | Same-opponent and market shadow outcomes. | Closing-line value, public split, bet timing, live score context. | Implement now as diagnostics-only P1, no ranking/staking effect. |
| Market inflation detection | Current line, odds, odds update timestamp, market shadow history. | `line_movement.py` scaffold. | Opening line, line snapshots by book, consensus close, public/handle volume. | Defer production control; add schema placeholders only if sourced. |
| Bankroll/risk mode | Kelly caps, bankroll CLI/env, exposure summaries, operator mode config. | Manual review policy fields. | A single runtime mode wired through reports without affecting selection. | P1 as reporting-only mode label; gating later. |
| Live betting context | Game status/date/datetime, odds freshness. | Provider game status normalization. | Period, clock, live score, possession, minutes left, live odds snapshots. | P2 unless a reliable provider is added. |
| Parlay leg limits | Correlation detector, SGP board artifacts, exposure reporting. | Same game/player identity. | Actual parlay candidate builder and leg-cap policy in daily path. | P2, after straight-bet diagnostics stabilize. |
| Opponent defensive resistance | Opponent defensive rating/net rating, defense signal, Power Rating matchup context. | Per-market sensitivity to defense. | Player-specific matchup resistance by stat type. | P1 diagnostics-only; useful input to survivability. |
| Low-line points over suppression | Player-points line band, edge denominator floors, profile bucket, strong-over guard, historical grading. | Low-line role rules in confidence multiplier. | Explicit public low-line over demand. | P1 diagnostics-only; do not suppress yet. |
| Rookie/role-player over suppression | Minutes, role/profile bucket, confidence, injury support. | Usage proxy in profile bucket. | Rookie/year, rotation role source, coach minute volatility. | P1/P2 depending on data source. |
| Public over bias controls | Selection side, over/under counts, strong-over guard. | Line movement scaffold. | Public bets/handle/splits, book consensus movement. | P3 until data exists. |

## Output And Schema Audit

Inspected operator artifacts:

- `outputs/runtime/operator/full_market_board_2026-05-08.csv`: 112 rows, 131 columns.
- `outputs/runtime/operator/elite_board_2026-05-08.csv`: 0 rows, 52 columns.
- `outputs/runtime/operator/kelly_stakes_2026-05-07.csv`: 2 rows, 32 columns.
- `outputs/runtime/operator/quality_summary_2026-05-07.json`: run health, coverage, manual review counts, Kelly safety, board movement, date isolation, Power Rating context, warnings.

Current full-market board field groups:

- Identity: `prediction_date`, `player_name`, `entity_name`, `player_id`, `team`, `team_abbr`, `opponent`, `game_id`, `home_away`.
- Market: `market_type`, `raw_prop_type`, `raw_market_type`, `selection`, `sportsbook_line`, `line`, `odds`, `line_source`, `source_lane`.
- Model/scoring: `model_projection`, `projection`, `projection_support_status`, `minutes_avg`, `edge`, `edge_pct`, `side_edge`, `side_edge_pct`, `confidence`, `quality_score`, `selection_score`, `is_elite`.
- Runtime gates: `is_live_market`, `synthetic_line`, `qualification_reason`, `pre_rejection_reason`, `selection_rejection_reason`, `final_elite_rejection_reason`, `kelly_projected_skip_reason`.
- Recalibration: `recalibrated_projection`, `recalibrated_edge`, components JSON, selected/rejection/mode.
- Injury: status, impact, team/opponent impact, notes, baseline/adjusted projection/confidence and deltas.
- Manual context: manual status/minutes/projection/confidence/reason/applied.
- Game context: rest, pace, offensive/defensive/net ratings, implied totals, spread, context signals, alignment/caution/suppression.
- Audit buckets: prop type, minutes/odds/quality/confidence/side/profile/line/injury buckets, guard fields, market trust.
- Rematch/manual review: same-opponent fields and manual-review fields.

Current Kelly field groups:

- Pick identity and market fields.
- Odds/edge/confidence.
- Stake fraction, stake amount, expected value, bankroll.
- Eligibility/skip reason/context dampener.
- Same-opponent/manual-review fields.
- `recommended_action`, `review_status`, `stake_policy`, `operator_action`, `operator_note`.

Missing or inconsistent fields:

- No first-class `fragility_score`, `fragility_bucket`, `fragility_reasons`, `survivability_score`, `survivability_bucket`, or `survivability_reasons`.
- CourtVision Power Rating row-level fields are not consistently present in the inspected `full_market_board` header even though quality summary has Power Rating context.
- No market inflation fields such as `opening_line`, `line_movement`, `consensus_line`, `public_side`, or `market_inflation_warning`.
- No operator risk-mode field carried through the board and Kelly artifacts.
- Empty elite boards should preserve the full elite schema. Current empty 2026-05-08 elite board only has context/review fields and omits core pick columns.
- Schema contract tests should verify both non-empty and empty board headers.

Compatibility concerns:

- Appending new diagnostics is safe; changing existing field meanings or removing fields is not.
- Empty board schema drift can break Kelly, summaries, or operator tooling even when there are no picks.
- Several reports read CSVs permissively and continue on missing columns. That is operator-friendly but can hide schema regressions.
- Any new score should be nullable/defaulted and appended to boards, quality summary, and diagnostics without changing sort order or selection.

## Diagnostics And Quality Summary Audit

Current diagnostics are useful and broad:

- `board_diagnostics_<date>.json` includes board counts, distributions, rejection reasons, guard payloads, player-points admission, final board construction, pipeline mode, and context gate status.
- `market_availability_audit_<date>.json` tracks raw/normalized markets, counts, rejection counts, and elite allowed markets.
- `market_performance_readiness_<date>.json` documents full-market readiness gates and market-level counts/averages.
- `elite_pipeline_audit_summary_<date>.json` tracks totals and board analytics.
- `quality_summary_<date>.json` includes run health, slate provider counts, baseline coverage, candidate funnel, manual-review counts, market coverage, Kelly safety, context safety, high-caution watchlist, risk exposure, board movement, date isolation, Power Rating context, and warnings.

Weak spots:

- Diagnostics are strong for what happened, but not yet organized into a single "why this pick is fragile/survivable" operator narrative.
- Some diagnostics are observation-only but not labeled consistently at the row level.
- Same concepts appear in multiple JSON locations with slightly different names, which increases maintenance burden.

## Test Coverage Audit

Existing coverage found:

- Runtime scoring: `tests/test_scoring_modules.py`, `tests/test_selection_score_ranking.py`, `tests/test_elite_edge_validation.py`, `tests/test_player_points_recalibration.py`, `tests/test_player_points_strong_over_calibration.py`, `tests/test_score_player_markets_low_match_rate.py`.
- Runtime selection: `tests/test_selection_modules.py`, `tests/test_elite_context_gate.py`, `tests/test_cap_enforcement.py`, `tests/test_operator_fixture_smoke.py`, `tests/test_rejection_reason_tracking.py`, stable live-gate tests under `tests/stable`.
- Kelly: `tests/test_kelly.py`, `tests/test_kelly_performance.py`, `tests/test_stale_kelly_reporting.py`, `tests/test_paper_kelly_performance.py`, `tests/test_paper_kelly_simulation.py`.
- Grading/history: `tests/test_grading.py`, `tests/test_grading_runtime.py`, `tests/test_grade_market_shadow_history.py`, `tests/test_history_tracking.py`, `tests/test_backfill_grading.py`, `tests/test_repair_pending_grades.py`.
- Board validation/output schemas: `tests/test_validate_runtime_outputs.py`, `tests/test_prediction_artifact_date_isolation.py`, `tests/test_player_candidate_row_contract.py`, `tests/test_operator_fixture_smoke.py`, `tests/test_quality_summary.py`.
- CourtVision Power Rating and game strength: `tests/test_power_rating.py`, `tests/test_power_rating_integration.py`, `tests/test_power_rating_shadow.py`, `tests/test_backfill_power_rating_context.py`, `tests/test_game_strength_context.py`.
- Injury/context: `tests/test_injury_modules.py`, `tests/test_injury_context_defined.py`, `tests/test_manual_player_context.py`, `tests/test_game_context.py`.
- Odds/data normalization: `tests/test_bdl_odds_adapter.py`, `tests/test_odds_freshness_gate.py`, `tests/test_pipeline_odds_player_name.py`, `tests/test_games_schema_normalization.py`, `tests/test_data_normalization.py`.
- Same-opponent/manual review: `tests/test_same_opponent_rematch.py`, `tests/test_manual_review_decision.py`.

Missing tests before fragility/survivability:

- Empty elite board preserves full schema, including identity, market, scoring, context, rematch, manual review, and future fragility/survivability columns.
- Full-market and elite board append-only schema contract for new diagnostics.
- Fragility diagnostics are computed but do not alter `quality_score`, `confidence`, `edge`, `selection_score`, board membership, ranking, Kelly eligibility, or stake amount.
- Survivability diagnostics are computed but do not alter selection or staking.
- Diagnostics handle missing injury, missing Power Rating, missing opponent, missing line, and missing history without exceptions.
- Quality summary includes fragility/survivability counts and does not fail when boards are empty.
- Daily summary and Kelly report surface manual review plus future diagnostics without changing stake math.
- Regression that low-line role-player over, same-opponent under, and high-caution over populate reasons independently without duplicate reason strings.

## Priority Roadmap

### P0 - Safety And Stability

1. Stabilize empty board schemas. Ensure empty elite/full-market outputs carry the same core headers as populated boards, plus appended diagnostics.
2. Add schema contract tests for board outputs, Kelly outputs, daily summary inputs, and quality summary inputs.
3. Document authoritative stake source: Kelly stake file, not pipeline diagnostic `recommended_bet`.
4. Keep CourtVision Power Rating context observation-only and validate row-level presence/absence consistently.

### P1 - High-Value Next Layer

1. Add diagnostics-only fragility fields:
   - `fragility_score`
   - `fragility_bucket`
   - `fragility_reasons`
   - `fragility_component_json`
2. Add diagnostics-only survivability fields:
   - `survivability_score`
   - `survivability_bucket`
   - `survivability_reasons`
   - `survivability_component_json`
3. Use only already-available fields: minutes, line band, profile bucket, injury impact/deltas, recent support, context caution, defense signal, Power Rating context if present, same-opponent warning, market trust, and calibration guard fields.
4. Surface summary counts in quality summary, daily summary, elite decision report, top plays report, and Kelly report as reporting context only.
5. Add history attribution reports comparing fragility/survivability buckets against graded outcomes before any gate is proposed.

### P2 - Useful But Not Urgent

1. Promote opponent defensive resistance into a clean row-level diagnostic summary.
2. Add bankroll/risk-mode reporting labels that do not affect thresholds yet.
3. Expand same-opponent diagnostics beyond player-points under only, after historical samples are reviewed.
4. Wire correlation/parlay reporting into a clearly separate shadow report.
5. Add line-movement storage if reliable opening/current/closing line data becomes available.

### P3 - Defer

1. Public over bias controls until public/handle/split data exists.
2. Live betting context until current period/clock/score data is reliable.
3. Automatic fragility gating, survivability ranking boosts, Kelly stake dampening, or pick removal.
4. Broad migration away from `courtvision_ai.py` until daily parity is protected by stronger artifact contract tests.

## Recommended Next Codex Prompt

```text
Add diagnostics-only Fragility + Survivability layer to CourtVision.

Requirements:
1. Reporting/context only.
2. Do not change model projections, candidate scoring, quality_score, confidence, edge, selection_score, elite gates, Kelly math, stake sizing, pick ranking, or board membership.
3. Add row-level fields to full_market_board and elite_board:
   - fragility_score
   - fragility_bucket
   - fragility_reasons
   - fragility_component_json
   - survivability_score
   - survivability_bucket
   - survivability_reasons
   - survivability_component_json
4. Use only existing fields already present in runtime rows:
   - minutes_avg
   - player_profile_bucket
   - player_points_line_band
   - injury_impact_score
   - injury_projection_delta
   - injury_confidence_delta
   - player_points_recent_form_ratio
   - player_points_injury_independent_support
   - context_caution_level
   - context_pick_alignment
   - defense_context_signal
   - blowout_risk / expected_competitiveness if available
   - same_opponent_under_warning
   - blocked_by_elite_points_risk_guard
   - blocked_by_player_points_strong_over_calibration
   - player_points_realism_dampened
   - market_trust_weight
5. Append fields without removing or reordering existing columns.
6. Ensure empty elite_board and full_market_board artifacts preserve stable schemas including new fields.
7. Surface summary counts in:
   - daily_summary
   - quality_summary JSON
   - elite_decision_report if present
   - top plays/operator text report if present
   - Kelly report/log as context only
8. Add tests proving:
   - diagnostics populate for fragile player_points over rows
   - diagnostics populate for resilient/survivable rows
   - missing optional inputs do not crash and produce safe defaults
   - quality_score, confidence, edge, selection_score, board membership, Kelly eligibility, and stake_amount are unchanged
   - empty board schemas include all core and new diagnostic fields
9. Run:
   py -3.13 -m pytest -k "fragility or survivability or quality_summary or daily_summary or kelly or schema or elite or full_market" -q
   py -3.13 -m pytest

Return:
- files changed
- sample row before/after
- proof no scoring/selection/Kelly values changed
- validation results
```

