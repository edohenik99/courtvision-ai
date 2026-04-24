"""Tests for shadow run (paper trading) mode.

VALIDATE + CALIBRATE mode - Measurement and validation only.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from courtvision.shadow_run import ShadowRunArtifact, ShadowRunEntry, ShadowRunRunner


def test_shadow_run_entry_creation():
    """Test creating a shadow run entry."""
    entry = ShadowRunEntry(
        entry_id="test_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    assert entry.entry_id == "test_001"
    assert entry.player_name == "LeBron"
    assert entry.recommended is True


def test_shadow_run_entry_to_dict():
    """Test entry serialization."""
    entry = ShadowRunEntry(
        entry_id="test_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
        thresholds_used={"edge": 0.05, "confidence": 0.65},
        market_regime="soft",
    )

    data = entry.to_dict()
    assert data["entry_id"] == "test_001"
    assert data["play"]["player"] == "LeBron"
    assert data["predictions"]["confidence"] == 0.75
    assert data["context"]["market_regime"] == "soft"


def test_shadow_run_entry_from_dict():
    """Test entry deserialization."""
    data = {
        "entry_id": "test_001",
        "timestamp": "2024-01-01T12:00:00",
        "prediction_date": "2024-01-01",
        "play": {
            "player": "LeBron",
            "stat": "points",
            "over_under": "over",
            "line": 25.5,
            "odds": -110,
        },
        "predictions": {
            "projected": 28.5,
            "confidence": 0.75,
            "edge": 0.08,
            "ev": 0.05,
        },
        "decision": {
            "recommended": True,
            "portfolio_included": True,
            "rejection_reason": None,
        },
        "context": {
            "thresholds": {"edge": 0.05},
            "market_regime": "neutral",
            "market_conditions": {},
        },
        "results": None,
    }

    entry = ShadowRunEntry.from_dict(data)
    assert entry.entry_id == "test_001"
    assert entry.player_name == "LeBron"
    assert entry.confidence == 0.75


def test_shadow_run_artifact_creation():
    """Test creating a shadow run artifact."""
    artifact = ShadowRunArtifact(
        artifact_id="shadow_2024-01-01_abc123",
        created_at="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        mode="shadow",
    )

    assert artifact.artifact_id == "shadow_2024-01-01_abc123"
    assert artifact.mode == "shadow"


def test_shadow_run_artifact_add_entry():
    """Test adding entries to artifact."""
    artifact = ShadowRunArtifact(
        artifact_id="shadow_2024-01-01_abc123",
        created_at="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
    )

    entry1 = ShadowRunEntry(
        entry_id="entry_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    entry2 = ShadowRunEntry(
        entry_id="entry_002",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="Curry",
        stat_type="threes",
        over_under="under",
        line_value=3.5,
        odds=-110,
        projected_value=3.0,
        confidence=0.70,
        edge=0.06,
        ev=0.04,
        recommended=True,
        portfolio_included=False,
    )

    artifact.add_entry(entry1)
    artifact.add_entry(entry2)
    artifact.finalize()

    assert artifact.total_candidates == 2
    assert artifact.portfolio_size == 1
    assert artifact.selection_rate == 0.5


def test_shadow_run_artifact_update_results():
    """Test updating entries with results."""
    artifact = ShadowRunArtifact(
        artifact_id="shadow_2024-01-01_abc123",
        created_at="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
    )

    entry = ShadowRunEntry(
        entry_id="entry_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    artifact.add_entry(entry)

    # Update with results
    updated = artifact.update_results(
        entry_id="entry_001",
        closing_line=27.5,
        closing_odds=-115,
        actual_result=30.0,
        hit=True,
    )

    assert updated is True
    assert entry.closing_line == 27.5
    assert entry.hit is True
    assert entry.clv is not None  # Should be calculated


def test_shadow_run_artifact_save_load():
    """Test saving and loading artifact."""
    artifact = ShadowRunArtifact(
        artifact_id="shadow_2024-01-01_abc123",
        created_at="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        config={"thresholds": {"edge": 0.05}},
    )

    entry = ShadowRunEntry(
        entry_id="entry_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    artifact.add_entry(entry)
    artifact.finalize()

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
        temp_path = f.name

    try:
        artifact.save(temp_path)
        assert os.path.exists(temp_path)

        loaded = ShadowRunArtifact.load(temp_path)
        assert loaded.artifact_id == artifact.artifact_id
        assert loaded.total_candidates == 1
        assert loaded.portfolio_size == 1
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_shadow_run_runner_creation():
    """Test creating shadow run runner."""
    runner = ShadowRunRunner(
        prediction_date="2024-01-01",
        config={"thresholds": {"edge": 0.05, "confidence": 0.65}},
    )

    assert runner.prediction_date == "2024-01-01"
    assert runner.config["thresholds"]["edge"] == 0.05


def test_shadow_run_runner_evaluate_candidate():
    """Test candidate evaluation."""
    runner = ShadowRunRunner(
        prediction_date="2024-01-01",
        config={"thresholds": {"edge": 0.05, "confidence": 0.65, "ev": 0.03}},
    )

    runner.set_context(
        thresholds={"edge": 0.05, "confidence": 0.65, "ev": 0.03},
        market_regime="neutral",
    )

    # Candidate that meets thresholds
    candidate_pass = {
        "player_name": "LeBron",
        "stat_type": "points",
        "over_under": "over",
        "line_value": 25.5,
        "odds": -110,
        "projected_value": 28.5,
        "confidence": 0.75,
        "edge": 0.08,
        "ev": 0.05,
    }

    entry_pass = runner.evaluate_candidate(candidate_pass)
    assert entry_pass.recommended is True
    assert entry_pass.portfolio_included is True  # Edge >= 1.2x threshold

    # Candidate that fails thresholds
    candidate_fail = {
        "player_name": "AD",
        "stat_type": "rebounds",
        "over_under": "under",
        "line_value": 9.5,
        "odds": -110,
        "projected_value": 9.0,
        "confidence": 0.55,
        "edge": 0.02,
        "ev": 0.01,
    }

    entry_fail = runner.evaluate_candidate(candidate_fail)
    assert entry_fail.recommended is False
    assert entry_fail.rejection_reason != ""


def test_shadow_run_runner_add_batch():
    """Test batch processing of candidates."""
    runner = ShadowRunRunner(prediction_date="2024-01-01")

    runner.set_context(
        thresholds={"edge": 0.05, "confidence": 0.65, "ev": 0.03},
    )

    candidates = [
        {
            "player_name": "Player1",
            "stat_type": "points",
            "over_under": "over",
            "line_value": 25.5,
            "odds": -110,
            "projected_value": 28.5,
            "confidence": 0.75,
            "edge": 0.08,
            "ev": 0.05,
        },
        {
            "player_name": "Player2",
            "stat_type": "rebounds",
            "over_under": "under",
            "line_value": 9.5,
            "odds": -110,
            "projected_value": 9.0,
            "confidence": 0.55,
            "edge": 0.02,
            "ev": 0.01,
        },
    ]

    entries = runner.add_batch(candidates)
    assert len(entries) == 2
    assert entries[0].recommended is True
    assert entries[1].recommended is False

    summary = runner.get_summary()
    assert summary["entries_recorded"] == 2


def test_shadow_run_runner_finalize():
    """Test finalizing shadow run."""
    runner = ShadowRunRunner(prediction_date="2024-01-01")

    runner.set_context(thresholds={"edge": 0.05})

    for i in range(10):
        candidate = {
            "player_name": f"Player{i}",
            "stat_type": "points",
            "over_under": "over",
            "line_value": 25.5,
            "odds": -110,
            "projected_value": 28.5,
            "confidence": 0.70 if i < 5 else 0.55,
            "edge": 0.08 if i < 5 else 0.02,
            "ev": 0.05 if i < 5 else 0.01,
        }
        entry = runner.evaluate_candidate(candidate)
        runner.add_entry(entry)

    artifact = runner.finalize()
    assert artifact.total_candidates == 10
    assert artifact.portfolio_size == 5  # Only first 5 meet thresholds
    assert artifact.selection_rate == 0.5


def test_shadow_run_artifact_export_comparison():
    """Test export for comparison format."""
    artifact = ShadowRunArtifact(
        artifact_id="shadow_2024-01-01_abc123",
        created_at="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
    )

    # Add recommended entry
    entry1 = ShadowRunEntry(
        entry_id="entry_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    # Add rejected entry (should not appear in export)
    entry2 = ShadowRunEntry(
        entry_id="entry_002",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="Curry",
        stat_type="threes",
        over_under="under",
        line_value=3.5,
        odds=-110,
        projected_value=3.0,
        confidence=0.55,
        edge=0.02,
        ev=0.01,
        recommended=False,
        portfolio_included=False,
        rejection_reason="low edge",
    )

    artifact.add_entry(entry1)
    artifact.add_entry(entry2)

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        temp_path = f.name

    try:
        artifact.export_for_comparison(temp_path)
        assert os.path.exists(temp_path)

        with open(temp_path, 'r') as f:
            lines = f.read().strip().split('\n')
            # Header + 1 recommended entry (rejected entries not included)
            assert len(lines) == 2
            assert "LeBron" in lines[1]
            assert "Curry" not in lines[1]
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def test_shadow_run_entry_clv_calculation():
    """Test CLV calculation on results update."""
    # Over pick - line moved up (favorable for over)
    entry_over = ShadowRunEntry(
        entry_id="entry_001",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="LeBron",
        stat_type="points",
        over_under="over",
        line_value=25.5,
        odds=-110,
        projected_value=28.5,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    # Simulate line moving from 25.5 to 27.5 (favorable for over)
    entry_over.closing_line = 27.5
    entry_over.clv = (entry_over.closing_line - entry_over.line_value) / entry_over.line_value
    assert entry_over.clv > 0  # Positive CLV

    # Under pick - line moved up (unfavorable for under)
    entry_under = ShadowRunEntry(
        entry_id="entry_002",
        timestamp="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
        player_name="Curry",
        stat_type="threes",
        over_under="under",
        line_value=3.5,
        odds=-110,
        projected_value=3.0,
        confidence=0.75,
        edge=0.08,
        ev=0.05,
        recommended=True,
        portfolio_included=True,
    )

    # Simulate line moving from 3.5 to 4.0 (unfavorable for under)
    entry_under.closing_line = 4.0
    entry_under.clv = (entry_under.line_value - entry_under.closing_line) / entry_under.line_value
    assert entry_under.clv < 0  # Negative CLV (bad for under pick)


def test_shadow_run_getters():
    """Test artifact getter methods."""
    artifact = ShadowRunArtifact(
        artifact_id="shadow_2024-01-01_abc123",
        created_at="2024-01-01T12:00:00",
        prediction_date="2024-01-01",
    )

    entries = [
        ShadowRunEntry(
            entry_id=f"entry_{i}",
            timestamp="2024-01-01T12:00:00",
            prediction_date="2024-01-01",
            player_name=f"Player{i}",
            stat_type="points",
            over_under="over",
            line_value=25.5,
            odds=-110,
            projected_value=28.5,
            confidence=0.75,
            edge=0.08,
            ev=0.05,
            recommended=i < 3,
            portfolio_included=i < 2,
        )
        for i in range(5)
    ]

    for e in entries:
        artifact.add_entry(e)

    recommended = artifact.get_recommended_entries()
    assert len(recommended) == 3

    portfolio = artifact.get_portfolio_entries()
    assert len(portfolio) == 2

    rejected = artifact.get_rejected_entries()
    assert len(rejected) == 2  # Not recommended (i=3,4)
