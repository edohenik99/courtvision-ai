# CourtVision MLB HR Sealed Backtest Runner Contract

Status: research-only, local-file-only, read-only, default-deny, not approved.

This contract is the safety shell for a possible future MLB home-run research
backtest. The current runner validates inputs and prints a plan only. It does
not fit or transform data, train a model, make predictions, compute metrics,
fetch data, execute a backtest, write an artifact, or enable any production or
wagering action.

## Allowed inputs

The dry-run CLI accepts exactly three existing local JSON files:

1. one `mlb-hr-research-feature-pack-v1` feature pack;
2. one `mlb-hr-research-temporal-split-plan-v1` temporal split plan bound to
   the exact feature-pack SHA-256; and
3. one `mlb-hr-fitted-preprocessing-artifact-v1` artifact bound to the exact
   feature-pack and split-plan SHA-256 values.

There is no URL, provider, date, model, threshold, output path, overwrite, or
production option. Live fetching and operational folders are outside the
contract.

## Required readiness gates

The command fails closed unless all of these gates pass:

- the feature pack is `historical_research`, `not_approved`, and explicitly
  disables every execution and wagering gate;
- every declared feature passes the exact allowlist and timestamp-aware
  feature firewall;
- the temporal plan uses non-overlapping, strictly ordered, whole-date
  60/20/20 train, validation, and test windows;
- the split plan records `READY_FOR_RESEARCH_BACKTEST` and matches the feature
  pack hash;
- the fitted preprocessing artifact passes its exact schema, safety fields,
  content hash, feature-pack hash, and split-plan hash;
- the artifact records train-only fitting and transform-only validation/test;
- every temporal window is
  `WINDOW_READY_FOR_RESEARCH_BACKTEST`; `WINDOW_READY_FOR_REVIEW` and
  `WINDOW_NOT_READY` both refuse the plan; and
- the evaluation-label access protocol below passes.

Passing means only `BACKTEST_EXECUTION_PLAN_ONLY`. It is not permission to run
a backtest.

## Fitted preprocessing artifact validation

The runner recomputes all three input file hashes. The fitted artifact must
match the feature and split files byte-for-byte through its recorded SHA-256
bindings, and its own `artifact_sha256` must match its canonical content. Its
train date range, row count, numeric medians, missing indicators, categorical
vocabularies, rare-category mappings, and category policy must reproduce the
sealed preprocessing plan.

The runner hashes every input again after validation and refuses if any input
changed during planning.

## Window readiness validation

Readiness is measured on independent `(game_date, game_id, player_id)` rows.
Duplicate sportsbook rows do not increase sample size. Label conflicts fail.
Each train, validation, and test window must meet the documented player-game,
game, player, positive-label, negative-label, odds, weather, ballpark, and date
span floors in `COURTVISION_MLB_HR_WINDOW_READINESS_GATES.md`.

Thresholds are fixed prerequisites. They must never be lowered to make a pack
pass.

## Fitting and transformation boundary

The fitted preprocessing artifact is the only accepted preprocessing state.
It was derived from train rows only. The dry-run runner validates that state
but performs no fitting and no transformation.

For a separately approved future executor:

- train may use the already sealed train-fitted preprocessing parameters;
- validation must be transform-only with those exact parameters and may not
  update medians, vocabularies, missingness rules, encodings, feature choices,
  or thresholds; and
- test must be transform-only with those exact parameters and may not be read
  for model, preprocessing, threshold, or metric-selection decisions.

Model fitting is not part of this contract. Adding it requires a new reviewed
contract and explicit approval; this shell contains no estimator call.

## Label access protocol

`is_home_run` is the only evaluation label. Every row must expose it as an
explicit boolean at the row metadata level. It must remain absent from
`feature_names`, `feature_values`, preprocessing summaries, and transformed
feature matrices.

