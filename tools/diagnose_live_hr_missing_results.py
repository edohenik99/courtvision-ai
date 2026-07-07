"""Diagnose blank MLB live HR workbook results without modifying them."""

from __future__ import annotations

import argparse
import csv
import difflib
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

if __package__:
    from tools.fill_live_hr_results_from_mlb_statsapi import (
        BOXSCORE_URL,
        DEFAULT_WORKBOOK,
        SCHEDULE_URL,
        JsonFetcher,
        WorkbookGame,
        fetch_json,
        is_blank,
        is_final_game,
        load_workbook,
        normalize_full_name,
        normalize_player_name,
        parse_commence_time,
        parse_target_date,
        schedule_games_for_date,
        select_schedule_game,
    )
else:
    from fill_live_hr_results_from_mlb_statsapi import (
        BOXSCORE_URL,
        DEFAULT_WORKBOOK,
        SCHEDULE_URL,
        JsonFetcher,
        WorkbookGame,
        fetch_json,
        is_blank,
        is_final_game,
        load_workbook,
        normalize_full_name,
        normalize_player_name,
        parse_commence_time,
        parse_target_date,
        schedule_games_for_date,
        select_schedule_game,
    )


DEFAULT_ODDS = Path(
    "data/theoddsapi/live_hr_snapshots/live_hr_props_master.csv"
)
DEFAULT_REPORT_DIRECTORY = Path(
    "data/theoddsapi/live_hr_snapshots/reports"
)
REPORT_COLUMNS = (
    "date",
    "commence_time",
    "away_team",
    "home_team",
    "event_id",
    "player",
    "diagnosis",
    "game_pk",
    "possible_matches",
)
ODDS_REQUIRED_COLUMNS = ("event_id", "commence_time")
NEAR_MATCH_THRESHOLD = 0.78


@dataclass(frozen=True)
class BoxscorePlayer:
    name: str
    normalized_name: str
    side: str
    has_batting_stats: bool


@dataclass(frozen=True)
class MissingResultDiagnostic:
    target_date: date
    commence_time: str
    away_team: str
    home_team: str
    event_id: str
    player: str
    diagnosis: str
    game_pk: str
    matched_game_found: bool
    game_final: bool
    normalized_player: str
    normalized_player_matched: bool
    player_batting_stats_found: bool
    player_boxscore_side: str
    possible_matches: tuple[str, ...]

    def as_csv_row(self) -> dict[str, str]:
        return {
            "date": self.target_date.isoformat(),
            "commence_time": self.commence_time,
            "away_team": self.away_team,
            "home_team": self.home_team,
            "event_id": self.event_id,
            "player": self.player,
            "diagnosis": self.diagnosis,
            "game_pk": self.game_pk,
            "possible_matches": " | ".join(self.possible_matches),
        }


def event_ids_for_date(odds_path: Path, target_date: date) -> set[str]:
    """Load target-date event IDs from the local master odds CSV."""

    if not odds_path.exists():
        raise FileNotFoundError(f"Master odds CSV not found: {odds_path}")

    with odds_path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Master odds CSV has no header row: {odds_path}")
        missing_columns = [
            column
            for column in ODDS_REQUIRED_COLUMNS
            if column not in reader.fieldnames
        ]
        if missing_columns:
            raise ValueError(
                f"Master odds CSV is missing required columns: {missing_columns}. "
                f"Available columns: {reader.fieldnames}"
            )

        event_ids: set[str] = set()
        for row_number, row in enumerate(reader, start=2):
            event_id = str(row.get("event_id") or "").strip()
            if not event_id:
                raise ValueError(
                    f"Master odds CSV row {row_number} has blank event_id."
                )
            commence_time_text = str(row.get("commence_time") or "").strip()
            try:
                commence_date = datetime.fromisoformat(
                    commence_time_text.replace("Z", "+00:00")
                ).date()
            except ValueError as exc:
                raise ValueError(
                    f"Master odds CSV row {row_number} has invalid "
                    f"commence_time: {commence_time_text!r}"
                ) from exc
            if commence_date == target_date:
                event_ids.add(event_id)
    return event_ids


def extract_boxscore_players(payload: dict[str, Any]) -> list[BoxscorePlayer]:
    players_by_name_and_side: dict[tuple[str, str], BoxscorePlayer] = {}
    teams = payload.get("teams") or {}

    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, dict):
            continue
        for player in players.values():
            if not isinstance(player, dict):
                continue
            name = str(((player.get("person") or {}).get("fullName")) or "").strip()
            normalized_name = normalize_player_name(name)
            if not normalized_name:
                continue
            batting = ((player.get("stats") or {}).get("batting") or {})
            has_batting_stats = isinstance(batting, dict) and bool(batting)
            key = (normalized_name, side)
            existing = players_by_name_and_side.get(key)
            if existing is None or (has_batting_stats and not existing.has_batting_stats):
                players_by_name_and_side[key] = BoxscorePlayer(
                    name=name,
                    normalized_name=normalized_name,
                    side=side,
                    has_batting_stats=has_batting_stats,
                )

    return list(players_by_name_and_side.values())


