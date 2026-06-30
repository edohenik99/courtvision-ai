# CourtVision MLB HR frozen test-evaluation access policy

Policy version: `mlb-hr-frozen-test-evaluation-access-v1`

This is the final technical gate before a human review may authorize one
evaluation-only opening of frozen MLB HR test labels. A passing read-only audit
prints `APPROVE_TEST_LABEL_ACCESS_REVIEW`. It does not open labels, calculate
test metrics, authorize the test evaluation by itself, approve production, or
enable EV, Kelly, Elite, staking, bankroll, betting, or any operational path.

## Required validation-promotion evidence

The exact feature pack, temporal split plan, fitted preprocessing artifact,
frozen validation prediction artifact, validation results, and immutable
validation-promotion audit result must be supplied again. The access audit
independently reruns the frozen validation-promotion audit. It denies access
unless that audit still returns `PROMOTE_TO_TEST_REVIEW` with test labels
sealed and every validation input unchanged.

The validation-promotion audit result uses schema
`mlb-hr-validation-promotion-audit-result-v1`. It is an immutable receipt for
the reviewed promotion decision, not a production approval. It must:

- contain `verdict=PROMOTE_TO_TEST_REVIEW` and an empty failure list;
- bind the file and canonical-content hashes of the feature pack, split plan,
  preprocessing artifact, validation predictions, and validation results;
- bind the model-specification, code-version, and canonical pipeline hashes;
- bind its own canonical content with `audit_result_sha256`;
- record that test labels remain sealed, unopened, and unevaluated; and
- explicitly retain research-only, not-approved, write-free, non-operational,
  and non-wagering status.

Unknown, missing, malformed, stale, or mismatched receipt fields deny access.
The receipt must be persisted outside this CLI by the approved immutable
research-evidence workflow; this audit never creates or updates it.

## Identical pipeline requirement

The accepted validation and test prediction artifacts must produce the same
canonical `pipeline_sha256`. The fingerprint binds:

- feature-pack SHA-256;
- temporal-split-plan SHA-256;
- fitted-preprocessing-artifact SHA-256;
- model-specification ID and SHA-256; and
- code version and code-version SHA-256.

The test artifact has different rows and therefore a different artifact hash,
but no feature, split, preprocessing, base-model, calibration, specification,
or code component may change. Train-plus-validation refitting is prohibited.

## Frozen full-population test predictions

Before test labels open, exactly one immutable `split_id=test` prediction
artifact must already exist with `evaluation_data_sealed=true`,
`immutable=true`, and `write_policy=create_once_atomic_no_overwrite`. The
artifact may contain only the frozen raw and calibrated probabilities and the
four identity fields. It cannot contain labels, outcomes, metrics, odds
decisions, EV, Kelly, staking, bankroll, or betting fields.

The required population is every feature-pack row whose `game_date` belongs
to the frozen test window. Coverage is compared as an exact bijection on
`(row_id, game_date, game_id, player_id)` while label values are neither
retrieved nor summarized. Duplicate feature identities, duplicate prediction
identities, missing predictions, extra predictions, or an empty test
population deny access. No row may be dropped, imputed, replaced, or added.

## Test-label access checklist

All items are conjunctive:

- [ ] The exact validation evidence independently re-audits to
      `PROMOTE_TO_TEST_REVIEW`.
- [ ] The immutable promotion receipt and every recorded hash match the
      supplied bytes.
- [ ] Validation attempt 1 remains complete, with no rerun, regeneration, or
      post-label model, calibration, metric, threshold, baseline, or subgroup
      selection.
- [ ] Test labels are still sealed, unopened, and unevaluated.
- [ ] The full test population has one and only one frozen prediction per
      identity.
- [ ] The test `pipeline_sha256` exactly equals the accepted validation
      `pipeline_sha256`.
- [ ] Every production, operational, live-fetching, EV, Kelly, Elite, staking,
      bankroll, wager-sizing, betting, and eligibility gate remains false.
- [ ] The read-only audit returns `APPROVE_TEST_LABEL_ACCESS_REVIEW` without
      changing any input or operational folder.
- [ ] Named methodology and operator reviewers approve one evaluation-only
      handoff for these exact hashes under the separate immutable-result
      protocol.

The first eight items are machine-auditable prerequisites. The final reviewer
authorization is deliberately outside this CLI. Until it is recorded, label
access and test evaluation remain unauthorized.

## One-shot, no-rerun, and no-cherry-pick rule

The approved hash set may be used for one frozen test evaluation only. All
predeclared test metrics and baselines must be reported together, including an
unfavorable, inconclusive, partial, or failed result. Test labels cannot be
used to alter the model, preprocessing, calibration, features, population,
thresholds, metrics, baselines, or subgroups. Predictions cannot be regenerated
after labels open. No second seed, attempt, artifact, evaluator variant,
replacement population, selective report, or favorable rerun is permitted.

If execution fails after any test-label value is exposed, the one-shot attempt
is consumed and the failure is final evidence under this policy. A new
research iteration requires a new prospectively declared experiment and
cannot replace or overwrite this test result.

## Immutable artifacts and research-only status

All seven supplied evidence files are hashed before and after the audit. Any
change during the audit denies access. Prediction artifacts and the promotion
receipt are canonical self-hashed and create-once. Validation results remain
bound by `validation_result_sha256`. A future test-result artifact must also be
create-once, atomic, no-overwrite, complete, and hash-bound; that writer and
execution protocol are not implemented by this step.

Both audit outcomes are research-only. They leave `approval_status` as
`not_approved`; perform no writes, fetching, fitting, prediction, label access,
or metric calculation; and grant no production or wagering permission.

## Read-only CLI

```powershell
python scripts\mlb_audit_hr_test_access.py `
  --feature-pack C:\research_staging\mlb_hr\feature_pack.json `
  --split-plan C:\research_staging\mlb_hr\temporal_split_plan.json `
  --preprocessing-artifact C:\research_staging\mlb_hr\fitted_preprocessing.json `
  --validation-prediction-artifact C:\research_staging\mlb_hr\frozen_validation_predictions.json `
  --validation-results C:\research_staging\mlb_hr\validation_results.json `
  --validation-promotion-audit-result C:\research_staging\mlb_hr\validation_promotion_audit_result.json `
  --test-prediction-artifact C:\research_staging\mlb_hr\frozen_test_predictions.json
```

Exit code `0` prints `APPROVE_TEST_LABEL_ACCESS_REVIEW`; exit code `2` prints
`DENY_TEST_LABEL_ACCESS`. Both outcomes write nothing and leave labels sealed.

## Remaining blockers before the one-shot test evaluation

Even after a passing technical audit, the following remain required:

1. independent methodology and operator review of the exact approved hashes;
2. explicit authorization for one evaluation-only test-label handoff;
3. a separately reviewed evaluator mode restricted to the frozen test
   contract and predeclared metrics; and
4. an immutable, create-once test-result writer that records all required
   evidence without enabling production or wagering behavior.
