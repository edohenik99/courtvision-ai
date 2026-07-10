from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from tools.summarize_live_hr_grades import main, summarize_live_hr_grades


COLUMNS = (
    "bookmaker",
    "player",
    "price",
    "result",
    "profit_1u",
    "home_team",
    "away_team",
)


def _write_csv(
    path: Path,
    rows: list[dict[str, object]],
    columns: tuple[str, ...] = COLUMNS,
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "bookmaker": "Book A",
            "player": "Batter One",
            "price": 250,
            "result": "win",
            "profit_1u": 2.5,
            "home_team": "Home Club",
            "away_team": "Away Club",
        },
        {
            "bookmaker": "Book A",
            "player": "Batter Two",
            "price": 450,
            "result": "loss",
            "profit_1u": -1,
            "home_team": "Home Club",
            "away_team": "Away Club",
        },
        {
            "bookmaker": "Book B",
            "player": "Batter One",
            "price": 650,
            "result": "win",
            "profit_1u": 6.5,
            "home_team": "Other Home",
            "away_team": "Other Away",
        },
        {
            "bookmaker": "Book B",
            "player": "Batter Three",
            "price": 900,
            "result": "loss",
            "profit_1u": -1,
            "home_team": "Other Home",
            "away_team": "Other Away",
        },
        {
            "bookmaker": "Book B",
            "player": "Batter Four",
            "price": 1200,
            "result": "loss",
            "profit_1u": -1,
            "home_team": "Other Home",
            "away_team": "Other Away",
        },
    ]


def test_summary_file_generated_with_correct_overall_profit_and_roi(
    tmp_path: Path,
) -> None:
    grade_csv = tmp_path / "grades.csv"
    report = tmp_path / "reports" / "summary.md"
    _write_csv(grade_csv, _rows())

    summary = summarize_live_hr_grades("2026-07-06", grade_csv, report)

    assert report.is_file()
    assert summary.overall.profit == Decimal("6.0")
    assert summary.overall.roi == Decimal("1.2")
    text = report.read_text(encoding="utf-8")
    assert "- Total profit_1u: 6.00" in text
    assert "- ROI: 120.00%" in text
    assert "Away Club @ Home Club" in text


def test_bookmaker_grouping_works(tmp_path: Path) -> None:
    grade_csv = tmp_path / "grades.csv"
    _write_csv(grade_csv, _rows())

    summary = summarize_live_hr_grades(
        "2026-07-06", grade_csv, tmp_path / "summary.md"
    )

    assert summary.bookmaker["Book A"].rows == 2
    assert summary.bookmaker["Book A"].wins == 1
    assert summary.bookmaker["Book A"].profit == pytest.approx(1.5)
    assert summary.bookmaker["Book B"].losses == 2


def test_odds_bucket_grouping_works(tmp_path: Path) -> None:
    grade_csv = tmp_path / "grades.csv"
    _write_csv(grade_csv, _rows())

    summary = summarize_live_hr_grades(
        "2026-07-06", grade_csv, tmp_path / "summary.md"
    )

    assert summary.odds_bucket["< +300"].rows == 1
    assert summary.odds_bucket["+300 to +499"].rows == 1
    assert summary.odds_bucket["+500 to +799"].rows == 1
    assert summary.odds_bucket["+800 to +1199"].rows == 1
    assert summary.odds_bucket["+1200+"].rows == 1


def test_missing_required_columns_causes_clear_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grade_csv = tmp_path / "grades.csv"
    report = tmp_path / "summary.md"
    columns = tuple(column for column in COLUMNS if column != "profit_1u")
    _write_csv(grade_csv, [], columns)

    exit_code = main(
        [
            "--date",
            "2026-07-06",
            "--grade-csv",
            str(grade_csv),
            "--output",
            str(report),
        ]
    )

    assert exit_code == 1
    assert "missing required columns for: profit" in capsys.readouterr().err
    assert not report.exists()


def test_summary_reports_void_candidates_and_manual_review_rows(
    tmp_path: Path,
) -> None:
    columns = (
        "event_id",
        "bookmaker",
        "player",
        "price",
        "result",
        "profit_1u",
        "home_team",
        "away_team",
        "grade_status",
        "game_status",
        "result_reason",
    )
    grade_csv = tmp_path / "grades.csv"
    report = tmp_path / "summary.md"
    _write_csv(
        grade_csv,
        [
            {
                "event_id": "event-1",
                "bookmaker": "Book A",
                "player": "Winning Batter",
                "price": 300,
                "result": "win",
                "profit_1u": 3,
                "home_team": "Home Club",
                "away_team": "Away Club",
                "grade_status": "graded",
                "game_status": "final",
                "result_reason": "",
            },
            {
                "event_id": "event-2",
                "bookmaker": "Book A",
                "player": "Owen Caissie",
                "price": 550,
                "result": "",
                "profit_1u": "",
                "home_team": "Miami Marlins",
                "away_team": "Seattle Mariners",
                "grade_status": "void_candidate",
                "game_status": "void_candidate",
                "result_reason": "player_missing_from_boxscore_roster",
            },
            {
                "event_id": "event-3",
                "bookmaker": "Book A",
                "player": "Ambiguous Batter",
                "price": 700,
                "result": "",
                "profit_1u": "",
                "home_team": "Pittsburgh Pirates",
                "away_team": "Atlanta Braves",
                "grade_status": "manual_review_required",
                "game_status": "manual_review_required",
                "result_reason": "ambiguous_normalized_player_match",
            },
        ],
        columns,
    )

    summary = summarize_live_hr_grades("2026-07-06", grade_csv, report)

    assert summary.total_rows == 3
    assert summary.overall.rows == 1
    assert summary.overall.losses == 0
    assert summary.void_candidate_rows == 1
    assert summary.manual_review_rows == 1
    text = report.read_text(encoding="utf-8")
    assert "- Void candidate rows: 1" in text
    assert "- Manual review rows: 1" in text
    assert "Owen Caissie" in text
    assert "event-2" in text
    assert "Seattle Mariners @ Miami Marlins" in text
    assert "player_missing_from_boxscore_roster" in text
    assert "Ambiguous Batter" in text
    assert "ambiguous_normalized_player_match" in text
