from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import scripts.build_daily_research_report as daily_report
from scripts.build_daily_research_report import (
    DAILY_RESEARCH_REPORT_INPUT_MISSING,
    DAILY_RESEARCH_REPORT_NO_OUTPUT_ROWS,
    DAILY_RESEARCH_REPORT_OK,
    DAILY_RESEARCH_REPORT_SCHEMA_INVALID,
    REPORT_NOTICE,
    REPORT_SECTIONS,
    SAFETY_STATUS,
    build_daily_research_report,
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


def _odds_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"odds_improvement_tracker_{PREDICTION_DATE}.csv"
    )


def _projection_path(tmp_path: Path) -> Path:
    return _research_dir(tmp_path) / (
        f"projection_quality_review_{PREDICTION_DATE}.csv"
    )


def _quality_row(
    player_name: str,
    *,
    rank: int,
    confidence: str = "high_research_confidence",
    category: str = "research_watchlist",
    odds_quality: str = "favorable_or_fair",
    american_odds: int = -110,
    eligible_for_betting: bool = False,
) -> dict[str, Any]:
    return {
        "research_board_rank": rank,
        "review_rank": rank,
        "player_name": player_name,
        "market_type": "player_points",
        "side": "over",
        "line": 20.5,
        "projection_value": 24.0,
        "side_adjusted_edge": 3.5,
        "sportsbook": "SourceBook",
        "american_odds": american_odds,
        "projection_confidence_tier": confidence,
        "odds_quality_flag": odds_quality,
        "research_category": category,
        "final_research_note": f"{player_name} research review only.",
        "eligible_for_betting": eligible_for_betting,
    }


def _odds_row(
    source: dict[str, Any],
    *,
    improvement_category: str,
    best_sportsbook: str = "BestBook",
    best_line: float = 20.5,
    best_odds: int = -110,
) -> dict[str, Any]:
    return {
        **source,
        "best_available_sportsbook": best_sportsbook,
        "best_available_line": best_line,
        "best_available_american_odds": best_odds,
        "best_available_side_adjusted_edge": 3.5,
        "improvement_category": improvement_category,
        "improvement_note": (
            f"{improvement_category} at {best_sportsbook}. "
            "Research review only."
        ),
    }


def _projection_row(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "player_name": source["player_name"],
        "market_type": source["market_type"],
        "side": source["side"],
        "line": source["line"],
        "projection_confidence_tier": source[
            "projection_confidence_tier"
        ],
        "eligible_for_betting": source["eligible_for_betting"],
    }


def _sample_rows() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    quality_rows = [
        _quality_row("Watch Player", rank=1),
        _quality_row(
            "Fair Price Player",
            rank=2,
            category="price_sensitive_watchlist",
            odds_quality="heavy_juice",
            american_odds=-220,
            eligible_for_betting=True,
        ),
        _quality_row(
            "Price Sensitive Player",
            rank=3,
            confidence="medium_research_confidence",
            category="price_sensitive_watchlist",
            odds_quality="heavy_juice",
            american_odds=-190,
        ),
        _quality_row(
            "Monitor Player",
            rank=4,
            confidence="low_research_confidence",
            category="low_confidence_review",
        ),
        _quality_row(
            "Excluded Player",
            rank=5,
            confidence="medium_research_confidence",
            category="excluded_research_only",
        ),
    ]
    odds_rows = [
        _odds_row(
            quality_rows[0],
            improvement_category="fair_price_now",
            best_sportsbook="WatchBook",
            best_line=21.5,
            best_odds=-120,
        ),
        _odds_row(
            quality_rows[1],
            improvement_category="fair_price_now",
            best_sportsbook="FairBook",
            best_line=20.5,
            best_odds=-125,
        ),
        _odds_row(
            quality_rows[2],
            improvement_category="monitor_for_price_drop",
        ),
        _odds_row(
            quality_rows[3],
            improvement_category="low_priority_monitor",
        ),
        _odds_row(
            quality_rows[4],
            improvement_category="no_improvement_available",
        ),
    ]
    projection_rows = [_projection_row(row) for row in quality_rows]
    return quality_rows, odds_rows, projection_rows


def _write_inputs(
    tmp_path: Path,
    quality_rows: list[dict[str, Any]],
    odds_rows: list[dict[str, Any]],
    projection_rows: list[dict[str, Any]],
) -> None:
    _research_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(quality_rows).to_csv(_quality_path(tmp_path), index=False)
    pd.DataFrame(odds_rows).to_csv(_odds_path(tmp_path), index=False)
    pd.DataFrame(projection_rows).to_csv(
        _projection_path(tmp_path),
        index=False,
    )


def _run(tmp_path: Path, *, top_n: int = 15):
    return build_daily_research_report(
        target_date=PREDICTION_DATE,
        quality_board=_quality_path(tmp_path),
        odds_tracker=_odds_path(tmp_path),
        projection_review=_projection_path(tmp_path),
        output_dir=_research_dir(tmp_path),
        diagnostics_dir=_diagnostics_dir(tmp_path),
        top_n=top_n,
    )