def build_target_workbook_games(
    rows: list[dict[str, str]], target_event_ids: set[str]
) -> dict[tuple[str, str, str], WorkbookGame]:
    """Group master-scoped rows with the filler's event/team key structure."""

    games: dict[tuple[str, str, str], WorkbookGame] = {}
    for index, row in enumerate(rows):
        event_id = str(row.get("event_id") or "").strip()
        if event_id not in target_event_ids:
            continue

        home_team = str(row.get("home_team") or "").strip()
        away_team = str(row.get("away_team") or "").strip()
        if not home_team or not away_team:
            raise ValueError(
                f"Workbook row {index + 2} is missing home_team or away_team."
            )
        commence_time = parse_commence_time(
            row.get("commence_time"), index + 2
        )
        key = (
            event_id,
            normalize_full_name(home_team),
            normalize_full_name(away_team),
        )
        if key not in games:
            games[key] = WorkbookGame(
                home_team=home_team,
                away_team=away_team,
                commence_time=commence_time,
                row_indexes=[],
            )
        games[key].row_indexes.append(index)
    return games


def closest_boxscore_names(
    player: str,
    boxscore_players: list[BoxscorePlayer],
    *,
    limit: int = 5,
) -> tuple[tuple[str, ...], float]:
    normalized_player = normalize_player_name(player)
    unique_players: dict[str, BoxscorePlayer] = {}
    for candidate in boxscore_players:
        unique_players.setdefault(candidate.normalized_name, candidate)

    ranked = sorted(
        (
            (
                difflib.SequenceMatcher(
                    None, normalized_player, candidate.normalized_name
                ).ratio(),
                candidate,
            )
            for candidate in unique_players.values()
        ),
        key=lambda item: (-item[0], item[1].normalized_name),
    )
    suggestions = tuple(
        f"{candidate.name} [{candidate.normalized_name}]"
        for _, candidate in ranked[:limit]
    )
    best_score = ranked[0][0] if ranked else 0.0
    return suggestions, best_score


def _missing_row_indexes(
    rows: list[dict[str, str]],
    workbook_games: dict[tuple[str, str, str], WorkbookGame],
) -> dict[tuple[str, str, str], list[int]]:
    missing_by_game: dict[tuple[str, str, str], list[int]] = {}
    for key, workbook_game in workbook_games.items():
        indexes = [
            row_index
            for row_index in workbook_game.row_indexes
            if (
                is_blank(rows[row_index].get("actual_home_runs"))
                or is_blank(rows[row_index].get("game_status"))
            )
        ]
        if indexes:
            missing_by_game[key] = indexes
    return missing_by_game


def diagnose_missing_results(
    workbook_path: Path,
    odds_path: Path,
    target_date: date,
    *,
    get_json: JsonFetcher = fetch_json,
) -> list[MissingResultDiagnostic]:
    """Return diagnostics for target-date blanks without changing local files."""

    _, rows = load_workbook(workbook_path)
    target_event_ids = event_ids_for_date(odds_path, target_date)
    workbook_games = build_target_workbook_games(rows, target_event_ids)
    missing_by_game = _missing_row_indexes(rows, workbook_games)
    if not missing_by_game:
        return []

    schedule_payload = get_json(
        SCHEDULE_URL,
        {"sportId": 1, "date": target_date.isoformat()},
    )
    scheduled_games = schedule_games_for_date(schedule_payload, target_date)

    matched_games: dict[tuple[str, str, str], dict[str, Any]] = {}
    boxscore_players_by_key: dict[
        tuple[str, str, str], list[BoxscorePlayer]
    ] = {}
    boxscore_players_by_game_pk: dict[str, list[BoxscorePlayer]] = {}
    for key in missing_by_game:
        workbook_game = workbook_games[key]
        candidates = scheduled_games.get((key[1], key[2]), [])
        game = select_schedule_game(workbook_game, candidates)
        if game is None:
            continue
        matched_games[key] = game
        game_pk = str(game["gamePk"])
        if game_pk not in boxscore_players_by_game_pk:
            boxscore_payload = get_json(
                BOXSCORE_URL.format(game_pk=game_pk), None
            )
            boxscore_players_by_game_pk[game_pk] = extract_boxscore_players(
                boxscore_payload
            )
        boxscore_players_by_key[key] = boxscore_players_by_game_pk[game_pk]

    player_game_keys: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for key, boxscore_players in boxscore_players_by_key.items():
        for boxscore_player in boxscore_players:
            player_game_keys[boxscore_player.normalized_name].add(key)

    diagnostics: list[MissingResultDiagnostic] = []
    for key, row_indexes in missing_by_game.items():
        game = matched_games.get(key)
        game_pk = str(game["gamePk"]) if game else ""
        game_final = bool(game and is_final_game(game))
        boxscore_players = boxscore_players_by_key.get(key, [])

        for row_index in row_indexes:
            row = rows[row_index]
            player = str(row.get("player") or "").strip()
            normalized_player = normalize_player_name(player)
            exact_matches = [
                candidate
                for candidate in boxscore_players
                if candidate.normalized_name == normalized_player
            ]
            suggestions, best_score = closest_boxscore_names(
                player, boxscore_players
            )

            if game is None:
                diagnosis = "game_not_matched"
            elif not game_final:
                diagnosis = "game_not_final"
            elif exact_matches and any(
                candidate.has_batting_stats for candidate in exact_matches
            ):
                diagnosis = "matched_player_found_in_boxscore"
            elif exact_matches:
                diagnosis = "matched_player_missing_from_boxscore"
            elif any(
                other_key != key
                for other_key in player_game_keys.get(normalized_player, set())
            ):
                diagnosis = "event_player_team_mismatch_possible"
            elif best_score >= NEAR_MATCH_THRESHOLD:
                diagnosis = "possible_name_mismatch"
            else:
                diagnosis = "matched_player_missing_from_boxscore"

            diagnostics.append(
                MissingResultDiagnostic(
                    target_date=target_date,
                    commence_time=str(row.get("commence_time") or "").strip(),
                    away_team=str(row.get("away_team") or "").strip(),
                    home_team=str(row.get("home_team") or "").strip(),
                    event_id=str(row.get("event_id") or "").strip(),
                    player=player,
                    diagnosis=diagnosis,
                    game_pk=game_pk,
                    matched_game_found=game is not None,
                    game_final=game_final,
                    normalized_player=normalized_player,
                    normalized_player_matched=bool(exact_matches),
                    player_batting_stats_found=any(
                        candidate.has_batting_stats
                        for candidate in exact_matches
                    ),
                    player_boxscore_side=(
                        ",".join(
                            sorted({candidate.side for candidate in exact_matches})
                        )
                        if exact_matches
                        else ""
                    ),
                    possible_matches=suggestions,
                )
            )

    return diagnostics


