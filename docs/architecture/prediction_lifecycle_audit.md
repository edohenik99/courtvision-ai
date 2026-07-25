# CourtVision Prediction Lifecycle and Historical Integrity Audit

**Phase:** 1 — audit and implementation plan only
**Audit date:** 2026-07-25
**Repository:** `C:\dev\Sport_Project1`
**Scope:** read-only inspection of prediction creation, persistence, market evidence, game locks, availability, grading, settlement, automation, and research pipelines

## 1. Executive summary

CourtVision does not currently use DuckDB, SQLite, or another relational database for prediction lifecycle persistence. No database file, SQL DDL/DML, database path, relational table, or relational view was found in the inspected repository. The production-facing NBA path persists CSV, JSON, text, and log files. DuckDB is not declared in `requirements.txt` and could not be imported from the active Python, repository `.venv`, Python 3.13, or Python 3.11 interpreters. Its installed version is therefore **not installed in the checked environments**; the state of any unreferenced external interpreter is unverified.

The accepted runtime decision makes `run_today.bat -> run_today.ps1 -> courtvision_ai.py / CourtVisionAI` the canonical operator path and makes `CourtVisionPro` and all MLB/NBA research pipelines non-canonical or research-only (`docs/ADR_001_CANONICAL_RUNTIME.md:9-13`, `:20-24`, `:33-37`, `:88-123`). The audit therefore distinguishes:

1. the canonical production-facing NBA path;
2. compatibility/non-canonical NBA paths;
3. NBA research-only immutable evidence;
4. MLB live-data tooling and MLB research-only evidence.

The canonical path has useful safeguards—date-to-filename validation, an existing-board guard, a closed-slate guard, terminal-grade preservation, and pending-only grading—but it does **not** have an authoritative persisted prediction lifecycle, publication record, lock event, immutable source snapshot, transaction, or append-only settlement ledger.

The highest-impact findings are:

- **Scheduled-time locking is not effectively enforced by the canonical selection calls.** `game_status_ineligibility_reason()` says the default `now` is current time, but `_is_before_lock_buffer()` returns `True` (bettable) when `now is None` (`courtvision/runtime_gates.py:91-104`, `:107-134`). Canonical callers invoke the gate without `now` (`courtvision/pipeline/predict_pipeline.py:495`, `:1555-1563`) and even diagnostics call `_is_before_lock_buffer(..., None, 10)` (`:1590-1597`). Live/final/postponed provider statuses still block, but a scheduled game remains eligible inside the ten-minute buffer unless its status changes.
- **Prediction history can be appended before the protected board write is rejected.** `CourtVisionAI.predict()` appends to `prediction_history.csv` (`courtvision_ai.py:4349-4351`, `:4712-4716`), while the protected board guard is applied later during `_write_cli_outputs()` (`:8895-8897`, `:9146-9152`). A rerun can therefore change history even if the dated board already exists.
- **Canonical history is not immutable.** `_append_history()` reads, concatenates, and rewrites a whole CSV without a stable prediction ID or deduplication (`courtvision_ai.py:7940-7974`). `pick_history.csv` and `market_shadow_history.csv` are also reconstructed and rewritten (`scripts/history_tracking.py:957-1011`, `:1173-1262`).
- **The primary large history has no creation/publication/run timestamp.** `prediction_history.csv` contains `prediction_date` and provider/game timestamps but no row creation or publication time. A read-only audit found 9,067 rows and 1,611 duplicate composite-key groups (7,897 rows) using date/player/market/selection/line. It is not possible to reliably determine which duplicate was first published.
- **Pending market history can be replaced by later runs.** Same-date market shadow persistence removes and replaces the date. Only terminally graded rows preserve selected entry/grade values (`scripts/history_tracking.py:384-425`, `:957-1011`).
- **Raw model inputs and model artifacts are replaceable.** `CourtVisionAI.fit()` writes baseline/calibration inputs directly to fixed CSV paths (`courtvision_ai.py:3987-4000`). Canonical provider and injury inputs are normalized in memory; the injury diagnostic retains only small samples and date-named diagnostics are overwritten (`courtvision_ai.py:4079-4119`, `:6253-6286`).
- **Grading and void terminology is inconsistent.** Canonical history uses `pending/hit/miss/push/void/unsupported`; `auto_grade()` uses `win/loss/push/unresolved`; the evidence CSV uses `win/loss/push/void/pending`; MLB and NBA research use still richer states. Historical unresolved rows not classified as unsupported are converted to `void` after the prediction date (`scripts/history_tracking.py:292-300`), which conflates “no settlement evidence” with a bookmaker void.
- **Current evidence CSVs are controlled mutable records, not append-only events.** The closing-line and result updaters only fill blank cells and rewrite atomically, which is a valuable idempotency control, but they mutate the original evidence row and do not record who changed it or the prior value (`scripts/update_evidence_closing_lines.py:183-244`; `scripts/update_evidence_results.py:190-250`).
- **Canonical CSV persistence has no single-writer enforcement.** Whole-file reads and rewrites are not protected by a file lock. Concurrent prediction, grading, repair, or automation processes can lose updates or publish partially related artifacts.
- **A non-canonical grading path can reconstruct a missing dated pick file from a current board after the event.** `courtvision/grading/grade_props.py:178-265` is not the accepted production path, but it remains an integration hazard if invoked.

The repository also contains strong, reusable precedents:

- NBA research evidence uses a writer lock, immutable per-run JSONL segments, hashes, completion markers, and atomic directory rename (`courtvision/sports/nba/player_points_evidence.py:281-295`, `:308-519`, `:692`; schema summary at `:288-289`).
- NBA research settlement and closing evidence preserve distinct observations, conflict/manual-review states, and append-only segments (`courtvision/sports/nba/player_points_settlement.py:31-65`, `:1010-1029`; `courtvision/sports/nba/player_points_closing.py:1-5`, `:44-87`, `:117-168`).
- MLB research creates immutable timestamped prediction outputs and appends separate prediction and settlement records under a file lock (`courtvision/sports/mlb/training/hr_research_baseline.py:256-335`, `:2775-2791`, `:2801-2844`, `:2847-2882`, `:3165-3177`, `:3252-3299`).

These protections are **research-only** and must not be represented as current production safety.

## 2. Audit method and verification limits

Inspection covered source, scripts, tools, tests, repository documentation, current CSV headers, and current history row/status counts. Searches included database file extensions, SQL statements, DuckDB/SQLite imports, migration frameworks, overwrites, CSV/workbook regeneration, grading, void, manual-review, lock, and scheduled-task code.

No application, database, data, workbook, scheduled task, or automation setting was changed. No provider was called and no runtime or grading job was executed.

Limits:

- Windows Task Scheduler was not queried. The repository documents/installs a nightly grading task, but whether it is installed, enabled, or overlapping on this workstation is **unverified**.
- No database engine was available, so no runtime database introspection was possible. Static inspection found no database integration to introspect.
- Provider-side retention, bookmaker rules, and the precise semantics of every upstream status string are **unverified** beyond repository adapters and fixtures.
- Existing history files establish current repository state, not a complete external production archive.
- `outputs/locked` contains preserved files, but no canonical source code was found that creates an authoritative lock record there. Its operational procedure is **unverified**.

## 3. Existing architecture

### 3.1 Canonical production-facing NBA path

```text
run_today.bat
  -> run_today.ps1
     -> courtvision_ai.py / CourtVisionAI
        -> provider games, stats, odds, injuries
        -> normalization and feature/model calculations
        -> in-memory candidates and selections
        -> prediction_history.csv append/rewrite
        -> guarded dated operator boards
     -> validation, tracking, grading, reports, evidence export
```

The accepted authority is explicit in `docs/ADR_001_CANONICAL_RUNTIME.md:9-13`, `:20-37`, and `:88-123`.

`run_today.ps1`:

