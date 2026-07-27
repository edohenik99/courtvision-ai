# Operator-Controlled Paper-Pick Promotion

Status: implemented foundation under verification, review schema v1 and
OfficialPick schema v2, paper/research only. This is not an operational or
release-readiness claim.

This workflow adds a human authorization boundary between a model candidate
and immutable `OfficialPick` publication. It does not authorize automatic
approval, live betting, bankroll output, Kelly sizing, automatic stake sizing,
automatic settlement or reconciliation, historical ID backfilling, or
migration of observations or legacy rows.

## Review Contract

`OfficialPickCandidateReview` is a frozen schema-version `1` contract published
as `OFFICIAL_PICK_CANDIDATE_REVIEWED`.

| Field | Rule |
| --- | --- |
| `review_id` | Immutable `review_<uuid4 hex>` assigned once for a newly committed review. |
| `source_candidate_id` | Required stable candidate identity. Reuse with different snapshot content fails. |
| `source_record_kind` | Always `MODEL_CANDIDATE`. Observations and legacy rows are rejected. |
| `review_status` | Always `COMMITTED`; this is the lifecycle/storage state. |
| `operator_decision` | `APPROVED`, `REJECTED`, `DEFERRED`, or `EXPIRED`; this is the operator outcome. |
| `approved_designation` | Required `PAPER` or `RESEARCH` value copied from and bound to the frozen candidate snapshot. |
| `operator_id` | Required asserted operator identity. It is recorded for audit but is not proof of authentication. |
| `decision_reason` | Required non-empty decision rationale. |
| `reviewed_at` | Immutable timezone-aware UTC publication time. |
| `review_run_id` | Required identity for the explicit review action or batch. |
| `candidate_snapshot` | Canonical normalized candidate fields frozen at review time. |
| `candidate_snapshot_sha256` | SHA-256 of the canonical snapshot. |
| `provenance` | Review service/policy, source candidate, and optional source/code hashes. |
| `idempotency_key` | Deterministic review-transition identity with `oprevidem_` prefix. |
| `schema_version` | Always `1`. |
| `record_kind` | Always `OFFICIAL_PICK_CANDIDATE_REVIEW`. |

`review_status` and `operator_decision` deliberately remain separate.
`COMMITTED` says that the event exists in the verified append-only ledger;
the decision says what the operator decided. There are no draft or implicitly
approved review rows in this contract.

The JSON payload contract is
`courtvision/lifecycle/schemas/official_pick_candidate_reviewed_payload_v1.json`.

## Candidate Snapshot Freezing

`review_official_pick_candidate(...)` accepts only a validated
`MODEL_CANDIDATE` with a resolved `source_candidate_id`. It normalizes and
freezes all data needed to construct an `OfficialPick`:

- sport, league, event identity and start time;
- prediction date, market, selection, line, odds, and sportsbook;
- player and team identity where applicable;
- model name/version and source run ID;
- the intended `PAPER` or `RESEARCH` designation;
- source candidate identity and candidate provenance; and
- the `MODEL_CANDIDATE` record kind.

The service freezes mappings as `FrozenJSONDict`, an immutable `bytes`-payload
`Mapping` whose complete instance value is deterministic canonical JSON. It is
not a string. The type has empty slots and no instance dictionary, so there is
no per-instance attribute storage for ordinary assignment or
`object.__setattr__` to replace. It exposes no mutable backing dictionary,
list, or set; nested mappings are reconstructed in the same storage-free form
and nested arrays are immutable tuples. String keys perform mapping lookup;
integer, slice, bytes, tuple, and arbitrary-object indexing is rejected rather
than exposing payload bytes.

The mapping object is intentionally unhashable. Stable payload/identity hashing
remains an explicit canonical-byte operation. `to_dict()`, review and pick
`to_dict()` methods, reporting DTO `to_dict()` methods, and
`dataclasses.asdict(...)` produce detached ordinary dictionaries and lists.
Those normalized dataclass values are JSON encodable and candidate snapshots
and provenance remain JSON objects. Direct `json.dumps(FrozenJSONDict(...))`
is unsupported and raises `TypeError`; callers must use `to_dict()` or
dataclass normalization.

