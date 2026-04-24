# Harsh audit

## What is wrong right now
- `courtvision_ai.py` is still a 6,107-line god-file. That is not architecture. That is backlog avoidance with import statements.
- The repo bundle included `.venv`, cache spam, logs, replay outputs, temp folders, and other runtime trash. That is sloppy repo discipline.
- The wrapper scripts were pretending to be thin while still acting like loose operational glue.
- Pipeline observability was weak. If a run went sideways, the codebase made the operator work too hard to understand where.
- Provider normalization was under-explicit. Raw provider payloads had too much influence beyond the client boundary.

## What this patch fixes
- Removes handoff junk so the repository stops looking like a hard drive dump.
- Adds `courtvision/pipeline/` with explicit stage and manifest contracts.
- Keeps `scripts/run_daily.py` and `scripts/run_grading.py` thin and writes manifests for operator visibility.
- Adds `courtvision/data/normalization.py` so provider payload shaping has a real home.
- Adds `pyproject.toml` and a stricter `.gitignore` so the project behaves more like a codebase than a folder of accidents.

## What is still not fixed
- The monolith is still the main problem.
- Real cleanup means extracting orchestration and decision lanes out of `courtvision_ai.py` into `courtvision/pipeline/`, `courtvision/scoring/`, and `courtvision/selection/`.
- Until that happens, this project is improved, not cured.

## Next mandatory refactor
1. Turn `courtvision_ai.py` into a compatibility shell.
2. Move stage sequencing into `courtvision/pipeline/runner.py`.
3. Move candidate scoring and lane assignment into package modules.
4. Move board construction rules into `courtvision/selection/`.
5. Leave wrappers as wrappers.
