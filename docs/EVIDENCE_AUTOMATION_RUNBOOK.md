# CourtVision Evidence Automation Runbook

## Purpose

This runbook covers the callable Phase 11 wrappers that orchestrate the existing
CourtVision runtime and evidence tools:

- `tools/run_courtvision_evidence_daily.ps1` runs the guarded daily prediction
  workflow and then delegates evidence export to
  `scripts/export_run_to_evidence.py`.
- `tools/run_courtvision_evidence_grading.ps1` applies supplied closing lines and
  supplied results by delegating to the existing evidence update scripts.

The wrappers do not implement predictions or grading. They do not schedule
themselves, collect closing lines, derive results, or create Task Scheduler
tasks. The governing evidence contracts and
`docs/EVIDENCE_FORWARD_TRIAL_RUNBOOK.md` remain authoritative.

## Generated evidence and logs

The scripts create local transcripts below:

- `data/history/evidence/run_logs/YYYY-MM-DD/`
- `data/history/evidence/grading_logs/YYYY-MM-DD/`

Real evidence operations also update generated stores such as
`data/history/evidence_daily_manifest.csv` and
`data/history/evidence_ledger.csv`. Day 0 JSON, evidence CSVs, runtime outputs,
transcripts, input closing/result CSVs, and other generated history are local
evidence. **Do not commit generated evidence or logs, and never force-add ignored
evidence files.** Preserve them in the approved append-only evidence archive and
back them up independently of Git.

## Day 0 before the first official run

Complete Day 0 before observing the first official prediction. Start from
`main` with a clean tree, initialize the two CSV stores if they do not exist,
then create the preregistration manifest:

```powershell
Set-Location C:\dev\Sport_Project1
python scripts/init_evidence_daily_manifest.py
python scripts/init_evidence_ledger.py

$TRIAL_ID = "nba-forward-2026-10-20-v1"
python scripts/create_evidence_day0_manifest.py `
  --trial-id $TRIAL_ID `
  --start-date 2026-10-20 `
  --end-date 2026-11-18
```

Review and archive the generated manifest before Day 1. Do not use `--force`
after evidence collection starts. Load the frozen `config_hash` directly from
that manifest rather than copying it from an informal note:

```powershell
$DAY0_PATH = "data/history/evidence/day0/day0_manifest_$TRIAL_ID.json"
$DAY0 = Get-Content $DAY0_PATH -Raw | ConvertFrom-Json
$CONFIG_HASH = $DAY0.config_hash
$DAY0 | Select-Object trial_id,start_date,end_date,code_sha,config_hash,working_tree_status
```

If decision-affecting code or configuration changes after Day 0, stop the
cohort and follow the restart rules in the forward-trial runbook. Pulling a new
commit is not permission to mix different code identities into one frozen
cohort.

## Daily prediction and evidence workflow

Required inputs are `-TrialId`, `-PredictionDate`, and `-ConfigHash`.
`-RunDate`, `-AllowMissingArtifacts`, `-DryRunEvidenceExport`,
`-SkipCourtVisionRun`, and `-Notes` are optional. The wrapper confirms `main`,
requires a clean tree apart from the repository's explicitly documented local
audit exception, fast-forwards from `origin/main`, runs `run_today.bat`, and
then invokes the existing exporter. Ignored generated evidence and logs are not
treated as source changes.

Use dry-run export first. This still runs the real canonical daily workflow;
only the evidence append is dry-run:

```powershell
.\tools\run_courtvision_evidence_daily.ps1 `
  -TrialId $TRIAL_ID `
  -PredictionDate "2026-10-20" `
  -ConfigHash $CONFIG_HASH `
  -DryRunEvidenceExport `
  -Notes "Official daily export preview."
```

Review the proposed status, artifact paths and hashes, released count, and
ledger rows. If those are correct and the artifacts have not changed, perform
the real run. Because the canonical runtime protects existing dated artifacts,
`-SkipCourtVisionRun` is the normal way to append the already-reviewed output
without rerunning prediction:

```powershell
.\tools\run_courtvision_evidence_daily.ps1 `
  -TrialId $TRIAL_ID `
  -PredictionDate "2026-10-20" `
  -ConfigHash $CONFIG_HASH `
  -SkipCourtVisionRun `
  -Notes "Official daily evidence append."