Authorization and identity comparisons use tagged canonical equality bytes.
Integer, float, boolean, string, enum, datetime, date, and null values retain
their types. Therefore `1`, `1.0`, `True`, and `"1"` are distinct; a datetime
is distinct from the same-looking string; null is distinct from a missing key;
and nested numeric type changes are rejected. Lists and tuples intentionally
share one canonical array representation, and mapping insertion order is
ignored. The snapshot and review-content SHA-256 values hash those same tagged
bytes. Promotion reconstructs the same representation and requires exact
canonical equality and digest equality. Mutable CSV files,
in-memory source dictionaries, and runtime state are not promotion authority
after review. Candidate mutation, source candidate ID reuse with different
content, or promotion input that differs from the approved snapshot fails
closed.

## Review Identity, Idempotency, and State

`review_id` is a UUID identity assigned only for a new committed event.
Idempotency is deterministic and separate:

- deferred reviews use a slot derived from `source_candidate_id` and
  `review_run_id`, with the approved designation included in identity;
- all final decisions (`APPROVED`, `REJECTED`, `EXPIRED`) share one canonical
  final slot per `source_candidate_id` and approved designation.

This model permits explicit deferral events while ensuring that concurrent or
sequential final decisions cannot create multiple final review authorities.
The review content hash excludes only generated `review_id`, `reviewed_at`,
and `idempotency_key`.

Legal transitions are:

| Current state | Requested review | Result |
| --- | --- | --- |
| Unreviewed | Approved, rejected, deferred, or expired | Append a committed review. |
| Any committed slot | Identical replay | Protected no-op; return the committed review and original `review_id`. |
| Any committed slot | Different content in the same slot | Fail with `IDEMPOTENCY_CONFLICT`. |
| Deferred only | Any new deferred review | Fail; a deferral may be followed only by one final decision. |
| Deferred only | Approved, rejected, or expired with a new run ID | Append the single final review. |
| Approved, rejected, or expired | Any new decision | Fail; final review cannot be replaced. |

General review correction or supersession authoring is not active. A future
correction contract must append an explicitly linked event; it must not rewrite
an existing review.

## Append-Only Review Publication

The explicit API is:

```python
from courtvision.official_picks import review_official_pick_candidate

result = review_official_pick_candidate(
    validated_candidate,
    operator_decision="APPROVED",
    operator_id="operator.alice",
    decision_reason="Verified market, identity, and model provenance.",
    review_run_id="paper-review-2026-07-26-a",
    lifecycle_root="data/lifecycle",
)
```

The service supplies a no-argument preparation callback to
`LifecycleWriter.run_locked_transaction`. Commit execution is lexically owned
by that method: no prepared-segment commit implementation exists as a callable
writer instance method. Before lock acquisition, the writer resolves
`writer.root` once and permanently binds the transaction to that captured
root. Lock metadata, committed-state and review reconstruction, authorization,
idempotency and evidence lookup, partition paths, staging, data and manifest
writes, verification, atomic rename, rollback, and returned paths all use the
captured root. Callback mutation of writer configuration cannot retarget an
active transaction. An unrestored root mutation is rejected defensively after
the callback; changing and restoring the attribute still leaves every
operation bound to the original root.

The writer also takes a private writer-instance reentrancy guard before any
filesystem root lock. While a callback is active, the same writer instance
cannot call `commit_segment`, `run_locked_transaction`, reconciliation writing,
or another equivalent write entry point, even after changing `writer.root`.
Rejection occurs before a second lock or staging directory, and the guard is
always cleared in `finally`. Separate writer instances have independent
in-process guards. Repository filesystem locks remain the cross-process
serialization mechanism.

After entering both protections, the writer rereads and verifies lock metadata
and owner, invokes the callback once, accepts only prepared
manifest/event/evidence data (or a no-op), validates the run binding and
complete semantic batch, resolves idempotency, stages one segment, verifies it,
atomically renames it, rereads and verifies the committed segment, and releases
the lock. The callback receives no lock object, token, registry entry, file
descriptor, commit function, or reusable commit capability.

