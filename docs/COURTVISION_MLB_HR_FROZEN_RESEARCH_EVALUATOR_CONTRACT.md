# CourtVision MLB HR Frozen Research Evaluator Contract

Status: research-only, read-only, default-deny, and not approved for
operational use, production, or wagering.

This contract defines the evaluation boundary after an immutable MLB home-run
prediction artifact exists. The current implementation has a default dry-run
that validates prerequisites and prints a plan, plus an explicit `validation`
mode that calculates validation metrics and confidence intervals in memory.
Neither mode creates an evaluation artifact.

## Label-opening sequence

The order is fixed and fail-closed:

1. Validate the frozen prediction artifact and its hashes while the labels are
   sealed.
2. Build the expected selected-split identity set and prove exact prediction
   population coverage. The coverage check uses only `row_id`, `game_date`,
   `game_id`, and `player_id`; it does not retrieve or summarize labels.
3. Only after steps 1 and 2 pass, validate the existing feature firewall,
   temporal split, fitted preprocessing, window readiness, and sealed runner
   gates.
4. Request the selected split's `evaluation_only` label handoff with
   `predictions_frozen=true`. The handoff may validate aggregate label counts,
   but the plan cannot expose row-level labels.
5. In dry-run mode, return and print an in-memory evaluation plan. In explicit
   validation mode, read only the identity-matched validation labels, compute
   the frozen metrics and bootstrap intervals, and print them from memory.
6. Stop without opening test labels or writing files.

Failure before step 4 means label handoff is not called. A prediction artifact
cannot be repaired or regenerated after any evaluation label access.

Evaluator contract v1 permits a `validation` prediction artifact only. Test
labels remain sealed until a separate immutable validation review,
validation-to-test promotion decision, and unchanged-pipeline proof are
defined and approved. Training labels remain fitting-only.

## Required artifact validation and population coverage

The feature pack, temporal split plan, fitted preprocessing artifact, model
specification, and prediction artifact must satisfy their existing closed
schemas, research-only gates, content hashes, input bindings, and
mid-validation re-hash checks. The evaluator also requires the runner and
label-handoff input hashes to equal the hashes bound into the frozen prediction
artifact.

The evaluation population is every feature-pack row whose `game_date` belongs
to the artifact's declared split. Identity is the exact four-field tuple
`(row_id, game_date, game_id, player_id)`. The feature population must have
unique identities and unique `row_id` values. The prediction identity set must
be an exact bijection with that population:

- every expected row has exactly one prediction;
- no prediction may refer to an unplanned row;
- missing, null, non-finite, or out-of-range probabilities fail;
- rows cannot be dropped, imputed, substituted, or assigned a default
  probability; and
- predictions cannot be regenerated after labels open.

Missing and extra predictions are hard failures reported before label handoff.
Missing market data affects only the paired market-baseline subset; it never
permits a missing model prediction.

## Allowed metrics

Only the following metrics are allowed, for both frozen raw and calibrated
probabilities:

| Metric | Frozen definition |
|---|---|
| Log loss | Mean binary negative log likelihood. Probabilities are clipped to `[1e-15, 1-1e-15]` only inside numerical metric evaluation; frozen values are never changed. Lower is better. |
| Brier score | Mean squared difference between probability and binary label. Lower is better. |
| ROC-AUC | Rank-based area under the receiver-operating curve. Higher is better. |
| PR-AUC | Positive-class average precision using step-wise recall increments. Higher is better. |
| Calibration error | Expected absolute calibration error over fixed equal-width bins `[0,.1)`, ..., `[.9,1]`, weighted by row count; empty bins are omitted. Lower is better. |

No metric may be added after labels open. Accuracy, ROI, profit, EV, edge,
wager return, Kelly growth, selection rate, and Elite hit rate are not research
evaluation metrics under this contract. Dry-run mode marks all five metrics
`planned_not_computed`; validation mode computes exactly these five for both
frozen probability fields.

## Required baseline comparisons

The future evaluator must compute every allowed metric and paired metric
difference where mathematically defined against:

1. a constant probability equal to the sealed train-window label prevalence,
   on the identical full evaluation population;
2. raw feature-pack `implied_probability`, on the identical predeclared subset
   with valid pregame market coverage, while reporting covered and missing
   market counts; and
3. the same logistic model family without market-context predictors, supplied
   by a separately frozen, hash-bound ablation prediction artifact on the
   identical full population.

Comparisons never create EV, fair odds, betting eligibility, or a selection.
The current prediction schema does not carry a separately frozen ablation
artifact, so that required input remains a blocker to real metric execution.

