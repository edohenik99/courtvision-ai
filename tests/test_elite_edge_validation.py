"""
Regression tests for elite edge validation.

Ensures only directionally-valid edges enter elite board.
"""

import pandas as pd
import pytest
from typing import Any


class TestEliteEdgeValidation:
    """Test edge direction validation for elite board admission."""

    def test_positive_edge_over_selection_valid(self):
        """Positive edge with 'over' selection should qualify for elite."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Valid: edge > 0, selection = over
        row = {
            "market_type": "player_points",
            "selection": "over",
            "edge": 5.5,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "28_34",
        }
        
        result = auditor.qualification_reason(row)
        assert result in ["player_points_high_quality_pass", "player_points_scoring_quality_pass", "player_points_edge_confidence_pass"]
        assert result != "negative_edge_elite_reject"
        print("✓ positive edge + over selection valid")

    def test_negative_edge_under_selection_valid(self):
        """Negative edge with 'under' selection should qualify for elite."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Valid: edge < 0, selection = under (projection < line)
        row = {
            "market_type": "player_points",
            "selection": "under",
            "edge": -3.5,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "28_34",
        }
        
        result = auditor.qualification_reason(row)
        assert result in ["player_points_high_quality_pass", "player_points_scoring_quality_pass", "player_points_edge_confidence_pass"]
        assert result != "negative_edge_elite_reject"
        print("✓ negative edge + under selection valid")

    def test_negative_edge_over_selection_invalid(self):
        """Negative edge with 'over' selection should be rejected."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Invalid: edge < 0 but selection = over (contradiction)
        row = {
            "market_type": "player_points",
            "selection": "over",
            "edge": -3.5,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "28_34",
        }
        
        result = auditor.qualification_reason(row)
        assert result == "negative_edge_elite_reject"
        print("✓ negative edge + over selection rejected")

    def test_positive_edge_under_selection_invalid(self):
        """Positive edge with 'under' selection should be rejected."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Invalid: edge > 0 but selection = under (contradiction)
        row = {
            "market_type": "player_points",
            "selection": "under",
            "edge": 5.5,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "28_34",
        }
        
        result = auditor.qualification_reason(row)
        assert result == "negative_edge_elite_reject"
        print("✓ positive edge + under selection rejected")

    def test_zero_edge_over_selection_invalid(self):
        """Zero edge with 'over' selection should be rejected."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Invalid: edge = 0, selection = over (no edge)
        row = {
            "market_type": "player_points",
            "selection": "over",
            "edge": 0.0,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "28_34",
        }
        
        result = auditor.qualification_reason(row)
        assert result == "negative_edge_elite_reject"
        print("✓ zero edge + over selection rejected")

    def test_zero_edge_under_selection_invalid(self):
        """Zero edge with 'under' selection should be rejected."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Invalid: edge = 0, selection = under (no edge)
        row = {
            "market_type": "player_points",
            "selection": "under",
            "edge": 0.0,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "28_34",
        }
        
        result = auditor.qualification_reason(row)
        assert result == "negative_edge_elite_reject"
        print("✓ zero edge + under selection rejected")

    def test_other_markets_not_affected(self):
        """Edge validation should only apply to player_points."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        # Other player markets should not have edge validation
        row = {
            "market_type": "player_rebounds",
            "selection": "over",
            "edge": -5.0,  # Would be rejected for player_points
            "quality_score": 85.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
            "minutes_bucket": "34_plus",
        }
        
        result = auditor.qualification_reason(row)
        # Should not be rejected for edge reasons (only player_points has edge validation)
        assert result != "negative_edge_elite_reject"
        print("✓ other markets not affected by edge validation")

    def test_moneyline_not_affected(self):
        """Moneyline markets should not have edge validation."""
        from courtvision.runtime_audit import BoardAuditPolicy
        
        auditor = BoardAuditPolicy()
        
        row = {
            "market_type": "moneyline",
            "selection": "over",  # Doesn't apply to moneyline
            "edge": -10.0,
            "quality_score": 90.0,
            "confidence": 0.8,
            "recommendation": "",
            "rejection_reason": "",
        }
        
        result = auditor.qualification_reason(row)
        assert result != "negative_edge_elite_reject"
        print("✓ moneyline not affected by edge validation")


if __name__ == "__main__":
    print("\n=== Testing Elite Edge Validation ===\n")
    
    test_class = TestEliteEdgeValidation()
    test_class.test_positive_edge_over_selection_valid()
    test_class.test_negative_edge_under_selection_valid()
    test_class.test_negative_edge_over_selection_invalid()
    test_class.test_positive_edge_under_selection_invalid()
    test_class.test_zero_edge_over_selection_invalid()
    test_class.test_zero_edge_under_selection_invalid()
    test_class.test_other_markets_not_affected()
    test_class.test_moneyline_not_affected()
    
    print("\n=== All Elite Edge Validation Tests Passed ===\n")
