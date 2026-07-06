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

The daily collection runner does not grade results. Grading can remain manual or use the guarded final-game automation below.

## Automatic final-game run

The final-game automation updates local `main`, runs the offline daily health check, regenerates the results workbook while preserving manually filled `actual_home_runs` and `game_status` values for matching `event_id + player` rows, exports the strict grader CSV, and checks coverage. It runs the grader only when coverage reports `Ready to grade: YES`.

If coverage reports `Ready to grade: NO`, the automation logs `Results incomplete; skipping grader.` and exits successfully. Failures from the daily check, workbook generation, export, or coverage checker still fail the run.

The final-game runner does not call the live odds collector and does not fill game results automatically. It only synchronizes the workbook with the existing master odds CSV and preserves matching manual result entries.

Create the daily Windows Task Scheduler task:

```powershell
schtasks /Create /TN "CourtVision MLB HR Finalizer" /SC DAILY /ST 03:30 /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\dev\Sport_Project1\tools\run_live_hr_final_auto.ps1" /F
```

The 3:30 AM local start time gives late West Coast games and extra innings more time to reach final status.

Check timestamped final automation logs in:

```text
data/theoddsapi/live_hr_snapshots/final_automation_logs/
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

After games are final:

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
python .\tools\check_live_hr_results_coverage.py
python .\tools\grade_live_hr_results.py
```

## Testing

Run the offline results tool tests:

```powershell
python -m pytest tests/test_live_hr_results_tools.py tests/test_live_hr_results_workbook.py tests/test_live_hr_results_exporter.py
```

Expected result:

```text
11 passed
```

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
