from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

import courtvision_ai


def _stub_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.NullHandler())
    return logger


@pytest.fixture
def ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> courtvision_ai.CourtVisionAI:
    monkeypatch.setattr(courtvision_ai, "_get_logger", lambda _: _stub_logger("elite-context-gate-test"))
    return courtvision_ai.CourtVisionAI(out_dir=str(tmp_path / "outputs"))


def _candidate(
    *,
    player_name: str,
    selection: str,
    line: float,
    edge: float,
    score: float,
    caution: str,
    alignment: str,
    signal: str,
    team: str = "BOS",
    opponent: str = "PHI",
) -> dict[str, object]:
    return {
        "prediction_date": "2026-05-02",
        "market_type": "player_points",
        "entity_name": player_name,
        "player_name": player_name,
        "team": team,
        "team_abbr": team,
        "opponent": opponent,
        "game_id": f"{team}-{opponent}",
        "selection": selection,
        "sportsbook_line": line,
        "line": line,
        "model_projection": line + edge,
        "edge": edge,
        "edge_abs": abs(edge),
        "edge_pct": edge / line,
        "side_edge_pct": abs(edge / line),
        "confidence": 0.82,
        "quality_score": score,
        "selection_score": score,
        "elite_rank_score": score,
        "odds": -110,
        "minutes_avg": 34.0,
        "minutes_recent": 34.0,
        "player_profile_bucket": "star_high_usage",
        "is_live_market": True,
        "synthetic_line": False,
        "line_source": "live_market",
        "qualification_gate_mode": "core_pass",
        "context_caution_level": caution,
        "context_pick_alignment": alignment,
        "overall_context_signal": signal,
    }


def test_high_caution_conflicted_over_excluded_when_safer_under_exists(
    ai: courtvision_ai.CourtVisionAI,
) -> None:
    prepared = pd.DataFrame(
        [
            _candidate(
                player_name="Blocked Over",
                selection="over",
                line=20.5,
                edge=5.0,
                score=150.0,
                caution="high",
                alignment="conflicted",
                signal="supports_under",
            ),
            _candidate(
                player_name="Safer Under",
                selection="under",
                line=24.5,
                edge=-3.0,
                score=95.0,
                caution="low",
                alignment="aligned",
                signal="supports_under",
                team="PHI",
                opponent="BOS",
            ),
        ]
    )

    elite_df, full_market_df, trace = ai._build_final_operator_boards(prepared)

    assert full_market_df["player_name"].tolist() == ["Blocked Over", "Safer Under"]
    blocked_full_market = full_market_df.set_index("player_name").loc["Blocked Over"]
    assert blocked_full_market["final_elite_rejection_reason"] == "elite_reject_context_high_caution_over"
    assert blocked_full_market["kelly_projected_skip_reason"] == "context_high_caution_over"

    assert elite_df["player_name"].tolist() == ["Safer Under"]
    assert elite_df.iloc[0]["selection"].lower() == "under"
    assert elite_df.iloc[0]["final_elite_rejection_reason"] == ""
    assert trace["elite"]["elite_context_safety_gate"]["rejected_from_elite_count"] == 1


def test_all_high_caution_conflicted_overs_produce_empty_elite_board(
    ai: courtvision_ai.CourtVisionAI,
) -> None:
    prepared = pd.DataFrame(
        [
            _candidate(
                player_name="Blocked Over A",
                selection="over",
                line=16.5,
                edge=4.0,
                score=120.0,
                caution="high",
                alignment="conflicted",
                signal="supports_under",
                team="BOS",
                opponent="PHI",
            ),
            _candidate(
                player_name="Blocked Over B",
                selection="over",
                line=12.5,
                edge=3.0,
                score=115.0,
                caution="high",
                alignment="conflicted",
                signal="supports_under",
                team="PHI",
                opponent="BOS",
            ),
        ]
    )

    elite_df, full_market_df, trace = ai._build_final_operator_boards(prepared)

    assert elite_df.empty
    assert len(full_market_df) == 2
    assert set(full_market_df["final_elite_rejection_reason"]) == {
        "elite_reject_context_high_caution_over"
    }
    gate = trace["elite"]["elite_context_safety_gate"]
    assert gate["rejected_from_elite_count"] == 2
    assert gate["final_elite_count"] == 0
    assert gate["empty_no_bet"] is True


def test_player_points_elite_admission_records_context_gate_reason(
    ai: courtvision_ai.CourtVisionAI,
) -> None:
    prepared = pd.DataFrame(
        [
            _candidate(
                player_name="Blocked Over",
                selection="over",
                line=20.5,
                edge=5.0,
                score=150.0,
                caution="high",
                alignment="conflicted",
                signal="supports_under",
            ),
            _candidate(
                player_name="Safer Under",
                selection="under",
                line=24.5,
                edge=-3.0,
                score=95.0,
                caution="low",
                alignment="aligned",
                signal="supports_under",
                team="PHI",
                opponent="BOS",
            ),
        ]
    )

    _, _, trace = ai._build_final_operator_boards(prepared)
    admission = pd.DataFrame(trace["elite"]["player_points_elite_admission_rows"])

    assert not admission.empty
    expected_columns = {
        "player_name",
        "market_type",
        "selection",
        "line",
        "projection",
        "edge",
        "confidence",
        "quality_score",
        "context_pick_alignment",
        "context_caution_level",
        "overall_context_signal",
        "elite_admitted",
        "elite_rejection_reason",
    }
    assert expected_columns.issubset(admission.columns)

    by_player = admission.set_index("player_name")
    assert bool(by_player.loc["Blocked Over", "elite_admitted"]) is False
    assert by_player.loc["Blocked Over", "elite_rejection_reason"] == "elite_reject_context_high_caution_over"
    assert bool(by_player.loc["Safer Under", "elite_admitted"]) is True
