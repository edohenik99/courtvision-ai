"""Integration Guide: Wiring New Selection Modules into predict()

This document provides step-by-step instructions for integrating the new
selection modules (operator_boards.py and lanes.py) into the predict() method.

STATUS: READY FOR INTEGRATION
RISK LEVEL: LOW (modules are pure functions, no side effects)
ESTIMATED EFFORT: 15 minutes
TESTING: Add integration test for lane assignment tracing

---

CURRENT FLOW (before integration):
  predict()
    ↓
    _build_final_operator_boards() [in courtvision_ai.py]
      ↓
      _live_market_only() → filter for sportsbook_line and odds
      _select_elite_board() → filter by quality/confidence
      _select_top_per_market() → limit per market type
      ↓
    returns: (elite_df, full_market_df, final_board_construction_traces)

NEW FLOW (after integration):
  predict()
    ↓
    build_operator_boards() [from courtvision.selection]
      ↓
      Takes same inputs + callables for board selection
      ↓
    returns: same outputs + lane assignment summary

---

INTEGRATION STEPS:

Step 1: Add import at top of courtvision_ai.py
  
  from courtvision.selection import build_operator_boards, assign_candidate_lanes

Step 2: Locate _build_final_operator_boards() call in predict() 
  
  Current code (line ~2827):
    elite_df, full_market_df, final_board_construction = self._build_final_operator_boards(
        prepared_selected_df,
        per_market_limit=20,
    )

Step 3: Replace with new function call
  
  Replace:
    elite_df, full_market_df, final_board_construction = self._build_final_operator_boards(
        prepared_selected_df,
        per_market_limit=20,
    )
  
  With:
    elite_df, full_market_df, final_board_construction = build_operator_boards(
        prepared_selected_df,
        per_market_limit=20,
        select_elite_board=self._select_elite_board,
        select_top_per_market=self._select_top_per_market,
    )

Step 4: Optional - Add lane assignment tracking
  
  After board construction, you can now call:
  
    lane_assignment_summary = assign_candidate_lanes(
        prepared_selected_df,  # all qualified candidates
        elite_df,              # selected for elite board
        full_market_df,        # selected for full_market board
    )
    
  Then log or include in summary:
    self.logger.info(
        "lane_assignment total_qualified=%d elite=%d full_market=%d unselected=%d",
        lane_assignment_summary["total_qualified"],
        lane_assignment_summary["assigned_to_elite"],
        lane_assignment_summary["assigned_to_full_market"],
        lane_assignment_summary["qualified_but_not_selected"],
    )

Step 5: Keep _build_final_operator_boards() in place for now
  
  Rationale:
  - _build_final_operator_boards() is still called by the new build_operator_boards()
  - Removing it would break the integration
  - It will be deprecated and removed in Phase 2 after full refactoring
  
  Optional: Add deprecation comment:
    def _build_final_operator_boards(self, ...):
        # DEPRECATED: Use courtvision.selection.build_operator_boards() instead
        # This method will be removed in Phase 2.
        ...

Step 6: Run tests to verify integration
  
  $ pytest tests/ -q
  Expected: 51 passed

Step 7: Manual smoke test
  
  $ python scripts/run_daily.py --prediction-date 2026-04-15
  Expected: Boards generated with elite, full_market, stat_only rows

Step 8: Verify boards are identical
  
  Compare output:
  - outputs/boards/2026-04-15/elite_props.csv (should have same candidates)
  - outputs/boards/2026-04-15/full_market_props.csv (should have same candidates)

---

ROLLBACK PROCEDURE (If something breaks):

  1. Undo the import statement
  2. Undo the function call replacement (revert to _build_final_operator_boards)
  3. Run tests again: pytest tests/ -q

  All changes are isolated - no other dependencies will break.

---

OPTIMIZATION OPPORTUNITIES (After Integration):

Once integrated and tested, consider:

1. Extract _select_elite_board and _select_top_per_market into package modules
   Location: courtvision/selection/selectors.py
   
2. Create integration test that verifies:
   - Elite board candidates are correct
   - Full market board candidates are correct
   - Lane assignment summary matches board contents

3. Log lane assignment summary in predict() for diagnostics

4. Add board composition metrics to prediction summary

---

INTEGRATION TEST TEMPLATE:

  def test_predict_uses_new_board_construction():
      \"\"\"Verify predict() successfully uses new board construction modules.\"\"\"
      ai = CourtVisionAI()
      outputs = ai.predict("2026-04-15")  # Requires valid date with games/odds
      
      elite_df = outputs.get("elite_props", pd.DataFrame())
      full_market_df = outputs.get("full_market_props", pd.DataFrame())
      
      # Both should be DataFrames (possibly empty)
      assert isinstance(elite_df, pd.DataFrame)
      assert isinstance(full_market_df, pd.DataFrame)
      
      # Elite should be subset of full_market (typically)
      if not elite_df.empty and not full_market_df.empty:
          # At least some elite candidates should exist
          assert len(elite_df) <= len(full_market_df)

---

SUCCESS CRITERIA:

After integration, verify:
  ✓ All 51 tests still pass
  ✓ Boards generated successfully
  ✓ Elite board candidates are high-quality
  ✓ Full market board has more candidates than elite
  ✓ No performance degradation
  ✓ Diagnostics/traces still available
  ✓ History tracking still works
  ✓ Manual smoke test passes

---

TIMELINE:

  Pre-integration review: 15 min
  Implementation: 10 min
  Testing & verification: 10 min
  Smoke testing: 15 min
  ---
  Total: ~50 minutes

---

QUESTIONS / TROUBLESHOOTING:

Q: Will boards change after integration?
A: No, they should be identical. New modules are faithful implementations of existing logic.

Q: Can I integrate just the lanes module without operator_boards?
A: Yes, they're independent. But for board construction, you'll need both.

Q: Do I need to update any other files?
A: No, just courtvision_ai.py. All other files are unchanged.

Q: What if tests fail after integration?
A: Check the error message. Most likely causes:
  - Import path wrong
  - Callable signatures changed (check select_elite_board, select_top_per_market)
  - DataFrame columns missing
  See REFACTORING_SUMMARY.md for full module documentation.

Q: When should I do this?
A: After the review comments on this PR are addressed. Suggested: separate integration PR.

---

For detailed module documentation, see:
  courtvision/selection/operator_boards.py (doc strings)
  courtvision/selection/lanes.py (doc strings)
  REFACTORING_SUMMARY.md (architecture overview)
  MIGRATION_PHASE1.md (phase notes)
"""
