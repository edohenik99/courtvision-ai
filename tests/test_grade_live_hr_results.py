from __future__ import annotations

import csv
from pathlib import Path

import pytest

from tools.grade_live_hr_results import (
    GRADE_COLUMNS,
    default_output_path,
    implied_probability,
    main,
    win_profit_1u,
)


ODDS_COLUMNS = (
    "snapshot_time",
    "event_id",
    "commence_time",
    "player",
    "side",
    "price",
    "point",
)
RESULTS_COLUMNS = (
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
    "result_reason",
)


def _write_csv(
    path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _odds_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "snapshot_time": "2026-07-01T18:20:52Z",
        "event_id": "event-1",
        "commence_time": "2026-07-06T23:10:00Z",
        "player": "Winning Batter",
        "side": "Over",
        "price": 300,
        "point": 0.5,
    }
    row.update(overrides)
    return row


def _run_with_temp_csvs(
    tmp_path: Path,
    odds_rows: list[dict[str, object]],
    result_rows: list[dict[str, object]],
    extra_args: list[str] | None = None,
) -> tuple[int, Path]:
    odds_path = tmp_path / "odds.csv"
    results_path = tmp_path / "results.csv"
    output_path = tmp_path / "graded.csv"
    _write_csv(odds_path, ODDS_COLUMNS, odds_rows)
    _write_csv(results_path, RESULTS_COLUMNS, result_rows)
    args = [
        "--odds-csv",
        str(odds_path),
        "--results-csv",
        str(results_path),
        "--output-csv",
        str(output_path),
    ]
    args.extend(extra_args or [])
    exit_code = main(args)
    return exit_code, output_path


def test_grades_win_loss_and_missing_result_and_prints_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    odds_rows = [
        _odds_row(),
        _odds_row(event_id="event-2", player="Losing Batter", price=-200),
        _odds_row(event_id="event-3", player="Missing Batter", price=150),
    ]
    result_rows = [
        {
            "event_id": "event-1",
            "player": "Winning Batter",
            "actual_home_runs": 2,
            "game_status": "final",
        },
        {
            "event_id": "event-2",
            "player": "Losing Batter",
            "actual_home_runs": 0,
            "game_status": "final",
        },
    ]

    exit_code, output_path = _run_with_temp_csvs(
        tmp_path, odds_rows, result_rows
    )

    assert exit_code == 0
    with output_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == [*ODDS_COLUMNS, *GRADE_COLUMNS]
        rows = list(reader)

    assert rows[0]["actual_home_runs"] == "2"
    assert rows[0]["game_status"] == "final"
    assert rows[0]["result"] == "win"
    assert rows[0]["grade_status"] == "graded"
    assert float(rows[0]["stake_1u"]) == 1.0
    assert float(rows[0]["profit_1u"]) == 3.0

    assert rows[1]["result"] == "loss"
    assert rows[1]["grade_status"] == "graded"
    assert float(rows[1]["profit_1u"]) == -1.0

    assert rows[2]["actual_home_runs"] == ""
    assert rows[2]["game_status"] == ""
    assert rows[2]["result"] == ""
    assert rows[2]["profit_1u"] == ""
    assert rows[2]["grade_status"] == "missing_result"

    output = capsys.readouterr().out
    assert "Total rows: 3" in output
    assert "Graded rows: 2" in output
    assert "Missing result rows: 1" in output
    assert "Wins: 1" in output
    assert "Losses: 1" in output
    assert "Total profit_1u: 2.00" in output
    assert "ROI: 100.00%" in output


@pytest.mark.parametrize(
    ("odds", "expected_probability", "expected_win_profit"),
    [
        (300, 0.25, 3.0),
        (-200, 2 / 3, 0.5),
    ],
)
def test_implied_probability_and_american_odds_profit_math(
    odds: int, expected_probability: float, expected_win_profit: float
) -> None:
    assert implied_probability(odds) == pytest.approx(expected_probability)
    assert win_profit_1u(odds) == pytest.approx(expected_win_profit)


@pytest.mark.parametrize("invalid_input", ["odds", "results"])
def test_schema_failure_exits_one_without_writing_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    invalid_input: str,
) -> None:
    odds_path = tmp_path / "odds.csv"
    results_path = tmp_path / "results.csv"
    output_path = tmp_path / "graded.csv"

    odds_columns = (
        tuple(column for column in ODDS_COLUMNS if column != "price")
        if invalid_input == "odds"
        else ODDS_COLUMNS
    )
    results_columns = (
        tuple(column for column in RESULTS_COLUMNS if column != "actual_home_runs")
        if invalid_input == "results"
        else RESULTS_COLUMNS
    )
    _write_csv(odds_path, odds_columns, [])
    _write_csv(results_path, results_columns, [])

    exit_code = main(
        [
            "--odds-csv",
            str(odds_path),
            "--results-csv",
            str(results_path),
            "--output-csv",
            str(output_path),
        ]
    )

    assert exit_code == 1
    assert "missing required columns" in capsys.readouterr().err
    assert not output_path.exists()