## Bootstrap confidence intervals

Every metric and every paired baseline difference requires a 95% percentile
confidence interval from a game-date block bootstrap. One replicate samples
the original number of game-date blocks with replacement and includes all rows
from each sampled date. Model and baseline values use the same sampled blocks.

The frozen seed is `20260629`; the required replicate count is `2000`, with at
least `1900` successful replicates. Report the seed, requested and successful
replicate counts, row count, positive and negative counts, unique date count,
and market coverage. A replicate in which a metric is undefined is not a
successful replicate. Failure to reach the minimum, or any otherwise
unestimable interval, yields `inconclusive`, never a pass.

The implementation uses Python's local seeded pseudo-random generator, sorts
the unique ISO game dates before sampling, shares every sampled row index
across probability series, and uses deterministic linear percentile
interpolation. The reference test locks exact output for the required `2000`
iterations and seed `20260629`.

## Segmentation policy

The full exact evaluation population is the sole primary population.
Predeclared diagnostic segments are market-covered versus market-missing and,
when the corresponding feature was frozen before labels opened, `batter_hand`,
`pitcher_hand`, and `platoon_side`.

A segment needs at least 200 rows, 20 positives, and 20 negatives. An
underpowered segment reports counts only. Missing values use a predeclared
missing bucket; categories may not be merged or invented after outcomes are
seen. No post-label subgroup search is allowed. Segment findings are
diagnostic only and cannot drive feature selection, hyperparameter changes,
promotion, eligibility, or wagering. Any future exploratory segmentation
requires a new frozen prediction run and a separately approved contract—not a
retry against opened labels.

## Immutable evaluation artifact policy

No evaluation writer exists in this step. A future separately approved writer
must:

- bind exact SHA-256 values for every input, the evaluator contract, code
  version, metric definitions, baseline inputs, bootstrap policy, and segment
  policy;
- include population counts, metric results, paired differences, intervals,
  and explicit inconclusive/failure states;
- use canonical content hashing and create-once atomic publication in a new,
  isolated, non-operational research staging location;
- refuse overwrite, append, repair, merge, in-place update, or rename-over;
  and
- never write to `outputs`, `test_outputs`, runtime, history, grading,
  feedback, dashboard, model, bankroll, or other operational paths.

An evaluation artifact is immutable after label access. A failed or partial
write is not repaired in place, and a result cannot be regenerated from an
altered prediction population.

## Explicit research-only gates

Every result remains `research_only=true` and `approval_status=not_approved`.
Model training, preprocessing fitting/transform execution, prediction
generation, test evaluation, live fetching, operational use,
evaluation-artifact writing, betting eligibility, EV, Kelly, Elite, staking,
bankroll use, and production approval all remain false. Validation-mode metric
computation is in-memory and validation-only; it is not an execution,
promotion, production, or wagering gate.

## Dry-run CLI

```powershell
python scripts\mlb_dry_run_hr_evaluator.py `
  --mode validation `
  --feature-pack C:\research_staging\mlb_hr\feature_pack.json `
  --temporal-split-plan C:\research_staging\mlb_hr\temporal_split_plan.json `
  --fitted-preprocessing-artifact C:\research_staging\mlb_hr\fitted_preprocessing.json `
  --prediction-artifact C:\research_staging\mlb_hr\frozen_validation_predictions.json
```

Omit `--mode validation` for the default plan-only dry-run. Exit code `0`
prints the selected in-memory result and writes nothing. Exit code `2` is a
closed refusal. Neither result authorizes test evaluation or operational use.

## Blockers before validation-to-test promotion

- Separate approval and implementation of the already specified train-only
  transform, scaling, model fit, calibration fit, and prediction generator.
- An independently reviewed, immutable frozen-prediction writer and real
  validation prediction artifact covering the exact validation population.
- A separately frozen no-market ablation prediction artifact and binding
  schema.
- Complete the three required baseline inputs and paired baseline comparisons,
  including the separately frozen no-market ablation artifact.
- A separately approved immutable evaluation-artifact schema/writer and
  isolated staging protocol.
- Operator verification of real input, model/code, and prediction hashes
  before label release.
- A reviewed immutable validation result and explicit pass/fail promotion
  policy that cannot modify the frozen test pipeline.

Test evaluation additionally requires the unchanged-pipeline promotion proof
described above. Even after all research blockers are resolved, EV, Kelly,
Elite, staking, betting, bankroll use, and production approval remain outside
this contract.
