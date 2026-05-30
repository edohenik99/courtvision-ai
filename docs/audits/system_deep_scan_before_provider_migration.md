# CourtVision Deep System Scan Report Before Provider Migration

This report documents the deep read-only system scan of the CourtVision system conducted prior to the SportsGameOdds provider migration. All assessments are based on the stable state of Phase 5J.2.

---

## 1. Repository Structure Scan

The CourtVision repository structure is organized into separate concern-based folders. Below is a mapping of the major directories and their architectural purposes:

*   **`courtvision/`**: Core system package containing the domain logic, models, and analytical engines.
    *   **`courtvision/clients/`**: Ingestion layer clients. Houses the BallDontLie (`balldontlie_client.py`), SportsDataIO (`sportsdataio_client.py`) integrations, and the provider coordinator (`provider_manager.py`).
    *   **`courtvision/data/`**: Data normalization adapters. Converts raw data schemas from various providers into standardized internal schemas (e.g., `bdl_odds_adapter.py`, `normalization.py`).
    *   **`courtvision/pipeline/`**: Execution pipeline. Orchestrates the prediction flow stages (`predict_pipeline.py`, `runner.py`, `contracts.py`).
    *   **`courtvision/reporting/`**: Phase-specific reporting modules. Generates analysis summaries and audit sheets (`quality_summary.py`, `incubator_board.py`, `shadow_candidate_lane.py`, `under_visibility_audit.py`).
    *   **`courtvision/calibration/`**: Performance tracking and calibration calculations (`buckets.py`, `grading_summary.py`).
    *   **`courtvision/context/`**: Situational filters and metadata tags (`game_context.py`, `manual_player_context.py`, `player_identity.py`).
    *   **`courtvision/injuries/`**: Injury tracking and injury-related projection volatility adjustments (`injury_engine.py`, `realism.py`, `volatility.py`).
*   **`scripts/`**: Orchestration and operational entry points. Contains daily run wrappers, historical grading tools, and manual override utilities.
*   **`tests/`**: Automated test suite. Divided into stable, legacy, and experimental test modules.
*   **`docs/`**: Documentation, architecture audits, and comparison matrices.
*   **`outputs/`**: Workspace for temporary and persistent runtime logs, diagnostics, and boards (gitignored).
*   **`data/history/`**: Historical performance databases (e.g., `pick_history.csv`, `shadow_candidate_lane_history.csv`, `incubator_history.csv`).

---

## 2. Entry Point Scan

Operational scripts define the daily ingestion and finalization flow:

*   **Canonical Entry Point**: `courtvision_ai.py` (legacy monolithic CLI entry point for prediction) and `run_today.ps1` (orchestrates validation, execution, grading, and reporting).
*   **Daily Orchestration Order**:
    1.  **Closed-Slate Lifecycle Guard**: Validates that the targeted slate date matches today's date or is explicitly forced via `-ForcePastDate`.
    2.  **Board Existence Check**: Prevents full prediction pipeline execution if protected prediction boards (`elite_board_YYYY-MM-DD.csv`, `full_market_board_YYYY-MM-DD.csv`) already exist.
    3.  **Baseline Fitting**: Performs baseline model fitting (`courtvision_ai.py --fit-only`) to generate player/team baseline artifacts if they are absent.
    4.  **Pipeline Prediction**: Runs the prediction pipeline (`courtvision_ai.py --predict-only`) to fetch odds, players, and calculate edge.
    5.  **Validation**: Validates runtime artifact shape and schemas using `scripts/validate_runtime_outputs.py`.
    6.  **Sanity Audits**: Runs full-market and candidate quality drift audits.
    7.  **Kelly Stakes Calculation**: Executes `scripts/run_kelly_stakes.py` to produce stakes allocations from the Elite board.
    8.  **Grading**: Tracks pending picks, additional completed picks, and grades market shadow history.
    9.  **Research & Shadow Generation**: Creates Phase 4B shadow artifacts and Phase 5 research reports.
    10. **Summarization**: Refreshes daily summaries, quality summaries, and completion state audits.
    11. **Operator Card & Manifest**: Generates the final operator text card and executes the artifact manifest completeness audit.
