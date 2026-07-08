# CourtVision Official NBA Forward Paper Trial Runbook

## Purpose and governing rule

This runbook is the operator procedure for the official CourtVision NBA forward
paper trial. The trial prospectively records what the frozen canonical runtime
released, the information available at release, the closing market observation,
and the eventual result. It is a paper evidence exercise. It does not authorize
live betting or changes to prediction, scoring, threshold, selection, Kelly,
provider, grading, or runtime behavior.

The official cohort must be frozen and complete across its preregistered calendar
window. Every calendar day needs a daily-manifest disposition, including a day
with no NBA slate, no released picks, a provider failure, or another failed run.
Never omit an inconvenient day or reconstruct a prediction after its outcome or
closing line is visible.

**This trial collects evidence. It does not prove profitability until enough
forward sample exists, and even a positive sample does not by itself establish
future profitability, live executability, or statistical significance.**

The governing contracts are:

- `docs/EVIDENCE_SPRINT_30_DAY_PLAN.md`
- `docs/EVIDENCE_DAY0_MANIFEST_CONTRACT.md`
- `docs/EVIDENCE_DAILY_MANIFEST_CONTRACT.md`
- `docs/EVIDENCE_LEDGER_CONTRACT.md`
- `docs/EVIDENCE_EXPORT_RUN_CONTRACT.md`
- `docs/EVIDENCE_CLOSING_LINE_UPDATE_CONTRACT.md`
- `docs/EVIDENCE_RESULT_UPDATE_CONTRACT.md`

If this runbook and a contract appear to differ, stop and resolve the discrepancy
before writing official evidence.

## Trial identity and rehearsal separation

Use a unique, sortable official identifier in this format:

```text
nba-forward-<YYYY-MM-DD>-v<integer>
```

For example, a first cohort beginning October 20, 2026 would use
`nba-forward-2026-10-20-v1`. A restarted cohort must use a new identifier, such
as `nba-forward-2026-10-20-v2`, and receive its own Day 0 manifest. Do not put
`rehearsal`, `test`, or `dry-run` in an official identifier.

Rehearsal IDs and official IDs must never be mixed. Rehearsal rows may contain
synthetic players, placeholder configuration, intentionally missing artifacts,
or updater test values. Pooling them with official rows would inflate calendar
coverage, recommendation counts, closing-line coverage, or results and would
destroy the official cohort's chain of custody. Filter every review, update, and
report by the exact official `trial_id`; never rename a rehearsal ID into an
official one.

## Source-controlled files and generated evidence

The evidence contracts, this runbook, and the scripts under `scripts/` are
source-controlled. The canonical runtime code and configuration definitions are
also source-controlled and are represented by the Day 0 `code_sha` and
`config_hash`.

The following are generated local evidence and are ignored by Git:

- `data/history/evidence/day0/*.json`
- `data/history/evidence_daily_manifest.csv`
- `data/history/evidence_ledger.csv`
- other `data/history/*.csv` runtime history
- `outputs/`, including dated boards, operator cards, audits, manifests, and logs
- local CSVs used to supply closing-line or result updates

Ignored does not mean disposable. Preserve generated evidence in controlled,
append-only storage with access control and backups. Do not force-add it to a
source-code commit. Keep input CSVs used for closing and result updates with the
evidence custody package, outside the repository or in an approved ignored
location.

Never manually edit any of the following:

- a Day 0 manifest after the first observation;
- daily-manifest or evidence-ledger CSV cells, headers, row order, or rows;
- prediction-time fields in a ledger row;
- dated runtime boards, Kelly artifacts, operator cards, audits, artifact
  manifests, or logs after release;
- a populated closing line, result, or `profit_1u` value.

Use only the evidence scripts for appends and permitted blank-field completion.
Do not use `--force` on a Day 0 manifest after evidence collection begins. Do not
use duplicate, unmatched, or existing-value override flags as routine recovery
tools. Corrections must preserve the original record and follow the applicable
contract.

## Day 0 setup

Day 0 must finish before the first official prediction is observed.

