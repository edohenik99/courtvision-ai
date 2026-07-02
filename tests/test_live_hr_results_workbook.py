from pathlib import Path

from tools.generate_live_hr_results_workbook import generate_workbook


HEADER = (
    "snapshot_time,event_id,commence_time,home_team,away_team,"
    "bookmaker_key,bookmaker,bookmaker_last_update,market,"
    "market_last_update,player,side,price,point,hr_label"
)


def test_generate_live_hr_results_workbook_dedupes_and_summarizes_prices(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "live_hr_props_master.csv"
    output_csv = tmp_path / "live_hr_results_workbook.csv"

    input_csv.write_text(
        "\n".join(
            [
                HEADER,
                "2026-07-02T15:00:00Z,event_1,2026-07-02T23:00:00Z,Yankees,Mets,draftkings,DraftKings,,batter_home_runs,,Aaron Judge,Over,300,0.5,HR",
                "2026-07-02T15:00:00Z,event_1,2026-07-02T23:00:00Z,Yankees,Mets,fanduel,FanDuel,,batter_home_runs,,Aaron Judge,Over,290,0.5,HR",
                "2026-07-02T15:00:00Z,event_1,2026-07-02T23:00:00Z,Yankees,Mets,betmgm,BetMGM,,batter_home_runs,,Juan Soto,Over,400,0.5,HR",
                "2026-07-02T15:00:00Z,event_2,2026-07-03T01:00:00Z,Dodgers,Giants,espnbet,ESPN BET,,batter_home_runs,,Shohei Ohtani,Over,250,0.5,HR",
            ]
        ),
        encoding="utf-8",
    )

    rows = generate_workbook(
        input_path=input_csv,
        output_path=output_csv,
        overwrite=False,
    )

    assert rows == 3

    output = output_csv.read_text(encoding="utf-8")

    assert "commence_time,home_team,away_team,event_id,player" in output
    assert "event_1,Aaron Judge" in output
    assert "DraftKings" in output
    assert "FanDuel" in output
    assert "DraftKings,300" in output
    assert "event_1,Juan Soto" in output
    assert "event_2,Shohei Ohtani" in output


def test_generate_live_hr_results_workbook_refuses_overwrite_by_default(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "live_hr_props_master.csv"
    output_csv = tmp_path / "live_hr_results_workbook.csv"

    input_csv.write_text(
        "\n".join(
            [
                HEADER,
                "2026-07-02T15:00:00Z,event_1,2026-07-02T23:00:00Z,Yankees,Mets,draftkings,DraftKings,,batter_home_runs,,Aaron Judge,Over,300,0.5,HR",
            ]
        ),
        encoding="utf-8",
    )

    output_csv.write_text("existing file", encoding="utf-8")

    try:
        generate_workbook(
            input_path=input_csv,
            output_path=output_csv,
            overwrite=False,
        )
    except FileExistsError:
        pass
    else:
        raise AssertionError("Expected FileExistsError when output exists.")


def test_generate_live_hr_results_workbook_allows_overwrite(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "live_hr_props_master.csv"
    output_csv = tmp_path / "live_hr_results_workbook.csv"

    input_csv.write_text(
        "\n".join(
            [
                HEADER,
                "2026-07-02T15:00:00Z,event_1,2026-07-02T23:00:00Z,Yankees,Mets,draftkings,DraftKings,,batter_home_runs,,Aaron Judge,Over,300,0.5,HR",
            ]
        ),
        encoding="utf-8",
    )

    output_csv.write_text("existing file", encoding="utf-8")

    rows = generate_workbook(
        input_path=input_csv,
        output_path=output_csv,
        overwrite=True,
    )

    assert rows == 1
    assert "Aaron Judge" in output_csv.read_text(encoding="utf-8")