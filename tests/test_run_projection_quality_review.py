from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.run_projection_quality_review as projection_quality_review
from scripts.run_projection_quality_review import (
    HIGH_RESEARCH_CONFIDENCE,
    LOW_RESEARCH_CONFIDENCE,
    MANUAL_REVIEW_ONLY,
    MEDIUM_RESEARCH_CONFIDENCE,
    PROJECTION_QUALITY_REVIEW_INPUT_MISSING,
    PROJECTION_QUALITY_REVIEW_NO_OUTPUT_ROWS,
    PROJECTION_QUALITY_REVIEW_OK,
    PROJECTION_QUALITY_REVIEW_SCHEMA_INVALID,
    run_projection_quality_review,
)


PREDICTION_DATE = "2026-06-05"


def _research_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "diagnostics"


def _join_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"market_projection_join_{PREDICTION_DATE}.csv"
    )


def _manual_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"manual_review_board_{PREDICTION_DATE}.csv"
    )


def _row(
    player_name: str,
    *,
    projection_value: float | None = 24.0,
    line: float | None = 20.0,
    side_adjusted_edge: float | None = 4.0,
    american_odds: float | None = -110,
    projection_quality_flag: str = "research_projection_only",
    minutes_factor: float | None = 1.0,
    eligible_for_betting: bool = False,
) -> dict[str, Any]:
    return {
        "player_name": player_name,
        "market_type": "player_points",
        "side": "over",
        "line": line,
        "projection_value": projection_value,
        "side_adjusted_edge": side_adjusted_edge,
        "sportsbook": "DraftKings",
        "american_odds": american_odds,
        "projection_source_type": "model_projection",
        "projection_quality_flag": projection_quality_flag,
        "minutes_factor": minutes_factor,
        "eligible_for_betting": eligible_for_betting,
    }


def _manual_row(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in source.items()
        if key != "minutes_factor"
    }


def _write_inputs(
    tmp_path: Path,
    join_rows: list[dict[str, Any]],
    manual_rows: list[dict[str, Any]],
) -> None:
    research_dir = _research_dir(tmp_path)
    research_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(join_rows).to_csv(_join_path(tmp_path), index=False)
    pd.DataFrame(manual_rows).to_csv(_manual_path(tmp_path), index=False)


def _run(tmp_path: Path):
    return run_projection_quality_review(
        target_date=PREDICTION_DATE,
        join_board=_join_path(tmp_path),
        manual_board=_manual_path(tmp_path),
        output_dir=_research_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
    )


