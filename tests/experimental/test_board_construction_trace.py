"""Regression tests for board construction trace compatibility.

Tests that _build_board_construction_trace handles:
- Optional parameters with None defaults
- Empty DataFrame inputs
- Missing optional stages (post_exposure_caps, post_backfill)
- Legacy wrapper path from CourtVisionAI.predict()

Related to fix for:
TypeError: _build_board_construction_trace() missing 2 required keyword-only arguments
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestBoardConstructionTraceCompatibility:
    """Test backward compatibility of board construction trace."""

    def test_all_parameters_optional_with_none_defaults(self):
        """Test that all parameters can be None (use defaults)."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Should not raise TypeError - all parameters have None defaults
        result = cva._build_board_construction_trace()

        # Should return trace with empty DataFrames normalized
        assert isinstance(result, dict)
        assert "input_live_candidates" in result
        assert "post_primary_selection" in result
        assert "post_exposure_caps" in result
        assert "post_backfill" in result
        assert result["candidate_count_before_final_board_build"] == 0
        assert result["final_selected_count"] == 0
        assert result["backfill_added_count"] == 0

    def test_partial_parameters_none(self):
        """Test that partial None parameters work."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Only provide some parameters, rest are None
        result = cva._build_board_construction_trace(
            input_live_candidates=pd.DataFrame({"col": [1, 2]}),
            post_primary_selection=pd.DataFrame({"col": [3]}),
            # post_exposure_caps and post_backfill are None
        )

        assert isinstance(result, dict)
        assert result["candidate_count_before_final_board_build"] == 2
        assert result["final_selected_count"] == 0  # post_backfill was None -> empty

    def test_all_parameters_provided(self):
        """Test normal operation with all parameters provided."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        result = cva._build_board_construction_trace(
            input_live_candidates=pd.DataFrame({"col": [1, 2, 3]}),
            post_primary_selection=pd.DataFrame({"col": [1, 2]}),
            post_exposure_caps=pd.DataFrame({"col": [1]}),
            post_backfill=pd.DataFrame({"col": [1]}),
        )

        assert isinstance(result, dict)
        assert result["candidate_count_before_final_board_build"] == 3
        assert result["final_selected_count"] == 1

    def test_empty_dataframes_handled_gracefully(self):
        """Test that empty DataFrames don't cause errors."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        result = cva._build_board_construction_trace(
            input_live_candidates=pd.DataFrame(),
            post_primary_selection=pd.DataFrame(),
            post_exposure_caps=pd.DataFrame(),
            post_backfill=pd.DataFrame(),
        )

        assert isinstance(result, dict)
        assert result["candidate_count_before_final_board_build"] == 0
        assert result["final_selected_count"] == 0
        assert result["backfill_added_count"] == 0

    def test_legacy_call_from_predict_path(self):
        """Test the specific legacy call pattern from CourtVisionAI.predict().

        This tests the exact call pattern that was failing:
        _build_board_construction_trace(
            input_live_candidates=result.merged_market_props.copy() if not empty else pd.DataFrame(),
            post_primary_selection=elite_df.copy(),
            # missing post_exposure_caps and post_backfill
        )
        """
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Simulate the legacy call pattern
        elite_df = pd.DataFrame({"player": ["Player A"], "edge": [2.5]})
        merged_props = pd.DataFrame({"market": ["pts"], "line": [24.5]})

        # This was causing: TypeError: missing 2 required keyword-only arguments
        try:
            result = cva._build_board_construction_trace(
                input_live_candidates=merged_props.copy() if not merged_props.empty else pd.DataFrame(),
                post_primary_selection=elite_df.copy(),
                # post_exposure_caps and post_backfill NOT provided
            )
        except TypeError as e:
            pytest.fail(f"Legacy call pattern should not raise TypeError: {e}")

        assert isinstance(result, dict)
        assert result["candidate_count_before_final_board_build"] == len(merged_props)

    def test_player_points_elite_admission_optional(self):
        """Test that player_points_elite_admission parameter remains optional."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Without player_points_elite_admission
        result1 = cva._build_board_construction_trace(
            input_live_candidates=pd.DataFrame(),
            post_primary_selection=pd.DataFrame(),
        )

        # With player_points_elite_admission
        admission_df = pd.DataFrame({"player": ["A"], "admitted": [True]})
        result2 = cva._build_board_construction_trace(
            input_live_candidates=pd.DataFrame(),
            post_primary_selection=pd.DataFrame(),
            player_points_elite_admission=admission_df,
        )

        assert "player_points_elite_admission_rows" in result1
        assert result1["player_points_elite_admission_rows"] == []
        assert "player_points_elite_admission_rows" in result2
        assert len(result2["player_points_elite_admission_rows"]) == 1


class TestTraceStagesDocumentation:
    """Document what trace stages are and which are optional."""

    def test_trace_stages_documented_in_output(self):
        """Verify trace output structure documents the pipeline stages."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        result = cva._build_board_construction_trace()

        # Core trace stages (all pipeline stages)
        expected_stages = [
            "input_live_candidates",      # Stage 1: Raw input candidates
            "post_primary_selection",     # Stage 2: After initial selection
            "post_exposure_caps",         # Stage 3: After exposure limits (optional)
            "post_backfill",             # Stage 4: After backfill (optional)
        ]

        for stage in expected_stages:
            assert stage in result, f"Expected stage {stage} in trace output"

        # Diagnostic counts
        expected_counts = [
            "candidate_count_before_final_board_build",
            "core_pass_candidate_count",
            "live_quality_rescue_candidate_count",
            "final_selected_count",
            "backfill_added_count",
            "backfill_added_by_qualification_gate_mode",
        ]

        for count in expected_counts:
            assert count in result, f"Expected count {count} in trace output"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
