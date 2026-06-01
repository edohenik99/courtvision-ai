from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from courtvision.reporting.under_visibility_audit import (
    build_under_visibility_audit,
    render_under_visibility_audit_text,
    report_paths_for_date,
    write_under_visibility_audit_outputs,
    # Phase 6B.1
    UNDER_DO_NOT_PROMOTE,
    UNDER_INSUFFICIENT_SAMPLE,
    UNDER_REVIEW_CANDIDATE_SHADOW_ONLY,
    UNDER_WATCHLIST_SHADOW_ONLY,
    _classify_under_lane,
    _sample_status,
    board_paths_for_date,
    build_under_visibility_board,
    render_under_visibility_report_text,
    write_under_visibility_board_outputs,
)


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_builds_visibility_audit_with_mock_slate(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-30"

    # Set up mock files
    # 1. Market availability
    market_avail = {
        "raw_provider_markets": {"provider1": 10, "provider2": 5},
        "normalized_markets": {"market1": 8},
        "counts": [],
    }
    _write_json(
        runtime_root / "diagnostics" / f"market_availability_audit_{prediction_date}.json",
        market_avail,
    )

    # 2. Player predictions (contains rejected)
    preds = [
        {
            "player_name": "Player A",
            "market_type": "player_points",
            "selection": "under",
            "line": 15.5,
            "model_projection": 13.2,
            "edge": -2.3,
            "confidence": 0.72,
            "quality_score": 62.0,
            "rejection_reason": "market_gate_confidence_lt_0.60",
        },
        {
            "player_name": "Player B",
            "market_type": "player_rebounds",
            "selection": "under",
            "line": 8.5,
            "model_projection": 7.0,
            "edge": -1.5,
            "confidence": 0.55,
            "quality_score": 45.0,
            "rejection_reason": "market_gate_minutes_lt_24",
        },
        {
            "player_name": "Player C",
            "market_type": "player_points",
            "selection": None,  # early gate rejection
            "rejection_reason": "reject_negative_edge_direction",
        },
    ]
    _write_csv(
        runtime_root / "research" / f"player_predictions_{prediction_date}.csv",
        preds,
    )

    # 3. Full market board (contains accepted)
    full_market = [
        {
            "player_name": "Player D",
            "market_type": "player_points",
            "selection": "under",
            "line": 20.5,
            "model_projection": 17.5,
            "edge": -3.0,
            "confidence": 0.75,
            "quality_score": 68.0,
            "same_opponent_under_warning": "True",
            "final_elite_rejection_reason": "none",
        },
        {
            "player_name": "Player E",
            "market_type": "player_rebounds",
            "selection": "over",
            "line": 6.5,
            "model_projection": 7.8,
            "edge": 1.3,
            "confidence": 0.76,
            "quality_score": 70.0,
            "same_opponent_under_warning": "False",
            "final_elite_rejection_reason": "none",
        },
    ]
    _write_csv(
        runtime_root / "operator" / f"full_market_board_{prediction_date}.csv",
        full_market,
    )

    # 4. Empty review / incubator / elite lists
    _write_csv(runtime_root / "operator" / f"near_elite_review_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"incubator_board_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"shadow_candidate_lane_{prediction_date}.csv", [])
    _write_csv(runtime_root / "operator" / f"elite_board_{prediction_date}.csv", [])

    # 5. History shadow candidates with mixed type shadow_roi
    history = [
        {"selection": "under", "result_status": "hit", "shadow_roi": "0.909091"},
        {"selection": "under", "result_status": "miss", "shadow_roi": -1.0},
        {"selection": "over", "result_status": "miss", "shadow_roi": "-1.0"},
        {"selection": "over", "result_status": "hit", "shadow_roi": 0.8},
    ]
    _write_csv(history_root / "market_shadow_history.csv", history)

    # Run build
    payload, df = build_under_visibility_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    # Assertions on payload structure and values
    assert payload["prediction_date"] == prediction_date
    assert payload["read_only"] is True
    assert payload["betting_logic_changed"] is False

    funnel = payload["funnel_stages"]
    assert funnel["raw_odds"]["total"] == 30
    assert funnel["full_market"]["under"] == 1
    assert funnel["full_market"]["over"] == 1

    # check rejection counts
    rejections = payload["rejection_reasons"]
    assert rejections["same opponent warning"] == 1
    assert rejections["low confidence"] == 1  # Player A: confidence=0.72, rejected by market_gate_confidence_lt_0.60
    assert rejections["negative edge direction"] == 1  # Player C is 0.5 negative edge, rounded to 1

    # check shadow performance averages (mixed strings & floats parsed without TypeError)
    hist_comp = payload["historical_comparison"]
    assert hist_comp["under"]["count"] == 2
    assert hist_comp["over"]["count"] == 2
    assert pytest.approx(hist_comp["under"]["roi"]) == -0.0454545
    assert pytest.approx(hist_comp["over"]["roi"]) == -0.1

    # Verify rendering text report
    csv_path, text_path, json_path = report_paths_for_date(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
    )
    text_report = render_under_visibility_audit_text(payload, csv_path)
    assert "UNDER Candidate Visibility Audit" in text_report
    assert "Raw Odds Feeds" in text_report
    assert "Player D" in text_report


def test_handles_empty_or_missing_files_gracefully(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-31"

    # All files missing
    payload, df = build_under_visibility_audit(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert payload["prediction_date"] == prediction_date
    assert payload["funnel_stages"]["raw_odds"]["total"] == 0
    assert payload["historical_comparison"]["under"]["count"] == 0
    assert payload["current_slate_candidates"] == []
    assert df.empty


def test_write_audit_outputs_persists_correctly(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    prediction_date = "2026-05-30"

    # Create directories
    (runtime_root / "operator").mkdir(parents=True, exist_ok=True)
    (runtime_root / "diagnostics").mkdir(parents=True, exist_ok=True)
    (runtime_root / "research").mkdir(parents=True, exist_ok=True)
    history_root.mkdir(parents=True, exist_ok=True)

    csv_path, text_path, json_path, payload = write_under_visibility_audit_outputs(
        prediction_date=prediction_date,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert csv_path.exists()
    assert text_path.exists()
    assert json_path.exists()

    # Verify no betting-facing flags or components were written
    content = json.loads(json_path.read_text(encoding="utf-8"))
    assert content["read_only"] is True
    assert content["betting_logic_changed"] is False


# ============================================================
# Phase 6B.1 — UNDER Visibility Board Tests
# ============================================================

BOARD_DATE = "2026-06-03"


def test_sample_status_labels() -> None:
    assert _sample_status(0) == "insufficient_lt_10"
    assert _sample_status(9) == "insufficient_lt_10"
    assert _sample_status(10) == "weak_10_49"
    assert _sample_status(49) == "weak_10_49"
    assert _sample_status(50) == "moderate_50_99"
    assert _sample_status(99) == "moderate_50_99"
    assert _sample_status(100) == "adequate_100+"
    assert _sample_status(200) == "adequate_100+"


def test_classify_under_lane_do_not_promote_same_opponent() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=True,
        identity_conflict=False,
        caution="low",
        context_aligned=True,
        hist_n=100,
        abs_edge=2.0,
    )
    assert lane == UNDER_DO_NOT_PROMOTE


def test_classify_under_lane_do_not_promote_identity_conflict() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=False,
        identity_conflict=True,
        caution="low",
        context_aligned=True,
        hist_n=100,
        abs_edge=2.0,
    )
    assert lane == UNDER_DO_NOT_PROMOTE


def test_classify_under_lane_do_not_promote_high_caution() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=False,
        identity_conflict=False,
        caution="high",
        context_aligned=True,
        hist_n=100,
        abs_edge=2.0,
    )
    assert lane == UNDER_DO_NOT_PROMOTE


def test_classify_under_lane_insufficient_sample() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=False,
        identity_conflict=False,
        caution="low",
        context_aligned=True,
        hist_n=5,
        abs_edge=2.0,
    )
    assert lane == UNDER_INSUFFICIENT_SAMPLE


def test_classify_under_lane_review_candidate() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=False,
        identity_conflict=False,
        caution="low",
        context_aligned=True,
        hist_n=50,
        abs_edge=1.0,
    )
    assert lane == UNDER_REVIEW_CANDIDATE_SHADOW_ONLY


