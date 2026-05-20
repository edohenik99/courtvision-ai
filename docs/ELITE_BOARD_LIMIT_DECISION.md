# Elite Board Limit Decision

Date: 2026-05-20

## Decision

Recommended canonical policy: make 10 the explicit default elite board size, and reserve 20 as a hard maximum cap only.

This is a recommendation only. Runtime behavior remains unchanged in this phase.

## Current Behavior

The package-owned selector in `courtvision/pipeline/predict_pipeline.py` applies elite truncation after team/game concentration caps:

```python
elite_size = self.config.elite_size if hasattr(self.config, 'elite_size') else 10
selected_df = capped_df.head(elite_size).copy()
```

`PredictionConfig` does not define `elite_size`, so normal package-pipeline usage currently defaults the final elite board to 10 rows. Separately, `EliteThresholds.board_limit` is 20, and `courtvision_ai.py` exposes that value as `ELITE_BOARD_LIMIT`.

## Evidence Used

- `docs/ELITE_BOARD_LIMIT_AUDIT.md` confirms the unresolved mismatch between the 10-row selector fallback and the 20-row `EliteThresholds.board_limit`.
- `docs/CANDIDATE_FUNNEL_MAP.md` marks the elite board as stake-facing and shows that Kelly reads the emitted elite board CSV.
- `scripts/run_kelly_stakes.py` reads every elite-board row, builds stake rows, and only then applies Kelly eligibility, dampeners, and the daily exposure cap.
- `outputs/runtime/operator/quality_history.csv` is present. It contains 21 recorded runs: 11 runs with 0 elite rows, 4 with 1, 2 with 2, 1 with 5, and 3 with 10. No recorded run in that file has more than 10 elite rows.
- `data/history/pick_history.csv` is present. It contains 167 historical pick rows with mixed historical coverage; some dates have more than 10 rows, so it should not be used as proof that a 20-row current elite board is safe.

## Risk Of Increasing To 20

Moving the default final elite size from 10 to 20 would widen the stake-facing input surface. The daily Kelly exposure cap limits total stake size, but it does not make the change behavior-neutral. A larger board can still:

- Add newly eligible picks that were previously below the final board cut.
- Change the relative scaling of eligible Kelly stakes when the daily cap binds.
- Increase operator review and manual hold surface.
- Alter context-safety/backfill behavior and final board composition.
- Break comparability with recent `quality_history.csv` runs, which have not observed more than 10 elite rows.

## Recommended Canonical Policy

Use two concepts:

- Default final elite board size: 10.
- Maximum allowed elite board size: 20.

The default should stay at 10 because it matches current package-pipeline behavior and recent operator history. The 20 value should be treated as an upper safety bound for explicit overrides or future controlled expansion, not as the default final board size.

This keeps the conservative bankroll-facing behavior while preserving room for an approved, measured increase later.

## When To Revisit

Revisit the default only after there is evidence that rows ranked 11-20 improve operator outcomes without creating unacceptable exposure or review burden.

Minimum revisit evidence:

- A shadow/backtest report comparing top-10 versus top-20 boards over enough slates.
- Separate ROI, hit rate, Kelly eligibility, skipped-reason, and exposure summaries for ranks 1-10 and 11-20.
- Confirmation that added rows do not mostly come from lower-confidence, context-fragile, or highly correlated game/team clusters.
- Operator approval for the added review workload and stake-surface expansion.

## Future Implementation Plan

If approved later, implement in a behavior-preserving first step:

1. Add explicit named fields for `elite_default_size = 10` and `elite_max_size = 20`.
2. Route `PredictionConfig` through the explicit default instead of relying on the current `hasattr(..., "elite_size") else 10` fallback.
3. Clamp explicit `elite_size` overrides to the hard max of 20.
4. Keep team/game caps before final board truncation.
5. Keep reason strings, eligibility, scoring, Kelly, and thresholds unchanged.
6. Add tests proving default 10, explicit override, max clamp, and cap-before-truncation ordering.
7. Only in a separate approved phase, consider changing the default above 10 with shadow evidence attached.