def _read_diagnostics(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_assigns_confidence_tiers_and_quality_flags(tmp_path: Path) -> None:
    join_rows = [
        _row("High", american_odds=-200),
        _row(
            "Medium",
            projection_value=22.0,
            side_adjusted_edge=2.0,
            projection_quality_flag="acceptable",
            minutes_factor=1.10,
        ),
        _row(
            "Low",
            projection_value=21.0,
            side_adjusted_edge=1.0,
            minutes_factor=1.15,
        ),
        _row(
            "Manual",
            projection_value=None,
            side_adjusted_edge=4.0,
            minutes_factor=1.0,
            eligible_for_betting=True,
        ),
    ]
    _write_inputs(
        tmp_path,
        join_rows,
        [_manual_row(row) for row in join_rows],
    )

    result = _run(tmp_path)

    assert result.status == PROJECTION_QUALITY_REVIEW_OK
    board = pd.read_csv(result.output_path).set_index("player_name")
    assert board["projection_confidence_tier"].to_dict() == {
        "High": HIGH_RESEARCH_CONFIDENCE,
        "Medium": MEDIUM_RESEARCH_CONFIDENCE,
        "Low": LOW_RESEARCH_CONFIDENCE,
        "Manual": MANUAL_REVIEW_ONLY,
    }
    assert board.loc["High", "odds_quality_flag"] == "heavy_juice"
    assert board.loc["Medium", "line_quality_flag"] == "review_edge_line"
    assert board.loc["Low", "line_quality_flag"] == "weak_edge_line"
    assert board.loc["Low", "minutes_quality_flag"] == "minutes_shift_review"
    assert board.loc["Manual", "review_warning_flags"].find(
        "missing_projection"
    ) >= 0
    assert board["eligible_for_betting"].tolist() == [False] * 4

    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert diagnostics["input_join_row_count"] == 4
    assert diagnostics["input_manual_row_count"] == 4
    assert diagnostics["output_row_count"] == 4
    assert diagnostics["confidence_tier_counts"] == {
        HIGH_RESEARCH_CONFIDENCE: 1,
        MEDIUM_RESEARCH_CONFIDENCE: 1,
        LOW_RESEARCH_CONFIDENCE: 1,
        MANUAL_REVIEW_ONLY: 1,
    }
    assert diagnostics["odds_quality_flag_counts"] == {
        "heavy_juice": 1,
        "favorable_or_fair": 3,
    }
    assert diagnostics["line_quality_flag_counts"] == {
        "strong_edge_line": 2,
        "review_edge_line": 1,
        "weak_edge_line": 1,
    }
    assert diagnostics["minutes_quality_flag_counts"] == {
        "stable_minutes": 3,
        "minutes_shift_review": 1,
    }
    assert diagnostics["high_research_confidence_count"] == 1
    assert diagnostics["medium_research_confidence_count"] == 1
    assert diagnostics["low_research_confidence_count"] == 1
    assert diagnostics["manual_review_only_count"] == 1
    assert diagnostics["eligible_for_betting_any_true"] is False


def test_derives_minutes_factor_from_joined_minutes_context(
    tmp_path: Path,
) -> None:
    join_row = _row("Derived", minutes_factor=None)
    join_row["projection_min_avg"] = 30.0
    join_row["projection_min_recent"] = 27.0
    manual_row = _manual_row(join_row)
    manual_row.pop("projection_min_avg")
    manual_row.pop("projection_min_recent")
    _write_inputs(tmp_path, [join_row], [manual_row])

    result = _run(tmp_path)

    board = pd.read_csv(result.output_path)
    assert board.loc[0, "minutes_quality_flag"] == "stable_minutes"
    assert (
        board.loc[0, "projection_confidence_tier"]
        == HIGH_RESEARCH_CONFIDENCE
    )


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

    row = _row("Safe")
    _write_inputs(tmp_path, [row], [_manual_row(row)])
    result = _run(tmp_path)

    summary = result.summary_path.read_text(encoding="utf-8")
    diagnostics = _read_diagnostics(result.diagnostics_path)
    assert "Research-only projection review" in summary
    assert "high_research_confidence_count: 1" in summary
    assert "eligible_for_betting_any_true: False" in summary
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(projection_quality_review, "MarketProp")
    assert [path.read_text(encoding="utf-8") for path in sentinels] == [
        "existing\n"
    ] * 4
    assert sorted(path.name for path in operator_dir.iterdir()) == sorted(
        path.name for path in sentinels
    )


def test_non_success_statuses_write_research_only_artifacts(
    tmp_path: Path,
) -> None:
    missing = _run(tmp_path)
    assert missing.status == PROJECTION_QUALITY_REVIEW_INPUT_MISSING
    assert pd.read_csv(missing.output_path).empty

    row = _row("Invalid")
    invalid_manual = _manual_row(row)
    invalid_manual.pop("projection_quality_flag")
    _write_inputs(tmp_path, [row], [invalid_manual])
    invalid = _run(tmp_path)
    assert invalid.status == PROJECTION_QUALITY_REVIEW_SCHEMA_INVALID
    invalid_board = pd.read_csv(invalid.output_path)
    assert invalid_board.loc[0, "projection_confidence_tier"] == MANUAL_REVIEW_ONLY

    pd.DataFrame([row]).to_csv(_join_path(tmp_path), index=False)
    pd.DataFrame(columns=_manual_row(row).keys()).to_csv(
        _manual_path(tmp_path),
        index=False,
    )
    no_rows = _run(tmp_path)
    assert no_rows.status == PROJECTION_QUALITY_REVIEW_NO_OUTPUT_ROWS
    assert pd.read_csv(no_rows.output_path).empty
