# Prediction Lifecycle Phase 2: Shadow Publication Foundation

## Status and authority

Phase 2 adds prospective immutable shadow evidence. It does not change the
current CourtVision authority model:

- `courtvision_ai.py` and its CSV/runtime outputs remain operationally
  authoritative.
- `data/lifecycle/` is shadow evidence only.
- No lifecycle status changes selection, qualification, Kelly sizing,
  bankroll behavior, grading, settlement, provider precedence, or the current
  scheduled workflow.
- Legacy history is not imported, rewritten, or represented as verified.
- DuckDB and relational storage are not used.

The feature is disabled by default. Set `COURTVISION_LIFECYCLE_SHADOW=1` to
enable the post-publication shadow writer.

The feature-flag dependency boundary is implemented in
`courtvision.shadow_lifecycle`. `courtvision_ai.py` imports that small adapter,
not `courtvision.lifecycle`. The adapter evaluates the environment flag before
using `importlib` to load `courtvision.lifecycle.publication`. When the flag is
unset, `0`, or any other non-true spelling such as `false`, importing and
running the canonical module does not import or require the lifecycle package
and performs no lifecycle initialization, root creation, locking, or
reconciliation.

When the flag is true, failure to import lifecycle publication is raised as
`ShadowLifecycleInitializationError` with
`classification=LIFECYCLE_IMPORT_FAILURE` and `status=DEGRADED`. The CLI
prints/logs that structured initialization failure and continues the canonical
prediction/artifact path. Enabled import errors are never silently ignored.

## Publication boundary

The canonical actionable publication artifact is the protected dated Elite
board:

```text
outputs/runtime/operator/elite_board_YYYY-MM-DD.csv
```

The integration point is in `courtvision_ai.py:main()` immediately after
`_write_cli_outputs()` returns successfully. At that point the existing model,
market, identity, availability, context, exposure, selection, and final-board
logic has already run and the actionable board exists on disk.

The lifecycle adapter:

1. opens the exact board that was written;
2. hashes its bytes without rewriting it;
3. reads exact CSV cell strings;
4. prepares `PREDICTION_PUBLISHED` events and available evidence;
5. commits an immutable segment;
6. reads the committed events back;
7. reconciles the board with the committed ledger.

If `_write_cli_outputs()` fails, no valid `PREDICTION_PUBLISHED` event is
created. A lifecycle-enabled failed canonical execution may write only
`RUN_STARTED` and `RUN_FAILED`.

## Run identity and manifest v1

Each actual prediction execution receives a new standard-library UUID. A
rerun receives a different `prediction_run_id`; retrying the same already
prepared segment keeps the same run ID and is idempotent.

Lifecycle evidence uses `run_mode=SHADOW` and records
`canonical_runtime_mode=LIVE` plus `lifecycle_authority=SHADOW_ONLY`.
`COURTVISION_RUN_REASON` is captured only when it is one of `SCHEDULED`,
`MANUAL`, `RETRY`, or `RECOVERY`; otherwise the reason is null.

Run manifest v1 contains:

```text
prediction_run_id, run_mode, run_reason, parent_run_id
started_at_utc, completed_at_utc
operating_date, operating_timezone
git_commit_sha, git_dirty, working_tree_hash
config_hash
model_id, model_version, model_bundle_hash
calibration_id, calibration_version, calibration_hash
strategy_version
pipeline_version, python_version, dependency_fingerprint
input_manifest_hash, reproducibility_level
canonical_runtime_mode, lifecycle_authority
run_manifest_schema_version
```

Unknown values are null. Phase 2 normally reports `PARTIAL` reproducibility:
the board row, available model files, safe runtime policy configuration, code
state, and package environment are bound, but complete raw contemporaneous
provider inputs are not yet available.

The working-tree fingerprint is SHA-256 over the tracked binary Git diff from
`HEAD`, excluding `data/lifecycle`, `outputs`, and `test_outputs`. `git_dirty`
also observes untracked paths. Untracked file contents are not included in the
working-tree hash.

## Central UTC clock

All new lifecycle timestamps use `courtvision.lifecycle.clock`. The system
clock returns an aware UTC `datetime`; tests inject `FixedClock`. Lifecycle
models reject naive datetimes.

