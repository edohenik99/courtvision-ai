# CourtVision Phase 1 Full Test Failure Triage — 2026-05-08

## Run baseline captured

- Full suite rerun command: `pytest -q`
- Result observed on 2026-05-09 UTC:
  - **1152 passed**
  - **32 failed**
  - **27 errors**
  - **31 xfailed**
- Net non-passing count: **59**
- Prior snapshot in request context was 23 failed / 4 errors; current baseline is materially worse and should be treated as **regression or environment/config drift since that snapshot**.

## Classification legend

- Categories: real production bug / stale test logic / fixture drift / schema drift / environment issue / obsolete/duplicate coverage / intentionally deferred failure.
- Production risk scale:
  - **P0** launch-blocking, bankroll-facing or core runtime pipeline break
  - **P1** high risk, adjacent to production-critical behavior
  - **P2** medium risk, support/reporting flows
  - **P3** low risk, legacy/experimental/tooling

---

## 1) Runtime scoring / selection

### Group R1 — Elite context gate tests erroring on initialization
**Tests (4):**
- `tests/test_elite_context_gate.py::test_high_caution_conflicted_over_excluded_when_safer_under_exists`
- `tests/test_elite_context_gate.py::test_all_high_caution_conflicted_overs_produce_empty_elite_board`
- `tests/test_elite_context_gate.py::test_suppressed_game_context_candidate_cannot_backfill_elite`
- `tests/test_elite_context_gate.py::test_player_points_elite_admission_records_context_gate_reason`

| Field | Assessment |
|---|---|
| Failure/error summary | Test setup errors before assertions due to `BALLDONTLIE_API_KEY` missing in `CourtVisionAI` init. |
| Affected modules | `courtvision_ai.py`, `courtvision/balldontlie_auth.py`, elite context gate path. |
| Likely root cause | Fixture/test harness drift after stricter API-key enforcement in constructor; tests not isolating network/provider auth dependency. |
| Classification | **fixture drift** + **environment issue** |
| Production behavior broken? | Not directly proven; indicates testability regression and constructor coupling risk. |
| Production risk | **P1** (touches runtime selection gates; default high until validated). |
| Recommended action | Update fixtures to inject stub key or mock `resolve_api_key`; keep gate assertions intact. |
| Confidence | High |

### Group R2 — Strong-over calibration failures
**Tests (3):**
- `tests/test_player_points_strong_over_calibration.py::test_strong_over_blocked_from_elite_replacement_backfill`
- `tests/test_player_points_strong_over_calibration.py::test_mild_over_can_replace_backfill`
- `tests/test_player_points_strong_over_calibration.py::test_under_and_non_pp_unaffected_in_backfill`

| Field | Assessment |
|---|---|
| Failure/error summary | Assertions in elite replacement/backfill calibration logic failing. |
| Affected modules | `courtvision/runtime_selection.py` (player-points risk guards/backfill), potentially `courtvision/pipeline/predict_pipeline.py`. |
| Likely root cause | Runtime selection logic drift vs expected calibration behavior OR stale expectations after policy edits. |
| Classification | **real production bug** (until disproven) |
| Production behavior broken? | Possibly yes; can alter elite pick composition and downstream Kelly inputs. |
| Production risk | **P0** |
| Recommended action | Investigate gate decision path with targeted tests + trace outputs; fix code if drift unintended, otherwise update tests with explicit policy changelog. |
| Confidence | Medium |

---

## 2) Kelly

- No direct Kelly-only test failures in current red set.
- Indirect risk: runtime selection failures (R2) can change Kelly candidate pool.

---

## 3) Grading / history

