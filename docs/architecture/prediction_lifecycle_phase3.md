# Prediction Lifecycle Phase 3: Prospective Source Observations

## Status and authority

Phase 3 is additive, prospective, and shadow-only. The existing CourtVision
CSV/runtime pipeline remains operationally authoritative for prediction
selection, projections, edge, confidence, Elite qualification, Kelly,
bankroll, exposure, schedule gates, injury adjustments, publication, grading,
settlement, void handling, histories, and operator outputs.

The observation feature defaults off. Phase 2 publication-only behavior
continues when lifecycle shadow mode is enabled without Phase 3 observations.
No lifecycle observation is consumed by the canonical prediction pipeline.

## Pre-implementation map

This map was recorded before Phase 3 code changes.

### Schedule and game state

| Boundary | Current runtime object and fields | Phase 3 use |
|---|---|---|
| Provider fetch | `CourtVisionAI.predict()` calls `client.get_games(prediction_date)` and retains `games_raw` | Best available provider-shaped schedule source |
| Canonical normalization | `CourtVisionAI._normalize_games()` delegates to `normalize_games_frame()` | `game_id`, home/visitor abbreviations, raw `status`, `date`, `datetime`, and `game_status_bucket` |
| Canonical consumption | `PredictionPipeline.run(games=games, ...)` builds game lookup records | Observation adapter reads the same already-available values before the pipeline consumes them |
| Provider event identity | BallDontLie `id`, normalized as `game_id` | Provider ID is retained and is converted to a CourtVision event ID only when deterministic identity v1 accepts it |

The current canonical schedule path does not reliably retain provider-reported
schedule timestamps, season, venue, or doubleheader sequence. Those fields
remain null. Team/date fallback is not used to resolve a missing event ID.
Raw and normalized status remain separate. Schedule observations do not alter
the existing status gate or the known `now=None` behavior.

### Market quotes

| Boundary | Current runtime object and fields | Phase 3 use |
|---|---|---|
| Provider fetch/adapter | `BallDontLieClient.get_odds()` calls the v2 player-props endpoint and passes provider rows through `normalize_bdl_player_props()` | The adapter output is the earliest complete object currently returned to the canonical runtime |
| Side normalization | One provider over/under row becomes side-specific rows | `game_id`, `player_id`, player/team identity, raw prop/market values, normalized market, selection, line, odds, vendor/bookmaker key, `updated_at` |
| Canonical filtering | `CourtVisionAI._normalize_odds()` calls `filter_valid_odds()` | Phase 3 retains the provider-adapter rows and the canonical normalized interpretation without changing filtering |
| Canonical consumption | `PredictionPipeline.run(odds=odds, ...)` | Observation adapter reads the same values before pipeline consumption |

The current BallDontLie client discards the complete HTTP response after
normalization. Phase 3 therefore records normalized-only evidence for this
path and does not claim full raw retention. `line_source=live_market` on a
board or candidate is not treated as a bookmaker. A bookmaker identity is
resolved only from an explicit vendor/bookmaker key recognized by identity
v1.

### Player availability

| Boundary | Current runtime object and fields | Phase 3 use |
|---|---|---|
| Primary fetch | `_get_sdk_injuries()` flattens SDK injury rows | Player ID/name, team ID/abbreviation, status, description, return date |
| Existing fallback | When the SDK is empty or unusable, `client.get_injuries()` supplies the current HTTP fallback | Phase 3 observes the source selected by the existing fallback without changing precedence |
| Canonical normalization | `normalize_injuries_frame()` | Canonical runtime fields plus normalization diagnostics |
| Canonical consumption | `PredictionPipeline.run(injuries=injuries, ...)` builds current injury context | Observation adapter reads the selected raw/normalized frames before pipeline consumption |

The current source does not reliably provide event ID, lineup status, starter
status, participation status, provider-reported timestamp, or a distinct
effective timestamp. Those fields remain null. `OUT` and `NOT_STARTING` are
availability facts only and never create DNP, void, grading, or settlement
events.

### Existing Phase 2 publication boundary