- uses local system date/time (`run_today.ps1:4`, `:47`);
- rejects past dates unless `-ForcePastDate` (`:50-74`);
- treats existing Elite/full-market/SGP files as a reporting-only no-op (`:77-100`);
- performs a closed-slate guard before mutation (`:25-30`);
- runs later tracking/grading/reporting stages described in the ADR (`docs/ADR_001_CANONICAL_RUNTIME.md:48-72`).

`-ForcePastDate` is not the same as Python’s `--force-output-overwrite`. The PowerShell variable controlling protected outputs is initialized false and not exposed as a parameter. Direct CLI use can still intentionally overwrite protected boards through `--force-output-overwrite` (`courtvision_ai.py:8775`, `:8895-8897`, `:9069-9072`).

### 3.2 Canonical persistence surfaces

| Logical object | Path/pattern | Role | Current write behavior |
|---|---|---|---|
| prediction history | `data/history/prediction_history.csv` | selected/qualified model rows | append in memory, rewrite whole file; no dedupe |
| rejection history | `data/history/rejection_history.csv` | rejected candidate diagnostics | same append/rewrite helper |
| result feedback | `data/history/result_feedback.csv` | `auto_grade()` feedback | append with special duplicate preparation; rewrite |
| pick history | `data/history/pick_history.csv` | released Elite picks plus results | same-date upsert/dedupe; rewrite |
| market shadow history | `data/history/market_shadow_history.csv` | market-observation and shadow outcome history | replace same prediction date; preserve terminal result fields only |
| dated picks | `outputs/runtime/picks_DATE.csv` | grading input | direct overwrite |
| operator boards | `outputs/operator/*_board_DATE.csv` and runtime operator paths | actionable/reporting output | core boards guarded unless explicit force; several other artifacts overwrite |
| evidence ledger | `data/history/evidence_ledger.csv` | manually controlled trial evidence | append at release; blank closing/result cells filled later in place |
| daily evidence manifest | `data/history/evidence_daily_manifest.csv` | run/artifact hashes | append |
| manual-review history | `data/history/manual_review_history.csv` | operator review decisions | mutable operational history |
| performance/readiness reports | `data/history/performance_*.csv`, `market_readiness_summary.csv`, runtime reports | derived analytics | regenerated/rewriteable |

The path declarations are in `courtvision_ai.py:1799-1806`; canonical append/write helpers are at `:7940-7974` and `:8124-8181`.

### 3.3 Non-canonical and research surfaces

| Surface | Authority | Integrity behavior |
|---|---|---|
| `courtvision/engine.py` / `CourtVisionPro` | compatibility/non-canonical | separate report and grading behavior; can reconstruct dated picks |
| `courtvision/sports/nba/player_points_*` | research-only | strongest immutable segment, source-hash, conflict, settlement, and closing-evidence controls |
| `tools/theoddsapi_live_hr_collector.py` and MLB nightly pipeline | MLB research/live-data tooling, not promoted | timestamped raw snapshots exist, but master CSV and grade outputs are mutable |
| `courtvision/sports/mlb/training/hr_research_baseline.py` | MLB research-only | create-once predictions and append-only prediction/settlement ledger under a lock |

## 4. Database and schema findings

### 4.1 Database path and engine

| Question | Finding |
|---|---|
| active database path | none found |
| database candidates | none found in repository source/config or as `.duckdb`, `.db`, `.sqlite`, or `.sqlite3` files |
| DuckDB dependency | absent from `requirements.txt` |
| DuckDB installed version | not installed in active Python, `.venv`, Python 3.13, or Python 3.11; any unrelated external environment unverified |
| relational tables/views | none found |
| SQL `INSERT`, `UPDATE`, `DELETE`, replacement, DDL | none found for prediction persistence |
| migration framework | no Alembic, SQL migration folder, version table, or equivalent found |

The repository’s uses of “migration” are file-schema/refactoring procedures, not database migrations. For example, `scripts/history_tracking.py:638-655` adds missing CSV columns and rewrites `pick_history.csv`; `tests/schema_contracts.py:1-5` defines minimum append-compatible output columns.

### 4.2 Exact current logical-object schemas

There are no database tables. The following are the exact inspected headers/contracts of the relevant persisted file objects.

#### `data/history/prediction_history.csv`

```text
prediction_date,player_name,entity_name,player_id,team,team_abbr,opponent,market_type,selection,sportsbook_line,model_projection,projection_support_status,edge,edge_pct,confidence,quality_score,selection_score,odds,is_elite,qualification_reason,is_live_market,synthetic_line,line_source,source_lane,injury_status,injury_impact_score,team_injury_impact,opponent_injury_impact,injury_notes,injury_baseline_projection,injury_adjusted_projection,injury_projection_delta,injury_baseline_confidence,injury_adjusted_confidence,injury_confidence_delta,player_points_recent_form_ratio,player_points_injury_independent_support,player_points_confidence_uplift_dampened,player_points_confidence_uplift_reason,selection_rejection_reason,team_exposure_count_at_decision,game_exposure_count_at_decision,market_label,bet_label,raw_stat_key,market_alias,player_tier_weight,favorite_bias_factor,historical_confidence_multiplier,volatility_penalty,projection_realism_penalty,under_bias_multiplier,elite_points_ranking_penalty,elite_points_ranking_reason,points_recent_form_ratio,elite_rank_score,letter_grade,edge_abs,prop_type,minutes_bucket,odds_bucket,quality_band,confidence_band,selection_side,player_profile_bucket,player_points_line_band,injury_influence_bucket,blocked_by_elite_points_risk_guard,elite_points_risk_guard_reason,player_points_realism_dampened,player_points_realism_dampener_reason,market_trust_weight,market_trust_weight_band,rejection_reason,pre_rejection_reason,line,projection,stake_fraction,recommended_bet,raw_prop_type,raw_market_type,minutes_avg,side_edge,side_edge_pct,manual_status,manual_minutes_limit,manual_projection_adjustment,manual_confidence_adjustment,manual_context_reason,manual_context_applied,home_away,game_id,postseason,team_pace,opponent_pace,team_def_rating,opponent_def_rating,team_off_rating,opponent_off_rating,rest_days,opponent_rest_days,is_back_to_back,opponent_is_back_to_back,implied_team_total,game_total,spread,rest_edge,matchup_pace,team_net_rating,opponent_net_rating,opponent_implied_team_total,pace_context_signal,defense_context_signal,rest_context_signal,playoff_context_signal,overall_context_signal,context_preview_applied,context_pick_alignment,context_caution_level,kelly_projected_skip_reason,final_elite_rejection_reason,final_selection_source_lane,candidate_team_not_in_game,game_context_suppressed,game_context_suppression_reason,playoff_only_high_caution,game_status,game_date,odds_updated_at,recalibrated_projection,recalibrated_edge,recalibration_components_json,recalibration_selected,recalibration_rejection_reason,recalibration_mode,game_datetime,game_status_bucket,context_conflict_cause,baseline_team_abbr,provider_team_abbr,odds_team_abbr,resolved_team_abbr,identity_source_team_abbr,game_home_team_abbr,game_away_team_abbr,canonical_player_id,canonical_player_name,canonical_team_abbr,identity_roster_date,player_identity_valid,player_identity_status,player_identity_conflict_reason,player_identity_conflict_details,identity_team_conflict,identity_team_conflict_reason,identity_quarantine_reason,row_identity_valid,row_identity_quarantined,row_identity_quarantine_reason,source_identity_conflicted,source_identity_conflict_reason,source_identity_conflict_details,source_identity_conflict_policy,identity_resolution_category
```

There is no declared key, prediction ID, model ID/version, code SHA, feature snapshot ID, bookmaker ID, created timestamp, publication timestamp, or lock timestamp.

#### `data/history/pick_history.csv`

