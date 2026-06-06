from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.run_odds_improvement_tracker as odds_tracker
from scripts.run_odds_improvement_tracker import (
    BETTER_LINE_NOW,
    BETTING_APPROVAL_STATUS,
    FAIR_PRICE_NOW,
    LOW_PRIORITY_MONITOR,
    MONITOR_FOR_PRICE_DROP,
    ODDS_IMPROVEMENT_TRACKER_OK,
    SUMMARY_NOTICE,
    run_odds_improvement_tracker,
)


PREDICTION_DATE = "2026-06-05"


def _research_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "research"


def _diagnostics_dir(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "runtime" / "diagnostics"


def _quality_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"quality_gated_research_board_{PREDICTION_DATE}.csv"
    )


def _market_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"market_validation_board_{PREDICTION_DATE}.csv"
    )


def _quality_row(
    player_name: str,
    *,
    side: str = "over",
    line: float = 20.5,
    projection_value: float = 24.0,
    side_adjusted_edge: float = 3.5,
    american_odds: int = -220,
    research_category: str = "price_sensitive_watchlist",
    projection_confidence_tier: str = "high_research_confidence",
    odds_quality_flag: str = "heavy_juice",
    line_quality_flag: str = "strong_edge_line",
    minutes_quality_flag: str = "stable_minutes",
    eligible_for_betting: bool = True,
) -> dict[str, Any]:
    return {
        "research_board_rank": 1,
        "player_name": player_name,
        "market_type": "player_points",
        "side": side,
        "line": line,
        "projection_value": projection_value,
        "side_adjusted_edge": side_adjusted_edge,
        "sportsbook": "OriginalBook",
        "american_odds": american_odds,
        "projection_confidence_tier": projection_confidence_tier,
        "odds_quality_flag": odds_quality_flag,
        "line_quality_flag": line_quality_flag,
        "minutes_quality_flag": minutes_quality_flag,
        "research_category": research_category,
        "eligible_for_betting": eligible_for_betting,
    }


def _market_row(
    player_name: str,
    *,
    side: str = "over",
    line: float = 20.5,
    american_odds: int | None = -110,
    sportsbook: str = "MarketBook",
) -> dict[str, Any]:
    return {
        "player_name": player_name,
        "market_type": "player_points",
        "side": side,
        "line": line,
        "american_odds": american_odds,
        "sportsbook": sportsbook,
        "eligible_for_betting": False,
    }


def _write_inputs(
    tmp_path: Path,
    quality_rows: list[dict[str, Any]],
    market_rows: list[dict[str, Any]],
) -> None:
    _research_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(quality_rows).to_csv(_quality_path(tmp_path), index=False)
    pd.DataFrame(market_rows).to_csv(_market_path(tmp_path), index=False)


def _run(tmp_path: Path):
    return run_odds_improvement_tracker(
        target_date=PREDICTION_DATE,
        quality_board=_quality_path(tmp_path),
        market_board=_market_path(tmp_path),
        output_dir=_research_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
    )


def test_finds_fair_price_now(tmp_path: Path) -> None:
    _write_inputs(
        tmp_path,
        [_quality_row("Fair Player")],
        [
            _market_row(
                "Fair Player",
                line=21.5,
                american_odds=-145,
                sportsbook="FairBook",
            ),
            _market_row(
                "Fair Player",
                line=20.5,
                american_odds=-210,
                sportsbook="HeavyBook",
            ),
        ],
    )

    result = _run(tmp_path)

    assert result.status == ODDS_IMPROVEMENT_TRACKER_OK
    tracker = pd.read_csv(result.output_path)
    assert tracker.loc[0, "improvement_category"] == FAIR_PRICE_NOW
    assert tracker.loc[0, "best_available_sportsbook"] == "FairBook"
    assert tracker.loc[0, "best_available_line"] == 21.5
    assert tracker.loc[0, "best_available_american_odds"] == -145
    assert tracker.loc[
        0,
        "best_available_odds_quality_flag",
    ] == "favorable_or_fair"
    assert tracker.loc[0, "best_available_side_adjusted_edge"] == 2.5


def test_finds_better_line_for_over(tmp_path: Path) -> None:
    _write_inputs(
        tmp_path,
        [
            _quality_row(
                "Over Player",
                line=20.5,
                projection_value=24.0,
                side_adjusted_edge=3.5,
            )
        ],
        [
            _market_row(
                "Over Player",
                line=19.5,
                american_odds=-180,
            )
        ],
    )

    result = _run(tmp_path)

    tracker = pd.read_csv(result.output_path)
    assert tracker.loc[0, "improvement_category"] == BETTER_LINE_NOW
    assert bool(tracker.loc[0, "better_line_available"]) is True
    assert tracker.loc[0, "best_available_line"] == 19.5
    assert tracker.loc[0, "best_available_side_adjusted_edge"] == 4.5


