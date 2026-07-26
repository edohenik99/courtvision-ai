# Testing And Validation

## Test Inventory

| Item | Value |
| --- | ---: |
| Test files under `tests/` | 293 |
| Framework | Pytest |
| Fixtures | CSV/JSON fixtures under `tests/fixtures/`, including MLB packs and game result samples |
| Tests run during this audit | 0 |

No tests were run because the user requested a read-only audit and forbade commands that might alter caches, outputs, datasets, logs, or live-service state unless verified safe. Static inspection was used instead.

## Coverage Areas Observed

| Area | Example tests/files | What they likely prove |
| --- | --- | --- |
| CLI/runtime | `tests/test_cli.py`, runtime golden/legacy tests | CLI and runtime contracts. |
| Providers/adapters | `test_bdl_odds_adapter.py`, `stable/test_provider_manager.py`, `test_api_nba_client.py` | Provider normalization and client behavior under mocks/fixtures. |
| Candidate/selection | `test_candidate_builder.py`, `test_selection_modules.py`, `test_cap_enforcement.py` | Board selection and gates. |
| Kelly/risk | Kelly and exposure-related tests | Stake sizing/cap behaviors. |
| Operator reports | `test_artifact_manifest.py`, `test_daily_summary_no_slate_safe.py`, `test_completion_state_audit.py` | Report writing and no-slate safety. |
| MLB HR collector/results | `test_theoddsapi_live_hr_collector_contract.py`, `test_courtvision_mlb_nightly_pipeline.py`, `test_fill_live_hr_results_from_mlb_statsapi.py`, `test_grade_live_hr_results.py` | Collector contracts, finalizer decisions, result filling, grading. |
| MLB historical research | MLB fixtures/tests under `tests/fixtures/mlb` and `test_mlb_*` | Dataset/readiness/crosswalk behavior. |
| Evidence workflows | `test_append_evidence_*`, `test_create_evidence_day0_manifest.py` | Evidence manifest/ledger scripts. |

## Runtime Validators

| Validator | Input | Output | Role |
| --- | --- | --- | --- |
| `scripts/validate_runtime_outputs.py` | NBA runtime artifacts | validation log/status | Guards daily outputs. |
| `scripts/audit_full_market_sanity.py` | full-market board | audit report | Detects suspicious market board state. |
| `scripts/audit_candidate_quality_drift.py` | candidate artifacts | drift audit | Detects quality drift. |
| `tools/run_live_hr_daily_check.py` | HR master/run log | validation output | Offline HR data-quality check. |
| `tools/validate_live_hr_data.py` | HR CSVs | validation output | Snapshot/master quality. |
| `tools/check_live_hr_results_coverage.py` | HR master/results | ready/not-ready coverage | Blocks HR grading when incomplete. |

## What Passing Tests Would Not Prove

| Limitation | Reason |
| --- | --- |
| Profitability | Unit tests do not prove real-world edge. |
| Provider availability | Mocked tests cannot prove current subscriptions or quota. |
| Scheduler health | Tests do not prove Windows Scheduled Tasks are installed or running. |
| Reproducibility | Local ignored outputs and raw data can differ by machine. |
| Bankroll readiness | Requires prospective evidence, not only code correctness. |
| Correct official pick performance | HR grader currently grades observations unless official pick layer is added. |

## Unverified Behaviors

| Behavior | Why unverified |
| --- | --- |
| Full current pytest suite pass/fail | Not run. |
| Live provider availability as of 2026-07-11 | No live API calls were made. |
| Scheduled task installation/state | Did not query or modify Windows scheduler. |
| Dashboards render correctly | Not launched. |
| End-to-end NBA live run today | Not run. |
| End-to-end MLB collector today | Not run to avoid API credits. |

## Validation Recommendations

1. Keep collector and API tests mocked by default.
2. Separate "pure read validation" from writers and name them clearly.
3. Add CI jobs for canonical no-live-service contracts.
4. Add scheduler dry-run validation that does not call APIs or write production data.
5. Add official-pick identity tests before any MLB HR live-pick launch.
6. Add performance-report tests that distinguish observations from official picks.