```text
prediction_date,run_timestamp,player_name,player_id,team,opponent,game_id,market,selection,line,projection,edge,abs_edge,odds,confidence,quality_score,qualification_reason,provider_used,result_status,actual_value,grading_skip_reason,kelly_eligible,skip_reason,context_caution_level,context_pick_alignment,line_source,fragility_score,fragility_bucket,fragility_reasons,survivability_score,survivability_bucket,survivability_reasons
```

The effective dedupe key is `prediction_date, player_name, market, selection, line` (`scripts/history_tracking.py:334-381`, `:1253-1260`). It omits player ID, game ID, provider/bookmaker, model/run, and opponent.

#### `data/history/market_shadow_history.csv`

```text
prediction_date,market_snapshot_key,player_name,player_id,team,team_abbr,opponent,game_id,market_type,selection,line,entry_line,entry_odds,opening_line_observed,closing_line_observed,close_source,close_coverage_status,line_move_points,movement_toward_pick,clv_line_points,clv_odds_delta,clv_grade,clv_confidence,model_projection,edge,confidence,quality_score,selection_score,odds,line_source,context_pick_alignment,context_caution_level,context_conflict_cause,kelly_projected_skip_reason,final_elite_rejection_reason,result_status,actual_value,hit,miss,push,shadow_roi,calibration_eligible,calibration_exclusion_reason,fragility_score,fragility_bucket,fragility_reasons,survivability_score,survivability_bucket,survivability_reasons,grading_skip_reason,same_opponent_recent_games,same_opponent_last_actual_points,same_opponent_last_line,same_opponent_last_selection,same_opponent_last_result_status,same_opponent_under_warning
```

`market_snapshot_key` is a truncated SHA-256 of date/game/player/team/opponent/market/selection, deliberately excluding line, odds, and bookmaker (`courtvision/market_intelligence/market_snapshots.py:16-25`, `:106-123`). It identifies a logical selection across observations, not an immutable quote.

#### Other core histories

```text
shadow_candidate_lane_history.csv:
prediction_date,source_artifact_date,source_board,lane,rank,rank_score,player,player_id,team,opponent,market_type,selection,line,odds,source_game_id,game_id,model_projection,edge,confidence,quality_score,selection_score,context_pick_alignment,context_edge_label,context_caution_level,source_rejection_reason,confidence_bucket,edge_bucket,historical_bucket_key,historical_recommendation,historical_graded_rows,historical_hit_rate,historical_roi,historical_clv_coverage_rate,promotion_status,real_money_eligible,kelly_eligible,elite_eligible,shadow_only,result_status,actual_value,hit,miss,push,flat_profit_loss,graded_at_utc,grading_status,grading_reason

incubator_history.csv:
prediction_date,game_date,player,player_id,team,opponent,market_type,selection,line,odds,edge,confidence,quality_score,context_caution_level,source_rejection_reason,incubator_status,real_money_eligible,result_status,actual_value,closing_line,clv,graded_at,grading_status,grading_reason

manual_review_history.csv:
prediction_date,player_name,team_abbr,opponent,market_type,selection,line,stake_amount,recommended_action,review_status,stake_policy,operator_action,operator_note,manual_review_required,manual_review_reason,same_opponent_under_warning,same_opponent_warning_reason,decision,decision_reason,created_at,updated_at

paper_kelly_history.csv:
prediction_date,paper_bucket,player_id,player_name,team_abbr,opponent,game_id,market_type,selection,line,odds,edge,directional_edge,confidence,quality_score,context_pick_alignment,context_caution_level,simulated_fraction,simulated_stake,pre_cap_simulated_stake,cap_adjustment_reason,player_exposure_after_cap,team_exposure_after_cap,game_exposure_after_cap,side_exposure_after_cap,bucket_exposure_after_cap,simulated_ev,real_kelly_eligible,simulation_only,reason_not_real_kelly,result_status,actual_value,paper_profit,paper_roi,grading_skip_reason,same_opponent_recent_games,same_opponent_last_actual_points,same_opponent_last_line,same_opponent_last_selection,same_opponent_last_result_status,same_opponent_under_warning
```

#### Evidence CSVs

```text
evidence_ledger.csv:
trial_id,run_date,prediction_date,code_sha,config_hash,provider_used,market,player,team,opponent,game_id,selection,line,odds,implied_probability,model_probability,edge,confidence,kelly_eligible,recommended_units,closing_line,closing_odds,result,profit_1u,void_reason,notes,created_at

evidence_daily_manifest.csv:
trial_id,run_date,prediction_date,code_sha,config_hash,run_status,provider_attempted,provider_used,fallback_used,released_recommendation_count,source_board_path,source_board_sha256,elite_board_path,elite_board_sha256,kelly_artifact_path,kelly_artifact_sha256,operator_card_path,operator_card_sha256,completion_audit_path,completion_audit_sha256,artifact_manifest_path,artifact_manifest_sha256,run_log_path,run_log_sha256,validation_log_path,validation_log_sha256,grading_log_path,grading_log_sha256,failure_reason,manual_intervention,notes,created_at
```

The evidence duplicate key is `trial_id,prediction_date,player,market,selection,line,odds` (`scripts/export_run_to_evidence.py:82-90`, `:480-528`). It omits game ID, bookmaker, team/opponent, model, and run artifact.

#### Dated operator outputs

The checked full-market and Elite board headers are subsets/extensions of `prediction_history.csv`. Minimum contracts require:

```text
prediction_date,player_name,team,opponent,game_id,market_type,selection,line,odds,line_source,model_projection,edge,confidence,quality_score,selection_score,is_live_market,context_caution_level,context_pick_alignment,same_opponent_under_warning,manual_review_required,fragility_score,fragility_bucket,survivability_score,survivability_bucket
```

See `tests/schema_contracts.py:8-27`. The actual historical boards inspected do not consistently include all newer minimum fields, showing that output schema evolved without a database migration/version record.

#### Game results

```text
date,home_team_id,away_team_id,home_score,away_score,game_id,home_team_name,away_team_name
```

This is team-game history, not per-prediction settlement evidence.

### 4.3 IDs and identifier representation

| Domain | Canonical representation | Integrity limitation |
|---|---|---|
| prediction | no stable ID; inferred composite fields | reruns and duplicate rows cannot be unambiguously ordered or linked |
| run | `run_timestamp` only in pick history; evidence has `trial_id` and manifest | no canonical run ID across all artifacts |
| game/event | `game_id` from provider, sometimes missing; date/team fallback | no provider/canonical event crosswalk in canonical history; doubleheader ambiguity |
| player | `player_id`, `canonical_player_id`, names, identity fields | fallbacks by normalized name/team/date can match ambiguously |
| market | free-text `market_type`/`market`, `prop_type`, `raw_*`, selection, line | no versioned canonical market ID |
| bookmaker | odds value and `line_source`/`provider_used`; no stable bookmaker ID in core history | quotes from multiple books/providers can collide |
| model | projections and calibration fields only | no model ID/version/bundle hash in canonical prediction rows |
| source snapshot | `odds_updated_at` and some line-source fields | no immutable canonical raw snapshot ID/hash |

Research-only NBA and MLB code already demonstrates stronger IDs: `prediction_id`, `prediction_run_id`, canonical/provider event IDs, player IDs, model IDs/versions, source hashes, repository commit SHA, and schema versions (`courtvision/sports/nba/player_points_evidence.py:1614-1744`; `courtvision/sports/mlb/training/hr_research_baseline.py:256-335`).

## 5. Prediction data flow

### 5.1 Inputs

The canonical runtime fetches:

- schedule/game status and game datetime;
- player/team statistics used to build baselines;
- market lines/odds;
- injuries/availability with SDK and HTTP fallback;
- manual context and lineup-related adjustments;
- calibration and prior history inputs.

Game normalization preserves provider status/date/datetime fields but does not establish one canonical UTC timestamp contract (`courtvision/data/normalization.py:175-224`, `:428-544`). Odds have `odds_updated_at`; injury diagnostics store only samples, not the complete provider payload (`courtvision_ai.py:4079-4119`, `:6253-6286`).