1. Open PowerShell in the repository and verify the exact working tree:

   ```powershell
   Set-Location C:\dev\Sport_Project1
   git status --short --untracked-files=all
   git status
   ```

   The short-status command must print nothing, and `git status` must say the
   working tree is clean. Ignored runtime evidence is intentionally not shown.
   Resolve tracked, staged, and untracked changes before Day 0; do not delete or
   hide another person's work merely to obtain a clean status.

2. Confirm the intended branch and full commit:

   ```powershell
   git branch --show-current
   git rev-parse HEAD
   ```

3. Review and freeze the trial window, timezone, source-book rule, line-capture
   cutoff, closing source and cutoff, settlement policy, eligible markets,
   behavior-affecting environment, and credential availability. Never print a
   secret value. The Day 0 creator stores credential state only as `configured`
   or `missing`.

4. Confirm the generated CSV stores exist and have valid schemas. These commands
   create only a missing empty store and otherwise validate the existing one:

   ```powershell
   python scripts/init_evidence_daily_manifest.py
   python scripts/init_evidence_ledger.py
   ```

5. Set the immutable trial values and create the manifest:

   ```powershell
   $TRIAL_ID = "nba-forward-2026-10-20-v1"
   $START_DATE = "2026-10-20"
   $END_DATE = "2026-11-18"
   $DAY0_PATH = "data/history/evidence/day0/day0_manifest_$TRIAL_ID.json"

   python scripts/create_evidence_day0_manifest.py `
     --trial-id $TRIAL_ID `
     --start-date $START_DATE `
     --end-date $END_DATE
   ```

   Specify nondefault policy flags explicitly if the approved preregistration
   differs from the documented defaults. Do not use `--force` for an existing
   official manifest. The narrowly supported
   `--allow-untracked-investor-audit` exception is not a substitute for a clean
   Day 0 and requires explicit approval for that exact known document.

6. Load and verify the frozen identity:

   ```powershell
   $DAY0 = Get-Content $DAY0_PATH -Raw | ConvertFrom-Json
   $CONFIG_HASH = $DAY0.config_hash
   $CODE_SHA = $DAY0.code_sha
   $DAY0 | Select-Object trial_id,start_date,end_date,git_branch,code_sha,config_hash,working_tree_status

   if ($DAY0.trial_id -ne $TRIAL_ID) { throw "Day 0 trial_id mismatch" }
   if ($DAY0.working_tree_status -ne "clean") { throw "Day 0 was not created from a clean tree" }
   if ((git rev-parse HEAD).Trim() -ne $CODE_SHA) { throw "Checkout does not match Day 0 code_sha" }
   ```

7. Copy the generated Day 0 JSON and the stored non-secret configuration object
   to the approved append-only evidence archive. Confirm the backup opens and
   matches the local file before the first run. Do not commit the generated JSON.

## Daily pre-run checklist

Before the declared prediction-line cutoff:

- Confirm the date is inside the Day 0 inclusive window and use the trial's
  `America/Toronto` calendar date unless the manifest declares another timezone.
- Load `$TRIAL_ID`, `$DAY0_PATH`, `$CONFIG_HASH`, and `$CODE_SHA` from the official
  Day 0 manifest, not from a note or prior console session.
- Run `git status --short --untracked-files=all`. Stop on any source or untracked
  change. Confirm `git rev-parse HEAD` equals `$CODE_SHA`.
- Confirm the effective non-secret environment still produces the same frozen
  configuration. If a behavior-affecting value or external input changed, stop
  the cohort and follow the restart procedure.
- Confirm required credentials are available without displaying their values.
- Confirm there is no existing original daily-manifest row for this trial and
  prediction date.
- Confirm the dated runtime artifacts have not already been released. Do not
  casually rerun or force a protected/past slate.
- Confirm the system clock, network, disk space, and approved evidence backup
  location are available.
- Record any operational anomaly factually. Never change a gate, threshold,
  provider rule, or runtime selection behavior to make the day complete.

Suggested identity preflight:

```powershell
$PREDICTION_DATE = "2026-10-20"
$DAY0 = Get-Content $DAY0_PATH -Raw | ConvertFrom-Json
$CONFIG_HASH = $DAY0.config_hash
$CODE_SHA = $DAY0.code_sha

if ((git status --short --untracked-files=all)) { throw "Working tree is not clean" }
if ((git rev-parse HEAD).Trim() -ne $CODE_SHA) { throw "code_sha differs from Day 0" }

