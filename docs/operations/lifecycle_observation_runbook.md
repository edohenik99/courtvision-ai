# Lifecycle Observation Runbook

## Authority and safe use

Phase 3 observation capture is optional and shadow-only. It records what the
canonical runtime received about schedules, market quotes, and player
availability. It does not determine picks, eligibility, Kelly, stake,
schedule locks, injury adjustments, grading, settlement, histories, or
operator output.

Use the existing CourtVision prediction command. Do not create a separate
observation process or scheduled task.

## Feature flags

Both flags must be enabled for Phase 3:

```powershell
$env:COURTVISION_LIFECYCLE_SHADOW = "1"
$env:COURTVISION_LIFECYCLE_OBSERVATIONS = "1"
```

Flag behavior:

```text
lifecycle off                         no lifecycle import or side effect
lifecycle on, observations off        Phase 2 publication-only payload v1
lifecycle on, observations on         Phase 3 observations + publication v2
```

Observation capture defaults off. To roll back only Phase 3 while retaining
Phase 2:

```powershell
$env:COURTVISION_LIFECYCLE_OBSERVATIONS = "0"
```

To disable all lifecycle behavior:

```powershell
$env:COURTVISION_LIFECYCLE_SHADOW = "0"
```

Disabling flags does not delete or edit committed evidence.

## Expected run output

The canonical board is written first. A successful observation-enabled shadow
segment then contains:

```text
RUN_STARTED
SCHEDULE_OBSERVED (zero or more)
MARKET_QUOTE_OBSERVED (zero or more)
PLAYER_AVAILABILITY_OBSERVED (zero or more)
PREDICTION_PUBLISHED v2 (zero or more)
RUN_COMPLETED
```

A successful zero-Elite run may contain observations without any
`PREDICTION_PUBLISHED`. This is expected and is not a fabricated prediction.

The standard locations remain:

```text
data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>/
data/lifecycle/evidence/objects/<prefix>/<sha256>.json
data/lifecycle/reconciliation/YYYY/MM/DD/<prediction_run_id>.json
data/lifecycle/.writer.lock
```

## Inspect observations

List observations for a run:

```powershell
python -m courtvision.lifecycle.inspection `
  --lifecycle-root data/lifecycle `
  run <prediction_run_id>
```

List observations linked to a prediction ID or prediction key:

```powershell
python -m courtvision.lifecycle.inspection `
  --lifecycle-root data/lifecycle `
  prediction <prediction_id_or_key>
```

Show schedule history by canonical or provider event ID:

```powershell
python -m courtvision.lifecycle.inspection `
  --lifecycle-root data/lifecycle `
  schedule <event_id>
```

Show market history for a prediction key:

```powershell
python -m courtvision.lifecycle.inspection `
  --lifecycle-root data/lifecycle `
  quote <prediction_key>
```

Show player availability history, optionally constrained to an event:

```powershell
python -m courtvision.lifecycle.inspection `
  --lifecycle-root data/lifecycle `
  availability <participant_id> --event-id <event_id>
```

Inspection verifies every committed segment it reads and fails closed if any
segment is invalid.

## Verify integrity

Verify all Phase 2/3 lifecycle segments:

```powershell
python -m courtvision.lifecycle.verify --lifecycle-root data/lifecycle
```

Verify one observation segment:

```powershell
python -m courtvision.lifecycle.inspection `
  --lifecycle-root data/lifecycle `
  verify data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>
```

Do not repair, delete, or rewrite a committed segment or evidence object.
Preserve failed evidence for investigation.

## Read observation and link status

The `RUN_COMPLETED` payload contains per-category observation counts, source
row counts, and capture errors. A v2 publication contains:

```text
observation_links.link_status
observation_links.schedule_observation_event_id
observation_links.market_quote_observation_event_id
observation_links.availability_observation_event_ids
observation_links.missing_or_unavailable_reasons
observation_links.capture_errors
```

`COMPLETE` means the run found exact, unambiguous links for the schedule,
market quote, and applicable availability observations.

`DEGRADED` means canonical publication succeeded but some observation was
missing, unresolved, ambiguous, or unavailable, or capture reported an
ordinary failure. Do not invent or manually fill a link.

## Reconciliation classifications

`PASS` means the exact board still matches publication evidence, links are
complete/resolved, linked values agree, and hashes verify.

`DEGRADED` means the canonical board is exact but observation evidence is
incomplete for an ordinary reason. The existing operational output remains
valid under the canonical system, but Phase 3 evidence is incomplete.

`FAIL` means an integrity conflict such as a wrong linked event/player/line/
odds/bookmaker, mismatched provider timestamp, idempotency conflict, event
hash mismatch, evidence mismatch, or tampering.

Reconciliation status never changes current selections, histories, grading,
settlement, or stake.

## Failure diagnosis

An observation initialization failure emits:

```text
[SHADOW_LIFECYCLE_OBSERVATIONS] status=DEGRADED
stage=INITIALIZATION classification=OBSERVATION_IMPORT_FAILURE
```

An observation preparation failure emits:

```text
[SHADOW_LIFECYCLE_OBSERVATIONS] status=DEGRADED
stage=CAPTURE classification=OBSERVATION_CAPTURE_FAILURE
```

In both cases, confirm that the canonical board completed and inspect the v2
publication's missing links/capture errors. Do not rerun solely to manufacture
evidence if the original source context is gone.

For writer-lock diagnosis and incomplete staging directories, follow
`docs/operations/lifecycle_shadow_runbook.md`. The existing exclusive lock,
dead-owner policy, atomic rename, and incomplete-directory rules are
unchanged.

## Source retention and secrets

Schedule and selected availability provider-shaped rows are stored as
`SANITIZED_RAW` when available. Current BallDontLie market rows are
`NORMALIZED_ONLY`; the adapter no longer has the complete HTTP response at
this boundary. Never represent these as `FULL_RAW`.

Authorization/API-key/token/access-token/secret/password/cookie/session keys
are recursively redacted. If suspected credentials appear in evidence, stop
treating the segment as safe, preserve it without redistribution, and follow
the repository's credential-rotation process. Do not edit the evidence in
place.

## Confirm canonical parity

For a representative run:

1. hash the canonical Elite board and relevant history/grading/Kelly files;
2. run with observations off and then on using otherwise identical fixed
   inputs;
3. confirm board bytes, rows, order, selections, line, odds, projection,
   confidence, qualification, Kelly eligibility, stake, history, grading, and
   lock behavior are unchanged;
4. confirm differences are limited to `data/lifecycle/` and shadow log lines.

The known `now=None` lock behavior is intentionally unchanged. This runbook
does not authorize fixing it, changing provider precedence, or making the
lifecycle ledger authoritative.