### 5.2 Model output and selection

Candidates are constructed in memory, enriched with identity/game context, scored, gated, and divided into Elite/full-market/near-miss/research outputs. The full row includes raw and adjusted projection, edge, confidence, quality, injury, context, identity, and recalibration fields.

There is no persisted `DRAFT` object. “Prediction” can mean:

- any calculated candidate;
- a qualified/Elite history row;
- a dated operator-board row;
- a released evidence-ledger row.

These moments are not represented by separate lifecycle events.

### 5.3 Persistence and implicit publication

The canonical runtime appends selected rows to prediction history during `predict()` (`courtvision_ai.py:4349-4351`, `:4712-4716`). It later writes boards through `_write_cli_outputs()` (`:8769-8983`).

For core boards, file existence is the closest current equivalent to publication/lock. `guard_no_existing_artifact()` checks `Path.exists()` and raises unless force is set (`courtvision/artifact_guard.py:40-57`). The subsequent write is a normal `to_csv`, not an atomic exclusive create (`courtvision_ai.py:8124-8147`). This creates a check-then-write race and does not persist publisher, publication time, content hash, or state transition.

Other outputs—such as several raw prediction/game edge files, diagnostics, JSON, and text—are not consistently protected and are overwritten (`courtvision_ai.py:8150-8181`, `:8902-8905`, `:8952-8983`).

### 5.4 Market observation versus prediction

Actual CourtVision predictions are best represented by:

- the canonical dated Elite board for released recommendations;
- the dated full-market board for the model’s broader decision surface;
- `pick_history.csv` for tracked released picks;
- an evidence-ledger row only after an intentional evidence export.

Market-only or provider-observation data includes:

- raw/normalized odds payloads and `odds_updated_at`;
- MLB `live_hr_snapshots_*.csv` rows;
- market shadow entry/open/closing fields;
- NBA research closing observations.

`market_shadow_history.csv` mixes a prediction-like model snapshot with market observations, later closing data, and results. It is not a clean immutable prediction table or a pure quote tape.

## 6. Grading and settlement data flow

### 6.1 Canonical pick-history grading

`grade_completed_picks()` loads `pick_history.csv`, selects only rows with `result_status == pending`, fetches game/stats evidence, calculates results, writes dated graded files, and rewrites pick history (`scripts/history_tracking.py:1755-1855`). This pending-only mask is the primary double-grading protection.

Matching first uses player/game IDs and then may fall back to player name, team, and prediction date (`scripts/history_tracking.py:1487-1536`). Only `player_points` is supported in that canonical path (`:1539-1570`).

Current terminal statuses are preserved when a same-date prediction row is regenerated (`scripts/history_tracking.py:344-381`). There is no `graded_at`, evidence hash, settlement rule version, actor, or revision chain in `pick_history.csv`.

### 6.2 `CourtVisionAI.auto_grade()`

`auto_grade()` loads `prediction_history.csv` by date, fetches/aggregates player statistics, compares actual to line, assigns win/loss/push, stamps `graded_at`, appends result feedback, and rebuilds calibration (`courtvision_ai.py:4926-5058`).

This is a separate grading vocabulary and history surface from `pick_history.csv`. The function itself does not filter already-graded prediction-history rows; protection depends on result-feedback append preparation. Multiple prediction-history duplicates can still cause repeated candidate grading work.

### 6.3 Nightly automation

`scripts/install_nightly_grader.ps1` defines a Windows scheduled task named `CourtVision Nightly Grader` at 02:00 local time (`:6-21`). It invokes `scripts/nightly_grade_and_refresh.py`, which grades pending picks and refreshes mutable performance summaries (`scripts/nightly_grade_and_refresh.py:50-95`).

Whether the task is installed and whether it can overlap another process are unverified. The script has no shared writer lock.

Evidence automation is operator-driven: its runbook explicitly says the wrappers do not independently collect closing lines, derive results, or create Task Scheduler tasks (`docs/EVIDENCE_AUTOMATION_RUNBOOK.md:11-15`, `:134-162`, `:194`).

### 6.4 Voids and manual review

Canonical pick-history unresolved handling:

- supported “unsupported” reasons become `unsupported`;
- current-day nonfinal/provider-missing-finality cases remain `pending`;
- other unresolved older cases become `void` (`scripts/history_tracking.py:292-300`).

This does not prove a sportsbook void and should not be treated as authoritative settlement.

MLB live tooling is more explicit:

- final, void, void-candidate, and manual-review-required results are terminal unless `--overwrite-filled` is used (`tools/fill_live_hr_results_from_mlb_statsapi.py:38-47`, `:500-511`);
- ambiguous/multiple player matches and related-event uncertainty route to manual review, while a player absent from the boxscore roster can become `void_candidate` (`:599-652`);
- workbook writes are atomic but replace the file (`:655-686`, `:901-902`);
- grade output excludes void/manual-review statuses but always opens the dated grade CSV with `w` (`tools/grade_live_hr_results.py:33-47`, `:179-231`, `:299-319`, `:384`).

NBA research settlement has separate game, participation, settlement, and manual-review domains. Postponed/cancelled becomes void, DNP becomes void, missing points/minutes/participation becomes manual review, and not-final remains pending (`courtvision/sports/nba/player_points_settlement.py:31-65`, `:1010-1029`). This is a useful contract, but it is research-only and not evidence of a universal bookmaker policy.

## 7. Historical-integrity risk register

| Priority | Risk | Concrete evidence | Consequence |
|---|---|---|---|
| Critical | scheduled lock buffer bypassed | `now=None` is treated as before lock; canonical calls omit `now` (`courtvision/runtime_gates.py:91-104`; `courtvision/pipeline/predict_pipeline.py:495`, `:1560`, `:1594`) | scheduled candidates can remain actionable inside/after intended lock until provider status changes |
| High | history mutates before board guard | history append occurs in `predict()`; board guard occurs later (`courtvision_ai.py:4351`, `:4715`, `:8895`, `:9146`) | failed rerun can still duplicate/alter history |
| High | no immutable prediction identity/time | exact `prediction_history.csv` schema has no ID/created/published/locked time; 1,611 duplicate groups observed | first-published probability/odds cannot be proven |
| High | pending market snapshots replaced | same-date shadow rows replaced; only terminal values preserved (`scripts/history_tracking.py:384-425`, `:957-1011`) | entry odds/projection can move retrospectively |
| High | canonical model inputs replaceable | fixed baseline CSV writes (`courtvision_ai.py:3987-4000`) | historical reproduction can use later training/baseline data |
| High | availability is a mutable row value, not evidence | injury fields embedded in current prediction row; only sample diagnostics retained (`courtvision_ai.py:6253-6286`) | original availability source/state is not independently auditable |
| High | generic unresolved becomes void | `scripts/history_tracking.py:292-300` | missing evidence/identity can be misreported as settlement |
| High | concurrent whole-file writers | `_append_history()` and `_write_csv()` read/rewrite without a common lock (`courtvision_ai.py:7940-7974`; `scripts/history_tracking.py:491-499`) | lost updates, partial cross-artifact publication, duplicate work |
| High | evidence ledger is mutable in place | closing/result tools fill cells then rewrite (`update_evidence_*`) | no append-only change history, actor, or prior value |
| High | non-canonical post-result recreation | missing dated picks can be rebuilt from a current board (`courtvision/grading/grade_props.py:178-265`) | look-ahead contamination if invoked after result availability |
| Medium | pick dedupe key too weak | omits IDs/provider/run (`scripts/history_tracking.py:334-381`, `:1259`) | doubleheaders or same player/line can collapse |
| Medium | fallback settlement match ambiguous | ID fallback to name/team/date (`scripts/history_tracking.py:1487-1536`) | wrong game/player result can be attached |
| Medium | timestamps have mixed meanings | prediction date, local run time, provider odds time, UTC grade time, and local evidence `created_at` coexist | ingestion, provider observation, creation, and publication can be confused |
| Medium | core board guard is TOCTOU | existence check and later normal write (`courtvision/artifact_guard.py:40-57`; `courtvision_ai.py:8124-8147`) | two writers can both pass the guard |
| Medium | forced overwrite exists | Python CLI force bypasses guard (`courtvision_ai.py:8775`, `:8895-8897`, `:9069-9072`) | dated “published” boards are not technically immutable |
| Medium | evidence export is multi-file nontransactional | manifest and ledger are appended in separate operations (`scripts/export_run_to_evidence.py:666-676`) | crash can leave partial evidence publication |
| Medium | MLB master discards earlier same-day quote observations | dedupe rewrites master and keeps latest key (`tools/theoddsapi_live_hr_collector.py:283-333`) | intraday market movement can disappear from master |
| Medium | MLB grades are regenerated | grader opens output with `w`; nightly pipeline invokes overwrite paths (`tools/grade_live_hr_results.py:384`; `tools/courtvision_mlb_nightly_pipeline.py:636-646`, `:736-746`, `:830-849`) | historical grade artifacts can change on rerun |
| Medium | late schedule/provider changes lack a transition record | game datetime/status is a row value, not an event | it is impossible to distinguish original schedule from later update |
| Medium | current full-market boards do not carry model/source hashes | minimum schema has values but no model bundle/input hash (`tests/schema_contracts.py:8-27`) | reproduction cannot bind a row to exact code/model/features |

