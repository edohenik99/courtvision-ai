from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.run_edge_validation as edge_validation
from scripts.run_edge_validation import (
    BETTING_APPROVAL_STATUS,
    EDGE_VALIDATION_NO_JOIN_BOARD,
    EDGE_VALIDATION_NO_RESEARCH_VALIDATED_ROWS,
    EDGE_VALIDATION_OK,
    EDGE_VALIDATION_SCHEMA_INVALID,
    run_edge_validation,
)


PREDICTION_DATE = "2026-06-05"


def _row(
    *,
    player_name: str = "Jane Doe",
    market_type: str = "player_points",
    side: str = "over",
    line: float = 20.5,
    american_odds: int | None = -110,
    sportsbook: str = "DraftKings",
    projection_value: float = 23.0,
    projection_source_type: str = "model_projection",
    abs_edge: float = 2.5,
    edge_bucket: str = "medium_edge",
    projection_min_avg: float | None = 30.0,
    eligible_for_betting: bool = False,
    provider_event_id: str = "evt_1",
) -> dict[str, Any]:
    return {
        "provider": "the_odds_api",
        "provider_event_id": provider_event_id,
        "home_team": "Oklahoma City Thunder",
        "away_team": "Indiana Pacers",
        "game_date": PREDICTION_DATE,
        "player_name": player_name,
        "market_type": market_type,
        "side": side,
        "line": line,
        "market_line": line,
        "american_odds": american_odds,
        "sportsbook": sportsbook,
        "eligible_for_betting": eligible_for_betting,
        "projection_value": projection_value,
        "projection_source_type": projection_source_type,
        "abs_edge": abs_edge,
        "edge_bucket": edge_bucket,
        "projection_min_avg": projection_min_avg,
    }


def _runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime"


def _output_dir(tmp_path: Path) -> Path:
    return _runtime_root(tmp_path) / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return _runtime_root(tmp_path) / "diagnostics"


def _join_board_path(tmp_path: Path) -> Path:
    return _output_dir(tmp_path) / f"market_projection_join_{PREDICTION_DATE}.csv"


