# CourtVision MLB HR Model Specification and Sealed Label Handoff

Status: **approved as the first allowed research specification only**. This is
not approval to implement or run training, transform rows, make predictions,
compute betting value, size wagers, enable Elite selection, or use the result
in production. All existing MLB research-only and `not_approved` gates remain
in force.

## Scope and first allowed model

The first allowed model family is an L2-regularized binomial logistic
regression with a logit link. It estimates the probability that a batter hits
at least one home run in one game. It is deliberately interpretable and must
be evaluated before any more expressive model family is proposed.

The eventual research pipeline, if separately approved, must use:

- numeric median imputation and missing indicators from the sealed,
  train-fitted preprocessing contract;
- train-only centering and scaling for numeric predictors;
- train-only categorical vocabularies and one-hot encoding, including the
  existing missing, rare, and unknown tokens;
- no class weighting, outcome-based resampling, or synthetic labels;
- an L2 strength selected only inside time-ordered train folds from the fixed
  candidate set `C = {0.01, 0.1, 1.0, 10.0}`, using mean train-fold log loss
  and choosing the smaller `C` on a tie; and
- a fixed implementation/version/seed record before validation predictions.

The current fitted-preprocessing artifact is still validation-only and does
not authorize these transformations or contain a numeric scaling artifact.
That is an execution blocker, not permission to infer or fit missing values.

## Allowed feature-pack inputs

Only columns already accepted by the MLB HR feature firewall may be
considered. A particular pack may use a subset, but it may not add an unknown
column. The model-eligible predictors are:

- Pregame categorical/context: `batter_hand`, `pitcher_hand`, `platoon_side`,
  `weather_wind_direction`, `weather_wind_out_to_field`, and `roof_status`.
- Pregame numeric/context: `weather_temperature`, `weather_wind_speed`,
  `weather_humidity`, `park_factor_hr`, `park_factor_lhb`, `park_factor_rhb`,
  and `altitude`.
- Historical rolling: `hitter_pa_window`, `hitter_recent_hr_rate`,
  `hitter_recent_barrel_rate`, `hitter_recent_hard_hit_rate`,
  `hitter_recent_fly_ball_rate`, `hitter_avg_exit_velocity`,
  `hitter_max_exit_velocity`, `hitter_season_hr_rate_to_date`,
  `hitter_season_barrel_rate_to_date`,
  `hitter_season_hard_hit_rate_to_date`,
  `pitcher_batters_faced_window`, `pitcher_hr_allowed_rate_to_date`,
  `pitcher_barrel_allowed_rate_to_date`,
  `pitcher_hard_hit_allowed_rate_to_date`,
  `pitcher_fly_ball_allowed_rate_to_date`, and `pitcher_pitch_mix_json`.
- Pregame market context: `sportsbook`, `odds_provider`,
  `hr_market_available`, `american_odds`, `decimal_odds`,
  `implied_probability`, and `odds_is_fresh_for_pregame`.

`odds_collected_at` and `odds_as_of` remain allowed in the feature pack only
as temporal-lineage checks. They are not model predictors. Every value must
still satisfy its pregame availability cutoff.

## Forbidden model inputs

The following are forbidden, even if present elsewhere in source data:

- `is_home_run` and every other label, outcome, result, or target alias;
- home-run counts, plate appearances, completion state, label availability,
  label source, label timestamps, event text, and postgame Statcast values;
- row/player/game identifiers, names, dates, event start times, provenance,
  availability metadata, and split membership as predictors;
- final or closing results, grades, profit, ROI, model outputs, predicted
  probabilities, fair probabilities, edges, EV, Kelly values, Elite flags,
  stake or wager fields, and betting eligibility;
- validation/test distribution statistics, category levels, transforms, or
  target rates learned before the corresponding predictions are frozen; and
- every field not explicitly accepted by the current feature allowlist.

## Label column and handoff rules

The sole target column is `is_home_run`. It must be present once per
player-game row, stored as a native JSON boolean, and remain outside both
`feature_names` and `feature_values`. Missing, null, string, integer, or
non-binary values fail closed; a missing label is never interpreted as
`false`. Label/outcome aliases are not permitted as additional targets.

