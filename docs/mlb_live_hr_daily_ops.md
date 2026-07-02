# MLB Live HR Daily Ops

## Daily collection

Run once per day only:

```powershell
cd C:\dev\Sport_Project1
python .\tools\theoddsapi_live_hr_collector.py --quiet
python .\tools\run_live_hr_daily_check.py
```

## Rules

- Do not run the collector more than once per day.
- Do not use `--force` unless intentionally collecting a second snapshot.
- Do not commit runtime CSVs, JSON payloads, API snapshots, or `.env`.
- After collection, always run the daily health check.

## If duplicates appear

```powershell
python .\tools\run_live_hr_daily_check.py --dedupe
python .\tools\run_live_hr_daily_check.py
```

## After games are final

Create:

```text
data/theoddsapi/live_hr_snapshots/live_hr_results.csv
```

Required columns:

```csv
event_id,player,actual_home_runs,game_status
```

Then run:

```powershell
python .\tools\grade_live_hr_results.py
```

## Offline results workflow

Generate a fillable results template from the master odds CSV:

```powershell
python .\tools\generate_live_hr_results_template.py