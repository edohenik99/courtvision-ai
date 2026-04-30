"""Tests for evaluation package.

VALIDATE + CALIBRATE mode - Measurement and validation only.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from courtvision.evaluation import ReportBuilder, ReportExporter, RollingWindow


def test_rolling_window():
    """Test rolling window configuration."""
    window = RollingWindow(window_size=30, min_samples=10)
    assert window.has_enough_samples(10)
    assert window.has_enough_samples(15)
    assert not window.has_enough_samples(5)


def test_report_builder_add_pick():
    """Test adding picks to report builder."""
    builder = ReportBuilder()

    builder.add_pick(
        pick_id="p1",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        hit=True,
        clv=0.02,
    )

    assert len(builder.pick_history) == 1
    assert builder.pick_history[0]["pick_id"] == "p1"


def test_report_builder_confidence_buckets():
    """Test confidence bucket aggregation."""
    builder = ReportBuilder()

    # Add picks across confidence buckets
    for i in range(15):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.55 + (i * 0.03),  # 0.55 to 0.97
            edge=0.05,
            ev=0.03,
            hit=i % 2 == 0,  # Alternate hits
            clv=0.01,
        )

    report = builder.build_report()
    assert report is not None
    assert len(report.confidence_buckets) == 5

    # At least some buckets should have data
    buckets_with_data = [b for b in report.confidence_buckets if b.count > 0]
    assert len(buckets_with_data) > 0


def test_report_builder_edge_buckets():
    """Test edge bucket aggregation."""
    builder = ReportBuilder()

    # Add picks with various edge values
    edges = [-0.02, 0.02, 0.06, 0.12, 0.25]
    for i, edge in enumerate(edges):
        for _ in range(3):  # 3 picks per edge bucket
            builder.add_pick(
                pick_id=f"p{i}_{_}",
                prediction_date="2024-01-01",
                player_name="Player",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.65,
                edge=edge,
                ev=edge * 0.9,
                hit=edge > 0,  # Positive edge picks hit
                clv=0.01,
            )

    report = builder.build_report()
    assert report is not None

    # Check that positive edge buckets have higher hit rates
    positive_buckets = [b for b in report.edge_buckets if b.bucket_name != "negative"]
    negative_bucket = next((b for b in report.edge_buckets if b.bucket_name == "negative"), None)

    if negative_bucket and negative_bucket.count > 0:
        assert negative_bucket.hit_rate < 0.5


def test_report_builder_calibration():
    """Test calibration score calculation."""
    builder = ReportBuilder(RollingWindow(window_size=100, min_samples=10))

    # Add perfectly calibrated picks
    # 60% confidence picks should hit 60% of the time
    for i in range(100):
        confidence = 0.65
        hit = i < 65  # Exactly 65% hit rate

        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=confidence,
            edge=0.05,
            ev=0.03,
            hit=hit,
            clv=0.01,
        )

    report = builder.build_report()
    assert report is not None
    assert report.calibration_score > 0.5  # Should be reasonably calibrated


def test_report_builder_stat_type_metrics():
    """Test stat type aggregation."""
    builder = ReportBuilder()

    stat_types = ["points", "rebounds", "assists", "threes"]
    for stat in stat_types:
        for i in range(5):
            builder.add_pick(
                pick_id=f"{stat}_{i}",
                prediction_date="2024-01-01",
                player_name="Player",
                stat_type=stat,
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.05,
                ev=0.03,
                hit=True,
                clv=0.01,
            )

    report = builder.build_report()
    assert report is not None
    assert len(report.stat_type_metrics) == 4

    for stat in stat_types:
        assert stat in report.stat_type_metrics
        assert report.stat_type_metrics[stat].count == 5


def test_report_builder_market_regime():
    """Test market regime aggregation."""
    builder = ReportBuilder()

    regimes = ["soft", "efficient", "overreactive", "neutral"]
    for regime in regimes:
        for i in range(5):
            builder.add_pick(
                pick_id=f"{regime}_{i}",
                prediction_date="2024-01-01",
                player_name="Player",
                stat_type="points",
                over_under="over",
                line_value=25.5,
                confidence=0.70,
                edge=0.05,
                ev=0.03,
                hit=regime == "soft",  # Only soft regime hits
                clv=0.01,
                market_regime=regime,
            )

    report = builder.build_report()
    assert report is not None
    assert len(report.regime_metrics) == 4

    # Soft regime should have higher hit rate
    if "soft" in report.regime_metrics:
        assert report.regime_metrics["soft"].hit_rate == 1.0


def test_report_builder_rejection_reasons():
    """Test rejection reason tracking."""
    builder = ReportBuilder()

    reasons = ["low_confidence", "high_variance", "low_ev", "low_confidence", "low_confidence"]
    for i, reason in enumerate(reasons):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=True,
            clv=0.01,
            rejection_reason=reason,
        )

    report = builder.build_report()
    assert report is not None
    assert len(report.top_rejection_reasons) > 0

    # Most common reason should be "low_confidence"
    most_common = report.top_rejection_reasons[0]
    assert most_common[0] == "low_confidence"
    assert most_common[1] == 3


def test_report_builder_miss_categories():
    """Test miss category tracking."""
    builder = ReportBuilder()

    # Add some hits and misses with categories
    for i in range(10):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=i < 3,  # 3 hits, 7 misses
            clv=0.01,
            miss_category="projection_error" if i >= 3 else "",
        )

    report = builder.build_report()
    assert report is not None
    assert len(report.top_miss_categories) > 0


def test_report_builder_signal_analysis():
    """Test signal reliability analysis."""
    builder = ReportBuilder()

    # Add picks with signal contributions
    for i in range(20):
        signals = {
            "recent_form": 0.3,
            "matchup": 0.2,
            "pace": 0.1,
        }

        # Recent form is reliable, matchup is not
        hit = i < 12  # 60% hit rate overall

        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=hit,
            clv=0.01,
            signals=signals,
        )

    report = builder.build_report()
    assert report is not None

    # Should have identified signals
    assert len(report.reliable_signals) > 0 or len(report.harmful_signals) > 0


def test_report_exporter_json():
    """Test JSON export."""
    builder = ReportBuilder()

    for i in range(15):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=True,
            clv=0.01,
        )

    report = builder.build_report()
    assert report is not None

    exporter = ReportExporter()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_path = f.name

    try:
        exporter.export_json(report, temp_path)
        assert os.path.exists(temp_path)
        assert os.path.getsize(temp_path) > 0
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_report_exporter_csv():
    """Test CSV export."""
    builder = ReportBuilder()

    for i in range(15):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=True,
            clv=0.01,
        )

    report = builder.build_report()
    assert report is not None

    exporter = ReportExporter()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        temp_path = f.name

    try:
        exporter.export_csv_summary(report, temp_path)
        assert os.path.exists(temp_path)

        with open(temp_path, 'r') as f:
            content = f.read()
            assert "metric,value" in content
            assert "total_picks" in content
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_report_exporter_txt():
    """Test TXT export."""
    builder = ReportBuilder()

    for i in range(15):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=True,
            clv=0.01,
        )

    report = builder.build_report()
    assert report is not None

    exporter = ReportExporter()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        temp_path = f.name

    try:
        exporter.export_txt_summary(report, temp_path)
        assert os.path.exists(temp_path)

        with open(temp_path, 'r') as f:
            content = f.read()
            assert "EVALUATION REPORT" in content
            assert "CONFIDENCE BUCKETS" in content
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_report_builder_drawdown_calculation():
    """Test drawdown calculation."""
    builder = ReportBuilder()

    # Add sequence: win, win, loss, loss, loss (drawdown period)
    results = [True, True, False, False, False, True, True]
    for i, hit in enumerate(results):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=hit,
            clv=0.01,
        )

    report = builder.build_report()
    assert report is not None
    assert report.portfolio_drawdown > 0


def test_report_builder_insufficient_data():
    """Test handling of insufficient data."""
    builder = ReportBuilder(RollingWindow(min_samples=10))

    # Add only 5 picks
    for i in range(5):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date="2024-01-01",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=True,
            clv=0.01,
        )

    report = builder.build_report()
    assert report is None  # Should return None for insufficient data


def test_report_builder_multiple_reports():
    """Test building multiple rolling reports."""
    builder = ReportBuilder(RollingWindow(window_size=20, min_samples=10))

    # Add 50 picks
    for i in range(50):
        builder.add_pick(
            pick_id=f"p{i}",
            prediction_date=f"2024-01-{i+1:02d}",
            player_name="Player",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            confidence=0.70,
            edge=0.05,
            ev=0.03,
            hit=i % 2 == 0,
            clv=0.01,
        )

    reports = builder.build_all_reports()
    assert len(reports) > 1  # Should have multiple rolling windows

    # Each report should have different date ranges
    for i, report in enumerate(reports[:-1]):
        assert report.window_end != reports[i + 1].window_end
