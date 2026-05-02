"""Unit tests for runtime output validation.

Tests that the validate_runtime_outputs.py script correctly:
1. Requires core prediction boards (elite board)
2. Warns but does not fail on missing audit summary
3. Validates directional edges and cap enforcement when data available
"""

import json
import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import pytest

# Import the module functions directly
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from validate_runtime_outputs import (
    load_audit_summary,
    print_cap_enforcement,
    print_directional,
    print_final_summary,
    validate_outputs,
    _operator_dir,
)


class TestLoadAuditSummary:
    """Test loading audit summary JSON."""

    def test_missing_audit_summary_returns_none(self, tmp_path, monkeypatch):
        """Missing audit summary should return None with warning, not crash."""
        # Mock the operator directory to tmp_path
        monkeypatch.setattr(
            "validate_runtime_outputs._operator_dir", lambda: tmp_path
        )
        
        # Capture stderr
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        result = load_audit_summary("2024-01-01")
        
        stderr_output = sys.stderr.getvalue()
        sys.stderr = old_stderr
        
        assert result is None
        assert "WARNING" in stderr_output
        assert "not found" in stderr_output

    def test_present_audit_summary_returns_dict(self, tmp_path, monkeypatch):
        """Present audit summary should return dict with data."""
        monkeypatch.setattr(
            "validate_runtime_outputs._operator_dir", lambda: tmp_path
        )
        
        # Create a mock audit summary
        audit_data = {
            "slate_date": "2024-01-01",
            "totals": {"total_candidates": 100},
            "summary": {
                "board_analytics": {
                    "max_team_exposure": 2,
                    "max_game_exposure": 3,
                }
            }
        }
        audit_path = tmp_path / "elite_pipeline_audit_summary_2024-01-01.json"
        with open(audit_path, "w") as f:
            json.dump(audit_data, f)
        
        result = load_audit_summary("2024-01-01")
        
        assert result is not None
        assert result["slate_date"] == "2024-01-01"


class TestPrintCapEnforcement:
    """Test cap enforcement printing."""

    def test_missing_audit_summary_shows_skip(self, capsys):
        """When audit summary is None, should show [SKIP] and return success."""
        max_team, max_game, ok = print_cap_enforcement(None)
        
        captured = capsys.readouterr()
        
        assert "[SKIP]" in captured.out
        assert "Audit summary not available" in captured.out
        assert max_team == 0
        assert max_game == 0
        assert ok is True  # Should allow run to continue

    def test_valid_audit_summary_passes_caps(self, capsys):
        """Valid audit with caps under limits should pass."""
        audit = {
            "summary": {
                "board_analytics": {
                    "max_team_exposure": 2,
                    "max_game_exposure": 3,
                }
            }
        }
        
        max_team, max_game, ok = print_cap_enforcement(audit)
        
        captured = capsys.readouterr()
        
        assert "[OK]" in captured.out
        assert "max_team_exposure = 2" in captured.out
        assert "max_game_exposure = 3" in captured.out
        assert ok is True

    def test_violation_audit_summary_fails_caps(self, capsys):
        """Audit with caps over limits should fail."""
        audit = {
            "summary": {
                "board_analytics": {
                    "max_team_exposure": 5,  # Over cap of 3
                    "max_game_exposure": 6,  # Over cap of 4
                }
            }
        }
        
        max_team, max_game, ok = print_cap_enforcement(audit)
        
        captured = capsys.readouterr()
        
        assert "[FAIL]" in captured.out
        assert "VIOLATION" in captured.out
        assert ok is False


