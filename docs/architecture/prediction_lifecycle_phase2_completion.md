# Prediction Lifecycle Phase 2 Completion Report

> **Corrective status, 2026-07-25:** the two blockers recorded by the initial
> acceptance audit were remediated and re-audited. The current verdict is
> **PASS WITH KNOWN PRE-EXISTING FAILURES**. The original implementation and
> test history below is retained; section 24 records the corrective delta.

## 1. Summary

Phase 2 implements an opt-in, shadow-only immutable publication foundation for
the canonical NBA runtime. It records globally unique runs, exact published
Elite-board rows, deterministic prediction identities when resolvable,
content-addressed contemporaneous evidence, immutable hash-chained JSONL
segments, code/model/config provenance, idempotency, and board-to-ledger
reconciliation.

The operational CSV/runtime pipeline remains authoritative. No selection,
threshold, Elite, Kelly, bankroll, grading, settlement, provider, lock-buffer,
dashboard, launcher, scheduled-task, or historical behavior was changed.
DuckDB and relational storage were not added.

## 2. Files created

```text
courtvision/lifecycle/__init__.py
courtvision/lifecycle/clock.py
courtvision/lifecycle/canonical.py
courtvision/lifecycle/identity.py
courtvision/lifecycle/models.py
courtvision/lifecycle/evidence.py
courtvision/lifecycle/provenance.py
courtvision/lifecycle/writer.py
courtvision/lifecycle/reconciliation.py
courtvision/lifecycle/publication.py
courtvision/lifecycle/verify.py
courtvision/lifecycle/schemas/event_envelope_v1.json
courtvision/lifecycle/schemas/run_manifest_v1.json
courtvision/lifecycle/schemas/prediction_published_payload_v1.json
tests/test_lifecycle_canonical_identity.py
tests/test_lifecycle_evidence_writer.py
tests/test_lifecycle_publication_reconciliation.py
tests/test_lifecycle_runtime_integration.py
docs/architecture/prediction_lifecycle_phase2.md
docs/operations/lifecycle_shadow_runbook.md
docs/architecture/prediction_lifecycle_phase2_completion.md
```

## 3. Files modified

```text
courtvision_ai.py
```

The pre-existing untracked Phase 1 audit and audit directory were read but not
modified.

## 4. Integration boundary

`courtvision_ai.py:main()` starts a shadow run identity immediately before the
actual prediction execution when the feature flag is enabled. It invokes the
publication adapter only after `_write_cli_outputs()` returns successfully,
using `output_paths["elite_board"]`.

This boundary observes the existing protected actionable Elite board after its
current selection/exposure logic. The adapter hashes and reads the on-disk
artifact; it never rewrites or adds columns to the board.

## 5. Identity v1

`identity_schema_version=1`.

- `market_subject_key` hashes sport, league, CourtVision event, participant,
  market, selection, and bookmaker identities.
- `prediction_key` adds the canonical numeric line.
- `prediction_id` hashes `prediction_key`, `prediction_run_id`, and identity
  schema version.
- IDs use namespaced canonical JSON v1 plus SHA-256.
- Known market aliases and a minimal explicit bookmaker table are normalized.
- Unknown bookmaker/event/player/market/selection/line inputs remain
  `UNRESOLVED`; they are not guessed and do not affect canonical selection.

## 6. Canonical JSON v1

UTF-8, sorted keys, compact separators, retained Unicode, stable nulls,
aware-datetime conversion to UTC with microseconds and `Z`, ISO dates, finite
numeric values only, and explicit rejection of sets/arbitrary objects/naive
datetimes/non-finite floats. SHA-256 is used for payload, file, evidence,
identity, event, and segment hashes.

## 7. Event envelope v1

The frozen envelope implements all approved identity, sequence, timestamp,
actor, correlation, idempotency, payload, source, provenance, hash-chain, and
future correction fields.

Implemented event types:

```text
RUN_STARTED
RUN_COMPLETED
RUN_FAILED
PREDICTION_PUBLISHED
```

`SHADOW_RECONCILIATION_COMPLETED` is schema-reserved but not emitted.
Settlement event types were not added.

## 8. Run manifest

The manifest captures UUID run identity, SHADOW mode, optional reason/parent,
UTC start/completion, operating date/timezone, Git SHA/dirty/fingerprint,
config hash, available model/calibration hashes and IDs, Python/dependency
fingerprint, input-manifest hash, and reproducibility level. Unknown semantic
versions remain null.

## 9. Segment layout

```text
data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>/
  events.jsonl
  run_manifest.json
  manifest.json
  COMPLETE
```

Evidence uses `data/lifecycle/evidence/objects/<prefix>/<sha256>.json`.
Reconciliation uses
`data/lifecycle/reconciliation/YYYY/MM/DD/<prediction_run_id>.json`.

## 10. Writer lock

One create-exclusive `data/lifecycle/.writer.lock` records lock ID, PID,
hostname, run ID, command, and aware UTC acquisition time. Acquisition is
bounded. Live/unverifiable owners fail busy. Same-host dead owners may be
recovered only after a process-liveness check. There is no bypass.

The lock is held only for validation, evidence publication, immutable segment
commit, or reconciliation report publication.

## 11. Idempotency

Resolved publication keys are
`PREDICTION_PUBLISHED:<prediction_id>`. Exact repeats return
`ALREADY_COMMITTED`. A changed payload/event for the same key returns
`IDEMPOTENCY_CONFLICT`; no prior bytes are overwritten. Run-level segment
retries compare a deterministic segment content hash.

## 12. Evidence captured

- exact complete published board row;
- entry market/line/odds/source state available on the board;
- schedule/game state available on the board;
- availability/manual/identity state available on the board;
- full board-row feature snapshot;
- model/config/calibration/Git/dependency manifest;
- exact board path, byte hash, size, row index/count, and publication time.

Sensitive keys are recursively redacted before object hashing/storage.

## 13. Evidence not currently available

- complete raw provider odds/schedule/injury responses at publication;
- reliable provider-reported timestamps for rows that do not already carry an
  aware timestamp;
- a canonical bookmaker on boards that omit bookmaker/vendor;
- complete pre-selection feature tensors and intermediate model inputs;
- semantic model/calibration/strategy/pipeline versions not supplied by the
  runtime;
- complete immutable schedule and availability observation tapes;
- untracked working-tree file content.

These remain null/unresolved rather than inferred.

## 14. Reconciliation

The reconciler reopens the board and committed events, verifies hashes, then
compares count, row index, complete exact row content, event/prediction
identity, and board SHA-256. Full-row comparison includes player/entity,
market, selection, line, odds, projection, optional probability, edge,
confidence, bookmaker/source, qualification, Kelly fields, and all other
published columns.

`PASS` is exact and resolved. `DEGRADED` preserves canonical operation when
shadow persistence/identity is incomplete. `FAIL` identifies an immutable
integrity conflict.

## 15. Feature flag

`COURTVISION_LIFECYCLE_SHADOW=1` enables the integration. Default/unset/`0`
keeps existing behavior and creates no lifecycle output. Shadow failures do
not change a successful canonical exit code. Disabling the flag is the safe
rollback; committed evidence is retained.

## 16. Security and sanitization

- credential environment variables are never captured;
- authorization/API-key/token/access-token/secret/password/cookie keys are
  redacted;
- a board with a secret-bearing header is rejected from shadow persistence
  without serializing the field value;
- common bearer/key patterns are redacted from recorded failure messages;
- only safe policy configuration is hashed;
- model artifact names/hashes/sizes are captured without absolute paths;
- board paths are repository-relative;
- Git fingerprints exclude output/lifecycle directories.

## 17. Test commands

Focused lifecycle:

```powershell
py -3.13 -m pytest `
  tests\test_lifecycle_canonical_identity.py `
  tests\test_lifecycle_evidence_writer.py `
  tests\test_lifecycle_publication_reconciliation.py `
  tests\test_lifecycle_runtime_integration.py `
  -q --basetemp=.pytest_phase2_2
```

Relevant canonical regressions:

```powershell
py -3.13 -m pytest `
  tests\test_artifact_overwrite_guard.py `
  tests\test_history_append.py `
  tests\test_history_tracking.py `
  tests\test_grading.py `
  tests\test_grading_runtime.py `
  tests\test_kelly.py `
  tests\test_schema_contracts.py `
  tests\test_game_status_gate.py `
  tests\legacy\test_runtime_golden.py `
  -q --basetemp=.pytest_phase2_existing
```

Broader validation was split to stay within the command time limit:

```powershell
$files = Get-ChildItem tests -File -Filter "test_*.py" |
  Where-Object { $_.Name -match "^test_[a-f]" }
py -3.13 -m pytest $files.FullName -q --basetemp=.pytest_phase2_af

# The same command was run for G-L, M, N-O, P-R, and S-Z.
py -3.13 -m pytest tests\stable -q --basetemp=.pytest_phase2_stable
py -3.13 -m pytest tests\experimental -q --basetemp=.pytest_phase2_experimental
py -3.13 -m pytest tests\legacy -q --basetemp=.pytest_phase2_legacy
```

## 18. Test results

- Focused lifecycle: **43 passed**.
- Relevant canonical regressions: **196 passed, 3 expected xfailed**.
- Broader repository, executed in bounded alphabetical/directory chunks:
  **4,044 passed, 31 expected xfailed, 9 skipped, 18 failed** out of
  4,102 collected.

All 18 broader failures are in unchanged NBA research evidence modules and
have the same environmental signature: Windows `PermissionError [WinError 5]`
while those modules atomically rename their own pytest temporary directories.
The affected files are:

```text
tests/test_nba_player_points_closing_evidence.py
tests/test_nba_player_points_evidence_writer.py
tests/test_nba_player_points_rehearsal_integration.py
tests/test_nba_player_points_research_runner.py
tests/test_nba_player_points_settlement_closing_binding.py
tests/test_nba_player_points_settlement_evidence.py
```

An isolated rerun of the closing-evidence file reproduced the same filesystem
condition: 35 passed, 1 skipped, and 5 permission failures. This is not a green
full-suite result and is reported as such. No source file involved in those
failures was modified by Phase 2.

## 19. Canonical parity results

Parity tests verify:

- lifecycle disabled preserves the current CLI success path and produces no
  lifecycle directory;
- the shadow hook occurs only after the successful actionable board write;
- `DEGRADED` shadow status does not change a successful canonical exit code;
- exact board bytes remain unchanged;
- representative prediction-history, pick/grading-history, and Kelly sentinel
  bytes remain unchanged;
- existing canonical runtime golden, artifact guard, history, grading, Kelly,
  schema, and current lock tests pass.

No bankroll-facing difference was observed.

## 20. Existing tests affected

No existing test expectation was changed or loosened. No production gate or
threshold was changed.

The canonical/risk-matched existing suite is green. The broader suite retains
18 Windows atomic-rename permission failures in unchanged research-only
modules, as detailed in section 18. Those failures do not show a changed
assertion or CourtVision behavior, but they prevent claiming a fully green
repository suite on this workstation.

## 21. Known limitations

- Bookmaker omission can produce a valid immutable event with unresolved
  prediction identity and `DEGRADED` reconciliation.
- Event/player identifiers still depend on the identifiers currently retained
  by the canonical board.
- Full raw contemporaneous provider inputs are unavailable at this late
  publication boundary.
- Reconciliation is a create-once report, not yet a ledger event.
- No correction-event workflow is implemented.
- `FULL` reproducibility is not claimed.
- The known scheduled lock-buffer behavior is intentionally unchanged.
- Legacy CSV history remains mutable operational history and is not migrated.

## 22. Unresolved identity/provenance fields

Depending on the board/run:

```text
canonical bookmaker ID
canonical event ID
canonical participant ID
canonical market ID for unknown aliases
provider-reported timestamp
model version
calibration version
strategy version
pipeline version
run reason
raw source snapshot hashes
```

## 23. Recommended Phase 3 scope

The highest-value next phase is prospective source-observation capture before
publication:

1. retain sanitized immutable raw market, schedule, and availability snapshots
   with provider and ingestion times;
2. strengthen canonical event/player/bookmaker crosswalk coverage without
   changing selection;
3. bind the full input/feature manifest to the run for `FULL` reproducibility;
4. append reconciliation as an event after its event-hash contract is approved;
5. design correcting/superseding events.

Any lock-policy fix, authoritative cutover, settlement events, ledger grading,
legacy migration, or analytical database projection requires a separate
explicit approval and is not part of this implementation.

## 24. Corrective remediation and re-audit (2026-07-25)

### 24.1 Blockers corrected

Flag-OFF dependency isolation now uses
`courtvision/shadow_lifecycle.py`. `courtvision_ai.py` imports only that small
adapter. The adapter evaluates `COURTVISION_LIFECYCLE_SHADOW` before
dynamically importing `courtvision.lifecycle.publication`.

- unset, `0`, and supported false spellings do not import or require
  `courtvision.lifecycle`;
- the OFF path creates no lifecycle root, acquires no lock, and performs no
  lifecycle initialization or reconciliation;
- enabled import failure raises the classified
  `ShadowLifecycleInitializationError`;
- the CLI emits `status=DEGRADED`, `stage=INITIALIZATION`, and
  `classification=LIFECYCLE_IMPORT_FAILURE`, then continues canonical
  prediction and artifact publication;
- enabled import errors are not silently suppressed.

Identity v1 now uses one whole-value, case-insensitive sentinel contract after
surrounding whitespace removal:

```text
UNKNOWN, UNK, N/A, NA, NONE, NULL, NAN, <NA>, MISSING,
NOT_AVAILABLE, NOT APPLICABLE, UNRESOLVED, TBD, -
```

Sentinel substrings do not match: `player-NA-42`, `event-none-7`, and similar
provider IDs remain valid. Exact valid expected-domain CourtVision event and
participant IDs are preserved unchanged. Wrong-domain, wrong sport/league,
malformed, empty-suffix, invalid-suffix, and sentinel-suffix CourtVision IDs
fail closed and are never double-prefixed. Unresolved required identities
retain null `market_subject_key`, `prediction_key`, and `prediction_id`, use
the existing deterministic run/row/payload-hash idempotency fallback, and
reconcile `DEGRADED` without changing the canonical board.

The directly related non-object writer-lock finding was also normalized:
valid JSON such as `[]` now fails closed as `LifecycleWriterBusyError` and is
not removed. Existing live/dead-owner lock policy was not changed.

### 24.2 Corrective file delta

Created:

```text
courtvision/shadow_lifecycle.py
tests/test_lifecycle_import_isolation.py
```

Modified:

```text
courtvision_ai.py
courtvision/lifecycle/identity.py
courtvision/lifecycle/publication.py
courtvision/lifecycle/writer.py
tests/test_lifecycle_canonical_identity.py
tests/test_lifecycle_evidence_writer.py
tests/test_lifecycle_publication_reconciliation.py
tests/test_lifecycle_runtime_integration.py
docs/architecture/prediction_lifecycle_phase2.md
docs/architecture/prediction_lifecycle_phase2_acceptance.md
docs/architecture/prediction_lifecycle_phase2_completion.md
docs/operations/lifecycle_shadow_runbook.md
```

No selection, projection, scoring, threshold, Elite, Kelly, bankroll,
provider, schedule, canonical lock-buffer, grading, settlement, void, history,
automation, dashboard, run script, or legacy data file was changed.

### 24.3 Tests added

The corrective tests cover:

1. fresh-process import with lifecycle imports blocked and the flag unset;
2. the same fresh-process import with flag `0`;
3. the same with false spelling `false`;
4. enabled blocked import classified as a visible `DEGRADED` initialization
   failure while the canonical CLI still returns success;
5. no OFF-path lifecycle filesystem side effects;
6. enabled loading with the lifecycle package available;
7. canonical CLI behavior with the flag off;
8. every required unknown sentinel, including case/whitespace variants;
9. legitimate IDs containing sentinel substrings;
10. exact valid namespaced event/participant preservation;
11. wrong-domain and malformed namespaced fail-closed behavior;
12. null prediction keys and `DEGRADED` reconciliation for unknown required
    identity;
13. deterministic unresolved-row idempotency fallback and unchanged board
    bytes;
14. documented busy-error classification for a non-object lock payload.

### 24.4 Corrective acceptance results

```text
Compilation and diff hygiene:
  py_compile passed
  git diff --check passed

