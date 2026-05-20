# CourtVision Candidate Funnel Map

Audit date: 2026-05-19

Scope: source inspection only. No daily runtime, slate regeneration, or production code changes were performed.

This map documents the current candidate funnel from provider odds to final elite board and Kelly staking. It is intentionally descriptive, not prescriptive. The goal is to make the current behavior traceable before any betting-logic refactor.

## Current Funnel

1. Raw provider odds are fetched in `CourtVisionAI.predict` by `client.get_odds(prediction_date, game_ids=game_ids)`. The fetched frame is stored as `odds_raw`, logged, and then passed through `CourtVisionAI._normalize_odds`.

2. Odds normalization has two paths. If `odds_raw` already has `unresolved_reason`, it is treated as BallDontLie-adapter output and `filter_valid_odds` keeps only rows with no unresolved reason. Otherwise `normalize_odds_frame` maps `raw_market_name` to `market_type`, fills `raw_stat_key` and `market_alias`, coerces line and odds fields, and keeps rows with a mapped market.

3. BallDontLie player props are normalized by `normalize_bdl_player_props`. It expands one provider over/under row into one row per actionable side, preserving `raw_market_name`, `raw_prop_type`, `raw_market_type`, `market_type`, `selection`, `line`, `odds`, `vendor`, `game_id`, team identity fields, `line_source`, `updated_at`, and `unresolved_reason`.

4. `PredictionPipeline.run` builds injury context before candidate construction. `InjuryEngine.build_context` creates player and team injury dictionaries from normalized injuries plus player baselines and active slate teams.

5. `PredictionPipeline._build_candidate_universe` builds a game-status lookup, creates a `CanonicalPlayerIdentityResolver`, defines nested candidate callbacks, and delegates player market construction to `score_player_markets`.

6. `score_player_markets` normalizes odds rows again for matching, removes milestone rows from its working odds, matches baselines to odds by `player_id` first and normalized name/team second, and materializes one candidate per player, market, and side. Partial-fill candidates are attempted for missing core player markets when enabled.

7. `build_candidate_row` creates the row-level projection, confidence, line, odds, edge, side-aware edge, source flags, game-status fields, recalibration shadow fields, injury metadata, quality score, selection score, and diagnostic pre-Kelly fields.

8. Projection support is checked in `PredictionPipeline._build_candidate_universe._get_projection_support_status`. Unsupported or invalid projections return `None`, which `score_player_markets` converts into rejection diagnostics.

9. Injury context is applied inside `build_candidate_row` before final edge and score metadata are attached. Own injury reduces projection and confidence; teammate and opponent injury contexts can adjust projection and confidence; player-points confidence uplift is dampened when injury uplift is already present.

10. Edge is calculated as `projection - line`. For under rows, `side_edge` flips sign so row qualification rewards only the side being bet. `score_candidate_fn` requires positive side edge, minimum edge, minimum confidence, full-market readiness gates for selected non-points markets, and enabled-mode recalibration acceptance.

11. Candidate scoring is applied through `CandidateScoringPolicy.apply_scoring_metadata`, which delegates to `compute_selection_score`. It calculates adjusted edge percentage, adjusted confidence, tier weight, historical multiplier, volatility and realism penalties, under bias, elite points ranking penalty, quality score, and selection score.

12. Team-market candidates are also built inside `_build_candidate_universe`, but the active operator market gate currently excludes `moneyline` and `team_total` from operator boards.

13. Player identity is annotated after accepted candidates are built. `CanonicalPlayerIdentityResolver.annotate_records` adds canonical player/team fields and identity conflict fields. Then `mark_identity_quarantine_fields` sets `identity_quarantine` rejection fields for stale or outside-team identity evidence.

14. Game status and odds freshness diagnostics are counted inside `_build_candidate_universe`, but those checks are not the first hard candidate drop there. The active hard elite path calls them later through `runtime_audit.get_elite_rejection_reason`.

15. `build_operator_boards` receives the wide candidate DataFrame and performs the first shared operator-board gate. It requires live market source, non-synthetic line, live qualification origin, non-milestone market, active operator market, no identity quarantine, and unique betting identity after dedupe.

