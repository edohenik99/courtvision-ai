# Cleanup notes

This cleaned package keeps the live runtime intact while making the repo easier to evolve.

## What changed
- Removed generated output folders, cache folders, temp folders, and virtualenv contents from the clean handoff.
- Added `courtvision/pipeline/` to introduce explicit stage + manifest contracts.
- Kept `courtvision_ai.py` as the canonical runtime so tests and current tooling do not break.
- Tightened wrapper scripts so they stay wrappers instead of architecture.
- Expanded `.gitignore` to keep the repository from filling back up with generated noise.

## What still needs real refactoring
- `courtvision_ai.py` is still the main structural bottleneck.
- The next serious cleanup step is to migrate orchestration blocks into `courtvision/pipeline/runner.py` one lane at a time.
