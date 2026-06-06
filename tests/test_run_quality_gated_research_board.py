from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.run_quality_gated_research_board as quality_gated_research
from scripts.run_quality_gated_research_board import (
    BETTING_APPROVAL_STATUS,
    EXCLUDED_RESEARCH_ONLY,
    LOW_CONFIDENCE_REVIEW,
    PRICE_SENSITIVE_WATCHLIST,
    QUALITY_GATED_RESEARCH_INPUT_MISSING,
    QUALITY_GATED_RESEARCH_NO_OUTPUT_ROWS,
    QUALITY_GATED_RESEARCH_OK,
    QUALITY_GATED_RESEARCH_SCHEMA_INVALID,
    RESEARCH_WATCHLIST,
    SUMMARY_NOTICE,
    run_quality_gated_research_board,
)


PREDICTION_DATE = "2026-06-05"


def _research_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "diagnostics"


def _quality_review_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"projection_quality_review_{PREDICTION_DATE}.csv"
    )


def _row(
    player_name: str,
    *,
    projection_value: float | None = 24.0,
    side_adjusted_edge: float | None = 4.0,
    american_odds: float | None = -110,
    review_rank: int = 1,
    projection_confidence_tier: str = "high_research_confidence",
    review_warning_flags: str = "none",
    odds_quality_flag: str = "favorable_or_fair",
    line_quality_flag: str = "strong_edge_line",
    minutes_quality_flag: str = "stable_minutes",
    eligible_for_betting: bool = False,
) -> dict[str, Any]:
    return {
        "review_rank": review_rank,
        "player_name": player_name,
        "market_type": "player_points",
        "side": "over",
        "line": 20.0,
        "projection_value": projection_value,
        "side_adjusted_edge": side_adjusted_edge,
        "sportsbook": "DraftKings",
        "american_odds": american_odds,
        "projection_quality_flag": "research_projection_only",
        "eligible_for_betting": eligible_for_betting,
        "projection_quality_review": "research_quality_review_complete",
        "projection_confidence_tier": projection_confidence_tier,
        "review_warning_flags": review_warning_flags,
        "odds_quality_flag": odds_quality_flag,
        "line_quality_flag": line_quality_flag,
        "minutes_quality_flag": minutes_quality_flag,
    }


def _write_quality_review(
    tmp_path: Path,
    rows: list[dict[str, Any]],
) -> Path:
    path = _quality_review_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(
    tmp_path: Path,
    *,
    include_heavy_juice_review: bool = True,
    top_n: int = 25,
):
    return run_quality_gated_research_board(
        target_date=PREDICTION_DATE,
        quality_review=_quality_review_path(tmp_path),
        output_dir=_research_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
        include_heavy_juice_review=include_heavy_juice_review,
        top_n=top_n,
    )


def _read_diagnostics(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_assigns_categories_and_ranks_category_priority(
    tmp_path: Path,
) -> None:
    _write_quality_review(
        tmp_path,
        [
            _row(
                "Excluded",
                side_adjusted_edge=1.0,
                line_quality_flag="weak_edge_line",
            ),
            _row(
                "Low",
                side_adjusted_edge=9.0,
                projection_confidence_tier="low_research_confidence",
            ),
            _row(
                "Price Medium",
                side_adjusted_edge=10.0,
                american_odds=-220,
                projection_confidence_tier="medium_research_confidence",
                odds_quality_flag="heavy_juice",
            ),
            _row(
                "Research",
                side_adjusted_edge=3.0,
                line_quality_flag="review_edge_line",
                review_rank=9,
            ),
            _row(
                "Price High",
                side_adjusted_edge=5.0,
                american_odds=-240,
                odds_quality_flag="heavy_juice",
                review_rank=7,
            ),
        ],
    )

    result = _run(tmp_path)

    assert result.status == QUALITY_GATED_RESEARCH_OK
    board = pd.read_csv(result.output_path)
    assert board["player_name"].tolist() == [
        "Research",
        "Price High",
        "Price Medium",
        "Low",
        "Excluded",
    ]
    assert board["research_category"].tolist() == [
        RESEARCH_WATCHLIST,
        PRICE_SENSITIVE_WATCHLIST,
        PRICE_SENSITIVE_WATCHLIST,
        LOW_CONFIDENCE_REVIEW,
        EXCLUDED_RESEARCH_ONLY,
    ]
    assert board["research_board_rank"].tolist() == [1, 2, 3, 4, 5]
    assert board.loc[1, "price_warning"] == "heavy_juice_price_warning"


def test_unstable_minutes_are_low_confidence_even_with_heavy_juice(
    tmp_path: Path,
) -> None:
    _write_quality_review(
        tmp_path,
        [
            _row(
                "Minutes Review",
                american_odds=-250,
                odds_quality_flag="heavy_juice",
                minutes_quality_flag="minutes_shift_review",
            )
        ],
    )

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path)
    assert board.loc[0, "research_category"] == LOW_CONFIDENCE_REVIEW