*   **Reporting Order**: Research audits (e.g., `under_visibility_audit`, `shadow_candidate_lane`) write their files first, followed by the daily summary, operator card, and finally the bet readiness report.
*   **Provider Data Ingestion**: Raw data enters through the `ProviderManager` during the pipeline execution stage. In legacy blocks of `courtvision_ai.py`, direct calls to `BallDontLieClient` fetch odds and injuries.
*   **Artifact Write Locations**: Output files are split between `outputs/runtime/operator/` (highly protected files read by downstream systems) and `outputs/runtime/diagnostics/` (JSON diagnostic files for audit tracking).
*   **Overwrite Guards**: Enforced strictly. `run_today.ps1` stops immediately if prediction outputs exist, blocking the execution from overwriting settled slates. Individual write scripts also implement checking parameters like `--closed-slate-safe` or checking for existing files.

---

## 3. Provider/Data Source Scan

### Dependencies
The system integrates with two APIs:
1.  **BallDontLie API**: Primarily accessed via the `balldontlie` python package SDK.
2.  **SportsDataIO API**: Leveraged through manual HTTP requests using the `requests` library in `SportsDataIOClient`.

### Schema Coupling
*   **BallDontLie-specific Assumptions**:
    *   `courtvision_ai.py` contains a complete internal implementation of `BallDontLieClient` that paganites `/stats`, `/games`, `/injuries`, and `/odds/player_props` expecting nested JSON dict arrays matching BallDontLie schemas.
    *   `courtvision/data/bdl_odds_adapter.py` assumes structure: flat integer `player_id`, nested `market` fields (`market.over_odds`, `market.under_odds`), and expands them from a single JSON row into separate over and under rows.
*   **Provider-agnostic Modules**:
    *   Analytical layers (`ValueEngine`, `predict_pipeline.py`), situational contexts (`game_context.py`), and injury logic are provider-agnostic. They operate on normalized `courtvision.models` domain dataclasses.
*   **SportsGameOdds Insertion Point**:
    *   A new `SportsGameOddsClient` class should be added under `courtvision/clients/`.
    *   The `ProviderManager` class in `courtvision/clients/provider_manager.py` must import this client, initialize it, and register it inside `_get_client()`.
*   **Provider Abstraction Gaps**:
    *   `courtvision_ai.py` bypasses the `ProviderManager` layer for its direct odds lookup (`BallDontLieClient.get_odds`) and SDK injuries fetch. This represents a hard coupling to BallDontLie.

---

## 4. Data Contract Scan

Downstream modules expect strict schemas in tabular runtime outputs. Below are the contracts identified from `tests/schema_contracts.py` and diagnostic code:

| Schema Name | Required Columns / Keys | Provider-Specific Columns | Downstream Consumers | Failure Risk if Missing |
| :--- | :--- | :--- | :--- | :--- |
| **Games** | `id`, `date`, `home_team`, `visitor_team`, `home_team_score`, `visitor_team_score`, `status` | None (Normalized) | Predict pipeline, player name resolvers | Inability to map matchups or team contexts |
| **Odds / Player Props** | `player_id`, `player_name`, `raw_market_name`, `raw_prop_type`, `raw_market_type`, `market_type`, `selection`, `line`, `odds`, `vendor`, `game_id`, `team_abbr`, `line_source`, `unresolved_reason`, `updated_at` | `provider_team_abbr`, `odds_team_abbr`, `resolved_team_abbr`, `identity_source_team_abbr` | Predict pipeline, edge evaluator | Complete drop in odds matching, empty boards |
| **Player Baselines** | `player_id`, `player_name`, `expected_minutes`, model coefficients, averages | None (Internal) | Value Engine, Predict pipeline | Fallback to uncalibrated predictions |
| **Injury** | `player_id`, `player_name`, `team_id`, `team_abbreviation`, `status`, `description` | None (Normalized) | Injury engine, minutes volatility calculator | Oversized projection lines due to missed injury adjustments |
| **Full Market Board** | `prediction_date`, `player_name`, `team`, `opponent`, `game_id`, `market_type`, `selection`, `line`, `odds`, `line_source`, `model_projection`, `edge`, `confidence`, `quality_score`, `selection_score`, `is_live_market`, `context_caution_level`, `context_pick_alignment`, `same_opponent_under_warning`, `manual_review_required`, `fragility_score`, `fragility_bucket`, `survivability_score`, `survivability_bucket` | None (Internal) | Summaries, research scripts, bet readiness checks | Reporting crashes |
| **Elite Board** | Identical to Full Market Board | None (Internal) | Kelly Stakes script, bet readiness check | Direct block on wagering stakes calculation |
| **Kelly Stakes** | `prediction_date`, `player_name`, `market_type`, `selection`, `line`, `american_odds`, `edge_pct`, `confidence`, `stake_fraction`, `stake_amount`, `kelly_eligible`, `skip_reason`, `manual_review_required`, `recommended_action`, `stake_policy` | None (Internal) | Operator card writer, bet readiness report | Block on manual betting execution |
| **Shadow Candidate Lane** | `prediction_date`, `source_artifact_date`, `player_name`, `market_type`, `selection`, `line`, `edge`, `confidence`, `research_lane`, `real_money_eligible`, `kelly_eligible`, `elite_eligible`, `shadow_only` | None (Internal) | Bet readiness report, shadow lane performance | Missing shadow tracking output |
| **Incubator Board** | `prediction_date`, `player_name`, `market_type`, `selection`, `line`, `edge`, `confidence`, `real_money_eligible`, `kelly_eligible`, `elite_eligible`, `shadow_only` | None (Internal) | Bet readiness report | Incomplete research lanes diagnostics |

---

## 5. Artifact Flow Scan

Tabular and text artifacts flow sequentially:

1.  **`full_market_board_YYYY-MM-DD.csv`**
    *   *Producer*: `courtvision_ai.py` prediction pipeline.
    *   *Consumers*: Audits, shadow lane generators.
    *   *Timing*: Step 5 of ps1 run.
    *   *Overwrite Guard*: Handled at runner level; run aborted if present.
    *   *Regenerate Pre-Game*: Safe.
    *   *Regenerate Post-Game*: Dangerous (erases raw candidate pool snapshot).
    *   *Real-Money impact*: Indirect.
2.  **`elite_board_YYYY-MM-DD.csv`**
    *   *Producer*: `courtvision_ai.py` prediction pipeline.
    *   *Consumers*: `run_kelly_stakes.py`, summaries.
    *   *Timing*: Step 5 of ps1 run.
    *   *Overwrite Guard*: Handled at runner level.
    *   *Regenerate Pre-Game*: Safe.
    *   *Regenerate Post-Game*: Unsafe.
    *   *Real-Money impact*: Direct (determines base bets).
3.  **`kelly_stakes_YYYY-MM-DD.csv`**
    *   *Producer*: `run_kelly_stakes.py`.
    *   *Consumers*: Operator card, readiness report.
    *   *Timing*: Step 9 of ps1 run (after elite board).
    *   *Overwrite Guard*: Checked in stakes calculation.
    *   *Regenerate Pre-Game*: Safe.
    *   *Regenerate Post-Game*: Unsafe (stakes might recalculate incorrectly).
    *   *Real-Money impact*: Direct (determines stake size).
4.  **`operator_card_YYYY-MM-DD.txt`**
    *   *Producer*: `write_operator_card.py`.
    *   *Consumers*: Operator review.
    *   *Timing*: Step 15 of ps1 run (finalization).
    *   *Overwrite Guard*: Script implements strict safety guards.
    *   *Regenerate Pre-Game*: Safe.
    *   *Regenerate Post-Game*: Safe (with `--force`).
    *   *Real-Money impact*: Direct (read by operator to execute wagers).
