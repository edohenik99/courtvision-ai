# CourtVision Phase 6A.0 Deep System Scan Before Provider Migration

This audit is a read-only system scan before the SportsGameOdds provider migration. It intentionally does not change production code, betting logic, provider logic, Elite/Kelly logic, bankroll/staking logic, thresholds, `final_decision`, `pick_history.csv`, runtime artifacts, or historical artifacts.

No `run_today.bat` execution, grading for `2026-05-30`, repair script, live provider request, closed-slate regeneration, or `--override-date-integrity` run was performed.

Source state inspected:

- Branch: `main`
- Expected latest commit confirmed: `1c572ab Deduplicate bet readiness candidates`
- Initial `git status --short`: clean
- Initial `git diff --stat`: empty
- `git ls-files outputs/runtime`: no tracked runtime artifacts
- `git ls-files data/history`: no tracked history artifacts

## 1. Repository Structure Scan

| Layer | What it does | Migration relevance |
| --- | --- | --- |
| `courtvision/` | Core package for prediction, scoring, selection, artifact guards, runtime gates, context, models, and reporting support. | Main production surface. Avoid betting/scoring changes during provider work. |
| `courtvision/clients/` | External provider clients and fallback coordinator: `balldontlie_client.py`, `sportsdataio_client.py`, `provider_manager.py`. | Natural home for a `sportsgameodds_client.py` and provider fallback wiring. |
| `courtvision/data/` | Normalization and adapter layer: BDL odds adapter, games/odds/injury normalization, candidate shaping. | Highest provider-schema coupling risk. SportsGameOdds must map into existing canonical frames here. |
| `courtvision/pipeline/` | Prediction pipeline contracts, runner, stages, and candidate generation. Consumes normalized games, odds, injuries, baselines. | Mostly provider-agnostic once input frames satisfy contracts. |
| `courtvision/reporting/` | Operator, quality, research, shadow, incubator, no-bet, safe-action, and diagnostic report builders. | Mostly downstream artifact consumers; should remain reporting-only during migration. |
| `courtvision/calibration/` | Calibration buckets, grading summaries, and performance-oriented helpers. | Do not alter for provider migration unless provider comparison exposes contract gaps. |
| `courtvision/context/` | Game context, manual context, player identity resolution, rematch/same-opponent checks. | Critical identity surface for player/team mismatch between providers. |
| `courtvision/injuries/` | Injury modeling, volatility, realism, and adjustment support. | SportsGameOdds injury payloads should be shadow-only or fallback-backed first. |
| `scripts/` | Operational entrypoints, daily run orchestration helpers, grading/reporting scripts, audits, guards. | Entry and write ordering lives here; many scripts can write reports/history, so provider tests should use temp roots. |
| `tests/` | Broad regression suite, stable provider tests, artifact guards, schema contracts, reporting safety tests. | Good safety coverage, but missing SportsGameOdds-specific contract/parity tests. |
| `docs/` | Architecture docs, audits, readiness notes, maintenance docs. | This report lives here; no runtime behavior change. |
| `outputs/` | Runtime boards, operator reports, diagnostics, logs, and generated artifacts. | Do not create or regenerate during this scan. Not tracked by git in current state. |
| `data/history/` | Long-lived history CSVs such as real-money pick history and shadow/incubator histories. | Must remain untouched. Not tracked by git in current state. |

## 2. Entry Point Scan

Canonical flow:

- `run_today.bat` is the Windows convenience wrapper.
- `run_today.ps1` is the canonical daily orchestration script.
- `courtvision_ai.py` is the canonical live prediction CLI/runtime. Its header says orchestration belongs there, while new scoring/grading/filtering/market logic belongs under `courtvision/`.
- `scripts/run_daily.py` is a compatibility entrypoint that resolves a BDL key, calls `CourtVisionAI`, writes prediction boards, and can notify Telegram. It is not the main daily operator path.

Daily orchestration order in `run_today.ps1`:

1. Validate slate date and protect closed/past slates unless `-ForcePastDate` is used.
2. If protected prediction artifacts already exist for the date, print protected no-op guidance and exit without regenerating boards.
3. Fit baselines only if needed.
4. Run `courtvision_ai.py --prediction-date <date> --predict-only --verbose-outputs`.
5. Validate runtime outputs.
6. Run full-market sanity and candidate quality drift audits.
7. Run `scripts/run_kelly_stakes.py` from the Elite board.
8. Run grading/tracking helpers for pending and shadow histories.
9. Run Phase 4B shadow artifact orchestrator.
10. Write daily summary, quality summary, and completion state audit.
11. Run Phase 5 research artifacts with `--skip-operator-card`.
12. Write the operator card once.
13. Write the artifact manifest.

Reporting order:

- Primary prediction boards are written first by `courtvision_ai.py`.
- Kelly stakes are written after the Elite board.
- Daily/quality summaries and completion state follow.
- Research artifacts such as under visibility, shadow candidate lane, and shadow lane performance run before the final operator card refresh.
- `pre_game_finalization_guard.py` and `write_bet_readiness_report.py` are standalone reporting/finalization checks that consume the existing artifacts.

Provider data enters:

- Live runtime path: `CourtVisionAI.predict()` fetches games, odds, and injuries before normalizing and handing them to the pipeline.
- BDL-specific live path: `courtvision_ai.py` still contains an embedded `BallDontLieClient` with direct `get_games`, `get_odds`, and injury paths.
- Package provider path: `courtvision/clients/provider_manager.py` coordinates `SportsDataIOClient` and `BalldontlieClient` by domain.
- Odds normalization path: BDL player props flow through `courtvision/data/bdl_odds_adapter.py` and then downstream pipeline/candidate logic.

Artifact write locations:

- Operator CSV/TXT outputs: `outputs/runtime/operator/`
- Diagnostic JSON outputs: `outputs/runtime/diagnostics/`
- Research diagnostics: mostly `outputs/runtime/operator/` plus paired JSON under diagnostics
- Histories: `data/history/`, only by history/grading/performance persistence scripts

Overwrite guards:

- `run_today.ps1` blocks full board regeneration when protected date artifacts already exist.
- `courtvision/artifact_guard.py` provides date and no-overwrite guards.
- `courtvision_ai.py` protects key prediction artifacts, including `elite_board`, `full_market_board`, `near_elite_review`, `sgp_board`, `stat_only_board`, `strike_board`, `predictive_lines_board`, `team_board`, and `near_miss_board`.
- `scripts/run_kelly_stakes.py` guards existing `kelly_stakes`.
- `scripts/write_operator_card.py` guards `operator_card` unless `--force` is passed.
- Some research/report files overwrite their own report outputs by design; they should not be used to refresh closed slates unless invoked with safe flags and an exact intent.

## 3. Provider/Data Source Scan

Current provider dependencies:

| Dependency | Modules | Notes |
| --- | --- | --- |
| BallDontLie API v1/v2 | `courtvision_ai.py`, `courtvision/clients/balldontlie_client.py`, `courtvision/balldontlie_auth.py`, `courtvision/data/bdl_odds_adapter.py` | Primary live dependency for games, odds, stats, and injuries in the current runtime. |
| BallDontLie Python SDK | `courtvision/clients/balldontlie_client.py`, `courtvision_ai.py` injury path | Used for injury fetches and SDK-backed data paths. |
| SportsDataIO API | `courtvision/clients/sportsdataio_client.py`, `courtvision/clients/provider_manager.py` | Already modeled as a fallback-capable provider returning internal dataclasses. |
| Environment variables | `BALLDONTLIE_API_KEY`, `BALLDONTLIE_BASE_URL`, `BALLDONTLIE_PER_PAGE`, `BALLDONTLIE_TIMEOUT`, `SPORTSDATAIO_API_KEY`, `SPORTSDATAIO_BASE_URL`, `NBA_PROVIDER_PRIORITY`, `DATA_PROVIDER_PRIORITY` | There are two provider-priority surfaces: `ProviderManager` defaults to SportsDataIO then BDL; `ProviderSettings.from_env()` defaults to BDL-only. |

Modules with BallDontLie-specific schema assumptions:

- `courtvision_ai.py`: embedded `BallDontLieClient`; direct `/games`, `/injuries`, and `NBA_V2/odds/player_props` assumptions.
- `courtvision/data/bdl_odds_adapter.py`: expects BDL flattened fields such as `player_id`, `prop_type`, `line_value`, `market.type`, `market.over_odds`, `market.under_odds`, `market.odds`, and `vendor`.
- `courtvision/data/normalization.py`: games normalization assumes nested `home_team` and `visitor_team`; odds normalization has BDL-compatible fallbacks such as `raw_market_name`, `prop_type`, `over_odds`, and `under_odds`.
- Legacy runtime tests still assert BDL URLs and auth diagnostics.