Review-state reconstruction, transition validation, event preparation,
staging, rename, and rollback occur within that one writer-owned flow. Generic
writer callers receive the same review-event state-machine,
snapshot-uniqueness, idempotency, envelope, and decision validation. Lock
metadata binds a non-empty lock ID, PID, hostname, resolved lifecycle root, run
ID, command, and acquisition time, and remains present through staging and the
pre-rename hook.

Review events are stored in:

```text
data/lifecycle/ledger/YYYY/MM/DD/<review-transaction-id>/
  COMPLETE
  events.jsonl
  manifest.json
  run_manifest.json
```

## Promotion Authorization Boundary

`promote_candidate_to_official_pick(...)` now requires `review_id`. Promotion
and every OfficialPick v2 ledger reconstruction call the same shared
review-authorization validator. It loads authority only from verified
committed review segments and requires:

- `review_status=COMMITTED`;
- `operator_decision=APPROVED`;
- the pick designation exactly equals `approved_designation` and the
  designation frozen in the candidate snapshot;
- matching `source_candidate_id`;
- exact canonical equality for all reproducible pick fields and candidate
  provenance from the frozen snapshot, plus its SHA-256;
- `PAPER` or `RESEARCH` designation; and
- preserved operator and review-run provenance.

Example:

```python
from courtvision.official_picks import promote_candidate_to_official_pick

promotion = promote_candidate_to_official_pick(
    validated_candidate,
    review_id=result.review.review_id,
    lifecycle_root="data/lifecycle",
)
```

`validated_candidate` already contains the intended `designation`. Promotion
does not accept a second independently supplied designation. Changing PAPER to
RESEARCH or RESEARCH to PAPER requires a new candidate identity and a new
valid review under the documented state model.

Missing, uncommitted, rejected, deferred, expired, malformed, mutated, or
tampered review authority is rejected. `MARKET_OBSERVATION` and
`LEGACY_UNIDENTIFIED` records cannot enter the review or promotion boundary.
The former observation-promotion entrypoint is retained only as a fail-closed
compatibility guard.

Every schema-v2 `OFFICIAL_PICK_PUBLISHED` event is re-authorized by
`LifecycleWriter` while its real lock is held. The validator receives the
target lifecycle root and reconstructs verified committed review state from
that root before any staging directory or atomic rename. A review in another
root, a self-consistent forged content hash, or a structurally valid event
without committed approval cannot enter the append-only ledger.

Authorization is batch-wide rather than event-local. Committed picks and all
earlier events in the pending segment participate in uniqueness checks for
`review_id`, source candidate identity, promotion idempotency key, and
`pick_id`. One review and its source candidate can authorize only one pick. A
second pick ID fails before staging; an exact replay can resolve only to the
already committed pick.

The writer recomputes the promotion idempotency key from the frozen policy
version, committed review ID, source candidate ID, approved designation, and
tagged canonical candidate snapshot digest. Neither the envelope nor the pick
payload is trusted to supply that identity. Pick provenance is exact: every
reviewed candidate-provenance key/value must be present, the generated keys are
precisely `source_type`, `source_id`, `promotion_service`, `promotion_actor`,
`promotion_policy_version`, `review_id`, `review_decision`,
`review_operator_id`, `review_run_id`, and `candidate_snapshot_sha256`, and no
additional key is allowed.

## OfficialPick Identity and Promotion Idempotency

The existing OfficialPick publication service and UUID `pick_id` assignment
remain the only identity/publication implementation. Schema v2 is the smallest
versioned extension and adds:

- `review_id`;
- `candidate_snapshot_sha256`; and
- review ID, decision, operator, review run, and snapshot digest in provenance.

