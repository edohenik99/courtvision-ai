# Official Pick Settlement Lifecycle

Status: implemented foundation, schema v1, paper/research only.

This lifecycle settles only committed `OfficialPick` records. It does not
authorize live betting, automatic promotion, Kelly sizing, bankroll output,
historical ID backfilling, or migration of existing NBA/MLB grading records.

## Settlement Schema

`OfficialPickSettlement` is a frozen schema-version `1` contract.

| Field | Rule |
| --- | --- |
| `settlement_id` | Immutable `settlement_<uuid4 hex>` assigned only for a new committed settlement event. |
| `pick_id` | Required `pick_<uuid4 hex>` and must exist in a verified `OFFICIAL_PICK_PUBLISHED` ledger event. |
| `settlement_status` | `UNRESOLVED` or `FINAL`. |
| `outcome` | `WIN`, `LOSS`, `PUSH`, `VOID`, `CANCELLED`, or `UNRESOLVED`. `UNRESOLVED` status and outcome must be used together. |
| `final_score` | Optional string or non-empty mapping. It must be null for `UNRESOLVED`. |
| `result_evidence` | Required non-empty mapping containing the facts used for the decision. |
| `settled_at` | Immutable timezone-aware UTC timestamp; cannot precede pick publication. |
| `result_source` | Required source identity. |
| `source_record_id` | Required stable source record/evidence identity. |
| `settlement_run_id` | Required result-collection or operator run identity. |
| `idempotency_key` | Deterministic transition identity with `opsetidem_` prefix. |
| `provenance` | Settlement actor, service, policy version, and optional source/code hashes. |
| `schema_version` | Always `1`. |
| `record_kind` | Always `SETTLED_OFFICIAL_PICK`. |

The event payload is frozen by
`official_pick_settled_payload_v1.json`. Each `OFFICIAL_PICK_SETTLED`
event includes the settlement record and a canonical settlement-content
SHA-256.

## Identity And Idempotency

`settlement_id` and the idempotency key serve different purposes:

- `settlement_id` is a UUID4 identity assigned once and reread from the
  committed event. It is never recalculated from outcome or evidence.
- the idempotency key is deterministic over settlement policy version,
  `pick_id`, and a transition slot.

The transition slots are:

- `INITIAL`: the first settlement event, whether final or unresolved;
- `FINALIZATION`: the final event after an unresolved event.

Using one `INITIAL` key for both possible first states prevents concurrent
initial `UNRESOLVED` and final events from both committing. Identical replay
returns the original settlement, event ID, and segment. Reuse of the same key
with different content raises `IDEMPOTENCY_CONFLICT`. The replay content hash
excludes only the generated `settlement_id`, `settled_at`, and idempotency key.

## State Transitions

| Current state | Requested event | Result |
| --- | --- | --- |
| Published official pick, no settlement | `UNRESOLVED` | Append initial unresolved settlement. |
| Published official pick, no settlement | Final outcome | Append initial final settlement. |
| Unresolved | Identical unresolved replay | Protected no-op; return committed unresolved settlement. |
| Unresolved | Final outcome | Append a new finalization settlement with a new `settlement_id`. |
| Final | Identical final replay | Protected no-op; return committed final settlement. |
| Final | Different final content | Fail with idempotency conflict; explicit correction required. |
| Final | New unresolved event | Fail; a final settlement cannot be silently replaced. |

There can be at most one unresolved event and one final event per `pick_id`.
Committed readers reject duplicate IDs, duplicate transition keys, unknown
pick references, final-before-unresolved ordering, malformed payloads, and
segments that fail file, event-hash, hash-chain, or manifest verification.

## Append-Only Publication API

The explicit call site is:

```python
from courtvision.official_picks import settle_official_pick

result = settle_official_pick(
    pick_id,
    outcome="WIN",
    result_source="verified.boxscore",
    source_record_id="game-123-boxscore-v1",
    settlement_run_id="result-run-2026-07-26",
    result_evidence={"player_points": 27, "game_status": "FINAL"},
    final_score={"away": 101, "home": 108},
    lifecycle_root="data/lifecycle",
)
```