Provider-agnostic or mostly provider-agnostic modules:

- `courtvision/models.py` dataclasses: `Game`, `Team`, `Injury`, `PlayerInfo`, `PlayerGameStats`, `MarketProp`.
- `courtvision/clients/provider_manager.py`: per-domain fallback abstraction for games, players, stats, injuries, and odds.
- `courtvision/pipeline/predict_pipeline.py`: consumes normalized frames and baseline data.
- `courtvision/selection/operator_boards.py`, `courtvision/runtime_gates.py`, `courtvision/runtime_audit.py`: operate on canonical candidate/board fields.
- Reporting modules once boards are present.

Provider fallback behavior:

- `ProviderManager` tries configured priority order per domain.
- Injuries and odds degrade to empty lists if all providers fail.
- Games/stats/players raise if all providers fail.
- Status tracking records provider used, fallback usage, and per-domain success.

Provider smoke tests:

- `courtvision/balldontlie_auth.py` includes `smoke_test_games_api()` for a known BDL games date, but this scan did not call it.
- Tests cover BDL auth diagnostics, ProviderManager fallback, SportsDataIO primary/fallback behavior, BDL odds adapter schema, and player-name normalization.

SportsGameOdds plug-in point:

- Add `courtvision/clients/sportsgameodds_client.py` with the same public interface as current provider clients.
- Add `courtvision/data/sgo_odds_adapter.py` or a generic provider-odds adapter that emits the existing BDL adapter's canonical long-market schema.
- Register `sportsgameodds` in `ProviderManager._get_client()` and provider priority parsing.
- Keep SportsGameOdds shadow-only at first; do not route it into Elite/Kelly or `final_decision`.
- Add a provider comparison report that reads BDL-primary outputs and SGO shadow outputs side-by-side.

Provider abstraction gaps:

- `courtvision_ai.py` can bypass `ProviderManager` and use the embedded BDL client directly.
- The canonical odds frame is named and shaped around BDL (`bdl_odds_adapter.py`), even though it is effectively the downstream player-prop contract.
- Provider priority defaults differ between `ProviderManager` and `ProviderSettings`.
- SportsDataIO returns `MarketProp` dataclasses, but live runtime odds normalization still expects DataFrame columns from the BDL-shaped path.

## 4. Data Contract Scan

