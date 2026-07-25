# Prediction Lifecycle Phase 2 Acceptance and Regression Audit

> **Current corrective verdict, 2026-07-25:**
> **PASS WITH KNOWN PRE-EXISTING FAILURES**. Sections 1-18 preserve the initial
> failed acceptance audit. Section 19 records the remediation and corrective
> re-audit that supersedes the initial release verdict.

**Audit date:** 2026-07-25  
**Repository:** `C:\dev\Sport_Project1`  
**Audited baseline:** clean local clone of `c2d2171acd87e22bb69012223afdfc6c4e567be3`  
**Scope:** Phase 2 shadow publication only; Phase 3 was not started

## 1. Initial verdict (preserved)

**FAIL**

No prediction, selection, Elite, Kelly, bankroll, provider, injury, schedule,
existing-lock, grading, settlement, void, manual-review, automation, or legacy
history content difference was observed. The shadow publication boundary,
segment transaction, tamper detection, evidence verification, cross-process
writer exclusion, idempotency, and reconciliation checks passed.

Phase 2 nevertheless fails two explicit acceptance requirements:

1. **Flag-OFF isolation is incomplete.** `courtvision_ai.py` imports
   `courtvision.lifecycle.publication` at module import time. With
   `COURTVISION_LIFECYCLE_SHADOW=0`, importing `courtvision_ai` still fails if
   the lifecycle package is unavailable. The flag defaults off and produces
   no lifecycle files, locks, events, or reconciliation when the package is
   present, but the requirement says the lifecycle directory/package must not
   be required when the flag is off.
2. **Unknown identity sentinels are not fully fail-closed.**
   `derive_publication_identity()` correctly leaves `None`, blank, `nan`,
   `none`, `null`, and `<na>` unresolved, but literal `UNKNOWN` event and
   participant values are accepted as resolved identifiers
   (`...:event:UNKNOWN`, `...:participant:UNKNOWN`). This violates the
   requirement that unknown canonical identity values not be silently treated
   as real identity.

Both defects are shadow-infrastructure contract defects. Neither changed a
live pick or bankroll-facing result during this audit, but each is a release
blocker under the approved acceptance gates.

The broad NBA research failures are not the reason for the `FAIL` verdict.
They reproduce as an unstable Windows filesystem/environment class on the
clean pre-Phase-2 baseline and involve files byte-identical to `HEAD`.

## 2. Exact Git diff scope

### 2.1 Repository state and boundary

At audit start, `git status --short --untracked-files=all` showed one modified
tracked file and the new lifecycle implementation/tests/docs. The only tracked
production diff is:

| Pre-existing file | Diff | Classification | Acceptance assessment |
|---|---:|---|---|
| `courtvision_ai.py` | 80 additions, 0 deletions, five hunks | **potentially operational** | Shadow integration only in intent, but the unconditional top-level lifecycle import changes the flag-OFF dependency boundary and is a blocker. |

The Phase 2-created files are:

```text
courtvision/lifecycle/__init__.py
courtvision/lifecycle/canonical.py
courtvision/lifecycle/clock.py
courtvision/lifecycle/evidence.py
courtvision/lifecycle/identity.py
courtvision/lifecycle/models.py
courtvision/lifecycle/provenance.py
courtvision/lifecycle/publication.py
courtvision/lifecycle/reconciliation.py
courtvision/lifecycle/verify.py
courtvision/lifecycle/writer.py
courtvision/lifecycle/schemas/event_envelope_v1.json
courtvision/lifecycle/schemas/prediction_published_payload_v1.json
courtvision/lifecycle/schemas/run_manifest_v1.json
tests/test_lifecycle_canonical_identity.py
tests/test_lifecycle_evidence_writer.py
tests/test_lifecycle_publication_reconciliation.py
tests/test_lifecycle_runtime_integration.py
docs/architecture/prediction_lifecycle_phase2.md
docs/architecture/prediction_lifecycle_phase2_completion.md
docs/operations/lifecycle_shadow_runbook.md
```

Classification:

| Files | Classification |
|---|---|
| `courtvision/lifecycle/**` | lifecycle integration only; shadow storage has no production reader |
| `tests/test_lifecycle_*.py` | test only |
| Phase 2 architecture/completion/runbook documents | documentation only |