### 7.1 Read-only history observations

As of 2026-07-25:

| File | Rows | Status/duplicate observation |
|---|---:|---|
| `prediction_history.csv` | 9,067 | 1,611 duplicate composite-key groups / 7,897 involved rows |
| `pick_history.csv` | 167 | 86 hit, 70 miss, 11 void; no duplicates under its effective key |
| `market_shadow_history.csv` | 1,856 | 427 hit, 470 miss, 65 pending, 2 push, 892 void; 13 duplicate groups |
| `shadow_candidate_lane_history.csv` | 90 | 6 hit, 13 miss, 71 pending; 4 duplicate groups |
| `incubator_history.csv` | 6 | 1 hit, 3 miss, 2 pending |
| `evidence_ledger.csv` | 1 | no duplicate under its export key |
| `manual_review_history.csv` | 1 | one recorded skip decision |

These counts are observations, not proof that each duplicate is incorrect. The missing stable ID/timestamp prevents a definitive classification.

### 7.2 Specific look-ahead questions

| Question | Finding |
|---|---|
| earlier probability overwritten by later probability | possible in same-date pick/shadow upsert; duplicates rather than overwrite in primary history; original publication cannot be selected reliably |
| prediction-time odds overwritten | possible in pending shadow history and mutable board files; evidence CSV protects already-filled closing fields but original odds have no bookmaker snapshot ID |
| raw model inputs replaced | yes, fixed baseline/calibration artifacts can be rewritten |
| player availability retroactively changed | possible as regenerated row state; no complete immutable availability event tape |
| post-event information used during grading | expected for settlement, but evidence/time boundaries are not cryptographically linked to the original prediction |
| predictions recreated after results known | possible in non-canonical `grade_props.py`; canonical past-date/board guards reduce but do not eliminate direct CLI/manual paths |
| regeneration changes history | yes for pick/shadow/performance/MLB master and grade files |
| double grading | canonical pick history limits to pending; other history surfaces and duplicates remain exposed |
| double provider processing | same-day guard exists for MLB collector but `--force` bypasses it; canonical provider calls lack a durable ingestion ID |
| ingestion vs provider time confused | yes; several fields/timestamps exist without a consistent semantic contract |

## 8. Existing protections

The following controls are real but limited:

- prediction date must match date-bearing artifact path (`courtvision/artifact_guard.py:22-37`);
- protected board existence fails closed unless force is explicit (`:40-57`);
- canonical PowerShell run no-ops when core dated boards exist (`run_today.ps1:77-100`);
- closed-slate guard runs before canonical mutation (`run_today.ps1:25-30`);
- pick-history terminal grades are preserved on rerun (`scripts/history_tracking.py:344-381`);
- grading processes pending pick-history rows only (`:1755-1855`);
- evidence export detects duplicate manifest/ledger keys unless an explicit allow flag is supplied (`scripts/export_run_to_evidence.py:495-528`, `:666-672`);
- evidence closing/result tools reject duplicate input keys, reject existing filled values unless skip is allowed, require a void reason, and use atomic replacement (`scripts/update_evidence_closing_lines.py:139-173`, `:215-244`; `scripts/update_evidence_results.py:131-175`, `:217-250`);
- MLB result workbooks are atomically replaced and terminal fields are normally preserved (`tools/fill_live_hr_results_from_mlb_statsapi.py:500-511`, `:655-686`);
- NBA and MLB research evidence demonstrates locks, hashes, stable IDs, create-once artifacts, conflict checks, and append-only settlement records.

The pre-game finalization guard is verification/reporting only. Its “pick history untouched” check contains only `if exists: pass` (`scripts/pre_game_finalization_guard.py:283-300`); it neither locks nor hashes pick history.

## 9. Existing state meanings

| Domain | Current meaning |
|---|---|
| prediction state | implicit: candidate, qualified, Elite/released, rejected, shadow, or incubator based on which file/lane contains the row |
| publication state | implicit file creation/evidence export; no persisted state or publisher |
| lock state | implicit protected-file existence and “READY_TO_LOCK” report; no authoritative `locked_at` event |
| player status | injury/manual/lineup fields copied into a row; continuing observations are not separate immutable events |
| settlement status | `pending/hit/miss/push/void/unsupported` in canonical histories; other surfaces use different vocabularies |
| grade | hit/miss booleans or win/loss/push strings; sometimes coupled to profit/ROI |
| void status | often generic unresolved historical row in canonical tracking; more explicit postponed/cancelled/DNP cases in research |
| manual-review status | row flags and separate manual-review history; MLB/NBA research have explicit required/quarantined states |

There is no single authoritative state machine.

## 10. Proposed immutable versus operational field classification

“Operationally mutable” below means **updated through new events and derived current-state views**, not in-place mutation of a published prediction.

### 10.1 Freeze at publication

| Field group | Existing fields covered | Proposed rule |
|---|---|---|
| prediction/run identity | `prediction_date`, new `prediction_id`, new `prediction_run_id`, `run_timestamp`, `trial_id`, `code_sha`, `config_hash`, new schema versions | immutable once published |
| event identity | `game_id`, `game_date`, `game_datetime`, home/away, team/opponent fields, canonical/provider team fields, postseason | freeze values and source IDs observed at publication; later schedule changes are events |
| player identity | `player_id`, `canonical_player_id/name/team`, identity source/status/conflict details, roster date | immutable snapshot; later identity correction is a correcting event |
| market identity | `market_type`, `market`, `prop_type`, raw market aliases/keys, `selection`, `line`, `sportsbook_line`, `line_source`, new bookmaker ID | immutable quote identity |
| prediction-time odds | `odds`, entry line/odds, implied probability, `odds_updated_at`, live/synthetic flags | immutable; later quotes/closing lines are market-observation events |
| model output | `model_projection`, `projection`, recalibrated projection/edge, edge fields, confidence, quality/selection/rank scores, probabilities, grade/bucket labels | immutable |
| model/config provenance | new model ID/version/bundle hash, feature schema, code SHA, config hash, calibration ID/hash | immutable |
| feature/input snapshot | minutes/recent form, pace/ratings/rest/totals/spread, injury impacts, manual adjustments, all context signals, calibration components | immutable content-addressed snapshot |
| availability at prediction | `injury_status`, injury notes/impact/baseline/adjusted fields, `manual_status`, lineup/manual fields | immutable observation attached to prediction; subsequent availability is separate |
| decision/gate result | `is_elite`, qualification/rejection reasons, exposure-at-decision, Kelly eligibility/skip, fragility/survivability, identity quarantine, source lane | immutable decision evidence |
| publication evidence | new `published_at_utc`, publisher/actor, artifact hash, source manifest hash, idempotency key | immutable |

