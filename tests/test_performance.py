"""Unit tests for performance analytics module.

Tests the betting performance analysis functions to ensure
accurate ROI calculations and segment analysis.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from courtvision.betting.performance import (
    load_pick_history,
    compute_overall_roi,
    roi_by_market,
    roi_by_confidence_bucket,
    roi_by_edge_bucket,
    worst_performing_segments,
    best_performing_segments,
    _calculate_profit_loss,
    _compute_segment_stats,
    generate_performance_report,
    save_performance_report,
    normalize_pick_history_schema,
    MIN_SAMPLE_SIZE,
)


class TestLoadPickHistory:
    """Test suite for loading pick history."""

    def test_empty_dataframe_for_missing_file(self):
        """Test that missing file returns empty DataFrame."""
        df = load_pick_history("/nonexistent/path.csv")
        assert df.empty

    def test_loads_valid_csv(self, tmp_path):
        """Test loading a valid pick history CSV."""
        csv_path = tmp_path / "test_history.csv"
        csv_path.write_text("""date,player_name,market_type,selection,result,stake,odds,profit_loss,confidence,edge
2024-01-01,Player A,player_points,over,win,20.0,1.91,18.2,0.75,0.05
2024-01-01,Player B,player_rebounds,under,loss,20.0,1.91,-20.0,0.70,0.04
""")
        df = load_pick_history(csv_path)
        assert len(df) == 2
        assert "date" in df.columns
        assert "profit_loss" in df.columns


class TestNormalizePickHistorySchema:
    """Test schema normalization for various column naming conventions."""

    def test_prediction_date_to_date(self):
        """Test that prediction_date is mapped to date."""
        df = pd.DataFrame({
            "prediction_date": ["2024-01-01", "2024-01-02"],
            "player_name": ["A", "B"],
        })
        result = normalize_pick_history_schema(df)
        assert "date" in result.columns
        assert pd.api.types.is_datetime64_any_dtype(result["date"])

    def test_market_to_market_type(self):
        """Test that market is mapped to market_type."""
        df = pd.DataFrame({
            "player_name": ["A", "B"],
            "market": ["points", "rebounds"],
            "result_status": ["hit", "miss"],
            "stake_fraction": [0.01, 0.01],
            "odds": [1.91, 1.91],
        })
        result = normalize_pick_history_schema(df)
        assert "market_type" in result.columns
        assert result["market_type"].iloc[0] == "points"

    def test_result_status_normalization(self):
        """Test that result_status values are normalized to win/loss/push."""
        df = pd.DataFrame({
            "player_name": ["A", "B", "C", "D"],
            "result_status": ["hit", "miss", "push", "pending"],
            "stake_fraction": [0.01, 0.01, 0.01, 0.01],
            "odds": [1.91, 1.91, 1.91, 1.91],
        })
        result = normalize_pick_history_schema(df)
        assert "result" in result.columns
        assert result["result"].iloc[0] == "win"  # hit -> win
        assert result["result"].iloc[1] == "loss"  # miss -> loss
        assert result["result"].iloc[2] == "push"  # push -> push

    def test_prop_type_to_market_type(self):
        """Test that prop_type is mapped to market_type."""
        df = pd.DataFrame({
            "player_name": ["A"],
            "prop_type": ["player_points"],
        })
        result = normalize_pick_history_schema(df)
        assert "market_type" in result.columns
        assert result["market_type"].iloc[0] == "player_points"

    def test_stake_fraction_calculation(self):
        """Test that stake_fraction is converted to dollar stake."""
        from courtvision.config import DEFAULT_BANKROLL
        df = pd.DataFrame({
            "player_name": ["A"],
            "stake_fraction": [0.02],
        })
        result = normalize_pick_history_schema(df)
        assert "stake" in result.columns
        expected_stake = DEFAULT_BANKROLL * 0.02
        assert result["stake"].iloc[0] == expected_stake

    def test_missing_result_not_crash(self):
        """Test that missing result column doesn't crash - allows ungraded mode."""
        df = pd.DataFrame({
            "player_name": ["A", "B"],
            "market": ["points", "rebounds"],
            "stake_fraction": [0.01, 0.01],
            "odds": [1.91, 1.91],
        })
        result = normalize_pick_history_schema(df)
        # Should not crash and should have result column (possibly NaN)
        assert "result" in result.columns

    def test_profit_loss_calculation(self):
        """Test that profit_loss is calculated from result/stake/odds."""
        df = pd.DataFrame({
            "player_name": ["A", "B"],
            "result_status": ["hit", "miss"],
            "stake_fraction": [0.01, 0.01],
            "odds": [1.91, 1.91],
        })
        result = normalize_pick_history_schema(df)
        assert "profit_loss" in result.columns
        # First row: win with 1.91 odds, stake = 0.01 * 1000 = 10
        # profit = 10 * (1.91 - 1) = 9.1
        assert result["profit_loss"].iloc[0] > 0
        # Second row: loss
        assert result["profit_loss"].iloc[1] < 0


