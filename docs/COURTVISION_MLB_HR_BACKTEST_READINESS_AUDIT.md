# CourtVision MLB HR Historical Backtest Readiness Audit

Status: research-only, local-file-only, read-only, default-deny. This audit is
a prerequisite check. It does not train a model, run a backtest, approve a
dataset, enable betting, calculate EV or Kelly sizing, or promote MLB into any
production path.

## Run the audit

From the repository root, point the CLI at an existing staged historical input
pack:

```powershell
python scripts\mlb_audit_hr_backtest_readiness.py `
  C:\courtvision_staging\mlb_hr\candidate_pack
```

Use `--format json` to print JSON to stdout. There is deliberately no output
path option. The command reads the fixed pack manifest and six CSV files in
place, does not fetch data, and does not create or update reports, caches,
manual data, outputs, history, or runtime files.

The process exits with code `2` for `NOT_READY` and `0` for either ready
verdict. A zero exit code is not approval to start model execution; callers
must inspect the verdict.

## What is audited

The gate first runs the immutable historical-input-pack preflight, including
manifest hashes, row counts, real-source classification, dates, exact
game/player/team/venue joins, pregame odds timing, and source alignment. It
then reports:

- rows in each source and distinct labeled player-games;
- unique games, players, game dates, date range, and inclusive calendar span;
- missing values by source/column, with a separate critical-missing count;
- repeated `(game_id, game_date, batter_id)` label rows;
- explicit HR-positive, HR-negative, missing, and invalid labels;
- labeled player-games having a matching pregame odds row;
- games having complete weather and ballpark context;
- team/opponent disagreements across game, Statcast, label, and odds rows;
- suspicious target, result, postgame, model-output, EV, Kelly, and similar
  columns that could leak outcomes into research features;
- sample, fixture, mock, test, synthetic, dummy, fake, example, and placeholder
  identities or provenance.

Statcast outcome/batted-ball fields can legitimately be empty on pitches where
they do not apply. Those values are still counted in the general missing-value
inventory but are not critical. Join identities, explicit labels, completed
game context, weather, ballpark, and odds contract fields are critical.

## Verdict rules

### `NOT_READY`

Any one of these conditions is enough to fail closed:

- historical input pack preflight or CSV reading fails;
- a critical field value or explicit `is_home_run` label is missing/invalid;
- a player-game label row is duplicated;
- team/opponent context is inconsistent;
- a possible leakage column is present;
- sample/synthetic identity or provenance is detected;
- the review floor is not met: two labeled player-games, one game, two players,
  one date, at least one HR-positive label, and at least one HR-negative label.

### `READY_FOR_REVIEW`

The pack passes every blocking integrity/alignment check and the review floor,
but at least one research-backtest threshold below is not met. This verdict is
for human data review and expansion only.

### `READY_FOR_RESEARCH_BACKTEST`

The pack passes every blocking check and all of these minimums:

| Measurement | Minimum |
|---|---:|
| Distinct labeled player-games | 1,000 |
| Unique games | 100 |
| Unique players | 100 |
| Unique game dates | 30 |
| Inclusive calendar span | 30 days |
| HR-positive labels | 50 |
| HR-negative labels | 500 |
| Player-game odds coverage | 80% |
| Complete weather game coverage | 95% |
| Complete ballpark game coverage | 95% |

Labels must still have 100% explicit coverage, duplicates/leakage/sample
findings must remain zero, and all exact pack-alignment rules must pass. These
are minimum research floors, not claims of statistical power or model quality.

Every report remains:

```text
approval_status: not_approved
eligible_for_betting: false
kelly_eligible: false
model_training_enabled: false
backtesting_enabled: false
```

## What must happen before the first real backtest

1. Stage independently collected, truthfully classified historical source
   files with verified Retrosheet-to-MLBAM game/player mappings.
2. Cover enough seasons or date windows to meet the quantitative thresholds;
   the repository's tiny packs are contract fixtures only.
3. Supply timestamped pregame HR odds, complete game weather, and versioned
   ballpark factors at the required coverage rates.
4. Resolve every audit finding at its source and rebuild the immutable
   manifest. Do not repair or impute values inside the audit.
5. Obtain `READY_FOR_RESEARCH_BACKTEST` and complete a human provenance,
   leakage, and methodology review.
6. Separately implement and approve a temporally ordered train/validation/test
   split and research backtest runner. This audit intentionally does not enable
   either capability.

Even after a first research backtest, MLB betting eligibility, EV, Kelly,
Elite selection, production approval, and runtime integration remain out of
scope and disabled.
