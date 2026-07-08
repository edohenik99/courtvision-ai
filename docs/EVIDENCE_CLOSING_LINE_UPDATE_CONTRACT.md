# CourtVision Evidence Closing-Line Update Contract

## Purpose

`scripts/update_evidence_closing_lines.py` is an offline-only completion tool for `data/history/evidence_ledger.csv`. It applies already collected closing-market observations to existing forward-trial recommendation rows. It does not fetch provider data, call live APIs, run predictions, select or grade picks, calculate returns, or alter runtime behavior.

The ledger must already exist and must have the exact schema defined in `docs/EVIDENCE_LEDGER_CONTRACT.md`. The updater never creates a ledger.

## Input CSV schema

Supply the local input path with `--closing-lines-csv`. The CSV requires these columns:

1. `trial_id`
2. `prediction_date`
3. `market`
4. `player`
5. `selection`
6. `line`
7. `odds`
8. `closing_line`
9. `closing_odds`

`notes` is optional. It may carry source or operator context in the input file, but it is not copied into the ledger because ledger audit notes are write-once. Unsupported columns, duplicate headers, blank required values, and duplicate matching keys fail validation before any write.

The tool consumes only the supplied local CSV and ledger. Collection and verification of the closing snapshot occur outside this script under the frozen trial protocol.

## Matching key

Each input row must identify exactly one ledger row using the complete key:

`trial_id + prediction_date + market + player + selection + line + odds`

Values are matched as CSV text after trimming outer whitespace from input values. Prediction-time `line` and `odds` identify the original recommendation; they are not replaced by closing values. A key that matches multiple ledger rows is ambiguous and always fails safely.

## Fields allowed to change

Only a simultaneously blank `closing_line` and `closing_odds` pair may be populated. All rows remain present and ordered, and every prediction-time, grading, and audit field remains unchanged. In particular, the updater cannot modify `line`, `odds`, probabilities, edge, confidence, Kelly fields, result, profit, ledger notes, or timestamps.

The batch is validated before it is written. A successful non-dry run replaces the ledger through a temporary file in the same directory and an atomic filesystem replace where the platform supports it.

## Dry-run usage

Preview the update and counts without changing the ledger:

```powershell
python scripts/update_evidence_closing_lines.py --closing-lines-csv path\to\closing_lines.csv --dry-run
```

Apply a fully validated batch by omitting `--dry-run`. Every successful invocation prints updated, skipped, and unmatched counts plus the resolved ledger path.

## Duplicate, unmatched, and existing-value policy

- Duplicate keys in the input fail. Multiple ledger rows matching one input key also fail because the target is ambiguous.
- An unmatched input row fails the entire batch by default. `--allow-unmatched` explicitly permits unmatched rows to remain unchanged and includes them in the reported unmatched count.
- If either closing field is already populated, the entire batch fails by default. `--allow-existing` explicitly skips that row, reports it in the skipped count, and never overwrites either existing value.
- The allow flags do not relax schema validation, authorize overwrites, or permit ambiguous matches.
- Any disallowed condition prevents every update in that batch; partial writes are not permitted.

## Investor-facing interpretation

- Treat the values as documented closing-market observations, not revised predictions and not proof that the closing quote was executable at the recommended stake.
- Reconcile updated, skipped, unmatched, missing-closing, and total ledger counts before reporting coverage.
- Disclose the closing source, timestamp/cutoff convention, book-selection policy, missing observations, and any use of allow flags.
- Compare closing values with the immutable prediction-time `line` and `odds`; do not replace or relabel the original quote.
- Keep trial regimes separate, show the full eligible sample, and do not cherry-pick favorable closing movement.
- Closing-line value, calibration, and realized paper profit are distinct measurements. None should be presented as a substitute for another.

## Evidence limitation

**Closing-line updates do not prove profitability.** Favorable movement can coexist with losing results, and a small or selectively observed sample can be misleading. Closing data is one evidence stream subject to source quality, timing, market availability, execution, and sampling limitations; it does not establish future returns.
