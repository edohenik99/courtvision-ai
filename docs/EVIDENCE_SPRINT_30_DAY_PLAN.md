# CourtVision 30-Day Evidence Sprint Plan

## Purpose

The sprint is a prospective, frozen, 30-calendar-day NBA paper trial of the current canonical operator path. Its purpose is to create an auditable record of what the system recommended at the time, at the available price, before outcomes were known.

This is evidence collection, not a live-bankroll authorization. Paper stakes are analytical outputs only. The trial must include every scheduled day, including no-slate, no-pick, provider-failure, and incomplete-run days.

## Day 0: preregistration and freeze

Before the first eligible slate:

1. Record the start and end dates, timezone, canonical command, branch, clean/dirty git status, and frozen `code_sha`.
2. Record the resolved non-secret operator configuration and compute `config_hash` as defined below.
3. Declare the source book or deterministic book-selection rule, observation cutoff, closing-line source, and closing cutoff.
4. Declare included markets and the treatment of pushes, voids, postponed games, duplicated recommendations, and missing closing lines.
5. Archive the Day 0 manifest in an append-only location before the first prediction is observed.

The trial clock is 30 consecutive calendar days, not 30 hand-selected slates. A day with no games or no recommendations still counts and must have a daily run manifest.

## Frozen-run rule

Thresholds, model rules, scoring formulas, selection gates, market eligibility, provider-selection rules, Kelly rules, bankroll assumptions, and behavior-affecting configuration must not change during the trial.

If any such item changes, intentionally or accidentally, the trial is stopped and restarted at Day 1 with a new `code_sha`, `config_hash`, and preregistration. Results from the interrupted segment may be retained and disclosed as a separate, incomplete cohort but must not be pooled into the restarted primary result.

Emergency operational fixes that cannot affect outputs may be considered only after a documented diff review. If output equivalence cannot be demonstrated, restart. Do not loosen a gate or threshold to increase volume or make a metric pass.

## Reproducibility identifiers

### `code_sha`

Use the full 40-character commit returned by `git rev-parse HEAD`. The sprint should begin from a clean tree. If the run includes uncommitted code or configuration, the committed SHA alone is insufficient and the run is not part of the primary frozen cohort.

### `config_hash`

Compute a SHA-256 over a canonical, UTF-8 JSON object with sorted keys and compact separators. The object must contain:

- resolved behavior-affecting CLI and environment settings, including explicit values for defaults and unset flags;
- SHA-256 hashes of external behavior-bearing inputs not represented by `code_sha`, including baseline/model artifacts and any manual context file used by the run;
- the declared source-book rule, line-capture cutoff, closing source, timezone, and settlement policy.

Never place secret values in the object. Represent credentials only by the canonical variable name and a state such as `configured` or `missing`. Provider identity is captured separately in `provider_used`. Store the unhashed, non-secret canonical JSON beside the Day 0 manifest so the hash can be independently reproduced.

## Daily procedure

1. Before the declared cutoff, run the canonical `run_today.bat -> run_today.ps1 -> courtvision_ai.py` workflow once for the current date.
2. Preserve the run, validation, and grading logs; operator card; completion-state audit; artifact manifest; source board; and Kelly artifact when applicable.
3. Append one evidence row per released recommendation. Never rewrite the prediction-side fields after release.
4. If no recommendation is released, preserve a zero-row daily pick file plus a daily run manifest. If the run fails, preserve the failure status and reason rather than silently omitting the day.
5. At the declared close, capture the closing line and price from the preregistered source.
6. After official settlement, append only the outcome-side fields: `closing_line`, `result`, `profit_1u`, and settlement notes.
7. Reconcile the evidence rows to the dated source artifacts and record any discrepancy. Corrections must be additive, timestamped, and reasoned; the original record remains available.

Use ISO `YYYY-MM-DD` dates in the declared timezone. Timestamps, while not part of the minimum row schema, should use ISO 8601 with timezone offsets in the daily manifest and custody log.

## Required daily recommendation fields

Every released recommendation row requires the following columns in this exact order:

```text
date, code_sha, config_hash, provider_used, market, player, line, odds, prediction, edge, confidence, kelly_eligible, stake_recommendation, closing_line, result, profit_1u, notes
```

Field contract:

| Field | Required meaning |
|---|---|
| `date` | Slate date as `YYYY-MM-DD` in the declared timezone. |
| `code_sha` | Full frozen git commit SHA. |
| `config_hash` | Frozen SHA-256 configuration/input-manifest hash. |
| `provider_used` | Actual provider provenance for the released quote/data, not merely the configured priority. Mixed or fallback use must be explicit. |
| `market` | Canonical market identifier from the source artifact. |
| `player` | Canonical player name or stable identifier used by the source artifact. |
| `line` | Released sportsbook line as a numeric value. |
| `odds` | Released American price as a signed integer. |
| `prediction` | Frozen model prediction used for the decision. |
| `edge` | Frozen edge value with one declared unit and formula for the entire trial. |
| `confidence` | Frozen confidence value on a declared 0–1 scale. |
| `kelly_eligible` | `true` or `false` as emitted by the approved path. Do not infer it later. |
| `stake_recommendation` | Paper stake emitted at release, including its unit (for example, bankroll units or currency). Zero is valid. |
| `closing_line` | Closing quote encoded as `<line>@<american_odds>`, for example `25.5@-115`; use `missing` only with a reason in `notes`. |
| `result` | One of `win`, `loss`, `push`, `void`, or `pending`, under the preregistered settlement rule. |
| `profit_1u` | Net flat-stake profit for one unit risked at the released `odds`; pending rows remain blank. |
| `notes` | Structured exception context such as fallback, stale/missing close, postponement, correction reference, or manual intervention. Use an empty string when none applies. |

Prediction-side fields are frozen at release: `date` through `stake_recommendation`. Outcome-side completion must not alter them.

For `profit_1u`, a win returns `odds / 100` when odds are positive and `100 / abs(odds)` when odds are negative; a loss is `-1`; a push or void is `0`. This convention makes the primary ROI price-aware while keeping risk fixed at one unit.

## Daily run manifest

Recommendation rows alone cannot distinguish a legitimate no-pick day from a missing or failed run. Each calendar day therefore also needs a small manifest containing at least:

- date and run timestamp;
- `code_sha` and `config_hash`;
- run status: `complete`, `no_slate`, `no_picks`, `provider_failure`, or `failed_other`;
- provider attempted and `provider_used`, including fallback status;
- released row count;
- paths and SHA-256 hashes for the source board, operator card, completion-state audit, artifact manifest, and logs;
- any failure reason or manual intervention.

## Success metrics

Report every metric for the full preregistered cohort and, secondarily, by market and Kelly eligibility. Always show sample size and missing-data count. Do not relabel exploratory slices as primary results.

### Price-weighted ROI

Primary ROI is odds-aware flat-1u ROI:

```text
price_weighted_roi = sum(profit_1u for settled non-void rows)
                     / count(settled non-void rows)
```

Report as a percentage and units won/lost. Pushes remain in the denominator because one unit was risked and returned; voids do not. Stake-weighted paper ROI may be shown separately as exploratory and must not replace the primary metric.

### Closing-line value (CLV)

Calculate CLV from the released `line@price` against the recorded closing `line@price`, in the direction of the recommendation. Report:

- mean and median directional line movement;
- the percentage of rows beating, matching, and losing to the close;
- price CLV in implied-probability points when line values match and both prices are available;
- missing-close count and rate.

Do not merge unlike line movements and price movements into a single undocumented number.

### Hit rate

```text
hit_rate = wins / (wins + losses)
```

Pushes and voids are excluded. Report the binomial confidence interval and the break-even hit rate implied by the recorded prices; raw hit rate alone is not evidence of profitability.

### Drawdown

Build the chronological cumulative `profit_1u` series and report maximum peak-to-trough decline in units, plus the start date, trough date, and recovery status. Use release order with a documented deterministic tie-breaker for same-day picks.

### Calibration

Treat `confidence` as a probability only if that is its declared runtime meaning. Report reliability bins with counts, predicted mean versus observed win rate, Brier score, and expected calibration error. Exclude pushes and voids and disclose missing/invalid confidence values. If confidence is a rank score rather than a probability, report rank discrimination instead and do not call it probability calibration.

### Void rate

```text
void_rate = void rows / all released recommendation rows
```

Report pushes separately. Do not remove voids from the evidence ledger.

### Provider failure rate

```text
provider_failure_rate = daily manifests marked provider_failure
                        / scheduled trial days with an eligible NBA slate
```

Also report fallback-use rate and partial-provider-use rate. A provider failure day remains in the calendar cohort even when it produces no recommendation rows.

## End-of-sprint report

Freeze the evidence ledger after all settleable rows are settled. Publish the preregistration, all daily manifests, row-level evidence, custody/correction log, definitions, primary metrics, confidence intervals, missingness, provider failures, voids, and interrupted days. Clearly separate prospective results from backtests and from any post hoc analysis.

A completed sprint is evidence, not automatic promotion. Any decision to change thresholds, activate another model path, wager real funds, or promote another sport requires separate review and approval.
