# MLB prospective recovery: finalizer prechange evidence

Status: STATIC_FAILURES_CONFIRMED; runtime reproduction NOT_EXECUTED.

This record was prepared before changing the isolated candidate finalizer files.
The candidate starts at `15b33588508cde4fe5ee92e467356f79a333ee1e`.
The reuse source is local-main commit
`8276a9df4ae3926a0c0d84058380859da26b939c`.
The existing execution checkout, external automation directory, historical
controls, tasks, and runtime evidence remain outside this edit scope.

## Observed caller failures at the candidate base

- `tools/courtvision_pinned_finalizer_contract.py` does not exist. The installed
  pinned finalizer calls its `claim` subcommand before invoking the pipeline.
- `tools/run_courtvision_mlb_nightly_pipeline.ps1` accepts Date, LookbackDays,
  DryRun and SkipGit only. Its caller additionally requires ExpectedCommit,
  AuthorizationReceipt, AuthorizationId, ControlId and ExecutionReceiptPath.
- `tools/courtvision_mlb_nightly_pipeline.py` still runs `git checkout main`
  and `git pull origin main`; runtime execution must verify frozen source
  identity without changing Git state.
- The prospective settlement function and CLI lack required executing-commit
  and authorization-id inputs. The owner of `hr_prospective_trial.py` will
  integrate the matching local-main change separately.
- The local-main wrapper selects `py -3.13` for contract calls and `python`
  for pipeline work. The candidate must require one explicit Python executable
  and propagate it to all three calls. Static follow-up found eight pipeline
  child executable tokens also use literal `python`; these must inherit
  `sys.executable` from the configured parent interpreter.

## Reuse boundaries

Port the existing local-main authorization/receipt implementation, pipeline
integration, and relevant tests. Preserve the internal MLB archive grade and
summary stage before execution-receipt publication. Do not retune the model,
change paper profit/loss formulas, relax gates, or enable any tasks.

Ordinary diffs exaggerate the local-main change because those files contain
mixed line endings. Review the meaningful patch with
`git diff --ignore-space-at-eol` and retain candidate line endings.

Static preparation does not qualify the implementation for execution.
Behavioral tests, result access, provider calls, rehearsal, control publication,
and activation remain NOT_EXECUTED until separately permitted and contained.