The service validates the committed pick and all existing settlement state,
constructs one immutable event, commits through the lifecycle writer, then
rereads and verifies the committed segment before returning. Staging and
atomic rename provide rollback on publication failure. No in-place settlement
update API exists.

## Correction Policy

A final settlement is never overwritten. The only correction API is
`correct_official_pick_settlement(...)`, backed by the frozen
`OfficialPickSettlementCorrection` v1 contract and
`official_pick_settlement_correction_payload_v1.json`.

A correction requires:

- `correction_id`;
- `original_settlement_id`;
- matching committed `pick_id`;
- non-empty correction reason;
- final corrected outcome and corrected evidence;
- result source and source record ID;
- correction run ID, actor, service, and policy provenance; and
- a deterministic correction idempotency key.

It publishes
`OFFICIAL_PICK_SETTLEMENT_CORRECTION_RECORDED`. The payload references
`original_settlement_id`, while the envelope's `corrects_event_id` references
the original settlement event. Only final settlements may be corrected.
Unresolved-to-final progression uses a new settlement event, not correction.
Schema v1 permits at most one correction per original settlement; identical
correction replay is a no-op and conflicting replay fails.

`OFFICIAL_PICK_CORRECTION_RECORDED` remains reserved for a future correction to
the published pick identity record and is not used by settlement correction.

## Ledger Location And Integrity

Settlement and correction events use the existing ledger:

```text
data/lifecycle/ledger/YYYY/MM/DD/<settlement-transaction-id>/
  COMPLETE
  events.jsonl
  manifest.json
  run_manifest.json
```

The partition date is the settlement/correction date in
`America/Toronto`. The writer provides its existing lock, staged writes,
atomic rename, global idempotency scan, canonical JSON, file hashes, event
hash chain, segment manifest, rollback, and pre/post-commit verification.
Committed segment bytes are append-only and must not be rewritten.

## Unresolved MLB Reconciliation

`MLBOfficialPickReconciliationItem` and
`MLBOfficialPickReconciliationQueue` form a strict schema-version `1` queue
model for unresolved MLB official picks. Supported reasons are:

- `game_not_final`;
- `player_missing_from_boxscore`;
- `event_not_matched`;
- `ambiguous_player_identity`;
- `source_unavailable`; and
- `manual_review_required`.

Queue construction requires an already committed MLB `pick_id` and rejects
candidate IDs, observation IDs, unknown IDs, non-MLB picks, and picks with a
final settlement. The queue does not scan or convert sportsbook observations.
It does not call a provider. Queue persistence and automated reconciliation
workers are not activated in this phase.

## Official-Pick-Only Reporting Boundary

`build_official_pick_settlement_dataset(...)` reconstructs official picks,
settlements, and corrections from verified lifecycle events. It joins only on
committed `pick_id`.

The dataset:

- returns final rows separately from unresolved official picks;
- applies a valid correction only as an explicit effective outcome while
  preserving the original settlement;
- excludes observation, unpromoted candidate, and legacy rows;
- ignores caller-supplied settlement-looking rows as join authority;
- does not mix existing MLB observation grading into official-pick performance;
  and
- calculates no ROI, stake, Kelly, profit, or bankroll values.

Historical MLB observation results are not migrated or assigned `pick_id`
values.

## Current Limitations

- The lifecycle is paper/research-only.
- No existing NBA or MLB grading pipeline calls this service automatically.
- No candidate pipeline automatically promotes picks.
- The MLB reconciliation queue is a strict model, not a persisted worker queue.
- Settlement policy v1 records outcomes and evidence but does not claim a
  universal bookmaker-specific void-rule registry.
- No historical identifiers or settlements are backfilled.
- A controlled prospective paper trial is still required before any live or
  bankroll-readiness claim.