def test_finds_better_line_for_under(tmp_path: Path) -> None:
    _write_inputs(
        tmp_path,
        [
            _quality_row(
                "Under Player",
                side="under",
                line=20.5,
                projection_value=17.0,
                side_adjusted_edge=3.5,
            )
        ],
        [
            _market_row(
                "Under Player",
                side="under",
                line=21.5,
                american_odds=-175,
            )
        ],
    )

    result = _run(tmp_path)

    tracker = pd.read_csv(result.output_path)
    assert tracker.loc[0, "improvement_category"] == BETTER_LINE_NOW
    assert bool(tracker.loc[0, "better_line_available"]) is True
    assert tracker.loc[0, "best_available_line"] == 21.5
    assert tracker.loc[0, "best_available_side_adjusted_edge"] == 4.5


def test_marks_monitor_for_price_drop_when_all_prices_are_heavy(
    tmp_path: Path,
) -> None:
    _write_inputs(
        tmp_path,
        [_quality_row("Heavy Player")],
        [
            _market_row(
                "Heavy Player",
                line=20.5,
                american_odds=-190,
                sportsbook="HeavyOne",
            ),
            _market_row(
                "Heavy Player",
                line=21.5,
                american_odds=-175,
                sportsbook="HeavyTwo",
            ),
        ],
    )

    result = _run(tmp_path)

    tracker = pd.read_csv(result.output_path)
    assert (
        tracker.loc[0, "improvement_category"]
        == MONITOR_FOR_PRICE_DROP
    )
    assert bool(tracker.loc[0, "better_price_available"]) is True


def test_marks_low_priority_monitor_for_low_confidence(
    tmp_path: Path,
) -> None:
    _write_inputs(
        tmp_path,
        [
            _quality_row(
                "Low Player",
                research_category="low_confidence_review",
                projection_confidence_tier="low_research_confidence",
            )
        ],
        [_market_row("Low Player", american_odds=-110)],
    )

    result = _run(tmp_path)

    tracker = pd.read_csv(result.output_path)
    assert tracker.loc[0, "improvement_category"] == LOW_PRIORITY_MONITOR


def test_preserves_research_only_fields_and_writes_diagnostics(
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

    _write_inputs(
        tmp_path,
        [_quality_row("Safe Player", eligible_for_betting=True)],
        [_market_row("Safe Player", american_odds=-110)],
    )
    result = _run(tmp_path)

    tracker = pd.read_csv(result.output_path)
    summary = result.summary_path.read_text(encoding="utf-8")
    diagnostics = json.loads(
        result.diagnostics_path.read_text(encoding="utf-8")
    )
    assert tracker["eligible_for_betting"].tolist() == [False]
    assert tracker["betting_approval_status"].tolist() == [
        BETTING_APPROVAL_STATUS
    ]
    assert SUMMARY_NOTICE in summary
    assert diagnostics["input_quality_row_count"] == 1
    assert diagnostics["input_market_row_count"] == 1
    assert diagnostics["output_row_count"] == 1
    assert diagnostics["fair_price_now_count"] == 1
    assert diagnostics["eligible_for_betting_any_true"] is False
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(odds_tracker, "MarketProp")
    assert [path.read_text(encoding="utf-8") for path in sentinels] == [
        "existing\n"
    ] * 4
    assert sorted(path.name for path in operator_dir.iterdir()) == sorted(
        path.name for path in sentinels
    )


def test_accepts_string_directory_arguments(tmp_path: Path) -> None:
    _write_inputs(
        tmp_path,
        [_quality_row("CLI Player")],
        [_market_row("CLI Player")],
    )

    result = run_odds_improvement_tracker(
        target_date=PREDICTION_DATE,
        quality_board=str(_quality_path(tmp_path)),
        market_board=str(_market_path(tmp_path)),
        output_dir=str(_research_dir(tmp_path)),
        diagnostics_dir=str(_diagnostics_dir(tmp_path)),
    )

    assert result.status == ODDS_IMPROVEMENT_TRACKER_OK
    assert result.output_path.exists()
    assert result.summary_path.exists()
    assert result.diagnostics_path.exists()
