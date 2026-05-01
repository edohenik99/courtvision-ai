# CourtVision Validation Workflow

This repo uses a two-layer validation contract:

- deterministic pytest coverage for stable, repeatable checks
- a live operator smoke run for real provider and runtime wiring

The deterministic checks are the source of truth for behavior that must not drift with external data.

## Full Suite

Run the full test suite with an explicit temporary directory:

```powershell
py -3.13 -m pytest tests --basetemp=.pytest_tmp_full -q
```

Current known baseline:

```text
643 passed, 31 xfailed
```

The full suite should pass with xfails only. Do not accept failures, errors, warnings, or XPASS results.

## Critical Focused Checks

Run these when touching the operator path, Kelly stake sizing, prediction artifact writes, or board generation:

```powershell
py -3.13 -m pytest tests/test_operator_fixture_smoke.py --basetemp=.pytest_tmp_fixture_smoke -q
py -3.13 -m pytest tests/test_kelly.py --basetemp=.pytest_tmp_kelly -q
py -3.13 -m pytest tests/test_prediction_artifact_date_isolation.py tests/test_predict_pipeline.py --basetemp=.pytest_tmp_date -q
```

The fixture smoke is deterministic. It uses local fixture data and must not call external APIs. It validates the current operator safety contract, including:

- high-caution OVER picks are skipped with `context_high_caution_over`
- medium-caution neutral OVER picks remain eligible and receive `stake_dampener_factor=0.5`
- Kelly output includes the required audit metadata columns
- prior-date prediction boards are not rewritten

## Live Smoke

Run the live operator smoke when the runtime/operator path changes:

```powershell
.\run_today.bat 2026-04-30
```

The live smoke may produce different board counts over time because provider data can change, especially for historical slates. A changed elite-board count is not automatically a regression if the run validates successfully and the deterministic fixture smoke still passes.

## Xfailed Tests

The current xfailed tests are targeted legacy or experimental checks. They are not a place to hide new failures.

Rules for xfails:

- keep xfails scoped to exact tests whenever possible
- include a precise reason
- do not add blanket xfail or skip markers for broad directories
- investigate and remove an xfail when the underlying behavior becomes stable and intended
- XPASS results should be treated as hygiene issues and resolved

## Before Committing

Use this checklist before committing changes:

- run the focused tests related to the change
- run the full suite
- run `.\run_today.bat 2026-04-30` if the runtime/operator path changed
- confirm no prior-date prediction artifacts are rewritten
- review `git status` and stage only intentional files
- commit and push only after validation passes