def test_weak_missing_and_manual_rows_are_excluded_research_only(
    tmp_path: Path,
) -> None:
    _write_quality_review(
        tmp_path,
        [
            _row(
                "Weak",
                side_adjusted_edge=1.0,
                line_quality_flag="weak_edge_line",
            ),
            _row(
                "Missing Odds",
                american_odds=None,
                odds_quality_flag="missing_odds",
                review_warning_flags="missing_odds",
            ),
            _row(
                "Missing Projection",
                projection_value=None,
                review_warning_flags="missing_projection",
            ),
            _row(
                "Missing Edge",
                side_adjusted_edge=None,
                line_quality_flag="missing_edge",
                review_warning_flags="missing_side_adjusted_edge",
            ),
            _row(
                "Manual",
                projection_confidence_tier="manual_review_only",
                review_warning_flags="input_schema_issue",
            ),
        ],
    )

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path).set_index("player_name")
    assert set(board["research_category"]) == {EXCLUDED_RESEARCH_ONLY}
    assert "weak_edge_line" in board.loc["Weak", "final_research_note"]
    assert "missing_odds" in board.loc[
        "Missing Odds",
        "final_research_note",
    ]
    assert "missing_projection" in board.loc[
        "Missing Projection",
        "final_research_note",
    ]
    assert "missing_edge" in board.loc[
        "Missing Edge",
        "final_research_note",
    ]
    assert "schema_issue" in board.loc["Manual", "final_research_note"]


def test_heavy_juice_requires_explicit_review_flag(tmp_path: Path) -> None:
    _write_quality_review(
        tmp_path,
        [
            _row(
                "Heavy",
                american_odds=-200,
                odds_quality_flag="heavy_juice",
            )
        ],
    )

    result = _run(tmp_path, include_heavy_juice_review=False)

    board = pd.read_csv(result.output_path)
    assert board.loc[0, "research_category"] == EXCLUDED_RESEARCH_ONLY
    assert "heavy_juice_review_not_enabled" in board.loc[
        0,
        "final_research_note",
    ]


def test_forces_research_only_fields_and_applies_top_n(tmp_path: Path) -> None:
    _write_quality_review(
        tmp_path,
        [
            _row("Second", side_adjusted_edge=4.0),
            _row(
                "First",
                side_adjusted_edge=5.0,
                eligible_for_betting=True,
            ),
        ],
    )

    result = _run(tmp_path, top_n=1)

    board = pd.read_csv(result.output_path)
    assert board["player_name"].tolist() == ["First"]
    assert board["eligible_for_betting"].tolist() == [False]
    assert board["betting_approval_status"].tolist() == [
        BETTING_APPROVAL_STATUS
    ]
    assert result.diagnostics["eligible_for_betting_any_true"] is False
    assert result.diagnostics[
        "source_eligible_for_betting_any_true"
    ] is True


def test_writes_summary_diagnostics_and_no_betting_artifacts(
    tmp_path: Path,
) -> None:
    operator_dir = tmp_path / "outputs" / "runtime" / "operator"
    operator_dir.mkdir(parents=True)
    sentinels = [
        operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv",
        operator_dir / f"elite_board_{PREDICTION_DATE}.csv",
        operator_dir / f"market_props_{PREDICTION_DATE}.csv",
        operator_dir / f"operator_board_{PREDICTION_DATE}.csv",
    ]
    for path in sentinels:
        path.write_text("existing\n", encoding="utf-8")

    _write_quality_review(tmp_path, [_row("Safe")])
    result = _run(tmp_path)

    summary = result.summary_path.read_text(encoding="utf-8")
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert SUMMARY_NOTICE in summary
    assert "research_watchlist_count: 1" in summary
    assert diagnostics["input_row_count"] == 1
    assert diagnostics["output_row_count"] == 1
    assert diagnostics["category_counts"] == {RESEARCH_WATCHLIST: 1}
    assert diagnostics["confidence_tier_counts"] == {
        "high_research_confidence": 1
    }
    assert diagnostics["odds_quality_flag_counts"] == {
        "favorable_or_fair": 1
    }
    assert diagnostics["line_quality_flag_counts"] == {
        "strong_edge_line": 1
    }
    assert diagnostics["minutes_quality_flag_counts"] == {
        "stable_minutes": 1
    }
    assert diagnostics["heavy_juice_count"] == 0
    assert diagnostics["favorable_or_fair_count"] == 1
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(quality_gated_research, "MarketProp")
    assert [path.read_text(encoding="utf-8") for path in sentinels] == [
        "existing\n"
    ] * 4
    assert sorted(path.name for path in operator_dir.iterdir()) == sorted(
        path.name for path in sentinels
    )


def test_non_success_statuses_write_only_research_artifacts(
    tmp_path: Path,
) -> None:
    missing = _run(tmp_path)
    assert missing.status == QUALITY_GATED_RESEARCH_INPUT_MISSING
    assert pd.read_csv(missing.output_path).empty

    invalid_row = _row("Invalid")
    invalid_row.pop("review_rank")
    _write_quality_review(tmp_path, [invalid_row])
    invalid = _run(tmp_path)
    assert invalid.status == QUALITY_GATED_RESEARCH_SCHEMA_INVALID
    invalid_board = pd.read_csv(invalid.output_path)
    assert invalid_board.loc[0, "research_category"] == EXCLUDED_RESEARCH_ONLY

    pd.DataFrame(columns=_row("Empty").keys()).to_csv(
        _quality_review_path(tmp_path),
        index=False,
    )
    no_rows = _run(tmp_path)
    assert no_rows.status == QUALITY_GATED_RESEARCH_NO_OUTPUT_ROWS
    assert pd.read_csv(no_rows.output_path).empty