| Schema | Required columns or keys | Optional columns | Provider-specific columns | Downstream consumers | Failure risk if missing |
| --- | --- | --- | --- | --- | --- |
| Games | `game_id` or provider `id`, `date`, `status`, home team, visitor team, home/visitor abbreviations | scores, `datetime`, `game_status_bucket`, postseason flags | BDL nested `home_team`/`visitor_team`; SportsDataIO `HomeTeam`, `AwayTeam`, IDs | slate matching, opponent mapping, runtime game gates, candidate construction | Empty slate, incorrect opponent, game status lock failures |
| Odds/player props | `player_id`, `player_name`, `market_type`, `selection`, `line`, `odds`, `vendor`/`bookmaker`, `game_id`, team abbreviation, `line_source`, `unresolved_reason` | `updated_at`, raw market aliases, source team columns, over/under side prices | BDL `line_value`, `prop_type`, `market.type`, `market.over_odds`, `market.under_odds`, `market.odds` | odds normalization, candidate generation, identity gates, full market board | Empty boards, bad side selection, wrong odds math, identity quarantines |
| Player baselines | player identity, player/team, minutes, stat baselines/projections | current team overrides, role/usage metrics | Provider IDs are currently BDL-oriented | prediction pipeline, value engine, context checks | Projection fallback or player drop; provider ID mismatches |
| Injury | `player_id`, `player_name`, `team_id`/team, `team_abbreviation`, `status`, `description` | return date, normalized injury flags, rejection/enrichment reason | SDK object vs dict shape; provider status vocabulary | injury engine, minutes volatility, realism penalties | Missed injury adjustments, overstated confidence |
| Full market board | Minimum contract from `tests/schema_contracts.py`: `prediction_date`, `player_name`, `team`, `opponent`, `game_id`, `market_type`, `selection`, `line`, `odds`, `line_source`, `model_projection`, `edge`, `confidence`, `quality_score`, `selection_score`, `is_live_market`, `context_caution_level`, `context_pick_alignment`, `same_opponent_under_warning`, `manual_review_required`, `fragility_score`, `fragility_bucket`, `survivability_score`, `survivability_bucket` | identity diagnostics, rejection reasons, source lane, sportsbook line, raw fields, stale odds flags | none after normalization | daily summary, quality summary, research reports, bet readiness, operator card | Report crashes or incorrect safety status |
| Elite board | Same minimum contract as full market board | same as full market | none after normalization | Kelly runner, operator card, bet readiness | Direct block on stake calculation |
| Kelly stakes | `prediction_date`, `player_name`, `market_type`, `selection`, `line`, `american_odds`, `edge_pct`, `confidence`, `stake_fraction`, `stake_amount`, `kelly_eligible`, `skip_reason`, `manual_review_required`, `recommended_action`, `stake_policy`; runner minimally requires `odds`, `confidence`, and one of `side_edge_pct`/`edge_pct`/`edge` on input | stake caps, same-opponent warning, manual review reason, source identity fields | none after Elite board | operator card, bet readiness, history enrichment | Missing/zero stakes or fatal Kelly run |
| Shadow candidate lane | `prediction_date`, `source_artifact_date`, `source_board`, `research_lane`, `rank_score`, player/team/game fields, `market_type`, `selection`, `line`, `odds`, projection/edge/confidence/quality fields, historical metrics, `promotion_status`, `real_money_eligible`, `kelly_eligible`, `elite_eligible`, `shadow_only` | warnings, CLV/sample notes | none; must be post-board research schema | pre-game guard, bet readiness, shadow lane performance | Date mismatch blocks readiness; bad flags create betting-safety violation |
| Incubator board | `prediction_date`, `player`, `player_id`, `team`, `opponent`, `market_type`, `selection`, `line`, `odds`, `edge`, `confidence`, `quality_score`, context fields, source rejection fields, fragility/role fields, `incubator_status`, `incubator_reason`, `real_money_eligible` | manual review flag, same-opponent warning | none; derived from board rows | daily summary, bet readiness, incubator performance | Research lane visibility loss; should never become stakeable |
| Bet readiness inputs | Required: elite board, full market board, operator card, daily summary, pre-game finalization guard JSON, board diagnostics JSON. Optional: SGP, near-elite, incubator, shadow lane, Kelly stakes, quality summary | candidate lane details and duplicate examples | none | operator/audit status only | Incorrect `BETTABLE`/`RESEARCH_ONLY` status if required artifacts or safety fields are missing |

## 5. Artifact Flow Scan