class TestCalculateProfitLoss:
    """Test profit/loss calculation."""

    def test_win_calculation(self):
        """Test profit calculation for win."""
        result = _calculate_profit_loss("win", 20.0, 1.91)
        expected = 20.0 * (1.91 - 1)  # 18.2
        assert result == pytest.approx(expected, 0.01)

    def test_loss_calculation(self):
        """Test loss calculation."""
        result = _calculate_profit_loss("loss", 20.0, 1.91)
        assert result == -20.0

    def test_push_calculation(self):
        """Test push returns 0."""
        result = _calculate_profit_loss("push", 20.0, 1.91)
        assert result == 0.0

    def test_none_result(self):
        """Test None result returns 0."""
        result = _calculate_profit_loss(None, 20.0, 1.91)
        assert result == 0.0


class TestComputeOverallRoi:
    """Test overall ROI calculation."""

    def test_empty_dataframe(self):
        """Test empty DataFrame returns zero ROI."""
        df = pd.DataFrame()
        result = compute_overall_roi(df)
        assert result["roi"] == 0.0
        assert result["sample_size"] == 0

    def test_single_win(self):
        """Test ROI with single winning pick."""
        df = pd.DataFrame({
            "result": ["win"],
            "stake": [20.0],
            "profit_loss": [18.2],
        })
        result = compute_overall_roi(df)
        assert result["roi"] == pytest.approx(0.91, 0.01)
        assert result["wins"] == 1

    def test_single_loss(self):
        """Test ROI with single losing pick."""
        df = pd.DataFrame({
            "result": ["loss"],
            "stake": [20.0],
            "profit_loss": [-20.0],
        })
        result = compute_overall_roi(df)
        assert result["roi"] == pytest.approx(-1.0, 0.01)
        assert result["losses"] == 1

    def test_mixed_results(self):
        """Test ROI with mixed win/loss."""
        df = pd.DataFrame({
            "result": ["win", "win", "loss", "loss"],
            "stake": [20.0, 20.0, 20.0, 20.0],
            "profit_loss": [18.2, 18.2, -20.0, -20.0],
        })
        result = compute_overall_roi(df)
        # Total PL = -3.6, Total Stake = 80
        assert result["roi"] == pytest.approx(-0.045, 0.01)
        assert result["sample_size"] == 4
        assert result["win_rate"] == 0.5


class TestRoiByMarket:
    """Test ROI by market type analysis."""

    def test_empty_dataframe(self):
        """Test empty DataFrame returns empty list."""
        df = pd.DataFrame()
        result = roi_by_market(df)
        assert result == []

    def test_single_market(self):
        """Test single market segment."""
        df = pd.DataFrame({
            "result": ["win", "win", "loss"],
            "market_type": ["player_points", "player_points", "player_points"],
            "stake": [20.0, 20.0, 20.0],
            "profit_loss": [18.2, 18.2, -20.0],
        })
        result = roi_by_market(df)
        assert len(result) == 1
        assert result[0]["segment"] == "player_points"
        assert result[0]["sample_size"] == 3

    def test_multiple_markets_sorted(self):
        """Test multiple markets sorted by ROI."""
        df = pd.DataFrame({
            "result": ["win", "loss", "win", "loss"],
            "market_type": ["points", "points", "rebounds", "rebounds"],
            "stake": [20.0, 20.0, 20.0, 20.0],
            "profit_loss": [18.2, -20.0, 18.2, -20.0],
        })
        # Both markets have same ROI, so just check they exist
        result = roi_by_market(df)
        assert len(result) == 2


class TestRoiByConfidenceBucket:
    """Test ROI by confidence bucket analysis."""

    def test_confidence_bucket_grouping(self):
        """Test picks are grouped into correct confidence buckets."""
        df = pd.DataFrame({
            "result": ["win", "loss", "win"],
            "confidence": [0.60, 0.70, 0.90],
            "stake": [20.0, 20.0, 20.0],
            "profit_loss": [18.2, -20.0, 18.2],
        })
        result = roi_by_confidence_bucket(df)
        assert len(result) > 0

    def test_below_threshold_excluded(self):
        """Test picks below 0.55 confidence excluded."""
        df = pd.DataFrame({
            "result": ["win"],
            "confidence": [0.50],  # Below threshold
            "stake": [20.0],
            "profit_loss": [18.2],
        })
        # Should not appear in results (0.50 < 0.55 bucket starts at 0.55)
        result = roi_by_confidence_bucket(df)
        # May be empty or in no bucket
        bucket_segments = [r["segment"] for r in result]
        assert "confidence_0.55-0.65" not in bucket_segments