`America/Toronto` is persisted separately as the operating/display timezone.
Provider-reported, run-start, publication, event-occurrence, and recording
times remain distinct. A missing, invalid, or naive provider timestamp remains
null and is never inferred from a file date, operating date, or ingestion time.

## Canonical JSON v1

The identifier is `canonical_json_v1`.

The algorithm is:

- recursively accept null, strings, booleans, integers, finite floats,
  mappings with string keys, lists/tuples, dates, and aware datetimes;
- reject sets, bytes, arbitrary objects, non-string mapping keys, naive
  datetimes, NaN, and infinities;
- convert aware datetimes to UTC using six fractional-second digits and `Z`;
- convert dates to ISO `YYYY-MM-DD`;
- serialize with UTF-8, Unicode retained, lexicographically sorted object keys,
  no insignificant whitespace, and no NaN extensions.

Generic serialization does not normalize player, market, selection, or
bookmaker semantics. Those transformations belong to identity v1.

All content and file hashes are lowercase SHA-256 hex.

## Identity v1

`identity_schema_version=1`.

Phase 2 constructs CourtVision-owned IDs from the existing resolved runtime
identifiers:

```text
canonical event ID:
  courtvision:<sport>:<league>:event:<existing game ID>

canonical participant ID:
  courtvision:<sport>:<league>:participant:<existing canonical/player ID>

canonical market ID:
  courtvision:<sport>:<league>:market:<known canonical market>

canonical bookmaker ID:
  courtvision:bookmaker:<known bookmaker>
```

Existing canonical market names and explicit aliases are accepted. The
bookmaker normalization table is intentionally small and explicit:
Bet365, BetMGM, BetRivers, Caesars, DraftKings, ESPN BET, FanDuel, Fanatics,
Pinnacle, and PointsBet. Unknown values are not guessed.

Selections are explicit `OVER`, `UNDER`, `YES`, `NO`, `HOME`, or `AWAY`.
Numeric lines use `Decimal`, reject non-finite/non-numeric values, remove
insignificant trailing zeroes, and normalize negative zero to `0`.

One exact-value sentinel contract is applied case-insensitively after
surrounding whitespace is removed:

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

Sentinel matching applies to the complete cleaned value only. Identifiers such
as `player-NA-42` and `event-none-7` therefore remain eligible for normal
provider-ID prefixing.

An input already in the exact expected canonical form
`courtvision:<sport>:<league>:event:<id>` or
`courtvision:<sport>:<league>:participant:<id>` is preserved byte-for-byte.
An expected-domain mismatch, different sport/league namespace, malformed
CourtVision prefix, empty/invalid suffix, or sentinel suffix fails closed.
Valid canonical IDs are never double-prefixed. Non-CourtVision provider IDs
continue through the existing deterministic prefixing path.

The identity payloads are:

```text
market_subject:
  identity schema version, sport, league, canonical event,
  canonical participant, canonical market, selection, canonical bookmaker

prediction_key:
  market_subject fields plus canonical line

prediction_id:
  identity schema version, prediction_key, prediction_run_id
```

Keys and IDs are `prefix + "_" + SHA256(canonical_json_v1(namespaced input))`.
Changing the line changes `prediction_key` but not `market_subject_key`.
Changing the run changes `prediction_id` but not `prediction_key`.

If a required field cannot be resolved, the event retains the exact board row
and an `identity.resolution_status=UNRESOLVED` list. Its three prediction
identity keys remain null; a deterministic run/row/payload idempotency key is
used. Reconciliation is `DEGRADED`, but the pick is not rejected or changed.

## Event envelope v1

The storage-neutral envelope is frozen and contains:

```text
event_id, event_type
event_schema_version, payload_schema_version
identity_schema_version, canonicalization_version
prediction_run_id, prediction_id, prediction_key, market_subject_key
event_sequence
occurred_at_utc, recorded_at_utc, provider_reported_at_utc
operating_date, operating_timezone
actor_type, actor_id
correlation_id, idempotency_key
payload_json, payload_sha256
source_refs, source_hashes
code_sha, config_hash, model_id, model_version
previous_event_hash, event_hash
corrects_event_id
```

Phase 2 emits `RUN_STARTED`, `RUN_COMPLETED`, `RUN_FAILED`, and
`PREDICTION_PUBLISHED`. The schema reserves
`SHADOW_RECONCILIATION_COMPLETED`; reconciliation is currently a separate
immutable report rather than an event.

Hash definitions:

```text
payload_sha256 =
  SHA256(UTF8(canonical_json_v1(payload object)))

event_id =
  deterministic ID over event type, run ID, event sequence,
  idempotency key, and payload SHA-256

event_hash =
  SHA256(UTF8(canonical_json_v1(all envelope fields except event_hash)))
```

Events are ordered from sequence 1 and chained within the segment. Event 1 has
`previous_event_hash=null`; each later event contains the preceding
`event_hash`. Filesystem timestamps are never part of a hash.

`corrects_event_id` is present but unused. Committed events are never edited.

## `PREDICTION_PUBLISHED` payload v1

Every board row retains:

- the complete exact CSV row as `canonical_board_row`;
- board row index, path, SHA-256, byte size, and explicit publication time;
- canonical and provider event/player/market references where present;
- display name, team, opponent, selection, line, odds, bookmaker/source;
- projection, probability only when present, implied probability when present,
  edge, confidence, quality score, selection score, and qualification reason;
- current context, gate, Kelly eligibility/stake fields already present on the
  board;
- current availability and schedule fields already present on the board;
- provider timestamp strings and a separately validated source timestamp;
- code/model/config/calibration provenance;
- feature, market, schedule, availability, model/config, and board evidence
  references.

Blank board cells are retained exactly in `canonical_board_row` and exposed as
null in the normalized nested views. No probability is invented.

## Content-addressed evidence

Evidence objects use:

```text
data/lifecycle/evidence/objects/<first-two-hash-chars>/<full-sha256>.json
```

The hash covers canonical JSON v1 bytes for:

```json
{
  "evidence_schema_version": 1,
  "category": "<category>",
  "payload": {}
}
```

Phase 2 categories are:

- `board_artifact`;
- `market_snapshot`;
- `schedule_snapshot`;
- `availability_snapshot`;
- `feature_snapshot`;
- `model_config_manifest`.

Identical content deduplicates. If a hash path exists, its exact bytes are
verified. Different bytes at the same path are an integrity failure; they are
never overwritten.

## Storage layout and segment commit

```text
data/lifecycle/
  .writer.lock
  evidence/
    objects/ab/<sha256>.json
  ledger/
    YYYY/MM/DD/<prediction_run_id>/
      events.jsonl
      run_manifest.json
      manifest.json
      COMPLETE
  reconciliation/
    YYYY/MM/DD/<prediction_run_id>.json
```

`events.jsonl` is canonical JSON v1, one envelope per line. `manifest.json`
binds event count, event hashes, idempotency keys, evidence hashes, file sizes,
file hashes, and a segment content hash. `COMPLETE` contains the manifest
SHA-256.

Commit protocol:

1. validate the prepared manifest, schemas, sequences, chain, evidence hashes,
   and duplicate keys;
2. acquire the repository lifecycle writer lock;
3. verify run-level and global idempotency;
4. publish or verify content-addressed evidence;
5. write and fsync a hidden temporary segment;
6. write its manifest and `COMPLETE`;
7. verify the entire staged segment;
8. fsync where supported;
9. atomically rename the directory to its final run path;
10. verify the committed segment;
11. release the lock.

Readers enumerate only directories containing a valid `COMPLETE`; hidden
temporary segments are ignored. The writer exposes no mutation or delete
operation for committed segments.

## Writer lock

`data/lifecycle/.writer.lock` is created with exclusive create semantics. It
contains:

```text
lock_id, pid, hostname, prediction_run_id, command, acquired_at_utc
```

Acquisition is bounded and returns an explicit busy error. A same-host stale
lock is recoverable only after the owning PID is verified dead. Age alone is
never sufficient. A foreign-host, corrupt, unreadable, or unverifiable lock
fails closed. There is no `--ignore-lock` bypass.

A syntactically valid non-object payload such as `[]` is classified as the
same documented `LifecycleWriterBusyError`; it is retained and never removed.

The lock covers only final validation/evidence/segment commit or immutable
reconciliation report publication. It is not held while providers, models,
features, or reports execute.

## Idempotency

Resolved publication keys are:

```text
PREDICTION_PUBLISHED:<prediction_id>
```

Unresolved rows use a deterministic equivalent containing run ID, board row,
and exact row payload hash.

