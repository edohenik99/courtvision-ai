"""Fill MLB live HR workbook results from the public MLB StatsAPI."""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import requests


DEFAULT_WORKBOOK = Path(
    "data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv"
)
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
REQUEST_TIMEOUT_SECONDS = 30
PLAYER_FIRST_NAME_ALIASES = {
    "james": "jim",
}

REQUIRED_COLUMNS = (
    "commence_time",
    "home_team",
    "away_team",
    "event_id",
    "player",
    "actual_home_runs",
    "game_status",
)

JsonObject = dict[str, Any]
JsonFetcher = Callable[[str, Mapping[str, object] | None], JsonObject]


@dataclass(frozen=True)
class FillSummary:
    target_date: date
    games_matched: int
    games_final: int
    rows_filled: int
    rows_skipped: int
    unmatched_games: int
    unmatched_players: int


@dataclass
class WorkbookGame:
    home_team: str
    away_team: str
    commence_time: datetime
    row_indexes: list[int]


def is_blank(value: object) -> bool:
    return value is None or str(value).strip() == ""


def normalize_full_name(value: object) -> str:
    """Normalize a full player or team name without using fuzzy matching."""

    text = unicodedata.normalize("NFKD", str(value or "")).casefold()
    normalized: list[str] = []
    for character in text:
        if unicodedata.combining(character):
            continue
        if character.isalnum():
            normalized.append(character)
        elif character.isspace() or character in "-_/":
            normalized.append(" ")
    return re.sub(r"\s+", " ", "".join(normalized)).strip()


def normalize_player_name(value: object) -> str:
    """Normalize a player name and canonicalize supported first-name aliases."""

    normalized_name = normalize_full_name(value)
    if not normalized_name:
        return ""
    name_parts = normalized_name.split(" ")
    name_parts[0] = PLAYER_FIRST_NAME_ALIASES.get(name_parts[0], name_parts[0])
    return " ".join(name_parts)


def parse_target_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid --date value {value!r}; expected YYYY-MM-DD.") from exc


def parse_commence_time(value: object, row_number: int) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"Workbook row {row_number} has invalid commence_time: {text!r}"
        ) from exc

    if parsed.tzinfo is not None:
        return parsed.astimezone()
    return parsed


def validate_columns(fieldnames: list[str] | None) -> list[str]:
    if not fieldnames:
        raise ValueError("Workbook CSV has no header row.")

    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        raise ValueError(
            f"Workbook CSV is missing required columns: {missing}. "
            f"Available columns: {fieldnames}"
        )
    return fieldnames


def load_workbook(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"Workbook CSV not found: {path}")

    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = validate_columns(reader.fieldnames)
        rows = list(reader)
    return fieldnames, rows


def fetch_json(
    url: str,
    params: Mapping[str, object] | None = None,
) -> JsonObject:
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"MLB StatsAPI returned a non-object response for {url}")
    return payload


def build_workbook_games(
    rows: list[dict[str, str]],
    target_date: date,
) -> dict[tuple[str, str, str], WorkbookGame]:
    games: dict[tuple[str, str, str], WorkbookGame] = {}

    for index, row in enumerate(rows):
        commence_time = parse_commence_time(row.get("commence_time"), index + 2)
        if commence_time.date() != target_date:
            continue

        home_team = (row.get("home_team") or "").strip()
        away_team = (row.get("away_team") or "").strip()
        event_id = (row.get("event_id") or "").strip()
        if not home_team or not away_team or not event_id:
            raise ValueError(
                f"Workbook row {index + 2} is missing home_team, away_team, or event_id."
            )

        key = (event_id, normalize_full_name(home_team), normalize_full_name(away_team))
        if key not in games:
            games[key] = WorkbookGame(
                home_team=home_team,
                away_team=away_team,
                commence_time=commence_time,
                row_indexes=[],
            )
        games[key].row_indexes.append(index)

    return games


