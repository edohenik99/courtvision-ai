# Official Pick Identity And Lifecycle

Status: implemented foundation, paper/research only.

This document describes the canonical official-pick identity layer. It does not
authorize live wagering, Kelly sizing, bankroll use, automatic candidate
promotion, historical backfilling, or claims of forward predictive validity.
Paper/research settlement is documented separately in
`official_pick_settlement_lifecycle.md`.

## Five Distinct Record Kinds

| Record kind | Meaning | Is an official pick? | May be described as betting ROI? |
| --- | --- | --- | --- |
| `MARKET_OBSERVATION` | A sportsbook quote or other observed market state. | No | No; label results `observation performance`. |
| `MODEL_CANDIDATE` | A model prediction/candidate/board row that has not been promoted. | No | No; label results `model candidate analysis`. |
| `OFFICIAL_PICK_CANDIDATE_REVIEW` | A committed operator decision over a frozen model-candidate snapshot. | No | No. |
| `OFFICIAL_PICK` | An approved model candidate explicitly promoted and transactionally published. | Yes | Only through an official-pick-only report boundary. |
| `SETTLED_OFFICIAL_PICK` | A settlement lifecycle record linked to a committed official pick by `pick_id`. | It is the settlement of one | Only after the settlement layer validates the referenced `pick_id`. |

MLB sportsbook HR rows remain `MARKET_OBSERVATION` records. NBA prediction
boards remain `MODEL_CANDIDATE` outputs. Neither pipeline imports or invokes the
promotion service automatically.

## Canonical `OfficialPick` Schema

Current schema version: `2`. New publication requires v2 operator-review
evidence. The local lifecycle inventory contains no production historical v1
OfficialPick segments, so active runtime boundaries reject v1 and no legacy
registry exists.

| Field | Rule |
| --- | --- |
| `pick_id` | Globally unique `pick_<uuid4 hex>` assigned once during explicit promotion. Immutable. |
| `sport`, `league` | Required normalized sport and league identity. |
| `event_id` | Required canonical CourtVision event ID. |
| `event_start_time` | Required timezone-aware UTC timestamp. Publication after the start is rejected. |
| `prediction_date` | Required ISO date and cannot be after the event date. |
| `market_key` | Required canonical CourtVision market key. |
| `selection` | Required normalized selection (`OVER`, `UNDER`, `YES`, `NO`, `HOME`, or `AWAY`). |
| `line` | Required finite canonical decimal string. |
| `odds` | Required finite, non-zero numeric price. |
| `sportsbook` | Required canonical CourtVision bookmaker ID. |
| `player_id`, `player_name` | Required for player markets; otherwise nullable. |
| `team_id` | Canonical when supplied; otherwise nullable. |
| `model_name`, `model_version` | Required model provenance. |
| `run_id` | Required source model/observation run ID. |
| `published_at` | Required immutable publication timestamp. |
| `source_candidate_id`, `source_observation_id` | v2 requires `source_candidate_id`; observation promotion is prohibited. |
| `review_id` | Required committed approved operator-review identity. |
| `candidate_snapshot_sha256` | Required immutable evidence binding the pick to the reviewed candidate. |
| `status` | `PUBLISHED`. Settlement does not mutate this record. |
| `designation` | `PAPER` or `RESEARCH`. `LIVE` is rejected because no live gate is active. |
| `idempotency_key` | Separate deterministic promotion identity; not the `pick_id`. |
| `provenance` | Exact reviewed candidate provenance plus the documented generated authorization keys; arbitrary additional keys are rejected. |
| `record_kind` | Always `OFFICIAL_PICK`. |
| `schema_version` | `2` for new publication. |

`OfficialPick` is a frozen dataclass. Re-reading a committed event reconstructs
and revalidates the same `pick_id`; it is never derived again from line, odds,
status, or other mutable-looking business fields.

Review and promotion authorization use one tagged canonical equality
representation. Integer and float values are distinct (`1` is not `1.0`);
boolean, number, and numeric string values are distinct; enum members are
distinct from arbitrary strings unless the contract explicitly normalizes the
enum first; aware datetimes are normalized to UTC but remain distinct from
strings; and null differs from a missing key. Lists and tuples intentionally
share one array representation, while dictionary insertion order is ignored.
Candidate snapshot, review content, and promotion content hashes use these same
typed bytes. Stored event/envelope JSON continues to use canonical JSON v1.

Nested JSON state is frozen in `FrozenJSONDict`, an immutable
`bytes`-payload `Mapping` whose complete value is deterministic canonical JSON.
It is not a string. It has empty slots, no instance dictionary, and no mutable
backing container, so neither ordinary assignment nor `object.__setattr__` can
replace authorization data through an internal attribute. Nested mappings use
the same representation and nested arrays are tuples. Non-string item access
is rejected rather than indexing the encoded payload.

