# Phase 14B - Old Project Comparison Matrix

## Executive Summary

- Old-project ideas worth keeping: legacy golden scenarios, the shadow-run checklist mindset, provider normalization/name recovery tests, and diagnostic-only views of minutes, injury role relief, and market/context factors.
- Ideas that should not be ported live: synthetic missing-market/fair-line rows, old team-market expansion into bettable boards, live adaptive threshold changes, and broad injury/minutes relief that changes eligibility without fresh validation.
- Already covered by CourtVision: package-owned prediction pipeline, BallDontLie normalization, provider fallback/provenance, injury usage/rebound/defensive boosts with caps, player_points low-line/strong-OVER guards, directional side edge, odds freshness gates, points-only Kelly lock, Power Rating/game context diagnostics, operator_card, daily_summary, and quality_summary.
- Biggest blind-copy risk: old logic could turn diagnostic or synthetic estimates into live elite/Kelly candidates, especially low-line player_points OVERs and unsupported team/moneyline markets, causing bankroll-facing drift without grading support.

Old-project sources inspected:

- `courtvision_ai.py`
- `MIGRATION_PHASE1.md` through `MIGRATION_PHASE5.md`
- `REFACTORING_SUMMARY.md`
- `README.md`
- `SYSTEM_OPERATING_MODEL.md`
- `SHADOW_RUN_CHECKLIST.md`
- `tests/legacy/test_runtime_golden.py`
- `tests/legacy/test_phase6_refinements.py`
- `tests/legacy/test_phase8_attribution.py`
- `tests/legacy/test_courtvision_ai_legacy_fix.py`

Current CourtVision sources inspected:

- `courtvision/pipeline/predict_pipeline.py`
- `courtvision/data/candidates.py`
- `courtvision/data/bdl_odds_adapter.py`
- `courtvision/data/normalization.py`
- `courtvision/clients/provider_manager.py`
- `courtvision/runtime_markets.py`
- `courtvision/runtime_selection.py`
- `courtvision/runtime_audit.py`
- `courtvision/scoring/*`
- `courtvision/injuries/*`
- `courtvision/context/*`
- `courtvision/ratings/*`
- `courtvision/projection/recalibration.py`
- `courtvision/reporting/player_points_inflation_audit.py`
- `courtvision/reporting/projection_bias_attribution.py`
- `courtvision/reporting/projection_calibration_shadow.py`
- `scripts/write_operator_card.py`
- `scripts/write_daily_summary.py`
- `scripts/run_kelly_stakes.py`
- `docs/live_vs_shadow_map.md`

Pre-existing runtime outputs, caches, and old `.claude` worktree folders were not treated as sources for this audit.

## Comparison Matrix

