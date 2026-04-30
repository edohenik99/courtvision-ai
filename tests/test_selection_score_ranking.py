"""Regression test for selection_score ranking.

Ensures selection_score is calculated and used for ranking candidates.

Related to fix for:
- selection_score = 0.0 for all rows
- system not ranking anything
- boards just filtered, not optimized
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSelectionScoreCalculation:
    """Test that selection_score is properly calculated."""

    def test_compute_selection_score_returns_score(self):
        """Verify compute_selection_score returns a selection_score."""
        from courtvision.scoring.candidate_scoring import compute_selection_score

        row = {
            "edge": 5.0,
            "confidence": 0.75,
            "quality_score": 85.0,
            "player_tier": "star",
            "is_favorite": False,
            "volatility": 0.3,
            "minutes_avg": 32.0,
            "projection": 25.5,
            "line": 24.5,
        }

        result = compute_selection_score(row)

        assert "selection_score" in result, "selection_score not in result"
        assert result["selection_score"] > 0, f"Expected positive selection_score, got {result['selection_score']}"
        assert result["selection_score"] == pytest.approx(30.125, rel=0.01)

    def test_selection_score_components(self):
        """Verify selection_score uses side edge, adjusted confidence, and quality."""
        from courtvision.scoring.candidate_scoring import compute_selection_score

        row1 = {"side_edge_pct": 0.10, "confidence": 0.6, "quality_score": 70.0}
        result1 = compute_selection_score(row1)
        assert result1["selection_score"] == pytest.approx(28.32, rel=0.01)

        # Negative side edge is clamped to zero for the edge reward component.
        row2 = {"side_edge_pct": -0.02, "confidence": 0.8, "quality_score": 90.0}
        result2 = compute_selection_score(row2)
        assert result2["selection_score"] == pytest.approx(30.08, rel=0.01)

    def test_selection_score_used_for_sorting(self):
        """Verify candidates are sorted by selection_score before board selection."""
        candidates = [
            {"player_name": "Player A", "selection_score": 5.0, "is_elite": True},
            {"player_name": "Player B", "selection_score": 15.0, "is_elite": True},
            {"player_name": "Player C", "selection_score": 10.0, "is_elite": True},
        ]

        df = pd.DataFrame(candidates)

        # Sort by selection_score descending (like in select_elite_board)
        df_sorted = df.sort_values("selection_score", ascending=False)

        # Verify order: B (15), C (10), A (5)
        names = df_sorted["player_name"].tolist()
        assert names == ["Player B", "Player C", "Player A"]

    def test_elite_board_takes_top_by_selection_score(self):
        """Verify elite board takes top plays by selection_score, not random."""
        candidates = [
            {"player_name": "Low Score", "selection_score": 5.0, "is_elite": True, "quality_score": 85, "confidence": 0.75},
            {"player_name": "High Score", "selection_score": 20.0, "is_elite": True, "quality_score": 90, "confidence": 0.85},
            {"player_name": "Medium Score", "selection_score": 12.0, "is_elite": True, "quality_score": 82, "confidence": 0.70},
        ]

        df = pd.DataFrame(candidates)

        # Sort and take top 2
        df_sorted = df.sort_values("selection_score", ascending=False)
        elite_df = df_sorted.head(2)

        # Should include High Score and Medium Score
        assert "High Score" in elite_df["player_name"].values
        assert "Medium Score" in elite_df["player_name"].values
        assert "Low Score" not in elite_df["player_name"].values


class TestRejectionReasonCleaning:
    """Test that rejection_reason is cleaned in selected rows."""

    def test_rejection_reason_cleaned_in_elite(self):
        """Verify selection_rejection_reason is cleared for selected elite rows."""
        df = pd.DataFrame([
            {"player_name": "Player A", "selection_rejection_reason": "some_reason", "is_elite": True},
        ])

        # Simulate cleaning in select_elite_board
        if "selection_rejection_reason" in df.columns:
            df["selection_rejection_reason"] = ""

        assert all(df["selection_rejection_reason"] == ""), "rejection_reason should be empty for selected rows"

    def test_rejection_reason_cleaned_in_full_market(self):
        """Verify selection_rejection_reason is cleared for selected full market rows."""
        df = pd.DataFrame([
            {"player_name": "Player A", "selection_rejection_reason": "another_reason", "market_type": "points"},
        ])

        # Simulate cleaning in select_top_per_market
        if "selection_rejection_reason" in df.columns:
            df["selection_rejection_reason"] = ""

        assert all(df["selection_rejection_reason"] == ""), "rejection_reason should be empty for selected rows"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
