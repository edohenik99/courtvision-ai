# Lifecycle Shadow Runbook

## What shadow mode does

Lifecycle shadow mode observes the existing canonical CourtVision prediction
run. After the canonical dated Elite board is successfully written, it records
an immutable run/publication segment and reconciles the ledger back to the
exact board bytes.

The shadow ledger is not production-authoritative. The existing CSV/runtime
pipeline continues to determine picks, Kelly output, history, grading, and
operator reports.

## Enable or disable

The default is disabled.

For one PowerShell session:

```powershell
$env:COURTVISION_LIFECYCLE_SHADOW = "1"
```

Then use the existing approved CourtVision command. Do not add a separate
lifecycle process or schedule.

Disable safely:

```powershell
$env:COURTVISION_LIFECYCLE_SHADOW = "0"
```

or remove the variable from the process environment. Disabling the hook does
not delete or modify evidence already committed.

The OFF boundary is dependency-isolated. With the variable unset, `0`, or a
non-true spelling such as `false`, `courtvision_ai` does not import
`courtvision.lifecycle`; the lifecycle package may be absent. The OFF path
creates no lifecycle directory, acquires no lifecycle lock, and performs no
lifecycle initialization or reconciliation.

When the flag is enabled, lifecycle publication is imported immediately before
the canonical prediction call. An absent or broken lifecycle package produces:

```text
[SHADOW_LIFECYCLE] status=DEGRADED stage=INITIALIZATION
  classification=LIFECYCLE_IMPORT_FAILURE error_type=<exception type>
```

This is a visible shadow initialization failure. The canonical prediction and
artifact path continues, and the error is not silently suppressed.

`COURTVISION_RUN_REASON` may optionally be `SCHEDULED`, `MANUAL`, `RETRY`, or
`RECOVERY`. Unknown values are stored as null.

## Data locations

```text
data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>/
data/lifecycle/evidence/objects/<prefix>/<sha256>.json
data/lifecycle/reconciliation/YYYY/MM/DD/<prediction_run_id>.json
data/lifecycle/.writer.lock
```

A committed segment contains `events.jsonl`, `run_manifest.json`,
`manifest.json`, and `COMPLETE`.

## Verify lifecycle data

Verify every complete segment:

```powershell
python -m courtvision.lifecycle.verify --lifecycle-root data/lifecycle
```

Verify one segment:

```powershell
python -m courtvision.lifecycle.verify `
  --lifecycle-root data/lifecycle `
  --segment data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>
```

Exit code 0 and `"ok": true` means all selected segment file hashes, event
hashes, hash chains, manifests, completion markers, and referenced evidence
objects verified.

Do not repair a failed committed segment in place. Preserve it, stop treating
its shadow evidence as verified, and investigate. Future correction support
must append a superseding/correction event.

## Inspect a run

Start with:

```powershell
Get-Content data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>/run_manifest.json
Get-Content data/lifecycle/ledger/YYYY/MM/DD/<prediction_run_id>/events.jsonl
Get-Content data/lifecycle/reconciliation/YYYY/MM/DD/<prediction_run_id>.json
```

The reconciliation report contains:

- expected board rows;
- committed publication events;
- matched rows;
- unresolved identity count;
- mismatched fields/errors;
- board path and SHA-256;
- `PASS`, `DEGRADED`, or `FAIL`.

Identity is unresolved when a complete cleaned identity value matches, without
case sensitivity:

```text
UNKNOWN, UNK, N/A, NA, NONE, NULL, NAN, <NA>, MISSING,
NOT_AVAILABLE, NOT APPLICABLE, UNRESOLVED, TBD, -
```

Whitespace is removed only around the value and sentinel matching is
whole-value only. `player-NA-42` is not a sentinel. Exact valid expected-domain
CourtVision event/participant IDs are preserved; wrong-domain or malformed
`courtvision:` IDs fail closed and are never double-prefixed. Unresolved
required identity yields null prediction keys and `DEGRADED` reconciliation
without changing the canonical pick.

## Meaning of statuses

`PASS` means the board exists, its hash is unchanged, its rows exactly match
verified committed publication events, and required identity resolved.

`DEGRADED` means the canonical board succeeded but shadow evidence is
incomplete. Typical reasons are a busy writer, filesystem failure, missing
canonical bookmaker/player/event identity, or unavailable source evidence.
The canonical picks remain unchanged. Treat the run as operationally produced
but not fully evidenced by Phase 2.

`FAIL` means an integrity conflict: missing/extra/mismatched events, an
idempotency conflict, changed committed bytes, invalid evidence, or a shadow
publication without its board. Preserve all files and investigate.

## Writer-lock diagnosis

Inspect metadata:

```powershell
Get-Content data/lifecycle/.writer.lock
```

It identifies the lock UUID, PID, hostname, run ID, command, and acquisition
time.

1. Confirm whether the PID is still running on the named host.
2. If it is running or cannot be verified, do not delete the lock.
3. If the same-host PID is dead, the next writer can recover it automatically.
4. Age alone never proves staleness.
5. Corrupt or foreign-host metadata fails closed and requires an operator
   investigation; there is no ignore-lock flag.

A valid JSON payload that is not an object, such as `[]`, also fails closed
with the documented lifecycle busy exception. Do not delete it automatically.

Do not launch two shadow writers and do not hold or create the lock manually.

## Incomplete temporary segments

Staging directories are hidden names like:

```text
.<prediction_run_id>.tmp-<random>
```

Readers ignore them because they are not final segment directories with a
valid `COMPLETE`. A failed construction cleans up its own staging directory.
Do not rename an incomplete directory into place.

Evidence objects are immutable and may be written before a segment reaches its
final atomic commit. A failed publication can therefore leave an unreferenced
object. Phase 2 has no garbage collector. Never delete an evidence object
merely because it appears unreferenced; safe cleanup requires a future
retention window and reachability scan across every completed segment.

## Confirm CourtVision is unaffected

With the flag disabled:

1. run the existing approved validation command;
2. confirm no new `data/lifecycle/` output was created;
3. compare canonical board/history/Kelly/grading file hashes with the expected
   run or fixture.

With the flag enabled:

1. capture the Elite board SHA-256 after canonical publication;
2. verify it matches the lifecycle reconciliation report;
3. confirm history, Kelly, grading, and lock diagnostics match the same
   canonical run without lifecycle enabled;
4. confirm the only new side effects are under `data/lifecycle/` and SHADOW log
   lines.

To roll back Phase 2 behavior, set the flag to `0`. Do not delete committed
shadow data as part of rollback.

## Corrective acceptance status

The 2026-07-25 corrective rerun passed all 80 lifecycle tests, all 196
canonical/risk-matched tests (with 3 expected xfails), and 26 additional
CLI/runtime parity tests. The validated chunked repository suite recorded
4,080 passed, 31 expected xfails, 9 Windows skips, and 19 failures confined to
the already documented unchanged NBA research Windows/environment modules.

Current Phase 2 verdict:
**PASS WITH KNOWN PRE-EXISTING FAILURES**. Phase 3 is not part of this runbook
or remediation.
