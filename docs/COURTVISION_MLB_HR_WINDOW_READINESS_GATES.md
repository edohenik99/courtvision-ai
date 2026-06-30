# CourtVision MLB HR Per-Window Readiness Gates

Status: research-only, local-file-only, read-only, default-deny.

These gates feed the sealed backtest execution-plan shell. Neither the gates
nor that shell train a model, transform rows, make predictions, execute a
backtest, fetch data, or grant production or wagering approval.

## Bound inputs

The validator accepts exactly three existing artifacts:

- an MLB HR feature-pack JSON;
- its temporal split-plan JSON; and
- its fitted train-only preprocessing artifact JSON.

Before metrics are calculated, the feature pack must pass the timestamp-aware
feature firewall, the temporal split must pass strict ordering and 60/20/20
whole-date validation, and the fitted preprocessing artifact must match the
exact feature-pack and split-plan SHA-256 hashes. Any prerequisite failure
fails closed without a window verdict.

`is_home_run` is an explicit boolean target carried on each artifact row. It
remains outside `feature_names` and `feature_values`, so the feature firewall
continues to reject it as a model input.

## Measurement rules

Player-game evidence is deduplicated by `(game_date, game_id, player_id)`.
Multiple sportsbook rows never add statistical power. Their label must agree,
and odds coverage is credited when at least one row has a complete, fresh,
pregame market snapshot.

Weather coverage requires temperature, wind speed, wind direction,
wind-to-field direction, humidity, and roof status. Ballpark coverage requires
overall HR factor, left/right-handed HR factors, and altitude. Date span is the
inclusive observed span from the first to last player-game date in a window.

## Review floor

Every window uses this lower floor:

| Metric | Minimum |
|---|---:|
| Player-game rows | 20 |
| Unique games | 5 |
| Unique players | 10 |
| HR-positive labels | 1 |
| HR-negative labels | 10 |
| Odds coverage | 50% |
| Weather coverage | 80% |
| Ballpark coverage | 80% |
| Inclusive date span | 3 days |

Falling below any review floor returns `WINDOW_NOT_READY`.

## Research-backtest floors

| Metric | Train | Validation | Test |
|---|---:|---:|---:|
| Player-game rows | 2,000 | 1,000 | 1,000 |
| Unique games | 200 | 100 | 100 |
| Unique players | 200 | 100 | 100 |
| HR-positive labels | 100 | 50 | 50 |
| HR-negative labels | 1,000 | 500 | 500 |
| Odds coverage | 80% | 80% | 80% |
| Weather coverage | 95% | 95% | 95% |
| Ballpark coverage | 95% | 95% | 95% |
| Inclusive date span | 90 days | 30 days | 30 days |

The validation/test row floor gives an approximate 95% margin of error of
1.35 percentage points for a 5% HR event rate. Fifty positive labels also
prevents a nominally large but nearly single-class evaluation window. These
are minimum research floors, not a claim that a particular model, subgroup,
metric, or strategy has adequate power.

Passing the review floor but missing any research floor returns
`WINDOW_READY_FOR_REVIEW`. Passing every research floor returns
`WINDOW_READY_FOR_RESEARCH_BACKTEST`. The overall verdict is the least-ready
of train, validation, and test.

## Read-only CLI

```powershell
python scripts\mlb_validate_hr_window_readiness.py `
  --feature-pack C:\courtvision_staging\mlb_hr\mlb_hr_research_feature_pack.json `
  --temporal-split-plan C:\courtvision_staging\mlb_hr\temporal_split_plan.json `
  --fitted-preprocessing-artifact C:\courtvision_staging\mlb_hr\mlb_hr_fitted_preprocessing.json
```

The command has no output option and writes nothing. Exit code `0` requires
all three windows to be `WINDOW_READY_FOR_RESEARCH_BACKTEST`; every review,
not-ready, firewall, ordering, or hash failure exits with code `2`.

Every report keeps model training, backtesting, prediction, fetching, EV,
Kelly, Elite, staking, betting eligibility, and production approval disabled.

## Relationship to the sealed runner shell

The read-only shell and its evaluation-label boundary are documented in
`COURTVISION_MLB_HR_SEALED_BACKTEST_RUNNER_CONTRACT.md`. A window verdict is
only prerequisite evidence; the shell still refuses unless all three windows
reach `WINDOW_READY_FOR_RESEARCH_BACKTEST` and every other bound-input gate
passes.

## Remaining blockers before actual research execution

- Approve a model specification and train-label handoff contract.
- Implement transform-only application of the fitted preprocessing artifact
  and sealed prediction/label opening without refitting or target-aware
  decisions.
- Freeze uncertainty intervals, baselines, segment rules, missing-prediction
  handling, and multiple-comparison controls.
- Implement immutable prediction/evaluation artifacts in isolated research
  staging; the current shell writes nothing.
- Complete independent provenance, leakage, preprocessing, power, and
  methodology review on the actual real-data artifact set.
- Keep all MLB output research-only; this work grants no production, betting,
  EV, Kelly, Elite, staking, or bankroll eligibility.
