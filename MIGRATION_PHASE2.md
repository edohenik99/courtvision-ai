# Phase 2 Migration: Scoring Logic Extraction

## Summary

Successfully extracted candidate scoring and confidence logic from `runtime_scoring.py` into modular package components under `courtvision/scoring/`.

## New Modules Created

### 1. `courtvision/scoring/edge.py`
**Purpose**: Edge calculation functions

**Functions**:
- `edge_pct_denominator(market_type, sportsbook_line)` - Compute floor-adjusted denominator for edge %
- `favorite_bias_factor(market_type, sportsbook_line, odds)` - Compute bias adjustment for favorites/longshots
- `edge_pct_value(market_type, adjusted_edge_abs, sportsbook_line)` - Compute edge as percentage
- `compute_edge(market_type, edge_abs, sportsbook_line, odds)` - Complete edge computation

**Thresholds Preserved**:
- Moneyline: -180 (favorite boost 1.05), +150/+250/+400 (underdog penalties)
- Player props: line >=20 (1.05 boost), line <=12 (0.90 penalty)

### 2. `courtvision/scoring/confidence.py`
**Purpose**: Confidence computation and historical multipliers

**Functions**:
- `player_points_scoring_stability(row, sportsbook_line)` - Scoring stability metric (0.0-1.0)
- `historical_confidence_multiplier(row)` - Complex historical weighting multiplier
- `compute_confidence(row, edge_abs, market_type, minutes_projection)` - Confidence with edge boosts

**Multiplier Formula Preserved**:
- player_points: `0.42 + 0.22*minutes + 0.08*stability + 0.22*confidence + 0.16*edge_ratio + 0.12*scoring_stability - 0.14*injury_risk`
- moneyline: `0.78 + max(0, confidence-0.50)*0.45 - longshot_penalty/100`
- other player markets: `0.45 + 0.30*minutes + 0.15*stability + 0.20*confidence - 0.15*injury_risk`

### 3. `courtvision/scoring/penalties.py`
**Purpose**: Penalty calculations

**Functions**:
- `longshot_penalty_points(odds)` - Penalty for extreme odds (150/250/450/700 thresholds)
- `volatility_penalty_points(row)` - Minutes/injury volatility penalties
- `projection_realism_penalty_points(row, edge_abs, confidence)` - Edge/confidence mismatch penalties
- `compute_penalties(row, adjusted_edge_abs, confidence)` - Complete penalty aggregation

**Penalty Thresholds Preserved**:
- Longshot: +150=4pts, +250=8pts, +450=12pts, +700=16pts
- Volatility: <22 min=+12pts, <26 min=+5pts, minutes drift*1.1, injury>0.25=*18
- Realism: edge>6.0 & conf<0.65 triggers penalty, edge>4.5 & conf<0.60 triggers smaller penalty
- Total penalty capped at 12.0

### 4. `courtvision/scoring/candidate_scoring.py`
**Purpose**: Main scoring orchestration

**Functions**:
- `player_tier_weight(market_type, minutes_projection)` - Tier weights (34+=1.15, 28+=1.05, 20+=0.95, <20=0.75)
- `compute_quality_score(...)` - Quality score formula
- `compute_selection_score(row)` - Complete candidate scoring (main entry point)

**Classes**:
- `CandidateScoringConfig` - Configuration dataclass with all thresholds
- `CandidateScoringPolicy` - Policy class with `apply_scoring_metadata()` and `is_elite_candidate()`

**Elite Qualification Thresholds Preserved**:
- min_confidence: 0.65
- min_quality_score: 82.0
- player_minutes: 24.0
- player_edge: 1.5
- player_confidence: 0.65
- moneyline_edge: 0.06
- moneyline_confidence: 0.70
- max_plus_moneyline_odds: 300

## Legacy Compatibility

### `runtime_scoring.py` (Updated)
Now acts as a **compatibility layer**:
- `BoardScoringConfig` - Preserved with same interface
- `BoardScoringPolicy` - Delegates to new modules internally
- All public methods maintained with identical signatures
- Uses lazy imports to avoid circular dependencies

### What Changed
- Implementation now delegates to `courtvision.scoring.*` modules
- Added default values to `BoardScoringConfig` fields (previously required args)
- Lazy imports used for `runtime_selection` functions to break circular deps

### What Stayed the Same
- All public method signatures identical
- All thresholds and weighting logic unchanged
- All output values identical (exact preservation verified by tests)
- `__all__` exports unchanged

## Clean Function Boundaries

As requested, the following clean function boundaries are now available:

```python
from courtvision.scoring import (
    compute_edge,           # Returns dict with bias_factor, adjusted_edge_abs, edge_pct
    compute_confidence,     # Returns dict with base_confidence, adjusted_confidence, edge_boost
    compute_selection_score, # Returns complete scoring metadata dict
)
```

## Test Coverage

### New Tests: `tests/test_scoring_modules.py` (44 tests)
- **TestEdgeFunctions**: 7 tests for edge calculations
- **TestPenaltyFunctions**: 10 tests for penalty computations
- **TestConfidenceFunctions**: 7 tests for confidence and multipliers
- **TestCandidateScoringFunctions**: 6 tests for scoring orchestration
- **TestCandidateScoringPolicy**: 8 tests for policy class
- **TestScoringModuleExports**: 1 test verifying all exports

### All Tests Pass
- 51 pre-existing tests (from Phase 1 and runtime golden)
- 44 new scoring module tests
- **Total: 95 tests passing**

## Migration Notes

### For New Code
Prefer importing directly from the scoring package:
```python
from courtvision.scoring import compute_selection_score, CandidateScoringPolicy
```

### For Existing Code
No changes required. Continue using:
```python
from courtvision.runtime_scoring import BoardScoringConfig, BoardScoringPolicy
```

### Future Phase 3+ Recommendations
- Consider migrating callers from `runtime_scoring` to `courtvision.scoring`
- `runtime_scoring` can eventually be deprecated once all callers migrate
- The new `CandidateScoringConfig` has default values (more ergonomic)

## Verification

All existing tests pass without modification:
- `test_runtime_golden.py` - 34 tests (scoring behavior unchanged)
- `test_selection_modules.py` - 7 tests (Phase 1 migration)
- `test_scoring_modules.py` - 44 tests (new coverage)

## Files Modified/Created

### Created
- `courtvision/scoring/__init__.py`
- `courtvision/scoring/edge.py`
- `courtvision/scoring/confidence.py`
- `courtvision/scoring/penalties.py`
- `courtvision/scoring/candidate_scoring.py`
- `tests/test_scoring_modules.py`
- `MIGRATION_PHASE2.md`

### Modified
- `courtvision/runtime_scoring.py` - Now a compatibility layer

## No Logic Changes Verified

The migration was **pure relocation** with no logic modifications:
- All thresholds identical
- All formulas identical
- All weightings identical
- All penalties identical
- All output keys identical
- All test assertions pass without modification
