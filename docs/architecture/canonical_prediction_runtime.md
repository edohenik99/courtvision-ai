# Canonical prediction runtime

Last verified: 2026-07-25

## Decision

Every supported workflow that creates new model-derived predictions crosses
`courtvision.prediction.application.PredictionApplicationService`.
Collection, grading, settlement, history, reporting, diagnostics, and
evaluation remain separate operational domains.

The application service owns orchestration only. It does not contain model
mathematics, thresholds, candidate admission rules, provider selection, or
sport-specific path policy.

## Runtime structure

```text
public entrypoint
    -> PredictionRequest
    -> PredictionApplicationService
        -> request validation and run ID
        -> sport/mode registry lookup
        -> lifecycle begin
        -> approved sport engine
        -> transactional publisher and shared manifest
        -> lifecycle completion or failure
        -> PredictionResult
```

The two approved engines are:

- NBA production: `_NBAPredictionEngine` in `courtvision_ai.py`. It calls the
  existing `CourtVisionAI._predict_internal()` implementation so the migrated
  route retains the established provider, projection, edge, board, and
  diagnostic behavior.
- MLB research: `_MLBHRResearchPredictionEngine` in
  `courtvision/sports/mlb/training/hr_research_baseline.py`. It calls the
  existing local-data logistic-regression calculation without promoting it to
  production.

`PredictionEngineRegistry` is an explicit `(sport, mode)` allowlist. An
unregistered combination fails before engine execution.

## Shared contracts

`PredictionRequest` carries the sport, ISO prediction date, mode, optional run
ID and output root, dry-run/overwrite choices, and entrypoint metadata.

`EnginePrediction` carries the engine's existing output object together with
provider/data provenance, model version, and an optional sport-engine outcome
status. The status lets a healthy sport-specific no-data result remain
distinct from application or lifecycle degradation.

`PredictionResult` carries the normalized request identity, run ID, overall
status, original outputs, artifact paths, provenance, lifecycle outcome,
manifest path, and an optional failure classification.

The public compatibility methods preserve their previous return shapes where
callers depend on them; the canonical CLI and UI consume `PredictionResult`.

## Supported entrypoints

### NBA production

```powershell
python courtvision_ai.py predict --sport nba --mode production `
  --prediction-date YYYY-MM-DD
```

The prior flag-oriented syntax remains accepted. `run_today.ps1`,
`run_today.bat`, `scripts/run_daily.py`, and the Streamlit prediction action
delegate to this boundary.

### MLB research

```powershell
py -3.13 courtvision_ai.py predict --sport mlb --mode research `
  --prediction-date YYYY-MM-DD
```

The canonical CLI resolves omitted paths without network access:

- model: the bundle with the latest `training_timestamp` under
  `outputs/research/mlb_hr_baseline/models` that passes the existing
  `load_model_bundle()` integrity contract; incomplete, corrupt, unrelated,
  and `.pytest_tmp*` directories are ignored;
- odds: `data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv`;
- output:
  `outputs/research/mlb_hr_baseline/daily_runs/YYYY-MM-DD`.

Explicit `--model-dir`, `--odds-csv`, and `--output-dir` values take
precedence. The default odds source is local-only; the command never calls a
paid API. `--dry-run` computes the same result without writing artifacts.

The existing `predict` and `run-daily-research` subcommands in
`hr_research_baseline.py`, and its public
`generate_daily_research_predictions()` function, are retained as
compatibility entrypoints. They delegate to the same application service.

MLB remains research-only, local-data-only, and isolated from NBA output
folders.

## Compatibility and legacy surfaces

- `CourtVisionAI.predict()` is a compatibility wrapper over the canonical
  service. Its engine adapter calls `_predict_internal()` directly to avoid
  recursion.
- `CourtVisionApplication.run_prediction()` delegates to the canonical
  service and emits a deprecation warning. Its grading methods remain for
  compatibility.
- `scripts/run_daily.py` is a deprecated CLI wrapper over
  `courtvision_ai.main()`.
- `courtvision.pipeline.runner.save_prediction_boards()` is retained for
  imports and tests, but uses the approved publication helpers and is not a
  live entrypoint.
- `CourtVisionPro` in `courtvision/engine.py` is explicitly `legacy-unused`
  and warns when its prediction method is invoked. It was not promoted because
  its separate writer and return behavior are not the active NBA path.

`courtvision/pipeline/predict_pipeline.py` remains the active package-owned NBA
calculation pipeline called by the monolith's internal implementation. It does
not own lifecycle or canonical publication.

## Lifecycle flow

When the lifecycle feature is enabled, `ShadowPredictionLifecycle` loads the
hooks lazily to preserve import isolation. It records structured identity:

- `entrypoint`
- `actor_id=prediction_application`
- `sport`
- `mode`
- `run_id`
- `prediction_date`
- `command`
- request metadata

The service owns the run ID and passes it to both the engine and lifecycle.
The lifecycle begins before engine execution. After the artifact transaction
commits, lifecycle publication records the primary artifact and terminal
result. Engine/publication exceptions trigger the lifecycle failure hook and
are re-raised, preserving CLI exit behavior.