`docs/architecture/prediction_lifecycle_audit.md` is the Phase 1 input, not a
Phase 2-created file. The separately untracked
`docs/audits/courtvision_full_system_audit/**` directory is also outside the
Phase 2 change set. Git cannot encode creation chronology for untracked files;
this exclusion is supported by the documents' phase labels, content, and the
Phase 2 inventory. No file in those two pre-existing untracked scopes was
edited by this acceptance audit.

### 2.2 Bankroll and operational scope

The actual diff contains no change to:

- prediction/model projection, edge, confidence, quality, scoring, ordering,
  Elite qualification, rejection, Kelly, bankroll, or exposure logic;
- provider fetching/precedence, odds normalization, injury handling, schedule
  gating, or existing lock code;
- grading, settlement, void, manual-review, or history writers;
- run scripts, PowerShell/batch files, Task Scheduler, dashboards, or UI;
- legacy history schemas or semantics.

The integration starts an optional shadow context immediately before
`ai.predict()`, invokes shadow publication only after `_write_cli_outputs()`
returns, and records a non-publication failed-run segment only on an enabled
canonical failure. No lifecycle result is read by prediction, selection,
grading, or bankroll code.

## 3. Publication-boundary verification

| Case | Observed result | Valid `PREDICTION_PUBLISHED` |
|---|---|---|
| 1. prediction succeeds, board write succeeds, shadow commit succeeds | Canonical board bytes unchanged; segment `COMMITTED`; reconciliation `PASS`; event order `RUN_STARTED`, `PREDICTION_PUBLISHED`, `RUN_COMPLETED` | yes |
| 2. prediction succeeds, board write succeeds, ordinary shadow commit fails | Canonical board bytes unchanged; structured/logged `DEGRADED`; canonical CLI exit remains 0; no committed segment | no |
| 3. prediction succeeds, board write fails | Canonical CLI exits 1; enabled lifecycle segment contains only `RUN_STARTED`, `RUN_FAILED` | no |
| 4. prediction fails before board publication | Canonical CLI exits 1; enabled lifecycle segment contains only `RUN_STARTED`, `RUN_FAILED` | no |
| 5. successful board contains zero Elite rows | Segment contains only `RUN_STARTED`, `RUN_COMPLETED`; reconciliation `PASS` with zero expected/committed publication rows | no fabricated prediction |

A missing board passed directly to the publication adapter returns `FAIL` and
does not create a publication segment. Failure-injection searches found no
orphan committed publication events and no visible incomplete segment.

Evidence objects are published before the final segment transaction. A later
segment failure can therefore leave an unreferenced content-addressed evidence
object, but it cannot leave a committed orphan event. This is non-authoritative
shadow storage and should eventually have an explicit safe garbage-collection
policy.

## 4. Identity verification

Identity v1 persists `identity_schema_version=1` in identities, envelopes, and
schemas.

Verified invariants:

- identical resolved canonical inputs produce identical keys;
- object/argument ordering does not affect identity;
- a different line changes `prediction_key` but not `market_subject_key`;
- a different bookmaker changes both subject and prediction keys;
- a different event changes all relevant keys;
- a different run leaves `prediction_key` unchanged and changes
  `prediction_id`;
- `24.50`, `24.5`, numeric `24.5`, trailing zeroes, and negative zero have
  deterministic Decimal line normalization;
- non-finite/non-numeric lines fail closed.

Approved payload composition is implemented:

```text
market_subject_key:
  sport + league + event + participant + market + selection + bookmaker

prediction_key:
  market_subject fields + canonical line

prediction_id:
  identity version + prediction_key + prediction_run_id
```

### 4.1 Unresolved representation

If a required identity input is `None`/blank/known null spelling, the three
prediction keys remain null. The event retains the exact board row and stores:

```text
identity.resolution_status = UNRESOLVED
identity.unresolved_fields = [explicit field names]
```

The idempotency fallback binds run ID, row index, and exact row payload hash.
Reconciliation is `DEGRADED`; canonical selection is not changed.

A read-only survey of the current runtime Elite boards found 65 rows:

- 65/65 have player IDs;
- 33/65 have nonblank game IDs (older board schemas account for the rest);
- 0/65 have `bookmaker`, `sportsbook`, or `vendor`;
- 65/65 have `line_source=live_market`, which is correctly not guessed to be a
  bookmaker.

Consequently, current boards are expected to publish immutable shadow rows but
reconcile `DEGRADED` for missing bookmaker identity; 32 rows also lack event
identity. This is documented shadow degradation and does not filter live
picks.

### 4.2 Identity blocker

Literal `UNKNOWN` event/player values are not included in `_clean_id()`'s null
sentinel set and are treated as resolved. This must be corrected and tested
before acceptance. The adapter should also define whether already namespaced
`courtvision:...` identity input is accepted as canonical or rejected; the
current prefixing functions can double-prefix an already namespaced event or
participant value. Current inspected boards do not contain those namespaced
columns, so this did not change live behavior.

## 5. Canonical JSON and hashing

Canonical JSON v1 passed deterministic checks for:

- lexicographic object-key order and nested order independence;
- UTF-8 with retained Unicode;
- stable null, string, boolean, integer, finite-float, list, tuple, and date
  behavior;
- aware-datetime conversion to UTC with six fractional digits and `Z`;
- repeat serialization and repeat payload SHA-256;
- rejection of non-string mapping keys, sets, bytes, arbitrary objects, naive
  datetimes, NaN, and infinities.

The algorithm identifiers are versioned/documented as
`canonical_json_v1` and `SHA-256`. Identity, payload, evidence, file, event,
and segment hashes are lowercase SHA-256. Filesystem creation/modification
timestamps are not inputs to event ID, event hash, evidence hash, or segment
content hash.

## 6. Event integrity

Event envelope v1 contains event, payload, identity, and canonicalization
schema versions. Verification confirmed:

- `payload_sha256` is SHA-256 over canonical payload JSON bytes;
- `event_hash` is deterministic over every envelope field except itself;
- sequences start at 1 and the first `previous_event_hash` is null;
- each later event points to the preceding event hash;
- lifecycle timestamps reject naive datetimes;
- `recorded_at_utc` is aware UTC;
- missing/invalid/naive provider timestamps remain null;
- `corrects_event_id` exists and committed Phase 2 events are never mutated.

A valid committed segment was copied to a disposable second lifecycle root,
one byte in `events.jsonl` was changed, and `verify_segment()` rejected the
copy. Real lifecycle evidence was not modified.

## 7. Segment transaction and writer/concurrency tests

Disposable failure injection covered:

- before temporary segment creation;
- after event/run data files;
- after segment manifest write;
- before atomic rename;
- during final directory rename;
- after the final rename but before the caller receives success.

Results:

- incomplete stages never appeared in `completed_segment_directories()`;
- temporary segment directories were cleaned;
- empty ancestor date directories may remain but contain no committed or
  staged files;
- a valid segment remained readable after a post-rename caller exception;
- retry of that exact run returned `ALREADY_COMMITTED`;
- the retry created no duplicate event or segment;
- same-run conflicting content failed with `IDEMPOTENCY_CONFLICT` and did not
  overwrite bytes.

Real separate-process Windows checks confirmed:

- process A can own `.writer.lock`;
- process B receives the busy failure and cannot commit;
- a live owner is not treated as stale;
- a lock left by a terminated process is recovered once Windows reports its
  PID dead;
- corrupt JSON and structurally malformed JSON lock metadata prevent the
  contender from acquiring the lock and are not deleted;
- cleanup does not delete metadata whose `lock_id` no longer matches the
  current owner.

Non-blocking robustness finding: a syntactically valid non-object lock payload
such as `[]` fails closed but currently escapes as `AttributeError` rather
than the documented `LifecycleWriterBusyError`.

## 8. Idempotency

Verified:

- same run path + same segment content returns `ALREADY_COMMITTED`;
- same idempotency set + identical event/payload hashes returns idempotent
  success;
- same key + changed content fails closed;
- a partial prior idempotency set is refused;
- a prepared commit retried after an ambiguous post-rename exception does not
  duplicate events;
- global scans verify existing committed segments before accepting a retry.

## 9. Evidence-object integrity and security

Verified:

- identical content deduplicates to the same hash/path;
- changed content produces a different hash/path;
- forced same-hash/different-byte publication fails closed;
- modifying committed evidence makes both `verify_evidence()` and its
  referencing segment verification fail;
- manifest evidence references resolve and required hashes verify.

Sanitization covers normalized key spellings/suffixes for:

```text
Authorization
API key
token
access token
cookie
password
secret
```

A canonical board with a secret-bearing header is rejected before its values
are persisted. Error text redacts common bearer/key forms. Model manifests
retain artifact names/hashes/sizes, not absolute paths. Board paths are
repository-relative; external paths become `<external>/<filename>`.

## 10. Run identity and provenance

Two real `begin_shadow_run()` initializations produced different UUID run IDs.
A rerun is therefore a new model execution; a retry of prepared immutable
content retains its existing run ID through the writer.

Verified manifest distinctions:

- lifecycle `run_mode=SHADOW`;
- `canonical_runtime_mode=LIVE`;
- `lifecycle_authority=SHADOW_ONLY`;
- `SCHEDULED`, `MANUAL`, `RETRY`, and `RECOVERY` reasons persist;
- unrecognized/unsupplied reason is null;
- commit SHA, dirty status, tracked working-tree fingerprint, config hash,
  available artifact hashes, Python, and dependency fingerprint are captured;
- model/calibration/strategy/pipeline versions remain null when unavailable.

The documented limitation remains: untracked file contents are not included
in `working_tree_hash`, although untracked presence contributes to
`git_dirty`.

## 11. Reconciliation

Reconciliation passed exact-board checks and deterministically detected:

- missing and extra publication events;
- player, event, market, selection, line, odds, projection, and probability
  differences;
- full exact row differences, including every other board field;
- event/identity inconsistency;
- board-file hash change.

Classification is deterministic:

- `PASS` for exact, fully resolved board/event parity;
- `DEGRADED` for exact publication with unresolved shadow identity or ordinary
  shadow persistence failure;
- `FAIL` for missing/extra/mismatched/tampered/integrity-conflict evidence.

Reconciliation writes only a create-once shadow report. No reconciliation
result feeds prediction, selection, history, grading, or bankroll behavior.

## 12. Feature-flag isolation and canonical parity

Verified when the package is present and the flag is unset/`0`:

- the flag defaults off;
- no lifecycle root is created;
- no writer lock is acquired;
- no event is written;
- no reconciliation is invoked;
- the mocked canonical CLI succeeds with its existing exit behavior.

Verified with the flag enabled in an isolated environment:

- canonical board/model/sentinel-history bytes exactly equal the flag-OFF
  run;
- only `data/lifecycle/**` and shadow log lines are added;
- shadow degradation does not change canonical success exit code.

**Gate failure:** a subprocess that blocked import of
`courtvision.lifecycle.publication` could not import `courtvision_ai` even with
the flag explicitly `0`. The adapter must be imported lazily/conditionally
after the flag check, or an equivalent no-dependency OFF path must be
implemented.

## 13. Existing histories and lock behavior

### 13.1 Histories

No Phase 2 diff touches history writers or research outputs. Before/after
content hashes were identical:

| File | SHA-256 |
|---|---|
| `data/history/prediction_history.csv` | `9164e9f9399741a3fab8aa1819fddbf0ebf45a8c08b054a4ede6bbae4de6a65b` |
| `data/history/pick_history.csv` | `11a09947247110e96e621cb91bdc6fd9c6a6eb6c23dd2c1ec8a7607a698b531b` |
| `data/history/market_shadow_history.csv` | `2fb67e676ac0ff9b5a2436e18a81706f8be16f9e04285745c61f874ec3f87c89` |
| `data/history/evidence_ledger.csv` | `b1d3c221ee731c86ee39d7025bf405d06384c3dd006d80d42d7d5d38961f0673` |

One existing broad test touched only
`market_shadow_history.csv`'s modification timestamp; its bytes and size were
unchanged. The pre-audit timestamp was recorded and restored exactly. MLB/NBA
research tests used pytest temp roots; no real research output was changed.

### 13.2 Known `now=None` lock defect

`courtvision/runtime_gates.py` and
`courtvision/pipeline/predict_pipeline.py` are byte-identical to `HEAD` and
absent from the Phase 2 diff. Direct verification still returns:

```text
_is_before_lock_buffer(aware_game_datetime, None, 10) == True
```

The canonical caller still passes `None` in the previously audited path. Phase
2 did not fix, alter, hide, or compensate for this defect.

## 14. Broad-suite failure investigation

### 14.1 Evidence limitation in the Phase 2 completion report

The completion report persisted the aggregate:

```text
4,044 passed, 31 xfailed, 9 skipped, 18 failed
```

and six affected modules, but it did **not** persist JUnit output,
`.pytest_cache/lastfailed`, or the 18 exact node IDs. Therefore the original
18 failures cannot be truthfully reconstructed one-for-one. This is a test
evidence deficiency; this report does not invent names.

Independent reruns demonstrated that “18” is not a stable set:

| Tree/run | Result in the same N-O population |
|---|---|
| current tree, long `%TEMP%` path | 49 failures, dominated by path-length `FileNotFoundError`; invalid comparison |
| current tree, short local temp, first run | 22 failed, 488 passed, 8 skipped |
| clean `HEAD`, matching short local temp | 15 failed, 494 passed, 9 skipped |
| current tree, completion-style `.pytest_phase2_no` repeat with JUnit | 7 failed, 502 passed, 9 skipped |

The varying node set and count are characteristic of the existing Windows
research writer atomic-directory-rename race/environment. All affected
research sources and tests have Git blob hashes identical to `HEAD`. Static
inspection also confirms these research modules intentionally do not import
`courtvision_ai` or lifecycle code. Lifecycle was explicitly disabled in all
comparison runs.

### 14.2 Exact failures in the persisted independent current-tree rerun

| Test | Module | Error | Reproducibility / baseline | Classification |
|---|---|---|---|---|
| `test_concurrent_later_batches_resolve_to_deterministic_latest_observation` | `test_nba_player_points_closing_evidence.py` | `PermissionError [WinError 5]` on stage-directory rename | Different atomic-rename nodes fail across repeats; clean `HEAD` shows same module/class | B — environment-specific, pre-existing/unrelated |
| `test_corrupted_prediction_file_with_complete_does_not_replay` | `test_nba_player_points_evidence_writer.py` | `PermissionError [WinError 5]` on ledger rename | Same writer fails nondeterministically on clean `HEAD` | B — environment-specific, pre-existing/unrelated |
| `test_temp_preview_outputs_are_explicit_and_limited` | `test_nba_player_points_rehearsal_integration.py` | preview path rejected because completion-style `--basetemp` is outside system temp | Fails identically on clean `HEAD` under the same repo-local base; passes when pytest uses system temp | A/B — pre-existing test-command/environment mismatch |
| `test_settlement_publish_and_minutes_participation_distinctions` | `test_nba_player_points_research_runner.py` | wrapped `WinError 5` atomic rename | Same runner/writer class fails on clean `HEAD`; exact node varies | B — environment-specific, pre-existing/unrelated |
| `test_cli_exit_codes_and_dry_run_behavior` | `test_nba_player_points_research_runner.py` | child subprocess `ModuleNotFoundError: courtvision` | Reproduces on clean `HEAD`; package is not installed for that child cwd | A/B — pre-existing packaging environment |
| `test_mismatched_mapping_identity_is_invalid[canonical_event_id-other-event]` | `test_nba_player_points_settlement_closing_binding.py` | wrapped `WinError 5` atomic rename during prerequisite setup | Other parametrizations/nodes fail on clean `HEAD`; exact node varies | B — environment-specific, pre-existing/unrelated |
| `test_compatible_terminal_enrichment_uses_latest_without_rewriting` | `test_nba_player_points_settlement_evidence.py` | `PermissionError [WinError 5]` on settlement segment rename | Same settlement writer fails on clean `HEAD`; exact node varies | B — environment-specific, pre-existing/unrelated |

### 14.3 Exact clean-HEAD failures in the matching baseline run

The clean baseline failed these 15 nodes:

```text
test_nba_player_points_closing_evidence.py::
  test_prediction_reference_validation_missing_incomplete_and_corrupt_runs
  test_older_outside_window_and_post_tip_batches_do_not_displace_effective_close
  test_policy_version_isolation_keeps_effective_selections_partitioned
  test_failure_recovery_for_observation_and_selection_publication
  test_fixture_file_is_not_mutated

test_nba_player_points_rehearsal_integration.py::
  test_temp_preview_outputs_are_explicit_and_limited

test_nba_player_points_research_runner.py::
  test_settlement_requires_prediction_evidence_and_terminal_conflict_fails_closed
  test_cli_exit_codes_and_dry_run_behavior

test_nba_player_points_settlement_closing_binding.py::
  test_prerequisite_is_deterministic_relocatable_and_root_append_independent
  test_v2_atomic_interruptions_leave_no_completed_segment_and_release_lock[before_complete_write]
  test_concurrent_identical_and_conflicting_v2_publication
  test_mismatched_mapping_identity_is_invalid[source_observation_record_hash-111...111]
  test_supported_long_windows_paths

test_nba_player_points_settlement_evidence.py::
  test_manual_review_missing_minutes_enriches_to_settled_without_rewriting
  test_prediction_reference_mismatch_missing_and_incomplete_runs_fail_before_write
```

Except for the two deterministic command/environment cases, the failures are
the same intermittent `WinError 5` atomic-rename class. The comparison
conclusively excludes Phase 2 as their cause, but it does not make the research
writer suite healthy.

## 15. Test results

### 15.1 Acceptance and regression suites

| Selection | Result |
|---|---|
| Phase 2 lifecycle tests | **43 passed** |
| Canonical/risk-matched regression set | **196 passed, 3 expected xfailed** |
| Additional disposable acceptance checks (canonical JSON, identity edge cases, all crash points, tamper, real cross-process lock, evidence, reconciliation, run reasons) | **45 passed** |
| Additional CLI publication-failure and flag parity checks | **3 passed** |

### 15.2 Independent full repository suite, chunked

| Chunk | Result |
|---|---|
| top-level A-F | 947 passed |
| top-level G-L | 496 passed |
| top-level M | 671 passed |
| top-level N-O, persisted repeat | 502 passed, 7 failed, 9 skipped |
| top-level P-R | 876 passed |
| top-level S-Z | 368 passed |
| `tests/stable` | 53 passed |
| `tests/experimental` | 64 passed, 20 expected xfailed |
| `tests/legacy` | 78 passed, 11 expected xfailed |
| **Aggregate** | **4,055 passed, 31 expected xfailed, 9 skipped, 7 failed (4,102 collected)** |

Expected xfails are not treated as failures.

All nine skips are Windows symlink-safety cases. Symlink creation was
unavailable because the process lacked Windows privilege 1314. They cover
closing evidence, prediction evidence files/root/run directories, runner
input/root-overlap protection, settlement-closing binding, and settlement
evidence path safety.

## 16. Blockers

1. Make the flag-OFF import path independent of `courtvision.lifecycle` and add
   a subprocess import test that hides/removes the package.
2. Make identity v1 reject explicit unknown event/participant sentinels and
   define/test already-namespaced identity handling.
3. Re-run the focused and canonical suites plus the import/identity acceptance
   tests after those corrections.

The existing Windows NBA research failures do not block Phase 2 because of a
Phase 2 regression, but the repository should not represent that research
suite as green.

## 17. Non-blocking findings

- Current Elite boards omit bookmaker identity, so practical Phase 2
  reconciliation is normally `DEGRADED` even when publication is exact.
- A failed segment after evidence publication can leave unreferenced immutable
  evidence objects; no orphan event is visible.
- Structurally non-object lock metadata fails closed with an implementation
  exception instead of the documented busy exception.
- Failed staging may leave empty date ancestor directories.
- The completion report's “all 18 are atomic-rename permission errors” is too
  strong: the exact node list was not retained, and independent runs also show
  a preview-temp configuration failure and a child-process package import
  failure.
- The full-suite atomic-rename count is nondeterministic (7, 15, 18, 22, or
  more depending on timing, ACL, and path geometry).
- Reconciliation remains a create-once report rather than a ledger event, as
  documented.

## 18. Recommendations before Phase 3

1. Correct and re-audit the two blockers; do not begin Phase 3 meanwhile.
2. Persist JUnit XML and the exact pytest command/environment for every future
   completion report so aggregate failures can be audited one-for-one.