`courtvision_ai.main()` begins the optional shadow run before
`CourtVisionAI.predict()`. It calls `publish_shadow_after_board()` only after
the protected dated Elite board has been written successfully. Phase 2 then
builds `PREDICTION_PUBLISHED` payload v1 and commits one immutable run segment.

Phase 3 keeps that boundary. Observation payloads and evidence are prepared
outside the writer lock from the already-available canonical runtime objects.
When observation capture is enabled, schedule, quote, availability, and
publication events are committed in the same immutable prediction-run
segment. This is the bounded batch: one canonical prediction run containing
the provider data fetched for that run.

Observations are committed even when the successful board contains zero
Elite rows. No `PREDICTION_PUBLISHED` event is fabricated.

## Linking and compatibility decision

New observation-enabled runs use `PREDICTION_PUBLISHED` payload schema v2.
Payload v2 retains all v1 fields and adds explicit immutable observation
references. Phase 2 publication-only runs continue to write payload v1, and
readers/reconciliation remain compatible with both versions. No committed v1
segment is modified.

The observation adapter creates events before publication events within the
same hash chain. A publication may reference:

```text
schedule_observation_event_id
market_quote_observation_event_id
availability_observation_event_ids
```

Missing or unavailable observation data is explicit and degrades shadow
reconciliation. A link to the wrong event, participant, market, line, odds, or
evidence hash is an integrity failure.

## Observation semantics

An observation records a provider/source claim and CourtVision's interpretation
of that claim at capture time. It is not a declaration of universal truth and
is not a lifecycle transition. Later observations append new events; they do
not update or supersede earlier bytes.

Every observation separates:

- the provider-shaped source row;
- the provider timestamp, when an aware timestamp was actually supplied;
- the CourtVision ingestion timestamp;
- the normalized interpretation;
- canonical identity resolution;
- the final immutable recording time in the event envelope.

Unavailable source timestamps are null. They are never copied from ingestion
time or filesystem metadata. Naive/invalid source timestamp values do not
become provider timestamps. The ingestion and recording clocks must be
timezone-aware.

## Event and envelope contracts

Phase 3 adds three values to the Phase 2 event-type enumeration without
changing event-envelope v1 fields or hashing:

```text
SCHEDULE_OBSERVED
MARKET_QUOTE_OBSERVED
PLAYER_AVAILABILITY_OBSERVED
```

Each event retains envelope v1's event/canonicalization/identity versions,
prediction-run and correlation IDs, sequence, occurrence/recorded/provider
times, deterministic idempotency key, payload JSON and SHA-256, source
references and hashes, code/config/model provenance, previous-event hash, and
event hash.

### Schedule payload v1

`schedule_observed_payload_v1.json` captures provider and canonical event
identity, sport/league/season/operating date, provider team IDs and names,
scheduled start, provider and ingestion timestamps, raw and normalized game
status, venue, doubleheader sequence, source evidence, retention level, and
normalization version.

Canonical event identity is derived only from a valid provider event ID.
Team/date matching is never authoritative. Missing doubleheader sequence,
venue, season, or timestamps remain null. Provider status and normalized
status are stored separately; normalized values are limited to:

```text
SCHEDULED DELAYED IN_PROGRESS FINAL POSTPONED CANCELLED SUSPENDED UNKNOWN
```

### Market quote payload v1

`market_quote_observed_payload_v1.json` captures provider and canonical
event/participant/market/bookmaker identity, unresolved fields, raw and
normalized market names, selection, line, American odds, implied probability,
live/synthetic/source flags, provider/market/ingestion times, and immutable
source evidence.

Line, odds, and implied probability are normalized through `Decimal`, emitted
as deterministic finite JSON numbers, and reject booleans, non-numeric values,
non-finite values, and zero American odds. Implied probability is quantized to
12 decimal places. Phase 3 performs no pricing-model conversion beyond the
American-odds calculation documented in the payload.

An explicit recognized bookmaker/vendor key is required for canonical
bookmaker identity. `line_source=live_market` is never interpreted as a
bookmaker. Missing or unknown bookmaker identity remains null and unresolved
without affecting the live board.

### Player availability payload v1