def schedule_games_for_date(
    payload: JsonObject,
    target_date: date,
) -> dict[tuple[str, str], list[JsonObject]]:
    scheduled: dict[tuple[str, str], list[JsonObject]] = defaultdict(list)

    for date_entry in payload.get("dates", []):
        if not isinstance(date_entry, dict) or date_entry.get("date") != target_date.isoformat():
            continue
        for game in date_entry.get("games", []):
            if not isinstance(game, dict) or not game.get("gamePk"):
                continue
            teams = game.get("teams") or {}
            home_team = (((teams.get("home") or {}).get("team") or {}).get("name"))
            away_team = (((teams.get("away") or {}).get("team") or {}).get("name"))
            if not home_team or not away_team:
                continue
            scheduled[
                (normalize_full_name(home_team), normalize_full_name(away_team))
            ].append(game)

    return scheduled


def parse_api_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone()
    return parsed


def select_schedule_game(
    workbook_game: WorkbookGame,
    candidates: list[JsonObject],
) -> JsonObject | None:
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None

    timed_candidates = [
        (parse_api_datetime(candidate.get("gameDate")), candidate)
        for candidate in candidates
    ]
    timed_candidates = [item for item in timed_candidates if item[0] is not None]
    if len(timed_candidates) != len(candidates):
        return None

    ranked = sorted(
        timed_candidates,
        key=lambda item: abs((item[0] - workbook_game.commence_time).total_seconds()),
    )
    if len(ranked) > 1:
        first_distance = abs(
            (ranked[0][0] - workbook_game.commence_time).total_seconds()
        )
        second_distance = abs(
            (ranked[1][0] - workbook_game.commence_time).total_seconds()
        )
        if first_distance == second_distance:
            return None
    return ranked[0][1]


def is_final_game(game: JsonObject) -> bool:
    status = game.get("status") or {}
    return str(status.get("abstractGameState") or "").casefold() == "final"


def extract_player_home_runs(payload: JsonObject) -> dict[str, int]:
    results: dict[str, int] = {}
    ambiguous_names: set[str] = set()
    teams = payload.get("teams") or {}

    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, dict):
            continue
        for player in players.values():
            if not isinstance(player, dict):
                continue
            full_name = ((player.get("person") or {}).get("fullName"))
            batting = ((player.get("stats") or {}).get("batting") or {})
            home_runs = batting.get("homeRuns")
            normalized_name = normalize_player_name(full_name)
            if not normalized_name or home_runs is None:
                continue
            try:
                parsed_home_runs = int(home_runs)
            except (TypeError, ValueError):
                continue
            if parsed_home_runs < 0:
                continue
            if (
                normalized_name in results
                and results[normalized_name] != parsed_home_runs
            ):
                ambiguous_names.add(normalized_name)
                continue
            results[normalized_name] = parsed_home_runs

    for normalized_name in ambiguous_names:
        results.pop(normalized_name, None)
    return results


def extract_boxscore_player_names(payload: JsonObject) -> set[str]:
    """Return normalized names for every player listed on either game roster."""

    normalized_names: set[str] = set()
    teams = payload.get("teams") or {}
    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, dict):
            continue
        for player in players.values():
            if not isinstance(player, dict):
                continue
            normalized_name = normalize_player_name(
                ((player.get("person") or {}).get("fullName"))
            )
            if normalized_name:
                normalized_names.add(normalized_name)
    return normalized_names


