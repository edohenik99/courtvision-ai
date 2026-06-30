# CourtVision MLB HR Frozen Prediction Artifact Contract

Status: research-only, local-file-only, immutable, default-deny, and not
approved for evaluation, production, or wagering.

This contract defines the bytes that a future separately approved MLB home-run
research predictor must freeze before validation or test labels are opened.
The implementation in this step only loads and validates an existing artifact.
It does not transform rows, train a model, generate predictions, open labels,
compute metrics, fetch data, write artifacts, or approve operational use.

## Schema and allowed fields

The schema version is `mlb-hr-frozen-research-predictions-v1`. The JSON root
is closed: every field below is required and every unlisted field is rejected.

| Field | Frozen requirement |
|---|---|
| `schema_version` | Exact version above. |
| `artifact_type` | `mlb_hr_frozen_research_predictions`. |
| `mode` | `historical_research`. |
| `research_only` | `true`. |
| `approval_status` | `not_approved`. |
| `production_approved` | `false`. |
| `operational_use_enabled` | `false`. |
| `model_training_enabled` | `false`. |
| `prediction_generation_enabled` | `false`; the artifact never authorizes generation or regeneration. |
| `evaluation_enabled` | `false`; validation is not evaluation approval. |
| `live_fetching_enabled` | `false`. |
| `evaluation_data_sealed` | `true` when the predictions are frozen. |
| `immutable` | `true`. |
| `write_policy` | `create_once_atomic_no_overwrite`. |
| `feature_pack_sha256` | SHA-256 of the exact feature-pack file bytes. |
| `temporal_split_plan_sha256` | SHA-256 of the exact split-plan file bytes. |
| `fitted_preprocessing_artifact_sha256` | SHA-256 of the exact preprocessing-artifact file bytes. |
| `model_specification_id` | `mlb-hr-first-research-model-v1`. |
| `model_specification_sha256` | SHA-256 of the exact approved research model-specification document bytes. |
| `code_version` | Non-empty immutable source identifier, normally the reviewed commit or release ID. |
| `code_version_sha256` | SHA-256 of the exact UTF-8 `code_version` string. |
| `split_id` | `validation` or `test`; train or mixed-split artifacts are refused. |
| `window_id` | `<split_id>:<first-game-date>:<last-game-date>` from the bound split plan. |
| `prediction_timestamp` | ISO-8601 timezone-aware datetime. |
| `row_identity_keys` | Exactly `row_id`, `game_date`, `game_id`, `player_id`, in that order. |
| `probability_fields` | Exactly `raw_home_run_probability`, `calibrated_home_run_probability`, in that order. |
| `probability_minimum` | Numeric `0.0`. |
| `probability_maximum` | Numeric `1.0`. |
| `rows` | Non-empty list using the closed row schema below. |
| `artifact_sha256` | SHA-256 of canonical JSON after removing only this field. |

The allowed row fields are exactly:

- `row_id`: non-empty source feature-row identifier;
- `game_date`: ISO-8601 date inside the declared split;
- `game_id`: non-empty game identifier;
- `player_id`: non-empty player identifier;
- `raw_home_run_probability`: finite JSON number in `[0, 1]`; and
- `calibrated_home_run_probability`: finite JSON number in `[0, 1]`.

Booleans, strings, nulls, NaN, and infinities are not probabilities. Each
`row_id` and each declared row-identity tuple must be unique. The artifact is
feature-row keyed so it can remain bound to the exact frozen input row even
when a source pack contains more than one market-context row for a player-game.

## Forbidden fields and data

No label, target, outcome, result, home-run observation, final score, postgame
value, completion state, or evaluation statistic may appear anywhere in a
prediction row or as extra root metadata. In particular, `is_home_run` is
forbidden.

EV, expected-value, Kelly, edge, Elite, odds, sportsbook, profit, ROI, payout,
stake, staking, wager, betting, eligibility, bankroll, grading, and production
selection fields are outside this artifact. A field remains forbidden even if
its value is zero, false, null, or described as diagnostic. The only control
fields are the explicit research-only gates in the closed schema above, and
all execution/production gates must remain false.

## Hash and label-seal order

The loader recomputes the feature-pack, temporal-plan, fitted-preprocessing,
model-specification, code-version, and canonical prediction-content hashes.
It re-hashes every file after validation and refuses any mid-read mutation.
Hash validation reads feature-pack bytes only; it does not parse the feature
pack and cannot return label values.

The dry-run sequence is fixed:

1. validate the frozen prediction artifact and all bindings without opening
   feature-pack labels;
2. after that succeeds, run the existing sealed runner checks and the
   aggregate-only label-handoff contract;
3. confirm that the selected split's evaluation phase requires predictions to
   be frozen and exposes no row-level label values; and
4. stop without evaluation or writes.

Failure at step 1 means label-handoff validation is never called. Validation
labels may open only for evaluation after a validation artifact passes this
boundary. Test labels additionally remain sealed until the unchanged pipeline
has frozen its test artifact under a separately approved execution protocol.

## Immutable write policy

The validator and dry-run CLI are read-only and accept no output directory,
overwrite, append, repair, or promotion option. A future writer is not
implemented or authorized here. If separately approved, it must create the
complete artifact atomically in a new empty non-operational research staging
location, verify its content hash, and never overwrite, append, rename over,
or mutate the artifact after label access. Operational outputs, history,
runtime, models, dashboards, grading, feedback, and bankroll paths remain
prohibited.

## Dry-run CLI

```powershell
python scripts\mlb_dry_run_hr_frozen_predictions.py `
  --feature-pack C:\research_staging\mlb_hr\feature_pack.json `
  --temporal-split-plan C:\research_staging\mlb_hr\temporal_split_plan.json `
  --fitted-preprocessing-artifact C:\research_staging\mlb_hr\fitted_preprocessing.json `
  --prediction-artifact C:\research_staging\mlb_hr\frozen_validation_predictions.json
```

Exit code `0` proves only that the local read-only contracts passed. Exit code
`2` is a closed refusal. Neither exit code authorizes training, prediction
generation, evaluation, backtesting, live fetching, wagering, production, or
artifact writing.

## Remaining blockers before research evaluation

- Separate approval and implementation of the exact train-only transform,
  scaling artifact, model fit, calibration fit, and prediction generator.
- An independently reviewed immutable writer and isolated staging protocol;
  this step validates existing bytes only.
- Proof that every frozen prediction row exactly covers the predeclared
  evaluation population, including missing-prediction and duplicate
  market-context handling.
- Frozen evaluator rules for clipping, missing values, baselines, calibration,
  game-date block-bootstrap intervals, segment reporting, and
  multiple-comparison controls.
- Operator-reviewed validation-to-test promotion rules proving that validation
  findings cannot alter the frozen test pipeline.
- Independent review of real feature, split, preprocessing, model/code, and
  prediction hashes before any label release.

Resolving these research blockers would still not approve EV, Kelly, Elite,
staking, betting eligibility, bankroll use, production publication, or any
operational decision.
