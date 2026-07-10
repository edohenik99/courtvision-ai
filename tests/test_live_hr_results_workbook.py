import csv
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


def test_generate_live_hr_results_workbook_preserves_existing_results(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "live_hr_props_master.csv"
    output_csv = tmp_path / "live_hr_results_workbook.csv"

    input_csv.write_text(
        "\n".join(
            [
                HEADER,
                "2026-07-03T15:00:00Z,event_1,2026-07-03T23:00:00Z,Yankees,Mets,fanduel,FanDuel,,batter_home_runs,,Aaron Judge,Over,325,0.5,HR",
                "2026-07-03T15:00:00Z,event_1,2026-07-03T23:00:00Z,Yankees,Mets,betmgm,BetMGM,,batter_home_runs,,Juan Soto,Over,400,0.5,HR",
                "2026-07-03T15:00:00Z,event_2,2026-07-04T01:00:00Z,Dodgers,Giants,draftkings,DraftKings,,batter_home_runs,,Aaron Judge,Over,350,0.5,HR",
            ]
        ),
        encoding="utf-8",
    )
    output_csv.write_text(
        "\n".join(
            [
                ",".join(
                    [
                        "commence_time",
                        "home_team",
                        "away_team",
                        "event_id",
                        "player",
                        "books_available",
                        "best_bookmaker",
                        "best_price",
                        "all_prices",
                        "actual_home_runs",
                        "game_status",
                    ]
                ),
                "2026-07-02T23:00:00Z,Yankees,Mets,event_1,Aaron Judge,DraftKings,DraftKings,300,DraftKings Over 0.5: 300,1,final",
            ]
        ),
        encoding="utf-8",
    )

    rows = generate_workbook(
        input_path=input_csv,
        output_path=output_csv,
        overwrite=True,
        preserve_results=True,
    )

    assert rows == 3

    with output_csv.open("r", newline="", encoding="utf-8") as f:
        output_rows = {
            (row["event_id"], row["player"]): row for row in csv.DictReader(f)
        }

    assert output_rows[("event_1", "Aaron Judge")]["actual_home_runs"] == "1"
    assert output_rows[("event_1", "Aaron Judge")]["game_status"] == "final"
    assert output_rows[("event_1", "Aaron Judge")]["result_reason"] == ""
    assert output_rows[("event_1", "Aaron Judge")]["best_price"] == "325"
    assert output_rows[("event_1", "Juan Soto")]["actual_home_runs"] == ""
    assert output_rows[("event_1", "Juan Soto")]["game_status"] == ""
    assert output_rows[("event_2", "Aaron Judge")]["actual_home_runs"] == ""
    assert output_rows[("event_2", "Aaron Judge")]["game_status"] == ""


def test_generate_live_hr_results_workbook_preserves_result_reason(
    tmp_path: Path,
) -> None:
    input_csv = tmp_path / "live_hr_props_master.csv"
    output_csv = tmp_path / "live_hr_results_workbook.csv"

    input_csv.write_text(
        "\n".join(
            [
                HEADER,
                "2026-07-03T15:00:00Z,event_1,2026-07-03T23:00:00Z,Marlins,Mariners,betmgm,BetMGM,,batter_home_runs,,Owen Caissie,Over,550,0.5,HR",
            ]
        ),
        encoding="utf-8",
    )
    output_csv.write_text(
        "\n".join(
            [
                "commence_time,home_team,away_team,event_id,player,books_available,best_bookmaker,best_price,all_prices,actual_home_runs,game_status,result_reason",
                "2026-07-03T23:00:00Z,Marlins,Mariners,event_1,Owen Caissie,BetMGM,BetMGM,550,BetMGM Over 0.5: 550,,void_candidate,player_missing_from_boxscore_roster",
            ]
        ),
        encoding="utf-8",
    )

    generate_workbook(
        input_path=input_csv,
        output_path=output_csv,
        overwrite=True,
        preserve_results=True,
    )

    with output_csv.open("r", newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))

    assert row["game_status"] == "void_candidate"
    assert row["result_reason"] == "player_missing_from_boxscore_roster"