def write_csv_report(
    path: Path, diagnostics: list[MissingResultDiagnostic]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(diagnostic.as_csv_row() for diagnostic in diagnostics)


def print_diagnostics(
    diagnostics: list[MissingResultDiagnostic], target_date: date
) -> None:
    print("MLB live HR missing-result diagnostics")
    print(f"Target date: {target_date.isoformat()}")
    print(f"Missing rows: {len(diagnostics)}")

    for diagnostic in diagnostics:
        print()
        print(f"Commence time: {diagnostic.commence_time}")
        print(f"Away team: {diagnostic.away_team}")
        print(f"Home team: {diagnostic.home_team}")
        print(f"Event ID: {diagnostic.event_id}")
        print(f"Player: {diagnostic.player}")
        print(f"Diagnosis: {diagnostic.diagnosis}")
        print(
            "Matched game found: "
            f"{'YES' if diagnostic.matched_game_found else 'NO'}"
        )
        print(f"Game PK: {diagnostic.game_pk or '-'}")
        print(f"Game final: {'YES' if diagnostic.game_final else 'NO'}")
        print(f"Normalized player: {diagnostic.normalized_player or '-'}")
        print(
            "Normalized player matched: "
            f"{'YES' if diagnostic.normalized_player_matched else 'NO'}"
        )
        print(
            "Player in boxscore batting stats: "
            f"{'YES' if diagnostic.player_batting_stats_found else 'NO'}"
        )
        print(f"Player boxscore side: {diagnostic.player_boxscore_side or '-'}")
        print("Closest boxscore players:")
        if diagnostic.possible_matches:
            for match in diagnostic.possible_matches:
                print(f"  - {match}")
        else:
            print("  - none")


def default_report_path(target_date: date) -> Path:
    return DEFAULT_REPORT_DIRECTORY / (
        f"missing_results_{target_date.strftime('%Y%m%d')}.csv"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diagnose blank live MLB HR workbook results using MLB StatsAPI. "
            "This command never fills or grades results."
        )
    )
    parser.add_argument(
        "--date", required=True, help="Date to diagnose, in YYYY-MM-DD format."
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help=f"Results workbook CSV. Default: {DEFAULT_WORKBOOK}",
    )
    parser.add_argument(
        "--odds-csv",
        type=Path,
        default=DEFAULT_ODDS,
        help=f"Local master odds CSV. Default: {DEFAULT_ODDS}",
    )
    parser.add_argument(
        "--csv-report",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help=(
            "Write a CSV report. If PATH is omitted, use the date-stamped "
            "reports directory path."
        ),
    )
    args = parser.parse_args(argv)

    try:
        target_date = parse_target_date(args.date)
        diagnostics = diagnose_missing_results(
            args.workbook,
            args.odds_csv,
            target_date,
        )
        print_diagnostics(diagnostics, target_date)
        if args.csv_report is not None:
            report_path = (
                Path(args.csv_report)
                if args.csv_report
                else default_report_path(target_date)
            )
            write_csv_report(report_path, diagnostics)
            print()
            print(f"CSV report: {report_path}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