16. The full-market board is selected by nested `select_top_per_market`, ranking live candidates by `selection_score` or `quality_score` and taking `per_market_limit`.

17. The elite board is selected by nested `select_elite_board`. It sorts by `selection_score`, applies elite market-mode filtering, checks elite quality/confidence admission, calls `runtime_audit.get_elite_rejection_reason`, applies team/game exposure caps, and takes the final elite size. Current board-limit ambiguity is documented in `docs/ELITE_BOARD_LIMIT_AUDIT.md`.

18. After the package pipeline returns, `CourtVisionAI.predict` attaches manual player context and passive game context to qualified, elite, and full-market DataFrames. Then `_apply_elite_context_safety_gate` removes context-blocked elite rows and attempts player-points backfill from the full-market pool.

19. `_write_cli_outputs` applies `BoardAuditPolicy`, manual review annotations, same-opponent warnings, fragility diagnostics, schema alignment, final game cap validation, and writes protected operator board CSVs.

20. `scripts/run_kelly_stakes.py` reads only `outputs/runtime/operator/elite_board_<DATE>.csv`. It then applies stake-facing eligibility checks, computes Kelly fractions, applies the daily exposure cap, applies the medium-neutral-over dampener, and writes `kelly_stakes_<DATE>.csv`.

## Stage Map

