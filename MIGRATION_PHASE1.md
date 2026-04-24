"""Migration: Board Construction Logic Extraction

SCOPE: Phase 1 - Operator Board Lane Assignment
DATE: 2026-04-15
TARGET: Extract board construction and lane assignment logic from courtvision_ai.py

---WHAT MOVED:
1. Board classification logic
   - operator_boards.py: build_operator_boards(), assign_candidate_lanes()
   - lanes.py: classify_candidate_lane(), classify_candidates_batch()
   
These modules provide:
- Clean lane assignment (elite, full_market, stat_only, team_board, rejected)
- Testable, unit-testable board construction
- Lane trace and assignment summary

---WHAT STILL LIVES IN courtvision_ai.py (TO BE EXTRACTED LATER):
1. Primary scoring loop (team markets + player markets)
   - _score_team_markets()
   - _score_player_markets()
   - Game/player iteration and contextualization

2. Board construction methods (still called, can be migrated to use new package modules)
   - _build_final_operator_boards()
   - _build_near_miss_board()
   - _build_stat_only_board()
   - _select_elite_board()
   - _select_top_per_market()
   
3. Output/exposure logic
   - _apply_team_exposure_caps()
   - _apply_board_audit_frame()
   - Candidate enrichment and preparation
   
4. Auxiliary boards
   - _build_team_board()
   - _build_sgp_board()
   - _build_strike_board()
   - _build_predictive_lines_board()

5. Diagnostics and history
   - _build_board_diagnostics()
   - _append_history()
   - Data status messages

---COMPATIBILITY GUARANTEE:
- ✓ courtvision_ai.py predict() still works as entrypoint
- ✓ All tests still pass (existing tests use the full pipeline)
- ✓ scripts/run_daily.py and scripts/run_grading.py unchanged
- ✓ All outputs identical (new modules are reference implementations, not yet integrated into predict())

---NEXT STEPS:
1. Update predict() to use new operator_boards.build_operator_boards() for elite/full_market
2. Extract scoring calibration into courtvision/scoring/ package
3. Extract team baseline lookup and injury context into courtvision/domain/
4. Move stat_only / predictive fill logic to courtvision/selection/predictive.py
5. Extract candidate enrichment into courtvision/pipeline/enrichment.py

---TESTING:
- Run: pytest tests/test_candidate_builder.py -v
- Run: python scripts/run_daily.py --prediction-date 2026-04-15 (manual smoke test)
- Verify boards are still populated with expected candidates

---CODE LOCATION:
- courtvision/selection/operator_boards.py (NEW)
- courtvision/selection/lanes.py (NEW)
- courtvision_ai.py (unchanged, but prepared for integration)
- courtvision/pipeline/runner.py (unchanged, ready to orchestrate new modules)
"""