### Group G1 — `repair_pending_grades` dtype assignment failures
**Tests (9):**
- `tests/test_repair_pending_grades.py::test_stale_pending_shadow_rows_are_repaired`
- `tests/test_repair_pending_grades.py::test_final_rows_missing_actual_value_are_repaired`
- `tests/test_repair_pending_grades.py::test_fixture_rows_are_removed_and_do_not_affect_readiness_or_paper_reports`
- `tests/test_repair_pending_grades.py::test_all_completed_repairs_old_stale_pending_rows`
- `tests/test_repair_pending_grades.py::test_current_date_game_not_final_rows_remain_pending_open_game`
- `tests/test_repair_pending_grades.py::test_completed_rows_cannot_stay_plain_pending_without_reason`
- `tests/test_repair_pending_grades.py::test_all_completed_hit_miss_rows_cannot_have_blank_actual_value`
- `tests/test_repair_pending_grades.py::test_daily_summary_does_not_reset_repaired_shadow_rows`
- `tests/test_repair_pending_grades.py::test_same_opponent_repeated_under_warning_is_flagged`

| Field | Assessment |
|---|---|
| Failure/error summary | `TypeError` from pandas Arrow string dtype when code assigns integers (e.g., `0`, `1`) into string-typed columns via `.at[...] = value`. |
| Affected modules | `scripts/repair_pending_grades.py`; pandas typing boundary. |
| Likely root cause | **schema drift** + pandas runtime behavior change (stricter Arrow string assignment semantics). |
| Classification | **schema drift** |
| Production behavior broken? | Likely yes for repair flow in environments with Arrow string backing; prevents remediation of pending grades. |
| Production risk | **P1** (grading/history core). |
| Recommended action | Explicit column typing/casting before scalar writes; normalize numeric flags as strings or cast destination columns to nullable numeric types. |
| Confidence | High |

### Group G2 — market shadow combo grading failures
**Tests (4):**
- `tests/test_grade_market_shadow_history.py::test_combo_shadow_grading_updates_combo_rows_only`
- `tests/test_grade_market_shadow_history.py::test_daily_summary_preserves_graded_combo_shadow_rows_and_report_counts`
- `tests/test_grade_market_shadow_history.py::test_combo_shadow_grading_replace_existing_regenerates_combo_result`
- `tests/test_grade_market_shadow_history.py::test_combo_shadow_grading_does_not_modify_elite_or_kelly_outputs`

| Field | Assessment |
|---|---|
| Failure/error summary | Assertion failures in combo shadow grading expectations. |
| Affected modules | `scripts/grade_market_shadow_history.py`, `scripts/market_shadow_grading.py`, reporting interactions. |
| Likely root cause | Logic drift in combo-row filtering/update semantics, or fixture assumptions stale after grading changes. |
| Classification | **real production bug** (default high due grading/history scope) |
| Production behavior broken? | Potentially yes; could corrupt grading history integrity/report counts. |
| Production risk | **P1** |
| Recommended action | Reproduce each with focused pytest -k; inspect mutated rows/summary counters; patch grading update criteria if unintended. |
| Confidence | Medium |

---

## 4) Schema / output

### Group S1 — board construction trace compatibility failures
**Tests (7):**
- all failures in `tests/experimental/test_board_construction_trace.py` listed in run summary.

| Field | Assessment |
|---|---|
| Failure/error summary | Compatibility/optional-parameter and trace-stage output assertions failing. |
| Affected modules | Board construction trace helpers (legacy compatibility path in runtime board builder). |
| Likely root cause | Signature/output contract changed without synchronized tests, or tests capture deprecated experimental API. |
| Classification | **stale test logic** (primary), possible **schema drift** |
| Production behavior broken? | Unknown; mostly experimental compatibility surface. |
| Production risk | **P3** |
| Recommended action | Decide whether this API remains supported; if yes, restore compat contract; if no, mark deprecated and prune obsolete tests. |
| Confidence | Medium |

### Group S2 — candidate odds preservation failure
**Test (1):**
- `tests/test_candidate_builder.py::test_score_player_markets_preserves_over_under_odds_when_odds_is_missing`

| Field | Assessment |
|---|---|
| Failure/error summary | Candidate builder did not preserve expected over/under odds behavior when generic `odds` field absent. |
| Affected modules | `courtvision/data/candidates.py`. |
| Likely root cause | Schema/field fallback drift in odds column mapping. |
| Classification | **real production bug** |
| Production behavior broken? | Likely yes in partially populated odds payloads. |
| Production risk | **P1** (odds normalization/selection input). |
| Recommended action | Fix fallback mapping while preserving existing normalization rules. |
| Confidence | Medium |