def test_missing_input_file_exits_one(tmp_path: Path) -> None:
    assert main(["--odds-csv", str(tmp_path / "missing.csv")]) == 1


def test_date_scoped_grading_grades_only_requested_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    odds_rows = [
        _odds_row(),
        _odds_row(
            event_id="event-2",
            player="Other Date Batter",
            commence_time="2026-07-05T19:05:00Z",
        ),
    ]
    result_rows = [
        {
            "event_id": "event-1",
            "player": "Winning Batter",
            "actual_home_runs": 1,
            "game_status": "final",
        },
        {
            "event_id": "event-2",
            "player": "Other Date Batter",
            "actual_home_runs": "",
            "game_status": "",
        },
    ]

    exit_code, output_path = _run_with_temp_csvs(
        tmp_path,
        odds_rows,
        result_rows,
        extra_args=["--date", "2026-07-06"],
    )

    assert exit_code == 0
    with output_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["event_id"] == "event-1"
    assert rows[0]["grade_status"] == "graded"
    output = capsys.readouterr().out
    assert "Target date: 2026-07-06" in output
    assert "Total rows: 1" in output


def test_grader_excludes_void_rows_from_calculations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    odds_rows = [
        _odds_row(),
        _odds_row(event_id="event-2", player="Bench Player", price=500),
    ]
    result_rows = [
        {
            "event_id": "event-1",
            "player": "Winning Batter",
            "actual_home_runs": 1,
            "game_status": "final",
        },
        {
            "event_id": "event-2",
            "player": "Bench Player",
            "actual_home_runs": "",
            "game_status": "void",
        },
    ]

    exit_code, output_path = _run_with_temp_csvs(
        tmp_path,
        odds_rows,
        result_rows,
        extra_args=["--date", "2026-07-06"],
    )

    assert exit_code == 0
    with output_path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["event_id"] == "event-1"
    assert rows[0]["grade_status"] == "graded"
    assert rows[1]["event_id"] == "event-2"
    assert rows[1]["actual_home_runs"] == ""
    assert rows[1]["result"] == ""
    assert rows[1]["profit_1u"] == ""
    assert rows[1]["grade_status"] == "void"
    output = capsys.readouterr().out
    assert "Total rows: 2" in output
    assert "Graded rows: 1" in output
    assert "Excluded void rows: 1" in output
    assert "Void candidate rows: 0" in output
    assert "Manual review rows: 0" in output
    assert "Wins: 1" in output
    assert "Losses: 0" in output


def test_grader_excludes_void_candidate_and_manual_review_rows_from_calculations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    odds_rows = [
        _odds_row(),
        _odds_row(event_id="event-2", player="Owen Caissie", price=550),
        _odds_row(event_id="event-3", player="Ambiguous Batter", price=700),
    ]
    result_rows = [
        {
            "event_id": "event-1",
            "player": "Winning Batter",
            "actual_home_runs": 1,
            "game_status": "final",
        },
        {
            "event_id": "event-2",
            "player": "Owen Caissie",
            "actual_home_runs": "",
            "game_status": "void_candidate",
            "result_reason": "player_missing_from_boxscore_roster",
        },
        {
            "event_id": "event-3",
            "player": "Ambiguous Batter",
            "actual_home_runs": "",
            "game_status": "manual_review_required",
            "result_reason": "ambiguous_normalized_player_match",
        },
    ]

    exit_code, output_path = _run_with_temp_csvs(
        tmp_path,
        odds_rows,
        result_rows,
        extra_args=["--date", "2026-07-06"],
    )

    assert exit_code == 0
    with output_path.open("r", newline="", encoding="utf-8") as handle:
        rows = {row["event_id"]: row for row in csv.DictReader(handle)}

    assert rows["event-1"]["grade_status"] == "graded"
    assert rows["event-2"]["actual_home_runs"] == ""
    assert rows["event-2"]["result"] == ""
    assert rows["event-2"]["grade_status"] == "void_candidate"
    assert rows["event-2"]["result_reason"] == "player_missing_from_boxscore_roster"
    assert rows["event-3"]["grade_status"] == "manual_review_required"
    assert rows["event-3"]["result_reason"] == "ambiguous_normalized_player_match"

    output = capsys.readouterr().out
    assert "Total rows: 3" in output
    assert "Graded rows: 1" in output
    assert "Void candidate rows: 1" in output
    assert "Manual review rows: 1" in output
    assert "Wins: 1" in output
    assert "Losses: 0" in output


def test_date_scoped_grading_rejects_blank_target_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result_rows = [
        {
            "event_id": "event-1",
            "player": "Winning Batter",
            "actual_home_runs": 1,
            "game_status": "",
        }
    ]

    exit_code, output_path = _run_with_temp_csvs(
        tmp_path,
        [_odds_row()],
        result_rows,
        extra_args=["--date", "2026-07-06"],
    )

    assert exit_code == 1
    assert not output_path.exists()
    error = capsys.readouterr().err
    assert "date 2026-07-06" in error
    assert "blank required field 'game_status'" in error


def test_date_scoped_default_output_filename() -> None:
    assert default_output_path("2026-07-06").name == "live_hr_grades_20260706.csv"