| Artifact | Producer | Consumers | Timing/order | Overwrite guard | Safe pre-game regen | Safe after game starts | Real-money effect |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `full_market_board_YYYY-MM-DD.csv` | `courtvision_ai.py` `_write_cli_outputs` | research reports, daily/quality summaries, operator card, bet readiness | before Kelly and reporting | protected by run script and dataframe guard | Yes, before lock | No for closed/started slate | Indirect |
| `elite_board_YYYY-MM-DD.csv` | `courtvision_ai.py` `_write_cli_outputs` | Kelly, operator card, summaries, bet readiness | before Kelly | protected | Yes, before lock | No | Direct |
| `sgp_board_YYYY-MM-DD.csv` | `courtvision_ai.py` `_write_cli_outputs` | operator/reporting surfaces | alongside prediction boards | protected | Yes, before lock | No | Research/operator visibility, not Kelly |
| `kelly_stakes_YYYY-MM-DD.csv` | `scripts/run_kelly_stakes.py` | operator card, bet readiness, pick history enrichment | after Elite board | guarded by `guard_no_existing_artifact` | Yes, before bet execution | No | Direct staking surface |
| `daily_summary_YYYY-MM-DD.txt` | `scripts/write_daily_summary.py` | operator, bet readiness, operator card context | after boards/Kelly/shadow state | report write; has closed-slate-safe flags | Yes with correct mode | Only with closed-slate-safe/report-only intent | Reporting; can also update paper histories in normal mode |
| `quality_summary_YYYY-MM-DD.txt/json` | `scripts/write_quality_summary.py` and reporting module | operator card, bet readiness, diagnostics | after boards/Kelly | report write; board annotation can be disabled | Yes | Only with safe flags | Reporting/safety diagnostics |
| `operator_card_YYYY-MM-DD.txt` | `scripts/write_operator_card.py` | human operator, finalization checks, bet readiness | final operator surface | guarded unless `--force` | Yes if intentionally refreshed | Yes for reporting, but preserve locked artifacts | Direct operator decision support |
| `no_bet_funnel_report` | `scripts/write_no_bet_funnel_report.py` / `courtvision/reporting/no_bet_funnel.py` | operator/research | optional reporting | report write | Yes | Yes as reporting | No |
| `safe_action_discovery_report` | `scripts/write_safe_action_discovery_report.py` / reporting module | operator/research | optional reporting | report write | Yes | Yes as reporting | No; explicitly no promotion |
| `under_visibility_audit` | `scripts/write_under_visibility_audit.py` / reporting module | daily summary, operator card, guard | Phase 5 research | report write | Yes | Yes as reporting | No; says no threshold/betting changes |
| `shadow_candidate_lane` | `scripts/write_shadow_candidate_lane_report.py` / reporting module | guard, performance, bet readiness | after full market/near elite/incubator are available | report write | Yes | Yes as reporting if exact source date | No; flags false/shadow-only |
| `shadow_candidate_lane_performance` | `scripts/write_shadow_candidate_lane_performance.py` / reporting module | operator/research, guard | after shadow lane | report write; history persistence guarded by source-date integrity | Yes | Caution: can persist paper history if date-integrity passes | No real-money effect |
| `incubator_board` | `courtvision/reporting/incubator_board.py`, usually from daily summary path | daily summary, bet readiness, incubator performance | after full market/Elite are available | direct CSV write | Yes | Yes as paper-only if source date is exact | No; `real_money_eligible=False` |
| `bet_readiness_report` | `scripts/write_bet_readiness_report.py` | operator/audit | after pre-game guard | direct report write | Yes | Yes as reporting | Reporting-only; does not change `final_decision` |
| `pre_game_finalization_guard` | `scripts/pre_game_finalization_guard.py` | bet readiness, operator lock decision | final pre-lock check | direct report write | Yes | Yes as reporting | Reporting-only; validates safety |

## 6. Betting Safety Scan

Verified by static inspection and targeted tests:

- Research artifacts do not change `final_decision`; `write_bet_readiness_report.py` declares it does not change `final_decision`.
- Shadow candidate lane rows are written with `real_money_eligible=False`, `kelly_eligible=False`, `elite_eligible=False`, and `shadow_only=True`.
- Pre-game guard fails rows that have shadow lane date mismatches or any of the shadow flags set unsafely.
- Kelly reads the Elite board only and has its own hard blocks, including points-only market lock for non-player-points rows.
- Incubator rows are paper-only and only carry `real_money_eligible=False`; bet readiness treats missing Kelly/Elite flags as non-promoted.
- UNDER research is surfaced in reporting and shadow lanes, not promoted to stakeable picks.
- Bet readiness report is reporting-only and does not write `pick_history.csv`.
- Pre-game finalization guard is reporting-only and checks `pick_history.csv` only as an untouched/safety signal.
- `scripts/write_research_artifacts.py` itself orchestrates subprocesses and has tests confirming no `pick_history.csv` mutation; note that one subprocess, shadow candidate lane performance, can persist a separate shadow history when not dry-run and date integrity passes.
- No Phase 5 reporting module inspected changes Elite/Kelly thresholds. Under visibility explicitly reports threshold changes as blocked/no modification.

Safety warnings:

- `scripts/write_daily_summary.py` can update board annotations, market shadow history, paper Kelly history, and incubator history in normal mode. Use its closed-slate-safe and skip flags for locked dates.
- Research/performance scripts are safe for bankroll but are not universally no-write; they can write report files and paper-only histories when intentionally run.
- The closed-slate no-op in `run_today.ps1` is the main protection against accidental board regeneration for `2026-05-30`.

## 7. Provider Migration Readiness Scan

How hard would SportsGameOdds be to add?