| Category | Old Project Finding | Current CourtVision Equivalent | Gap | Recommendation | Migration Risk | Suggested Phase |
|---|---|---|---|---|---|---|
| Minutes modeling | `courtvision_ai.py` uses `min_avg`, `min_recent`, a clamped recent-minutes factor, minutes-based confidence, and injury-driven threshold relief. It does not show actual_minutes, starter/bench status, rotation tracking, or a live blowout minutes model. | Candidate rows carry `minutes_avg` and `minutes_recent`; `runtime_selection.py` has minutes gates; `scoring/confidence.py` and `scoring/penalties.py` penalize weak/volatile minutes; manual context is passive; Power Rating blowout risk is diagnostic. Phase 13B explicitly marks `actual_minutes` as absent. | Old logic does not fix Phase 13B's missing-field problem. The real gap is actual_minutes and postgame minutes-error coverage, plus starter/bench/rotation provenance. | Do not port old minutes relief live. Use it as a checklist for a minutes availability audit that measures projected-vs-actual minutes and field coverage. | `NEEDS_REWRITE`; diagnostic-only portions are `SAFE_TO_PORT_AS_DIAGNOSTIC`. | Phase 15A |
| Usage redistribution | Old code computes team injury impact, usage_boost, rebound_boost, defensive_event_boost, role_factor, and dynamic threshold relief when teammates are out. | `courtvision/injuries/injury_engine.py` already contains capped usage/rebound/defensive boosts, teammate absence notes, own-injury reductions, confidence-uplift damping, and injury boost caps. `courtvision/injuries/realism.py` adds low-line/role safeguards. | Neither old nor current model identifies exact beneficiaries through rotation/starter data. Old threshold relief could over-promote low-line OVERs if copied live. | Treat old usage-relief behavior as already mostly covered. Add only shadow attribution later, after minutes availability is measured. | Mostly `ALREADY_EXISTS`; live threshold relief is `LIVE_RISK_HIGH`. | Later, not next |
| Pace adjustment | Old player projections include opponent allowance adjustments clamped around league context. Old team totals and moneyline use team offense/defense blends and home edge. | `courtvision/context/game_context.py` creates pace, defense, rest, spread, implied-total, alignment, and caution fields in passive mode. `courtvision/context/game_strength.py` and Power Rating add competitiveness/blowout diagnostics. High-caution OVER exposure can be blocked from elite/Kelly. | Old pace logic is more live, but less audited. Current context is safer and more operator-visible. | Keep pace/matchup diagnostic. Do not let old multipliers alter projections until graded shadow evidence proves value. | `SAFE_TO_PORT_AS_DIAGNOSTIC`; live projection multiplier is `LIVE_RISK_HIGH`. | Phase 15D later |
| Projection realism | Old golden tests include low-line injury OVER dampeners, injury boost caps, board diagnostics, and guard reasons. | Current runtime has player_points strong-OVER guard, elite points risk guard, low-line realism dampeners, edge denominator floors, recalibration shadow, fragility diagnostics, and Phase 13B inflation audit. | The remaining question is whether LOW_LINE_UPSIDE_INFLATION_CONFIRMED should become a live guard. Old code does not answer that safely. | Reuse old cases as test-only regression scenarios. Do not port old live dampener formulas. | `ALREADY_EXISTS` plus `SAFE_TO_PORT_AS_TEST_ONLY`. | Phase 15C later |
| Directional edge calibration | Old docs describe adaptive thresholds and feedback loops; legacy tests cover asymmetric qualification and moneyline gates. | Current pipeline computes `side_edge` and `side_edge_pct`, validates direction for player_points, and Kelly prefers positive directional edge. Projection calibration and bias attribution are shadow/reporting only. | Current calibration is still mostly diagnostic by market/side/context. Old adaptive thresholds are too risky for live promotion. | Keep old edge examples as tests. Do not port live adaptive threshold adjustment. | Test cases: `SAFE_TO_PORT_AS_TEST_ONLY`; adaptive thresholds: `DO_NOT_PORT` / `LIVE_RISK_HIGH`. | Later, after audits |
| Presentation / operator output | Old `SHADOW_RUN_CHECKLIST.md` is useful for required fields, candidate volume checks, anomaly conditions, weekly aggregation, and sign-off. Old Telegram/text output is less structured. | Current `operator_card`, `daily_summary`, and `quality_summary` already show run health, provider/live status, Kelly, board counts, watchlists, review reasons, context safety, and required artifact failures. | Current output could borrow a compact checklist-style "accept/review/block" summary, but not old formatting wholesale. | Port checklist ideas only as operator diagnostics or docs, not as betting logic. | `SAFE_TO_PORT_AS_DIAGNOSTIC`. | Later, docs/reporting only |
| Prop expansion | Old project contains or tests moneyline/team_total/team board logic and synthetic missing-market fills. It also handles more player markets in legacy code. | Current full-market candidate universe includes points, rebounds, assists, combos, threes, steals, and blocks. Elite defaults to `points_only`; Kelly is locked to `player_points`. Moneyline/team_total aliases and scoring code exist, but market support and live staking are intentionally constrained. | Validation/grading depth is not sufficient to recommend new live markets. Synthetic missing-market rows are especially unsafe. | No new markets from old code. Market status: threes = already supported/useful later; steals = already supported/useful later but not ready for live; blocks = already supported/useful later but not ready for live; turnovers = not ready; fantasy score = dangerous/bloat; game spreads = dangerous/bloat; totals/team_total = not ready; moneyline = not ready; rebounds/assists/combos = already supported in full-market/shadow, not Kelly. | Unsupported expansion is `LIVE_RISK_HIGH`; synthetic market fill is `DO_NOT_PORT`. | No implementation phase |
| Data quality / provider handling | Legacy code and tests cover BallDontLie player_id/name recovery, provider trace logging, failure diagnostics, and grading contracts. | Current `bdl_odds_adapter.py` normalizes player props to one actionable side per row, preserves raw fields, resolves player/team from lookup, records unresolved reasons, line source, vendor, game_id, and updated_at. `provider_manager.py` supports SportsDataIO/BallDontLie priority and per-domain fallback/provenance. | Current provider handling is stronger. Gap is not old provider logic; it is adding field-availability provenance for actual_minutes and context fields in audits. | Keep existing current adapter. Port only missing old tests if they catch regressions not already covered. | Mostly `ALREADY_EXISTS`; tests are `SAFE_TO_PORT_AS_TEST_ONLY`. | Phase 15A for minutes field provenance |
| Testing / validation | Legacy tests include golden board generation, low-line guards, injury boost caps, BDL player lookup, Phase 6 confidence/diversity, and Phase 8 attribution/miss classification. `SHADOW_RUN_CHECKLIST.md` gives operational validation patterns. | Current tests cover provider fallback, BDL adapter, game schema normalization, market expansion, Kelly, strong-OVER guard, player_points inflation audit, projection calibration shadow, Power Rating, quality summary, and operator card. | Some old tests are valuable as historical fixtures, but several target obsolete legacy APIs or old provider shapes. | Adapt only small, behavior-focused old scenarios into current package tests. Do not resurrect `courtvision_ai.py` behavior to satisfy legacy tests. | `SAFE_TO_PORT_AS_TEST_ONLY`; obsolete fixture expectations are `DO_NOT_PORT`. | After Phase 15A |
| Risk of migration | Old code mixes production scoring, synthetic projections, output generation, provider handling, grading, and optional adaptive ideas in one runtime. | Current CourtVision has separated package modules, explicit live vs shadow map, points-only elite/Kelly gates, and diagnostic/reporting boundaries. | Direct migration would collapse boundaries and silently alter bankroll-facing behavior. | Treat old project as a library of audit prompts and test cases, not source logic. | Broad live migration is `LIVE_RISK_HIGH`. | Phase 15A only |