5.  **`pre_game_finalization_guard_YYYY-MM-DD.json`**
    *   *Producer*: `pre_game_finalization_guard.py`.
    *   *Consumers*: Bet readiness report.
    *   *Timing*: Step 13 of ps1 run.
    *   *Overwrite Guard*: Writes safety report directly.
    *   *Regenerate Pre-Game*: Safe.
    *   *Regenerate Post-Game*: Safe.
    *   *Real-Money impact*: None (reporting only).
6.  **`bet_readiness_report_YYYY-MM-DD.json`**
    *   *Producer*: `write_bet_readiness_report.py`.
    *   *Consumers*: Audits.
    *   *Timing*: Step 14 of ps1 run.
    *   *Overwrite Guard*: Safe overwrite.
    *   *Regenerate Pre-Game*: Safe.
    *   *Regenerate Post-Game*: Safe.
    *   *Real-Money impact*: None (reporting only).

---

## 6. Betting Safety Scan

The following invariants have been verified during the read-only scan:

*   **Research Isolation**: Research artifacts (such as under audits or shadow lane reports) do not write to or mutate `final_decision` inside `operator_card_YYYY-MM-DD.txt`.
*   **Staking Separation**: Shadow candidate lanes and incubator rows are marked with `real_money_eligible=False` and `kelly_eligible=False`. Downstream staking scripts (`run_kelly_stakes.py`) only ingest from the Elite board, ensuring shadow rows cannot feed into Kelly calculation.
*   **UNDER Directional Gate**: Research UNDERs are marked as `shadow_only=True` and cannot become eligible for real-money staking. No betting promotion exists.
*   **Script Safety**: The research orchestrator (`write_research_artifacts.py`) only performs read-only parsing of boards and does not write to or alter the historical wagering log `pick_history.csv`.
*   **Threshold Constancy**: No Phase 5 or reporting scripts modify or dynamically adjust Elite admission or Kelly sizing thresholds. All thresholds remain constant.

---

## 7. Provider Migration Readiness Scan

*   **How hard would it be to add SportsGameOdds?**
    *   It is structurally straightforward because of the priority fallback engine (`ProviderManager`) already built in `courtvision/clients/provider_manager.py`.
*   **Which modules need new adapters?**
    *   `courtvision/clients/sportsgameodds_client.py` must be created.
    *   `courtvision/data/sgo_odds_adapter.py` must be implemented to convert SGO odds rows into `MarketProp` entities.
*   **Which schemas must be mapped?**
    *   SGO raw games, players, stats, injuries, and player prop odds must be mapped to normalized domains inside `courtvision.models`.
*   **What provider comparison report already exists or needs to exist?**
    *   A comparative validation script checking odds value mapping and identity alignment (team name and athlete name parity) between BDL, SportsDataIO, and SGO must be implemented during a shadow phase.
*   **What should remain BallDontLie-primary?**
    *   The baseline player/team historical data and IDs are heavily structured around BDL indices. These must remain BDL-primary for projection calculations during the initial transition.
*   **What should be shadow-only first?**
    *   The entire SGO client pipeline must be deployed as a shadow provider first. Its outputs must map exclusively to a shadow-only research lane to avoid bankroll contamination.
*   **What tests are needed before any provider switch?**
    *   Parity tests verifying player identity resolution, odds mapping, and fallback capability of `ProviderManager` under SGO mock payloads.

### Migration Readiness Classification
The codebase is classified as **`READY_FOR_SHADOW_PROVIDER_SPIKE`**. The architectural separation inside `ProviderManager` is prepared to accommodate the new client.

---

## 8. Test Coverage Scan

### Strongest Test Areas
*   **Daily finalization & summaries**: Thoroughly tested across closed slate rules (`test_daily_summary_closed_slate_safe.py`, `test_daily_summary_no_slate_safe.py`).
*   **Operator card formatting & safety**: Verified across edge configurations and overwrite checks (`test_operator_card.py`, `test_operator_card_overwrite_guard.py`).
*   **Bet readiness & finalization guards**: Well-covered by direct unit assertions (`test_bet_readiness_report.py`, `test_pre_game_finalization_guard.py`).

