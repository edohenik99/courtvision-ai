"""Regression test for injury_context NameError fix.

Ensures the active CourtVisionAI NBA engine implementation extracts
injury_context from PredictionResult instead of using an undefined variable.

Related to fix for:
NameError: name 'injury_context' is not defined
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestInjuryContextDefined:
    """Test that injury_context is properly sourced from pipeline."""

    def test_prediction_result_has_injury_context(self):
        """Verify PredictionResult dataclass includes injury_context field."""
        from courtvision.pipeline import PredictionResult

        # Create result with injury_context
        injury_ctx = {"teams": {"LAL": {}}, "players": {}}
        result = PredictionResult(
            prediction_date="2026-04-17",
            injury_context=injury_ctx,
        )

        assert result.injury_context is not None
        assert result.injury_context["teams"] == {"LAL": {}}

    def test_prediction_result_injury_context_defaults_to_empty_dict(self):
        """Verify injury_context defaults to canonical empty dict when not provided."""
        from courtvision.pipeline import PredictionResult

        result = PredictionResult(prediction_date="2026-04-17")
        assert result.injury_context == {
            "teams": {},
            "players": {},
            "metadata": {},
        }

    def test_extract_injury_context_from_result(self):
        """Simulate the extraction logic in CourtVisionAI.predict()."""
        from courtvision.pipeline import PredictionResult

        # Simulate pipeline result
        injury_ctx = {"teams": {"LAL": {"impact_score": 0.2}}, "players": {}}
        result = PredictionResult(
            prediction_date="2026-04-17",
            injury_context=injury_ctx,
            elite_props=pd.DataFrame({"player": ["A"]}),
            full_market_props=pd.DataFrame(),
        )

        # Simulate extraction in CourtVisionAI.predict()
        injury_context = result.injury_context

        assert injury_context is not None
        assert "LAL" in injury_context["teams"]

    def test_injury_context_passed_to_helpers(self):
        """Verify injury_context can be passed to helper methods."""
        from courtvision.pipeline import PredictionResult

        result = PredictionResult(
            prediction_date="2026-04-17",
            injury_context={"teams": {}, "players": {}},
        )

        injury_context = result.injury_context

        # Simulate helper method signature check
        def mock_helper(injury_context: dict | None = None) -> dict | None:
            return injury_context

        # Should accept the extracted injury_context
        passed = mock_helper(injury_context=injury_context)
        assert passed is not None

    def test_predict_source_uses_result_injury_context(self):
        """Verify the active NBA implementation extracts injury context."""
        import ast

        source_path = Path(__file__).parent.parent / "courtvision_ai.py"
        source = source_path.read_text(encoding="utf-8", errors="ignore")

        # The public predict() method is now a thin canonical-service wrapper.
        # Inspect the approved internal NBA engine implementation instead.
        tree = ast.parse(source)

        # Find the active implementation method.
        predict_method = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef)
                and node.name == "_predict_internal"
            ):
                predict_method = node
                break

        assert predict_method is not None, "_predict_internal() method not found"

        # Check that injury_context is extracted from result
        source_lines = source.split("\n")
        predict_start = predict_method.lineno - 1
        predict_end = predict_method.end_lineno

        predict_source = "\n".join(source_lines[predict_start:predict_end])

        # Should extract injury_context from result
        assert "result.injury_context" in predict_source, (
            "_predict_internal() should extract injury_context from result"
        )

        # Should define injury_context before using it in calls
        lines = predict_source.split("\n")
        def_line = None
        use_lines = []

        for i, line in enumerate(lines):
            if "injury_context = result.injury_context" in line:
                def_line = i
            if "injury_context=injury_context" in line:
                use_lines.append(i)

        # If we found both, verify definition comes before usage
        if def_line is not None and use_lines:
            assert def_line < min(use_lines), (
                f"injury_context defined at line {def_line} but used at line {min(use_lines)}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