def write_workbook_atomic(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            newline="",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def fill_results_from_mlb_statsapi(
    workbook_path: Path,
    target_date: date,
    *,
    overwrite_filled: bool = False,
    dry_run: bool = False,
    get_json: JsonFetcher = fetch_json,
) -> FillSummary:
    fieldnames, rows = load_workbook(workbook_path)
    workbook_games = build_workbook_games(rows, target_date)

    schedule_payload = get_json(
        SCHEDULE_URL,
        {"sportId": 1, "date": target_date.isoformat()},
    )
    scheduled_games = schedule_games_for_date(schedule_payload, target_date)

    matched_games: dict[tuple[str, str, str], JsonObject] = {}
    unmatched_game_keys: set[tuple[str, str, str]] = set()
    for key, workbook_game in workbook_games.items():
        candidates = scheduled_games.get((key[1], key[2]), [])
        matched_game = select_schedule_game(workbook_game, candidates)
        if matched_game is None:
            unmatched_game_keys.add(key)
        else:
            matched_games[key] = matched_game

    final_games = {
        key: game for key, game in matched_games.items() if is_final_game(game)
    }
    home_runs_by_game_pk: dict[str, dict[str, int]] = {}
    roster_names_by_game_pk: dict[str, set[str]] = {}
    for game in final_games.values():
        game_pk = str(game["gamePk"])
        if game_pk not in home_runs_by_game_pk:
            boxscore_payload = get_json(BOXSCORE_URL.format(game_pk=game_pk), None)
            home_runs_by_game_pk[game_pk] = extract_player_home_runs(boxscore_payload)
            roster_names_by_game_pk[game_pk] = extract_boxscore_player_names(
                boxscore_payload
            )

    rows_filled = 0
    rows_skipped = 0
    unmatched_player_keys: set[tuple[str, str, str, str]] = set()

    for key, workbook_game in workbook_games.items():
        game = final_games.get(key)
        if game is None:
            rows_skipped += len(workbook_game.row_indexes)
            continue

        game_pk = str(game["gamePk"])
        home_runs = home_runs_by_game_pk[game_pk]
        roster_names = roster_names_by_game_pk[game_pk]
        for row_index in workbook_game.row_indexes:
            row = rows[row_index]
            existing_status = str(row.get("game_status") or "").strip().casefold()
            if not overwrite_filled and existing_status in {"final", "void"}:
                rows_skipped += 1
                continue

            needs_home_runs = overwrite_filled or is_blank(row.get("actual_home_runs"))
            needs_status = overwrite_filled or is_blank(row.get("game_status"))

            if not needs_home_runs and not needs_status:
                rows_skipped += 1
                continue

            normalized_player = normalize_player_name(row.get("player"))
            if normalized_player not in home_runs:
                if normalized_player in roster_names:
                    if overwrite_filled:
                        row["actual_home_runs"] = ""
                    if needs_status:
                        row["game_status"] = "void"
                        rows_filled += 1
                    else:
                        rows_skipped += 1
                    continue
                unmatched_player_keys.add((*key, normalized_player))
                rows_skipped += 1
                continue

            if needs_home_runs:
                row["actual_home_runs"] = str(home_runs[normalized_player])

            if needs_status:
                row["game_status"] = "final"
            rows_filled += 1

    if not dry_run and rows_filled:
        write_workbook_atomic(workbook_path, fieldnames, rows)

    return FillSummary(
        target_date=target_date,
        games_matched=len(matched_games),
        games_final=len(final_games),
        rows_filled=rows_filled,
        rows_skipped=rows_skipped,
        unmatched_games=len(unmatched_game_keys),
        unmatched_players=len(unmatched_player_keys),
    )


def print_summary(summary: FillSummary, *, dry_run: bool) -> None:
    print("MLB StatsAPI HR result fill")
    print(f"Target date: {summary.target_date.isoformat()}")
    print(f"Games matched: {summary.games_matched}")
    print(f"Games final: {summary.games_final}")
    print(f"Rows filled: {summary.rows_filled}")
    print(f"Rows skipped: {summary.rows_skipped}")
    print(f"Unmatched games: {summary.unmatched_games}")
    print(f"Unmatched players: {summary.unmatched_players}")
    if dry_run:
        print("Dry run: workbook was not modified.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fill live MLB HR workbook results from MLB StatsAPI."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="MLB schedule date to fill, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--workbook",
        default=str(DEFAULT_WORKBOOK),
        help=f"Workbook CSV to update. Default: {DEFAULT_WORKBOOK}",
    )
    parser.add_argument(
        "--overwrite-filled",
        action="store_true",
        help="Replace existing actual_home_runs and game_status values.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and report matching results without modifying the workbook.",
    )
    args = parser.parse_args(argv)

    try:
        summary = fill_results_from_mlb_statsapi(
            workbook_path=Path(args.workbook),
            target_date=parse_target_date(args.date),
            overwrite_filled=args.overwrite_filled,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_summary(summary, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