The v2 payload contract is
`courtvision/lifecycle/schemas/official_pick_published_payload_v2.json`.
New `OFFICIAL_PICK_PUBLISHED` writes through `LifecycleWriter` require payload
v2 containing pick v2. The local lifecycle inventory contains no production
historical schema-v1 OfficialPick segments, so there is no runtime allowlist or
legacy registry. Active publication, reconstruction, settlement, and reporting
reject every v1 OfficialPick. Parsing is retained only in
`courtvision.official_picks.legacy_v1` for a future explicit audited
migration/import procedure. That parser returns the distinct
`LegacyOfficialPickV1` compatibility DTO, not `OfficialPick`, and the type is
not exported by the normal package. Environment variables, runtime configuration,
segment payloads, manifests, provenance, and caller input cannot enable v1.
Historical rows are not backfilled or rewritten.

Payload and pick versions dispatch exactly. Missing or unknown versions,
missing `record_kind`, mixed payload/pick versions, policy or publication
authority mismatches, unexpected top-level properties, and non-canonical
round-trips fail closed.

Promotion idempotency is derived from promotion policy, `review_id`,
`source_candidate_id`, the approved designation, and the exact typed candidate
snapshot digest and is recomputed at writer and reader boundaries. Therefore:

- identical promotion replay returns the original committed `OfficialPick`;
- different promotion content for the same approved review conflicts;
- one approved review cannot produce multiple `pick_id` values;
- concurrent duplicate promotion attempts commit at most one pick; and
- `pick_id` remains a UUID assigned once and reread from the committed event.

## Multiprocess Race-Test Evidence

One pre-instrumentation race run was anomalous: both workers exited with code
zero, two error-classified queue results were received, and no worker reported
success. The exact exception module/class/message/traceback tuples and the
resulting committed review and OfficialPick state were not preserved. Its root
cause therefore cannot be recovered from the available evidence.

The strengthened test harness records complete structured worker results and
parent-process accounting, reconstructs the committed state, checks lock and
staging residue, and writes an external JSON diagnostic before failing on any
unexpected exception or inconsistent attempt. A recurrence cannot pass based
only on zero process exit codes or a result count, and its diagnostic path is
included in the pytest failure.

## Operator-Review Reporting

`build_official_pick_operator_review_dataset(...)` reconstructs verified review
and pick events and reports these buckets separately:

- approved candidates;
- rejected candidates;
- deferred candidates;
- expired candidates;
- approved but not promoted candidates; and
- approved and promoted candidates.

Promotion status joins strictly through both `review_id` and
`source_candidate_id`, and reports the approved designation independently from
promotion status. A missing, mismatched, non-approved, designation-mismatched,
or duplicate join fails. Report rows are dedicated flattened DTOs with an
explicit safe-field allowlist; they never embed
`OfficialPickCandidateReview`, `OfficialPick`, raw provenance, or arbitrary
nested mappings. Official-pick and settlement report DTOs follow the same
flattened scalar-only policy. Structural fields for bankroll, Kelly sizing,
stake, wager amount, expected profit, ROI, live-bet state, wagering metadata,
or execution instructions are prohibited. Ordinary scalar text is not
substring-redacted: a player named “Kelly” and a decision reason explaining
that Kelly staking is prohibited are preserved. The dataset does not ingest
sportsbook observations and does not calculate financial or execution output.
Rejected, deferred, and expired candidates remain non-picks.

## Relationship to Settlement

Settlement remains a separate append-only lifecycle. It can reference only a
committed `pick_id`; it cannot approve, promote, or mutate a candidate review.
This phase adds no automatic settlement, reconciliation worker, result-provider
call, grading migration, or historical settlement.

## Remaining Limitations

- No dashboard, scheduled workflow, prediction pipeline, or collector invokes
  review or promotion automatically.
- `operator_id` is asserted input only. No authenticated principal or
  capability check exists. Dashboard, CLI, and API approval interfaces remain
  disabled until an authenticated runtime supplies that authority.
- There is no general review correction/supersession API.
- There is no live designation, staking, Kelly, bankroll, or wager execution.
- Existing observation graders and legacy histories are not migrated.
- A controlled prospective paper trial and operational review interface remain
  future work.
- The immutability and transaction-ownership statements above are precise
  Python object/API invariants. They do not claim protection against arbitrary
  process-memory corruption, malicious native-code modification, operating
  system compromise, or code that bypasses the lifecycle APIs to rewrite files.
