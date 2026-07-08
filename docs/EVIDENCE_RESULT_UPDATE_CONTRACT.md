# CourtVision Evidence Result Update Contract

## Purpose

`scripts/update_evidence_results.py` is an offline-only grading completion tool for `data/history/evidence_ledger.csv`. It applies already determined final results to existing forward-trial recommendation rows. It does not fetch provider data, call live APIs, run predictions, select picks, calculate scores, change thresholds, recalculate Kelly values, or alter runtime behavior.

The ledger must already exist and must have the exact schema defined in `docs/EVIDENCE_LEDGER_CONTRACT.md`. The updater never creates a ledger.

## Required input CSV schema

Supply a local CSV path with `--results-csv`. The required columns are:

1. `trial_id`
2. `prediction_date`
3. `market`
4. `player`
5. `selection`
6. `line`
7. `odds`
8. `result`
9. `profit_1u`

The optional columns are `void_reason` and `notes`. Unsupported columns, duplicate headers, blank required values, and duplicate matching keys fail validation before any ledger write. `result` is case-sensitive and must be one of `win`, `loss`, `push`, or `void`. The script records supplied results and standardized returns; it does not derive or verify them against a live source.

## Matching key

Each input row must identify exactly one ledger row using:

`trial_id + prediction_date + market + player + selection + line + odds`

Input values are matched as CSV text after trimming outer whitespace. The prediction-time `line` and `odds` are identity values and are never replaced. A key matching multiple ledger rows is ambiguous and always fails safely.

## Fields allowed to change

Only these fields may be populated by this updater:

- `result`
- `profit_1u`
- `void_reason`, when a nonblank value is supplied
- `notes`, when a nonblank value is supplied

All rows remain present and ordered. Prediction-time fields and closing fields, including `closing_line` and `closing_odds`, remain unchanged. Blank optional input values leave their ledger fields unchanged. A validated non-dry-run batch is written through a temporary file in the ledger directory and atomically replaced where the platform supports it.

## Dry-run usage

Preview validation and counts without changing the ledger:

```powershell
python scripts/update_evidence_results.py --results-csv path\to\results.csv --dry-run
```

Omit `--dry-run` to apply a fully validated batch. A successful command prints updated, skipped, and unmatched counts and the resolved ledger path.

## Duplicate, unmatched, and existing-value policy

- Duplicate input keys fail. Multiple ledger rows matching one input key also fail because the target is ambiguous.
- An unmatched input row fails the whole batch by default. `--allow-unmatched` leaves it unchanged and reports it in the unmatched count.
- If either `result` or `profit_1u` is already populated, the whole batch fails by default.
- `--allow-existing` skips and reports an already populated row. It never overwrites existing grading values, `void_reason`, or `notes` on that row.
- Allow flags do not relax schema validation or permit ambiguous matches. Any disallowed condition prevents every update in the batch; partial writes are not permitted.

## Void policy

A `void` result requires a nonblank `void_reason` in the input row. A non-void result normally leaves `void_reason` blank. A nonblank reason may be supplied explicitly for a non-void result when exceptional grading provenance needs to be preserved; it must not be used to disguise a void or rewrite the outcome.

## Investor-facing interpretation rules

- Report the frozen trial identity, full eligible sample, graded count, open count, void count, skipped count, unmatched count, and any use of allow flags.
- Keep trials and differing policies separate. Do not cherry-pick favorable players, markets, dates, or completed observations.
- Interpret `profit_1u` as the standardized paper return at immutable prediction-time odds, not as proof that a wager was placed, accepted, or executable at scale.
- Disclose grading sources, conventions, corrections, void reasons, missing outcomes, and data-quality limitations.
- Distinguish realized paper returns from calibration and closing-line value. None substitutes for the others.
- Preserve an audit trail for corrections; do not silently rewrite previously populated grades.

## Evidence limitation

**Result updates do not prove profitability.** Positive graded results can reflect variance, small samples, selection effects, execution assumptions, unavailable prices, or data errors. Updating outcomes improves the evidence record, but it does not establish future returns, statistical significance, or live wagering performance.