- same key + same payload/event hash: `ALREADY_COMMITTED`;
- same key + different payload/event hash: `IDEMPOTENCY_CONFLICT`;
- same run path + same segment content hash: `ALREADY_COMMITTED`;
- same run path + different segment content hash: conflict.

No conflict path overwrites existing content.

## Reconciliation

Reconciliation reopens the exact canonical board and committed segment, then
compares:

- board byte hash and row count;
- board row index and full exact row content;
- prediction/event identity;
- player/entity, market, selection, line, odds, projection, optional
  probability, edge, confidence, bookmaker/source because they are included in
  the full-row comparison;
- board artifact SHA-256 stored on each publication event.

Results:

- `PASS`: successful board, verified ledger, exact counts/content/hashes, and
  all required identity resolved;
- `DEGRADED`: canonical board succeeded but identity or shadow persistence is
  incomplete; operational output remains unchanged;
- `FAIL`: identity/event mismatch, missing/extra event, idempotency conflict,
  orphan publication evidence, tampering, or another integrity failure.

Reports are create-once canonical JSON under `data/lifecycle/reconciliation/`.
Reconciliation never changes picks or grading.

## Security

Lifecycle code never reads or serializes credential-bearing environment
variables. Evidence recursively redacts sensitive key names including
authorization, API key, token, access token, secret, password, and cookie
forms. A canonical board whose header itself contains a secret-bearing field
name is rejected from shadow persistence and reported `DEGRADED`; its values
are not serialized. Failure text redacts common bearer/key patterns.

Model manifests contain artifact names, hashes, sizes, and existence only.
Board paths are repository-relative; external test paths are represented as
`<external>/<filename>`. Git fingerprints exclude runtime/lifecycle output.

## Failure semantics

- Canonical board failure: no valid publication event.
- Canonical board success plus ordinary shadow write failure: `DEGRADED`,
  loudly printed/logged, canonical exit behavior preserved.
- Idempotency or immutable-integrity conflict: `FAIL`, loudly printed/logged,
  no overwrite, canonical output preserved.
- Lifecycle initialization failure: loud `DEGRADED` log and canonical
  execution continues.
- Enabled lifecycle import failure: loud structured `DEGRADED` initialization
  log with `LIFECYCLE_IMPORT_FAILURE`; canonical execution continues.

## Known limitations

- Current boards do not always retain a bookmaker, canonical event/player ID,
  or aware provider timestamp. Those rows are explicitly unresolved.
- The feature snapshot is the complete published board row, not the full
  pre-selection model tensor.
- Raw provider schedule, odds, and injury payloads are not always available at
  this integration boundary.
- Model, calibration, strategy, and pipeline semantic versions are null when
  the current runtime does not provide them.
- Untracked Git file contents are not in the working-tree fingerprint.
- Run reason is null unless explicitly supplied.
- The current lock-buffer defect and all bankroll-facing lock behavior remain
  unchanged.
- Settlement, correction workflows, legacy migration, authoritative cutover,
  and analytical projections are not implemented.
- A failed segment after immutable evidence-object publication can leave
  unreferenced objects. Phase 2 performs no evidence garbage collection.
  Future cleanup requires a retention policy and a safe reachability scan of
  every completed segment; apparent non-reference alone is never sufficient
  grounds for deletion.

## Corrective acceptance rerun (2026-07-25)

The initial acceptance audit recorded two blockers: flag-OFF import isolation
and explicit unknown identity handling. Both were corrected without changing
canonical selection, projection, threshold, Elite, Kelly, bankroll, provider,
schedule, lock-buffer, grading, settlement, history, or automation behavior.

Validation results:

```text
Focused lifecycle and corrective tests:
  80 passed

Canonical/risk-matched regression:
  196 passed, 3 expected xfailed

Additional CLI/runtime-output/artifact-date/runtime-gate parity:
  26 passed

Validated chunked repository suite:
  4,080 passed
  31 expected xfailed
  9 skipped
  19 failed
  4,139 collected
```

All 19 chunked-suite failures are confined to the six unchanged NBA research
modules already documented by the initial clean-baseline audit. They comprise
the nondeterministic Windows atomic-directory-rename/concurrency class plus
the previously documented preview-temp and child-process package-import
environment cases. No lifecycle, canonical parity, artifact/history, or
bankroll-facing test failed.

Final Phase 2 verdict:
**PASS WITH KNOWN PRE-EXISTING FAILURES**. Phase 3 was not started.