The handoff validator may inspect all three splits only to verify existence,
binary type, leakage separation, and aggregate split distributions. It
returns counts and rates, never row-level target values. A future model
consumer is subject to this phase contract:

| Phase | Train labels | Validation labels | Test labels |
|---|---|---|---|
| Feature preparation | Sealed | Sealed | Sealed |
| Baseline fit | Fitting only | Sealed | Sealed |
| Train-only calibration fit | Fitting only | Sealed | Sealed |
| Validation prediction | Sealed | Sealed | Sealed |
| Validation evaluation after predictions freeze | Sealed | Evaluation only | Sealed |
| Test prediction | Sealed | Sealed | Sealed |
| Test evaluation after predictions freeze | Sealed | Sealed | Evaluation only |

Train labels may never be requested for prediction or operational output.
Validation/test labels may never be used for fitting, preprocessing,
hyperparameter selection, feature selection, calibration fitting, retrying a
run, or changing a frozen pipeline. Test labels remain sealed unless the
validation review has passed and the test predictions from the unchanged
pipeline have already been frozen. No train-plus-validation refit is allowed
before test evaluation under this specification.

## Calibration plan

Calibration fitting is train-only. If execution is later approved, strictly
time-ordered out-of-fold scores must be generated inside the train window and
a one-dimensional Platt sigmoid may be fitted to those train scores and train
labels. The base model and calibrator are then frozen together before any
validation prediction. Raw and calibrated probabilities must both be
reported; validation or test labels cannot refit or select the calibrator.

Calibration reporting consists of log loss, Brier score, calibration
intercept, calibration slope, and a reliability table with bin counts. Empty
or underpowered bins must be combined according to a rule frozen before label
release, not edited after viewing outcomes.

## Required baseline comparisons

The research model must be compared on the same frozen rows with:

1. a constant probability equal to the train-window HR prevalence;
2. raw `implied_probability` on the market-covered subset, with coverage and
   missing-market rows reported; and
3. the same logistic family without market-context predictors as a
   predeclared diagnostic ablation.

The primary metric is log loss. Brier score, ROC AUC, average precision,
calibration intercept, and calibration slope are secondary diagnostics. No
metric is an EV, wagering, ROI, or production-approval metric.

## Uncertainty reporting

Every validation/test metric and every difference from a baseline must include
a 95% interval from a predeclared game-date block bootstrap so correlated
player rows from the same date remain together. Report the resampling seed,
block construction, successful replicate count, row count, positive count,
and market coverage. If an interval cannot be estimated, the result is
inconclusive rather than passing. Subgroup results require their counts and
positive counts and are diagnostic only.

## Failure criteria

The future research run fails closed if any of the following occurs:

- an input hash, feature firewall, temporal split, fitted-preprocessing,
  window-readiness, research-only gate, or label-handoff check fails;
- labels are missing, non-binary, outside exactly one split, exposed as a
  feature, or requested outside the phase table;
- any preprocessing or model parameter is learned from validation/test, or
  predictions are regenerated after labels are released;
- fitting does not converge, produces non-finite coefficients/probabilities,
  or does not preserve the declared row set;
- validation log loss does not improve on the train-prevalence baseline;
- the 95% interval for baseline-minus-model validation log loss includes
  zero, in which case the result is explicitly **inconclusive**;
- the market-informed model is worse than raw implied probability on the
  identical covered validation rows; or
- required calibration or uncertainty diagnostics cannot be produced.

Failure or success remains a research finding only. It cannot toggle
prediction, EV, Kelly, Elite, staking, betting eligibility, or production
approval.

## Current stop boundary

The accompanying dry-run command validates existing artifacts and prints
aggregate label availability by phase. It writes nothing and contains no
model implementation. Actual research execution remains blocked pending a
separate approval for an executable transformer, scaling artifact, model
implementation, frozen prediction artifact schema, evaluation code,
uncertainty implementation, and an operator-reviewed execution protocol.