def _write_join_board(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = _join_board_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(tmp_path: Path):
    return run_edge_validation(
        target_date=PREDICTION_DATE,
        join_board=_join_board_path(tmp_path),
        output_dir=_output_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
        min_edge=1.5,
        allowed_source_types="recent_avg_fallback,model_projection",
        min_minutes=20,
        max_events=1,
    )


def _read_diagnostics(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_validation_flags_rejections_and_ranking(tmp_path: Path) -> None:
    _write_join_board(
        tmp_path,
        [
            _row(
                player_name="Strong Model",
                projection_value=24.5,
                abs_edge=4.0,
                edge_bucket="large_edge",
            ),
            _row(
                player_name="Valid Fallback",
                projection_value=22.5,
                projection_source_type="recent_avg_fallback",
                abs_edge=2.0,
                edge_bucket="medium_edge",
            ),
            _row(
                player_name="Tiny Edge",
                projection_value=21.0,
                projection_source_type="recent_avg_fallback",
                abs_edge=0.5,
                edge_bucket="small_edge",
            ),
            _row(
                player_name="Missing Odds",
                american_odds=None,
                abs_edge=2.5,
            ),
            _row(
                player_name="Disallowed Source",
                projection_source_type="baseline_fallback",
                abs_edge=2.5,
            ),
            _row(
                player_name="Low Minutes",
                projection_min_avg=19.0,
                abs_edge=2.5,
            ),
            _row(
                player_name="Missing Minutes Allowed",
                projection_min_avg=None,
                abs_edge=1.5,
            ),
        ],
    )

    result = _run(tmp_path)

    assert result.status == EDGE_VALIDATION_OK
    board = pd.read_csv(result.output_path)
    assert board["player_name"].tolist()[:2] == ["Strong Model", "Valid Fallback"]
    assert board["eligible_for_betting"].tolist() == [False] * 7
    assert board["betting_approval_status"].tolist() == [
        BETTING_APPROVAL_STATUS
    ] * 7

    indexed = board.set_index("player_name")
    assert bool(indexed.loc["Strong Model", "passes_research_validation"])
    assert bool(indexed.loc["Valid Fallback", "passes_research_validation"])
    assert bool(indexed.loc["Missing Minutes Allowed", "passes_research_validation"])
    assert not bool(indexed.loc["Tiny Edge", "passes_min_edge"])
    assert not bool(indexed.loc["Tiny Edge", "passes_research_validation"])
    assert not bool(indexed.loc["Missing Odds", "has_american_odds"])
    assert not bool(indexed.loc["Missing Odds", "passes_basic_schema"])
    assert not bool(indexed.loc["Disallowed Source", "passes_projection_source"])
    assert bool(indexed.loc["Low Minutes", "has_minutes_context"])
    assert not bool(indexed.loc["Low Minutes", "passes_minutes_context"])
    assert not bool(indexed.loc["Missing Minutes Allowed", "has_minutes_context"])
    assert bool(indexed.loc["Missing Minutes Allowed", "passes_minutes_context"])

    assert indexed.loc["Strong Model", "research_rank_score"] == 48.0
    assert indexed.loc["Valid Fallback", "research_rank_score"] == 20.0
    assert indexed.loc["Strong Model", "edge_rank_overall"] == 1
    assert indexed.loc["Valid Fallback", "edge_rank_overall"] == 2
    assert board["research_rank_score"].iloc[0] > board["research_rank_score"].iloc[1]

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["input_row_count"] == 7
    assert diagnostics["output_row_count"] == 7
    assert diagnostics["research_validated_count"] == 3
    assert diagnostics["research_rejected_count"] == 4
    assert diagnostics["eligible_for_betting_any_true"] is False
    assert diagnostics["passes_min_edge_count"] == 6
    assert diagnostics["passes_projection_source_count"] == 6
    assert diagnostics["passes_minutes_context_count"] == 6
    assert diagnostics["passes_research_validation_count"] == 3
    assert diagnostics["rejected_reason_counts"] == {
        "edge_below_minimum": 1,
        "minutes_below_minimum": 1,
        "missing_american_odds": 1,
        "projection_source_not_allowed": 1,
    }
    assert diagnostics["top_research_rows_sample"][0]["player_name"] == "Strong Model"
    assert diagnostics["top_research_rows_sample"][0][
        "betting_approval_status"
    ] == BETTING_APPROVAL_STATUS


def test_eligible_for_betting_is_forced_false(tmp_path: Path) -> None:
    _write_join_board(tmp_path, [_row(eligible_for_betting=True)])

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path)
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert board["eligible_for_betting"].tolist() == [False]
    assert diagnostics["source_eligible_for_betting_any_true"] is True
    assert diagnostics["eligible_for_betting_any_true"] is False
    assert any("forced false" in warning for warning in diagnostics["warnings"])


def test_no_betting_domain_rows_calls_or_operator_artifacts_are_created(
    tmp_path: Path,
) -> None:
    runtime_root = _runtime_root(tmp_path)
    operator_dir = runtime_root / "operator"
    operator_dir.mkdir(parents=True)
    kelly_sentinel = operator_dir / f"kelly_stakes_{PREDICTION_DATE}.csv"
    elite_sentinel = operator_dir / f"elite_board_{PREDICTION_DATE}.csv"
    operator_sentinel = operator_dir / f"operator_board_{PREDICTION_DATE}.csv"
    kelly_sentinel.write_text("player_name,stake\nExisting,10\n", encoding="utf-8")
    elite_sentinel.write_text("player_name,score\nExisting,99\n", encoding="utf-8")
    operator_sentinel.write_text("player_name\nExisting\n", encoding="utf-8")
    before = {
        path: path.read_text(encoding="utf-8")
        for path in [kelly_sentinel, elite_sentinel, operator_sentinel]
    }

    _write_join_board(tmp_path, [_row(), _row(side="under")])
    result = _run(tmp_path)

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(edge_validation, "MarketProp")
    for path, content in before.items():
        assert path.read_text(encoding="utf-8") == content
    assert sorted(path.name for path in operator_dir.iterdir()) == sorted(
        path.name for path in before
    )


def test_missing_join_board_writes_empty_research_artifacts(tmp_path: Path) -> None:
    result = _run(tmp_path)

    assert result.status == EDGE_VALIDATION_NO_JOIN_BOARD
    assert result.output_path.exists()
    assert result.summary_path.exists()
    assert result.diagnostics_path.exists()
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["input_row_count"] == 0
    assert diagnostics["output_row_count"] == 0
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False


def test_missing_required_column_is_schema_invalid(tmp_path: Path) -> None:
    row = _row()
    row.pop("abs_edge")
    _write_join_board(tmp_path, [row])

    result = _run(tmp_path)

    assert result.status == EDGE_VALIDATION_SCHEMA_INVALID
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["schema_missing_required_columns"] == ["abs_edge"]
    board = pd.read_csv(result.output_path)
    assert not bool(board.iloc[0]["passes_min_edge"])
    assert "missing_abs_edge" in board.iloc[0]["research_rejection_reasons"]


def test_no_validated_rows_uses_explicit_status_and_reason_counts(
    tmp_path: Path,
) -> None:
    _write_join_board(
        tmp_path,
        [
            _row(
                player_name="Tiny Edge",
                abs_edge=0.25,
                edge_bucket="tiny_edge",
            )
        ],
    )

    result = _run(tmp_path)

    assert result.status == EDGE_VALIDATION_NO_RESEARCH_VALIDATED_ROWS
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["research_validated_count"] == 0
    assert diagnostics["rejected_reason_counts"] == {"edge_below_minimum": 1}
    summary = result.summary_path.read_text(encoding="utf-8")
    assert "WARNING: Research-only validation. No row is betting-approved." in summary
    assert "No picks, MarketProp rows, Elite rows, Kelly calls" in summary


def test_max_events_keeps_first_distinct_event_only(tmp_path: Path) -> None:
    _write_join_board(
        tmp_path,
        [
            _row(player_name="First Event", provider_event_id="evt_1"),
            _row(player_name="Second Event", provider_event_id="evt_2"),
        ],
    )

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path)
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert board["player_name"].tolist() == ["First Event"]
    assert diagnostics["input_row_count"] == 2
    assert diagnostics["output_row_count"] == 1
    assert any("limited from 2 events to 1" in warning for warning in diagnostics["warnings"])