## Top 5 Useful Ideas

1. Minutes availability and actual_minutes audit seed
   - Why useful: Phase 13B cannot fully diagnose minutes overprojection because `actual_minutes` is absent from available sources.
   - Where it would fit: reporting/diagnostics beside `player_points_inflation_audit.py`, plus provider/stat-field provenance.
   - Classification: shadow/review only.
   - Risk level: low if diagnostic-only; high if used to change gates.
   - Recommended next phase: Phase 15A.

2. Legacy low-line and injury-boost golden scenarios
   - Why useful: They capture known fragile player_points OVER failure modes without requiring live formula changes.
   - Where it would fit: focused tests around current `runtime_selection.py`, `injuries/realism.py`, and reporting outputs.
   - Classification: test-only.
   - Risk level: low.
   - Recommended next phase: after Phase 15A.

3. Shadow-run checklist concepts
   - Why useful: Candidate volume, missing fields, provider safety, required artifacts, and anomaly checks improve operator review.
   - Where it would fit: `quality_summary`, `operator_card`, or docs as a review checklist.
   - Classification: diagnostic/review only.
   - Risk level: low.
   - Recommended next phase: after data-field audit.

4. Usage redistribution attribution, not redistribution logic
   - Why useful: Old injury role-relief ideas can help explain whether teammate absences are over-weighted.
   - Where it would fit: a shadow attribution report grouped by injury impact, role bucket, minutes bucket, and line band.
   - Classification: shadow only.
   - Risk level: medium if it remains diagnostic; high if it modifies selection.
   - Recommended next phase: Phase 15B later.

5. Pace/matchup audit framing
   - Why useful: Old opponent allowance factors are a reminder to evaluate whether pace/defense context explains misses.
   - Where it would fit: Power Rating/game_context diagnostics and projection-bias reports.
   - Classification: shadow only.
   - Risk level: medium.
   - Recommended next phase: Phase 15D later.

## Things Not To Port

- Synthetic missing-player-market rows and fair-line generation into live boards or Kelly.
- Old team totals, spreads, moneyline, or game-line logic as bettable markets without separate validation and grading support.
- Adaptive thresholds or feedback adjustments that change gates automatically.
- Dynamic injury/minutes threshold relief as live selection logic.
- Old pace/opponent-allowance multipliers as live projection changes.
- Any old logic that widens elite/Kelly beyond the current points-only safety posture.
- Legacy Telegram/text output as a replacement for operator_card/daily_summary.
- Obsolete legacy API expectations from tests when they conflict with the package-owned runtime.

## Suggested Roadmap

Recommended next phase: **Phase 15A - Minutes Availability Audit**.

This is the highest-leverage next move because it addresses the concrete Phase 13B blocker: missing actual_minutes and minutes field provenance. The old project has useful minutes/role heuristics, but it does not supply the missing actual-minute truth set. A diagnostic-only audit should first answer what minutes fields are available, where they come from, how often they are missing, and whether projected minutes explain player_points misses. Only after that should CourtVision consider usage redistribution, low-line guard changes, or pace/matchup projection adjustments.