class TestPrintDirectional:
    """Test directional edge validation."""

    def test_valid_directional_picks(self, tmp_path, capsys):
        """Valid picks with correct directional edges should pass."""
        # Create a valid elite board CSV
        elite_path = tmp_path / "elite_board.csv"
        df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "market_type": ["points", "rebounds"],
            "selection": ["over", "under"],
            "edge": [0.05, -0.03],  # over with positive, under with negative
        })
        df.to_csv(elite_path, index=False)
        
        result = print_directional(elite_path)
        
        captured = capsys.readouterr()
        
        assert result is True
        assert "[OK]" in captured.out
        assert "2 rows" in captured.out

    def test_invalid_directional_picks(self, tmp_path, capsys):
        """Invalid picks with wrong directional edges should fail."""
        elite_path = tmp_path / "elite_board.csv"
        df = pd.DataFrame({
            "player_name": ["Player A", "Player B"],
            "market_type": ["points", "rebounds"],
            "selection": ["over", "under"],
            "edge": [-0.05, 0.03],  # WRONG: over with negative, under with positive
        })
        df.to_csv(elite_path, index=False)
        
        result = print_directional(elite_path)
        
        captured = capsys.readouterr()
        
        assert result is False
        assert "[FAIL]" in captured.out
        assert "directional violation" in captured.out


class TestPrintFinalSummary:
    """Test final summary printing."""

    def test_missing_audit_summary_shows_info(self, capsys):
        """Missing audit summary should show [INFO] message."""
        print_final_summary(None, 0, 0)
        
        captured = capsys.readouterr()
        
        assert "[INFO]" in captured.out
        assert "Audit summary not available" in captured.out

    def test_present_audit_summary_shows_stats(self, capsys):
        """Present audit summary should show detailed stats."""
        audit = {
            "summary": {
                "provider_used": "sportsdataio",
                "elite_count": 10,
                "candidate_count": 50,
            },
            "totals": {
                "total_rejections": 40,
            }
        }
        
        print_final_summary(audit, 2, 3)
        
        captured = capsys.readouterr()
        
        assert "Provider Used:" in captured.out
        assert "sportsdataio" in captured.out
        assert "Elite Count:" in captured.out
        assert "10" in captured.out


class TestValidateOutputs:
    """Integration tests for full validation."""

    def test_missing_elite_board_fails(self, tmp_path, monkeypatch):
        """Missing elite board should cause validation to fail."""
        monkeypatch.setattr(
            "validate_runtime_outputs._operator_dir", lambda: tmp_path
        )
        
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        result = validate_outputs("2024-01-01")
        
        stderr_output = sys.stderr.getvalue()
        sys.stderr = old_stderr
        
        assert result == 1
        assert "[ERROR]" in stderr_output
        assert "Elite board not found" in stderr_output

    def test_present_elite_missing_audit_passes(self, tmp_path, monkeypatch, capsys):
        """Present elite board but missing audit summary should pass (warn only)."""
        monkeypatch.setattr(
            "validate_runtime_outputs._operator_dir", lambda: tmp_path
        )
        
        # Create valid elite board
        elite_path = tmp_path / "elite_board_2024-01-01.csv"
        df = pd.DataFrame({
            "player_name": ["Player A"],
            "market_type": ["points"],
            "selection": ["over"],
            "edge": [0.05],
        })
        df.to_csv(elite_path, index=False)
        
        old_stderr = sys.stderr
        sys.stderr = StringIO()
        
        result = validate_outputs("2024-01-01")
        
        stderr_output = sys.stderr.getvalue()
        sys.stderr = old_stderr
        
        # Should pass (exit code 0) even without audit summary
        assert result == 0
        # But should warn
        assert "WARNING" in stderr_output
        assert "Audit summary not found" in stderr_output

    def test_present_elite_present_audit_passes(self, tmp_path, monkeypatch):
        """Both elite board and audit summary present should pass."""
        monkeypatch.setattr(
            "validate_runtime_outputs._operator_dir", lambda: tmp_path
        )
        
        # Create valid elite board
        elite_path = tmp_path / "elite_board_2024-01-01.csv"
        df = pd.DataFrame({
            "player_name": ["Player A"],
            "market_type": ["points"],
            "selection": ["over"],
            "edge": [0.05],
        })
        df.to_csv(elite_path, index=False)
        
        # Create valid audit summary
        audit_data = {
            "summary": {
                "board_analytics": {
                    "max_team_exposure": 2,
                    "max_game_exposure": 3,
                }
            }
        }
        audit_path = tmp_path / "elite_pipeline_audit_summary_2024-01-01.json"
        with open(audit_path, "w") as f:
            json.dump(audit_data, f)
        
        result = validate_outputs("2024-01-01")
        
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
