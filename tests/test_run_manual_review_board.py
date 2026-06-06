from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.run_manual_review_board as manual_review
from scripts.run_manual_review_board import (
    BETTING_APPROVAL_STATUS,
    MANUAL_REVIEW_NO_EDGE_BOARD,
    MANUAL_REVIEW_NO_VALIDATED_ROWS,
    MANUAL_REVIEW_OK,
    MANUAL_REVIEW_SCHEMA_INVALID,
    MANUAL_REVIEW_STATUS,
    SUMMARY_NOTICE,
    run_manual_review_board,
)


PREDICTION_DATE = "2026-06-05"


def _row(
    *,
    player_name: str = "Jane Doe",
    market_type: str = "player_points",
    side: str = "over",
    line: float = 20.5,
    research_rank_score: float = 25.0,
    passes_research_validation: bool = True,
    sportsbook: str = "DraftKings",
    eligible_for_betting: bool = False,
) -> dict[str, Any]:
    return {
        "player_name": player_name,
        "market_type": market_type,
        "side": side,
        "line": line,
        "projection_value": 23.0,
        "side_adjusted_edge": 2.5,
        "edge_bucket": "medium_edge",
        "directional_edge_bucket": "medium_edge",
        "sportsbook": sportsbook,
        "american_odds": -110,
        "projection_source_type": "model_projection",
        "projection_quality_flag": "model_projection",
        "research_rank_score": research_rank_score,
        "research_rank_reason": f"score={research_rank_score}",
        "game_date": PREDICTION_DATE,
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "commence_time_local": "2026-06-05T19:30:00-04:00",
        "betting_approval_status": BETTING_APPROVAL_STATUS,
        "eligible_for_betting": eligible_for_betting,
        "passes_research_validation": passes_research_validation,
    }


def _runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime"


def _output_dir(tmp_path: Path) -> Path:
    return _runtime_root(tmp_path) / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return _runtime_root(tmp_path) / "diagnostics"


def _edge_board_path(tmp_path: Path) -> Path:
    return _output_dir(tmp_path) / f"edge_validation_board_{PREDICTION_DATE}.csv"


