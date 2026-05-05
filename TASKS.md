# Codex Task Template

Use this template when handing work to Codex. Keep scope explicit so future sessions can move quickly and safely.

## Task

Describe the specific outcome. Include the bug, feature, test, or documentation change.

## Context

Summarize what is already known, recent commits, failing commands, relevant diagnostics, and any prior validation.

## Allowed Files

List files or directories Codex may edit.

## Forbidden Changes

List files, directories, or logic areas that must not change. Include bankroll-facing logic if the task is not about it.

## Implementation Plan

Give preferred steps, constraints, and examples. State whether production logic may change or only tests/docs may change.

## Validation Commands

List exact commands to run, from narrow to broad.

## Stop Conditions

State when Codex should stop and ask for review. Examples: before commit, after tests pass, after first failing unknown, or before touching restricted files.

## Report Format

Ask for a concise summary with changed files, validation results, and remaining risks.

## Example: Bug Fix

### Task

Fix candidates being rejected as `game_status_unknown` when game datetime is available.

### Context

Runtime diagnostics show candidates missing `game_datetime` after games enrichment. Completed games should still be blocked as `game_final`.

### Allowed Files

- `courtvision/data/normalization.py`
- `courtvision/pipeline/predict_pipeline.py`
- Related tests only

### Forbidden Changes

- Kelly rules
- Elite thresholds
- Odds freshness gate
- Strong player_points OVER guard
- Dashboard files
- Run scripts
- Generated outputs

### Implementation Plan

Inspect current enrichment flow, preserve date and datetime fields, add focused tests, and keep completed games blocked.

### Validation Commands

```powershell
py -3.13 -m py_compile courtvision/pipeline/predict_pipeline.py
py -3.13 -m pytest tests/test_game_status_gate.py tests/test_data_normalization.py tests/test_predict_pipeline.py -v --tb=short
.\run_today.bat 2026-05-04
```

### Stop Conditions

Stop before commit unless commit is explicitly approved.

### Report Format

List changed files, key diagnostics, and validation pass/fail status.

## Example: Test Update

### Task

Update fixtures so tests reflect current Elite admission policy.

### Context

Runtime logic is correct. Tests fail because candidates no longer pass current policy.

### Allowed Files

- Specific test files only

### Forbidden Changes

- Production gates
- Scoring formulas
- Kelly rules
- Runtime selection logic

### Implementation Plan

Inspect failing candidate fields, make fixtures clearly valid under current policy, and add assertions for diagnostic fields.

### Validation Commands

```powershell
py -3.13 -m pytest tests/test_predict_pipeline.py::TestPredictionPipeline::test_name_here -v --tb=short
py -3.13 -m pytest tests/test_predict_pipeline.py -v --tb=short
```

### Stop Conditions

Stop if a test cannot pass without changing production policy.

### Report Format

Explain why the old fixture failed and why the new fixture is valid.

## Example: Runtime Diagnostics

### Task

Add diagnostics for a runtime gate without changing gate behavior.

### Context

Need better observability for candidate counts and rejection reasons.

### Allowed Files

- Diagnostic writer or pipeline file named by the task
- Related tests

### Forbidden Changes

- Candidate eligibility decisions
- Thresholds
- Kelly eligibility
- Provider fetching

### Implementation Plan

Add counters or structured fields only. Keep behavior unchanged and assert diagnostics in tests.

### Validation Commands

```powershell
py -3.13 -m pytest tests/test_predict_pipeline.py tests/test_selection_modules.py -v --tb=short
.\run_today.bat 2026-05-04
```

### Stop Conditions

Stop if adding diagnostics requires changing candidate admission.

### Report Format

Report new diagnostic names and a sample value from validation.

## Example: Docs-Only Task

### Task

Create or update repo workflow documentation.

### Context

No runtime behavior should change.

### Allowed Files

- Documentation files only

### Forbidden Changes

- Runtime code
- Tests unless explicitly requested
- Generated outputs
- Recalibration files

### Implementation Plan

Create concise docs with examples and commands. Do not run runtime pipelines unless requested.

### Validation Commands

```powershell
git status --short
git diff --stat
```

### Stop Conditions

Stop after docs are created and git status is shown.

### Report Format

List created files and confirm no commit was made.

