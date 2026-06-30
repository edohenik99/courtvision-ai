# CourtVision MLB HR Research Temporal Backtest Design

Status: specification and split-planning dry run only. Nothing in this design
enables model training, predictions, backtest execution, betting eligibility,
EV, Kelly sizing, Elite selection, staking, production approval, live data
fetching, or writes to outputs/history/runtime/manual-data.

## Entry gate

The only admissible input is an immutable local staged pack. A future research
backtest must run the historical input-pack preflight and the backtest readiness
audit against the same pack bytes. Split planning fails closed unless:

- input-pack preflight passes;
- the exact readiness verdict is `READY_FOR_RESEARCH_BACKTEST`;
- possible leakage columns are empty; and
- any supplied feature pack passes the timestamp-aware feature firewall; and
- all approval and execution fields remain disabled and `not_approved`.

`READY_FOR_REVIEW` and `NOT_READY` are rejections. A readiness verdict is a
research data gate, not approval of methodology, a model, a wager, or a
production path.

## Train, validation, and test split

Use the sorted distinct `game_date` values from `retrosheet_games.csv`. Assign
whole dates, never rows, in this fixed order:

| Split | Allocation | Minimum unique game dates |
|---|---:|---:|
| Train | first 60% | 18 |
| Validation | next 20% | 6 |
| Test | final 20% | 6 |

Integer allocation uses `floor(60%)` for train, `floor(20%)` for validation,
and assigns the remainder to test. At the 30-date readiness floor this is
exactly 18/6/6. Calendar gaps may exist, but the observed dates assigned to
each window are contiguous in sorted-date order.

The ordering invariants are:

```text
max(train.game_date) < min(validation.game_date)
max(validation.game_date) < min(test.game_date)
```

Each `game_date` belongs to exactly one split. Doubleheaders and all games,
players, books, and rows on a date stay together. No same-day training or
model update may use an earlier game's label to score a later game that day.
Player and team identities may recur across windows; rows and dates may not.

Validation is used only for research choices fixed before test evaluation.
The test window stays sealed until feature definitions, model family,
hyperparameters, probability calibration plan, and evaluation metrics are
frozen. Test results cannot be recycled into another selection round while
still being described as the held-out test.

## Feature freeze and availability

`courtvision.sports.mlb.training.hr_feature_allowlist` is the executable,
default-deny specification. Only fields in the following three allowed classes
may appear in `feature_names`. The other two classes are retained for explicit
classification and are always rejected as features.

### Allowed pregame features

`batter_hand`, `pitcher_hand`, `platoon_side`,
`primary_pitch_matchup_score`, `weather_temperature`, `weather_wind_speed`,
`weather_wind_direction`, `weather_wind_out_to_field`, `weather_humidity`,
`roof_status`, `park_factor_hr`, `park_factor_lhb`, `park_factor_rhb`, and
`altitude`.

### Allowed historical rolling features

`hitter_pa_window`, `hitter_recent_hr_rate`, `hitter_recent_barrel_rate`,
`hitter_recent_hard_hit_rate`, `hitter_recent_fly_ball_rate`,
`hitter_recent_pull_rate`, `hitter_avg_exit_velocity`,
`hitter_max_exit_velocity`, `hitter_season_hr_rate_to_date`,
`hitter_season_barrel_rate_to_date`,
`hitter_season_hard_hit_rate_to_date`, `pitcher_batters_faced_window`,
`pitcher_hr_allowed_rate_to_date`, `pitcher_barrel_allowed_rate_to_date`,
`pitcher_hard_hit_allowed_rate_to_date`,
`pitcher_fly_ball_allowed_rate_to_date`, and `pitcher_pitch_mix_json`.

### Allowed market features

`sportsbook`, `odds_provider`, `hr_market_available`, `american_odds`,
`decimal_odds`, `implied_probability`, `odds_collected_at`, `odds_as_of`, and
`odds_is_fresh_for_pregame`.

### Labels/outcomes: retained for evaluation, rejected as features

`hit_hr_today`, `home_run_count`, `plate_appearances`, `game_completed`,
`label_source`, `label_available`, `label_as_of`, `is_home_run`, `label`,
`target`, `outcome`, `result`, `event_type`, `event_text`, `events`,
`description`, and `rbi`.

### Forbidden leakage fields

The forbidden class includes same-game/postgame Statcast measurements such as
`launch_speed`, `launch_angle`, `hit_distance_sc`, `bb_type`, estimated
BA/wOBA, `woba_value`, and `barrel`; final-game fields such as `home_score`,
`away_score`, `final_score`, and `game_status`; closing/post-start odds; model,
decision, settlement, grade, payout, profit, ROI, EV, Kelly, Elite, stake, and
wager fields. Names with `actual_`, `closing_`, `final_`, `future_`,
`post_game_`, `postgame_`, `result_`, `same_day_`, `same_game_`, `settled_`, or
`settlement_` prefixes, or `_grade`, `_payout`, `_profit`, `_result`, `_roi`, or
`_settlement` suffixes, are forbidden. Label/outcome/target prefixes and
suffixes are classified as labels/outcomes. Every other unknown column is
rejected unless this specification is deliberately updated and reviewed.

Every candidate player-game requires a timezone-aware lineage timestamp for
every declared feature. The feature cutoff is the selected odds snapshot time,
`odds_collected_at`, and must be strictly earlier than `event_start_time`:

```text
feature.available_at <= odds_collected_at < event_start_time
```

Missing, naive, or incomparable timestamps fail closed. A timestamp at game
start is too late. Historical rolling features additionally require
`source_latest_game_date < game_date`; same-day outcomes are never admitted.
Pregame/static fields with an effective source date may use the target game
date but never a future date.