3. Stabilize the NBA research atomic directory publication tests on Windows
   and separate path-length, ACL/indexer, and true writer-concurrency causes.
4. Run temp-only tests under the system temp directory and ensure subprocess
   CLI tests have an explicit install/PYTHONPATH contract.
5. Add explicit tests for `UNKNOWN`/`N/A` identity sentinels and already
   namespaced canonical IDs.
6. Add a documented policy for unreferenced content-addressed evidence
   objects and normalize malformed lock errors.
7. Keep lifecycle shadow-only; do not change lock, grading, settlement,
   provider, selection, or bankroll behavior as part of these corrections.

---

**Acceptance stopping point:** verification and this report only. Phase 3,
market/availability observation capture, DuckDB, legacy migration, scheduler
changes, production lock changes, grading/settlement changes, and Git commit
creation were not performed.

## 19. Corrective remediation and re-audit

**Corrective audit date:** 2026-07-25  
**Scope:** the two Section 16 blockers and directly related tests/docs only  
**Current verdict:** **PASS WITH KNOWN PRE-EXISTING FAILURES**

The original `FAIL` finding above is retained as the historical first audit.
This section records the corrective implementation and evidence that changes
the current Phase 2 release classification.

### 19.1 Flag-OFF import isolation

The top-level `courtvision.lifecycle.publication` import was removed from
`courtvision_ai.py`. The new `courtvision.shadow_lifecycle` adapter contains
the flag contract and dynamically imports publication hooks only after the
flag evaluates true.

Fresh-process import-hook tests block both `courtvision.lifecycle` and all of
its submodules:

| Flag | Blocked lifecycle package | Result |
|---|---|---|
| unset | yes | `import courtvision_ai` succeeds; no lifecycle module or filesystem side effect |
| `0` | yes | import succeeds; no lifecycle module or filesystem side effect |
| `false` | yes | import succeeds; no lifecycle module or filesystem side effect |
| `1` | yes | structured `DEGRADED` initialization failure with `LIFECYCLE_IMPORT_FAILURE`; canonical predict/write path runs and exits 0 |
| `1` | no | publication hooks load and the existing publication integration passes |

The enabled import failure is wrapped, not suppressed. The structured line
contains `status=DEGRADED`, `stage=INITIALIZATION`,
`classification=LIFECYCLE_IMPORT_FAILURE`, and the original exception type.
The failure remains available through exception chaining and the canonical
logger.

### 19.2 Unknown and namespaced identity handling

`courtvision.lifecycle.identity` now owns one centralized exact-value sentinel
set. It is applied case-insensitively after trimming surrounding whitespace:

```text
UNKNOWN
UNK
N/A
NA
NONE
NULL
NAN
<NA>
MISSING
NOT_AVAILABLE
NOT APPLICABLE
UNRESOLVED
TBD
-
```

Matching is against the complete cleaned value, not a substring.
`player-NA-42`, `event-none-7`, and `unknown-event-9` remain resolved through
the existing provider-ID path.

Exact valid expected-domain identifiers in these forms are preserved:

```text
courtvision:<sport>:<league>:event:<id>
courtvision:<sport>:<league>:participant:<id>
```

Wrong-domain, wrong sport/league, malformed prefix, invalid/empty suffix, and
sentinel suffix inputs fail closed. Valid canonical IDs are not double
prefixed.

Unknown event or participant values now produce the existing approved
unresolved representation:

```text
identity.resolution_status = UNRESOLVED
identity.unresolved_fields = [...]
market_subject_key = null
prediction_key = null
prediction_id = null
```

The event retains the exact board row. Its fallback idempotency key remains
deterministically bound to run ID, row index, and exact row payload hash.
Reconciliation returns `DEGRADED`; canonical board bytes and selection output
remain unchanged.

### 19.3 Directly related robustness and retention documentation

A syntactically valid non-object writer-lock payload such as `[]` now raises
the documented `LifecycleWriterBusyError`. It remains fail-closed and is not
removed. Live-owner, stale-owner, lock timing, and all canonical lock-buffer
behavior were not changed.

No evidence garbage collection was added. A segment failure after
content-addressed evidence publication can leave unreferenced immutable
objects. Any future cleanup requires an approved retention window and a safe
reachability scan across all completed segments; apparent non-reference alone
is never a deletion criterion.

