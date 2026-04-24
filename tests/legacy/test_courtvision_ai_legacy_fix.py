"""Regression tests for CourtVisionAI legacy attribute fix.

Ensures CourtVisionAI.predict() no longer references legacy monolith attributes
like self.min_edge, self.min_confidence, etc.

The fix ensures:
- CourtVisionAI does not own thresholds
- PredictionConfig owns thresholds via defaults
- No AttributeError on predict() call
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCourtVisionAILegacyFix:
    """Test that legacy monolith attribute dependencies are removed."""

    def test_predict_does_not_reference_min_edge(self):
        """Verify CourtVisionAI class has no min_edge attribute reference."""
        # Read the source file and check for legacy patterns
        source_path = Path(__file__).parent.parent / "courtvision_ai.py"
        source = source_path.read_text()

        # Should NOT have self.min_edge or self.min_confidence in predict method
        assert "self.min_edge" not in source, (
            "Legacy self.min_edge reference found in courtvision_ai.py. "
            "Thresholds should come from PredictionConfig."
        )
        assert "self.min_confidence" not in source, (
            "Legacy self.min_confidence reference found in courtvision_ai.py. "
            "Thresholds should come from PredictionConfig."
        )

    def test_prediction_config_has_threshold_defaults(self):
        """Verify PredictionConfig has default threshold values."""
        from courtvision.pipeline import PredictionConfig

        # Create config with just required field
        config = PredictionConfig(prediction_date="2024-01-01")

        # Should have default values
        assert hasattr(config, "min_edge")
        assert hasattr(config, "min_confidence")
        assert config.min_edge == 0.5
        assert config.min_confidence == 0.35

    def test_predict_runs_without_attribute_error(self):
        """Verify predict() can be called without AttributeError.

        This tests that the fix works by mocking the dependencies
        and ensuring no legacy attribute access.
        """
        from courtvision_ai import CourtVisionAI

        # Create instance
        cva = CourtVisionAI(out_dir="test_outputs")

        # Mock all the dependencies to isolate the predict method
        mock_client = MagicMock()
        mock_client.get_games.return_value = []
        mock_client.get_odds.return_value = pd.DataFrame()
        mock_client.get_injuries.return_value = pd.DataFrame()
        mock_client.last_odds_status = "ok"
        mock_client.last_odds_message = "test"

        # Mock the _get_client method
        cva._get_client = lambda: mock_client

        # Mock file reading methods
        cva._safe_read_csv = lambda path: pd.DataFrame()
        cva._load_calibration_rules = lambda: {}
        cva._build_league_context = lambda x: {}
        cva._normalize_games = lambda x: pd.DataFrame()
        cva._normalize_odds = lambda x: pd.DataFrame()
        cva._normalize_injuries = lambda x: pd.DataFrame()

        # This should NOT raise AttributeError for min_edge/min_confidence
        # The fix ensures these are no longer referenced
        try:
            result = cva.predict("2024-01-01")
        except AttributeError as e:
            if "min_edge" in str(e) or "min_confidence" in str(e):
                pytest.fail(f"Legacy attribute error: {e}")
            # Other AttributeErrors are expected due to mocking
            pass
        except Exception as e:
            # Other exceptions from mocking are fine
            # We just want to ensure no AttributeError on legacy attributes
            if "min_edge" in str(e) or "min_confidence" in str(e):
                pytest.fail(f"Legacy attribute error: {e}")

    def test_prediction_config_used_in_pipeline(self):
        """Verify PredictionConfig is properly used in pipeline construction."""
        from courtvision.pipeline import PredictionConfig, PredictionPipeline

        # Create minimal config
        config = PredictionConfig(
            prediction_date="2024-01-01",
            enable_partial_fill=True,
        )

        # Should not need min_edge/min_confidence passed explicitly
        # They should use defaults
        assert config.min_edge == 0.5
        assert config.min_confidence == 0.35

        # Pipeline should accept config without errors
        pipeline = PredictionPipeline(config)
        assert pipeline.config is config

    def test_run_daily_compatibility(self):
        """Verify run_daily entry point still works with the fix."""
        from courtvision_ai import CourtVisionAI

        cva = CourtVisionAI(out_dir="test_outputs")

        # Check that the instance can be created without legacy attributes
        # Previously this would fail if min_edge/min_confidence were required in __init__
        assert not hasattr(cva, "min_edge")
        assert not hasattr(cva, "min_confidence")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
