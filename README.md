# CourtVision

This repository is in a migration state.

## Current truth
- `courtvision_ai.py` is still the canonical runtime.
- `courtvision/` is the destination architecture and should own new logic.
- `scripts/` must stay thin wrappers.
- `courtvision/pipeline/` now exists to hold explicit stage and manifest contracts.

## Why this cleanup exists
The original handoff was bloated with a full virtualenv, replay outputs, cache junk, and a monolithic runtime. This cleanup strips the garbage and introduces a cleaner migration path without pretending the refactor is finished.

## Repo lanes
- `courtvision/`: package-owned logic
- `courtvision/pipeline/`: stage contracts, manifests, future orchestration extraction
- `courtvision/data/`: provider normalization helpers
- `courtvision_ai.py`: legacy canonical runtime that should shrink over time
- `scripts/`: operational wrappers only
- `tests/`: regression coverage

## Install
```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -r requirements.txt
```

Create a `.env` file with:
```env
BALLDONTLIE_API_KEY=your_api_key_here
```

## Run
```bash
python courtvision_ai.py --prediction-date 2026-04-10 --out-dir outputs
python scripts/run_daily.py --prediction-date 2026-04-10 --out-dir outputs
python scripts/run_grading.py --prediction-date 2026-04-10 --out-dir outputs
```

## Outputs and history
- **`data/history/`** — Long-lived data (for example `prediction_history.csv`). Do not delete this folder if you want to keep prediction history and anything that depends on it.
- **`outputs/runtime/`** — Disposable run outputs (operator boards, telemetry, diagnostics, and `runtime/history/` pick/graded files). Safe to delete to free space; the next run recreates it.

## Daily run (Windows)
- **`run_today.bat`** — Main user entrypoint: optional fit, prediction for a date, validation, and optional grading.
- **`scripts/validate_runtime_outputs.py`** — Post-run validator used from that flow: exposure caps, directional checks on the elite board, summary printing, and optional `--grading-summary` after grading.

## Result tracking and dashboard

### 1. Daily run
```powershell
.\run_today.bat
```
- Validation must pass before picks are saved as official.
- Official picks are appended to `data/history/pick_history.csv`.
- A runtime copy is saved to `outputs/runtime/history/picks_<date>.csv`.

### 2. Manual grading
```bash
python scripts/grade_completed_picks.py --prediction-date YYYY-MM-DD
```
- Grades pending picks when actual results are available.
- Unresolved picks remain `pending`.
- Writes `graded_picks_<date>.csv` under `outputs/runtime/history/`.

### 3. Dashboard CLI
```bash
python scripts/dashboard.py
```
- Prints a summary from `data/history` performance files.

### 4. Streamlit dashboard
```bash
streamlit run scripts/dashboard.py -- --streamlit
```

### 5. Important note
- `data/history/` is long-lived model/result memory.
- `outputs/runtime/` is disposable runtime output.

## Nightly grading automation
Use these commands on Windows:

```powershell
.\scripts\install_nightly_grader.ps1
.\scripts\uninstall_nightly_grader.ps1
python scripts\nightly_grade_and_refresh.py
```

- Daily run (`.\run_today.bat`) creates official picks and appends them to `data/history/pick_history.csv`.
- The nightly grader runs after games finish, grades pending picks when results are available, and refreshes performance summary files under `data/history/`.
- The dashboard reads updated performance data from `data/history/`.
- `outputs/runtime/` remains disposable runtime output.
- `data/history/` is long-lived memory and must not be deleted.

## New in this cleanup
- manifest writing under `outputs/manifests/`
- explicit pipeline stage contracts
- provider normalization helpers

## Pipeline mode

Controls which prediction pipeline path is used.

**Default:**
```env
COURTVISION_ENABLE_LEGACY_PIPELINE=false
```

**Meaning:**
- `courtvision/pipeline/predict_pipeline.py` `PredictionPipeline` is authoritative
- Single, unified elite board production path
- Legacy post-processing/rebuild path is skipped

**Optional legacy comparison:**
```env
COURTVISION_ENABLE_LEGACY_PIPELINE=true
```

**Warning:**
- Legacy mode is for debugging/comparison only
- Not the default daily operating path
- May produce different elite board outputs; use only for regression testing
- better repo hygiene

## Still needs real work
Read `HARSH_AUDIT.md` and `courtvision/CLEANUP_NOTES.md`.