def _write_edge_board(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = _edge_board_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(
    tmp_path: Path,
    *,
    dedupe_mode: str = "best_per_player_market",
    top_n: int = 50,
    min_rank_score: float = 0,
):
    return run_manual_review_board(
        target_date=PREDICTION_DATE,
        edge_board=_edge_board_path(tmp_path),
        output_dir=_output_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
        top_n=top_n,
        dedupe_mode=dedupe_mode,
        min_rank_score=min_rank_score,
    )


def _read_diagnostics(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_creates_board_from_validated_rows_and_required_review_flags(
    tmp_path: Path,
) -> None:
    _write_edge_board(
        tmp_path,
        [
            _row(player_name="Second", research_rank_score=20),
            _row(player_name="First", research_rank_score=40),
        ],
    )

    result = _run(tmp_path)

    assert result.status == MANUAL_REVIEW_OK
    board = pd.read_csv(result.output_path)
    assert board["player_name"].tolist() == ["First", "Second"]
    assert board["review_rank"].tolist() == [1, 2]
    assert board["needs_manual_projection_review"].tolist() == [True, True]
    assert board["needs_line_shop_review"].tolist() == [True, True]
    assert board["needs_injury_review"].tolist() == [True, True]
    assert board["needs_minutes_review"].tolist() == [True, True]
    assert board["manual_review_status"].tolist() == [MANUAL_REVIEW_STATUS] * 2
    assert SUMMARY_NOTICE in result.summary_path.read_text(encoding="utf-8")

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["input_row_count"] == 2
    assert diagnostics["research_validated_input_count"] == 2
    assert diagnostics["output_row_count"] == 2
    assert diagnostics["unique_player_count"] == 2
    assert diagnostics["unique_market_count"] == 1
    assert diagnostics["unique_sportsbook_count"] == 1
    assert diagnostics["market_counts"] == {"player_points": 2}
    assert diagnostics["side_counts"] == {"over": 2}


def test_filters_out_non_validated_rows(tmp_path: Path) -> None:
    _write_edge_board(
        tmp_path,
        [
            _row(player_name="Validated"),
            _row(
                player_name="Rejected",
                research_rank_score=100,
                passes_research_validation=False,
            ),
        ],
    )

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path)
    assert board["player_name"].tolist() == ["Validated"]
    assert result.diagnostics["research_validated_input_count"] == 1


def test_best_per_player_market_dedupe_keeps_highest_score(tmp_path: Path) -> None:
    _write_edge_board(
        tmp_path,
        [
            _row(side="over", line=20.5, research_rank_score=10),
            _row(side="under", line=22.5, research_rank_score=30),
            _row(market_type="player_assists", research_rank_score=20),
        ],
    )

    result = _run(tmp_path, dedupe_mode="best_per_player_market")

    board = pd.read_csv(result.output_path)
    assert len(board.index) == 2
    points = board[board["market_type"] == "player_points"].iloc[0]
    assert points["side"] == "under"
    assert points["research_rank_score"] == 30


def test_best_per_player_market_side_dedupe_keeps_each_side(tmp_path: Path) -> None:
    _write_edge_board(
        tmp_path,
        [
            _row(side="over", line=20.5, research_rank_score=10),
            _row(side="over", line=21.5, research_rank_score=30),
            _row(side="under", line=22.5, research_rank_score=20),
        ],
    )

    result = _run(tmp_path, dedupe_mode="best_per_player_market_side")

    board = pd.read_csv(result.output_path)
    assert board["side"].tolist() == ["over", "under"]
    assert board["research_rank_score"].tolist() == [30, 20]


def test_top_n_limit_is_applied_after_ranking(tmp_path: Path) -> None:
    _write_edge_board(
        tmp_path,
        [
            _row(player_name="Third", research_rank_score=10),
            _row(player_name="First", research_rank_score=30),
            _row(player_name="Second", research_rank_score=20),
        ],
    )

    result = _run(tmp_path, dedupe_mode="none", top_n=2)

    board = pd.read_csv(result.output_path)
    assert board["player_name"].tolist() == ["First", "Second"]
    assert board["review_rank"].tolist() == [1, 2]


def test_eligible_and_approval_are_forced_research_only(tmp_path: Path) -> None:
    _write_edge_board(tmp_path, [_row(eligible_for_betting=True)])

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path)
    assert board["eligible_for_betting"].tolist() == [False]
    assert board["betting_approval_status"].tolist() == [
        BETTING_APPROVAL_STATUS
    ]
    assert result.diagnostics["eligible_for_betting_any_true"] is False


def test_no_market_prop_kelly_elite_or_operator_artifacts_are_written(
    tmp_path: Path,
) -> None:
    operator_dir = _runtime_root(tmp_path) / "operator"
    operator_dir.mkdir(parents=True)
    sentinels = [
        operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv",
        operator_dir / f"elite_board_{PREDICTION_DATE}.csv",
        operator_dir / f"operator_board_{PREDICTION_DATE}.csv",
    ]
    for path in sentinels:
        path.write_text("existing\n", encoding="utf-8")

    _write_edge_board(tmp_path, [_row()])
    result = _run(tmp_path)

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(manual_review, "MarketProp")
    assert [path.read_text(encoding="utf-8") for path in sentinels] == [
        "existing\n",
        "existing\n",
        "existing\n",
    ]
    assert sorted(path.name for path in operator_dir.iterdir()) == sorted(
        path.name for path in sentinels
    )


def test_missing_empty_and_invalid_inputs_use_explicit_statuses(
    tmp_path: Path,
) -> None:
    missing = _run(tmp_path)
    assert missing.status == MANUAL_REVIEW_NO_EDGE_BOARD
    assert pd.read_csv(missing.output_path).empty

    _write_edge_board(
        tmp_path,
        [_row(passes_research_validation=False)],
    )
    no_validated = _run(tmp_path)
    assert no_validated.status == MANUAL_REVIEW_NO_VALIDATED_ROWS
    assert pd.read_csv(no_validated.output_path).empty

    invalid_row = _row()
    invalid_row.pop("research_rank_score")
    _write_edge_board(tmp_path, [invalid_row])
    invalid = _run(tmp_path)
    assert invalid.status == MANUAL_REVIEW_SCHEMA_INVALID
    assert invalid.diagnostics["schema_missing_required_columns"] == [
        "research_rank_score"
    ]