The current shell may inspect labels only in the evaluation-planning gate to
verify presence, type, class counts, conflicts, and per-window statistical
readiness. It does not return row-level labels in the execution plan. No model,
transformer, or predictor receives labels because none of those operations
exists here.

A future executor must use a separate evaluator-owned label view. Validation
labels remain unavailable until validation predictions are immutable; test
labels remain unavailable until the final test predictions are immutable.
Opening either label view earlier, using labels for preprocessing, or using
test results to revise the model invalidates the run. Any future train-label
handoff for explicitly approved model fitting requires a separate contract.

## Predeclared metric definitions

The dry run lists these metrics as `planned_not_computed`:

| Metric | Role | Definition | Direction |
|---|---|---|---|
| Log loss | Primary | Mean negative Bernoulli log likelihood over independent player-games. | Lower is better. |
| Brier score | Secondary calibration | Mean squared error between predicted HR probability and the binary outcome. | Lower is better. |
| ROC AUC | Secondary discrimination | Probability that a randomly selected positive ranks above a randomly selected negative. | Higher is better. |
| Average precision | Secondary discrimination | Positive-class precision averaged across recall increments. | Higher is better. |
| Calibration intercept | Diagnostic only | Intercept from outcome on prediction log-odds with slope fixed at one. | Closer to zero is better. |
| Calibration slope | Diagnostic only | Slope from outcome on prediction log-odds. | Closer to one is better. |

Metrics are player-game metrics, not sportsbook-row metrics. A future
methodology approval must still freeze probability clipping, missing-prediction
handling, confidence intervals, grouping/cluster rules, calibration bins,
baseline comparisons, segment reporting, and multiple-comparison controls
before any prediction is generated. No profit, ROI, EV, stake, Kelly, or
betting metric belongs to this contract.

## Immutable artifact policy

This dry-run command writes nothing and has no output argument. Printed text is
diagnostic only and is not an approved research result artifact.

Any future executor must write only to a new, empty, non-operational research
staging directory. Its manifest must bind the runner contract version, code
version, feature pack, temporal plan, preprocessing artifact, model
specification, predictions, and evaluation payload by SHA-256. Files must be
created atomically, content-addressed, and never overwritten or appended.
Operational `outputs`, history, runtime, model, dashboard, grading, and
bankroll paths remain prohibited. Artifact writing is not implemented by this
step.

## Dry-run CLI

```powershell
python scripts\mlb_dry_run_hr_research_backtest.py `
  --feature-pack C:\courtvision_staging\mlb_hr\mlb_hr_research_feature_pack.json `
  --temporal-split-plan C:\courtvision_staging\mlb_hr\temporal_split_plan.json `
  --fitted-preprocessing-artifact C:\courtvision_staging\mlb_hr\mlb_hr_fitted_preprocessing.json
```

Exit code `0` means every gate passed and an in-memory execution plan was
printed. Exit code `2` means the contract refused. Neither code means that a
backtest, model, prediction, or metric computation ran.

## Explicit non-betting status

MLB remains research-only and not approved. Model training, predictions, live
fetching, backtest execution, production approval, betting eligibility, EV,
Kelly, Elite, staking, bankroll use, wager sizing, and operational publication
all remain disabled. This contract cannot promote or approve any play.

## Blockers before actual research execution

- Independent review of the real feature pack, split plan, fitted artifact,
  provenance, leakage controls, and per-window power evidence.
- A separately approved model specification and train-label handoff contract.
- A transform implementation proving exact train-artifact reuse and no refit.
- A prediction contract that seals validation output before validation labels
  open and seals final test output before test labels open.
- Frozen uncertainty intervals, baseline rules, segment policy, probability
  clipping, missing-prediction handling, and multiple-comparison controls.
- An immutable, isolated research artifact writer and independent methodology
  review. None is implemented or authorized here.

Even after those research blockers are resolved, production or betting use
would require separate evidence and explicit approval outside this contract.
