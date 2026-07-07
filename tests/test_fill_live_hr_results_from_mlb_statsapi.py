import csv
from datetime import date
from pathlib import Path
from typing import Mapping

from tools.diagnose_live_hr_missing_results import (
    REPORT_COLUMNS,
    diagnose_missing_results,
    write_csv_report,
)
from tools.fill_live_hr_results_from_mlb_statsapi import (
    BOXSCORE_URL,
    SCHEDULE_URL,
    fill_results_from_mlb_statsapi,
    normalize_full_name,
)


FIELDNAMES = [
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


def write_workbook(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def read_workbook(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_odds_csv(path: Path, *event_ids: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["event_id", "commence_time"])
        writer.writeheader()
        for event_id in event_ids:
            writer.writerow(
                {
                    "event_id": event_id,
                    "commence_time": "2026-07-02T23:00:00Z",
                }
            )


def workbook_row(
    event_id: str,
    player: str,
    home_team: str,
    away_team: str,
    *,
    actual_home_runs: str = "",
    game_status: str = "",
) -> dict[str, str]:
    return {
        "commence_time": "2026-07-02T23:00:00Z",
        "home_team": home_team,
        "away_team": away_team,
        "event_id": event_id,
        "player": player,
        "books_available": "DraftKings",
        "best_bookmaker": "DraftKings",
        "best_price": "+300",
        "all_prices": "DraftKings: +300",
        "actual_home_runs": actual_home_runs,
        "game_status": game_status,
    }


def schedule_game(
    game_pk: int,
    home_team: str,
    away_team: str,
    state: str,
) -> dict[str, object]:
    return {
        "gamePk": game_pk,
        "gameDate": "2026-07-02T23:00:00Z",
        "status": {"abstractGameState": state},
        "teams": {
            "home": {"team": {"name": home_team}},
            "away": {"team": {"name": away_team}},
        },
    }


def boxscore(*players: tuple[str, int]) -> dict[str, object]:
    player_entries = {
        f"ID{index}": {
            "person": {"fullName": player},
            "stats": {"batting": {"homeRuns": home_runs}},
        }
        for index, (player, home_runs) in enumerate(players, start=1)
    }
    return {
        "teams": {
            "away": {"players": player_entries},
            "home": {"players": {}},
        }
    }


class FakeStatsApi:
    def __init__(
        self,
        games: list[dict[str, object]],
        boxscores: dict[int, dict[str, object]],
    ) -> None:
        self.games = games
        self.boxscores = boxscores
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    def __call__(
        self,
        url: str,
        params: Mapping[str, object] | None,
    ) -> dict[str, object]:
        self.calls.append((url, params))
        if url == SCHEDULE_URL:
            return {"dates": [{"date": "2026-07-02", "games": self.games}]}
        for game_pk, payload in self.boxscores.items():
            if url == BOXSCORE_URL.format(game_pk=game_pk):
                return payload
        raise AssertionError(f"Unexpected MLB URL: {url}")


def test_fills_only_final_matched_games_and_reports_unmatched_rows(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "workbook.csv"
    write_workbook(
        workbook,
        [
            workbook_row("event_1", "Aaron Judge", "New York Yankees", "New York Mets"),
            workbook_row("event_1", "Missing Batter", "New York Yankees", "New York Mets"),
            workbook_row(
                "event_2",
                "Shohei Ohtani",
                "Los Angeles Dodgers",
                "San Francisco Giants",
            ),
            workbook_row("event_3", "Rafael Devers", "Boston Red Sox", "Baltimore Orioles"),
        ],
    )
    api = FakeStatsApi(
        games=[
            schedule_game(101, "New York Yankees", "New York Mets", "Final"),
            schedule_game(102, "Los Angeles Dodgers", "San Francisco Giants", "Live"),
        ],
        boxscores={101: boxscore(("Aaron Judge", 1))},
    )

    summary = fill_results_from_mlb_statsapi(
        workbook,
        date(2026, 7, 2),
        get_json=api,
    )

    rows = read_workbook(workbook)
    assert rows[0]["actual_home_runs"] == "1"
    assert rows[0]["game_status"] == "final"
    assert rows[1]["actual_home_runs"] == ""
    assert rows[2]["actual_home_runs"] == ""
    assert rows[3]["actual_home_runs"] == ""
    assert summary.games_matched == 2
    assert summary.games_final == 1
    assert summary.rows_filled == 1
    assert summary.rows_skipped == 3
    assert summary.unmatched_games == 1
    assert summary.unmatched_players == 1
    assert [url for url, _ in api.calls].count(BOXSCORE_URL.format(game_pk=101)) == 1
    assert BOXSCORE_URL.format(game_pk=102) not in [url for url, _ in api.calls]


def test_preserves_filled_values_unless_overwrite_is_enabled(tmp_path: Path) -> None:
    workbook = tmp_path / "workbook.csv"
    write_workbook(
        workbook,
        [
            workbook_row(
                "event_1",
                "Aaron Judge",
                "New York Yankees",
                "New York Mets",
                actual_home_runs="2",
                game_status="manual-final",
            ),
            workbook_row(
                "event_1",
                "Juan Soto",
                "New York Yankees",
                "New York Mets",
                actual_home_runs="3",
            ),
        ],
    )
    api = FakeStatsApi(
        games=[schedule_game(101, "New York Yankees", "New York Mets", "Final")],
        boxscores={101: boxscore(("Aaron Judge", 0), ("Juan Soto", 1))},
    )

    preserved = fill_results_from_mlb_statsapi(
        workbook,
        date(2026, 7, 2),
        get_json=api,
    )

    rows = read_workbook(workbook)
    assert rows[0]["actual_home_runs"] == "2"
    assert rows[0]["game_status"] == "manual-final"
    assert rows[1]["actual_home_runs"] == "3"
    assert rows[1]["game_status"] == "final"
    assert preserved.rows_filled == 1
    assert preserved.rows_skipped == 1

    overwritten = fill_results_from_mlb_statsapi(
        workbook,
        date(2026, 7, 2),
        overwrite_filled=True,
        get_json=api,
    )

    rows = read_workbook(workbook)
    assert rows[0]["actual_home_runs"] == "0"
    assert rows[0]["game_status"] == "final"
    assert rows[1]["actual_home_runs"] == "1"
    assert rows[1]["game_status"] == "final"
    assert overwritten.rows_filled == 2
    assert overwritten.rows_skipped == 0


def test_dry_run_reports_changes_without_modifying_workbook(tmp_path: Path) -> None:
    workbook = tmp_path / "workbook.csv"
    write_workbook(
        workbook,
        [workbook_row("event_1", "Aaron Judge", "New York Yankees", "New York Mets")],
    )
    original = workbook.read_bytes()
    api = FakeStatsApi(
        games=[schedule_game(101, "New York Yankees", "New York Mets", "Final")],
        boxscores={101: boxscore(("Aaron Judge", 1))},
    )

    summary = fill_results_from_mlb_statsapi(
        workbook,
        date(2026, 7, 2),
        dry_run=True,
        get_json=api,
    )

    assert summary.rows_filled == 1
    assert workbook.read_bytes() == original


def test_normalized_full_name_matches_accents_and_punctuation(tmp_path: Path) -> None:
    workbook = tmp_path / "workbook.csv"
    write_workbook(
        workbook,
        [workbook_row("event_1", "Jose Ramirez", "Cleveland Guardians", "Detroit Tigers")],
    )
    api = FakeStatsApi(
        games=[schedule_game(101, "Cleveland Guardians", "Detroit Tigers", "Final")],
        boxscores={101: boxscore(("José Ramírez", 2))},
    )

    fill_results_from_mlb_statsapi(
        workbook,
        date(2026, 7, 2),
        get_json=api,
    )

    assert read_workbook(workbook)[0]["actual_home_runs"] == "2"
    assert normalize_full_name("J.P. Crawford") == normalize_full_name("JP Crawford")


def test_diagnoses_missing_player_without_live_network(tmp_path: Path) -> None:
    workbook = tmp_path / "workbook.csv"
    odds_csv = tmp_path / "odds.csv"
    write_workbook(
        workbook,
        [
            workbook_row(
                "event_1", "Missing Batter", "New York Yankees", "New York Mets"
            )
        ],
    )
    write_odds_csv(odds_csv, "event_1")
    api = FakeStatsApi(
        games=[schedule_game(101, "New York Yankees", "New York Mets", "Final")],
        boxscores={101: boxscore(("Aaron Judge", 1), ("Juan Soto", 0))},
    )

    diagnostics = diagnose_missing_results(
        workbook, odds_csv, date(2026, 7, 2), get_json=api
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.diagnosis == "matched_player_missing_from_boxscore"
    assert diagnostic.matched_game_found is True
    assert diagnostic.game_final is True
    assert diagnostic.normalized_player_matched is False
    assert diagnostic.game_pk == "101"
    assert len(diagnostic.possible_matches) == 2
    assert [url for url, _ in api.calls] == [
        SCHEDULE_URL,
        BOXSCORE_URL.format(game_pk=101),
    ]
    report_path = tmp_path / "report.csv"
    write_csv_report(report_path, diagnostics)
    with report_path.open("r", newline="", encoding="utf-8") as handle:
        report_reader = csv.DictReader(handle)
        assert tuple(report_reader.fieldnames or ()) == REPORT_COLUMNS
        assert list(report_reader)[0]["diagnosis"] == (
            "matched_player_missing_from_boxscore"
        )


def test_diagnostic_suggests_near_name_matches(tmp_path: Path) -> None:
    workbook = tmp_path / "workbook.csv"
    odds_csv = tmp_path / "odds.csv"
    write_workbook(
        workbook,
        [workbook_row("event_1", "Aron Judge", "New York Yankees", "New York Mets")],
    )
    write_odds_csv(odds_csv, "event_1")
    api = FakeStatsApi(
        games=[schedule_game(101, "New York Yankees", "New York Mets", "Final")],
        boxscores={101: boxscore(("Aaron Judge", 1), ("Juan Soto", 0))},
    )

    diagnostic = diagnose_missing_results(
        workbook, odds_csv, date(2026, 7, 2), get_json=api
    )[0]

    assert diagnostic.diagnosis == "possible_name_mismatch"
    assert diagnostic.possible_matches[0] == "Aaron Judge [aaron judge]"
    assert diagnostic.normalized_player == "aron judge"


def test_diagnostic_reports_game_not_matched(tmp_path: Path) -> None:
    workbook = tmp_path / "workbook.csv"
    odds_csv = tmp_path / "odds.csv"
    write_workbook(
        workbook,
        [workbook_row("event_1", "Aaron Judge", "New York Yankees", "New York Mets")],
    )
    write_odds_csv(odds_csv, "event_1")
    api = FakeStatsApi(games=[], boxscores={})

    diagnostic = diagnose_missing_results(
        workbook, odds_csv, date(2026, 7, 2), get_json=api
    )[0]

    assert diagnostic.diagnosis == "game_not_matched"
    assert diagnostic.matched_game_found is False
    assert diagnostic.game_pk == ""
    assert api.calls == [
        (SCHEDULE_URL, {"sportId": 1, "date": "2026-07-02"})
    ]


def test_diagnostic_reports_final_game_player_without_batting_stats(
    tmp_path: Path,
) -> None:
    workbook = tmp_path / "workbook.csv"
    odds_csv = tmp_path / "odds.csv"
    write_workbook(
        workbook,
        [workbook_row("event_1", "Bench Player", "New York Yankees", "New York Mets")],
    )
    write_odds_csv(odds_csv, "event_1")
    api = FakeStatsApi(
        games=[schedule_game(101, "New York Yankees", "New York Mets", "Final")],
        boxscores={
            101: {
                "teams": {
                    "away": {
                        "players": {
                            "ID1": {
                                "person": {"fullName": "Bench Player"},
                                "stats": {"batting": {}},
                            }
                        }
                    },
                    "home": {"players": {}},
                }
            }
        },
    )

    diagnostic = diagnose_missing_results(
        workbook, odds_csv, date(2026, 7, 2), get_json=api
    )[0]

    assert diagnostic.diagnosis == "matched_player_missing_from_boxscore"
    assert diagnostic.game_final is True
    assert diagnostic.normalized_player_matched is True
    assert diagnostic.player_batting_stats_found is False
    assert diagnostic.player_boxscore_side == "away"
