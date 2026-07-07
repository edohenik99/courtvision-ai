from pathlib import Path

import pytest

from tools.check_live_hr_results_coverage import check_results_coverage, main
from tools.generate_live_hr_results_template import generate_template


def _write_odds_csv(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "event_id,commence_time",
                "event_1,2026-07-06T23:10:00Z",
                "event_2,2026-07-05T19:05:00Z",
            ]
        ),
        encoding="utf-8",
    )


def test_generate_live_hr_results_template_dedupes_event_player(tmp_path: Path) -> None:
    input_csv = tmp_path / "live_hr_props_master.csv"
    output_csv = tmp_path / "live_hr_results.csv"

    input_csv.write_text(
        "\n".join(
            [
                "event_id,player,bookmaker,price",
                "event_1,Aaron Judge,draftkings,+300",
                "event_1,Aaron Judge,fanduel,+290",
                "event_1,Juan Soto,draftkings,+400",
                "event_2,Shohei Ohtani,betmgm,+250",
            ]
        ),
        encoding="utf-8",
    )

    rows = generate_template(
        input_path=input_csv,
        output_path=output_csv,
        overwrite=False,
    )

    assert rows == 3

    output_lines = output_csv.read_text(encoding="utf-8").splitlines()

    assert output_lines[0] == "event_id,player,actual_home_runs,game_status"
    assert "event_1,Aaron Judge,," in output_lines
    assert "event_1,Juan Soto,," in output_lines
    assert "event_2,Shohei Ohtani,," in output_lines


def test_check_live_hr_results_coverage_reports_incomplete_file(tmp_path: Path) -> None:
    results_csv = tmp_path / "live_hr_results.csv"

    results_csv.write_text(
        "\n".join(
            [
                "event_id,player,actual_home_runs,game_status",
                "event_1,Aaron Judge,,",
                "event_1,Juan Soto,1,final",
            ]
        ),
        encoding="utf-8",
    )

    report = check_results_coverage(results_csv)

    assert report["total_rows"] == 2
    assert report["missing_actual_home_runs"] == 1
    assert report["missing_game_status"] == 1
    assert report["invalid_actual_home_runs"] == 0
    assert report["ready_to_grade"] is False


def test_check_live_hr_results_coverage_reports_ready_file(tmp_path: Path) -> None:
    results_csv = tmp_path / "live_hr_results.csv"

    results_csv.write_text(
        "\n".join(
            [
                "event_id,player,actual_home_runs,game_status",
                "event_1,Aaron Judge,0,final",
                "event_1,Juan Soto,1,final",
            ]
        ),
        encoding="utf-8",
    )

    report = check_results_coverage(results_csv)

    assert report["total_rows"] == 2
    assert report["missing_actual_home_runs"] == 0
    assert report["missing_game_status"] == 0
    assert report["invalid_actual_home_runs"] == 0
    assert report["ready_to_grade"] is True


def test_check_live_hr_results_coverage_flags_invalid_home_runs(tmp_path: Path) -> None:
    results_csv = tmp_path / "live_hr_results.csv"

    results_csv.write_text(
        "\n".join(
            [
                "event_id,player,actual_home_runs,game_status",
                "event_1,Aaron Judge,nope,final",
                "event_1,Juan Soto,-1,final",
            ]
        ),
        encoding="utf-8",
    )

    report = check_results_coverage(results_csv)

    assert report["total_rows"] == 2
    assert report["invalid_actual_home_runs"] == 2
    assert report["ready_to_grade"] is False


def test_date_scoped_coverage_ignores_blank_rows_from_other_dates(
    tmp_path: Path,
) -> None:
    odds_csv = tmp_path / "live_hr_props_master.csv"
    results_csv = tmp_path / "live_hr_results.csv"
    _write_odds_csv(odds_csv)
    results_csv.write_text(
        "\n".join(
            [
                "event_id,player,actual_home_runs,game_status",
                "event_1,Aaron Judge,1,final",
                "event_2,Juan Soto,,",
            ]
        ),
        encoding="utf-8",
    )

    scoped_report = check_results_coverage(
        results_csv,
        odds_path=odds_csv,
        target_date="2026-07-06",
    )
    global_report = check_results_coverage(results_csv)

    assert scoped_report["total_rows"] == 1
    assert scoped_report["missing_actual_home_runs"] == 0
    assert scoped_report["ready_to_grade"] is True
    assert global_report["total_rows"] == 2
    assert global_report["missing_actual_home_runs"] == 1
    assert global_report["ready_to_grade"] is False


def test_date_scoped_coverage_reports_not_ready_for_target_date_blanks(
    tmp_path: Path,
) -> None:
    odds_csv = tmp_path / "live_hr_props_master.csv"
    results_csv = tmp_path / "live_hr_results.csv"
    _write_odds_csv(odds_csv)
    results_csv.write_text(
        "\n".join(
            [
                "event_id,player,actual_home_runs,game_status",
                "event_1,Aaron Judge,,",
                "event_2,Juan Soto,1,final",
            ]
        ),
        encoding="utf-8",
    )

    report = check_results_coverage(
        results_csv,
        odds_path=odds_csv,
        target_date="2026-07-06",
    )

    assert report["total_rows"] == 1
    assert report["missing_actual_home_runs"] == 1
    assert report["missing_game_status"] == 1
    assert report["ready_to_grade"] is False


def test_date_scoped_coverage_reports_zero_rows_without_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    odds_csv = tmp_path / "live_hr_props_master.csv"
    results_csv = tmp_path / "live_hr_results.csv"
    _write_odds_csv(odds_csv)
    results_csv.write_text(
        "event_id,player,actual_home_runs,game_status\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--results",
            str(results_csv),
            "--odds-csv",
            str(odds_csv),
            "--date",
            "2026-07-04",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Target date: 2026-07-04" in output
    assert "Rows: 0" in output
    assert "Ready to grade: NO" in output