def test_builds_markdown_with_required_sections_and_research_rows(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path, *_sample_rows())

    result = _run(tmp_path)

    assert result.status == DAILY_RESEARCH_REPORT_OK
    report = result.report_path.read_text(encoding="utf-8")
    assert result.report_path.name == (
        f"daily_research_report_{PREDICTION_DATE}.md"
    )
    for section in REPORT_SECTIONS:
        assert f"## {section}" in report
    assert "Watch Player" in report
    assert "Fair Price Player" in report
    assert "FairBook" in report
    assert "Price Sensitive Player" in report
    assert "Monitor Player" in report
    assert "Excluded Player" in report


def test_summarizes_full_input_counts_independent_of_top_n(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path, *_sample_rows())

    result = _run(tmp_path, top_n=1)

    report = result.report_path.read_text(encoding="utf-8")
    diagnostics = result.diagnostics
    assert diagnostics["quality_board_row_count"] == 5
    assert diagnostics["odds_tracker_row_count"] == 5
    assert diagnostics["projection_review_row_count"] == 5
    assert diagnostics["total_research_rows"] == 5
    assert diagnostics["high_research_confidence_count"] == 2
    assert diagnostics["medium_research_confidence_count"] == 2
    assert diagnostics["low_research_confidence_count"] == 1
    assert diagnostics["research_watchlist_count"] == 1
    assert diagnostics["fair_price_now_count"] == 2
    assert diagnostics["price_sensitive_watchlist_count"] == 2
    assert diagnostics["heavy_juice_count"] == 2
    assert diagnostics["low_priority_count"] == 1
    assert diagnostics["low_confidence_review_count"] == 1
    assert diagnostics["excluded_research_only_count"] == 1
    assert "Total research rows: 5" in report
    assert "high=2, medium=2, low=1" in report
    assert "Showing 1 of 2 research-only rows" in report


def test_preserves_research_only_language_and_forces_eligibility_false(
    tmp_path: Path,
) -> None:
    _write_inputs(tmp_path, *_sample_rows())

    result = _run(tmp_path)

    report = result.report_path.read_text(encoding="utf-8")
    summary = result.summary_path.read_text(encoding="utf-8")
    assert REPORT_NOTICE in report
    assert report.count("not betting-approved picks") >= len(
        REPORT_SECTIONS
    )
    assert "Eligible for Betting" in report
    assert "| False |" in report
    assert "| True |" not in report
    assert "eligible_for_betting=True" not in report
    assert "eligible_for_betting_any_true: False" in summary
    assert result.diagnostics[
        "source_eligible_for_betting_any_true"
    ] is True
    assert result.diagnostics["eligible_for_betting_any_true"] is False
    assert result.diagnostics["safety_status"] == SAFETY_STATUS


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
    _write_inputs(tmp_path, *_sample_rows())

    result = _run(tmp_path)

    diagnostics = json.loads(
        result.diagnostics_path.read_text(encoding="utf-8")
    )
    assert result.summary_path.exists()
    assert diagnostics["status"] == DAILY_RESEARCH_REPORT_OK
    assert diagnostics["report_sections_created"] == REPORT_SECTIONS
    assert diagnostics["market_prop_rows_created"] == 0
    assert diagnostics["elite_rows_created"] == 0
    assert diagnostics["kelly_called"] is False
    assert diagnostics["operator_betting_boards_written"] == []
    assert not hasattr(daily_report, "MarketProp")
    assert [path.read_text(encoding="utf-8") for path in sentinels] == [
        "existing\n"
    ] * 4
    assert sorted(path.name for path in operator_dir.iterdir()) == sorted(
        path.name for path in sentinels
    )


def test_non_success_statuses_still_write_research_report_bundle(
    tmp_path: Path,
) -> None:
    missing = _run(tmp_path)
    assert missing.status == DAILY_RESEARCH_REPORT_INPUT_MISSING
    assert missing.report_path.exists()
    assert missing.summary_path.exists()
    assert missing.diagnostics_path.exists()

    quality_rows, odds_rows, projection_rows = _sample_rows()
    invalid_quality = [dict(quality_rows[0])]
    invalid_quality[0].pop("research_category")
    _write_inputs(
        tmp_path,
        invalid_quality,
        [odds_rows[0]],
        [projection_rows[0]],
    )
    invalid = _run(tmp_path)
    assert invalid.status == DAILY_RESEARCH_REPORT_SCHEMA_INVALID

    pd.DataFrame(columns=quality_rows[0].keys()).to_csv(
        _quality_path(tmp_path),
        index=False,
    )
    pd.DataFrame(columns=odds_rows[0].keys()).to_csv(
        _odds_path(tmp_path),
        index=False,
    )
    pd.DataFrame(columns=projection_rows[0].keys()).to_csv(
        _projection_path(tmp_path),
        index=False,
    )
    no_rows = _run(tmp_path)
    assert no_rows.status == DAILY_RESEARCH_REPORT_NO_OUTPUT_ROWS