`player_availability_observed_payload_v1.json` captures provider and canonical
event/participant/team identity, unresolved/conflict fields, player/team
labels, raw and normalized availability, injury description, lineup/starter/
participation fields, provider/effective/ingestion times, and source evidence.

The conservative normalized vocabulary is:

```text
UNKNOWN ACTIVE AVAILABLE QUESTIONABLE DOUBTFUL OUT INACTIVE STARTING NOT_STARTING
```

Unmapped states such as `PROBABLE` remain `UNKNOWN`; their raw value is
preserved. `OUT` and `NOT_STARTING` do not imply DNP, void, grading, or
settlement. A supplied identity-conflict marker sets status to `CONFLICT` and
fails participant/team identity closed.

## Timestamp distinctions

| Timestamp | Meaning |
|---|---|
| `provider_reported_at_utc` | Aware source-reported update time, otherwise null |
| `scheduled_start_at_utc` | Source/canonical scheduled event start, when aware |
| `market_observed_at_utc` | Source quote observation time; currently the retained provider update time |
| `effective_at_utc` | Source availability effective time, when separately supplied |
| `ingested_at_utc` | Aware clock time at the beginning of run-batch preparation |
| envelope `occurred_at_utc` | Effective time, then provider time, then ingestion time, according to event type |
| envelope `recorded_at_utc` | Immutable segment construction/publication time |

Reconciliation verifies envelope/payload provider-time agreement and compares
retained board timestamps to linked observations where the board has the
corresponding schedule, market, or availability field.

## Idempotency

Observation identity v1 hashes the source name, provider identifiers, exact
raw/normalized observation values, material state fields, available provider
timestamps, and sanitized source-payload hash. Ingestion time is deliberately
excluded.

Within one prepared source/run batch, exact `(event_type,
observation_identity)` duplicates collapse. A duplicate with the same identity
but different content fails closed. Material changes to schedule time/status,
quote line/odds/bookmaker/timestamp, or availability status/detail/lineup/
timestamp produce distinct identities.

The final event idempotency key is:

```text
<event_type>:<prediction_run_id>:<observation_identity>
```

Run scoping is intentional because an event envelope belongs to exactly one
prediction run. Exact commit retry for that run returns `ALREADY_COMMITTED`;
conflicting content for an existing key returns an integrity failure. A later
prediction run may independently attest that it received the same provider
claim, while preserving the stable observation identity inside its payload.

## Batch and commit boundary

One `ObservationBatch` is prepared outside the writer lock for one canonical
prediction run from the provider data already fetched for that run. It records
raw/normalized source counts and row-level capture errors. Exact duplicates are
removed before materialization.

For an observation-enabled successful canonical publication, the immutable
segment order is:

```text
RUN_STARTED
zero or more observation events
zero or more PREDICTION_PUBLISHED v2 events
RUN_COMPLETED
```

The observations, publication links, evidence objects, manifest, and hash
chain commit atomically through the unchanged Phase 2 writer lock. A zero-row
Elite board can therefore commit source observations and a completed run
without fabricating `PREDICTION_PUBLISHED`.

## Source evidence and retention

All retained snapshots use the Phase 2 content-addressed evidence store and
are sanitized before hashing. Supported declarations are:

```text
FULL_RAW
SANITIZED_RAW
NORMALIZED_ONLY
HASH_REFERENCE_ONLY
```

The current runtime uses `SANITIZED_RAW` for retained provider-shaped schedule
and selected availability rows, and `NORMALIZED_ONLY` for BallDontLie market
adapter rows because the complete HTTP response is no longer available at the
capture boundary. Phase 3 does not claim full raw retention.

The payload's `source_payload_sha256` hashes the sanitized source claim plus
normalized interpretation. The envelope also binds the content-addressed
evidence-object SHA-256 and `evidence://sha256/...` reference.

Keys matching authorization, API key, token, access token, secret, password,
cookie, or session forms are recursively replaced with `[REDACTED]`.

## Canonical identity and crosswalk limits

Phase 3 reuses identity schema v1 and existing reliable provider IDs. It adds
only the same-domain canonical team-ID helper used for availability evidence.
It does not use team/date guessing, fuzzy player matching, or a new
master-data system.