| Stage | File/function | Input columns | Output columns | Drop/reject reason | Risk |
|---|---|---|---|---|---|
| Raw provider odds fetch | `courtvision_ai.py:CourtVisionAI.predict` | `prediction_date`, `game_ids` | `odds_raw` provider frame | None here; zero rows produce diagnostics | High: source of every market row |
| BallDontLie prop adaptation | `courtvision/data/bdl_odds_adapter.py:normalize_bdl_player_props` | `player_id`, player fields, `prop_type`, `market.type`, `market.over_odds`, `market.under_odds`, `market.odds`, `line_value`, `game_id`, team fields, `updated_at` | `player_id`, `player_name`, `raw_market_name`, `raw_prop_type`, `raw_market_type`, `market_type`, `selection`, `line`, `odds`, `vendor`, `game_id`, team identity fields, `line_source`, `unresolved_reason`, `updated_at` | `missing_player_name`, `missing_line`, `missing_market_type`, `missing_odds` in `unresolved_reason` | High: invalid rows are removed before candidates |
| Odds valid-row filter | `courtvision/data/bdl_odds_adapter.py:filter_valid_odds`; `courtvision_ai.py:_normalize_odds` | BDL adapter columns, especially `unresolved_reason` | Valid odds frame plus legacy columns `team`, `raw_stat_key`, `market_alias`, `bookmaker`, `over_odds`, `under_odds` | Rows with non-null `unresolved_reason` are dropped | High: hidden upstream-data gate |
| Legacy odds normalization | `courtvision/data/normalization.py:normalize_odds_frame` | `raw_market_name`, `line`, `over_odds`, `under_odds`, `odds`, `player_id`, `player_name`, `team` | `market_type`, `raw_stat_key`, `market_alias`, numeric line/odds fields | Only rows with null `market_type` are dropped | Medium: weaker validation than BDL path |
| Pipeline input telemetry | `courtvision/pipeline/predict_pipeline.py:PredictionPipeline.run` | `games`, `odds`, `player_baselines`, `team_baselines`, `injuries` | Count logs, normalized injury context | None | Low: diagnostics only |
| Injury context build | `courtvision/injuries/injury_engine.py:build_injury_context` | injuries: `player_id`, `player_name`, `team_abbr`, `team_id`, `status`; baselines: stat averages and `min_avg`; active teams | `injury_context.players`, `injury_context.teams`, `active_injuries`, metadata | Injury rows can be skipped for missing status/player identity; team-missing rows get `missing_team_identity` diagnostics | High: changes projection and confidence |
| Baseline and odds matching | `courtvision/data/candidates.py:score_player_markets` | baselines: `player_id`, `player_name`, `team_abbr`; odds: `player_id`, `player_name`, `team_abbr`, `market`, `selection` | Matched player-market row groups and coverage diagnostics | `player_ruled_out_or_doubtful`, coverage diagnostics, unsupported milestone removed from working odds | High: determines candidate universe |
| Candidate projection | `predict_pipeline.py:_build_candidate_universe.build_candidate_row`; `_compute_projection`; `_compute_combo_projection` | `market_type`, stat averages, `min_avg` | `model_projection`, `projection`, `projection_support_status` | `unsupported_projection_market`, `unsupported_market_type`, `invalid_projection_output` | Critical: bankroll-facing projection source |
| Projection support gate | `predict_pipeline.py:_get_projection_support_status` via `score_player_markets` | `market_type`, projection | Accepted candidate row or rejected diagnostic | `market_not_supported_by_projection`, `unsupported_market_type`, `invalid_projection_output`, `unsupported_projection_market` | Critical: controls supported market set |
| Injury adjustment | `injury_engine.py:apply_player_injury_context` | projection, confidence, player/team injury context | adjusted projection/confidence; `injury_status`, `injury_impact_score`, `team_injury_impact`, `opponent_injury_impact`, injury deltas, player-points injury support fields | None directly; own injury can reduce projection/confidence enough for later gates | High: scoring input mutation |
| Edge calculation | `predict_pipeline.py:build_candidate_row` | `projection`, `line`, `selection` | `edge`, `edge_pct`, `side_edge`, `side_edge_pct` | `market_missing_line`, `market_missing_odds` as `pre_rejection_reason` where applicable | Critical: directional selection depends on this |
| Confidence calculation | `predict_pipeline.py:_compute_confidence`; `scoring/confidence.py:compute_confidence` through scoring policy | `min_avg`, market type, edge metadata | `confidence`, adjusted confidence in scoring metadata | Later `market_supported_but_failed_confidence` or `reject_quality_confidence_threshold` | Critical: elite and Kelly threshold input |
| Quality and selection score | `courtvision/scoring/candidate_scoring.py:compute_selection_score` | `edge_abs`, `side_edge_pct`, `sportsbook_line`, `confidence`, `minutes_avg`, `minutes_recent`, penalties/context fields | `quality_score`, `selection_score`, `elite_rank_score`, penalty columns, ranking reason columns | None directly, but used by subsequent gates | Critical: board rank and elite admission |
| Candidate threshold gate | `predict_pipeline.py:_build_candidate_universe.score_candidate_fn` | `side_edge`, `confidence`, `minutes_avg`, `market_type`, recalibration fields | Accepted scored row or rejected diagnostic | `market_gate_minutes_lt_<N>`, `market_gate_confidence_lt_<N>`, `reject_negative_edge_direction`, `market_supported_but_failed_quality`, `market_supported_but_failed_confidence`, `edge_and_confidence_below_threshold`, recalibration reason | Critical: first hard quality gate |
| Identity annotation | `context/player_identity.py:CanonicalPlayerIdentityResolver.annotate_records` | candidate team fields, baseline team fields, provider team fields, game teams | `canonical_player_id`, `canonical_player_name`, `canonical_team_abbr`, `player_identity_status`, conflict reason/details | Diagnostic rows with `player_identity_validation`; candidate rows carry conflict fields | Critical: identity errors can invalidate bets |
| Identity quarantine | `context/game_context.py:mark_identity_quarantine_fields`; `operator_boards.py:build_operator_boards` | identity conflict fields, game team fields, source team fields | `identity_team_conflict`, `identity_quarantine_reason`, `selection_rejection_reason`, `rejection_reason`, `recommended_action` | `identity_quarantine` with `outside_team_identity`, `stale_team_identity`, `game_not_bettable` | Critical: stake safety gate |
| Full-market readiness gates | `predict_pipeline.py:FULL_MARKET_READINESS_GATES`; `score_candidate_fn`; `_write_market_performance_readiness` | `market_type`, `minutes_avg`, `confidence` | Accepted full-market eligible rows; readiness JSON | `market_gate_minutes_lt_<N>`, `market_gate_confidence_lt_<N>` | High: non-points market readiness |
| Live/source/synthetic gate | `selection/operator_boards.py:build_operator_boards` | `qualification_reason`, `is_live_market`, `synthetic_line`, `line_source`, `source_lane` | `live_candidates_before_dedupe_df`, rejection trace counts | `selection_not_live_market_eligible`, `selection_live_gate_missing_qualification_reason`, `selection_live_gate_filtered` | Critical: prevents synthetic/stat-only stake leakage |
| Active operator market gate | `selection/operator_boards.py:_active_operator_market_mask` | `market_type` | Live active operator candidates only | `unsupported_active_operator_market` | Critical: final board market allowlist |
| Milestone gate | `data/candidates.py:_is_milestone_market_row`; `operator_boards.py:_non_milestone_mask`; `run_kelly_stakes.py:_build_stake_row` | `raw_market_type`, `selection` | Non-milestone candidates and boards | `unsupported_milestone_market` | High: repeated safeguard |
| Duplicate betting identity dedupe | `selection/operator_boards.py:_dedupe_betting_identities` | `player_id` or name, `game_id`, `market_type`, `selection`, `line`, scoring/context fields | Deduped live candidates; duplicate summary | `duplicate_betting_identity` | Critical: avoids duplicate same bet |
| Elite market-mode gate | `predict_pipeline.py:_resolve_elite_allowed_markets`; nested `select_elite_board` | `market_type`, `ELITE_MARKET_MODE`, `ELITE_ALLOWED_MARKETS` | Elite-admissible market subset | `market_filtered_by_elite_policy` | Critical: default elite is points-only |
| Elite admission gate | `predict_pipeline.py:select_elite_board`; `runtime_audit.py:get_elite_rejection_reason` | `is_elite`, `quality_score`, `confidence`, game status, odds freshness, context, line, odds, direction | `elite_df` before caps; telemetry CSV/JSON | `reject_quality_confidence_threshold`, runtime audit elite reject reasons | Critical: main stake-facing gate |
| Exposure caps | `predict_pipeline.py:select_elite_board`; later `courtvision_ai.py:_apply_elite_context_safety_gate`; final writer cap assert | `team_abbr`, `game_id` or normalized game key, sorted elite rows | Capped elite board | `reject_team_exposure_cap`, `reject_game_exposure_cap`; final writer raises on game cap violation | Critical: bankroll concentration control |
| Manual context attachment | `courtvision_ai.py:_attach_manual_player_context` | qualified/elite/full-market rows plus `manual_player_context` rows | manual review fields, action fields, diagnostics | No direct drop here; can force Kelly hold later | High: stake routing changes |
| Game context attachment | `courtvision_ai.py:_attach_game_context`; `context/game_context.py:apply_game_context` | board rows, games, team baselines, odds, schedule games | opponent, home/away, pace/defense/rest/playoff signals, `context_pick_alignment`, `context_caution_level`, suppression and quarantine fields | `team_not_in_game_context`, `stale_team_not_in_game`, identity quarantine fields | Critical: applied after initial package selection |
| Late elite context safety | `courtvision_ai.py:_apply_elite_context_safety_gate` | elite and full-market rows with context fields | final context-safe elite rows, backfill summary, `kelly_projected_skip_reason`, `final_elite_rejection_reason` | `elite_reject_context_high_caution_over`, `elite_reject_game_context_suppressed`, `elite_reject_player_points_strong_over_calibration` | Critical: late stake-facing mutation |
| Board audit and final writer | `runtime_audit.py:BoardAuditPolicy`; `courtvision_ai.py:_write_cli_outputs` | final boards and diagnostics | audited operator CSVs, board diagnostics, elite decision report | Writer raises if final game cap exceeds 4 | Critical: last pre-Kelly board surface |
| Kelly eligibility | `scripts/run_kelly_stakes.py:_build_stake_row`; `main` | elite CSV: `odds`, `confidence`, `side_edge_pct` or `edge_pct` or `edge`, context/review/identity fields | `kelly_eligible`, `eligible`, `skip_reason`, `stake_fraction`, `stake_amount`, `expected_value`, exposure summary | Kelly skip reasons listed below | Critical: final stake-facing sizing |

