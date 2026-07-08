# CourtVision Forward Evidence Ledger Contract

## Purpose

`data/history/evidence_ledger.csv` is the canonical evidence record for a frozen NBA forward paper trial. It preserves what CourtVision knew and recommended at prediction time, then records closing-market observations and outcomes as they become available. The ledger supports reproducible review of calibration, edge, closing-line value, and paper-trial results without changing prediction, selection, Kelly, provider, or runtime behavior.

The ledger is for prospective observations only. Backfilled predictions must not be presented as forward-trial evidence.

## Exact schema

The CSV header is strict. The required columns, in exact order, are:

1. `trial_id`
2. `run_date`
3. `prediction_date`
4. `code_sha`
5. `config_hash`
6. `provider_used`
7. `market`
8. `player`
9. `team`
10. `opponent`
11. `game_id`
12. `selection`
13. `line`
14. `odds`
15. `implied_probability`
16. `model_probability`
17. `edge`
18. `confidence`
19. `kelly_eligible`
20. `recommended_units`
21. `closing_line`
22. `closing_odds`
23. `result`
24. `profit_1u`
25. `void_reason`
26. `notes`
27. `created_at`

Columns must not be added, removed, renamed, or reordered within a trial. Dates and timestamps should use ISO 8601 representations, and `created_at` should include a timezone.

## Field ownership and timing

### Prediction-time fields

These values describe the observable recommendation and must be captured before the relevant game or market begins:

- `run_date`, `prediction_date`, `provider_used`
- `market`, `player`, `team`, `opponent`, `game_id`
- `selection`, `line`, `odds`
- `implied_probability`, `model_probability`, `edge`, `confidence`
- `kelly_eligible`, `recommended_units`

Prediction-time values are immutable after the row is first recorded. In particular, later market movement must never replace `line` or `odds`.

### Closing-line fields

- `closing_line`
- `closing_odds`

These fields remain blank until a documented closing snapshot is available. They describe the closing market, not a revised prediction. Record the snapshot consistently under the frozen trial protocol; do not substitute a more favorable book or timestamp after seeing the outcome.

### Grading fields

- `result`
- `profit_1u`
- `void_reason`

These fields remain blank until the event is final and gradable. `result` must use the trial's documented result vocabulary. `profit_1u` reports the standardized return for one unit at the recorded prediction-time odds; it must not be silently replaced by recommended-stake profit. A void or ungradable observation requires `void_reason` and must not be converted into a win or loss.

### Audit fields

- `trial_id`
- `code_sha`
- `config_hash`
- `notes`
- `created_at`

Audit values bind each row to one frozen trial and the exact code/configuration identity used at prediction time. `notes` is for exceptional provenance or a clearly labeled correction reference, not retrospective justification. These fields must be captured when the prediction row is created.

## Append-only and correction policy

The ledger is append-only: prediction records may be added, but existing records must never be deleted, reordered, or replaced. Prediction-time and audit values are write-once and immutable. The only permitted in-place completion is to populate previously blank closing-line or grading fields once their source data becomes available. A populated value must not be changed silently.

If a recorded value is wrong, preserve the original evidence and append a clearly labeled correction record under the same `trial_id`, with the reason and the superseded record identity described in `notes`. Never rewrite history to improve trial results. Access controls, backups, or hashes should be used operationally to make unauthorized mutation detectable.

## Appending a recommendation row

Use the offline appender after a prospective recommendation has been released and before its market or game begins:

```powershell
python scripts/append_evidence_ledger_row.py --trial-id nba-forward-2026-01 --run-date 2026-07-07 --prediction-date 2026-07-08 --code-sha $CODE_SHA --config-hash $CONFIG_HASH --provider-used $PROVIDER --market player_points --player "Player Name" --selection over --line 24.5 --odds -110 --edge 0.042 --confidence 0.61 --kelly-eligible true --recommended-units 0.50
```

The script is offline-only: it does not fetch provider data or run predictions. It requires the ledger to exist, validates the exact header above, and appends one row without rewriting existing rows. Closing, result, and profit arguments normally remain blank on this first write. Supplying `--closing-line`, `--closing-odds`, `--result`, or `--profit-1u` is reserved for controlled tests or documented post-processing, not normal prospective row creation.

## Frozen-trial rule

A trial is valid only while its decision policy is frozen. Thresholds, model parameters or artifacts, feature/configuration values, scoring rules, selection gates, Kelly eligibility or sizing rules, provider policy, and other decision-affecting settings must not change during a trial.

If any such item changes, the current trial must end and a new trial must start with a new `trial_id`, `code_sha`, and/or `config_hash` as applicable. Results from distinct trials must not be silently pooled. Fixing ledger infrastructure alone does not authorize a decision-policy change.

## Investor-facing interpretation

- Report the trial identifier, frozen period, code/config identity, sample size, markets covered, and the counts of open, graded, and void observations.
- Distinguish prediction-time performance, closing-line value, calibration, and realized `profit_1u`; none is a substitute for the others.
- Show all eligible trial observations under the frozen protocol. Do not cherry-pick players, markets, dates, providers, wins, or completed records.
- Keep separate trials separate. Any combined view must disclose the component trials and their differing policies.
- Disclose missing closing data, ungraded events, voids, corrections, data-quality limitations, and the odds/line convention used.
- Treat `recommended_units` as a recorded paper recommendation. It is not evidence that the stake was available, accepted, or suitable for any investor.
- Do not annualize a short trial, imply statistical significance without an appropriate analysis, or present paper results as live execution results.

## Evidence limitation

**This ledger is an evidence-collection mechanism, not proof of profitability.** A positive paper-trial result can reflect variance, limited sample size, market availability, execution assumptions, data errors, or selection effects. It does not establish that future returns will be positive or that quoted lines and odds were practically obtainable at scale.