- Shadow spike difficulty: moderate.
- Primary-provider switch difficulty: higher, because live odds/games paths still contain BDL-shaped assumptions.
- The existing `ProviderManager` makes client registration straightforward, but live `courtvision_ai.py` and odds normalization are not fully provider-neutral.

Modules needing new adapters or wiring:

- `courtvision/clients/sportsgameodds_client.py`
- `courtvision/data/sgo_odds_adapter.py` or a generic `provider_odds_adapter.py`
- `courtvision/clients/provider_manager.py` registration and priority handling
- Tests under `tests/stable/` and schema contract tests for SGO payloads
- A provider comparison reporting script/module

Schemas that must be mapped:

- SportsGameOdds games to canonical games frame
- SportsGameOdds player props to the current long-market odds schema
- SportsGameOdds player/team identity to BDL-oriented baseline identities
- SportsGameOdds injuries, if used, to the canonical injury frame
- Bookmaker/source/update timestamp fields to `vendor`/`bookmaker`/`updated_at`

Provider comparison report:

- No dedicated SportsGameOdds comparison report exists.
- Existing audits cover provider fallback and output schemas, but not cross-provider parity.
- Needed report should compare BDL-primary vs SGO-shadow by game, team, player identity, market, selection, line, odds, bookmaker, timestamp, and unmapped/drop reasons.

What should remain BallDontLie-primary:

- Current live prediction run.
- Historical baselines and ID joins.
- Final boards, Elite/Kelly, `final_decision`, and real-money histories until SGO parity is proven.

What should be shadow-only first:

- All SportsGameOdds player props.
- Any SportsGameOdds injuries until status mapping and identity coverage are validated.
- Provider comparison reports and diagnostics.

Tests needed before any provider switch:

- SGO raw fixture contract tests for games, odds, injuries, and missing/partial payloads.
- SGO adapter tests proving exact canonical output columns.
- ProviderManager tests for SGO first, BDL fallback, and SGO shadow-only mode.
- Player identity parity tests for suffixes, punctuation, team abbreviations, traded players, and missing IDs.
- Market naming tests for all supported prop markets and unsupported combo/milestone props.
- Odds format tests for American, decimal, plus/minus strings, missing odds, and stale timestamps.
- End-to-end shadow provider comparison test using temp runtime/history roots.

Migration readiness classification:

**READY_FOR_SHADOW_PROVIDER_SPIKE**

The system is ready to start a shadow-only SportsGameOdds spike. It is not ready for a primary provider switch until schema coupling and comparison coverage are addressed.

## 8. Test Coverage Scan

Strongest test areas:

- Artifact overwrite guards: `test_artifact_overwrite_guard.py`, `test_operator_card_overwrite_guard.py`, `test_kelly_artifact_overwrite_guard.py`.
- Bet readiness and finalization guard: `test_bet_readiness_report.py`, `test_pre_game_finalization_guard.py`.
- Research artifact orchestrator and no-pick-history safety: `test_research_artifact_orchestrator.py`, shadow lane tests, no-bet/safe-action tests.
- BDL odds normalization: `test_bdl_odds_adapter.py`.
- Provider fallback structure: `tests/stable/test_provider_manager.py`, `tests/stable/test_provider_sportsdataio_primary.py`.
- Grading and history behavior: grading runtime/backfill/history tests.

Weak test areas:

- No SportsGameOdds client or fixtures yet.
- Provider comparison is not covered by a dedicated report or test suite.
- The live `courtvision_ai.py` provider path is less abstracted than the package `ProviderManager` path.
- Raw provider schema contract tests are stronger for BDL than for alternate providers.
- End-to-end tests for provider dataclasses flowing into live DataFrame normalization are limited.

Missing provider abstraction tests:

- All provider clients must satisfy the same games/odds/injury public interface.
- All provider odds adapters must emit the same canonical long-market odds frame.
- Provider priority and configuration defaults should be asserted from one canonical settings path.

Recommended tests before SportsGameOdds integration:

1. `test_sgo_odds_adapter_required_columns`
2. `test_sgo_over_under_expands_to_one_row_per_side`
3. `test_sgo_missing_player_team_or_bookmaker_sets_unresolved_reason`
4. `test_provider_manager_sgo_shadow_does_not_replace_bdl_primary`
5. `test_provider_comparison_report_no_elite_or_kelly_write`
6. `test_sgo_identity_aliases_match_baseline_players`
7. `test_sgo_unsupported_combo_props_are_reported_not_promoted`

