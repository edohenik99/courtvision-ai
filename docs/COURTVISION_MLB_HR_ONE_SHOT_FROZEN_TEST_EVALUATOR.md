# CourtVision MLB HR one-shot frozen test evaluator

Policy version: `mlb-hr-one-shot-frozen-test-evaluator-v1`

Status: research-only one-shot test execution is implemented; it is not
approved for production, operations, wagering, EV, Kelly, Elite, staking, or
bankroll use.

This contract follows the frozen test-access technical audit. It does not
treat `APPROVE_TEST_LABEL_ACCESS_REVIEW` as authorization by itself. The
write-free planner requires a separate immutable human approval receipt for
the exact test evidence. The reviewed executor then repeats that preflight,
claims one receipt-bound result location, opens only the approved test labels,
calculates only the frozen metrics, and writes one terminal immutable result.

## Required test-access approval receipt

The receipt schema is `mlb-hr-test-access-approval-receipt-v1`. It is a closed,
canonical self-hashed, `create_once_atomic_no_overwrite` record. It must bind:

- the frozen test-access policy and one-shot evaluator policy versions;
- the prior `APPROVE_TEST_LABEL_ACCESS_REVIEW` technical verdict;
- a unique approval ID, timezone-aware approval time, and distinct named
  methodology and operator approvers;
- explicit scope `one_shot_frozen_test_evaluation_only`;
- explicit `APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF` authorization;
- SHA-256 values for the feature pack, temporal split plan, fitted
  preprocessing artifact, test prediction file, and canonical test prediction
  artifact;
- identical accepted-validation and test `pipeline_sha256` values;
- the exact allowed metric list and all one-shot/result-artifact rules; and
- sealed, unopened, unevaluated labels plus research-only, non-operational,
  non-wagering gates.

Missing, malformed, stale, altered, expanded, or self-hash-invalid receipts
fail closed. Receipt approval is limited to label handoff for this exact
research evaluation. Root `approval_status` remains `not_approved` and no
production or wagering approval is implied.

## Identical validation/test pipeline

The planner independently validates the frozen test prediction artifact and
recomputes its canonical pipeline hash. The hash must equal both the test hash
and the accepted validation hash recorded in the receipt. This binds the
feature pack, temporal split plan, fitted preprocessing artifact, model
specification, and code version. Any refit, regenerated pipeline, changed
preprocessing, changed model specification, or changed code version fails.

## Frozen population and label seal

After receipt approval is verified, the planner compares only
`(row_id, game_date, game_id, player_id)` identities. Every feature-pack row in
the frozen test window must have exactly one prediction. Missing, extra,
duplicate, substituted, imputed, or replacement rows fail. No label key is
retrieved or returned, labels remain sealed, and metric calculation remains
disabled.

## Allowed frozen metrics

Only the following predeclared metrics may appear in the eventual complete
test result, for the already frozen probability fields:

- `log_loss`
- `brier_score`
- `roc_auc`
- `pr_auc`
- `calibration_error`

The plan prints each as `frozen_not_computed`. No metric may be added, removed,
selected, or redefined after label handoff. ROI, profit, EV, edge, Kelly,
Elite, staking, bankroll, betting eligibility, and operational performance are
not allowed test-evaluation metrics.

## One-shot, no-rerun, and no-cherry-pick rule

The maximum attempt count is one. All frozen metrics must be reported together
regardless of whether the outcome is favorable, unfavorable, inconclusive,
partial, or failed. A failure after any label value is exposed consumes the
attempt. No second seed, retry, replacement population, regenerated
prediction, alternate evaluator, selective metric report, repair run, or
favorable-result cherry-pick is permitted.

A subsequent research iteration requires a new prospective experiment and
cannot replace, overwrite, or suppress the first result.

## Immutable result artifact

The test-result artifact uses `create_once_atomic_no_overwrite`, binds all
input and policy hashes, and is immutable. Append, overwrite, repair, merge,
in-place update, and operational
publication are prohibited. The result must remain in isolated research
staging and must record complete, inconclusive, partial, and failed outcomes.

The closed schema binds the approval receipt, feature pack, temporal split
plan, fitted preprocessing artifact, frozen prediction artifact, and pipeline
SHA-256 values. It records the exact allowed metric list, supplied metric
results, a timezone-aware timestamp, the terminal attempt status, the consumed
one-shot attempt, no-rerun/no-cherry-pick rules, and research-only gates. Every
metric key is always present; incomplete attempts use null for unavailable
results. EV, Kelly, betting/wagering, and production fields are rejected even
when false.

The writer remains serialization-only. The reviewed executor is the only new
component that opens test labels or invokes the frozen metric functions. It
uses the writer for complete, inconclusive, partial, and failed terminal
results.

## Reviewed one-shot executor

`execute_one_shot_frozen_mlb_hr_test_evaluation` first runs the existing
label-sealed approval, hash, pipeline, prediction, research-gate, and exact
population checks. A missing receipt, stale hash, changed pipeline, missing or
extra prediction, forbidden operational field, or existing attempt exits
before label access.

After preflight it atomically creates the sole result staging directory derived
from the approval-receipt path. Directory creation is the local attempt claim.
From that point the attempt is consumed: controlled label or metric failures
are written as immutable terminal evidence, earlier metric results are retained
for partial attempts, and unavailable metrics remain null. The directory and
result cannot be appended to, repaired, or overwritten.

The executor evaluates both already frozen probability fields with exactly
`log_loss`, `brier_score`, `roc_auc`, `pr_auc`, and `calibration_error` from the
existing validation-metric implementation. It has no argument or call path for
training, prediction generation, fetching, production gates, odds decisions,
EV, Kelly, Elite, staking, wager sizing, betting, or bankroll behavior.

## Read-only planning CLI

```powershell
python scripts\mlb_plan_hr_one_shot_test_evaluation.py `
  --feature-pack C:\research_staging\mlb_hr\feature_pack.json `
  --split-plan C:\research_staging\mlb_hr\temporal_split_plan.json `
  --preprocessing-artifact C:\research_staging\mlb_hr\fitted_preprocessing.json `
  --test-prediction-artifact C:\research_staging\mlb_hr\frozen_test_predictions.json `
  --test-access-approval-receipt C:\research_staging\mlb_hr\test_access_approval_receipt.json
```

Exit code `0` prints `ONE_SHOT_TEST_EVALUATION_PLAN_ONLY`. Exit code `2`
fails closed. Both paths perform no training, preprocessing fit/transform,
prediction generation, live fetching, metric calculation, label opening, or
file write.

## Remaining blockers after the first test result

1. The real approval receipt still must be persisted through the independently
   reviewed create-once evidence workflow before execution.
2. The first terminal result, including an unfavorable, inconclusive, partial,
   or failed result, must be retained and reviewed as-is. It cannot be repaired,
   replaced, or rerun under the same experiment.
3. Any changed model, preprocessing, population, prediction, metric, or code
   requires a new prospectively approved experiment rather than a retry.
4. Production or wagering use would require a separate explicit governance and
   safety decision. This result does not enable or satisfy that decision.
