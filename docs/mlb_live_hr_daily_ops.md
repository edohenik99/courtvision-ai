# MLB Live HR Daily Ops

## Daily collection

Run once per day only:

```powershell
cd C:\dev\Sport_Project1
git checkout main
git pull origin main
python .\tools\theoddsapi_live_hr_collector.py --quiet
python .\tools\run_live_hr_daily_check.py
```

## Rules

* Do not run the collector more than once per day.
* Do not use `--force` unless intentionally collecting a second snapshot.
* Do not commit runtime CSVs, JSON payloads, API snapshots, or `.env`.
* After collection, always run the daily health check.
* If the daily health check is valid, stop collecting for the day.

## Safe daily health check

This command is always safe because it does not call the live API:

```powershell
python .\tools\run_live_hr_daily_check.py
```

Expected healthy output includes:

```text
Live HR daily check: VALID
Duplicates: 0
```

## Automatic daily run

The Windows automation runner updates the local `main` branch, checks local run-log and master data for today's collection, runs the collector only when no same-day collection is found, and always runs the daily health check. It writes a timestamped transcript containing command output and errors.

It does not grade results, fill results files, use Task Scheduler by itself, or make an API call merely to decide whether today's collection exists.

Manual test command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_live_hr_daily_auto.ps1
```

> **Warning:** Do not manually test the runner on a day already collected unless you have first confirmed that the duplicate guard recognizes today's local run-log or master-data entry.

Create the daily Windows Task Scheduler task:

```powershell
schtasks /Create /TN "CourtVision MLB Live HR Daily" /SC DAILY /ST 11:30 /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_live_hr_daily_auto.ps1" /F
```

Check timestamped automation logs in:

```text
data/theoddsapi/live_hr_snapshots/automation_logs/
```

The daily collection runner does not grade results. Grading can remain manual or use the consolidated nightly finalization below.

## Consolidated nightly finalization

The consolidated nightly pipeline is the intended 3:30 AM local automation. It
updates local `main`, selects completed MLB dates that need processing, runs the
offline daily health check, regenerates the results workbook with
`--preserve-results`, fills results from MLB StatsAPI without
`--overwrite-filled`, exports the strict grader CSV, checks coverage per date,
grades only dates whose date-scoped coverage reports `Ready to grade: YES`, and
writes a concise JSON and text run summary.

Incomplete dates are skipped without failing the whole run. No-data dates are
logged and treated as successful skips. Failures from git, the health check,
workbook generation, result filling, export, coverage execution, grading, or
grade-summary generation fail the run and appear in the timestamped summaries.

The nightly pipeline does not call the live odds collector. It uses MLB StatsAPI
only for result filling, preserves matching result entries already present in
the workbook, and relies on the existing date-scoped coverage gate before
grading. For final games, rostered players without batting stats are marked
`void` by the existing filler so non-participants do not block coverage.

Manual dry-run command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_courtvision_mlb_nightly_pipeline.ps1 -DryRun
```

Manual date-scoped dry-run command:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_courtvision_mlb_nightly_pipeline.ps1 -DryRun -Date YYYY-MM-DD
```

The `-DryRun` mode copies the master/workbook/results inputs under
`automation_logs\dry_run_YYYYMMDD_HHMMSS\` and writes dry-run result, grade, and
report outputs there. It does not mutate the canonical workbook, strict results
CSV, grade CSV, or grade summary report. Use `-SkipGit` only for local validation
when uncommitted work is present; scheduled production runs must omit it.

Manual production test command after review:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_courtvision_mlb_nightly_pipeline.ps1
```

Repoint the existing 3:30 AM finalizer task only after a dry-run and manual
production test have passed:

```powershell
schtasks /Change /TN "CourtVision MLB HR Finalizer" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_courtvision_mlb_nightly_pipeline.ps1" /ST 03:30
```

Do not disable the existing 2:00 AM Nightly Grader until the new nightly pipeline
has passed at least one manual production test and one scheduled run. After that
validation, the review command to disable it is:

```powershell
schtasks /Change /TN "CourtVision Nightly Grader" /DISABLE
```

The 3:30 AM local pipeline normally processes yesterday plus the prior completed
dates in its lookback window. This gives late West Coast games and extra innings
more time to reach final status before the MLB StatsAPI fill runs, and it retries
recent incomplete dates idempotently.

If target-date coverage remains incomplete, diagnose the blank workbook rows without
changing results or running the grader:

```powershell
python .\tools\diagnose_live_hr_missing_results.py --date YYYY-MM-DD
```

The diagnostic uses the local workbook and master odds CSV plus MLB StatsAPI schedule
and boxscore endpoints. Add `--csv-report` to write
`data/theoddsapi/live_hr_snapshots/reports/missing_results_YYYYMMDD.csv`, or pass an
explicit path after the flag.