## Gate And Reason Code Map

| Gate/reason code | Where created | Where consumed | Stake-facing? | Notes |
|---|---|---|---|---|
| `missing_player_name`, `missing_line`, `missing_market_type`, `missing_odds` | `bdl_odds_adapter._unresolved_reason` | `filter_valid_odds` drops rows before pipeline | Yes | Removes odds rows before candidates exist. |
| `player_ruled_out_or_doubtful` | `score_player_markets` via inactive callback | Rejected diagnostics | Yes | Prevents inactive player candidates. |
| `unsupported_milestone_market` | `score_player_markets`, `operator_boards`, `run_kelly_stakes` | Rejected diagnostics, selection trace, Kelly skip | Yes | Repeated in three layers. |
| `unsupported_projection_market` | `build_candidate_row`/`score_player_markets` | Rejected diagnostics, readiness reports | Yes | Controls market promotion readiness. |
| `unsupported_market_type` | `_get_projection_support_status`, `score_player_markets` | Rejected diagnostics | Yes | Generic unsupported provider market. |
| `market_not_supported_by_projection` | `_get_projection_support_status`, team market branch | Rejected diagnostics | Yes | Used for moneyline/team_total or projection gaps. |
| `invalid_projection_output` | `_get_projection_support_status`, player points missing projection | Rejected diagnostics | Yes | Covers missing or zero projection placeholders. |
| `projection_missing_for_market` | Team-market branch in `_build_candidate_universe` | Rejected diagnostics | No currently | Active operator market gate excludes team markets. |
| `market_missing_line`, `market_missing_odds` | `build_candidate_row`, team-market branch | `pre_rejection_reason`, rejected diagnostics | Yes | Partial-fill rows can receive synthetic defaults but are later live-gated out. |
| `market_gate_minutes_lt_<N>` | `score_candidate_fn` | Rejected diagnostics/readiness | Yes for full-market eligibility | Generated dynamically from `FULL_MARKET_READINESS_GATES`. |
| `market_gate_confidence_lt_<N>` | `score_candidate_fn` | Rejected diagnostics/readiness | Yes for full-market eligibility | Generated dynamically from `FULL_MARKET_READINESS_GATES`. |
| `reject_negative_edge_direction` | `score_candidate_fn`; `runtime_audit.get_elite_rejection_reason`; legacy runtime selection | Rejected diagnostics and elite telemetry | Yes | Side-aware candidate gate plus elite tripwire. |
| `market_supported_but_failed_quality` | `score_player_markets`, team-market branch | Rejected diagnostics | Yes | Usually side edge or absolute edge below threshold. |
| `market_supported_but_failed_confidence` | `score_player_markets`, team-market branch | Rejected diagnostics | Yes | Candidate confidence below threshold. |
| `edge_and_confidence_below_threshold` | `score_player_markets` | Rejected diagnostics | Yes | Fallback reason when score callback returns `None`. |
| Recalibration rejection reason | `recalibrate_player_points` result copied by `build_candidate_row`; enforced in enabled mode | `score_candidate_fn` | Yes when recalibration enabled | Dynamic reason string. |
| `player_identity_validation` | `CanonicalPlayerIdentityResolver._diagnostic_row` | Rejected diagnostics | Yes as diagnostic; not the same as quarantine | Source identity conflict rows. |
| `player_id_team_conflict` | `CanonicalPlayerIdentityResolver` | Conflict details and summary | Yes if it triggers quarantine | Identity-level reason, not final selection reason. |
| `baseline_provider_team_conflict` | `CanonicalPlayerIdentityResolver` | Conflict details and summary | Yes if it triggers quarantine | Indicates stale baseline/provider team disagreement. |
| `player_team_not_in_active_game` | `CanonicalPlayerIdentityResolver` | Conflict details and summary | Yes if it triggers quarantine | Provider team not in the active game. |
| `identity_quarantine` | `mark_identity_quarantine_fields`; `operator_boards`; Kelly | Selection trace, diagnostics, Kelly skip | Yes | Final quarantine rejection reason. |
| `outside_team_identity` | `is_identity_quarantined` | Quarantine counts and Kelly operator note | Yes | Narrow quarantine cause. |
| `stale_team_identity` | `is_identity_quarantined` | Quarantine counts and Kelly operator note | Yes | Narrow quarantine cause. |
| `game_not_bettable` | `is_identity_quarantined`; Kelly projected skip | Quarantine counts or Kelly skip | Yes | Name overlaps with game-status Kelly skip. |
| `selection_not_live_market_eligible` | `operator_boards.build_operator_boards` | Selection trace | Yes | Blocks rows that fail live diagnostic and origin checks. |
| `selection_live_gate_missing_qualification_reason` | `operator_boards.build_operator_boards` | Selection trace | Yes | Live flags exist but no live qualification reason. |
| `selection_live_gate_filtered` | `operator_boards.build_operator_boards` | Selection trace | Yes | Live diagnostic flag passes but origin text does not. |
| `unsupported_active_operator_market` | `operator_boards.build_operator_boards` | Selection trace, summaries | Yes | Active operator allowlist excludes markets outside current operator scope. |
| `duplicate_betting_identity` | `operator_boards._dedupe_betting_identities` | Selection trace, diagnostics | Yes | Drops lower-ranked duplicate same bet. |
| `selection_not_selected_by_board_selector` | `operator_boards.build_operator_boards` | Selection trace sample | No direct stake | Qualified live row lost final board selection. |
| `market_filtered_by_elite_policy` | `predict_pipeline.select_elite_board` | Elite telemetry | Yes | Default `ELITE_MARKET_MODE=points_only` blocks non-points elite. |
| `reject_quality_confidence_threshold` | `predict_pipeline.select_elite_board` | Elite telemetry | Yes | Basic elite admission failure. |
| `total_candidates`, `passed_to_elite` | `EliteTelemetry` | Elite telemetry CSV/JSON | No direct stake | Telemetry pseudo-reasons. |
| `elite_reject_game_final` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry; final board exclusion | Yes | Mapped from `game_status_ineligibility_reason`. |
| `elite_reject_game_in_progress` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Mapped from game status. |
| `elite_reject_game_locked` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Within lock buffer. |
| `elite_reject_game_postponed` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Postponed/cancelled. |
| `elite_reject_game_not_bettable` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Unknown/unparseable game status. |
| `elite_reject_odds_stale` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Mapped from `odds_stale_ineligibility_reason`. |
| `reject_injury_flag` | `runtime_audit.get_elite_rejection_reason`; legacy runtime selection | Elite telemetry | Yes | Flag-based hard reject if present. |
| `reject_minutes_volatility` | `runtime_audit.get_elite_rejection_reason`; legacy runtime selection | Elite telemetry | Yes | Flag-based hard reject if present. |
| `reject_confidence_below_threshold` | `runtime_audit.get_elite_rejection_reason`; legacy runtime selection | Elite telemetry | Yes | Flag-based hard reject if present. |
| `reject_projection_realism` | `runtime_audit.get_elite_rejection_reason`; legacy runtime selection | Elite telemetry | Yes | Flag-based hard reject if present. |
| `reject_exposure_limit` | `runtime_audit.get_elite_rejection_reason`; legacy runtime selection | Elite telemetry | Yes | Flag-based hard reject if present. |
| `elite_reject_game_context_suppressed` | `elite_context_rejection_reason`; `_apply_elite_context_safety_gate` | Elite telemetry, final board construction, projected Kelly annotations | Yes | Also catches team-not-in-game context. |
| `elite_reject_context_high_caution_over` | `elite_context_rejection_reason`; `_apply_elite_context_safety_gate` | Elite telemetry, final board construction, projected Kelly annotations | Yes | Blocks high-caution conflicted overs. |
| `reject_unrealistic_line` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Player line sanity gate. |
| `reject_heavy_favorite_odds` | `runtime_audit.get_elite_rejection_reason` | Elite telemetry | Yes | Blocks odds worse than -400. |
| `player_points_strong_over_calibration_guard` | `runtime_selection.player_points_strong_over_calibration_reason` | Scoring policy, board audit, runtime audit | Yes | Raw guard reason before elite/Kelly-specific mapping. |
| `elite_reject_player_points_strong_over_calibration` | `runtime_audit.get_elite_rejection_reason`; `_apply_elite_context_safety_gate` | Elite telemetry, final board construction | Yes | Elite-facing mapped reason. |
| `injury_driven_low_line_over` | `runtime_selection.elite_points_risk_guard_reason` | Scoring policy and audit guard fields | Yes | Player-points low-line over guard. |
| `weak_role_under` | `runtime_selection.elite_points_risk_guard_reason` | Scoring policy and audit guard fields | Yes | Player-points under guard. |
| `weak_secondary_under` | `runtime_selection.elite_points_risk_guard_reason` | Scoring policy and audit guard fields | Yes | Player-points secondary-under guard. |
| `reject_team_exposure_cap` | `predict_pipeline.select_elite_board` | Mutated on non-selected admitted rows, trace counts | Yes | Team cap source is `EliteThresholds` unless config override exists. |
| `reject_game_exposure_cap` | `predict_pipeline.select_elite_board` | Mutated on non-selected admitted rows, trace counts | Yes | Game cap also asserted in final writer. |
| `edge_containment_hold_for_review` | `run_kelly_stakes._check_edge_containment_hold` | Kelly skip and operator action | Yes | Evaluated before market lock. |
| `kelly_points_only_market_lock` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Kelly is player-points locked. |
| `context_high_caution_over` | `projected_kelly_skip_reason`; `run_kelly_stakes._build_stake_row` | Kelly projected annotations and Kelly skip | Yes | Kelly-facing version of high-caution over gate. |
| `player_points_strong_over_calibration` | `projected_kelly_skip_reason` | Projected Kelly annotations | Yes if consumed | `run_kelly_stakes.py` does not currently call this helper directly. |
| `game_final`, `game_in_progress`, `game_locked`, `game_postponed`, `game_not_bettable` | `projected_kelly_skip_reason` | Projected Kelly annotations | Yes if consumed | Kelly script relies on elite board filtering rather than re-calling this helper. |
| `odds_stale` | `projected_kelly_skip_reason`; `odds_stale_ineligibility_reason` | Projected Kelly annotations | Yes if consumed | Kelly script does not directly re-run stale-odds check. |
| `missing_or_invalid_odds` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Final stake input validation. |
| `non_positive_decimal_odds` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Final stake input validation. |
| `missing_confidence` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Final stake input validation. |
| `missing_<edge_col>` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Dynamic skip reason based on selected edge column. |
| `confidence_below_min(<threshold>)` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Final Kelly confidence floor from `courtvision.betting.kelly`. |
| `non_positive_edge` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Final Kelly side edge validation. |
| `kelly_returned_zero` | `run_kelly_stakes._build_stake_row` | Kelly skip | Yes | Sizing model returns no stake. |
| `medium_neutral_over_dampener` | `run_kelly_stakes._medium_neutral_over_dampener` | Kelly stake dampener fields | Yes | Reduces stake, does not reject. |

