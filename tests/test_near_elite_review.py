from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from courtvision.reporting.near_elite_review import (
    NO_AUTO_STAKE_POLICY,
    REVIEW_LANE,
    REVIEW_ONLY_ACTION,
    REVIEW_ONLY_NOTE,
    build_near_elite_review,
    write_near_elite_review,
)
from courtvision.reporting.quality_summary import write_quality_summary_outputs
from scripts.write_daily_summary import write_daily_summary_outputs
from scripts.write_operator_card import write_operator_card_outputs


def _write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _candidate(
    player_name: str,
    *,
    prediction_date: str = "2026-05-23",
    edge: float = 3.5,
    confidence: float = 0.74,
    quality_score: float = 52.0,
    selection: str = "over",
    market_type: str = "player_points",
    line: float = 21.5,
    **extra: object,
) -> dict:
    row = {
        "prediction_date": prediction_date,
        "player_id": player_name.lower().replace(" ", "-"),
        "player_name": player_name,
        "team_abbr": "BOS",
        "opponent": "NYK",
        "game_id": "game-1",
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook_line": line,
        "model_projection": line + edge,
        "odds": -110,
        "edge": edge,
        "confidence": confidence,
        "quality_score": quality_score,
        "selection_score": 80.0,
        "final_elite_rejection_reason": "market_filtered_by_elite_policy",
        "row_identity_quarantined": False,
        "player_identity_valid": True,
        "review_before_bet": False,
        "manual_review_required": False,
        "same_opponent_under_warning": False,
        "operator_action": "OK_TO_CONSIDER",
        "stake_policy": "NORMAL",
    }
    row.update(extra)
    return row


def _quality_payload(prediction_date: str, *, full_market_count: int, near_elite_count: int) -> dict:
    return {
        "run_identity": {"prediction_date": prediction_date},
        "run_health_status": "NO_BET",
        "run_health_reason": "No stakeable picks are available.",
        "slate_provider_counts": {
            "games_count": 1,
            "raw_odds_rows_count": full_market_count,
            "normalized_odds_rows_count": full_market_count,
            "live_odds_count": full_market_count,
            "synthetic_or_fallback_odds_count": 0,
            "provider_breakdown": {"line_source": {"fixture_live_market": full_market_count}},
        },
        "candidate_funnel": {
            "raw_candidates_count": full_market_count,
            "rejected_candidates_count": full_market_count,
            "full_market_board_count": full_market_count,
            "elite_board_count": 0,
            "sgp_board_count": 0,
            "kelly_rows_count": 0,
            "near_elite_review_count": near_elite_count,
        },
        "near_elite_review": {
            "row_count": near_elite_count,
            "review_only": True,
            "kelly_eligible": False,
            "note": REVIEW_ONLY_NOTE,
        },
        "kelly_safety_summary": {
            "total_rows": 0,
            "kelly_eligible_count": 0,
            "manual_review_required_count": 0,
            "review_before_bet_count": 0,
            "review_policy_hold_count": 0,
        },
        "manual_review_required_count": 0,
        "same_opponent_under_warning_count": 0,
        "high_caution_over_watchlist": {"row_count": 0},
        "date_isolation_check": {"status": "ok"},
    }


def test_near_elite_review_rows_are_produced_from_qualifying_non_elite_full_market_rows(tmp_path: Path) -> None:
    prediction_date = "2026-05-23"
    runtime_root = tmp_path / "runtime"
    operator = runtime_root / "operator"
    qualifying = _candidate("Near Elite")
    elite_duplicate = _candidate("Already Elite", edge=5.0)
    rows = [
        qualifying,
        elite_duplicate,
        _candidate("Wrong Side", selection="under"),
        _candidate("Low Edge", edge=2.99),
        _candidate("Low Confidence", confidence=0.69),
        _candidate("Low Quality", quality_score=47.9),
    ]
    _write_csv(operator / f"full_market_board_{prediction_date}.csv", rows)
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [elite_duplicate])

    output_path, review = write_near_elite_review(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )

    assert output_path == operator / f"near_elite_review_{prediction_date}.csv"
    assert review["player_name"].tolist() == ["Near Elite"]
    row = review.iloc[0]
    assert row["operator_action"] == REVIEW_ONLY_ACTION
    assert row["stake_policy"] == NO_AUTO_STAKE_POLICY
    assert str(row["kelly_eligible"]).lower() == "false"
    assert row["review_lane"] == REVIEW_LANE
    assert "source_rejection_reason=market_filtered_by_elite_policy" in row["review_reason"]