### Weak Test Areas
*   **Alternative Provider Integration**: The testing of `SportsDataIOClient` depends largely on mock shims and lacks broad integration coverage.
*   **Data contract validation**: Schema structure checks are present but do not assert mock provider data corruption recovery.

### Missing Parity/Validation Tests
*   Provider comparison validation is absent.
*   Schema contract assertions for raw client HTTP responses are missing.

---

## 9. Risk Register

| Risk | Severity | Likelihood | Affected Files / Modules | Why It Matters | Recommended Mitigation | Must Fix Before Spike |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Provider Schema Mismatch** | High | Medium | `courtvision/clients/sportsgameodds_client.py` | Incompatible nesting or data types (e.g., string lines) crash pipeline | Implement thorough JSON schema assertions in adapter unit tests | Yes |
| **Player Identity Mismatch** | High | High | `courtvision/context/player_identity.py` | Diverging naming conventions (e.g., suffixes, abbreviations) drop players | Leverage a robust fuzzy matching or alias mapping lookup table | Yes |
| **Odds Format Mismatch** | Medium | Low | `courtvision/data/sgo_odds_adapter.py` | Decimal/Fractional odds instead of American format break edge calculations | Build explicit American-odds conversion shims in the adapter | Yes |
| **Stale Artifact Rerun Risk** | Medium | Medium | `run_today.ps1` | Executing past date slate rewrites historical records | Enforce strict date locks unless `-ForcePastDate` is passed | Yes (Fixed) |
| **Injuries Unavailable** | Low | Medium | `courtvision/injuries/injury_engine.py` | SGO injury endpoints returning empty list skips realism penalties | Ensure fallback to BallDontLie or SportsDataIO injury feeds | No |

---

## 10. Static Commands Verification Results

### Git Status & History
The repository is in a clean state:
```
$ git status --short
(empty)
```

Latest commit log matches the stable work perfectly:
```
1c572ab Deduplicate bet readiness candidates
0c1682b Avoid duplicate operator cards and improve research names
97e6599 Add bet readiness report
e971dea Document architecture and pre-lock audit
0462159 Add pre-game finalization guard
```

---

## 11. Targeted Test Suite Results

The targeted pytest suite executed successfully:
```
py -3.13 -m pytest -k "bet_readiness or pre_game_finalization or research_artifact or operator_card or under_visibility or shadow_candidate or incubator or no_bet or grading or artifact_guard"

============== 182 passed, 2257 deselected, 1 xfailed in 27.08s ===============
```
All core finalization and safety tests pass.

---

## 12. Final Verdict

### A. System Health
**`HEALTHY`**
The system is stable, validation gates operate correctly, and the test suite is green.

### B. Provider Migration Readiness
**`READY_FOR_SHADOW_PROVIDER_SPIKE`**
The existing multi-provider architecture allows the insertion of a shadow client with zero disruption.

### C. Betting Safety
**`SAFE`**
All safety gates are locked. Shadow candidates cannot impact wagers, and historical records are protected.

### D. Final Recommendation
**Proceed to SportsGameOdds shadow provider spike.**
The repository is ready. Begin by adding the shadow client and configuring it strictly as a non-wagering candidate lane.

---

## 13. Remaining Work

*   **MUST FIX before SportsGameOdds Phase 6A**:
    *   Decouple the direct `BallDontLieClient` calls inside `courtvision_ai.py` and route all player props/injuries through `ProviderManager` to ensure complete provider independence.
*   **SHOULD FIX before Phase 6A**:
    *   Create a provider comparison validation audit script.
*   **CAN WAIT**:
    *   Expand SGO mock payloads testing.
*   **DO NOT TOUCH**:
    *   Elite board, Kelly stakes allocation engine, and settled history database files.
