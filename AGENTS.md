# CourtVision Agent Rules

This repository contains bankroll-facing sports prediction logic. Treat changes as production-risky unless the task explicitly says otherwise.

## Working Rules

- Always inspect the relevant files and current git state before editing.
- Prefer surgical diffs over broad refactors.
- Preserve existing patterns, naming, and module boundaries.
- Do not change bankroll-facing logic without explicit approval.
- Do not loosen production gates or thresholds to make tests pass.
- Do not commit generated files, runtime outputs, caches, logs, scratch scripts, or local environment files.
- Stop before commit unless the user explicitly approves committing.
- If the tree is dirty, assume existing changes belong to the user. Work around them and do not revert them unless asked.

## Restricted Areas

Do not edit these files or categories unless the task explicitly allows them:

- Kelly sizing, Kelly eligibility, bankroll, or wager sizing logic.
- Grading, feedback, result history, or ROI calculations.
- Scoring formulas, scoring thresholds, Elite thresholds, and selection gates.
- Provider fetching, API auth, data source priority, or odds normalization.
- Dashboard files and UI assets.
- Run scripts, batch files, PowerShell scripts, and scheduled workflow entrypoints.
- `.env`, secrets, local credentials, and provider keys.
- `outputs/`, `test_outputs/`, `.pytest_cache/`, logs, and generated artifacts.
- Recalibration files unless the task is explicitly about recalibration.

## Validation Posture

- Match validation scope to risk.
- Use targeted tests first, then broader suites when behavior is shared or bankroll-facing.
- For runtime issues, validate with the smallest relevant `run_today.bat` date and inspect diagnostics.
- Report what changed, what was validated, and any files intentionally left untouched.

