# ADR-001: Canonical CourtVision Operator Runtime

- **Status:** Accepted
- **Decision date:** 2026-07-07
- **Scope:** Current NBA operator workflow

## Decision

`courtvision_ai.py`, through its `CourtVisionAI` class and command-line entry point, is the canonical CourtVision operator runtime.

`CourtVisionPro` in `courtvision/engine.py` is non-canonical. It is a compatibility and alternate architecture surface, not the runtime invoked by the approved daily operator workflow. It must not be represented as production-facing unless a later ADR explicitly promotes it after parity, validation, and cutover work.

NBA is the only production-facing sport in this decision. MLB, WNBA, NFL, and NHL code and artifacts are research-only unless a separate approval explicitly promotes a sport and names its canonical runtime, data contract, validation gates, and operator procedure.

This ADR establishes ownership and terminology only. It does not certify profitability, authorize real-money wagering, or change any model, threshold, scoring, selection, or bankroll behavior.

## Canonical operator path

```text
run_today.bat
  -> run_today.ps1
     -> courtvision_ai.py / CourtVisionAI
        -> optional baseline fit when model artifacts are absent
        -> prediction and canonical operator-board writes
     -> validation and diagnostic scripts
     -> Kelly recommendation script when the Elite board is non-empty
     -> tracking and grading scripts when picks exist
     -> summary, audit, research, learning, operator-card, and manifest scripts
```

### 1. Launcher

`run_today.bat` is a thin Windows wrapper. It resolves the requested date and forwards `--verbose` and `--force-past-date` to `run_today.ps1` as PowerShell switches. It does not implement prediction logic.

### 2. Orchestrator

`run_today.ps1` is the canonical daily workflow orchestrator. It applies date and overwrite guards, selects the Python interpreter, checks dependencies, orders the stages, captures logs, and enforces fatal versus nonfatal stage outcomes.

The normal current-day path invokes:

- `courtvision_ai.py --fit-only --verbose-outputs` only when required baseline files are absent;
- `courtvision_ai.py --prediction-date <date> --predict-only --verbose-outputs` for the daily prediction run.

The Python entry point constructs and runs `CourtVisionAI`. That class owns the active NBA fetch, projection/orchestration, board-generation, history, grading, and core report-writing behavior used by this path.

### 3. Validation and diagnostics

After prediction, `run_today.ps1` verifies expected board files and runs:

- `scripts/validate_runtime_outputs.py <date>` — required validation; a nonzero result stops the run;
- `scripts/audit_full_market_sanity.py --prediction-date <date>` — diagnostic audit; failures are reported as warnings;
- `scripts/audit_candidate_quality_drift.py --prediction-date <date>` — diagnostic audit; failures are reported as warnings.

The distinction matters: completion of the launcher does not imply that every warning-only diagnostic passed cleanly. Evidence must retain the validation and grading logs as well as the final manifest.

### 4. Kelly recommendation

When the canonical Elite board has at least one row, `run_today.ps1` invokes:

- `scripts/run_kelly_stakes.py --prediction-date <date> --bankroll <resolved bankroll>`.

This writes the dated Kelly recommendation artifact. A nonzero exit or missing expected output is fatal. If the Elite board is empty, the Kelly stage is explicitly skipped. This ADR does not alter Kelly eligibility, sizing, exposure limits, or bankroll defaults.

### 5. History and grading

When any board rows exist, the orchestrator invokes:

- `scripts/post_run_tracking.py --prediction-date <date> --grade-pending` — required tracking/grading stage;
- `scripts/grade_completed_picks.py` — additional grading pass; failure is a warning;
- `scripts/market_shadow_grading.py --prediction-date <date>` — shadow-market grading; failure is a warning.

`CourtVisionAI` also writes its canonical prediction, rejection, feedback, and run histories. Existing history and grading artifacts remain governed by their current overwrite and closed-slate protections.

### 6. Reporting and completion evidence

The remaining orchestrated scripts are:

- `scripts/write_shadow_artifacts.py` — shadow-only outputs;
- `scripts/write_daily_summary.py` and `scripts/write_quality_summary.py` — required summaries;
- `scripts/write_completion_state_audit.py` — required completion-state evidence;
- `scripts/write_research_artifacts.py` — nonfatal research-only bundle;
- `scripts/write_learning_artifacts.py` — nonfatal reporting-only learning bundle; it does not activate proposed rules;
- `scripts/write_operator_card.py` — required operator-facing card;
- `scripts/write_artifact_manifest.py` — required final artifact manifest; fatal missing artifacts prevent successful completion.

The dated operator card, completion-state audit, artifact manifest, run log, validation log, and grading log together describe the operational result. No single file should be treated as complete evidence by itself.

## Production-facing versus research-only

For communications and diligence, **production-facing** means only that a component participates in the current controlled NBA operator workflow. It does not mean the system has demonstrated a profitable edge, passed a forward trial, manages live capital, or is suitable for unsupervised wagering.

The production-facing surface is:

- the guarded NBA path from `run_today.bat` through `run_today.ps1`;
- `courtvision_ai.py` / `CourtVisionAI` as the canonical runtime;
- the validation, Kelly recommendation, tracking, grading, reporting, and manifest stages explicitly enumerated above;
- the dated artifacts produced by a successfully completed, unmodified run.

Research-only surfaces include:

- `CourtVisionPro` and entry points that use it;
- MLB, WNBA, NFL, and NHL implementations and evaluations;
- shadow boards, research bundles, learning proposals, recalibration experiments, and provider smoke tests;
- backtests, historical reconstructions, fixture runs, and diagnostics that were not captured prospectively.

## Investor-facing claim discipline

Permitted claims must be narrow and verifiable, for example: “CourtVision has a defined, guarded NBA operator workflow and is collecting a frozen 30-day forward paper trial.” Once collected, results may be reported with the code SHA, configuration hash, sample size, inclusion rules, failures, voids, and complete metric definitions.

Avoid claims that:

- CourtVision is profitable, has proven alpha, beats closing markets, or is investment-ready before prospective evidence supports the statement;
- backtests, historical replays, or shadow outputs are live or forward results;
- Kelly output proves bankroll safety or constitutes a wager;
- a successful software run proves data completeness, model accuracy, or positive expected value;
- `CourtVisionPro` is the live runtime;
- MLB, WNBA, NFL, or NHL are production-ready or betting-approved;
- a 30-day sample alone establishes long-term performance, statistical significance, or generalization across seasons and market regimes;
- omitted days, provider failures, voids, stale lines, or manual interventions can be excluded without disclosure.

## Promotion and change control

Promoting `CourtVisionPro` or another sport requires a new ADR and explicit approval. At minimum, that decision must identify the replacement entry point, demonstrate output and safety-gate parity, define provider provenance, migrate operator documentation, and complete a new frozen forward evidence period. Until then, ambiguity is resolved in favor of `courtvision_ai.py` / `CourtVisionAI` and NBA-only production-facing language.
