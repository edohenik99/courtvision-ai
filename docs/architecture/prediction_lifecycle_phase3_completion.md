# Prediction Lifecycle Phase 3 Completion Report

## Verdict

**PASS WITH KNOWN PRE-EXISTING FAILURES**

Phase 3 prospectively captures immutable schedule, market, and player-
availability observations and links exact contemporaneous evidence to new
shadow publications. It remains opt-in and non-authoritative. No current
betting decision, provider precedence/fallback, schedule/injury gate, Kelly,
bankroll, grading, settlement, void, history, run script, dashboard, or
scheduled task was changed. DuckDB was not installed, Phase 4 was not started,
and no Git commit was created.

This report describes the Phase 3 delta relative to the accepted dirty/
untracked Phase 2 working-tree baseline.

## 1. Exact files created

```text
courtvision/lifecycle/observations.py
courtvision/lifecycle/inspection.py
courtvision/lifecycle/schemas/schedule_observed_payload_v1.json
courtvision/lifecycle/schemas/market_quote_observed_payload_v1.json
courtvision/lifecycle/schemas/player_availability_observed_payload_v1.json
courtvision/lifecycle/schemas/prediction_published_payload_v2.json
tests/test_lifecycle_observations.py
docs/architecture/prediction_lifecycle_phase3.md
docs/architecture/prediction_lifecycle_phase3_completion.md
docs/operations/lifecycle_observation_runbook.md
```

## 2. Exact files modified

```text
courtvision_ai.py
courtvision/shadow_lifecycle.py
courtvision/lifecycle/identity.py
courtvision/lifecycle/models.py
courtvision/lifecycle/evidence.py
courtvision/lifecycle/publication.py
courtvision/lifecycle/reconciliation.py
courtvision/lifecycle/schemas/event_envelope_v1.json
tests/test_lifecycle_import_isolation.py
```

The event-envelope v1 field/hash meaning was not changed; its allowed event
enumeration was extended. Publication payload v1 and every accepted Phase 2
committed-segment meaning remain unchanged.

## 3. Integration points

`CourtVisionAI.predict()` already owns the canonical source values:

- `games_raw` from `client.get_games()` and `games` from
  `normalize_games_frame()`;
- provider-adapter `odds_raw` from `client.get_odds()` and `odds` after the
  existing bad-player-name filter and `filter_valid_odds()` normalization;
- the already-selected SDK or HTTP injury fallback `injuries_raw` and
  `normalize_injuries_frame()` result.

When both lifecycle flags enable observations, `courtvision_ai.main()` installs
an optional observer callable before `predict()`. The callable receives copies/
references to those already-available frames at provider-fetch completion.
It does not intercept, mutate, reorder, replace, or gate canonical data.

After `_write_cli_outputs()` successfully writes the dated Elite board, the
accepted Phase 2 publication hook commits prepared observations and
publication evidence. Observation preparation happens outside the writer
lock. Canonical publication still succeeds when observation import or capture
degrades.

## 4. Schedule observation schema

`SCHEDULE_OBSERVED` payload v1 captures:

```text
canonical_event_id
event_identity_resolution_status
provider_name
provider_event_id
league
sport
season
operating_date
home_team_id
away_team_id
home_team_name
away_team_name
scheduled_start_at_utc
provider_reported_at_utc
ingested_at_utc
game_status_raw
game_status_normalized
doubleheader_sequence
venue
source_payload_ref
source_payload_sha256
evidence_retention_level
normalization_version
observation_identity
prediction_run_id
correlation_id
```

Provider raw status is retained separately from the normalized status.
Canonical event identity requires a provider event ID; team/date is not used
as authoritative fallback. Missing doubleheader/venue/season/timestamps are
null.

## 5. Market quote observation schema

`MARKET_QUOTE_OBSERVED` payload v1 captures canonical and provider event,
participant, market, and bookmaker identifiers; resolution status and
unresolved fields; sport/league; raw/normalized market; selection; line;
American odds; implied probability; live/synthetic/source flags; distinct
provider/market/ingestion timestamps; source evidence; retention/
normalization versions; observation identity; and run/correlation IDs.

Finite numeric values use deterministic `Decimal` normalization. Zero American
odds and non-finite/non-numeric values fail closed. Implied probability is
quantized to 12 decimal places. `line_source=live_market` is not a bookmaker.

## 6. Availability observation schema