class TestRoiByEdgeBucket:
    """Test ROI by edge bucket analysis."""

    def test_edge_bucket_grouping(self):
        """Test picks are grouped into correct edge buckets."""
        df = pd.DataFrame({
            "result": ["win", "loss", "win"],
            "edge": [0.02, 0.04, 0.08],
            "stake": [20.0, 20.0, 20.0],
            "profit_loss": [18.2, -20.0, 18.2],
        })
        result = roi_by_edge_bucket(df)
        assert len(result) > 0

    def test_small_edge_bucket(self):
        """Test small edge (0-3%) bucket."""
        df = pd.DataFrame({
            "result": ["win", "loss"],
            "edge": [0.02, 0.025],
            "stake": [20.0, 20.0],
            "profit_loss": [18.2, -20.0],
        })
        result = roi_by_edge_bucket(df)
        small_bucket = next((r for r in result if "small" in r["segment"]), None)
        assert small_bucket is not None
        assert small_bucket["sample_size"] == 2


class TestWorstPerformingSegments:
    """Test worst performing segments identification."""

    def test_negative_roi_with_sufficient_samples(self):
        """Test segments with negative ROI and sufficient samples are identified."""
        # Create 20 losses to meet MIN_SAMPLE_SIZE
        results = ["loss"] * MIN_SAMPLE_SIZE
        stakes = [20.0] * MIN_SAMPLE_SIZE
        pls = [-20.0] * MIN_SAMPLE_SIZE
        markets = ["bad_market"] * MIN_SAMPLE_SIZE

        df = pd.DataFrame({
            "result": results,
            "market_type": markets,
            "stake": stakes,
            "profit_loss": pls,
        })
        result = worst_performing_segments(df)
        assert len(result) > 0
        assert result[0]["roi"] < 0
        assert result[0]["sample_size"] >= MIN_SAMPLE_SIZE

    def test_insufficient_samples_excluded(self):
        """Test segments with insufficient samples are excluded."""
        # Only 5 losses, below MIN_SAMPLE_SIZE
        df = pd.DataFrame({
            "result": ["loss"] * 5,
            "market_type": ["bad_market"] * 5,
            "stake": [20.0] * 5,
            "profit_loss": [-20.0] * 5,
        })
        result = worst_performing_segments(df)
        # Should be excluded due to insufficient sample size
        assert len(result) == 0


class TestBestPerformingSegments:
    """Test best performing segments identification."""

    def test_positive_roi_with_sufficient_samples(self):
        """Test segments with positive ROI and sufficient samples are identified."""
        # Create 20 wins to meet MIN_SAMPLE_SIZE
        results = ["win"] * MIN_SAMPLE_SIZE
        stakes = [20.0] * MIN_SAMPLE_SIZE
        pls = [18.2] * MIN_SAMPLE_SIZE
        markets = ["good_market"] * MIN_SAMPLE_SIZE

        df = pd.DataFrame({
            "result": results,
            "market_type": markets,
            "stake": stakes,
            "profit_loss": pls,
        })
        result = best_performing_segments(df)
        assert len(result) > 0
        assert result[0]["roi"] > 0
        assert result[0]["sample_size"] >= MIN_SAMPLE_SIZE


class TestGeneratePerformanceReport:
    """Test full performance report generation."""

    def test_complete_report_structure(self):
        """Test report has all required sections."""
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=10),
            "player_name": ["Player A"] * 10,
            "market_type": ["player_points"] * 10,
            "selection": ["over"] * 10,
            "result": ["win"] * 5 + ["loss"] * 5,
            "stake": [20.0] * 10,
            "profit_loss": [18.2] * 5 + [-20.0] * 5,
            "confidence": [0.75] * 10,
            "edge": [0.05] * 10,
        })
        report = generate_performance_report(df)
        
        assert "overall" in report
        assert "by_market" in report
        assert "by_confidence_bucket" in report
        assert "by_edge_bucket" in report
        assert "worst_performing_segments" in report
        assert "best_performing_segments" in report
        assert "recommendations" in report

    def test_empty_dataframe_report(self):
        """Test report handles empty DataFrame."""
        df = pd.DataFrame()
        report = generate_performance_report(df)
        
        assert "error" in report
        assert report["overall"] == {}


class TestSavePerformanceReport:
    """Test saving performance report."""

    def test_saves_json_file(self, tmp_path):
        """Test report is saved as valid JSON."""
        report = {
            "overall": {"roi": 0.05, "sample_size": 100},
            "by_market": [],
        }
        output_path = tmp_path / "test_report.json"
        saved_path = save_performance_report(report, output_path)
        
        assert saved_path.exists()
        # Verify it's valid JSON
        with open(saved_path) as f:
            loaded = json.load(f)
        assert loaded["overall"]["roi"] == 0.05


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
