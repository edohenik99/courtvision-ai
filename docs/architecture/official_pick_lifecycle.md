# Official Pick Identity And Lifecycle

Status: implemented foundation, paper/research only.

This document describes the canonical official-pick identity layer. It does not
authorize live wagering, Kelly sizing, bankroll use, automatic candidate
promotion, settlement completion, or claims of forward predictive validity.

## Four Distinct Record Kinds

| Record kind | Meaning | Is an official pick? | May be described as betting ROI? |
| --- | --- | --- | --- |
| `MARKET_OBSERVATION` | A sportsbook quote or other observed market state. | No | No; label results `observation performance`. |
| `MODEL_CANDIDATE` | A model prediction/candidate/board row that has not been promoted. | No | No; label results `model candidate analysis`. |
| `OFFICIAL_PICK` | A validated candidate or observation reference explicitly promoted and transactionally published. | Yes | Only through an official-pick-only report boundary. |
| `SETTLED_OFFICIAL_PICK` | A settlement lifecycle record linked to a committed official pick by `pick_id`. | It is the settlement of one | Only after the settlement layer validates the referenced `pick_id`. |

MLB sportsbook HR rows remain `MARKET_OBSERVATION` records. NBA prediction
boards remain `MODEL_CANDIDATE` outputs. Neither pipeline imports or invokes the
promotion service automatically.

## Canonical `OfficialPick` Schema

Schema version: `1`.

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
| `source_candidate_id`, `source_observation_id` | Exactly one is required for promotion. |
| `status` | `PUBLISHED` in schema v1. Settlement does not mutate this record. |
| `designation` | `PAPER` or `RESEARCH`. `LIVE` is rejected because no live gate is active. |
| `idempotency_key` | Separate deterministic promotion identity; not the `pick_id`. |
| `provenance` | Source type/ID, promotion actor/service/policy, plus optional source hashes/version refs. |
| `record_kind` | Always `OFFICIAL_PICK`. |
| `schema_version` | Always `1`. |

`OfficialPick` is a frozen dataclass. Re-reading a committed event reconstructs
and revalidates the same `pick_id`; it is never derived again from line, odds,
status, or other mutable-looking business fields.

## `pick_id` Versus Idempotency Key

The two identifiers have different jobs:

- `pick_id` is a UUID4-based globally unique identity assigned only when the
  promotion operation creates the official pick.
- `idempotency_key` is a deterministic SHA-256 identity generated from
  promotion-policy version, sport, league, source type, source ID, and
  designation.

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

Committed segments are append-only. There is no official-pick update or overwrite
API. Any future correction must append an
`OFFICIAL_PICK_CORRECTION_RECORDED` lifecycle event that references the event it
corrects; it must never rewrite the published `OfficialPick`. Correction
authoring is intentionally not activated in this phase.

## Explicit Promotion API

The call site is:

```python
from courtvision.official_picks import promote_candidate_to_official_pick

result = promote_candidate_to_official_pick(
    validated_candidate,
    lifecycle_root="data/lifecycle",
    designation="PAPER",
    promotion_actor="courtvision.operator",
)
```

For a sportsbook observation, the named
`promote_observation_to_official_pick(...)` entrypoint additionally requires
`source_observation_id` and rejects a candidate source reference.

Promotion:

1. requires exactly one trustworthy source reference;
2. generates the deterministic idempotency key;
3. returns an existing pick for an identical replay;
4. validates event, market, selection, line, odds, timing, model/run identity,
   source provenance, and designation;
5. assigns `pick_id` and `published_at`;
6. publishes one lifecycle event transactionally; and
7. returns the persisted pick, event ID, status, idempotency key, and ledger
   segment path.

No prediction or collection pipeline calls this function. Promotion is a
deliberate operation with its own event and actor provenance.

## Grading, Reporting, And Settlement Boundaries

`courtvision.official_picks.reporting` provides the shared safe boundaries:

- `build_official_pick_report_dataset(...)` includes only validated official
  picks and reports explicit exclusion counts for observations, candidates,
  settlements, and legacy unidentified rows.
- `require_official_pick_roi_rows(...)` fails if a caller attempts to mix any
  non-pick record into official-pick ROI.
- `observation_performance_metadata()` requires the label `observation
  performance` and forbids a betting-ROI claim.
- `candidate_performance_metadata()` requires the label `model candidate
  analysis` and forbids a betting-ROI claim.
- `validate_settlement_pick_reference(...)` requires `pick_id` and verifies
  that it exists in the committed ledger.

This phase does not rewrite the existing NBA or MLB grading pipelines. It
creates the strict contract those pipelines must use in the settlement phase.

## Legacy Migration Policy

Historical rows are not backfilled with guessed IDs and historical MLB market
observations are not converted to picks. `adapt_legacy_unidentified(...)`
returns a read-only copy labeled:

```text
record_kind=LEGACY_UNIDENTIFIED
official_pick_identity_status=legacy_unidentified
```

It never creates a `pick_id`.

## Current Limitations

- No pipeline automatically produces official picks.
- Live designation and bankroll/Kelly output remain blocked.
- Settlement lifecycle events and official-pick ROI computation are not
  implemented here; only their identity/reporting boundaries are.
- Existing legacy grading reports have not been migrated wholesale.
- No historical IDs are backfilled.
- Forward paper-trial evidence is still required before any live-readiness
  claim.