def test_near_elite_review_excludes_hard_blocked_manual_review_and_identity_rows() -> None:
    rows = [
        _candidate("Clean Candidate"),
        _candidate("Identity Quarantined", identity_quarantined=True),
        _candidate("Row Identity Quarantined", row_identity_quarantined=True),
        _candidate("Invalid Identity", player_identity_valid=False),
        _candidate("Review Before Bet", review_before_bet=True),
        _candidate("Manual Review", manual_review_required=True),
        _candidate("Do Not Bet", operator_action="DO_NOT_BET_UNTIL_REVIEWED"),
        _candidate("Held Stake", stake_policy="HOLD"),
        _candidate("Held For Review", stake_policy="HOLD_FOR_REVIEW"),
        _candidate("Same Opponent Warning", same_opponent_under_warning=True),
    ]

    review = build_near_elite_review(pd.DataFrame(rows), pd.DataFrame())

    assert review["player_name"].tolist() == ["Clean Candidate"]
    assert set(review["kelly_eligible"].astype(str).str.lower()) == {"false"}


def test_daily_and_quality_summaries_surface_near_elite_without_changing_elite_count(tmp_path: Path) -> None:
    prediction_date = "2026-05-23"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    candidate = _candidate("Near Elite")

    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate])
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"kelly_stakes_{prediction_date}.csv", [], columns=["kelly_eligible", "stake_amount", "expected_value"])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_csv(history_root / "paper_kelly_history.csv", [], columns=["prediction_date"])
    diagnostics.mkdir(parents=True, exist_ok=True)
    _write_json(
        diagnostics / f"market_shadow_grading_{prediction_date}.json",
        {"kelly_decision_performance": {"by_kelly_eligible": {"true": {}, "false": {}}}},
    )

    daily_path, daily_metadata = write_daily_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
        persist_shadow_history=False,
        persist_paper_kelly_history=False,
    )
    daily_text = daily_path.read_text(encoding="utf-8")
    assert "Near-Elite Review Lane" in daily_text
    assert "- review row count: 1" in daily_text
    assert REVIEW_ONLY_NOTE in daily_text
    assert daily_metadata["near_elite_review_count"] == 1

    quality_txt, _quality_json, quality_payload = write_quality_summary_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        out_dir=tmp_path,
        history_root=history_root,
        generated_at="2026-05-23T00:00:00+00:00",
    )
    quality_text = quality_txt.read_text(encoding="utf-8")
    assert "Near-Elite Review Lane" in quality_text
    assert quality_payload["near_elite_review"]["row_count"] == 1
    assert quality_payload["candidate_funnel"]["near_elite_review_count"] == 1
    assert quality_payload["candidate_funnel"]["elite_board_count"] == 0


def test_operator_card_near_elite_review_does_not_affect_final_decision(tmp_path: Path) -> None:
    prediction_date = "2026-05-23"
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator = runtime_root / "operator"
    diagnostics = runtime_root / "diagnostics"
    candidate = _candidate("Near Elite")

    _write_csv(operator / f"full_market_board_{prediction_date}.csv", [candidate])
    _write_csv(operator / f"elite_board_{prediction_date}.csv", [], columns=list(candidate.keys()))
    _write_csv(operator / f"near_elite_review_{prediction_date}.csv", [candidate | {
        "operator_action": REVIEW_ONLY_ACTION,
        "stake_policy": NO_AUTO_STAKE_POLICY,
        "kelly_eligible": False,
        "review_lane": REVIEW_LANE,
        "review_reason": "near_elite_player_points_over_met_review_thresholds_not_elite",
    }])
    _write_csv(operator / f"sgp_board_{prediction_date}.csv", [], columns=["prediction_date"])
    _write_json(
        operator / f"quality_summary_{prediction_date}.json",
        _quality_payload(prediction_date, full_market_count=1, near_elite_count=1),
    )
    _write_json(diagnostics / f"board_diagnostics_{prediction_date}.json", {"board_counts": {"qualified_pool": 1}})
    _write_json(
        diagnostics / f"market_shadow_grading_{prediction_date}.json",
        {
            "totals": {"total_picks": 1, "graded_picks": 0, "pending_picks": 0, "hit_rate": None},
            "kelly_decision_performance": {"status": "insufficient_sample"},
        },
    )
    _write_json(diagnostics / f"injury_context_diagnostics_{prediction_date}.json", {})
    _write_json(diagnostics / f"game_context_{prediction_date}.json", {})

    output_path, payload = write_operator_card_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    text = output_path.read_text(encoding="utf-8")
    assert payload["final_decision"] == "NO BET"
    assert payload["elite_count"] == 0
    assert payload["near_elite_review_count"] == 1
    assert "- near-elite review count: 1" in text
    assert "top 5 near-elite candidates" in text
    assert REVIEW_ONLY_NOTE in text