### 19.4 Exact corrective file scope

Created:

```text
courtvision/shadow_lifecycle.py
tests/test_lifecycle_import_isolation.py
```

Modified for corrective code/tests:

```text
courtvision_ai.py
courtvision/lifecycle/identity.py
courtvision/lifecycle/publication.py
courtvision/lifecycle/writer.py
tests/test_lifecycle_canonical_identity.py
tests/test_lifecycle_evidence_writer.py
tests/test_lifecycle_publication_reconciliation.py
tests/test_lifecycle_runtime_integration.py
```

Modified for required corrective documentation:

```text
docs/architecture/prediction_lifecycle_phase2.md
docs/architecture/prediction_lifecycle_phase2_acceptance.md
docs/architecture/prediction_lifecycle_phase2_completion.md
docs/operations/lifecycle_shadow_runbook.md
```

No bankroll-facing, provider, schedule, lock-buffer, grading, settlement,
history, automation, dashboard, launcher, or legacy data file was modified.

### 19.5 Focused and canonical results

| Gate | Result |
|---|---:|
| Python compilation | passed |
| `git diff --check` | passed |
| focused lifecycle/corrective suite | **80 passed** |
| canonical/risk-matched regression | **196 passed, 3 expected xfailed** |
| additional CLI/runtime-output/artifact-date/runtime-gate parity | **26 passed** |
| explicit current `now=None` behavior check | passed unchanged |
| five non-NBA full-run cwd-leak failures, isolated rerun | **5 passed** |

`courtvision/runtime_gates.py` and
`courtvision/pipeline/predict_pipeline.py` remain unchanged from `HEAD`. The
known `_is_before_lock_buffer(..., now=None, ...) == True` behavior is
unchanged, as required.

### 19.6 Complete and validated chunked suite results

The single-process complete suite collected 4,139 tests:

```text
4,078 passed, 31 expected xfailed, 8 skipped, 22 failed
```

Seventeen failures were in the already documented NBA research environment
cluster. Five failures read repository-relative production files after a
long-run working-directory leak. All five passed in immediate isolation; the
referenced production files exist and are unchanged.

The initial audit validated bounded separate-process chunks as the reliable
Windows method. The corrective rerun repeated that same method and persisted
JUnit XML for each chunk:

| Chunk | Result |
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

The 19 failures are restricted to the same six byte-unchanged modules and
environment classes documented in Sections 14-15:

| Module | Corrective failures |
|---|---:|
| `test_nba_player_points_closing_evidence.py` | 7 |
| `test_nba_player_points_evidence_writer.py` | 3 |
| `test_nba_player_points_rehearsal_integration.py` | 1 |
| `test_nba_player_points_research_runner.py` | 2 |
| `test_nba_player_points_settlement_closing_binding.py` | 4 |
| `test_nba_player_points_settlement_evidence.py` | 2 |

Fifteen nodes directly report Windows `PermissionError [WinError 5]` during
atomic directory publication. Two concurrent-writer nodes report worker
exceptions through aggregate assertions, consistent with the same
nondeterministic clean-baseline concurrency class. One node is the documented
repo-local pytest-base versus system-temp preview-policy mismatch, and one is
the documented child-process `ModuleNotFoundError: courtvision` packaging
environment case.

The varying atomic-rename node set and count remain consistent with the
initial clean-`HEAD` comparison. No failing node imports or exercises the
corrective lifecycle adapter/identity code. The broad repository suite is not
reported as green.

### 19.7 Corrective verdict

Both release blockers are fixed:

1. flag-OFF import and runtime behavior no longer require
   `courtvision.lifecycle`;
2. explicit unknown identity sentinels and malformed/wrong-domain canonical
   namespaces fail closed.

All lifecycle, publication, reconciliation, canonical parity,
artifact/history, CLI, and known lock-non-change gates pass. No new failure is
attributable to the corrective work. Remaining failures match the documented
clean-baseline Windows/environment classes.

**Final Phase 2 verdict: PASS WITH KNOWN PRE-EXISTING FAILURES**

Phase 3 was not started. DuckDB was not installed. No committed lifecycle
segment was rewritten. No Git commit was created.