## Duplicated Or Overlapping Logic

1. `runtime_audit.py` vs `runtime_selection.py`

   `runtime_audit.get_elite_rejection_reason` is the active package-pipeline elite rejection function imported by `predict_pipeline.select_elite_board`. It includes game status, odds freshness, context safety, directional edge, unrealistic line, heavy favorite odds, and strong-over calibration. `runtime_selection.get_elite_rejection_reason` still exists with a narrower flag and player-points direction check. This is drift-prone because both functions share the same name and overlapping constants.

2. Nested selection functions inside `predict_pipeline.py`

   `select_elite_board` and `select_top_per_market` are nested inside `PredictionPipeline.run`. They own elite market-mode filtering, quality/confidence admission, telemetry, exposure caps, top-N selection, and full-market ranking. Their behavior is hard to import directly into golden tests without running the whole pipeline.

3. Scoring facade vs scoring package

   `courtvision/scoring/candidate_scoring.py` is the newer scoring package. `courtvision/runtime_scoring.py` is a compatibility facade that re-implements much of scoring metadata and duplicates `is_elite_candidate` logic. `courtvision_ai.py` still delegates through `BoardScoringPolicy`, while the package pipeline uses `CandidateScoringPolicy`.

4. Elite admission vs late context safety

   The package pipeline applies `runtime_audit.get_elite_rejection_reason` before manual/game context is attached by `CourtVisionAI.predict`. After context attachment, `_apply_elite_context_safety_gate` re-evaluates context and can remove/backfill elite rows. This is intentional safety behavior, but it means the "final elite board" is not only the package-pipeline elite output.

