# Elite Board Concentration Controls and Grading Analytics

## Implementation Summary

This implementation adds elite board concentration controls and extends grading analytics for better board quality tracking.

## Files Changed

### 1. `courtvision/pipeline/predict_pipeline.py`
**Changes:**
- Added concentration caps (team/game) at final elite selection stage (lines 336-386)
- Added `_compute_board_analytics()` helper method for board-level metrics
- Extended `_build_summary()` to include board analytics in summary output
- Added trace fields for cap diagnostics:
  - `skipped_by_team_cap`
  - `skipped_by_game_cap`
  - `max_team_exposure`
  - `max_game_exposure`
  - `unique_teams`
  - `unique_games`

**Behavior:**
- Default team cap: 3 picks per team
- Default game cap: 4 picks per game
- Caps applied at final selection stage, preserving ranking order
- Skipped rows get rejection reasons: `reject_team_exposure_cap` or `reject_game_exposure_cap`
- Next-best eligible candidates fill board after cap skips

### 2. `courtvision/selection/operator_boards.py`
**Changes:**
- Live gate fix preserved (lines 193-226)
- Combined live mask: `legacy_live_mask | line_source_live_mask`
- No changes to this file - fix remains intact

### 3. `courtvision/calibration/grading_summary.py`
**Changes:**
- Added `_edge_bucket()` helper function for edge magnitude bucketing
- Added new grading buckets:
  - `by_edge_bucket`: Hit rate by edge magnitude (0.00-1.99, 2.00-3.99, 4.00-5.99, 6.00+)
  - `by_qualification_reason`: Hit rate by qualification reason
- Row processing logic added to populate new buckets

### 4. `tests/test_live_gate_regression.py` (NEW)
**Test Coverage:**
- `test_live_market_admission_via_line_source`: Live rows pass via line_source
- `test_no_missing_qualification_reason_rejection_for_healthy_rows`: No false rejections
- `test_elite_board_non_empty_with_valid_live_rows`: Board populates correctly
- `test_directional_validation_still_enforced`: Edge direction validation preserved
- `test_synthetic_lines_filtered_by_live_gate`: Synthetic lines still filtered
- `test_team_cap_enforcement`: Team cap limits picks per team
- `test_game_cap_enforcement`: Game cap limits picks per game
- `test_next_best_candidate_backfill`: Ranking preserved after cap skips

## Config/Environment Variables

### Concentration Caps
- `ELITE_TEAM_CAP`: Max picks per team (default: 3)
- `ELITE_GAME_CAP`: Max picks per game (default: 4)

Set via config attributes on PredictPipelineConfig:
```python
config = PredictPipelineConfig(
    elite_team_cap=3,
    elite_game_cap=4,
)
```

## New Analytics Fields

### Board-Level Analytics (in summary)
```python
{
    "board_analytics": {
        "elite_count": 10,
        "overs_count": 6,
        "unders_count": 4,
        "avg_edge": 2.5,
        "avg_abs_edge": 3.2,
        "max_team_exposure": 3,
        "max_game_exposure": 4,
        "unique_teams": 5,
        "unique_games": 3,
    },
    "elite_overs_count": 6,
    "elite_unders_count": 4,
    "elite_avg_edge": 2.5,
    "elite_avg_abs_edge": 3.2,
    "elite_max_team_exposure": 3,
    "elite_max_game_exposure": 4,
    "elite_unique_teams": 5,
    "elite_unique_games": 3,
}
```

### Grading Summary Extensions
New buckets in `summarize_graded_props()`:
- `by_side`: Hit rate by over/under (already existed)
- `by_edge_bucket`: Hit rate by edge magnitude buckets
- `by_qualification_reason`: Hit rate by qualification reason

## Cap Behavior

### Team Cap Example
```
Input: 4 LAL candidates ranked by selection_score
Cap: elite_team_cap=2
Output: Top 2 LAL candidates selected, remaining 2 skipped
        Next-best non-LAL candidates fill board
```

### Game Cap Example
```
Input: 5 candidates from same game
Cap: elite_game_cap=3
Output: Top 3 candidates selected, remaining 2 skipped
        Next-best candidates from other games fill board
```

## Testing

### Run Tests
```bash
python -m pytest tests/test_live_gate_regression.py -v
```

### Expected Results
- All 8 tests pass
- Live gate fix remains intact
- Concentration caps enforce limits
- Backfill preserves ranking order

## Backward Compatibility

All changes are additive:
- Existing output schemas unchanged
- New fields added to summary only
- Cap defaults are sensible (3 team, 4 game)
- If caps not hit, behavior unchanged
- No breaking changes to existing APIs

## Success Criteria

- [x] Live market rows flow via line_source
- [x] No `selection_live_gate_missing_qualification_reason` rejections
- [x] Elite board populates with valid candidates
- [x] Team cap limits picks per team
- [x] Game cap limits picks per game
- [x] Next-best candidates fill after cap skips
- [x] Board analytics computed and logged
- [x] Grading summary includes edge/qualification buckets
- [x] Directional validation preserved
- [x] All tests pass