The mapping object is intentionally unhashable; stable hashes remain explicit
canonical-byte operations. Public `to_dict()` routes and
`dataclasses.asdict(...)` return detached ordinary dictionaries and lists, so
candidate snapshots and provenance serialize as JSON objects. Direct
`json.dumps(FrozenJSONDict(...))` is unsupported and raises `TypeError`;
callers normalize through `to_dict()` or the documented dataclass routes.

## `pick_id` Versus Idempotency Key

The two identifiers have different jobs:

- `pick_id` is a UUID4-based globally unique identity assigned only when the
  promotion operation creates the official pick.
- `idempotency_key` is a deterministic SHA-256 identity generated from
  promotion-policy version, committed `review_id`, source candidate ID,
  approved designation, and the exact typed candidate snapshot digest.

Repeating the same promotion returns the existing committed pick and original
`pick_id`. Reusing the deterministic key with different pick content raises an
`IDEMPOTENCY_CONFLICT`. The generated content hash covers the official-pick
fields except the generated `pick_id`, `published_at`, and idempotency key.

## Ledger And Transaction Rules

Official picks use the existing canonical lifecycle writer. The ledger is:

```text
data/lifecycle/ledger/YYYY/MM/DD/<promotion-transaction-id>/
  COMPLETE
  events.jsonl
  manifest.json
  run_manifest.json
```

The date partition is the pick's `prediction_date`. Each promotion transaction
contains an `OFFICIAL_PICK_PUBLISHED` event with the schema-versioned official
pick and promotion-content hash.

The existing lifecycle writer supplies:

- a repository-level verified-owner writer lock;
- staged files and an atomic directory rename;
- canonical JSON and SHA-256 file/event hashes;
- an event hash chain and segment manifest;
- global idempotency checks;
- rollback of the temporary segment on failure; and
- verification before and after commit.

`OFFICIAL_PICK_PUBLISHED` is additionally validated at the writer boundary:
new payload v1 is rejected, payload v2 must contain pick v2, and malformed or
mixed-version publications cannot be committed through the writer. While the
writer lock is held and before staging or atomic rename, the writer reconstructs
verified committed review state from the target lifecycle root and calls the
same authorization validator used by promotion and reconstruction. Missing,
foreign-root, rejected, deferred, expired, malformed, tampered, or
field-mismatched reviews fail before commit.

Publication authorization is one batch-wide operation. Committed picks and
earlier pending events enforce unique review IDs, source candidate identities,
promotion idempotency keys, and pick IDs. The writer recomputes the exact
promotion key from frozen policy inputs and compares the full provenance key
set and canonical values. A second pick for one review fails before staging;
only an exact replay can resolve to the committed pick.

Generic review events are also authorized under the writer lock with the same
transition state machine, snapshot uniqueness, decision, envelope, and
idempotency rules as the review service.

The public commit API does not accept caller-held lock objects. Review
transitions use `run_locked_transaction`, whose commit implementation is
lexically contained in that writer-owned call and is not reachable as an
instance method. Before acquiring the filesystem lock, the writer resolves its
configured root once and permanently binds the transaction to that path. Lock
metadata, ledger and review reconstruction, OfficialPick authorization,
idempotency and evidence lookup, paths, staging, writes, manifests, completion
marker, atomic rename, reread, verification, rollback, and returned paths all
use the captured root. Callback changes to `writer.root` cannot retarget the
active transaction; an unrestored change is also rejected after callback
execution.

A private writer-instance guard rejects recursive `commit_segment`,
`run_locked_transaction`, reconciliation writing, and equivalent writes before
another root lock or staging operation. The guard is cleared after success,
no-op, and exception paths. Separate writer instances retain independent
guards, while filesystem locks continue to provide cross-process
serialization. With those protections active, the writer rereads and verifies
its lock ID, PID, hostname, captured root, run ID, command, timestamp, and
filesystem identity; invokes one no-argument preparation callback; accepts only
prepared manifest/event/evidence data; performs semantic authorization; stages
and verifies exactly one segment; atomically renames it; rereads and verifies
the committed segment; and releases the lock. The callback receives no lock,
token, registry, file descriptor, commit function, or reusable commit
capability.

Committed segments are append-only. There is no official-pick update or overwrite
API. Any future correction to the published pick identity must append an
`OFFICIAL_PICK_CORRECTION_RECORDED` lifecycle event that references the event it
corrects; it must never rewrite the published `OfficialPick`. Published-pick
correction authoring is intentionally not activated. Settlement corrections
use the distinct `OFFICIAL_PICK_SETTLEMENT_CORRECTION_RECORDED` event.

## Explicit Promotion API

The call site is:

```python
from courtvision.official_picks import (
    promote_candidate_to_official_pick,
    review_official_pick_candidate,
)

review = review_official_pick_candidate(
    validated_candidate,
    operator_decision="APPROVED",
    operator_id="operator.alice",
    decision_reason="Candidate identity and market evidence verified.",
    review_run_id="paper-review-2026-07-26-a",
    lifecycle_root="data/lifecycle",
)

result = promote_candidate_to_official_pick(
    validated_candidate,
    review_id=review.review.review_id,
    lifecycle_root="data/lifecycle",
)
```

The validated candidate contains the intended `PAPER` or `RESEARCH`
designation before review. The review stores it as `approved_designation` and
in the frozen snapshot. Promotion cannot supply or change designation
independently.

`promote_observation_to_official_pick(...)` is a fail-closed compatibility
entrypoint and always rejects observations.

Promotion:

1. requires a committed `APPROVED` review and matching candidate snapshot;
2. generates the deterministic idempotency key;
3. returns an existing pick for an identical replay;
4. validates event, market, selection, line, odds, timing, model/run identity,
   review evidence, source provenance, and designation;
5. assigns `pick_id` and `published_at`;
6. publishes one lifecycle event transactionally; and
7. returns the persisted pick, event ID, status, idempotency key, and ledger
   segment path.

No prediction or collection pipeline calls this function. Promotion is a
deliberate operation with its own event and actor provenance.

## Grading, Reporting, And Settlement Boundaries

`courtvision.official_picks.reporting` provides the shared safe boundaries:

- `build_official_pick_report_dataset(...)` includes only validated official
  pick-v2 objects, emits flattened scalar-only rows, and reports explicit
  exclusion counts for observations, candidates, settlements, and legacy
  unidentified rows.
- `require_official_pick_roi_rows(...)` fails if a caller attempts to mix any
  non-pick record into official-pick ROI.
- `observation_performance_metadata()` requires the label `observation
  performance` and forbids a betting-ROI claim.
- `candidate_performance_metadata()` requires the label `model candidate
  analysis` and forbids a betting-ROI claim.
- `validate_settlement_pick_reference(...)` requires `pick_id` and verifies
  that it exists in the committed ledger.
- `build_official_pick_settlement_dataset(...)` reconstructs committed picks
  and settlement state from the ledger, joins strictly by `pick_id`, and reports
  unresolved official picks through flattened scalar-only rows.

Report structure excludes bankroll, Kelly-sizing, stake, wager amount,
expected-profit, ROI, live-bet, wagering-metadata, and execution-instruction
fields. Raw provenance and arbitrary mappings are never embedded. Descriptive
scalar values are not censored by substring, so a person named “Kelly” or a
decision reason that says Kelly staking is prohibited remains intact.

This phase does not rewrite the existing NBA or MLB grading pipelines. It
creates the strict contract those pipelines must use if they are migrated.
See `official_pick_settlement_lifecycle.md` for settlement and correction rules.

## Legacy Migration Policy

Historical rows are not backfilled with guessed IDs and historical MLB market
observations are not converted to picks. `adapt_legacy_unidentified(...)`
returns a read-only copy labeled:

```text
record_kind=LEGACY_UNIDENTIFIED
official_pick_identity_status=legacy_unidentified
```

It never creates a `pick_id`.

No production historical OfficialPick schema-v1 segment was found in the local
lifecycle inventory. Active writers and readers therefore reject all v1
OfficialPick events. A schema-v1 parser remains isolated in
`courtvision.official_picks.legacy_v1` solely for a future audited
migration/import procedure. It returns the distinct compatibility-only
`LegacyOfficialPickV1` DTO, is not re-exported by the normal package, and is
rejected by active reporting, settlement, promotion, and lifecycle readers.
No environment variable, runtime configuration,
manifest claim, provenance field, payload field, or caller input can extend
active compatibility.

## Current Limitations

- No pipeline automatically produces official picks.
- The explicit operator review and paper/research promotion APIs are available;
  no runtime or dashboard invokes them automatically.
- `operator_id` is an asserted audit identity, not an authenticated principal.
  Operational dashboard, CLI, and API approval surfaces remain disabled until
  an authenticated capability is available.
- Live designation and bankroll/Kelly output remain blocked.
- Append-only paper/research settlement and correction events are implemented,
  but no existing grader invokes them automatically.
- The official settlement dataset does not calculate ROI, Kelly, stake, profit,
  or bankroll values.
- Existing legacy grading reports have not been migrated wholesale.
- No historical IDs are backfilled.
- Forward paper-trial evidence is still required before any live-readiness
  claim.
- The frozen-mapping and locked-transaction guarantees are Python object/API
  invariants, not claims of protection against arbitrary process-memory
  corruption, malicious native code, operating-system compromise, or direct
  out-of-band lifecycle-file modification.

