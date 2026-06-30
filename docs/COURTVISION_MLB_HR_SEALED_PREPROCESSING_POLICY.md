# CourtVision MLB HR Sealed Preprocessing Policy

Status: research planning only. This policy and its dry-run planner do not fit
or persist an executable transformer, train a model, generate predictions, run
a backtest, fetch data, calculate EV, use Kelly sizing, select Elite plays,
size stakes, establish betting eligibility, or grant production approval.

Policy version: `mlb-hr-sealed-preprocessing-v1`.

## Entry gates

Preprocessing planning accepts one immutable MLB HR feature-pack JSON plus
exactly one temporal source:

- a versioned temporal split-plan JSON bound to the feature pack by SHA-256; or
- the immutable staged historical pack from which the existing read-only
  temporal dry run can reproduce the split.

Before any summaries are calculated, the feature pack must pass the exact
feature-name and timestamp-lineage firewall. Labels, outcomes, forbidden
leakage fields, duplicate features, unknown features, late features, and
same-day rolling outcomes fail closed. The temporal source must then pass the
existing whole-date ordering, non-overlap, readiness, and 18/6/6 minimum-date
rules. Every feature row must belong to exactly one split, and train,
validation, and test must each contain at least one feature row.

All research and wagering gates remain explicitly false and approval remains
`not_approved`.

## Column classes

The planner uses a frozen, code-reviewed type map; it does not infer a feature
type from validation or test values.

### Numeric

All allowlisted rate, count, score, weather-number, park-factor, altitude, and
price/probability fields are numeric. `hr_market_available` and
`odds_is_fresh_for_pregame` are binary numeric fields and accept only JSON
booleans. Other numeric fields accept finite JSON numbers only. Numeric
strings, infinities, NaN, and booleans in non-binary fields are rejected.

For each numeric feature, the future transformer must:

1. calculate the median from non-missing train rows only;
2. replace missing train, validation, and test values with that frozen train
   median; and
3. emit a missing indicator for every numeric feature, using a schema fixed
   before validation/test transformation.

An entirely missing train numeric feature is a refusal because no train median
exists. This policy does not authorize scaling. If a future model contract
requires scaling, its center and scale must be learned from train only and the
policy version must change.

### Categorical

The categorical fields are batter/pitcher hand, platoon side, wind direction,
wind-out field, roof status, pitcher pitch-mix JSON, sportsbook, and odds
provider. Values are stripped of surrounding whitespace but otherwise retain
their exact spelling and case. Non-string values are rejected.

`pitcher_pitch_mix_json` remains an opaque, canonical string in version 1; it
is not reparsed into a validation/test-informed pitch vocabulary. A later
structured expansion requires a new policy version and leakage review.

The one-hot vocabulary is learned only from train. The following reserved
tokens are fixed before fitting and cannot occur as raw categories:

- `__MISSING__` for null, empty, or whitespace-only categorical values;
- `__RARE__` for train-observed categories appearing fewer than five times;
- `__UNKNOWN__` for a non-missing validation/test category absent from train.

Train categories with at least five observations receive their own columns.
Rare status is based only on train count. Validation/test counts cannot promote
a rare train category or add a category column.

### Market and lineage

Market columns are reported as a separate overlay even when their processing
type is numeric or categorical. Their presence does not enable odds selection,
EV, edge, Kelly, staking, betting eligibility, or profitability analysis.

`odds_collected_at` and `odds_as_of` are lineage timestamps, not modeled
numeric/categorical inputs. They must remain timezone-aware and at or before
the pregame cutoff. Row identity, game/player identity, `game_date`, event
start time, and feature-availability records are also lineage columns and stay
outside the transformed feature matrix.

## Missing-value policy

Only train missingness may define imputation behavior. The dry-run plan reports
for every numeric and categorical feature:

- train row, missing, and non-missing counts;
- train missing rate;
- the train median for numeric fields; and
- train category retention/rare sets for categorical fields.

Validation/test missingness must not change an imputation value, add or remove
a missing indicator, drop a feature, or alter the encoded schema. No forward
fill, backward fill, cross-split fill, full-dataset statistic, or outcome-based
imputation is allowed.

## Sealed validation and test transformation

The only fitting split is `train`. Requests to fit on `validation` or `test`
are rejected. After the train parameters and schema are frozen:

- validation and test use the train medians;
- missing categoricals map to `__MISSING__`;
- train-rare categoricals map to `__RARE__`; and
- validation/test-only categoricals map to `__UNKNOWN__`.

The planner reports validation-only and test-only category values for review,
but never adds them to the fitted vocabulary. Test diagnostics do not reopen
train fitting or validation choices.

## Forbidden target-aware preprocessing

Labels and outcomes cannot be preprocessing inputs. In particular, this policy
forbids target/mean/impact/likelihood encoding; supervised binning; label-aware
imputation; feature selection using outcomes; class-dependent scaling;
resampling before the temporal seal; category merging based on HR rate; and
using validation/test loss, labels, prevalence, or distributions to refit any
transform.

