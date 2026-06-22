# CourtVision Phase 4C: MLB HR Fixture Dataset Leakage Audit

Date: 2026-06-19  
Validation date: 2026-06-21

## Scope and purpose

Phase 4C adds a read-only leakage audit for the fixture-backed MLB home-run
batter-game rows created in Phases 4B and 4B.1. The audit runs after rows are
built and does not change builder success or eligibility behavior.

This remains fixture/audit-only. It does not download or join real datasets,
call live APIs, build a model, or begin historical training. Its purpose is to
prove that malformed fixture rows produce explicit, actionable findings before
any future real-data work.

## What was added

- `courtvision/sports/mlb/training/hr_leakage_audit.py`
  - Immutable issue, severity, and report contracts.
  - Single-row and multi-row audit helpers.
  - Deterministic report serialization for a fixed `checked_at` value.
  - An opt-in JSON writer that refuses overwrite by default.
- `tests/test_mlb_hr_leakage_audit.py`
  - Clean, incomplete, and deliberately malformed fixture-row coverage.
- `scripts/mlb_inspect_fixture_stats.py`
  - One in-memory summary line containing audited rows, errors, warnings, and
    pass status. It performs no writes.
- Package exports in `courtvision/sports/mlb/training/__init__.py`.

The Phase 4B builder remains unchanged and does not fail automatically on an
audit report. Callers audit a build result with
`audit_hr_batter_game_rows(result.rows)`.

## Audit categories and severity rules

- `feature_timestamp`: missing, invalid, incomparable, or non-pregame feature
  cutoffs. A missing cutoff is an error for a training-eligible row and a
  warning otherwise. A cutoff at or after game start is always an error.
- `label_separation`: label keys inside feature namespaces and same-game
  Statcast batted-ball values in pregame fixture features. These are errors.
- `outcome_integrity`: training eligibility without a completed game, usable
  labels, or label provenance; and postponed, suspended, or unknown statuses
  claiming training eligibility. These are errors.
- `provenance`: absent fixture manifest identifiers or missing source types for
  populated context. These are warnings when the row makes no unsafe claim.
- `approval_safety`: any betting eligibility, Kelly eligibility, or production
  approval claim. These are errors.
- `required_field`: missing `game_id`, `player_id`, `game_date`, `row_id`, or
  `schema_version`, plus duplicate row IDs in a collection. These are errors.
- `data_quality`: missing optional weather or ballpark context, missing optional
  features on a training-eligible row, and unknown lineup or pitcher status.
  These are warnings.

Errors make `report.passed` false. Warning-only reports keep
`report.passed == true` so critical leakage checks and incomplete data are not
conflated; every warning remains present in `issues` and `warning_count`.
Regardless of pass status, the report remains default-deny.

## Leakage, missing-data, and approval checks

The audit verifies that `feature_as_of` is strictly before a known
`event_start_time`, label fields remain label-only, and the Phase 4B fixture
join does not place same-game Statcast-derived values into canonical pregame
fields. Mapping-shaped malformed rows are supported in tests so nested feature
payload contamination can be demonstrated without weakening the canonical
schema.

Completed training-eligible rows must have available, complete labels.
Non-completed or unknown games cannot be training eligible. Missing manifest
IDs, weather, ballpark values, source types, and uncertain pregame statuses stay
visible as findings rather than silently succeeding.

Rows and reports must explicitly retain:

- `eligible_for_betting = false`
- `kelly_eligible = false`
- `approval_status = "not_approved"`

The report constructor enforces those values as well as exposing them in JSON.

## Report schema

Each issue contains:

- `issue_id`, `row_id`, `game_id`, `player_id`
- `severity`, `category`, `message`, `field_name`
- `expected`, `actual`, `recommended_fix`

Each report contains:

- `sport = "MLB"`, `league = "MLB"`, `market_type = "home_run"`
- `row_count`, `checked_at`, and total/error/warning/info counts
- `passed` and the ordered immutable `issues` collection
- the three default-deny approval fields listed above

Issue order and JSON key output are stable. Tests inject a fixed `checked_at`
timestamp when asserting byte-for-byte deterministic serialization.

## Validation

Commands run successfully from `C:\dev\Sport_Project1`:

```powershell
py -3.13 scripts/mlb_inspect_fixture_stats.py
py -3.13 -m courtvision.sports.mlb.hr_report --date 2026-06-21 --provider sample
$base = 'C:\Users\edohe\AppData\Local\Temp\courtvision-phase4c-full-final-aebfd43b6c7441ccb12c299040333347'
py -3.13 -m pytest tests --basetemp=$base -q
```

Inspection result:

```text
Leakage audit summary: rows=4, errors=0, warnings=16, passed=true
```

Exact full-suite result:

```text
3010 passed, 31 xfailed in 240.73s (0:04:00)
```

The full suite includes the Phase 4B.1 fixture joins, Phase 4B builder, Phase
4A schema, Phase 3B-3F ingestion and manifest coverage, Phase 2F pipeline,
Phase 1A-1D contracts, keyless MLB sample CLI, and NBA compatibility tests.

## Explicit non-changes

No real dataset was downloaded or joined. No live API was called. No model was
built and no training was started. No MLB HR scoring weight, selection gate,
bankroll/Kelly behavior, provider behavior, or NBA runtime code was changed.
MLB remains research/historical only, and the sample CLI remains keyless.

## Next recommended step

Define a separate, explicitly approved pre-real-data acceptance phase that maps
each future historical source column to its availability timestamp and
provenance contract, then run this audit against synthetic boundary cases
before authorizing any real download or training work.
