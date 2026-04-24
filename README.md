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

## New in this cleanup
- manifest writing under `outputs/manifests/`
- explicit pipeline stage contracts
- provider normalization helpers
- better repo hygiene

## Still needs real work
Read `HARSH_AUDIT.md` and `courtvision/CLEANUP_NOTES.md`.