Targeted test run performed:

```text
py -3.13 -m pytest -p no:cacheprovider -k "bet_readiness or pre_game_finalization or research_artifact or operator_card or under_visibility or shadow_candidate or incubator or no_bet or grading or artifact_guard"
```

Result:

```text
182 passed, 2257 deselected, 1 xfailed in 16.06s
```

Pytest also printed an ignored Windows temp cleanup `PermissionError` for `pytest-current`; the test command returned exit code 0 and the repo worktree stayed clean.

## 9. Risk Register

| Risk | Severity | Likelihood | Affected files/modules | Why it matters | Recommended mitigation | Must fix before SGO shadow spike |
| --- | --- | --- | --- | --- | --- | --- |
| Provider schema mismatch | High | High | new SGO client/adapter, `courtvision/data/`, `courtvision_ai.py` | Wrong fields can empty boards or corrupt lines/odds. | Build fixture-based adapter tests before live use. | Yes, for SGO adapter output |
| Player identity mismatch | High | High | `courtvision/context/player_identity.py`, baselines, provider adapters | Dropped or misassigned players can create false edges. | Alias map plus exact mismatch diagnostics in provider comparison. | Yes |
| Team name/abbreviation mismatch | High | Medium | games normalization, identity gates, SportsDataIO/SGO adapters | Opponent and active-slate checks can fail. | Canonical team map tests for all NBA teams and provider variants. | Yes |
| Player prop market naming mismatch | High | High | `courtvision/markets/prop_types.py`, odds adapters | Unsupported markets can be dropped or mislabeled. | Explicit SGO market map with unsupported reason output. | Yes |
| Odds format mismatch | High | Medium | odds adapter, Kelly runner, value calculations | Decimal or string odds can break implied probability/staking. | Normalize to American odds and test malformed values. | Yes |
| Missing bookmaker/source fields | Medium | Medium | odds adapter, board diagnostics, provider comparison | Source provenance and dedupe can degrade. | Require `vendor`/`bookmaker` fallback and source diagnostics. | Yes |
| Stale runtime artifacts | High | Medium | `outputs/runtime`, `run_today.ps1`, report refresh scripts | Closed slates can be misread or refreshed from stale data. | Keep exact source date checks; avoid fallback-to-latest for locked slates. | No code blocker, but operationally critical |
| Closed-slate rerun risk | High | Low | `run_today.ps1`, `courtvision_ai.py` artifact guards | Regenerating locked boards would destroy decision memory. | Preserve no-op guard; do not use force flags casually. | Already guarded |
| Duplicate artifact writes | Medium | Low | `run_today.ps1`, `write_research_artifacts.py`, `write_operator_card.py` | Duplicate operator cards/readiness rows confuse lock state. | Current tests cover recent fixes; keep single-writer order. | No |
| History contamination | High | Medium | `scripts/write_daily_summary.py`, shadow/incubator performance scripts | Paper-only rows could pollute history if wrong root/date used. | Temp roots in tests; source-date integrity; explicit paper-only history files. | No blocker, but monitor |
| `pick_history.csv` safety | Critical | Low | grading/history scripts, reporting scripts | Real-money audit log must not be mutated by research. | Keep tests that assert report scripts do not write pick history. | Already covered for scanned reports |
| Unsupported combo props | Medium | Medium | prop type maps, Kelly, shadow lanes | Future provider may expose markets not supported by projections. | Report unsupported props; keep out of Elite/Kelly. | Yes for adapter mapping |
| Injuries unavailable or mismatched | Medium | Medium | injury providers, injury normalization, volatility | Missing injury context can overstate projections. | Keep BDL/SportsDataIO primary for injuries initially; shadow SGO injuries. | No for shadow props |
| Provider priority/config split | Medium | Medium | `ProviderManager`, `ProviderSettings`, `courtvision_ai.py` | Different defaults can make tests and live runs disagree. | Consolidate provider priority when moving beyond shadow. | No for shadow, yes before switch |
| BDL-shaped live odds path | High | High | `courtvision_ai.py`, `bdl_odds_adapter.py` | SGO cannot safely become primary without matching BDL-shaped frame. | Add generic canonical adapter or rename/wrap BDL adapter contract. | No for shadow, yes before switch |

