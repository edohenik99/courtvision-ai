# Historical Cockpit Maintenance

This runbook covers safe maintenance for historical CourtVision cockpit artifacts. It is for reporting repair only. It must not rerun predictions, grading, Kelly sizing, pick selection, suppression gates, board generation, or history mutation.

## Purpose

`scripts/validate_historical_cockpit.py` checks whether historical cockpit artifacts are present, readable, and logically aligned across a date range. It reads:

- `outputs/runtime/operator/operator_card_YYYY-MM-DD.txt`
- `outputs/runtime/operator/completion_state_audit_YYYY-MM-DD.txt`
- `outputs/runtime/diagnostics/completion_state_audit_YYYY-MM-DD.json`
- `outputs/runtime/operator/daily_summary_YYYY-MM-DD.txt`
- `outputs/runtime/operator/quality_summary_YYYY-MM-DD.txt`

`scripts/refresh_historical_operator_cards.py` safely regenerates only stale or missing operator cards. It writes only:

- `outputs/runtime/operator/operator_card_YYYY-MM-DD.txt`

Use it when an old operator card is missing the Phase 16E `recommended action` line or the card itself is missing but the required source artifacts already exist.

## Maintenance Loop

1. Validate the date range:

```powershell
py -3.13 scripts/validate_historical_cockpit.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

2. Preview stale-card refreshes:

```powershell
py -3.13 scripts/refresh_historical_operator_cards.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD --only-stale --dry-run
```

3. Refresh stale or missing operator cards only:

```powershell
py -3.13 scripts/refresh_historical_operator_cards.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD --only-stale
```

4. Validate again:

```powershell
py -3.13 scripts/validate_historical_cockpit.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD
```

## Status Interpretation

- `PASS`: Operator card and completion audit are present, readable, and aligned.
- `PASS_NO_SLATE`: No-slate day is closed cleanly, with `NO BET`, `games count: 0`, `COMPLETE`, and no action required.
- `WARN_MISSING_RECOMMENDED_ACTION`: Operator card is readable but stale, usually missing the Phase 16E `recommended action` line. Refresh only the operator card.
- `WARN_MISSING_ARTIFACTS`: One or more expected cockpit artifacts are missing. Generate the missing reporting artifact, not the full daily run, unless a full rebuild is intentional.
- `WARN_AUDIT_ISSUES`: Completion audit JSON has warnings or agreement issues. Inspect the audit before trusting the cockpit.
- `FAIL_PENDING_REAL_PICKS`: Real picks remain pending. Inspect grading before trusting historical results.
- `FAIL_UNREADABLE`: A required existing artifact cannot be read or parsed, such as malformed completion audit JSON.

## Safety Rules

- Do not run `run_today.bat` on old slates unless intentionally rebuilding historical outputs.
- Use `scripts/write_operator_card.py` or `scripts/refresh_historical_operator_cards.py` for card-only refresh.
- Do not mutate `data/history/pick_history.csv` for cockpit maintenance.
- Do not regenerate elite, full market, or SGP boards for cockpit maintenance.
- Do not rerun prediction, grading, Kelly, selection, suppression, or board-generation scripts as part of this workflow.

## Troubleshooting

### Stale Card Missing Recommended Action

Validator status: `WARN_MISSING_RECOMMENDED_ACTION`

Run:

```powershell
py -3.13 scripts/refresh_historical_operator_cards.py --start-date YYYY-MM-DD --end-date YYYY-MM-DD --only-stale
```

For one date, this is equivalent to:

```powershell
py -3.13 scripts/write_operator_card.py --prediction-date YYYY-MM-DD
```

### Missing Completion Audit JSON

Validator status: `WARN_MISSING_ARTIFACTS`

Run only the completion audit writer for the affected date:

```powershell
py -3.13 scripts/write_completion_state_audit.py --prediction-date YYYY-MM-DD
```

Then rerun the operator card refresh if the card needs to pick up the new audit state.

### Real Pending Picks

Validator status: `FAIL_PENDING_REAL_PICKS`

Do not treat the cockpit as closed. Inspect grading state and completion audit details before trusting results. Do not mutate history as part of cockpit maintenance.

### Unreadable Audit JSON

Validator status: `FAIL_UNREADABLE`

Inspect the affected JSON file directly. If the file is malformed, regenerate only the completion audit for that date:

```powershell
py -3.13 scripts/write_completion_state_audit.py --prediction-date YYYY-MM-DD
```

Then refresh the operator card only if needed.
