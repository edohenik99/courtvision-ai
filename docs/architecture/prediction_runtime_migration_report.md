# Prediction runtime migration report

Date: 2026-07-25

## Outcome

NBA production and MLB home-run research prediction generation now share one
application boundary:
`courtvision.prediction.application.PredictionApplicationService`.

The migration preserves the existing sport calculations behind adapters. It
does not alter model mathematics, thresholds, candidate ranking, provider
selection, injury handling, Kelly rules, grading rules, or output locations.
No live or paid API was used during development. No historical or canonical
production artifact was overwritten.

### MLB canonical CLI hardening

The remaining MLB usability and healthy-zero classification work was completed
after the architecture migration:

- `--model-dir`, `--odds-csv`, and `--output-dir` are optional on the canonical
  CLI and resolve to validated local defaults;
- the latest-model resolver sorts valid bundles by `training_timestamp` and
  ignores corrupt, incomplete, unrelated, and `.pytest_tmp*` directories;
- the canonical local odds default is
  `data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv`;
- the default output is
  `outputs/research/mlb_hr_baseline/daily_runs/YYYY-MM-DD`;
- explicit CLI paths retain highest priority;
- healthy zero-prediction runs return `NO_DATA` or
  `NO_ELIGIBLE_PREDICTIONS`, not `DEGRADED`;
- existing verified immutable output returns `PROTECTED_NO_OP` with its
  artifact paths and without starting lifecycle work;
- `exclusion_reasons`, stage counts, and `exclusion_summary.json` make
  eligibility outcomes observable without changing model or feature
  mathematics.

The final simple command is:

```powershell
py -3.13 courtvision_ai.py predict --sport mlb --mode research `
  --prediction-date YYYY-MM-DD
```

The requested date uses the CourtVision operating date obtained by converting
`commence_time` to `America/Toronto`; snapshot collection date is not used as a
substitute.

## Issues and root causes

1. **Orchestration was attached to entrypoints.** The CLI, daily script,
   Streamlit action, legacy application object, and MLB research command could
   initiate prediction work through different call paths. Shared guarantees
   therefore depended on the caller rather than on the operation.
2. **NBA calculation, application control, and writing were interleaved.**
   `CourtVisionAI.predict()` contained the active calculation path, while CLI
   output writing and lifecycle calls lived elsewhere in the monolith.
3. **Duplicate abstractions were not equivalent.**
   `courtvision/application.py` was a legacy pipeline facade with direct
   `runtime.predict()` calls and no sport registry. `CourtVisionPro` in
   `courtvision/engine.py` had a separate writer and was already designated
   non-canonical by ADR-001. `predict_pipeline.py` was the active calculation
   component, while `pipeline/runner.py` was a compatibility writer.
4. **MLB research was a real but isolated prediction workflow.** Its public
   function calculated probabilities and immediately published immutable
   output without shared run identity, lifecycle, or application manifest.
5. **Lifecycle identity was CLI-specific.** Actor and command fields assumed
   `courtvision_ai.py`, preventing an accurate shared application identity for
   UI, compatibility, and MLB entrypoints.
6. **Lifecycle initialization contained a real defect.**
   `courtvision/lifecycle/publication.py::_run_reason()` accessed
   `os.environ` without importing `os`. Feature-enabled integration reached a
   `NameError`; degraded-mode handling masked it as a nonfatal initialization
   problem.
7. **Publication controls were distributed.** NBA dataframe/JSON/text writers,
   the pipeline compatibility writer, and MLB CSV/manifest writers did not
   share one transaction, artifact metadata contract, or rollback mechanism.
8. **Downstream scripts depended on the full runtime.** Grading and history
   tools constructed `CourtVisionAI` for provider, normalization, and helper
   methods, coupling non-prediction work to a prediction-capable object.
9. **The UI lacked application-level concurrency semantics.** It could guard a
   button locally, but there was no shared sport/date process lock and status
   handling could not distinguish successful publication from degraded
   lifecycle completion.

## Implemented architecture

The canonical flow is:

```text
CLI / PowerShell / daily wrapper / Streamlit / MLB compatibility command
    -> PredictionRequest
    -> PredictionApplicationService
    -> explicit sport+mode registry
    -> lifecycle begin
    -> NBA production adapter OR MLB research adapter
    -> staged publication transaction and shared manifest
    -> lifecycle completion/failure
    -> PredictionResult