This covers all fields in `prediction_history.csv`: identity; market/quote; projections/scoring/ranking; injury/manual context; game/context; recalibration; and identity-resolution fields. None should be overwritten after publication even if the model, provider, or operator later changes its view.

### 10.2 Separate append-only observations after publication

| Domain | Fields/events |
|---|---|
| market | observed line, odds, bookmaker, provider-reported time, ingestion time, source ID/hash, opening/closing classification |
| availability | availability status, lineup status, source, provider time, ingestion time, evidence hash |
| schedule | scheduled start, timezone, provider event ID, status, source, effective/observed time |
| lock | `locked_at_utc`, lock basis, scheduled-start snapshot, buffer, actor, policy version |
| manual review | required reason, reviewer, decision, decision reason, timestamps, evidence links |
| settlement evidence | final game status, player participation, actual stats, boxscore/source hash, observed time |
| settlement decision | status, result, void reason/rule, grade, profit, reviewer/policy version, correction link |

### 10.3 Mutable derived projections only

These can be rebuilt and replaced because they are not source evidence:

- current-state lifecycle/settlement views;
- performance, ROI, CLV, calibration, readiness, and daily summaries;
- operator dashboards/reports;
- “latest availability” and “latest quote” views;
- reconciliation/index files that are fully derivable from immutable events.

Every derived artifact should declare source event range/hash and build version.

### 10.4 Exhaustive cross-object classification rule

To remove ambiguity from the grouped classification:

- Every field in the exact `prediction_history.csv` header in section 4.2 is prediction-time evidence and is immutable after publication. There are no settlement fields in that header.
- In `pick_history.csv`, `result_status`, `actual_value`, and `grading_skip_reason` are operational settlement fields. All other fields describe the published pick or the at-decision context and are immutable. `run_timestamp` must be relabeled or replaced by explicit creation/publication timestamps because its current meaning is persistence time.
- In `market_shadow_history.csv`, `closing_line_observed`, `close_source`, `close_coverage_status`, movement/CLV fields, `result_status`, `actual_value`, `hit`, `miss`, `push`, `shadow_roi`, calibration fields, `grading_skip_reason`, and same-opponent result fields are later observations or derived fields. The remaining identity, entry quote, model, decision, fragility, and context fields are immutable.
- In shadow-candidate/incubator/paper-Kelly histories, identity, source, rank, prediction, quote, model, selection, simulation, and decision fields are immutable; result/actual/grade/profit/ROI/CLV/graded-at/grading-reason fields are operational or derived.
- In `evidence_ledger.csv`, `closing_line`, `closing_odds`, `result`, `profit_1u`, and `void_reason` are later operational facts/decisions. `notes` must be append-only commentary, not an editable cell. All other fields are immutable evidence.
- In `manual_review_history.csv`, prediction/market/stake/recommended-action fields are immutable request context. Review status, operator action/note, decision/reason, and update time are operational events. `created_at` is immutable.
- In dated boards, all row fields are immutable once that board is published. A corrected board is a new version/event and must not replace the prior artifact.
- In game/provider/availability data, raw source observations and their timestamps/hashes are immutable. “Latest” values are derived views only.

## 11. Recommended authoritative state model

This is a recommendation for approval, not an implementation.

### 11.1 Prediction lifecycle

Use one operational lifecycle:

```text
DRAFT -> PUBLISHED -> LOCKED
   \         \
    \         -> WITHDRAWN
     -> WITHDRAWN
```

- `DRAFT`: calculated internally, not released; may be discarded without entering the permanent ledger.
- `PUBLISHED`: exact recommendation and evidence have been committed to the ledger.
- `LOCKED`: publication can no longer be withdrawn or replaced; settlement may proceed later.
- `WITHDRAWN`: recommendation was explicitly retracted before lock. The original remains visible.

Do not use `OPEN`: it is ambiguous between market availability and lifecycle. Do not use `SETTLED` in this domain; settlement is independent. Do not use `CANCELLED` for a prediction because it collides with game status.

Publication is the `PREDICTION_PUBLISHED` transition, not a second mutable status column. Lock is the `PREDICTION_LOCKED` transition with policy evidence.

### 11.2 Settlement status

Use a separate settlement domain:

```text
NOT_READY
AWAITING_EVIDENCE
READY_FOR_GRADING
WIN | LOSS | PUSH | VOID | MANUAL_REVIEW
```

- `NOT_READY`: event has not reached a settlement-eligible game state.
- `AWAITING_EVIDENCE`: event is eligible to examine but required final evidence is unavailable/incomplete.
- `READY_FOR_GRADING`: final evidence and applicable rule are present.
- `WIN`, `LOSS`, `PUSH`, `VOID`: terminal settlement outcomes.
- `MANUAL_REVIEW`: evidence, identity, or rule is ambiguous; not silently converted to void.

Map current `pending` to `NOT_READY` or `AWAITING_EVIDENCE` based on reason. Map `hit/miss` to `WIN/LOSS`. Map `unsupported`, `unresolved`, `ambiguous`, and `conflicting` to `MANUAL_REVIEW` unless a separately approved rule says otherwise.

### 11.3 Other independent domains

- **Game state:** `SCHEDULED`, `IN_PROGRESS`, `FINAL`, `POSTPONED`, `CANCELLED`, `SUSPENDED`, `UNKNOWN`.
- **Availability observation:** `UNKNOWN`, `ACTIVE`, `QUESTIONABLE`, `DOUBTFUL`, `OUT`, `STARTING`, `NOT_STARTING`.
- **Participation evidence:** `UNKNOWN`, `PARTICIPATED`, `ZERO_MINUTES`, `DID_NOT_PARTICIPATE`.
- **Manual-review workflow:** `NOT_REQUIRED`, `REQUIRED`, `IN_REVIEW`, `RESOLVED`.

Do not infer settlement from availability alone. A pre-start `OUT`, a confirmed `DID_NOT_PARTICIPATE`, and a bookmaker void are different facts.

## 12. Recommended append-only event-ledger design

### 12.1 Source-of-truth model

Create a storage-neutral append-only event envelope:

| Field | Purpose |
|---|---|
| `event_id` | globally unique/stable event ID |
| `prediction_id` | aggregate ID shared by all events for a prediction |
| `event_sequence` | monotonic sequence within the aggregate |
| `event_type` | versioned event name |
| `occurred_at_utc` | real-world/source effective time |
| `recorded_at_utc` | ledger commit time |
| `provider_reported_at_utc` | optional upstream time, kept distinct |
| `actor_type`, `actor_id` | system/operator/reviewer provenance |
| `prediction_run_id`, `correlation_id` | transaction/run trace |
| `idempotency_key` | duplicate-write protection |
| `schema_version`, `policy_version` | contract provenance |
| `payload_json`, `payload_sha256` | canonical immutable event payload |
| `source_refs`, `source_hashes` | content-addressed evidence |
| `code_sha`, `config_hash`, `model_id`, `model_version` | reproducibility |
| `previous_event_hash`, `event_hash` | tamper-evident chain |
| `corrects_event_id` | explicit correction/supersession without mutation |

Minimum event types:

- `PREDICTION_PUBLISHED`
- `PREDICTION_WITHDRAWN`
- `PREDICTION_LOCKED`
- `SCHEDULE_OBSERVED`
- `MARKET_QUOTE_OBSERVED`
- `PLAYER_AVAILABILITY_OBSERVED`
- `SETTLEMENT_EVIDENCE_RECORDED`
- `SETTLEMENT_READY`
- `SETTLEMENT_DECIDED`
- `MANUAL_REVIEW_REQUESTED`
- `MANUAL_REVIEW_DECIDED`
- `CORRECTION_RECORDED`