`PLAYER_AVAILABILITY_OBSERVED` payload v1 captures canonical/provider event,
participant, and team identity; resolution status/unresolved fields; player/
team labels; raw and normalized availability; injury type/detail; lineup,
starter, and participation state; provider/effective/ingestion times; source
evidence; retention/normalization versions; observation identity; and
run/correlation IDs.

Normalization is conservative. `PROBABLE` remains `UNKNOWN`; raw source status
is preserved. `OUT` and `NOT_STARTING` do not produce DNP, void, grading, or
settlement. Explicit identity conflicts yield `CONFLICT` with null canonical
participant/team identity.

## 7. Event idempotency definitions

The stable observation identity excludes ingestion time and includes source,
provider IDs, material raw/normalized state, available source timestamps, and
sanitized source-payload hash.

- schedule changes in start/status/timestamp/source content create a new ID;
- quote changes in event/participant/market/selection/line/odds/bookmaker/
  timestamp/source content create a new ID;
- availability changes in status/injury/lineup/starter/participation/
  timestamp/source content create a new ID.

Exact duplicates collapse inside the run/source batch. The final key is
`<event_type>:<prediction_run_id>:<observation_identity>`. Exact run retry is
`ALREADY_COMMITTED`; same key/different content is an integrity conflict.
Different runs remain separate attestations while carrying the same stable
observation identity when their source claim is identical.

## 8. Observation batch boundary

One bounded `ObservationBatch` represents the schedule, market, and selected
availability source frames obtained for one prediction run. Preparation and
deduplication occur outside the lock.

The atomic immutable segment order is `RUN_STARTED`, observation events,
publication v2 events, then `RUN_COMPLETED`. The latter records category
counts, provider/canonical source-row counts, and capture errors. A zero-Elite
board still commits observations without fabricating publication.

## 9. Publication-linking design

New observation-enabled runs use `PREDICTION_PUBLISHED` payload v2. It retains
v1 content and adds:

```text
observation_links.observation_link_schema_version
observation_links.link_status
observation_links.schedule_observation_event_id
observation_links.market_quote_observation_event_id
observation_links.availability_observation_event_ids
observation_links.missing_or_unavailable_reasons
observation_links.capture_errors
```

Only exact and unambiguous matches link. The publication envelope binds each
linked event ID to its event hash. Missing/unresolved/ambiguous evidence is
explicitly `DEGRADED`; no observation is fabricated.

## 10. Schema-version compatibility

Event envelope, identity, canonical JSON, run manifest, evidence, writer, and
reconciliation remain version 1. The event-type enum is additive.

Lifecycle shadow on with observations off continues to write
`PREDICTION_PUBLISHED` v1. Observation-enabled runs write v2. Readers and
reconciliation accept both. Tests freeze the v1 schema constant and verify
that old v1 publication remains readable. No Phase 2 segment is rewritten.

## 11. Source/evidence retention behavior

The existing content-addressed store is reused:

```text
data/lifecycle/evidence/objects/<prefix>/<sha256>.json
```

Provider-shaped schedule and selected availability rows are retained as
`SANITIZED_RAW` when available. BallDontLie market rows are
`NORMALIZED_ONLY`, because the full HTTP response is discarded before the
canonical client returns. Phase 3 does not claim `FULL_RAW`.

Every observation carries a sanitized source-claim/normalized-interpretation
hash, immutable evidence reference, and evidence-object hash.

## 12. Canonical identity coverage

Phase 3 reuses identity v1 provider event/player IDs, existing market aliases,
and the explicit known-bookmaker table. A same-domain canonical team-ID helper
was added for availability evidence. No fuzzy guessing, team/date event
resolution, provider precedence change, or new master-data platform was added.

Unknown values remain `UNRESOLVED`. Explicit conflicts remain `CONFLICT`.
Supporting provider IDs remain present.

## 13. Unresolved identity counts

The deterministic focused fixture produces one schedule, one quote, and one
availability observation, all identity-resolved.

The current available canonical operator-board sample
(`outputs/runtime/operator/elite_board_*.csv`) contains 39 files and 65 rows:

```text
event identity missing       32 / 65
participant identity missing  0 / 65
market identity missing       0 / 65
bookmaker identity missing   65 / 65
line_source=live_market      65 / 65
```

`live_market` was not promoted to bookmaker identity. These counts describe
available historical/current artifacts, not a migrated lifecycle history.

## 14. Reconciliation behavior