- Historical player, pitcher, team, and park aggregates use source events from
  dates strictly earlier than the target `game_date`. Same-day events are never
  included, even when their timestamps precede a later game.
- Static/versioned park context must have `as_of_date <= game_date`; future
  season revisions cannot be applied retroactively without a recorded version.
- Weather must be a timestamped forecast or pregame observation that existed by
  the cutoff. A postgame historical archive observation may support provenance
  review but is not a valid pregame feature merely because it describes game
  conditions accurately.
- Missing values are handled by a policy learned on train only. Validation or
  test distributions cannot define imputations, encodings, scaling, feature
  selection, or thresholds.

The in-memory `MLBHRResearchFeaturePack` records the exact feature names and
per-row availability lineage. Validation is read-only and cannot enable model
training, backtest execution, predictions, wagering fields, or approval. This
step does not build or persist a feature pack.

## Label timing

`is_home_run` is available only after the applicable game is completed and the
result source has been ingested. Labels are never features. Train labels may be
used only after every game date in the training window is complete. Validation
and test labels remain inaccessible until predictions for the corresponding
sealed window have been generated and frozen by a future approved runner.

There is no intra-day learning: a label from one game on date D cannot affect
any feature, fit, calibration, threshold, or prediction for another game on D.
Postponed or suspended games use the actual completion/result availability date
under a future explicit policy; they must not be silently treated as labels
available on the originally scheduled date.

## Odds timestamp contract

Every odds row used by a future runner must satisfy all input-pack rules and:

- timezone-aware `odds_collected_at < event_start_time`;
- snapshot age is greater than zero and no more than 24 hours;
- the snapshot existed at the feature cutoff and is associated with the exact
  game, player, team/opponent, sportsbook, and HR market;
- no closing, settled, corrected-after-start, or postgame price is substituted;
- duplicate sportsbook/player/game snapshots are rejected unless a future
  deterministic pregame selection rule is separately specified and tested.

Odds coverage remains a data-quality measurement. It does not authorize EV,
edge, Kelly, staking, wager selection, or profitability reporting.

## Leakage exclusions

The readiness audit rejects suspicious target/result/model/wager columns before
split planning. The feature firewall then applies the exact allowlist above and
excludes:

- labels and results: `is_home_run`, `label`, `target`, `outcome`, `result`,
  `actual*`, `future*`, `postgame*`, settlement, grade, payout, profit, and ROI;
- final-game information: final scores, completed-game status used as a feature,
  same-game Retrosheet `event_type`, `event_text`, and `rbi`;
- same-game Statcast outcomes and batted-ball measurements, including `events`,
  hit descriptions, launch speed/angle/distance, batted-ball type, estimated
  outcome values, wOBA values, and barrel flags;
- model or decision outputs: predicted/model probability, eligibility, edge,
  EV, Kelly values, Elite flags, stake, wager, and backtest profit; and
- closing or post-start odds fields such as `closing_line`, `closing_odds`, and
  `closing_price`.

Raw outcome tables may retain labels for later evaluation, but those columns
and current-game rows cannot enter `feature_names` or feature-value payloads.

## Minimum samples

Before split planning, the readiness audit requires at least:

| Measurement | Pack minimum |
|---|---:|
| Distinct labeled player-games | 1,000 |
| Unique games | 100 |
| Unique players | 100 |
| Unique game dates | 30 |
| Inclusive calendar span | 30 days |
| HR-positive labels | 50 |
| HR-negative labels | 500 |
| Pregame odds coverage | 80% |
| Complete weather game coverage | 95% |
| Complete ballpark game coverage | 95% |

The split planner separately enforces the 18/6/6 unique-date floors. These are
entry floors, not a statistical-power finding. Before actual backtesting, an
approved runner still needs per-window player-game, positive-label,
negative-label, odds, weather, and park coverage checks. Those thresholds must
be set from a documented power/uncertainty analysis and cannot be lowered to
make a pack pass.

## Sealed runner dry-run CLI

From the repository root:

```powershell
python scripts\mlb_dry_run_hr_research_backtest.py `
  --feature-pack C:\courtvision_staging\mlb_hr\mlb_hr_research_feature_pack.json `
  --temporal-split-plan C:\courtvision_staging\mlb_hr\temporal_split_plan.json `
  --fitted-preprocessing-artifact C:\courtvision_staging\mlb_hr\mlb_hr_fitted_preprocessing.json
```

The command validates the feature firewall, strict temporal split, hash-bound
fitted preprocessing artifact, every per-window readiness gate, and the
evaluation-only label boundary. It prints a non-executable plan, has no output
option, and writes nothing. Exit code `0` does not mean a backtest ran or
received approval; exit code `2` means the contract refused.

The full contract is documented in
`COURTVISION_MLB_HR_SEALED_BACKTEST_RUNNER_CONTRACT.md`.

## Blockers before an actual research backtest

The feature builder, fitted preprocessing artifact, read-only per-window power
gates, and sealed execution-plan shell now exist. The shell performs no
backtest operation. Actual research execution remains prohibited until
separately reviewed work adds:

1. an approved model specification and train-label handoff;
2. an implemented transform-only executor and sealed prediction/label opening
   sequence;
3. frozen uncertainty, baseline, segmentation, missing-prediction, and
   multiple-comparison details;
4. an immutable isolated research artifact writer; and
5. independent methodology, provenance, leakage, preprocessing, and power
   review on the real-data artifact set.

Even completion of a research backtest would not approve MLB betting, EV,
Kelly, Elite, staking, bankroll use, production integration, or live fetching.