The published payload must contain the exact prediction, quote, model/config/source identities, feature snapshot reference, gate decisions, and publication time. Closing lines, later availability, actuals, and grades must never be added to that payload.

### 12.2 Physical persistence recommendation

Phase 2 should not immediately introduce DuckDB as the authority because the repository has no database dependency, path, migration, backup, or writer policy.

Use the existing NBA research pattern as the first implementation basis:

- per-run immutable JSONL event segments;
- a repository-local lock file with stale-lock policy;
- write to a temporary directory;
- hash and verify every segment/manifest;
- atomic directory rename;
- completion marker;
- idempotent “already complete” only when canonical content hashes match;
- conflict quarantine rather than overwrite;
- rebuildable index/materialized CSV views.

DuckDB may later be approved as a **read model/analytical projection**. If it becomes the authoritative store, Phase 2 must first define a pinned version, path, schema migrations, backups, transactions, and a single-writer service. DuckDB’s single-process/multi-process write constraints must be tested for the actual automation topology.

### 12.3 Correction and deletion policy

- No `UPDATE` or `DELETE` of source events.
- A factual correction appends `CORRECTION_RECORDED` referencing the erroneous event.
- A settlement change appends a new settlement decision referencing and superseding the prior decision.
- Current-state views select the latest valid event by aggregate sequence and correction relationship.
- Source artifacts remain content-addressed; retention deletion requires a separate approved policy and must not break hashes.

## 13. Lock-policy findings and recommendation

### 13.1 Current findings

- Canonical scheduled start comes from provider game status/date/datetime normalized into candidate rows (`courtvision/data/normalization.py:175-224`; `courtvision/pipeline/predict_pipeline.py:913-952`).
- Default intended lock buffer is ten minutes (`courtvision/runtime_gates.py:44-45`).
- Final, in-progress, postponed, cancelled, suspended, delayed, and abandoned status categories block (`:53-67`, `:152-162`).
- Research mode bypasses game-status gates (`:144-147`).
- Scheduled status without a datetime is trusted (`:181-188`).
- Canonical operating date and scheduled automation are based on local Windows time. The Odds API research adapter explicitly uses `America/Toronto` and carries UTC/local commence times (`courtvision/providers/the_odds_api_provider.py:27`, `:595-612`, `:680`), but that is not a universal canonical contract.
- No explicit doubleheader policy, provider start-time precedence, schedule-revision history, or actual-start detector was found.

### 13.2 Recommended policy for approval

1. Store scheduled start as timezone-aware UTC plus the operating-date/timezone projection.
2. Persist every schedule observation with provider time, ingestion time, source ID, and hash.
3. At publication, bind the prediction to the then-current scheduled-start observation.
4. Lock at the earlier of:
   - scheduled start minus the approved buffer; or
   - a credible provider transition to in-progress/actual start.
5. Never automatically unlock because of a delay or later start-time revision.
6. A postponed/rescheduled event requires a reviewed event-identity decision. A new canonical event should receive a new prediction identity unless an approved mapping rule says otherwise.
7. Doubleheaders require canonical event IDs and start times; date/team/player keys are insufficient.
8. Conflicting provider times must fail closed or route to manual review according to an approved provider-precedence policy.
9. A late automation job must not publish if the computed lock time has passed, even when the provider still reports “scheduled.”

This intentionally favors the earliest credible information boundary. Human approval is required for the buffer duration, provider precedence, delay/unlock rule, reschedule identity, and emergency override procedure.

## 14. Settlement-policy findings and recommendation

### 14.1 Current coverage

| Case | Canonical NBA | NBA research | MLB tooling |
|---|---|---|---|
| pre-start scratch | affects injury/manual prediction fields; no explicit settlement rule | availability and participation separated | lineup/roster evidence used in research tooling |
| post-lock scratch | no authoritative separate event/rule | can record later evidence; settlement based on participation | result filler may classify roster/boxscore absence |
| did not start | no explicit rule | not identical to DNP; participation facts retained | no universal bookmaker rule |
| missing from boxscore | may become unresolved then historical void | missing identity/evidence can require manual review | ambiguous/related/multiple matches -> manual review; confirmed absence -> void candidate |
| postponed/cancelled | gate blocks; generic history void path | explicit void reason | explicit statuses available |
| shortened/suspended | no market-specific rule found | suspended remains pending | no universal shortened-game rule found |
| bookmaker-specific void | not modeled | closing policy can require same book, but settlement rules are not universal | not modeled |
| market participation rule | only player-points grading calculation is implemented | player-points research contract only | home-run Over 0.5 research logic |

### 14.2 Recommended policy structure

Do not implement a universal “DNP = void” or “postponed = void” rule across all books/markets.

Create a versioned settlement policy registry keyed by:

```text
sport + league + bookmaker + market + rule_effective_date + policy_version
```

Each rule must state:

- event finality requirement;
- participation/start/minimum-action requirement;
- postponed/rescheduled/cancelled/suspended/shortened treatment;
- push calculation;
- source precedence;
- required evidence;
- unsupported conditions;
- whether automated settlement is permitted.

If a rule or evidence is missing, set `MANUAL_REVIEW`; do not default to void. Keep the factual settlement evidence separate from the grading decision.

## 15. Migration risks

- **Semantic drift:** “prediction” currently describes candidates, Elite boards, pick history, and evidence rows. A cutover can accidentally change which rows count as released.
- **Backfill look-ahead:** existing rows lack reliable publication timestamps/model hashes. Historical imports must be labeled `legacy_import_unverified`, not reconstructed as contemporaneous events.
- **ID collision:** current composite keys omit bookmaker/model/game in several files. Stable IDs require an approved canonical identity contract.
- **Dual-write divergence:** CSV success plus ledger failure, or the reverse, can produce inconsistent state without one publication transaction/reconciliation.
- **Lock behavior change:** enforcing the intended ten-minute buffer is bankroll-facing. It must be a separately approved behavior change, not smuggled into persistence work.
- **Settlement behavior change:** replacing historical generic voids with manual review changes metrics and must not rewrite old records.
- **Schema evolution:** current schemas are header-driven and some historical boards predate newer minimum contracts.
- **Automation overlap:** a daily run, nightly grader, repair script, or manual evidence update can race until a single writer is enforced.
- **Storage choice:** adding DuckDB introduces packaging, backup, migration, file-locking, and deployment decisions that do not exist today.
- **Research promotion risk:** research-only NBA/MLB contracts are strong but not validated as parity-preserving replacements for the canonical runtime.
- **PII/secrets/path leakage:** source manifests must hash/normalize paths and never copy credentials or raw authorization headers.

## 16. Rollback requirements

1. Keep all current CSV/workbook writers authoritative during initial shadow phases.
2. Add event writing behind an explicit disabled-by-default feature flag.
3. Never delete or rewrite event segments during rollback.
4. If a shadow write fails, fail or warn according to a pre-approved mode; record reconciliation status.
5. Build a daily reconciliation report for counts, IDs, prediction values, quote values, artifact hashes, and lifecycle states.
6. Cut readers over separately from writers.
7. Preserve a reader flag that restores current CSV-derived views without data migration.
8. Before any authoritative cutover, snapshot and hash the relevant existing histories and document restore instructions.
9. Roll back by disabling new writes/readers and appending a suspension/correction event—not by deleting the ledger.
10. Database adoption, if approved later, requires tested backup/restore and schema downgrade/forward compatibility.

## 17. File-by-file implementation plan

No changes in this section are authorized by this Phase 1 report.