```

`-RunDate` overrides the export record date only. `-SkipCourtVisionRun` never
creates or repairs artifacts. Duplicate safeguards remain active; do not work
around a duplicate failure by editing CSVs.

## Verify daily evidence rows

After a real append, inspect the file tails and reconcile the exact trial/date:

```powershell
$PREDICTION_DATE = "2026-10-20"
Get-Content data/history/evidence_daily_manifest.csv -Tail 5
Get-Content data/history/evidence_ledger.csv -Tail 10

$daily = @(Import-Csv data/history/evidence_daily_manifest.csv | Where-Object {
  $_.trial_id -eq $TRIAL_ID -and $_.prediction_date -eq $PREDICTION_DATE
})
$picks = @(Import-Csv data/history/evidence_ledger.csv | Where-Object {
  $_.trial_id -eq $TRIAL_ID -and $_.prediction_date -eq $PREDICTION_DATE
})
if ($daily.Count -ne 1) { throw "Expected one daily manifest row" }
if ($daily[0].config_hash -ne $CONFIG_HASH) { throw "config_hash mismatch" }
if ([int]$daily[0].released_recommendation_count -ne $picks.Count) {
  throw "Released count does not match ledger rows"
}
```

Also verify the status is factual, artifact hashes are populated for every
present artifact, and new ledger rows still have blank closing/result fields.

## Post-game closing-line and result workflow

Prepare local CSVs using the exact schemas in
`docs/EVIDENCE_CLOSING_LINE_UPDATE_CONTRACT.md` and
`docs/EVIDENCE_RESULT_UPDATE_CONTRACT.md`. `-ClosingLinesCsv` and `-ResultsCsv`
are required. `-DryRun`, `-AllowUnmatched`, and `-AllowExisting` are optional.
Both allow switches apply to both update tools and never authorize overwrites.

Preview both batches and their updated, skipped, and unmatched counts:

```powershell
.\tools\run_courtvision_evidence_grading.ps1 `
  -ClosingLinesCsv "C:\evidence-inputs\closing_lines_2026-10-20.csv" `
  -ResultsCsv "C:\evidence-inputs\results_2026-10-20.csv" `
  -DryRun
```

After independently verifying every close, result, `profit_1u`, void reason,
and preview count, apply the same inputs:

```powershell
.\tools\run_courtvision_evidence_grading.ps1 `
  -ClosingLinesCsv "C:\evidence-inputs\closing_lines_2026-10-20.csv" `
  -ResultsCsv "C:\evidence-inputs\results_2026-10-20.csv"
```

The closing-line update runs first. The wrapper exits nonzero if either updater
fails. After a real update, filter the ledger by `trial_id` and prediction date;
confirm only previously blank closing fields and permitted result fields changed.
Investigate skipped or unmatched rows before considering allow switches.

## Exceptional daily dispositions

- **No slate:** run the canonical daily workflow. Export one daily row with zero
  recommendations and factual `no_slate` status. If the normal no-slate path
  omits required artifacts, preview with `-AllowMissingArtifacts`, disclose the
  exact absence in `-Notes`, then repeat the reviewed real export with
  `-SkipCourtVisionRun -AllowMissingArtifacts`.
- **No picks:** preserve the empty Elite board and all normal artifacts. The
  exporter should infer `no_picks` and append no ledger rows. Never loosen a
  threshold or gate to create volume.
- **Provider failure:** do not retry until a preferred answer appears and do not
  relabel failure as no picks. The daily wrapper exits nonzero when the canonical
  run fails. Record the zero-release `provider_failure` disposition with the
  existing `scripts/append_evidence_daily_manifest.py` procedure documented in
  `docs/EVIDENCE_FORWARD_TRIAL_RUNBOOK.md`, including attempted providers,
  failure reason, and available log paths. Do not use the generic exporter to
  make a failed run look complete.
- **Missing artifacts:** the default export fails closed. Inspect dated paths and
  logs first; never borrow another date's artifact or rerun a closed prediction.
  If absence is genuine, use `-AllowMissingArtifacts` first in dry-run and then
  in the matching real export, with explicit notes. This records the custody gap;
  it does not repair it or prove a successful run.

Every calendar day in an official trial needs a truthful disposition. Preserve
failures and missingness rather than backfilling predictions after market or
result information is known.

## What this automation does not establish

The wrappers do not validate the supplied closing source or independently grade
the supplied outcomes. They do not prove line availability, execution, capacity,
statistical significance, or future returns. **Automated evidence collection
does not prove profitability.** Investors must see the full preregistered sample,
all failures and missing data, cohort identity, uncertainty, and the distinction
between paper results and live wagering performance.