$existing = @(Import-Csv data/history/evidence_daily_manifest.csv | Where-Object {
  $_.trial_id -eq $TRIAL_ID -and $_.prediction_date -eq $PREDICTION_DATE
})
if ($existing.Count -ne 0) { throw "Official daily manifest row already exists" }
```

## Daily run workflow

1. Run the canonical operator path once for the current slate date:

   ```powershell
   .\run_today.bat $PREDICTION_DATE
   ```

   Do not use the past-date force option for ordinary trial operation. Do not
   rerun merely because the output is `no_picks`, a provider failed, validation
   failed, or the recommendations are unattractive.

2. Inspect the final console disposition and the dated artifacts under
   `outputs/runtime`: source and Elite boards, Kelly artifact when applicable,
   operator card, completion-state audit, artifact manifest, and run,
   validation, and grading logs. Determine the factual final status from the
   evidence; do not use the exporter to make an incomplete run appear complete.

3. Perform the dry-run export below. Review every proposed path, hash, status,
   provider, count, recommendation identity, and missing-field note.

4. If and only if the preview is accurate, perform the real export with the same
   artifacts and arguments. Do not modify files between dry run and real export.

5. Verify the appended rows and copy the day's generated evidence to the
   append-only archive. A successful export message is not a substitute for row
   reconciliation.

## Dry-run evidence export

For a normal completed run or a complete run with no picks:

```powershell
python scripts/export_run_to_evidence.py `
  --trial-id $TRIAL_ID `
  --prediction-date $PREDICTION_DATE `
  --run-date (Get-Date -Format "yyyy-MM-dd") `
  --code-sha $CODE_SHA `
  --config-hash $CONFIG_HASH `
  --dry-run
```

The dry run performs schema, artifact, hash, status, and duplicate preflight but
writes neither CSV. Confirm that its inferred status is correct. A successful
preview does not reserve the row, and it becomes stale if any artifact changes.

## Real evidence export

Repeat the reviewed command without `--dry-run`:

```powershell
python scripts/export_run_to_evidence.py `
  --trial-id $TRIAL_ID `
  --prediction-date $PREDICTION_DATE `
  --run-date (Get-Date -Format "yyyy-MM-dd") `
  --code-sha $CODE_SHA `
  --config-hash $CONFIG_HASH
```

Never use `--allow-duplicates` or `--allow-duplicate-manifest` for an accidental
rerun. Stop and investigate a duplicate error. The original official row remains
the evidence.

## Daily row verification

Check the raw file tails after each real append:

```powershell
Get-Content data/history/evidence_daily_manifest.csv -Tail 5
Get-Content data/history/evidence_ledger.csv -Tail 10
```

Then reconcile the official date structurally:

```powershell
$manifestRows = @(Import-Csv data/history/evidence_daily_manifest.csv | Where-Object {
  $_.trial_id -eq $TRIAL_ID -and $_.prediction_date -eq $PREDICTION_DATE
})
$ledgerRows = @(Import-Csv data/history/evidence_ledger.csv | Where-Object {
  $_.trial_id -eq $TRIAL_ID -and $_.prediction_date -eq $PREDICTION_DATE
})

if ($manifestRows.Count -ne 1) { throw "Expected exactly one original daily row" }
$daily = $manifestRows[0]
if ($daily.code_sha -ne $CODE_SHA) { throw "Daily code_sha mismatch" }
if ($daily.config_hash -ne $CONFIG_HASH) { throw "Daily config_hash mismatch" }
if ([int]$daily.released_recommendation_count -ne $ledgerRows.Count) {
  throw "Daily released count does not reconcile to ledger rows"
}
if (@($ledgerRows | Where-Object {
  $_.code_sha -ne $CODE_SHA -or $_.config_hash -ne $CONFIG_HASH
}).Count -ne 0) { throw "Ledger identity mismatch" }

$daily | Format-List trial_id,prediction_date,run_status,provider_used,released_recommendation_count,failure_reason,notes,created_at
$ledgerRows | Format-Table prediction_date,player,market,selection,line,odds,provider_used,result -AutoSize
```

Also confirm each populated artifact path has a 64-character hash, every released
recommendation is present exactly once, prediction-time fields match the released
board, and closing/result fields are still blank on new rows. Corrections are
additive and require separate reconciliation; do not create a correction merely
to repair an unreviewed duplicate run.