```

The application service owns request normalization, run ID, registry
selection, lock acquisition, lifecycle coordination, publication coordination,
manifest creation, provenance, status classification, rollback, and exception
propagation. Sport engines retain calculation logic.

## Active prediction-producing entrypoints

- `courtvision_ai.py predict --sport nba --mode production`
- the existing flag-only NBA `courtvision_ai.py` syntax
- `run_today.ps1` and `run_today.bat`, through the supported NBA CLI
- `scripts/run_daily.py`, as a deprecated wrapper over the supported NBA CLI
- the Streamlit `Run Predictions` action
- `courtvision_ai.py predict --sport mlb --mode research`
- `hr_research_baseline.py predict`
- `hr_research_baseline.py run-daily-research`
- `generate_daily_research_predictions()` for MLB compatibility callers
- `CourtVisionAI.predict()` and
  `CourtVisionApplication.run_prediction()` as programmatic compatibility
  wrappers

Only `_NBAPredictionEngine` and `_MLBHRResearchPredictionEngine` are approved
active engine adapters.

## Compatibility and legacy disposition

- `CourtVisionAI.predict()` delegates to the application service, preserves its
  mapping return shape, and enters the approved NBA internal method without
  recursion. Its no-board programmatic contract uses a no-artifact publisher
  and disabled publication lifecycle; live CLI/UI publication uses the full
  lifecycle adapter.
- `CourtVisionApplication.run_prediction()` delegates to the service and emits
  a deprecation warning. Its grading surface remains unchanged.
- `scripts/run_daily.py` emits a compatibility warning and delegates to
  `courtvision_ai.main()`.
- `save_prediction_boards()` remains import-compatible but is deprecated and
  uses shared publication helpers.
- `CourtVisionPro.predict()` remains `legacy-unused` and warns. It was not
  deleted because proving equivalence of its different output contract is
  outside this migration.
- MLB's public function and subcommands remain for compatibility, but all
  prediction generation delegates to the research application adapter.

## Non-prediction boundaries

The migration deliberately leaves collection, odds ingestion, result fetching,
settlement, grading, Kelly staking over existing predictions, reporting-only
refresh, operator-card refresh, history reconstruction, diagnostics, audits,
simulations, backtests, dry-run evaluation, and test fixtures outside the
prediction service.

The five identified grading/history scripts now use the restricted
`CourtVisionOperations` facade or narrow helpers. Its `predict()` method fails
closed. The facade still composes the legacy runtime internally for exact
provider/grading parity; extracting those methods is a separate production-risk
change.

See `prediction_entrypoint_inventory.md` for the exact file-by-file
classification.

## Files changed

### Canonical prediction package

- `courtvision/prediction/__init__.py`
- `courtvision/prediction/contracts.py`
- `courtvision/prediction/application.py`
- `courtvision/prediction/registry.py`
- `courtvision/prediction/publication.py`

### NBA, lifecycle, and compatibility paths

- `courtvision_ai.py`
- `courtvision/application.py`
- `courtvision/engine.py`
- `courtvision/pipeline/runner.py`
- `courtvision/lifecycle/identity.py`
- `courtvision/lifecycle/publication.py`
- `courtvision_streamlit_app.py`
- `run_today.ps1`
- `scripts/run_daily.py`

### MLB and downstream isolation

- `courtvision/sports/mlb/training/hr_research_baseline.py`
- `courtvision/operations.py`
- `scripts/backfill_grading.py`
- `scripts/grade_market_shadow_history.py`
- `scripts/history_tracking.py`
- `scripts/prefill_actual_feedback.py`
- `scripts/update_game_results_history.py`

### Tests and documentation

- `tests/test_prediction_application.py`
- `tests/test_prediction_architecture.py`
- `tests/test_mlb_hr_research_baseline.py`
- `tests/test_injury_context_defined.py`
- `tests/test_team_lookup_defined.py`
- `docs/architecture/prediction_entrypoint_inventory.md`
- `docs/architecture/canonical_prediction_runtime.md`
- `docs/architecture/prediction_runtime_migration_report.md`

Existing untracked audit documents and scan CSV/text files were treated as user
artifacts and were not edited. `.claude/worktrees` was not modified.

## Tests added or updated

`tests/test_prediction_application.py` adds:

- deterministic old-writer versus canonical NBA output parity;
- core prediction artifact byte-hash and name parity;
- overwrite rejection;
- rollback after an injected publication failure;
- a feature-enabled real lifecycle run and chain verification;
- run identity, request record, manifest, artifact hash, and terminal status
  assertions.

`tests/test_prediction_architecture.py` adds explicit source allowlists for
approved engines, public wrappers, downstream isolation, canonical writers,
MLB boundaries, and structured lifecycle identity.

`tests/test_mlb_hr_research_baseline.py` adds old-internal versus canonical
research parity for rows, probabilities, edges, feature identities, immutable
CSV bytes, and SHA-256 metadata.

It also covers MLB `PASS`, `NO_DATA`, `NO_ELIGIBLE_PREDICTIONS`, structured
exclusion totals, operating-timezone date filtering, latest-valid-model
selection, canonical local odds/output defaults, explicit override priority,
structured dependency failure, immutable `PROTECTED_NO_OP`, diagnostics
publication, and terminal lock release. Existing NBA application and
architecture tests remain unchanged and passing.

The injury-context and team-lookup regression tests now inspect
`_predict_internal()`, the approved active NBA implementation, because public
`predict()` is intentionally a thin service wrapper.

## Principal commands run

Read-only discovery and review used `git status --short`, `git diff`,
`rg --files`, targeted `rg -n` call-site/writer scans, and source inspection.
No live runtime command was executed.

The principal validation commands were:

```powershell
py -3.13 -m pytest `
  tests/test_lifecycle_runtime_integration.py `
  tests/test_lifecycle_publication_reconciliation.py `
  tests/test_lifecycle_canonical_identity.py `
  tests/test_lifecycle_observations.py `
  tests/test_lifecycle_import_isolation.py `
  tests/test_prediction_application.py -q

