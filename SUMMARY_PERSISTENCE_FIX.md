# Summary Persistence Fix - Implementation Note

## Root Cause
The `elite_pipeline_audit_summary_YYYY-MM-DD.json` file had an empty `summary: {}` because:
1. The `_build_summary()` method was called **after** `elite_telemetry.write_summary_json()`
2. The telemetry writer only persisted `slate_date`, `totals`, and `rows` - but not the `summary` object containing board analytics

## Files Changed

### 1. `courtvision/runtime_audit.py`
**Changes:**
- Added `__init__()` with `self.summary: dict[str, Any] = {}` initialization
- Added `set_summary()` method to store summary before writing
- Updated `write_summary_json()` to include `"summary": self.summary` in payload

**Lines modified:** 1197-1228

### 2. `courtvision/pipeline/predict_pipeline.py`
**Changes:**
- Reordered execution: moved `_build_summary()` call **before** telemetry writing
- Added `elite_telemetry.set_summary(result.summary)` before writing audit files
- Telemetry writing now happens after summary is populated

**Lines modified:** 447-500

### 3. `tests/test_live_gate_regression.py`
**Changes:**
- Added `TestSummaryPersistence` class with 3 regression tests:
  - `test_summary_not_empty_when_elite_populated`: Verifies summary exists when elite board has rows
  - `test_board_analytics_fields_present`: Verifies all expected fields in summary
  - `test_elite_telemetry_set_summary`: Verifies EliteAudit accepts and stores summary

**Tests added:** Lines 437-530

## Fields Now Persisted
The summary JSON now contains:

```json
{
  "slate_date": "2026-04-21",
  "totals": {...},
  "rows": [...],
  "summary": {
    "board_analytics": {
      "elite_count": 10,
      "overs_count": 6,
      "unders_count": 4,
      "avg_edge": 2.5,
      "avg_abs_edge": 3.2,
      "max_team_exposure": 3,
      "max_game_exposure": 4,
      "unique_teams": 5,
      "unique_games": 3
    },
    "elite_overs_count": 6,
    "elite_unders_count": 4,
    "elite_avg_edge": 2.5,
    "elite_avg_abs_edge": 3.2,
    "elite_max_team_exposure": 3,
    "elite_max_game_exposure": 4,
    "elite_unique_teams": 5,
    "elite_unique_games": 3
  }
}
```

## Backward Compatibility
- All existing fields (`slate_date`, `totals`, `rows`) preserved
- New `summary` field is additive only
- Empty summary handled gracefully (won't crash if not set)

## Verification
Run regression tests:
```bash
python tests/test_live_gate_regression.py
```

Expected: All 11 tests pass (8 original + 3 new summary persistence tests)

## Fix Validation Checklist
- [x] Summary not empty when elite board populated
- [x] Board analytics fields present in summary
- [x] EliteAudit.set_summary() works correctly
- [x] JSON output includes summary field
- [x] Backward compatibility maintained