## No-slate handling

A legitimate no-slate day still consumes one preregistered calendar day and must
have one daily-manifest row with `run_status=no_slate` and zero released rows.
Run the approved daily workflow; do not skip the date based only on memory or a
third-party schedule page.

If the run produced the normal dated artifacts, preview and export with the
status made explicit:

```powershell
python scripts/export_run_to_evidence.py `
  --trial-id $TRIAL_ID `
  --prediction-date $PREDICTION_DATE `
  --code-sha $CODE_SHA `
  --config-hash $CONFIG_HASH `
  --run-status no_slate `
  --notes "Canonical run confirmed no eligible NBA slate." `
  --dry-run
```

After review, remove `--dry-run` and run the same command. If the canonical
no-slate exit legitimately omits expected artifacts, add
`--allow-missing-artifacts` to both commands and ensure the notes and proposed
blank path/hash pairs disclose exactly what was not produced. This flag records
absence; it does not turn missing evidence into successful evidence. Verify the
ledger count is zero.

## No-pick handling

An eligible slate that completed successfully but released nothing is
`no_picks`, not `no_slate` and not a failure. Preserve the empty dated Elite
board and all other required artifacts. Use the standard dry-run and real export;
the exporter should propose `no_picks` and zero ledger rows. If inference is not
unambiguous, stop and inspect the completion audit rather than guessing. You may
pass `--run-status no_picks` only when the artifacts prove that disposition.
Never loosen a threshold or selection gate to manufacture volume.

## Provider-failure handling

Do not rerun until a preferred outcome appears, switch to an unapproved provider,
or label a provider failure as `no_picks`. If an approved fallback fully recovers
the run, use the factual successful status (`complete` or `no_picks`) and disclose
the attempted provider order, provider used, and fallback use in the custody
record. If provider failure prevents a trustworthy completion, release no new
recommendations and append a `provider_failure` daily row with an exact failure
reason.

Use the dedicated daily-manifest appender for a zero-release provider failure so
provider attempts and the required reason are explicit:

```powershell
python scripts/append_evidence_daily_manifest.py `
  --trial-id $TRIAL_ID `
  --prediction-date $PREDICTION_DATE `
  --code-sha $CODE_SHA `
  --config-hash $CONFIG_HASH `
  --run-status provider_failure `
  --provider-attempted "primary|approved_fallback" `
  --fallback-used false `
  --released-recommendation-count 0 `
  --failure-reason "Provider requests failed before a trustworthy board completed." `
  --manual-intervention false `
  --notes "Use exact provider identifiers and factual diagnostics; no recommendations released." `
  --run-log-path "outputs/runtime/logs/run_today_$PREDICTION_DATE.log" `
  --validation-log-path "outputs/runtime/logs/validation_$PREDICTION_DATE.log" `
  --allow-missing-artifacts
```

Replace the example provider identifiers, reason, and artifact paths with the
observed facts. Supply every existing relevant artifact path; the appender hashes
it. `--allow-missing-artifacts` is appropriate only for named paths that the
failed workflow did not produce. Verify zero ledger rows for the date.

If recommendations were released before a later failure, stop and follow the
daily-manifest and ledger contracts under review; do not zero the factual released
count or improvise a second run.

## Missing-artifact handling

The default export fails closed when a required dated artifact is missing. On
failure:

1. Do not borrow an artifact from another date, regenerate a closed slate,
   fabricate a hash, or manually edit an evidence row.
2. Check the run logs, exact dated paths, artifact manifest, and completion audit
   to determine whether the artifact was never produced, was misplaced, or was
   deleted after release.
3. If the artifact exists at its canonical dated path, rerun only the export
   preflight; do not rerun predictions.
4. If it truly was not produced, use `--allow-missing-artifacts` in a dry run,
   confirm the proposed status and disclosure, then perform the matching real
   export. Without an explicitly supplied successful zero-row status, the
   exporter conservatively records missing required artifacts as `failed_other`.
5. Preserve the failure and disclose the custody break. Never call the day
   `complete` solely because recommendations look plausible.

## Closing-line update workflow

Collect closing observations from the Day 0 declared source and cutoff. Preserve
the source snapshot and prepare a CSV with the exact schema in
`docs/EVIDENCE_CLOSING_LINE_UPDATE_CONTRACT.md`. The prediction-time `line` and
`odds` identify the original row; `closing_line` and `closing_odds` are new
observations and must not replace them.

Preview first:

```powershell
$CLOSING_CSV = "C:\evidence-inputs\closing_lines_$PREDICTION_DATE.csv"
python scripts/update_evidence_closing_lines.py `
  --closing-lines-csv $CLOSING_CSV `
  --dry-run