| File/area | Future integration work |
|---|---|
| `courtvision_ai.py` | define one publication boundary after successful prediction and before/with protected board publication; emit exact prediction/source/model payload; eliminate history mutation before a failed board guard |
| `courtvision/artifact_guard.py` | replace check-then-write with atomic/idempotent publication primitive; persist content hash and publication event |
| `courtvision/runtime_outputs.py` | add immutable evidence/event paths and manifest references without changing current output paths initially |
| `run_today.ps1` / `run_today.bat` | only after explicit approval: coordinate publication transaction, lock event, reconciliation, and failure mode; scripts are restricted production entrypoints |
| `courtvision/runtime_gates.py` | separately approved fix to use an explicit timezone-aware `now`; persist policy/version and lock reason |
| `courtvision/pipeline/predict_pipeline.py` | pass explicit time context; attach schedule observation ID/hash and gate outcome |
| `courtvision/data/normalization.py` | normalize UTC/provider/ingestion timestamps without discarding raw source values |
| provider clients/adapters | retain source IDs, provider timestamps, ingestion timestamps, raw payload hashes, bookmaker IDs, and event crosswalk |
| injury/lineup/manual-context modules | emit immutable availability observations; link the exact observation set to publication |
| `scripts/history_tracking.py` | derive pick/shadow current views from events; stop replacing pending source evidence; emit settlement evidence/decision events; strengthen IDs |
| `scripts/post_run_tracking.py` and grading/repair/backfill scripts | use one settlement service/idempotency contract; do not silently convert unresolved to void |
| `scripts/nightly_grade_and_refresh.py` | acquire the same writer lease; write settlement events first, then rebuild reports |
| `scripts/pre_game_finalization_guard.py` | validate hashes/event state; require a persisted publication/lock record; implement a real “history untouched” check |
| `scripts/write_artifact_manifest.py` | bind every published artifact and source snapshot by hash |
| `scripts/init_*evidence*`, `append_*evidence*`, `export_run_to_evidence.py` | treat current CSVs as compatibility exports; replace in-place closing/result updates with append-only events |
| `scripts/update_evidence_closing_lines.py` / `update_evidence_results.py` | preserve validation but emit observation/settlement events and rebuild CSV export |
| `courtvision/grading/grade_props.py` | prohibit post-lock prediction reconstruction or require an explicit legacy/manual-review path |
| `courtvision/reporting/text_report.py`, `courtvision/engine.py` | consume authoritative read models only; preserve non-canonical status until a new ADR |
| `courtvision/sports/nba/player_points_evidence.py` | extract/reuse its lock, hash, atomic segment, completion, conflict, and verifier primitives |
| `courtvision/sports/nba/player_points_settlement*.py`, `player_points_closing.py` | reuse domain separation and immutable evidence concepts; do not promote policy without approval |
| `tools/theoddsapi_live_hr_collector.py` | preserve every observation in immutable segments; make master CSV a derived latest view |
| `tools/fill_live_hr_results_from_mlb_statsapi.py` | emit factual result/participation evidence and manual-review requests; keep workbook as projection |
| `tools/grade_live_hr_results.py` / `courtvision_mlb_nightly_pipeline.py` | append settlement decisions; stop destructive grade regeneration; share writer lock/reconciliation |
| `courtvision/sports/mlb/training/hr_research_baseline.py` | reuse stable ID, file lock, create-once prediction, separate settlement record, and conflict logic |
| `tests/schema_contracts.py` | add versioned event/published-payload contracts; keep compatibility output contracts |
| relevant tests | add idempotency, concurrency, crash-boundary, rerun, late-run, timezone/DST, delay, reschedule, doubleheader, scratch/DNP, conflict, and correction tests |
| docs/ADR/runbooks | approve storage, state semantics, lock, settlement policy, single writer, backup, override, and operational recovery |

Key existing tests to extend include `test_artifact_overwrite_guard.py`, `test_prediction_artifact_date_isolation.py`, `test_history_tracking.py`, `test_grade_completed_picks_dry_run.py`, `test_nightly_grade_and_refresh_dry_run.py`, the evidence append/update tests, NBA player-points evidence/settlement/closing tests, and MLB HR research/grading tests.

## 18. Recommended staged rollout

### Stage 0 — decisions and contracts

- approve state vocabularies, identity rules, publication moment, storage, lock policy, settlement policy ownership, and writer topology;
- freeze versioned JSON schemas and event hash canonicalization;
- add tests only; no production behavior change.

### Stage 1 — immutable snapshot envelope in shadow mode

- generate `prediction_run_id`, stable `prediction_id`, source/model/config hashes, and publication candidate payload;
- do not change selection, Kelly, thresholds, board contents, or current histories;
- compare payloads to existing boards.

### Stage 2 — append-only shadow ledger

- dual-write immutable publication events after current board publication;
- use writer lock, temp directory, hashes, atomic rename, completion marker, and reconciliation;
- no reader consumes ledger for production.

### Stage 3 — append-only market, availability, and settlement evidence

- record observations and settlement facts without altering current grading;
- build shadow current-state views;
- quarantine ambiguity.

### Stage 4 — parity and failure testing

- exercise duplicate runs, concurrent writers, crashes between files, DST, delayed/rescheduled games, doubleheaders, post-lock scratches, missing boxscores, and conflicting results;
- complete a frozen forward evidence period.

### Stage 5 — read-model cutover

- switch reporting/history readers one at a time after parity approval;
- retain CSV compatibility exports and rollback flags.

### Stage 6 — authoritative lifecycle/settlement cutover

- only through a new ADR and explicit approval;
- enforce approved lock and settlement policies;
- archive but do not rewrite legacy evidence.

## 19. Human decisions required before implementation

1. What exact action constitutes publication: first Elite board write, operator approval, evidence export, or another step?
2. Is full-market output a prediction record, a candidate record, or both under distinct event types?
3. What fields define stable prediction identity, especially across reruns, line changes, books, and models?
4. Which provider supplies the canonical event/player/bookmaker IDs, and how are crosswalk corrections handled?
5. What is the authoritative operating timezone, and must all persisted timestamps be UTC with an explicit local projection?
6. What lock buffer is approved?
7. Which provider time wins on conflict, and may a delayed game ever unlock?
8. How are postponed/rescheduled events and doubleheaders assigned new or continuing identities?
9. Is the lock based on scheduled start, earliest credible scheduled start, detected actual start, or the recommended conservative combination?
10. Which bookmaker/market settlement rule sources are authoritative and who versions/approves them?
11. How should pre-start scratch, post-lock scratch, did-not-start, zero-minutes, shortened, and suspended cases settle per market/book?
12. Who may resolve manual review and append corrections?
13. Should shadow-ledger failure fail the canonical run or only alert during early rollout?
14. Is immutable JSONL the source of truth with DuckDB as a read model, or should a database become authoritative?
15. What are retention, backup, off-host archive, and disaster-recovery requirements?
16. What legacy histories, if any, should be imported as explicitly unverified evidence?
17. What concurrency model is supported: one scheduled writer, a writer service, or a lease/queue?
18. What emergency override is permitted, how is it approved, and what mandatory audit event accompanies it?

## 20. Recommended Phase 2 scope

Phase 2 should be narrow and non-bankroll-facing:

1. approve and implement versioned, storage-neutral prediction IDs and event envelopes;
2. reuse the research evidence writer’s lock/hash/atomic-segment mechanics;
3. dual-write **shadow-only `PREDICTION_PUBLISHED` events** at the canonical successful publication boundary;
4. capture exact source/model/config/artifact hashes and distinct provider/ingestion/publication timestamps;
5. add idempotency, concurrency, crash, rerun, and reconciliation tests;
6. leave selection logic, thresholds, Kelly, bankroll, lock behavior, grading decisions, settlement rules, current CSVs, and operator outputs unchanged.

Fixing the canonical `now=None` lock defect is important, but it is a separate explicitly approved bankroll-facing change and should not be bundled into the initial ledger implementation.

---

**Phase 1 stopping point:** This report is the only repository change. Phase 2 has not begun.