def test_classify_under_lane_watchlist_low_edge() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=False,
        identity_conflict=False,
        caution="low",
        context_aligned=True,
        hist_n=50,
        abs_edge=0.1,  # below threshold
    )
    assert lane == UNDER_WATCHLIST_SHADOW_ONLY


def test_classify_under_lane_watchlist_not_aligned() -> None:
    lane = _classify_under_lane(
        same_opponent_warning=False,
        identity_conflict=False,
        caution="low",
        context_aligned=False,
        hist_n=50,
        abs_edge=5.0,
    )
    assert lane == UNDER_WATCHLIST_SHADOW_ONLY


def test_board_paths_for_date(tmp_path: Path) -> None:
    csv_path, txt_path, json_path = board_paths_for_date(
        prediction_date=BOARD_DATE,
        runtime_root=tmp_path / "runtime",
    )
    assert csv_path.name == f"under_visibility_board_{BOARD_DATE}.csv"
    assert txt_path.name == f"under_visibility_report_{BOARD_DATE}.txt"
    assert json_path.name == f"under_visibility_board_{BOARD_DATE}.json"
    assert "operator" in str(csv_path)
    assert "operator" in str(txt_path)
    assert "diagnostics" in str(json_path)


def test_build_under_visibility_board_empty_sources(tmp_path: Path) -> None:
    """Board builds without errors when no source files exist."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    payload, board_df = build_under_visibility_board(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert payload["shadow_only"] is True
    assert payload["betting_logic_changed"] is False
    assert payload["real_money_promotion"] is False
    assert payload["pick_history_written"] is False
    assert payload["board_row_count"] == 0
    assert board_df.empty


def test_build_under_visibility_board_all_four_lanes(tmp_path: Path) -> None:
    """All four lanes are classified correctly from full_market_board."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        # Should be REVIEW_CANDIDATE: context-aligned, low caution, adequate sample (from history), big edge
        {
            "player_name": "Alice",
            "team_abbr": "BOS",
            "opponent": "LAL",
            "market_type": "player_points",
            "selection": "under",
            "line": 20.5,
            "odds": -115,
            "model_projection": 17.2,
            "edge": 3.3,
            "confidence": 0.72,
            "quality_score": 0.85,
            "context_caution_level": "low",
            "context_pick_alignment": "aligned",
            "same_opponent_under_warning": False,
        },
        # Should be WATCHLIST: context-aligned, low caution, adequate sample, small edge
        {
            "player_name": "Bob",
            "team_abbr": "MIA",
            "market_type": "player_rebounds",
            "selection": "under",
            "line": 6.5,
            "edge": 0.2,
            "confidence": 0.55,
            "quality_score": 0.60,
            "context_caution_level": "low",
            "context_pick_alignment": "aligned",
            "same_opponent_under_warning": False,
        },
        # Should be DO_NOT_PROMOTE: same_opponent_warning
        {
            "player_name": "Carol",
            "market_type": "player_assists",
            "selection": "under",
            "line": 4.5,
            "edge": 1.5,
            "confidence": 0.65,
            "context_caution_level": "low",
            "context_pick_alignment": "aligned",
            "same_opponent_under_warning": True,
        },
        # Over row should be excluded
        {
            "player_name": "Dave",
            "market_type": "player_points",
            "selection": "over",
            "line": 25.0,
            "edge": 2.0,
            "context_caution_level": "low",
        },
    ]
    pd.DataFrame(rows).to_csv(operator_dir / f"full_market_board_{BOARD_DATE}.csv", index=False)

    # Seed market shadow history with enough UNDER rows for adequate sample
    history_root.mkdir(parents=True, exist_ok=True)
    hist_rows = [
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.05},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.06},
        {"selection": "under", "result_status": "miss", "shadow_roi": -0.04},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.03},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.07},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.02},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.05},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.04},
        {"selection": "under", "result_status": "miss", "shadow_roi": -0.03},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.06},
        {"selection": "under", "result_status": "hit", "shadow_roi": 0.08},
    ]
    pd.DataFrame(hist_rows).to_csv(history_root / "market_shadow_history.csv", index=False)

    payload, board_df = build_under_visibility_board(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    # Over row should be excluded
    assert len(board_df) == 3, f"Expected 3 UNDER rows, got {len(board_df)}"
    assert payload["board_row_count"] == 3

    lane_col = board_df["under_visibility_lane"].tolist()
    assert UNDER_DO_NOT_PROMOTE in lane_col
    assert UNDER_REVIEW_CANDIDATE_SHADOW_ONLY in lane_col
    assert UNDER_WATCHLIST_SHADOW_ONLY in lane_col

    # All safety flags must be consistent
    assert payload["shadow_only"] is True
    assert payload["betting_logic_changed"] is False
    assert payload["real_money_promotion"] is False
    assert payload["elite_promotion"] is False
    assert payload["kelly_promotion"] is False
    assert payload["pick_history_written"] is False

    # All required columns present
    for col in ("player_name", "market_type", "selection", "line", "edge", "abs_edge",
                "caution_bucket", "context_alignment", "same_opponent_warning",
                "under_visibility_lane", "recommended_action", "safety_notes",
                "sample_status", "historical_bucket_n"):
        assert col in board_df.columns, f"Missing column: {col}"

    # Review candidate should rank first
    assert board_df.iloc[0]["player_name"] == "Alice"
    assert board_df.iloc[0]["under_visibility_lane"] == UNDER_REVIEW_CANDIDATE_SHADOW_ONLY

    # All rows have correct recommended_action and shadow_tracking note
    assert all(board_df["recommended_action"] == "shadow_tracking_only")


def test_build_under_visibility_board_insufficient_sample(tmp_path: Path) -> None:
    """Row with no history is classified as UNDER_INSUFFICIENT_SAMPLE."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)

    rows = [{
        "player_name": "Eve",
        "market_type": "player_points",
        "selection": "under",
        "line": 18.5,
        "edge": 1.5,
        "confidence": 0.7,
        "context_caution_level": "low",
        "context_pick_alignment": "aligned",
        "same_opponent_under_warning": False,
    }]
    pd.DataFrame(rows).to_csv(operator_dir / f"full_market_board_{BOARD_DATE}.csv", index=False)
    # No history file → hist_n = 0 → UNDER_INSUFFICIENT_SAMPLE

    payload, board_df = build_under_visibility_board(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert len(board_df) == 1
    assert board_df.iloc[0]["under_visibility_lane"] == UNDER_INSUFFICIENT_SAMPLE
    assert board_df.iloc[0]["sample_status"] == "insufficient_lt_10"


def test_build_under_visibility_board_shadow_lane_supplement(tmp_path: Path) -> None:
    """UNDER rows from shadow_candidate_lane that aren't in full_market_board are included."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)

    # full_market has one row
    _write_csv(
        operator_dir / f"full_market_board_{BOARD_DATE}.csv",
        [{"player_name": "Alice", "market_type": "player_points", "selection": "under",
          "line": 20.5, "edge": 1.0, "context_caution_level": "low"}],
    )
    # shadow lane has an additional UNDER row not in full_market_board
    _write_csv(
        operator_dir / f"shadow_candidate_lane_{BOARD_DATE}.csv",
        [{"player_name": "Shadow Sam", "market_type": "player_rebounds", "selection": "under",
          "line": 8.5, "edge": 0.8, "context_caution_level": "low",
          "historical_graded_rows": 25, "historical_hit_rate": 0.60, "historical_roi": 0.04}],
    )

    payload, board_df = build_under_visibility_board(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    # Both players should be present
    player_names = board_df["player_name"].tolist()
    assert "Alice" in player_names
    assert "Shadow Sam" in player_names
    assert payload["board_row_count"] == 2


def test_build_under_visibility_board_shadow_lane_dedup(tmp_path: Path) -> None:
    """Duplicate keys between full_market_board and shadow_candidate_lane are deduplicated."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)

    same_row = {"player_name": "Duplicate", "market_type": "player_points", "selection": "under",
                "line": 15.5, "edge": 1.0, "context_caution_level": "low"}
    _write_csv(operator_dir / f"full_market_board_{BOARD_DATE}.csv", [same_row])
    _write_csv(operator_dir / f"shadow_candidate_lane_{BOARD_DATE}.csv", [same_row])

    payload, board_df = build_under_visibility_board(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )
    assert payload["board_row_count"] == 1


def test_render_under_visibility_report_text(tmp_path: Path) -> None:
    """Report text contains all required sections and disclaimers."""
    payload = {
        "prediction_date": BOARD_DATE,
        "shadow_only": True,
        "betting_logic_changed": False,
        "real_money_promotion": False,
        "pick_history_written": False,
        "board_row_count": 0,
        "lane_counts": {
            UNDER_REVIEW_CANDIDATE_SHADOW_ONLY: 0,
            UNDER_WATCHLIST_SHADOW_ONLY: 0,
            UNDER_INSUFFICIENT_SAMPLE: 0,
            UNDER_DO_NOT_PROMOTE: 0,
        },
        "disclaimers": [
            "This is shadow-only.",
            "This is not an Elite board.",
            "This is not a Kelly input.",
            "This is not a betting recommendation.",
            "No real-money promotion is allowed.",
        ],
        "safety_declarations": [
            "This report does not create bets.",
            "This report does not change final_decision.",
            "This report does not write to pick_history.csv.",
            "This report does not promote UNDERs to Elite.",
        ],
    }
    board_df = pd.DataFrame(columns=["under_visibility_lane"])
    csv_path = tmp_path / "under_visibility_board_2026-06-03.csv"
    text = render_under_visibility_report_text(payload, board_df, csv_path)

    assert f"CourtVision UNDER Visibility Board" in text
    assert BOARD_DATE in text
    assert "OPERATOR NOTICE" in text
    assert "This is shadow-only." in text
    assert "UNDER_REVIEW_CANDIDATE_SHADOW_ONLY" in text
    assert "UNDER_DO_NOT_PROMOTE" in text
    assert "Safety Declarations" in text
    assert "This report does not create bets." in text


def test_write_under_visibility_board_outputs_creates_files(tmp_path: Path) -> None:
    """write_under_visibility_board_outputs writes all three output files."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"

    csv_path, txt_path, json_path, payload = write_under_visibility_board_outputs(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert csv_path.exists()
    assert txt_path.exists()
    assert json_path.exists()

    content = json.loads(json_path.read_text(encoding="utf-8"))
    assert content["shadow_only"] is True
    assert content["betting_logic_changed"] is False
    assert content["real_money_promotion"] is False
    assert content["pick_history_written"] is False
    assert "lane_counts" in content
    assert "safety_declarations" in content


def test_write_under_visibility_board_no_pick_history_written(tmp_path: Path) -> None:
    """The board writer NEVER writes to or modifies pick_history.csv."""
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    history_root.mkdir(parents=True, exist_ok=True)

    pick_hist = history_root / "pick_history.csv"
    pd.DataFrame([{"prediction_date": BOARD_DATE, "pick": "under"}]).to_csv(pick_hist, index=False)
    pick_before = pick_hist.read_bytes()

    write_under_visibility_board_outputs(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert pick_hist.read_bytes() == pick_before


def test_under_visibility_board_safety_flags_are_immutable(tmp_path: Path) -> None:
    """
    Even with a full market board present, the board never sets promotion flags.
    Regression guard: confirms shadow_only / no-promotion flags cannot be True.
    """
    runtime_root = tmp_path / "runtime"
    history_root = tmp_path / "history"
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True, exist_ok=True)

    _write_csv(
        operator_dir / f"full_market_board_{BOARD_DATE}.csv",
        [{"player_name": "Promo Test", "market_type": "player_points", "selection": "under",
          "line": 20.5, "edge": 5.0, "confidence": 0.95, "quality_score": 0.99,
          "context_caution_level": "low", "context_pick_alignment": "aligned"}],
    )

    _, _, _, payload = write_under_visibility_board_outputs(
        prediction_date=BOARD_DATE,
        runtime_root=runtime_root,
        history_root=history_root,
    )

    assert payload["shadow_only"] is True
    assert payload["betting_logic_changed"] is False
    assert payload["real_money_promotion"] is False
    assert payload["elite_promotion"] is False
    assert payload["kelly_promotion"] is False
    assert payload["pick_history_written"] is False
    assert payload["final_decision_unchanged"] is True