### Group S3 — odds dataframe player-name false positive
**Test (1):**
- `tests/stable/test_odds_dataframe_player_name.py::TestOddsDataFramePlayerName::test_no_false_positives_for_player_name`

| Field | Assessment |
|---|---|
| Failure/error summary | Player-name parsing/matching produced false positives. |
| Affected modules | Odds normalization/parsing path (adapter/dataframe transform). |
| Likely root cause | Regex/tokenization loosened or edge-case fixture drift. |
| Classification | **real production bug** |
| Production behavior broken? | Possibly; mislabels market rows to wrong player. |
| Production risk | **P1** |
| Recommended action | Tighten player extraction matching + add adversarial fixtures. |
| Confidence | Medium |

---

## 5) Power Rating

- No direct power-rating-specific failures in current red set.
- Hidden coupling risk exists through grading/history and runtime-golden end-to-end tests.

---

## 6) Diagnostics / reporting

### Group D1 — runtime golden suite blocked by API key
**Tests (23 errors):**
- all `tests/legacy/test_runtime_golden.py::*` listed in run summary.

| Field | Assessment |
|---|---|
| Failure/error summary | Fixture setup fails; `CourtVisionAI` constructor raises missing API key before golden assertions execute. |
| Affected modules | `courtvision_ai.py`, `courtvision/balldontlie_auth.py`, legacy runtime golden harness. |
| Likely root cause | Constructor now hard-requires API key; golden tests expect offline deterministic mode with stubs. |
| Classification | **fixture drift** + **environment issue** |
| Production behavior broken? | Not directly; this is primarily test harness break. |
| Production risk | **P1** because these are broad integration regression sentinels. |
| Recommended action | Introduce test-only auth bypass fixture (stub key/env), keep assertions unchanged; ensure no live API dependency in golden tests. |
| Confidence | High |

---

## 7) Environment / tooling

### Group E1 — provider adapter tests
**Tests (2):**
- `tests/stable/test_provider_sportsdataio_primary.py::TestProviderClientAdapter::test_adapter_initializes_provider_manager`
- `tests/stable/test_provider_sportsdataio_primary.py::TestProviderClientAdapter::test_adapter_has_fallback_client`

| Field | Assessment |
|---|---|
| Failure/error summary | Provider adapter initialization/fallback client assertions failing. |
| Affected modules | Provider manager adapter path (SportsDataIO primary integration). |
| Likely root cause | Test environment lacks expected config/dependency injection; or adapter contract changed. |
| Classification | **environment issue** (primary), possible stale test logic |
| Production behavior broken? | Unknown. |
| Production risk | **P2** |
| Recommended action | Isolate provider client with mocks; assert contract without external credential/state dependency. |
| Confidence | Medium |

### Group E2 — team lookup tests blocked by API key constructor gate
**Tests (3):**
- all failures in `tests/test_team_lookup_defined.py` listed in run summary.

| Field | Assessment |
|---|---|
| Failure/error summary | Tests fail before team lookup assertions due to missing API key in `CourtVisionAI` constructor. |
| Affected modules | `courtvision_ai.py`, auth bootstrap path; team lookup logic unexecuted. |
| Likely root cause | Fixture drift after constructor auth hardening. |
| Classification | **fixture drift** + **environment issue** |
| Production behavior broken? | Unclear; test no longer probes intended behavior. |
| Production risk | **P2** |
| Recommended action | Replace constructor dependency with direct unit-level function/object setup or inject stub key in fixture. |
| Confidence | High |

---

## 8) Stale / legacy

### Group L1 — legacy compatibility regressions
**Tests (2):**
- `tests/legacy/test_courtvision_ai_legacy_fix.py::TestCourtVisionAILegacyFix::test_predict_runs_without_attribute_error`
- `tests/legacy/test_courtvision_ai_legacy_fix.py::TestCourtVisionAILegacyFix::test_run_daily_compatibility`