5. Live/source/synthetic checks

   `operator_boards.build_operator_boards` has the current unified live gate. Similar live checks exist in `BoardVolumePolicy._is_live_board_candidate`, `CandidateScoringPolicy.is_elite_candidate`, `BoardScoringPolicy.is_elite_candidate`, and `CourtVisionAI._live_market_only`.

6. Milestone gates

   Milestone rows are filtered in `score_player_markets`, `operator_boards`, and `run_kelly_stakes`. This is safe as defense in depth, but reason ownership is unclear.

7. Identity quarantine ownership

   Identity conflicts are produced in `player_identity.py`, converted into quarantine fields in `game_context.py`, filtered in `operator_boards.py`, summarized in `runtime_audit.py`, and rechecked in `run_kelly_stakes.py`. The final reason is stable (`identity_quarantine`), but the narrower cause codes live in context helpers.

8. Strong-over calibration guard

   The raw guard lives in `runtime_selection.player_points_strong_over_calibration_reason`. It is consumed by scoring policy, runtime audit, board audit, CourtVisionAI late backfill, and projected Kelly annotations. Kelly's actual script has high-caution and points-only checks but does not directly call `projected_kelly_skip_reason`, so the projected skip taxonomy is not identical to actual Kelly skip taxonomy.

9. Kelly-like metadata before Kelly

   `build_candidate_row` computes `stake_fraction` and `recommended_bet` using `compute_kelly_fraction`, but the real stake-facing calculation happens later in `scripts/run_kelly_stakes.py`. These early fields are diagnostic/pre-Kelly and can confuse traceability.