A rostered player without batting stats in a final boxscore keeps a blank
`actual_home_runs` value and receives `game_status=void`. The diagnostic recommends
this status without changing files itself. A player name absent from the roster remains
unresolved for manual review.

Check timestamped nightly pipeline transcripts, dry-run workspaces, and summary
files in:

```text
data/theoddsapi/live_hr_snapshots/automation_logs/
```

Every run writes:

```text
data/theoddsapi/live_hr_snapshots/automation_logs/mlb_nightly_summary_YYYYMMDD_HHMMSS.json
data/theoddsapi/live_hr_snapshots/automation_logs/mlb_nightly_summary_YYYYMMDD_HHMMSS.txt
```

## Daily operations report

Generate the offline daily report for a specific local date:

```powershell
python .\tools\generate_live_hr_daily_report.py --date YYYY-MM-DD
```

If `--date` is omitted, the report uses the current local date. The command reads
only local snapshot, run-log, results, and automation-log files. It does not call
The Odds API or MLB StatsAPI, and it does not run the grader.

Reports are written to:

```text
data/theoddsapi/live_hr_snapshots/reports/live_hr_daily_report_YYYYMMDD.md
```

## If duplicates appear

Run the dedupe command, then rerun the health check:

```powershell
python .\tools\run_live_hr_daily_check.py --dedupe
python .\tools\run_live_hr_daily_check.py
```

Only commit code or documentation changes. Do not commit runtime CSV files unless intentionally changing repository policy.

## Live HR snapshot files

Snapshot directory:

```text
data/theoddsapi/live_hr_snapshots/
```

Master CSV:

```text
data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv
```

Run log:

```text
data/theoddsapi/live_hr_snapshots/run_log.csv
```

Daily snapshot CSV files follow this pattern:

```text
live_hr_props_YYYYMMDD_HHMMSSZ.csv
```

## Offline results workflow

Generate a fillable strict results template from the master odds CSV:

```powershell
python .\tools\generate_live_hr_results_template.py
```

Default output:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results.csv
```

Required results columns:

```csv
event_id,player,actual_home_runs,game_status
```

Check whether the results file is ready for grading:

```powershell
python .\tools\check_live_hr_results_coverage.py
```

The command above checks all historical rows in the strict results file. To
check one game date, use the master odds CSV to scope results by `event_id`:

```powershell
python .\tools\check_live_hr_results_coverage.py --date YYYY-MM-DD
```

Date-scoped coverage can be ready even when older or newer dates still contain
blank results. The report includes the target date and returns `Rows: 0` with
`Ready to grade: NO` when the master odds CSV has no events for that date.

Expected pre-fill coverage state:

```text
Ready to grade: NO
```

That is not an error. It means the results template has not been filled yet.

## Workbook-based results workflow

The workbook workflow is the preferred way to fill results because it includes game, team, player, bookmaker, and price context.

Generate the human-friendly workbook:

```powershell
python .\tools\generate_live_hr_results_workbook.py --overwrite --preserve-results
```

With `--preserve-results`, matching `event_id + player` rows retain any existing `actual_home_runs` and `game_status` values while newly discovered players receive blank result fields.

Workbook output:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv
```

After games are final, manually fill these columns in the workbook:

```csv
actual_home_runs,game_status
```

Use:

```text
actual_home_runs = 0, 1, 2, etc.
game_status = final
```

Result statuses have these meanings:

* `final` is a completed, gradeable result and requires `actual_home_runs`.
* `void` is a rostered non-participant or otherwise non-gradeable prop and keeps
  `actual_home_runs` blank.

Coverage treats `void` as resolved, while the grader excludes void rows from its
output, win/loss counts, profit, and ROI calculations.

After filling the workbook, export the strict grader file:

```powershell
python .\tools\export_live_hr_results_from_workbook.py --overwrite
```

Strict grader output:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results.csv
```

Check whether the strict results file is ready:

```powershell
python .\tools\check_live_hr_results_coverage.py
```

Only run the grader when coverage says:

```text
Ready to grade: YES
```

Then run:

```powershell
python .\tools\grade_live_hr_results.py
```

Full workbook grading flow:

```powershell
python .\tools\generate_live_hr_results_workbook.py --overwrite --preserve-results
# manually fill data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv
python .\tools\export_live_hr_results_from_workbook.py --overwrite
python .\tools\check_live_hr_results_coverage.py
python .\tools\grade_live_hr_results.py
```

## Filling the strict results file directly

The strict results file can still be filled manually, but the workbook workflow is preferred.

Strict results file:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results.csv
```

Required columns:

```csv
event_id,player,actual_home_runs,game_status
```

Use:

```text
actual_home_runs = 0, 1, 2, etc.
game_status = final
```