py -3.13 -m pytest `
  tests/legacy/test_runtime_golden.py `
  tests/test_predict_pipeline.py `
  tests/test_operator_fixture_smoke.py `
  tests/test_prediction_application.py `
  tests/test_prediction_architecture.py `
  tests/test_mlb_hr_research_baseline.py -q

py -3.13 -m pytest tests -q `
  --basetemp=.pytest_tmp_prediction_full_2

py -3.13 -m pytest `
  tests/test_prediction_application.py `
  tests/test_prediction_architecture.py `
  tests/test_mlb_hr_research_baseline.py `
  tests/test_lifecycle_runtime_integration.py `
  tests/test_lifecycle_publication_reconciliation.py `
  tests/test_lifecycle_canonical_identity.py `
  tests/test_lifecycle_observations.py `
  tests/test_lifecycle_import_isolation.py `
  tests/test_injury_context_defined.py `
  tests/test_team_lookup_defined.py -q

py -3.13 -m py_compile <all changed Python source files>
git diff --check
```

Pytest was run outside the filesystem sandbox after sandboxed temporary
directory creation produced permission errors. All test-only temp roots
created by these commands were removed after validation.

## Validation results

All migration-critical gates pass:

- Lifecycle-focused integration:
  `136 passed in 30.50s`.
- NBA/MLB deterministic parity, architecture enforcement, golden runtime,
  pipeline, and operator fixture smoke:
  `94 passed, 3 xfailed in 23.76s`.
- MLB research unit and parity suite:
  `12 passed`.
- Downstream grading/history isolation set:
  `59 passed`; its one compatibility-injection expectation was updated and
  then passed in isolation.
- Updated wrapper-source, application, and architecture set:
  `18 passed in 5.69s`.