10. Kelly skip reasons vs elite rejection reasons

   `runtime_audit.projected_kelly_skip_reason` maps game and odds gates to Kelly-style strings. `scripts/run_kelly_stakes.py` uses its own skip sequence and does not re-run game status or odds freshness checks. This is probably acceptable because Kelly reads an already elite-filtered board, but it is a traceability gap.

## Safest Future Extraction Plan

### Phase C1: Extract Reason-Code Constants Only

- Create a constants-only module, for example `courtvision/selection/reason_codes.py`.
- Move string constants without changing imports semantically. Start with aliases, not behavior changes.
- Keep existing names exported from old modules for compatibility.
- Add a small test that the old and new constants match exactly.
- Do not touch thresholds, ordering, or gate predicates.

### Phase C2: Centralize Gate Evaluation Without Behavior Change

- Introduce a pure evaluator that returns ordered decisions for one row: live/source, identity, active market, game status, odds freshness, context, elite hard guards, exposure placeholders, and Kelly projected skips.
- Initially call existing functions internally and preserve exact reason strings.
- Add trace output comparing old path result and new evaluator result in tests only.
- Do not replace production selectors until fixtures prove parity.

### Phase C3: Move Nested Selectors Out Of `predict_pipeline.py`

- Extract `select_elite_board` and `select_top_per_market` into a named module with injected thresholds, allowed markets, telemetry, and cap settings.
- Preserve the function signatures used by `build_operator_boards`.
- Keep `PredictionPipeline.run` as orchestration only.
- Add tests that the extracted selectors produce byte-equivalent selected identities and reason traces on fixed fixtures.

### Phase C4: Add Golden Fixture Tests For Candidate Funnel Invariants

Minimum invariant fixtures:

- BDL over/under odds expansion keeps both sides and drops unresolved rows only through `unresolved_reason`.
- Baseline matching prefers `player_id` and rejects stale team identity.
- Side-aware edge rejects wrong-direction over/under rows.
- Full-market readiness gates block low minutes/confidence for promoted markets.
- Live/source/synthetic gate blocks partial-fill and stat-only rows from operator boards.
- Active operator market gate blocks team markets and unsupported markets.
- Duplicate betting identity keeps the row with better context/live/source/rank.
- Elite market-mode default admits only `player_points`.
- Game status and stale odds never reach elite.
- Context high-caution conflicted overs are removed after context attachment.
- Final elite board respects team cap, game cap, and board limit.
- Kelly reads only elite rows and remains player-points locked.

## First Refactor Target

The safest first refactor target is reason-code constants, not gate logic. The current behavior has multiple overlapping gate owners, but the most immediate drift risk is duplicated string literals with similar meanings. A constants-only extraction gives future diffs a stable vocabulary while keeping bankroll-facing behavior untouched.