Raw label tables may remain available to a separately approved evaluator, but
the preprocessing planner reads only the feature-pack values and lineage. A
label/outcome column in `feature_names` or `feature_values` is rejected by the
feature firewall before preprocessing summaries are computed.

## Temporal split-plan artifact contract

When a split-plan path is supplied, it must be JSON with schema version
`mlb-hr-research-temporal-split-plan-v1`. It records:

- `mode: historical_research`;
- the exact feature-pack SHA-256;
- the staged `pack_dir` identity;
- `split_method: whole_unique_game_dates_60_20_20`;
- `readiness_verdict: READY_FOR_RESEARCH_BACKTEST`;
- `approval_status: not_approved`;
- every execution/wagering gate explicitly false; and
- explicit `game_dates` arrays under `train`, `validation`, and `test`.

The planner validates that hash, all gates, strict date order, date uniqueness,
and minimum date counts. It does not create this artifact.

## Artifact and versioning expectations

The dry-run CLI writes nothing and has no output-path option. Its plan schema is
`mlb-hr-preprocessing-plan-v1`, and `artifacts_written` remains false.

The fitted-parameter artifact schema is
`mlb-hr-fitted-preprocessing-artifact-v1`. The writer produces one immutable
JSON file named `mlb_hr_fitted_preprocessing.json` only in an explicit, empty,
non-operational research staging directory. The artifact records:

- preprocessing artifact, policy, plan, and code versions;
- the exact feature-pack file SHA-256;
- the exact temporal split-plan file SHA-256, or a canonical hash of a split
  safely derived from a staged pack;
- train date bounds and row count;
- numeric train medians and per-column missing indicators;
- train-only categorical vocabularies, rare-category-to-`__RARE__` mappings,
  and the missing/rare/unknown category policy;
- a timezone-aware creation timestamp and a canonical content SHA-256; and
- `research_only: true`, `approval_status: not_approved`, and explicit false
  gates for training, backtesting, predictions, live fetching, betting, EV,
  Kelly, Elite, staking, and production use/approval.

The loader requires the exact schema, rejects missing and unknown fields,
verifies the content hash, reruns the sealed preprocessing planner, and compares
the artifact to the exact feature pack and split source supplied by the caller.
It exposes fitted parameters only; it does not transform data or expose model,
prediction, evaluation, or wagering behavior.

Any change to feature typing, missing rules, category normalization, rare
threshold, reserved tokens, output ordering, or fitted parameters requires a
new immutable artifact. A semantic rule change requires a new policy version.
Artifacts must never overwrite `outputs/`, `history/`, `runtime/`,
`manual-data/`, caches, dashboards, or production model locations.

## Fitted-parameter writer CLI

Using a versioned split plan:

```powershell
python scripts\mlb_write_hr_fitted_preprocessing.py `
  --feature-pack C:\courtvision_staging\mlb_hr\mlb_hr_research_feature_pack.json `
  --temporal-split-plan C:\courtvision_staging\mlb_hr\temporal_split_plan.json `
  --output-staging-dir C:\courtvision_staging\mlb_hr\fitted_preprocessing_v1
```

The mutually exclusive `--staged-pack` option may replace
`--temporal-split-plan`; the resulting deterministic split is hashed in its
canonical form. The output directory must be explicitly supplied, empty (or
not yet exist beneath an existing parent), and outside all operational paths.

## Dry-run CLI

Use either a versioned split plan:

```powershell
python scripts\mlb_dry_run_hr_preprocessing.py `
  --feature-pack C:\courtvision_staging\mlb_hr\mlb_hr_research_feature_pack.json `
  --temporal-split-plan C:\courtvision_staging\mlb_hr\temporal_split_plan.json
```

or reproduce the split from a staged pack:

```powershell
python scripts\mlb_dry_run_hr_preprocessing.py `
  --feature-pack C:\courtvision_staging\mlb_hr\mlb_hr_research_feature_pack.json `
  --staged-pack C:\courtvision_staging\mlb_hr\candidate_pack
```

Exit code `0` means the read-only preprocessing plan passed. Exit code `2`
means a gate refused it. Neither result means a model, prediction, or backtest
was executed or approved.

## Remaining blockers before an actual research backtest

This step and the subsequent read-only window-readiness validator close the
fitted preprocessing and per-window statistical-power gate gaps. An actual MLB
HR research backtest remains prohibited until separately reviewed work
provides:

1. a sealed label-access protocol and a runner that prevents validation/test
   refitting and same-day learning;
2. predeclared baselines, metrics, calibration assessment, uncertainty
   intervals, and multiple-comparison controls;
3. immutable prediction/evaluation contracts and an approved research-only
   artifact location; and
4. independent provenance, leakage, preprocessing, power, and methodology
   review on the real-data artifact set.

None of those steps would by itself approve live fetching, production use,
betting, EV, Kelly, Elite selection, staking, or bankroll integration.