- Final combined prediction, MLB parity, lifecycle, architecture, and
  active-engine regression gate on the completed source:
  `164 passed in 35.10s`.

The broadest safe local command completed with:

```text
4146 passed, 9 skipped, 31 xfailed, 29 failed in 427.94s
```

The two migration-caused failures were stale AST expectations that searched
the public wrapper for internal engine variables. After updating them to
inspect `_predict_internal()`, both pass as part of the 18-test result above.

The remaining broad-run failures were investigated rather than hidden:

- Sentinel tests and the lifecycle schema test passed when rerun in the
  isolated cluster.
- The remaining player-points evidence failures occur in unchanged modules at
  `Path.rename()` with Windows `PermissionError: [WinError 5]`.
- An isolated rerun produced `334 passed, 8 skipped, 24 failed`; a second run
  using a verified short temp root produced `220 passed, 8 skipped, 15
  failed`. The changing test set and identical Windows rename error indicate
  environment/filesystem flakiness, not deterministic prediction assertions.

An initial sandboxed lifecycle run also produced pytest temporary-directory
permission errors. The same lifecycle tests passed after using the approved
outside-sandbox pytest execution.

No live API command was run.

## Behavior-parity evidence

### NBA

The deterministic fixture executes the previous internal engine plus previous
writer and the canonical service plus adapter/publisher. It compares:

- dataframe row counts, columns, order, and values;
- selections, confidence, edge, elite eligibility, board membership, and
  rejection-related columns present in the fixture outputs;
- the compatibility output mapping;
- every core prediction artifact name and byte hash;
- overwrite failure when the dated artifact already exists.

The parity case passes. No scoring or selection source was changed.

### MLB research

The deterministic local fixture executes
`_generate_daily_research_predictions_internal()` and the new public canonical
path. It compares prediction rows, model probabilities, market/fair-odds and
edge fields when present, feature identity, deterministic prediction IDs,
`predictions.csv` bytes, and the recorded SHA-256. The parity and create-once
tests pass. The engine remains registered only for `mlb/research`.

## Lifecycle result

The feature-enabled positive integration test starts a real application run in
a temporary lifecycle root, creates the initial segment, records the
application-scoped request, publishes the board, records
`PREDICTION_PUBLISHED` and `RUN_COMPLETED`, reopens the chain, verifies it, and
asserts `PASS` rather than `DEGRADED`.

The missing `os` import is fixed; the positive path no longer raises
`NameError`. Existing disabled and degraded behavior remains covered.

## Known remaining risks

- The complete Windows suite is not green because unrelated player-points
  evidence directory-renames intermittently fail with `WinError 5`. Those
  modules were not changed, but the filesystem/antivirus interaction should be
  resolved before using a single full-suite green run as a release gate.
- `CourtVisionAI._predict_internal()` remains large. Further extraction would
  carry model-behavior risk and should occur only with additional golden
  fixtures.
- `CourtVisionOperations` still constructs the legacy runtime for exact
  downstream parity. It cannot call prediction, but narrower provider and
  grading services would reduce initialization cost.
- `CourtVisionPro` and legacy pipeline contracts remain in the tree for
  compatibility. Architecture tests prevent them from becoming approved live
  paths silently.
- Transaction rollback is covered for injected local publication failures.
  Process termination or host failure during the small interval between
  artifact commit, lifecycle append, and final manifest replacement still
  relies on the existing lifecycle reconciliation tooling.
- No live provider smoke test was performed, by design.

## Rollback

No commit was created. To roll back only this migration:

1. Restore the tracked files listed in **Files changed** from the pre-migration
   revision.
2. Remove only the newly added canonical package, operations facade, new tests,
   and three architecture documents listed above.
3. Do not remove the pre-existing untracked audit directory or scan/baseline
   files.
4. Re-run the pre-migration targeted lifecycle, NBA golden, and MLB research
   suites.

Because the worktree contained user-owned untracked artifacts before the
migration, do not use `git reset --hard` or a broad clean command. Restore
explicit paths only.
