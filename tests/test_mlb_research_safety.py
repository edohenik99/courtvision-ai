from __future__ import annotations

import csv

import pytest

from scripts.run_kelly_stakes import _build_stake_row, _validate_columns, main


def _research_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "player_name": "Research Candidate",
        "sport": "MLB",
        "mode": "research",
        "market_type": "player_points",
        "selection": "over",
        "line": "0.5",
        "odds": "+350",
        "confidence": "0.95",
        "edge_pct": "0.25",
        "side_edge_pct": "0.25",
        "eligible_for_betting": False,
        "kelly_eligible": False,
        "betting_approval_status": "research_only_not_betting_approved",
        "calibrated_probability": "",
        "model_approval_status": "",
        "data_quality": "Sample data",
    }
    row.update(overrides)
    return row


def test_mlb_research_row_is_blocked_before_sizing() -> None:
    row = _research_row()
    edge_col = _validate_columns(list(row))

    result = _build_stake_row(row, edge_col, bankroll=1000.0)

    assert result.eligible is False
    assert result.stake_fraction == 0.0
    assert result.stake_amount == 0.0
    assert result.expected_value == 0.0
    assert "sport=MLB" in result.operator_note
    assert "mode=research" in result.operator_note
    assert "eligible_for_betting=false" in result.operator_note
    assert "kelly_eligible=false" in result.operator_note
    assert "missing_calibrated_probability" in result.operator_note
    assert "missing_model_approval" in result.operator_note
    assert "sample_data_source" in result.operator_note


def test_mlb_sport_guard_cannot_be_overridden_by_approval_like_provider_data() -> None:
    row = _research_row(
        mode="production",
        eligible_for_betting=True,
        kelly_eligible=True,
        calibrated_probability="0.99",
        model_approval_status="production_approved",
        model_approved=True,
        data_quality="live",
    )
    result = _build_stake_row(row, _validate_columns(list(row)), bankroll=1000.0)

    assert result.eligible is False
    assert result.stake_amount == 0.0
    assert "sport=MLB" in result.operator_note


def test_mlb_only_input_cannot_create_staking_artifact(tmp_path) -> None:
    input_path = tmp_path / "mlb_research.csv"
    output_path = tmp_path / "stakes.csv"
    row = _research_row()
    with input_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)

    with pytest.raises(SystemExit, match="only MLB/research/sample rows"):
        main(
            [
                "--prediction-date",
                "2026-06-19",
                "--input-csv",
                str(input_path),
                "--output-csv",
                str(output_path),
            ]
        )

    assert not output_path.exists()


def test_explicit_nba_row_retains_existing_staking_behavior() -> None:
    row = {
        "player_name": "NBA Candidate",
        "sport": "NBA",
        "market_type": "player_points",
        "selection": "over",
        "line": "20.5",
        "odds": "-110",
        "confidence": "0.75",
        "edge_pct": "0.10",
        "side_edge_pct": "0.10",
    }
    result = _build_stake_row(row, _validate_columns(list(row)), bankroll=1000.0)

    assert result.eligible is True
    assert result.stake_amount > 0.0
