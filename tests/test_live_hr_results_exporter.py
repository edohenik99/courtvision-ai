from pathlib import Path

from tools.export_live_hr_results_from_workbook import export_results_from_workbook


def test_export_live_hr_results_from_workbook_writes_strict_results_csv(
    tmp_path: Path,
) -> None:
    workbook_csv = tmp_path / "live_hr_results_workbook.csv"
    results_csv = tmp_path / "live_hr_results.csv"

    workbook_csv.write_text(
        "\n".join(
            [
                "commence_time,home_team,away_team,event_id,player,books_available,best_bookmaker,best_price,all_prices,actual_home_runs,game_status",
                "2026-07-02T23:00:00Z,Yankees,Mets,event_1,Aaron Judge,DraftKings,DraftKings,300,DraftKings Over 0.5: 300,1,final",
                "2026-07-02T23:00:00Z,Yankees,Mets,event_1,Juan Soto,BetMGM,BetMGM,400,BetMGM Over 0.5: 400,0,final",
            ]
        ),
        encoding="utf-8",
    )

    rows = export_results_from_workbook(
        input_path=workbook_csv,
        output_path=results_csv,
        overwrite=False,
    )

    assert rows == 2

    output_lines = results_csv.read_text(encoding="utf-8").splitlines()

    assert output_lines[0] == (
        "event_id,player,actual_home_runs,game_status,result_reason"
    )
    assert output_lines[1] == "event_1,Aaron Judge,1,final,"
    assert output_lines[2] == "event_1,Juan Soto,0,final,"


def test_export_live_hr_results_from_workbook_preserves_blank_result_fields(
    tmp_path: Path,
) -> None:
    workbook_csv = tmp_path / "live_hr_results_workbook.csv"
    results_csv = tmp_path / "live_hr_results.csv"

    workbook_csv.write_text(
        "\n".join(
            [
                "commence_time,home_team,away_team,event_id,player,books_available,best_bookmaker,best_price,all_prices,actual_home_runs,game_status",
                "2026-07-02T23:00:00Z,Yankees,Mets,event_1,Aaron Judge,DraftKings,DraftKings,300,DraftKings Over 0.5: 300,,",
            ]
        ),
        encoding="utf-8",
    )

    rows = export_results_from_workbook(
        input_path=workbook_csv,
        output_path=results_csv,
        overwrite=False,
    )

    assert rows == 1
    assert "event_1,Aaron Judge,,," in results_csv.read_text(encoding="utf-8")


def test_export_live_hr_results_from_workbook_preserves_result_reason(
    tmp_path: Path,
) -> None:
    workbook_csv = tmp_path / "live_hr_results_workbook.csv"
    results_csv = tmp_path / "live_hr_results.csv"

    workbook_csv.write_text(
        "\n".join(
            [
                "commence_time,home_team,away_team,event_id,player,books_available,best_bookmaker,best_price,all_prices,actual_home_runs,game_status,result_reason",
                "2026-07-02T23:00:00Z,Marlins,Mariners,event_1,Owen Caissie,BetMGM,BetMGM,550,BetMGM Over 0.5: 550,,void_candidate,player_missing_from_boxscore_roster",
            ]
        ),
        encoding="utf-8",
    )

    rows = export_results_from_workbook(
        input_path=workbook_csv,
        output_path=results_csv,
        overwrite=False,
    )

    assert rows == 1
    assert results_csv.read_text(encoding="utf-8").splitlines()[1] == (
        "event_1,Owen Caissie,,void_candidate,player_missing_from_boxscore_roster"
    )


def test_export_live_hr_results_from_workbook_refuses_overwrite_by_default(
    tmp_path: Path,
) -> None:
    workbook_csv = tmp_path / "live_hr_results_workbook.csv"
    results_csv = tmp_path / "live_hr_results.csv"

    workbook_csv.write_text(
        "\n".join(
            [
                "commence_time,home_team,away_team,event_id,player,books_available,best_bookmaker,best_price,all_prices,actual_home_runs,game_status",
                "2026-07-02T23:00:00Z,Yankees,Mets,event_1,Aaron Judge,DraftKings,DraftKings,300,DraftKings Over 0.5: 300,1,final",
            ]
        ),
        encoding="utf-8",
    )

    results_csv.write_text("existing file", encoding="utf-8")

    try:
        export_results_from_workbook(
            input_path=workbook_csv,
            output_path=results_csv,
            overwrite=False,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("Expected FileExistsError when output exists.")


def test_export_live_hr_results_from_workbook_allows_overwrite(
    tmp_path: Path,
) -> None:
    workbook_csv = tmp_path / "live_hr_results_workbook.csv"
    results_csv = tmp_path / "live_hr_results.csv"

    workbook_csv.write_text(
        "\n".join(
            [
                "commence_time,home_team,away_team,event_id,player,books_available,best_bookmaker,best_price,all_prices,actual_home_runs,game_status",
                "2026-07-02T23:00:00Z,Yankees,Mets,event_1,Aaron Judge,DraftKings,DraftKings,300,DraftKings Over 0.5: 300,1,final",
            ]
        ),
        encoding="utf-8",
    )

    results_csv.write_text("existing file", encoding="utf-8")

    rows = export_results_from_workbook(
        input_path=workbook_csv,
        output_path=results_csv,
        overwrite=True,
    )

    assert rows == 1
    assert results_csv.read_text(encoding="utf-8").splitlines()[1] == (
        "event_1,Aaron Judge,1,final,"
    )