For a non-participant, leave `actual_home_runs` blank and set `game_status = void`.
Void rows are resolved for coverage and excluded from grading.

Example:

```csv
event_id,player,actual_home_runs,game_status
0d018935d27849176c88ca1dae8a8da8,Alec Bohm,0,final
0d018935d27849176c88ca1dae8a8da8,Brandon Lowe,1,final
```

Before grading, rerun:

```powershell
python .\tools\check_live_hr_results_coverage.py
```

The file is ready only when the checker reports:

```text
Ready to grade: YES
```

## Grading results

Only run the grader after the results coverage checker says the strict results file is ready:

```powershell
python .\tools\grade_live_hr_results.py
```

The command above preserves the global grading workflow. To grade only one game
date after its date-scoped coverage is ready, run:

```powershell
python .\tools\check_live_hr_results_coverage.py --date YYYY-MM-DD
python .\tools\grade_live_hr_results.py --date YYYY-MM-DD
```

The date-scoped grader filters both master odds and strict results rows to event
IDs for that date. Its default output is
`data/theoddsapi/live_hr_snapshots/live_hr_grades_YYYYMMDD.csv`, and it refuses
to grade when required result fields for the target date are blank. The 3:30 AM
finalizer runs these date-scoped commands with the previous local date, so blank
historical dates do not block the current finalization run.

## Grading performance summary

After date-scoped grading succeeds, generate the offline Markdown performance
summary:

```powershell
python .\tools\summarize_live_hr_grades.py --date YYYY-MM-DD
```

The command reads only the date-scoped grade CSV and does not call an external
API or run the collector or grader. It writes:

```text
data/theoddsapi/live_hr_snapshots/reports/live_hr_grade_summary_YYYYMMDD.md
```

The report includes overall, bookmaker, odds-bucket, player, and available
game/team performance. Because the grader excludes void rows from its output,
the summary can report void/excluded counts only when those rows are present in
the supplied grade CSV.

The 3:30 AM finalizer runs this command automatically for the previous local
date after successful date-scoped grading. No PowerShell automation test was
added because this repository has no test harness for the finalizer script;
the offline Python tools remain covered by their targeted tests.

If the grader reports a blank required field, that usually means `live_hr_results.csv` is incomplete. Run:

```powershell
python .\tools\check_live_hr_results_coverage.py
```

Then finish filling the missing fields.

## Recommended daily workflow

Start of day:

```powershell
cd C:\dev\Sport_Project1
git checkout main
git pull origin main
python .\tools\theoddsapi_live_hr_collector.py --quiet
python .\tools\run_live_hr_daily_check.py
```

After games are final, the consolidated nightly pipeline can perform the whole
fill/export/coverage/grade flow:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_courtvision_mlb_nightly_pipeline.ps1 -DryRun -Date YYYY-MM-DD
```

For manual workbook operation without the pipeline:

```powershell
python .\tools\generate_live_hr_results_workbook.py --overwrite --preserve-results
```

Fill the workbook manually:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv
```

Then export, check coverage, and grade:

```powershell
python .\tools\export_live_hr_results_from_workbook.py --overwrite
python .\tools\check_live_hr_results_coverage.py --date YYYY-MM-DD
python .\tools\grade_live_hr_results.py --date YYYY-MM-DD
```

## Testing

Run the offline results tool tests:

```powershell
python -m pytest tests/test_courtvision_mlb_nightly_pipeline.py tests/test_live_hr_results_tools.py tests/test_grade_live_hr_results.py tests/test_live_hr_results_workbook.py tests/test_live_hr_results_exporter.py tests/test_fill_live_hr_results_from_mlb_statsapi.py tests/test_run_live_hr_daily_check.py tests/test_summarize_live_hr_grades.py tests/test_validate_live_hr_data.py
```

All of these tests are offline. They cover the nightly orchestration decisions,
date-scoped coverage and grading, result preservation, export behavior, the
StatsAPI result filler with fakes, data-quality checks, and grade summaries.

## Git workflow

Check status:

```powershell
git status --short
```

Commit code or documentation changes only:

```powershell
git add <file>
git commit -m "<clear commit message>"
git push origin main
```

Do not commit generated runtime files unless intentionally changing the project policy.

Runtime/helper files that should generally stay uncommitted:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results.csv
data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv
data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv
data/theoddsapi/live_hr_snapshots/run_log.csv
```

## Current operational reminder

For July 2, 2026:

* July 2 collection has already been completed.
* Do not run the live collector again on July 2, 2026.
* Safe commands are:

```powershell
python .\tools\run_live_hr_daily_check.py
python .\tools\check_live_hr_results_coverage.py
python -m pytest tests/test_live_hr_results_tools.py tests/test_live_hr_results_workbook.py tests/test_live_hr_results_exporter.py
git status --short
```
