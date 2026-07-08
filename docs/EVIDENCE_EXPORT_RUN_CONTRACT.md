# CourtVision Run-to-Evidence Export Contract

## Purpose

`scripts/export_run_to_evidence.py` is an offline, append-only adapter between an already completed CourtVision daily run and the forward-trial evidence records. It reads dated artifacts already present on disk, records their repository-relative paths and SHA-256 hashes in `data/history/evidence_daily_manifest.csv`, and appends one `data/history/evidence_ledger.csv` row for each released recommendation found on the canonical Elite board.

The exporter does not run predictions, contact providers or other APIs, grade picks, recalculate Kelly stakes, change selection behavior, or alter any runtime artifact. It transfers values that the artifacts already contain. Missing recommendation fields remain blank and are named in the ledger row's `notes`; values must not be inferred or reconstructed.

## Expected workflow after `run_today`

1. Run the approved `run_today` workflow and let it reach its final disposition.
2. Inspect the operator card, completion-state audit, artifact manifest, validation log, and grading log. Do not use the exporter to make an incomplete run appear successful.
3. Run a dry-run export and review the proposed status, artifact hashes, released count, and recommendation rows.
4. Run the same command without `--dry-run` to append the evidence.
5. Reconcile `released_recommendation_count` to the new ledger-row count before treating the day as captured.

Example:

```powershell
python scripts/export_run_to_evidence.py `
  --trial-id nba-forward-2026-01 `
  --prediction-date 2026-10-20 `
  --config-hash <frozen-config-sha256> `
  --dry-run

python scripts/export_run_to_evidence.py `
  --trial-id nba-forward-2026-01 `
  --prediction-date 2026-10-20 `
  --config-hash <frozen-config-sha256>
```

`--run-date` defaults to the current local date. `--code-sha` defaults to the result of local `git rev-parse HEAD`. An explicitly supplied `--run-status` must use the daily-manifest status vocabulary. Without it, the exporter conservatively infers `complete`, `no_picks`, `no_slate`, or `failed_other` from the released count, required-artifact presence, and clear no-slate evidence.

## Dry-run behavior

`--dry-run` performs the same schema, artifact, hash, status, and duplicate preflight as a real export. It prints the exact proposed manifest row and ledger rows but does not write either CSV. A successful dry run does not reserve a record or prove that a later append will succeed if files change in between.

## Artifact requirements

The exporter searches the current dated runtime conventions under `outputs/runtime`:

| Evidence field | Preferred dated artifact |
|---|---|
| Source board | `operator/full_market_board_<DATE>.csv` (or `source_board_<DATE>.csv`) |
| Elite board | `operator/elite_board_<DATE>.csv` |
| Kelly artifact | `operator/kelly_stakes_<DATE>.csv` |
| Operator card | `operator/operator_card_<DATE>.txt` |
| Completion audit | `diagnostics/completion_state_audit_<DATE>.json` |
| Artifact manifest | `diagnostics/artifact_manifest_<DATE>.json` |
| Run log | `logs/run_today_<DATE>.log` |
| Validation log | `logs/validation_<DATE>.log` |
| Grading log | `logs/grading_<DATE>.log` |

Every artifact must be date-specific to the requested prediction date. Each existing artifact is hashed locally with SHA-256. Missing artifacts fail the export by default. `--allow-missing-artifacts` leaves the missing path/hash pair blank, identifies it in `notes`, and causes inferred status to be `failed_other`; this flag is disclosure, not a success override. If a status is supplied explicitly, that status is retained, so the operator remains responsible for its accuracy.

The daily manifest and evidence ledger must already exist and match their exact documented schemas. The exporter never initializes, replaces, repairs, or rewrites them.

## Recommendation extraction

The Elite board defines released recommendations when present. The Kelly artifact is matched by player, market, selection, line, and odds and supplies Kelly-owned fields where available. If no Elite rows exist but Kelly rows do, those dated Kelly rows are exported. An empty released set appends only the daily manifest row with `released_recommendation_count=0`.

The exporter copies available identity, market, probability, edge, confidence, eligibility, unit, and provider fields through known column aliases. It does not calculate implied probability from odds, treat confidence as model probability, or convert a currency `stake_amount` into betting units.

## Duplicate policy

The default is fail-closed:

- A daily manifest row duplicates an existing row when `trial_id + prediction_date` match. `--allow-duplicate-manifest` is required for an intentional additive record.
- A ledger row duplicates an existing or same-export row when `trial_id + prediction_date + player + market + selection + line + odds` match. `--allow-duplicates` is required to append it.

Both checks happen before either CSV is written. Duplicate flags do not edit or supersede prior rows, and they must not be used to conceal accidental reruns. Corrections still follow the correction and disclosure rules in the daily-manifest and ledger contracts.

## Investor-facing interpretation

- An export proves only that specified local artifacts were observed and hashed at export time.
- `complete` means the evidence workflow released one or more recommendations; it does not mean those recommendations won, were executable, or were profitable.
- `no_picks` and `no_slate` are valid observed zero-recommendation days and remain in calendar coverage.
- Missing artifacts, failure statuses, duplicate overrides, manual interventions, and corrections must be disclosed.
- Released counts must reconcile to ledger rows, and all trial dates must reconcile to the preregistered calendar range.
- Separate `trial_id`, code, and configuration regimes must not be silently pooled.

## Evidence limitation

**Exporting evidence does not prove profitability.** Hashes and append-only records improve custody and reproducibility, but they do not establish market availability, live execution, statistical significance, calibration quality, or future returns. Paper outcomes remain vulnerable to variance, small samples, data errors, and execution assumptions.
