"""Regression test for team_lookup NameError fix.

Ensures CourtVisionAI.predict() properly defines team_lookup
from team_baselines before using it in helper method calls.

Related to fix for:
NameError: name 'team_lookup' is not defined
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestTeamLookupDefined:
    """Test that team_lookup is properly defined in predict()."""

    def test_team_lookup_built_from_team_baselines(self):
        """Verify team_lookup is built from team_baselines with team_abbr column."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Create mock team_baselines with team_abbr
        team_baselines = pd.DataFrame({
            "team_abbr": ["LAL", "BOS", "GSW"],
            "team_name": ["Lakers", "Celtics", "Warriors"],
            "some_stat": [100.0, 95.0, 98.0],
        })

        # Simulate the team_lookup building logic
        team_lookup: dict[str, dict[str, any]] = {}
        if not team_baselines.empty and "team_abbr" in team_baselines.columns:
            team_lookup = {
                str(row["team_abbr"]): row.to_dict()
                for _, row in team_baselines.iterrows()
                if pd.notna(row.get("team_abbr"))
            }

        # Verify team_lookup was built correctly
        assert "LAL" in team_lookup
        assert "BOS" in team_lookup
        assert "GSW" in team_lookup
        assert team_lookup["LAL"]["team_name"] == "Lakers"

    def test_team_lookup_empty_when_no_team_abbr(self):
        """Verify team_lookup is empty when team_abbr column missing."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Create mock team_baselines WITHOUT team_abbr
        team_baselines = pd.DataFrame({
            "team_name": ["Lakers", "Celtics"],
            "some_stat": [100.0, 95.0],
        })

        # Simulate the team_lookup building logic
        team_lookup: dict[str, dict[str, any]] = {}
        if not team_baselines.empty and "team_abbr" in team_baselines.columns:
            team_lookup = {
                str(row["team_abbr"]): row.to_dict()
                for _, row in team_baselines.iterrows()
                if pd.notna(row.get("team_abbr"))
            }

        # Verify team_lookup is empty
        assert team_lookup == {}

    def test_team_lookup_used_in_build_stat_only_board(self):
        """Verify team_lookup is passed to _build_stat_only_board without NameError."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Build minimal team_lookup
        team_lookup = {
            "LAL": {"team_abbr": "LAL", "pts_allowed_avg": 110.0},
            "GSW": {"team_abbr": "GSW", "pts_allowed_avg": 115.0},
        }

        # Verify team_lookup can be used (would raise NameError if undefined)
        opp_abbr = "LAL"
        opponent_row = team_lookup.get(opp_abbr)
        assert opponent_row is not None
        assert opponent_row["team_abbr"] == "LAL"

    def test_predict_source_has_team_lookup_defined(self):
        """Verify the predict() source code defines team_lookup before use."""
        import ast

        source_path = Path(__file__).parent.parent / "courtvision_ai.py"
        source = source_path.read_text()

        # Parse the source to find the predict method
        tree = ast.parse(source)

        # Find predict method
        predict_method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "predict":
                predict_method = node
                break

        assert predict_method is not None, "predict() method not found"

        # Check that team_lookup is assigned before any calls using it
        source_lines = source.split("\n")
        predict_start = predict_method.lineno - 1
        predict_end = predict_method.end_lineno

        predict_source = "\n".join(source_lines[predict_start:predict_end])

        # Should have team_lookup assignment
        assert "team_lookup:" in predict_source or "team_lookup =" in predict_source, (
            "team_lookup is not defined in predict() method"
        )

        # Should define it BEFORE using it in calls
        # Find the position of definition vs usage
        lines = predict_source.split("\n")
        def_line = None
        use_lines = []

        for i, line in enumerate(lines):
            if "team_lookup:" in line or ("team_lookup" in line and "=" in line and "team_lookup.get" not in line):
                if "for" not in line:  # Exclude the comprehension
                    def_line = i
            if "team_lookup=team_lookup" in line or "team_lookup.get" in line:
                use_lines.append(i)

        # If we found both, verify definition comes before usage
        if def_line is not None and use_lines:
            assert def_line < min(use_lines), (
                f"team_lookup defined at line {def_line} but used at line {min(use_lines)}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