- provider event/player/team IDs become namespaced CourtVision IDs only when
  valid under identity v1;
- known market aliases use existing market normalization;
- known bookmaker aliases use the existing explicit bookmaker table;
- unresolved inputs remain null with `UNRESOLVED`;
- supplied conflicts remain null with `CONFLICT`;
- all supporting provider identifiers remain in the payload.

## Prediction linking and schema compatibility

`PREDICTION_PUBLISHED` payload v2 is used only when observation capture is
enabled. It retains the v1 publication content and adds
`observation_links` v1:

```text
schedule_observation_event_id
market_quote_observation_event_id
availability_observation_event_ids
link_status
missing_or_unavailable_reasons
capture_errors
```

Only exact, unambiguous matches are linked. The publication envelope binds
each linked event ID to its event hash. Missing, unresolved, ambiguous, or
failed capture produces explicit `DEGRADED` linkage. Phase 2 publication-only
runs still emit payload v1. Existing v1 schemas and committed segments are
never rewritten.

## Reconciliation

Phase 2 full-board reconciliation remains intact. Payload v2 additionally
verifies that linked events exist in the same committed event set, have the
expected type and bound event hash, and agree with the publication on all
available resolved fields:

- event and participant identity;
- market, selection, line, odds, and resolved bookmaker;
- scheduled start and normalized game status;
- availability participant/event/status;
- provider/source timestamps where retained;
- source reference, source-payload hash, and evidence-object hash.

`PASS` requires exact canonical publication, complete links, resolved
identities, and valid hashes. `DEGRADED` represents missing optional fields,
unresolved identity, legitimate source absence, or ordinary capture failure.
Contradictory links, wrong types/IDs/values, idempotency conflict, tampering, or
hash mismatch are `FAIL`. Reconciliation never changes selection, grading, or
settlement.

## Feature flags and failure semantics

| Shadow flag | Observation flag | Behavior |
|---|---|---|
| off | any | No lifecycle package import, lock, directory, or side effect |
| on | off/unset | Accepted Phase 2 publication-only behavior; payload v1 |
| on | on | Phase 3 capture and payload v2 linking |

Both flags default off. The observation module (and pandas dependency) is
imported only after both flags enable it.

Observation import/preparation failures are visible in logs and stored as
capture errors. The canonical board still writes and the shadow publication
commits explicit degraded/missing links when possible. Ordinary filesystem or
writer-busy failures remain `DEGRADED`; integrity/idempotency/tamper failures
remain `FAIL`. Neither classification changes canonical exit status or board
bytes.

## Storage and inspection

Phase 3 reuses the Phase 2 layout:

```text
data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>/
  events.jsonl
  run_manifest.json
  manifest.json
  COMPLETE
data/lifecycle/evidence/objects/<prefix>/<sha256>.json
data/lifecycle/reconciliation/YYYY/MM/DD/<prediction_run_id>.json
data/lifecycle/.writer.lock
```

`python -m courtvision.lifecycle.inspection` provides read-only commands to
list a run, list evidence linked to a prediction ID/key, verify a segment, show
event schedule history, show quote history for a prediction key, and show
availability history for a participant/event. Every inspection query verifies
each segment first and refuses invalid immutable data.

## Known limitations and stopping point

- The current BallDontLie adapter does not retain complete raw HTTP market
  responses.
- Provider timestamps, schedule venue/season/doubleheader, availability event
  IDs, and lineup/starter/participation fields are often absent.
- Historical operator boards omit bookmakers on all currently available rows
  and omit event identity on some rows; shadow linkage therefore degrades
  honestly.
- Identical claims in distinct prediction runs are separate run attestations,
  not one shared cross-run event.
- No authoritative current-state view, correction workflow, garbage
  collection, migration, database, dashboard, or scheduled workflow is added.
- The known `now=None` lock behavior is unchanged.

Phase 3 remains shadow-only. The CSV/runtime pipeline is operationally
authoritative. This phase does not introduce DuckDB, provider refactors,
settlement/grading/lock events, legacy migration, authoritative cutover, or
Phase 4 behavior.