If lifecycle is disabled, a successful publication returns
`lifecycle_status=DISABLED`. MLB dry runs do not start a publication
lifecycle. The optional observation capture adapter is currently NBA-shaped,
so it is not attached to MLB runs; MLB lifecycle publication still records and
reconciles the immutable prediction artifact. If lifecycle initialization or
completion degrades, the application result is `DEGRADED`; the Streamlit UI
does not show a success message.

MLB application statuses are:

- `PASS`: one or more model predictions were produced and any requested
  publication completed;
- `NO_DATA`: the local CSV contains no rows whose `commence_time` maps to the
  requested CourtVision operating date;
- `NO_ELIGIBLE_PREDICTIONS`: requested-date rows exist, but all consolidated
  rows were excluded by existing research eligibility rules;
- `DEGRADED`: lifecycle or dependency integrity was impaired or processing
  could not be guaranteed;
- `FAILED`: a terminal CLI validation, dependency, engine, or publication
  exception;
- `PROTECTED_NO_OP`: a verified immutable output package already exists.

`PROTECTED_NO_OP` does not start a new lifecycle run or acquire a publication
lock. It returns the existing artifact paths without altering their bytes.

The former real-run initialization `NameError` was caused by
`courtvision/lifecycle/publication.py` using `os.environ` without importing
`os`. The import is now present, and a positive integration test opens and
verifies the resulting lifecycle chain.

## Publication flow

Canonical writers use `courtvision.prediction.publication`.

1. Validate the requested date against each prediction artifact path.
2. Enforce create-once or explicit overwrite policy.
3. Serialize to a short, same-directory staging file.
4. Collect label, row count, byte size, and SHA-256 metadata.
5. Stage the application manifest in the same transaction.
6. Commit with `os.replace`.
7. Retain backups until lifecycle completion and final manifest update.
8. Finalize by deleting staging and backup files.

Any staging or commit exception rolls back files created or replaced by the
transaction. Sport adapters retain their existing output layouts:

- NBA uses `OutputLayoutPolicy` and the dated canonical board/report paths.
- MLB uses the immutable research-run directory and create-once
  `predictions.csv`.

The shared manifest records run identity, sport, mode, prediction date, model
version, entrypoint/command, provider or data provenance, lifecycle status,
artifact paths, row counts, hashes, and sizes.

The MLB prediction manifest and `exclusion_summary.json` also record
`exclusion_reasons` and these stage counts:

- all local CSV rows loaded;
- raw rows matching the requested operating date;
- rows passing the canonical HR market contract;
- rows after snapshot/player/market consolidation;
- feature-valid rows;
- final eligible rows.

The canonical date is derived from `commence_time` in
`America/Toronto`. Snapshot timestamps remain timing/leakage inputs and are
not silently substituted for the game operating date.

## Concurrency and overwrite safety

Non-dry runs acquire a create-exclusive lock keyed by output root, sport, mode,
and prediction date. A concurrent attempt fails with
`PredictionRunConflictError`. The Streamlit button also uses a session guard
and is disabled while its request is running.

The lock does not replace artifact guards. Existing NBA protections and MLB
create-once behavior still reject silent overwrites unless the established
explicit force option applies.

## Non-prediction boundaries

The following do not enter the prediction service:

- source collection and odds ingestion;
- result fetching, finalization, settlement, and grading;
- history reconstruction and completed-slate feedback;
- Kelly staking from already-published predictions;
- operator cards, summaries, and reporting-only refresh;
- diagnostics, audits, backtests, simulations, dry-run evaluators, and
  test-fixture creation.

Downstream scripts that formerly constructed `CourtVisionAI` for helper access
now use `courtvision.operations.CourtVisionOperations` or narrow helper
functions. That facade deliberately raises if `predict()` is called. It is a
transitional parity boundary: extracting provider/grading methods from the
legacy runtime is a separate change because those behaviors are production
sensitive.

The complete file-by-file classification is in
`prediction_entrypoint_inventory.md`.

## Adding another sport

1. Implement a `PredictionEngine` adapter with a unique `sport` and explicit
   supported modes. Keep model logic in the sport package.
2. Make `execute()` return `EnginePrediction` without owning application
   lifecycle.
3. Implement a publisher callback that uses the shared publication helpers,
   preserves the sport's path policy, and selects a primary lifecycle artifact.
4. Register the engine only in an approved public adapter.
5. Populate entrypoint, command, model version, and provider/data provenance.
6. Add deterministic engine and artifact parity tests.
7. Add the new engine, entrypoint, and writer to the explicit architecture
   allowlists.
8. Update the inventory and this document before treating the route as
   supported.

Do not register settlement, grading, or reporting commands as prediction
engines merely because they consume prediction rows.

## Enforcement

`tests/test_prediction_architecture.py` scans relevant non-test source with
explicit exclusions and documented allowlists. It enforces approved engine
implementations and writers, service delegation by live wrappers, the absence
of direct `CourtVisionAI.predict()` calls in scripts, downstream isolation,
Streamlit and MLB routing, non-generating MLB finalization/settlement, and
structured lifecycle identity.

`tests/test_prediction_application.py` covers service contracts, NBA output
and artifact parity, overwrite protection, publication rollback, and a real
lifecycle-enabled successful run. MLB deterministic parity and immutability
coverage lives in `tests/test_mlb_hr_research_baseline.py`.