| Field | Assessment |
|---|---|
| Failure/error summary | Legacy compatibility assertions failing (details not isolated due full-run summary truncation). |
| Affected modules | `courtvision_ai.py` legacy execution path. |
| Likely root cause | Legacy shim drift after pipeline/auth changes. |
| Classification | **stale test logic** or **real production bug** (needs targeted rerun to disambiguate). |
| Production behavior broken? | Possibly if production still enters same legacy path. |
| Production risk | **P1** |
| Recommended action | Run two tests with `-vv` to capture exact assertion deltas; if behavior still required in prod, fix code not tests. |
| Confidence | Low-medium |

---

## Must-fix-before-public-launch (blockers)

1. **R2 strong-over calibration failures (3)** — elite selection safety behavior uncertain. (**P0**)  
2. **G1 repair_pending_grades dtype failures (9)** — grading repair path can hard fail under current pandas semantics. (**P1**)  
3. **G2 market shadow combo grading failures (4)** — grading integrity/report correctness at risk. (**P1**)  
4. **S2/S3 candidate odds and player-name parsing (2)** — odds normalization correctness risk. (**P1**)  
5. **D1 runtime golden harness auth break (23)** — broad integration signal currently blind; must restore deterministic testability before launch. (**P1**)  
6. **R1 elite context gate harness auth break (4)** — key runtime gate tests are not executing. (**P1**)  

## Safe-to-defer (conditional)

- Experimental board trace compatibility set (S1, 7 tests) if API is formally deprecated and downstream consumers are confirmed absent.
- Provider adapter contract tests (E1, 2 tests) if production provider path validated separately and failures are purely environment harness gaps.

## Architectural debt signals

- `CourtVisionAI` constructor performs auth gating too early for offline/unit/integration deterministic tests (testability anti-pattern).
- Grading/history scripts are fragile to dataframe dtype evolution (Arrow string behavior).
- Legacy/experimental test surfaces are mixed with production-critical coverage, obscuring true launch risk picture.

## Hidden launch risks

- Loss of meaningful integration coverage: 27 errors are setup/auth failures, not logic checks.
- Potential silent grading/report drift due dtype coercion assumptions.
- Selection safety policy drift (strong-over calibration) may impact bankroll-facing board composition despite high pass count.

---

## Ranked remediation roadmap

1. **Restore deterministic test harness execution** for runtime-golden + elite-context + team-lookup suites (fixture/env stubbing only, no policy changes).
2. **Fix `repair_pending_grades` dtype-safe writes** and rerun full `tests/test_repair_pending_grades.py`.
3. **Resolve strong-over calibration logic/test drift** with targeted policy trace validation.
4. **Fix combo shadow grading regressions** and verify summary/report invariants.
5. **Fix odds/player-name parsing regressions** and rerun stable odds tests.
6. Reassess legacy/experimental compatibility tests; prune or update intentionally.

## Stabilization order

1. Environment/fixture unblockers (auth-gated setup errors).  
2. Grading/history correctness.  
3. Runtime selection safety calibration.  
4. Odds normalization correctness.  
5. Legacy/experimental cleanup.

## Recommended Phase 2 entry point

Begin with **test harness unblocking + grading dtype stabilization**. This yields maximum signal quickly by converting many setup errors into actionable logic results while addressing a concrete production failure mode.

## Copy-paste Codex prompt (highest-priority only)

```text
CourtVision Phase 2A: High-Priority Stabilization Only

Scope:
1) Fix test harness setup drift so these suites execute logic offline (no live API dependency):
   - tests/legacy/test_runtime_golden.py
   - tests/test_elite_context_gate.py
   - tests/test_team_lookup_defined.py
2) Fix dtype-safe assignment regressions in scripts/repair_pending_grades.py causing ArrowStringArray TypeError.

Constraints:
- Do not change scoring/selection policy thresholds or Kelly math.
- Do not add new features.
- Use surgical diffs only.
- Preserve production auth behavior; any bypass must be test-only via fixture/mocking.

Validation:
- pytest -q tests/legacy/test_runtime_golden.py tests/test_elite_context_gate.py tests/test_team_lookup_defined.py
- pytest -q tests/test_repair_pending_grades.py
- Then rerun full pytest -q and report updated fail/error counts.

Deliverables:
- Commit with only harness/dtype fixes.
- Updated audit note with before/after counts and remaining highest-priority failures.
```