```

Reconcile the updated, skipped, and unmatched counts. Then apply the identical
validated input:

```powershell
python scripts/update_evidence_closing_lines.py `
  --closing-lines-csv $CLOSING_CSV
```

Filter the ledger by exact `trial_id` and prediction date and verify only blank
`closing_line` and `closing_odds` fields were populated. Do not routinely use
`--allow-unmatched` or `--allow-existing`; investigate mismatches and never
overwrite an existing close. Record missing closes as missing under the declared
policy rather than selecting only favorable observations.

## Result and grading update workflow

Wait for official settlement under the Day 0 policy. Preserve the result source
and prepare a CSV with the exact schema in
`docs/EVIDENCE_RESULT_UPDATE_CONTRACT.md`. The updater records supplied grading;
it does not fetch or independently derive results.

Preview first:

```powershell
$RESULTS_CSV = "C:\evidence-inputs\results_$PREDICTION_DATE.csv"
python scripts/update_evidence_results.py `
  --results-csv $RESULTS_CSV `
  --dry-run
```

After independently checking result values, `profit_1u`, void reasons, and
counts, apply the same input:

```powershell
python scripts/update_evidence_results.py `
  --results-csv $RESULTS_CSV
```

Verify only permitted blank outcome fields were populated and every updated row
belongs to the official trial. A `void` requires a nonblank `void_reason`. Do not
use `--allow-existing` to regrade a populated row or silently change a loss,
push, or void. Corrections must preserve the original and follow the documented
audit policy.

## Pausing, interruption, and restart

A trial cannot be paused by silently omitting calendar days. If operations stop
temporarily while the frozen code, configuration, policy, and date window remain
unchanged, record each affected calendar day with the factual failure status,
reason, and manual-intervention flag. The interruption remains a disclosed
limitation; do not backfill recommendations after market movement or outcomes are
known.

Terminate the current cohort and create a new Day 0 manifest with a new
`trial_id` when any decision-affecting code, configuration, model input, eligible
market, provider/source-book rule, cutoff, timezone, settlement rule, or cohort
window changes. Restart as well when an emergency fix might affect output and
equivalence cannot be demonstrated. Retain the interrupted cohort as a separate,
incomplete cohort and never pool it silently with the restart.

Before a restart:

1. close and archive the interrupted cohort without rewriting it;
2. document the stop date and reason;
3. make and validate the approved change outside the old cohort;
4. restore a clean Git tree;
5. choose a new official `trial_id` and date window;
6. create and archive a new Day 0 manifest before observing the new Day 1.

## Investor-facing interpretation

Any internal or external report must:

- name the exact official `trial_id`, date window, `code_sha`, and `config_hash`;
- include every preregistered calendar day and status, not only active slates,
  released picks, settled rows, or wins;
- keep rehearsal, interrupted, restarted, and differing-policy cohorts separate;
- reconcile daily released counts to ledger rows and disclose missing artifacts,
  closes, results, failures, fallbacks, interventions, corrections, pushes, and
  voids;
- show sample size and missingness beside every metric;
- distinguish price-weighted flat-1u ROI, hit rate, CLV, calibration, drawdown,
  and provider reliability rather than substituting one for another;
- describe quoted recommendations and returns as paper observations, not proof
  that a wager was placed, accepted, available at scale, or suitable for an
  investor;
- avoid cherry-picked slices, annualizing a short sample, or claiming statistical
  significance without an appropriate analysis;
- state plainly that promotion, real-money wagering, threshold changes, or policy
  changes require a separate decision and approval.

**The official forward trial strengthens the evidence record; it does not prove
profitability until enough forward sample exists, and no finite paper sample
guarantees future profit.**