Publication v1 retains accepted Phase 2 reconciliation. V2 additionally
verifies linked event existence/type/hash, source/evidence bindings, event and
participant identity, market/selection/line/odds/resolved bookmaker, scheduled
start/game status, availability state, and provider timestamps when
corresponding board fields exist.

- `PASS`: exact board, complete/resolved links, matching values, valid hashes.
- `DEGRADED`: exact canonical publication with unavailable/unresolved/
  ambiguous optional evidence or ordinary capture failure.
- `FAIL`: contradiction, wrong link/type/identity/value, idempotency conflict,
  tampering, or source/evidence/event hash mismatch.

Reconciliation is read-only with respect to selection, grading, and
settlement.

## 15. Feature-flag behavior

```text
COURTVISION_LIFECYCLE_SHADOW=0/unset
  no lifecycle import, dependency, lock, directory, or event

COURTVISION_LIFECYCLE_SHADOW=1
COURTVISION_LIFECYCLE_OBSERVATIONS=0/unset
  accepted Phase 2 publication-only behavior, payload v1

COURTVISION_LIFECYCLE_SHADOW=1
COURTVISION_LIFECYCLE_OBSERVATIONS=1
  Phase 3 capture, observation events, payload v2
```

The observation flag defaults off. Subprocess tests prove observations are
not imported when only Phase 2 is enabled.

## 16. Failure semantics

Observation import or preparation failure is logged with stage and
classification, canonical execution continues, and publication v2 records
degraded missing links/capture errors where possible. Source absence yields
absent/null observations and source counts, never invented content.

Ordinary writer-busy/filesystem failures remain `DEGRADED`. Idempotency
conflict, invalid committed bytes, wrong links, and hash/tamper conflicts are
`FAIL`. Neither status changes canonical board bytes or successful canonical
exit behavior.

## 17. Security/sanitization behavior

Evidence recursively redacts normalized key forms ending in:

```text
authorization api_key apikey token access_token secret password cookie session
```

Tests verify redaction, including `session`, and ensure values do not survive
in evidence bytes. All source/evidence and event hashes are reverified by
inspection/reconciliation. No environment credential or provider key is
captured intentionally.

## 18. Tests added

`tests/test_lifecycle_observations.py` contains 65 focused cases covering:

- schedule determinism/dedup/change/null-time/aware-time/unresolved/
  doubleheader/raw-versus-normalized behavior;
- quote dedup/movement/bookmaker/live-market/numeric/timestamp/intraday/raw
  identity behavior;
- availability dedup/state changes/conservative normalization/no DNP/void/
  settlement/conflict/immutability;
- v2 linking, wrong links/values/timestamps, degraded missing sources, zero
  board, and v1 compatibility;
- feature flags/import boundaries, exact writer retry, Phase 2 segment
  immutability, tamper detection, evidence sanitization, schema/envelope
  fields, and read-only inspection;
- runtime board-byte parity and capture-failure continuation.

`tests/test_lifecycle_import_isolation.py` adds the fresh-process Phase 2-only
import boundary for the new observation module. Existing Phase 2 writer tests
continue to cover exclusive locking, invisible incomplete staging, exact
retry, conflicting retry, and atomic commit; the Phase 3 integration test
proves observation events use that unchanged writer.

## 19. Focused test results

```text
Phase 3 observation file:
  65 passed

Combined Phase 2 + Phase 3 lifecycle suite:
  146 passed

Compilation:
  passed

git diff --check:
  passed
```

The combined total includes canonical identity, evidence/writer,
import-isolation, observation, publication/reconciliation, and runtime-
integration tests.

## 20. Canonical parity results

The accepted risk-matched suite recorded:

```text
196 passed, 3 expected xfailed
```

Provider/normalization plus targeted NBA/MLB research validation recorded:

```text
185 passed
```

Runtime plumbing tests compare canonical board bytes with observation capture
off/on and with forced capture failure; bytes and successful exit behavior are
identical. Selection, row order, line, odds, projection, confidence,
qualification, Kelly eligibility, recommended stake, history, grading, and
lock behavior were not changed.

Post-validation SHA-256/length checks exactly match the pre-Phase-3 freeze:

```text
data/history/prediction_history.csv
  7,345,502 bytes
  9164e9f9399741a3fab8aa1819fddbf0ebf45a8c08b054a4ede6bbae4de6a65b
data/history/pick_history.csv
  41,519 bytes
  11a09947247110e96e621cb91bdc6fd9c6a6eb6c23dd2c1ec8a7607a698b531b
data/history/market_shadow_history.csv
  705,587 bytes
  2fb67e676ac0ff9b5a2436e18a81706f8be16f9e04285745c61f874ec3f87c89
data/history/evidence_ledger.csv
  642 bytes
  b1d3c221ee731c86ee39d7025bf405d06384c3dd006d80d42d7d5d38961f0673
courtvision/runtime_gates.py
  17,444 bytes
  e633462fedd4a795d952bf1bc99f68024848ff4ef88488ad2180ad4d8385239b
courtvision/pipeline/predict_pipeline.py
  108,901 bytes
  cff7390419b1dbb190b7983ea3147dcbdda9f1834c5d2ea6cad6b50b7eb064ce
```

The direct known-defect check still returns:

```text
_is_before_lock_buffer("2026-07-25", None, 10) == True
```

No real `data/lifecycle/` directory was created by validation.

## 21. Full/chunked suite results

The current repository collects 4,205 tests. The accepted bounded,
separate-process matrix, with the final G-L observation delta included, is:

| Chunk | Result |
|---|---:|
| top-level A-F | 947 passed |
| top-level G-L | 599 passed |
| top-level M | 671 passed |
| top-level N-O (workspace temp) | 487 passed, 23 failed, 8 skipped |
| top-level P-R | 876 passed |
| top-level S-Z | 368 passed |
| `tests/stable` | 53 passed |
| `tests/experimental` | 64 passed, 20 expected xfailed |
| `tests/legacy` | 78 passed, 11 expected xfailed |
| **Workspace-temp aggregate** | **4,143 passed, 23 failed, 8 skipped, 31 expected xfailed** |

Because every failure was in the existing Windows/environment N-O cluster, it
was rerun against a unique actual system-temp root:

```text
504 passed, 5 failed, 9 skipped
```

Substituting that rerun yields:

```text
4,160 passed, 5 failed, 9 skipped, 31 expected xfailed
```

The suite is not represented as fully green. All lifecycle, canonical, and
other alphabetical/directory chunks passed.

## 22. Remaining pre-existing failures

The workspace-temp failures are confined to the same six unchanged,
pre-accepted NBA research/environment modules:

```text
tests/test_nba_player_points_closing_evidence.py
tests/test_nba_player_points_evidence_writer.py
tests/test_nba_player_points_rehearsal_integration.py
tests/test_nba_player_points_research_runner.py
tests/test_nba_player_points_settlement_closing_binding.py
tests/test_nba_player_points_settlement_evidence.py
```

Their signatures are Windows atomic-directory rename/path timing,
system-temp-only preview policy, and synthetic child-process import
environment behavior. Moving the N-O base to actual system temp reduced 23
failures to five, confirming environmental sensitivity. None of those modules
or their production implementation files was modified by Phase 3, and no
failure traversed lifecycle observation code.

The accepted Phase 2 matrix recorded 19 failures in this same nondeterministic
cluster. The different count is classified as the same pre-existing Windows
environment class, not as a new canonical/lifecycle regression.

## 23. Known limitations

- Complete raw market HTTP responses are unavailable at the current adapter
  boundary.
- Many source rows lack provider timestamps, venue/season/doubleheader,
  availability event ID, or lineup/starter/participation state.
- Current board artifacts frequently omit event identity and universally omit
  bookmaker identity; linkage therefore degrades honestly.
- Exact provider claims in separate runs are separate run attestations.
- Evidence-object garbage collection is not implemented.
- No correction/supersession event or authoritative current-state view exists.
- No source-fetch success/failure event was necessary for this integration.
- The ledger is not authoritative and legacy history is not migrated.
- The known `now=None` lock defect is unchanged.

## 24. Recommended Phase 4 scope

Phase 4 should remain separately approved and should focus first on evidence
completeness, not authoritative cutover:

1. add prospective source-fetch outcome events with explicit batch/source IDs;
2. retain sanitized full raw provider payloads where contractual and safe;
3. improve deterministic event/bookmaker/player crosswalk coverage without
   changing live eligibility;
4. define append-only correction/supersession semantics;
5. build a read-only shadow current-state projection and observation coverage
   reports;
6. define soak/acceptance thresholds before considering any operational
   authority.

Settlement/grading events, lock-policy changes, legacy migration, database
installation, or authoritative selection/history cutover require distinct
approval and are not implied by this recommendation.

## Stopping point

Phase 3 implementation, testing, documentation, and completion reporting are
complete. Work stops here. Phase 4 has not begun.
