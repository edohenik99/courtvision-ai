# Validation Guide

Use the narrowest meaningful validation first, then climb the ladder when the touched code affects shared or bankroll-facing behavior.

## Validation Ladder

1. Compile touched Python files.
2. Run targeted pytest for the changed behavior.
3. Run the related pytest bundle.
4. Run full pytest.
5. Run `run_today.bat` for date-specific runtime checks.
6. Review `git status` and `git diff` before staging or asking for review.

## PowerShell Commands

Compile a touched file:

```powershell
py -3.13 -m py_compile courtvision/pipeline/predict_pipeline.py
```

Run a targeted test:

```powershell
py -3.13 -m pytest tests/test_predict_pipeline.py::TestPredictionPipeline::test_board_selection_trace_explains_live_gate_admission -v --tb=short
```

Run a related bundle:

```powershell
py -3.13 -m pytest tests/test_game_status_gate.py tests/test_data_normalization.py tests/test_predict_pipeline.py tests/test_selection_modules.py -v --tb=short
```

Run the full suite:

```powershell
py -3.13 -m pytest
```

Run a date-specific runtime check:

```powershell
.\run_today.bat 2026-05-04
```

Review the tree:

```powershell
git status --short
git diff --stat
git diff --cached --stat
```

## Runtime Diagnostics To Check

For game-status work:

- `candidates_with_game_status_count`
- `candidates_with_game_datetime_count`
- `game_status_exclusion_reasons`
- `game_status_unknown_missing_datetime_count`

For odds freshness:

- `odds_rows_total`
- `odds_rows_with_updated_at`
- `stale_odds_count`
- `stale_odds_by_vendor`

For board admission:

- `qualified_over_rows`
- `qualified_under_rows`
- `elite_over_rows`
- `elite_under_rows`
- `candidate_count_after_elite_admission_filter`
- `elite_board_size`

For Kelly:

- Kelly rows
- Kelly eligible rows
- Kelly skip reasons
- Graded rows with Kelly eligibility

## Cleanup Commands

Remove generated test outputs:

```powershell
Remove-Item .\test_outputs -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item .\.pytest_cache -Recurse -Force -ErrorAction SilentlyContinue
```

Remove common scratch files:

```powershell
Remove-Item .\check_compile.py -Force -ErrorAction SilentlyContinue
Remove-Item .\check_py.py -Force -ErrorAction SilentlyContinue
Remove-Item .\check_scoring.py -Force -ErrorAction SilentlyContinue
Remove-Item .\compile_check.py -Force -ErrorAction SilentlyContinue
Remove-Item .\find_load_games.py -Force -ErrorAction SilentlyContinue
Remove-Item .\hello_test.txt -Force -ErrorAction SilentlyContinue
Remove-Item .\quick_test.py -Force -ErrorAction SilentlyContinue
Remove-Item .\run_all_checks.py -Force -ErrorAction SilentlyContinue
Remove-Item .\run_pytest.py -Force -ErrorAction SilentlyContinue
Remove-Item .\run_quick.py -Force -ErrorAction SilentlyContinue
Remove-Item .\run_test_check.py -Force -ErrorAction SilentlyContinue
Remove-Item .\run_tests.py -Force -ErrorAction SilentlyContinue
Remove-Item .\run_tests_script.py -Force -ErrorAction SilentlyContinue
Remove-Item .\test_result.txt -Force -ErrorAction SilentlyContinue
Remove-Item .\test_runner.py -Force -ErrorAction SilentlyContinue
Remove-Item .\verify_all.py -Force -ErrorAction SilentlyContinue
Remove-Item .\verify_fix.py -Force -ErrorAction SilentlyContinue
```

Before deleting anything, confirm the target is untracked if there is any doubt:

```powershell
git ls-files --error-unmatch .\path\to\file
git status --short
```

