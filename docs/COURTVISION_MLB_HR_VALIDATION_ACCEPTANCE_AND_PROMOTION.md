# CourtVision MLB HR validation acceptance and promotion policy

Policy version: `mlb-hr-validation-acceptance-and-promotion-v1`

This policy defines the evidence required to move an unchanged research
pipeline to **frozen test review**. A passing audit prints
`PROMOTE_TO_TEST_REVIEW`; it does not approve test-label access, run a test
evaluation, approve production use, or enable any betting operation.

## Evidence boundary

The audit consumes one existing feature pack, temporal split plan, fitted
preprocessing artifact, frozen **validation** prediction artifact, and one
validation-result object (an in-memory mapping or JSON file). It writes
nothing. The feature pack is hashed but is not opened by this audit, so the
audit does not inspect validation or test labels.

The result must use schema
`mlb-hr-validation-promotion-evidence-v1`, bind its canonical content with
`validation_result_sha256`, and report only `split_id=validation`. Unknown or
missing fields fail closed. The evidence must remain `research_only=true`,
`approval_status=not_approved`, `production_approved=false`, and
`operational_use_enabled=false`.

## Required baseline comparisons

Every one of the five frozen metrics—log loss, Brier score, ROC-AUC, PR-AUC,
and calibration error—must be present for raw and calibrated model
probabilities and as a paired improvement comparison against all three
predeclared baselines:

1. Train-window prevalence constant on the identical full validation
   population.
2. Raw feature-pack `implied_probability` on the identical predeclared
   market-covered subset. Its source hash must equal the feature-pack hash,
   and covered plus missing rows must equal the full population.
3. A separately frozen, hash-bound no-market logistic ablation on the
   identical full validation population.

An improvement is oriented so positive is always better: baseline minus model
for lower-is-better metrics and model minus baseline for higher-is-better
metrics. No required paired point estimate may be negative.

## Minimum acceptance thresholds

All conditions are conjunctive:

- Calibrated ROC-AUC is at least `0.55`, and its 95% lower confidence bound is
  strictly greater than `0.50`.
- Calibrated calibration error is at most `0.05`, and its 95% upper confidence
  bound is at most `0.075`.
- The 95% lower bound for calibrated log-loss improvement is strictly greater
  than zero against each of the train-prevalence, raw-implied-probability, and
  no-market-ablation baselines.
- Against train prevalence, the 95% lower bounds for calibrated Brier-score
  improvement and PR-AUC improvement are also strictly greater than zero.
- Calibration may not worsen the point estimate for log loss, Brier score, or
  calibration error compared with raw predictions. Because the frozen Platt
  transform is monotonic, raw and calibrated ROC-AUC and PR-AUC must match to
  absolute tolerance `1e-12`.

These are research acceptance thresholds only. They do not alter model,
selection, Elite, EV, Kelly, bankroll, staking, or betting thresholds.

## Confidence-interval rules

Every model metric and every paired baseline difference must have an
`estimated` 95% percentile interval from the frozen paired game-date block
bootstrap:

- unit: `game_date_block`;
- method: `paired_percentile_bootstrap`;
- seed: `20260629`;
- requested replicates: `2000`;
- minimum successful replicates: `1900`; and
- deterministic shared resamples for model and baseline series.

Missing bounds, non-finite values, reversed bounds, an `inconclusive` status,
or fewer than 1900 successful replicates fails acceptance. Diagnostic
subgroups cannot substitute for the full primary population.

## Calibration requirements

The only accepted calibration declaration is a train-only Platt sigmoid fit
from strictly time-ordered out-of-fold train scores. The base model and
calibrator must have been frozen before validation labels opened. Validation
or test refitting, calibrator selection using validation labels, and a
reliability-bin policy chosen after validation labels opened all fail.

## No cherry-picking and no rerun

The evidence must identify attempt `1` and attest that metrics and baselines
were predeclared, the validation predictions were frozen before label access,
and all required results are reported. Any rerun after validation-label
access, prediction regeneration, omitted required result, or post-label model,
metric, baseline, threshold, calibration, or subgroup selection invalidates
the run. A failed or partial attempt is not repaired or replaced with a more
favorable attempt.

## Unchanged-pipeline proof

The audit independently hashes every supplied file, validates the frozen
prediction artifact's own canonical hash and input bindings, compares all
hashes with the validation evidence, and re-hashes file inputs before
returning.

`pipeline_sha256` is the canonical SHA-256 fingerprint of:

- feature-pack SHA-256;
- temporal-split-plan SHA-256;
- fitted-preprocessing-artifact SHA-256;
- model-specification ID and SHA-256; and
- code version and code-version SHA-256.

The validation prediction hash is bound separately because a future frozen
test prediction contains different rows. Before test-label access, the test
prediction must bind the exact same pipeline components and therefore produce
the same `pipeline_sha256`. No train-plus-validation refit, preprocessing
change, calibration change, code change, model change, feature change, or
split-plan change is permitted.

## Failure criteria

The result is `DO_NOT_PROMOTE` if any required artifact or evidence hash does
not match; the prediction artifact is not validation-only; a required metric,
baseline, interval, population, or calibration declaration is missing or
invalid; a minimum threshold fails; a no-rerun attestation fails; test labels
were opened or test metrics were computed; an input changes during audit; or
any research-only safety field is relaxed.

## Test-label access approval checklist

`PROMOTE_TO_TEST_REVIEW` completes only the first item. Test labels must remain
sealed until an authorized reviewer records every remaining item:

- [ ] The read-only audit returned `PROMOTE_TO_TEST_REVIEW` for the reviewed,
      immutable validation result and exact validation artifacts.
- [ ] The validation-result content hash, all source hashes, and the computed
      pipeline hash were independently reviewed.
- [ ] The first-attempt/no-rerun and no-post-label-selection attestations were
      independently reviewed.
- [ ] A full-population test prediction artifact was generated while test
      labels were sealed, then frozen create-once before any test-label access.
- [ ] The frozen test prediction has exact row coverage and the same feature,
      split-plan, preprocessing, model-specification, code-version, and
      `pipeline_sha256` values as the accepted validation pipeline.
- [ ] A separate read-only pre-access audit confirms the test prediction hash,
      unchanged-pipeline proof, research-only gates, and sealed test labels.
- [ ] Named methodology and operator reviewers explicitly approve one
      evaluation-only test-label handoff for that exact frozen test artifact.
- [ ] The label handoff exposes test labels only to the evaluator, performs no
      training/refit/regeneration, writes only through a separately approved
      immutable research-result protocol, and cannot trigger operational use.

If any item is incomplete, test-label access remains prohibited. One frozen
test evaluation is final research evidence; its outcome cannot be used to
revise and rerun the pipeline under this contract.

## CLI

```powershell
python scripts\mlb_audit_hr_validation_promotion.py `
  --feature-pack C:\research_staging\mlb_hr\feature_pack.json `
  --temporal-split-plan C:\research_staging\mlb_hr\temporal_split_plan.json `
  --fitted-preprocessing-artifact C:\research_staging\mlb_hr\fitted_preprocessing.json `
  --prediction-artifact C:\research_staging\mlb_hr\frozen_validation_predictions.json `
  --validation-results C:\research_staging\mlb_hr\validation_results.json
```

Exit code `0` means review may begin and prints `PROMOTE_TO_TEST_REVIEW`. Exit
code `2` prints `DO_NOT_PROMOTE`. Both paths write nothing and leave test
evaluation, test-label access, production approval, and all wagering behavior
disabled.