Focused lifecycle/corrective:
  80 passed

Canonical/risk-matched:
  196 passed, 3 expected xfailed

Additional CLI/runtime-output/artifact-date/runtime-gate parity:
  26 passed

Known now=None lock-behavior non-change:
  direct behavior check passed
  courtvision/runtime_gates.py unchanged from HEAD
  courtvision/pipeline/predict_pipeline.py unchanged from HEAD
```

The complete single-process repository run collected 4,139 tests:

```text
4,078 passed, 31 expected xfailed, 8 skipped, 22 failed
```

Seventeen failures were in the documented NBA research environment cluster.
Five other nodes reported missing repository-relative source paths after a
working-directory leak in the long single-process run; all five passed
immediately in isolation, and their referenced production files are present
and unchanged.

The acceptance classification uses the same bounded, separate-process
alphabetical/directory methodology validated by the initial audit:

| Chunk | Corrective result |
|---|---:|
| top-level A-F | 947 passed |
| top-level G-L | 533 passed |
| top-level M | 671 passed |
| top-level N-O | 490 passed, 19 failed, 9 skipped |
| top-level P-R | 876 passed |
| top-level S-Z | 368 passed |
| `tests/stable` | 53 passed |
| `tests/experimental` | 64 passed, 20 expected xfailed |
| `tests/legacy` | 78 passed, 11 expected xfailed |
| **Aggregate** | **4,080 passed, 19 failed, 9 skipped, 31 expected xfailed (4,139 collected)** |

All 19 remaining chunked failures are confined to the six unchanged modules
already identified by the initial clean-baseline audit:

```text
tests/test_nba_player_points_closing_evidence.py
tests/test_nba_player_points_evidence_writer.py
tests/test_nba_player_points_rehearsal_integration.py
tests/test_nba_player_points_research_runner.py
tests/test_nba_player_points_settlement_closing_binding.py
tests/test_nba_player_points_settlement_evidence.py
```

They are the same nondeterministic Windows atomic-directory-rename/concurrency
class plus the already documented system-temp preview-policy and child-process
package-import environment cases. No lifecycle or canonical parity test
failed. The broad repository suite is not represented as green.

### 24.5 Evidence-object retention note

A segment failure after evidence publication can leave unreferenced immutable
objects. No garbage collection was added. Future cleanup requires an approved
retention window and safe reachability scan over all completed segments.
Objects must never be deleted merely because they currently appear
unreferenced.

### 24.6 Final verdict and stopping point

**PASS WITH KNOWN PRE-EXISTING FAILURES**

Both Phase 2 blockers are corrected, all lifecycle and canonical parity gates
pass, and no new attributable regression remains. Phase 3 was not started,
DuckDB was not installed, committed lifecycle data was not rewritten, and no
Git commit was created.