## 10. Static Commands Run

Commands run as requested:

```text
git status --short
```

Result: empty/clean.

```text
git log -5 --oneline
```

Result:

```text
1c572ab Deduplicate bet readiness candidates
0c1682b Avoid duplicate operator cards and improve research names
97e6599 Add bet readiness report
e971dea Document architecture and pre-lock audit
0462159 Add pre-game finalization guard
```

```text
git diff --stat
```

Initial result: empty.

```text
git ls-files outputs/runtime
git ls-files data/history
```

Result: no tracked files in either location.

Requested ripgrep scan run:

```text
rg "balldontlie|BALLDONTLIE|player_props|odds|provider|kelly_eligible|real_money_eligible|shadow_only|pick_history|final_decision|source_artifact_date" courtvision scripts tests
```

The result set was large. Key findings are reflected in this report:

- BDL auth and smoke-test code is centralized in `courtvision/balldontlie_auth.py`.
- BDL odds/player prop assumptions are concentrated in `courtvision_ai.py`, `courtvision/clients/balldontlie_client.py`, and `courtvision/data/bdl_odds_adapter.py`.
- Provider fallback tests exist for SportsDataIO and BDL.
- `pick_history.csv` writes are concentrated in grading/history modules, not the scanned reporting-only guard/readiness modules.
- `source_artifact_date` checks exist in shadow lane, shadow lane performance, pre-game guard, and bet readiness.
- Shadow lane safety flags are explicitly written and tested.

Commands intentionally not run:

- `.\run_today.bat`
- `grade_completed_picks.py` for `2026-05-30`
- repair scripts
- delete commands
- live provider requests
- any command using `--override-date-integrity`

## 11. Optional Test Health

Targeted suite passed:

```text
182 passed, 2257 deselected, 1 xfailed in 16.06s
```

The run used `-p no:cacheprovider` to avoid creating `.pytest_cache`. No repo files were changed by the test run.

## 12. Final Verdict

### A. System Health

**HEALTHY_WITH_WARNINGS**

The repository is stable and the targeted safety suite is green. Warnings are for provider schema coupling and report scripts that can write paper-only histories when intentionally run.

### B. Provider Migration Readiness

**READY_FOR_SHADOW_PROVIDER_SPIKE**

SportsGameOdds can begin as a shadow-only provider spike. The system is not ready for SportsGameOdds as a primary provider until adapter contracts, identity parity, odds mapping, and comparison reporting are in place.

### C. Betting Safety

**SAFE_WITH_WARNINGS**

Elite/Kelly/final-decision boundaries are intact in the scanned modules. Warnings are operational: do not rerun closed slates, do not use force flags casually, and keep SGO out of Elite/Kelly until proven.

### D. Final Recommendation

**Proceed to SportsGameOdds shadow provider spike.**

Keep BallDontLie primary for live decisions. Add SportsGameOdds behind a shadow-only adapter and provider comparison report first.

## 13. Remaining Work

### MUST FIX before SportsGameOdds Phase 6A

- No existing production-code blocker must be patched before starting an isolated shadow spike.
- The first SportsGameOdds spike work must include a hard shadow-only boundary and adapter contract tests before any SGO output is trusted.

### SHOULD FIX before Phase 6A

- Add SGO adapter fixture tests for games, odds, injuries, missing fields, and unsupported markets.
- Add a provider comparison report that writes only reporting/diagnostic artifacts.
- Consolidate provider priority/config behavior before any primary-provider switch.
- Document the canonical odds schema independently from the BDL adapter name.

### CAN WAIT

- Refactor `courtvision_ai.py` fully onto `ProviderManager`.
- Rename `bdl_odds_adapter.py` to a provider-neutral contract module.
- Expand SportsDataIO live integration tests beyond fallback/mocks.

### DO NOT TOUCH

- Elite/Kelly thresholds.
- Kelly sizing, bankroll, or wager sizing logic.
- `final_decision` rules.
- `pick_history.csv`.
- Locked `2026-05-30` runtime artifacts.
- Provider switch-over to primary mode.

## Audit Conclusion

CourtVision remains stable after Phase 5J.2. The safe next step is a SportsGameOdds shadow provider spike with strict schema mapping, parity reporting, and no path into Elite, Kelly, bankroll, `final_decision`, or `pick_history.csv`.
