# CourtVision Daily Evidence Manifest Contract

## Purpose

`data/history/evidence_daily_manifest.csv` is the calendar-day custody record for the frozen NBA forward paper trial. It records whether the approved daily workflow completed, what provider path was attempted and used, how many recommendations were released, and where the run's hashed evidence artifacts are retained. It documents evidence collection only; it does not run or alter predictions, scoring, selection, Kelly, provider, grading, or runtime behavior.

The trial covers consecutive calendar days, not a hand-selected set of slates or winning days. A manifest row is required for every calendar day from the preregistered start date through the end date, including days with no NBA slate, no released picks, provider failure, validation or grading failure, or another failed run. Recommendation rows alone cannot distinguish a legitimate zero-pick day from an omitted day.

## Exact schema

The CSV header is strict. Columns must not be added, removed, renamed, or reordered within a trial. The required columns, in exact order, are:

1. `trial_id`
2. `run_date`
3. `prediction_date`
4. `code_sha`
5. `config_hash`
6. `run_status`
7. `provider_attempted`
8. `provider_used`
9. `fallback_used`
10. `released_recommendation_count`
11. `source_board_path`
12. `source_board_sha256`
13. `elite_board_path`
14. `elite_board_sha256`
15. `kelly_artifact_path`
16. `kelly_artifact_sha256`
17. `operator_card_path`
18. `operator_card_sha256`
19. `completion_audit_path`
20. `completion_audit_sha256`
21. `artifact_manifest_path`
22. `artifact_manifest_sha256`
23. `run_log_path`
24. `run_log_sha256`
25. `validation_log_path`
26. `validation_log_sha256`
27. `grading_log_path`
28. `grading_log_sha256`
29. `failure_reason`
30. `manual_intervention`
31. `notes`
32. `created_at`

Field rules:

| Field | Required meaning |
|---|---|
| `trial_id` | Immutable identifier for the preregistered frozen trial. |
| `run_date` | Calendar date on which the workflow was attempted, as `YYYY-MM-DD` in the trial timezone. |
| `prediction_date` | NBA slate date targeted by the run, as `YYYY-MM-DD` in the trial timezone. |
| `code_sha` | Full frozen git commit SHA used for the run. |
| `config_hash` | Frozen SHA-256 configuration/input-manifest hash. |
| `run_status` | Exactly one valid status from the vocabulary below. |
| `provider_attempted` | Provider identifiers in attempt order, joined by `|`; blank only when no provider attempt was applicable or possible. |
| `provider_used` | Provider identifier that supplied the released evidence; use `|` for disclosed mixed provenance, and leave blank if none supplied usable evidence. |
| `fallback_used` | Lowercase `true` or `false`, indicating whether the approved fallback path supplied usable evidence. |
| `released_recommendation_count` | Base-10 integer greater than or equal to zero. This counts recommendations actually released, not candidates considered. |
| `*_path` | Repository-relative artifact path using `/`, or blank when that artifact was not produced or not applicable. |
| `*_sha256` | Lowercase 64-character SHA-256 digest of the corresponding retained artifact, or blank when its path is blank. A path and its digest must either both be populated or both be blank. |
| `failure_reason` | Concise factual reason for a failure status; blank for `complete`, `no_slate`, and `no_picks`. |
| `manual_intervention` | Lowercase `true` or `false`. Details of any intervention belong in `notes`; this flag must not hide a policy change. |
| `notes` | Exceptional provenance, limitations, or an additive correction reference. It must not be used for retrospective justification. |
| `created_at` | ISO 8601 timestamp with an explicit timezone offset recording when the row was appended. |

Artifact path/hash fields may be blank when the status explains why an artifact was not produced. Missing expected artifacts must not be disguised with placeholder hashes; explain the absence in `failure_reason` or `notes`.

## Valid run statuses

Only these values are valid:

- `complete`: the daily prediction and required validation workflow completed and one or more recommendations were released.
- `no_slate`: no eligible NBA slate existed for the `prediction_date`; the released recommendation count is zero.
- `no_picks`: an eligible slate was processed successfully but no recommendations were released; the released recommendation count is zero.
- `provider_failure`: provider failure prevented a trustworthy completion of the daily prediction workflow. A recovered provider attempt uses `complete` or `no_picks`, with `fallback_used` and provider provenance recording the recovery.
- `failed_validation`: prediction artifacts may have been produced, but required validation did not complete successfully.
- `failed_grading`: the day's required grading step did not complete successfully. Any already released recommendation count remains factual and must not be zeroed merely because grading failed.
- `failed_other`: another failure prevented full completion and does not fit a more specific status.

Every failure status requires `failure_reason`. Status describes the final disposition of that calendar day's approved workflow, not a later reinterpretation based on outcomes.

## Append-only and correction policy

The manifest is append-only. Append the original daily row as soon as the day's final run disposition is known; never delete, reorder, replace, or silently edit an existing row. Artifact paths and hashes describe what existed at that time and are write-once.

Use the offline appender after the CourtVision run attempt has reached its final disposition:

```powershell
python scripts/append_evidence_daily_manifest.py `
  --trial-id nba-forward-2026-01 `
  --prediction-date 2026-10-20 `
  --run-status complete `
  --config-hash <frozen-config-sha256> `
  --released-recommendation-count 2 `
  --source-board-path outputs/2026-10-20/source_board.csv
```

The script requires the manifest to exist, validates this exact schema, obtains `code_sha` from the local Git checkout unless supplied, hashes each existing artifact locally, and appends one row without running predictions or contacting providers. A supplied missing artifact fails by default. `--allow-missing-artifacts` must be explicit; when used, the missing path/hash pair remains blank and the intended repository-relative path is recorded in `notes` so the row remains contract-compliant.

If a recorded value is wrong, retain the original and append a correction row with the same `trial_id`, `run_date`, and `prediction_date`. The correction's `notes` must begin with `CORRECTION:`, identify the superseded row by its `created_at`, and state the reason. Its own `created_at` must be later. Investor reporting may use the latest valid correction, but must disclose the correction count and preserve the superseded rows in the published evidence. Corrections must never be used to erase a failed day or improve results after outcomes are known.

## Relationship to `evidence_ledger.csv`

The two records serve different, complementary purposes:

- `evidence_daily_manifest.csv` proves calendar coverage and run/artifact custody with one original row for every trial day, even when there are no recommendations.
- `evidence_ledger.csv` contains one row per released recommendation and its later closing and grading evidence.

For each `trial_id` and `prediction_date`, `released_recommendation_count` must reconcile to the number of original released-recommendation records in `evidence_ledger.csv`; additive correction records are not additional recommendations. `no_slate` and `no_picks` require a count of zero and therefore legitimately have no recommendation rows. Failure statuses may still have a nonzero count when recommendations were released before a later validation or grading failure. The daily manifest must not duplicate, synthesize, or retrospectively backfill recommendation details.

## Investor-facing interpretation

- Report all preregistered calendar days, grouped by `run_status`, and reconcile the total to the full trial date range.
- Treat `no_slate` and `no_picks` as observed zero-recommendation days, not as days to remove from the cohort.
- Disclose provider failures, fallback use, failed validation, failed grading, other failures, manual interventions, missing artifacts, and corrections.
- Reconcile released counts to `evidence_ledger.csv` before reporting sample size or results. A manifest row is not itself a wager or recommendation.
- Interpret `complete` only as successful evidence collection for that day. It is not evidence of profitability, execution quality, or future returns.
- Keep different `trial_id` values separate. A decision-affecting code or configuration change ends the frozen trial; it must not be hidden as a manifest correction.

## Missing-day warning

**A missing calendar day breaks the chain of custody and invalidates or materially weakens the forward trial.** It creates unresolved selection bias because an outside reviewer cannot distinguish an innocent omission from a no-pick, provider-failure, failed-run, or unfavorable day. Any missing day must be disclosed, investigated, and left visible as a trial limitation; it must not be silently reconstructed after outcomes are known.
