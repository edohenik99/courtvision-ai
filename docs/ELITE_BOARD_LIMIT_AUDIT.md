# Elite Board Limit Audit

Date: 2026-05-20

## Scope

This note characterizes current elite board size behavior only. It does not recommend changing runtime behavior in this phase.

## Observed Current Behavior

The package-owned prediction pipeline builds the elite board inside `PredictionPipeline.run` through the nested `select_elite_board` callable.

Current ordering in that path:

1. Sort candidates by `selection_score`.
2. Apply elite market policy and admission gates.
3. Apply team and game concentration caps.
4. Apply final board truncation with `capped_df.head(elite_size)`.

When the pipeline config object does not expose `elite_size`, the final package-pipeline elite board currently falls back to 10 rows.

## Source Of 10

`courtvision/config/__init__.py` defines `DEFAULT_ELITE_BOARD_SIZE = 10`, and `courtvision/pipeline/predict_pipeline.py` uses it for the missing-override fallback:

```python
elite_size = self.config.elite_size if hasattr(self.config, 'elite_size') else DEFAULT_ELITE_BOARD_SIZE
selected_df = capped_df.head(elite_size).copy()
```

`PredictionConfig` does not define an `elite_size` field, so a plain `PredictionConfig` reaches the `else 10` fallback. Tests can still supply an external config proxy with `elite_size`, and that explicit override controls the final `head(...)` size.

No environment variable or `.env.example` setting was found for `elite_size` or a package-pipeline board limit override.

## Source Of 20

`courtvision/config/__init__.py` defines `EliteThresholds.board_limit = 20` and exposes that value as `MAX_ELITE_BOARD_LIMIT`.

That value is consumed by the broader `CourtVisionAI` path as `ELITE_BOARD_LIMIT = EliteThresholds.default().board_limit`, including late context-safety/backfill and board-construction diagnostics. The package-owned nested `select_elite_board` does not currently use `EliteThresholds.board_limit` for its final `head(...)` fallback.

The architecture audit previously described the elite board limit as `EliteThresholds.default().board_limit = 20`. That is accurate for `EliteThresholds` and the `CourtVisionAI.ELITE_BOARD_LIMIT` constant, but not for the default final size of the package-owned nested selector when `config.elite_size` is absent.

## Intentionality

This audit does not confirm whether the 10-row package-pipeline fallback or the 20-row `EliteThresholds.board_limit` value is the intended canonical behavior. Treat the mismatch as unresolved until an operator-facing decision is made.

## Risk Of Changing It

Changing the package-pipeline default from 10 to 20 could materially increase the stake-facing elite board size after caps. Even if Kelly and later guards still apply, the change may alter:

- Number of rows emitted on the elite board.
- Candidate exposure after late context/backfill paths.
- Operator review workload.
- Kelly input surface and daily exposure distribution.
- Historical comparability of board-size diagnostics.

Changing `EliteThresholds.board_limit` downward to 10 could affect the broader `CourtVisionAI` path, including context safety/backfill and diagnostics that currently use `ELITE_BOARD_LIMIT`.

## Recommended Future Fix Path

1. Decide the canonical elite board limit with bankroll/operator intent: 10, 20, or another value.
2. Add one named config field for that canonical value.
3. Route both the package-owned selector and `CourtVisionAI.ELITE_BOARD_LIMIT` through the same source.
4. Keep caps and final truncation ordering unchanged unless explicitly approved.
5. Update docs and golden tests in the same change so historical behavior remains traceable.
