"""Fill MLB live HR workbook results from the public MLB StatsAPI."""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import requests

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


DEFAULT_WORKBOOK = Path(
    "data/theoddsapi/live_hr_snapshots/live_hr_results_workbook.csv"
)
DEFAULT_AUTOMATION_LOG_DIR = DEFAULT_WORKBOOK.parent / "automation_logs"
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
BOXSCORE_URL = "https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
REQUEST_TIMEOUT_SECONDS = 30
NEAR_MATCH_THRESHOLD = 0.78
RESULT_REASON_COLUMN = "result_reason"
FINAL_STATUS = "final"
VOID_STATUS = "void"
VOID_CANDIDATE_STATUS = "void_candidate"
MANUAL_REVIEW_STATUS = "manual_review_required"
TERMINAL_RESULT_STATUSES = {
    FINAL_STATUS,
    VOID_STATUS,
    VOID_CANDIDATE_STATUS,
    MANUAL_REVIEW_STATUS,
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


@dataclass(frozen=True)
class BoxscorePlayer:
    name: str
    normalized_name: str
    side: str
    has_batting_stats: bool


@dataclass(frozen=True)
class RelatedEventMatch:
    event_id: str
    home_team: str
    away_team: str
    game_pk: str

    def as_json(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_pk": self.game_pk,
        }


@dataclass(frozen=True)
class UnmatchedPlayerDiagnostic:
    target_date: date
    row_number: int
    commence_time: str
    event_id: str
    player: str
    normalized_player: str
    home_team: str
    away_team: str
    game_pk: str
    reason: str
    reason_detail: str
    result_status: str
    matched_game_found: bool
    game_final: bool
    possible_matches: tuple[str, ...] = ()
    related_event_matches: tuple[RelatedEventMatch, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "date": self.target_date.isoformat(),
            "row_number": self.row_number,
            "commence_time": self.commence_time,
            "event_id": self.event_id,
            "player": self.player,
            "normalized_player": self.normalized_player,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "game_pk": self.game_pk,
            "reason": self.reason,
            "reason_detail": self.reason_detail,
            "result_status": self.result_status,
            "matched_game_found": self.matched_game_found,
            "game_final": self.game_final,
            "possible_matches": list(self.possible_matches),
            "related_event_matches": [
                match.as_json() for match in self.related_event_matches
            ],
        }


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
    """Normalize a player name with the canonical MLB matcher."""

    return normalize_mlb_player_name(value)


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


def ensure_optional_result_columns(
    fieldnames: list[str],
    rows: list[dict[str, str]],
) -> list[str]:
    """Add optional result metadata columns when working with older workbooks."""

    updated_fieldnames = list(fieldnames)
    if RESULT_REASON_COLUMN not in updated_fieldnames:
        updated_fieldnames.append(RESULT_REASON_COLUMN)
    for row in rows:
        row.setdefault(RESULT_REASON_COLUMN, "")
    return updated_fieldnames


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


def extract_boxscore_player_match_counts(payload: JsonObject) -> dict[str, int]:
    """Return normalized roster candidate counts for the boxscore."""

    match_counts: Counter[str] = Counter()
    teams = payload.get("teams") or {}

    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, dict):
            continue
        for player in players.values():
            if not isinstance(player, dict):
                continue
            full_name = ((player.get("person") or {}).get("fullName"))
            normalized_name = normalize_player_name(full_name)
            if normalized_name:
                match_counts[normalized_name] += 1

    return dict(match_counts)


def extract_player_home_runs(payload: JsonObject) -> dict[str, int]:
    results: dict[str, int] = {}
    ambiguous_names = {
        normalized_name
        for normalized_name, match_count in extract_boxscore_player_match_counts(
            payload
        ).items()
        if match_count > 1
    }
    teams = payload.get("teams") or {}

    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, dict):
            continue
        for player in players.values():
            if not isinstance(player, dict):
                continue
            full_name = ((player.get("person") or {}).get("fullName"))
            normalized_name = normalize_player_name(full_name)
            if not normalized_name or normalized_name in ambiguous_names:
                continue
            batting = ((player.get("stats") or {}).get("batting") or {})
            home_runs = batting.get("homeRuns")
            if home_runs is None:
                continue
            try:
                parsed_home_runs = int(home_runs)
            except (TypeError, ValueError):
                continue
            if parsed_home_runs < 0:
                continue
            if normalized_name in results:
                ambiguous_names.add(normalized_name)
                results.pop(normalized_name, None)
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


def extract_boxscore_players(payload: JsonObject) -> list[BoxscorePlayer]:
    """Return display and normalized names for every player in the boxscore."""

    players_by_identity_and_side: dict[tuple[str, str], BoxscorePlayer] = {}
    teams = payload.get("teams") or {}

    for side in ("away", "home"):
        players = ((teams.get(side) or {}).get("players") or {})
        if not isinstance(players, dict):
            continue
        for player_key, player in players.items():
            if not isinstance(player, dict):
                continue
            person = player.get("person") or {}
            name = str(person.get("fullName") or "").strip()
            normalized_name = normalize_player_name(name)
            if not normalized_name:
                continue
            batting = ((player.get("stats") or {}).get("batting") or {})
            has_batting_stats = isinstance(batting, dict) and bool(batting)
            identity = str(person.get("id") or player_key or name).strip()
            key = (identity, side)
            existing = players_by_identity_and_side.get(key)
            if existing is None or (has_batting_stats and not existing.has_batting_stats):
                players_by_identity_and_side[key] = BoxscorePlayer(
                    name=name,
                    normalized_name=normalized_name,
                    side=side,
                    has_batting_stats=has_batting_stats,
                )

    return list(players_by_identity_and_side.values())


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


def row_needs_fill_attempt(
    row: Mapping[str, str],
    *,
    overwrite_filled: bool,
) -> bool:
    existing_status = str(row.get("game_status") or "").strip().casefold()
    if not overwrite_filled and existing_status in TERMINAL_RESULT_STATUSES:
        return False

    needs_home_runs = overwrite_filled or is_blank(row.get("actual_home_runs"))
    needs_status = overwrite_filled or is_blank(row.get("game_status"))
    return needs_home_runs or needs_status


def schedule_status_text(game: JsonObject | None) -> str:
    if game is None:
        return ""
    status = game.get("status") or {}
    return str(
        status.get("detailedState")
        or status.get("abstractGameState")
        or status.get("codedGameState")
        or ""
    ).strip()


def related_event_matches_for_player(
    normalized_player: str,
    current_key: tuple[str, str, str],
    player_game_keys: Mapping[str, set[tuple[str, str, str]]],
    workbook_games: Mapping[tuple[str, str, str], WorkbookGame],
    matched_games: Mapping[tuple[str, str, str], JsonObject],
) -> tuple[RelatedEventMatch, ...]:
    related_matches: list[RelatedEventMatch] = []
    for other_key in player_game_keys.get(normalized_player, set()):
        if other_key == current_key:
            continue
        workbook_game = workbook_games[other_key]
        game = matched_games.get(other_key)
        related_matches.append(
            RelatedEventMatch(
                event_id=other_key[0],
                home_team=workbook_game.home_team,
                away_team=workbook_game.away_team,
                game_pk=str(game.get("gamePk")) if game else "",
            )
        )
    return tuple(
        sorted(
            related_matches,
            key=lambda match: (
                match.event_id,
                match.away_team,
                match.home_team,
                match.game_pk,
            ),
        )
    )


def build_unmatched_player_diagnostic(
    *,
    target_date: date,
    row_index: int,
    row: Mapping[str, str],
    key: tuple[str, str, str],
    workbook_game: WorkbookGame,
    game: JsonObject | None,
    reason: str,
    reason_detail: str,
    result_status: str = "",
    boxscore_players: list[BoxscorePlayer] | None = None,
    related_matches: tuple[RelatedEventMatch, ...] = (),
) -> UnmatchedPlayerDiagnostic:
    player = str(row.get("player") or "").strip()
    suggestions: tuple[str, ...] = ()
    if boxscore_players:
        suggestions, _ = closest_boxscore_names(player, boxscore_players)

    return UnmatchedPlayerDiagnostic(
        target_date=target_date,
        row_number=row_index + 2,
        commence_time=str(row.get("commence_time") or "").strip(),
        event_id=str(row.get("event_id") or key[0] or "").strip(),
        player=player,
        normalized_player=normalize_player_name(player),
        home_team=str(row.get("home_team") or workbook_game.home_team).strip(),
        away_team=str(row.get("away_team") or workbook_game.away_team).strip(),
        game_pk=str(game.get("gamePk")) if game else "",
        reason=reason,
        reason_detail=reason_detail,
        result_status=result_status,
        matched_game_found=game is not None,
        game_final=bool(game and is_final_game(game)),
        possible_matches=suggestions,
        related_event_matches=related_matches,
    )


def classify_missing_final_player(
    *,
    player: str,
    normalized_player: str,
    boxscore_players: list[BoxscorePlayer],
    related_matches: tuple[RelatedEventMatch, ...],
    exact_match_count: int = 0,
) -> tuple[str, str]:
    _, best_score = closest_boxscore_names(player, boxscore_players)

    if not normalized_player:
        return (
            "blank_player_name",
            "Workbook player name is blank after normalization.",
        )
    if exact_match_count > 1:
        return (
            "ambiguous_normalized_player_match",
            (
                "Multiple StatsAPI boxscore roster players normalize to this "
                "workbook player name; the row was left unresolved."
            ),
        )
    if related_matches:
        return (
            "event_player_team_mismatch_possible",
            (
                "Player appears on another final target-date StatsAPI boxscore, "
                "but not on this event/team boxscore."
            ),
        )
    if not boxscore_players:
        return (
            "boxscore_roster_empty",
            "Matched final StatsAPI boxscore did not include any rostered players.",
        )
    if best_score >= NEAR_MATCH_THRESHOLD:
        return (
            "possible_name_mismatch",
            (
                "No exact normalized player match was found in the final "
                "StatsAPI boxscore; nearest names suggest a spelling or alias mismatch."
            ),
        )
    return (
        "player_missing_from_boxscore_roster",
        "Player was not found on the matched final StatsAPI boxscore roster.",
    )


def result_status_for_missing_final_player(reason: str) -> str:
    if reason == "player_missing_from_boxscore_roster":
        return VOID_CANDIDATE_STATUS
    return MANUAL_REVIEW_STATUS


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
    unmatched_player_diagnostics: list[UnmatchedPlayerDiagnostic] | None = None,
    get_json: JsonFetcher = fetch_json,
) -> FillSummary:
    if unmatched_player_diagnostics is not None:
        unmatched_player_diagnostics.clear()

    fieldnames, rows = load_workbook(workbook_path)
    fieldnames = ensure_optional_result_columns(fieldnames, rows)
    workbook_games = build_workbook_games(rows, target_date)

    schedule_payload = get_json(
        SCHEDULE_URL,
        {"sportId": 1, "date": target_date.isoformat()},
    )
    scheduled_games = schedule_games_for_date(schedule_payload, target_date)

    matched_games: dict[tuple[str, str, str], JsonObject] = {}
    unmatched_game_keys: set[tuple[str, str, str]] = set()
    schedule_candidate_counts: dict[tuple[str, str, str], int] = {}
    for key, workbook_game in workbook_games.items():
        candidates = scheduled_games.get((key[1], key[2]), [])
        schedule_candidate_counts[key] = len(candidates)
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
    roster_match_counts_by_game_pk: dict[str, dict[str, int]] = {}
    boxscore_players_by_game_pk: dict[str, list[BoxscorePlayer]] = {}
    for game in final_games.values():
        game_pk = str(game["gamePk"])
        if game_pk not in home_runs_by_game_pk:
            boxscore_payload = get_json(BOXSCORE_URL.format(game_pk=game_pk), None)
            home_runs_by_game_pk[game_pk] = extract_player_home_runs(boxscore_payload)
            roster_match_counts = extract_boxscore_player_match_counts(
                boxscore_payload
            )
            roster_match_counts_by_game_pk[game_pk] = roster_match_counts
            roster_names_by_game_pk[game_pk] = set(roster_match_counts)
            boxscore_players_by_game_pk[game_pk] = extract_boxscore_players(
                boxscore_payload
            )

    player_game_keys: defaultdict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for key, game in final_games.items():
        game_pk = str(game["gamePk"])
        for normalized_name in roster_names_by_game_pk.get(game_pk, set()):
            player_game_keys[normalized_name].add(key)

    rows_filled = 0
    rows_skipped = 0
    unmatched_player_keys: set[tuple[str, str, str, str]] = set()

    for key, workbook_game in workbook_games.items():
        game = final_games.get(key)
        if game is None:
            if unmatched_player_diagnostics is not None:
                matched_game = matched_games.get(key)
                if matched_game is None:
                    candidate_count = schedule_candidate_counts.get(key, 0)
                    if candidate_count == 0:
                        reason_detail = (
                            "No MLB StatsAPI schedule game matched the workbook "
                            "home/away teams for the target date."
                        )
                    else:
                        reason_detail = (
                            "MLB StatsAPI returned "
                            f"{candidate_count} schedule candidates for these teams, "
                            "but none could be selected uniquely by commence_time."
                        )
                    reason = "game_not_matched"
                else:
                    reason = "game_not_final"
                    status_text = schedule_status_text(matched_game)
                    reason_detail = (
                        "Matched MLB StatsAPI game is not final"
                        + (f" (status={status_text})." if status_text else ".")
                    )

                for row_index in workbook_game.row_indexes:
                    row = rows[row_index]
                    if not row_needs_fill_attempt(
                        row, overwrite_filled=overwrite_filled
                    ):
                        continue
                    unmatched_player_diagnostics.append(
                        build_unmatched_player_diagnostic(
                            target_date=target_date,
                            row_index=row_index,
                            row=row,
                            key=key,
                            workbook_game=workbook_game,
                            game=matched_game,
                            reason=reason,
                            reason_detail=reason_detail,
                        )
                    )
            rows_skipped += len(workbook_game.row_indexes)
            continue

        game_pk = str(game["gamePk"])
        home_runs = home_runs_by_game_pk[game_pk]
        roster_match_counts = roster_match_counts_by_game_pk[game_pk]
        boxscore_players = boxscore_players_by_game_pk.get(game_pk, [])
        for row_index in workbook_game.row_indexes:
            row = rows[row_index]
            existing_status = str(row.get("game_status") or "").strip().casefold()
            if not overwrite_filled and existing_status in TERMINAL_RESULT_STATUSES:
                rows_skipped += 1
                continue

            needs_home_runs = overwrite_filled or is_blank(row.get("actual_home_runs"))
            needs_status = overwrite_filled or is_blank(row.get("game_status"))

            if not needs_home_runs and not needs_status:
                rows_skipped += 1
                continue

            normalized_player = normalize_player_name(row.get("player"))
            if normalized_player not in home_runs:
                if roster_match_counts.get(normalized_player, 0) == 1:
                    if overwrite_filled:
                        row["actual_home_runs"] = ""
                    if needs_status:
                        row["game_status"] = VOID_STATUS
                        row[RESULT_REASON_COLUMN] = (
                            "player_rostered_without_batting_stats"
                        )
                        rows_filled += 1
                    else:
                        rows_skipped += 1
                    continue
                unmatched_player_keys.add((*key, normalized_player))
                classification_status = ""
                if unmatched_player_diagnostics is not None:
                    related_matches = related_event_matches_for_player(
                        normalized_player,
                        key,
                        player_game_keys,
                        workbook_games,
                        final_games,
                    )
                    reason, reason_detail = classify_missing_final_player(
                        player=str(row.get("player") or "").strip(),
                        normalized_player=normalized_player,
                        boxscore_players=boxscore_players,
                        related_matches=related_matches,
                        exact_match_count=roster_match_counts.get(
                            normalized_player, 0
                        ),
                    )
                    classification_status = result_status_for_missing_final_player(
                        reason
                    )
                    unmatched_player_diagnostics.append(
                        build_unmatched_player_diagnostic(
                            target_date=target_date,
                            row_index=row_index,
                            row=row,
                            key=key,
                            workbook_game=workbook_game,
                            game=game,
                            reason=reason,
                            reason_detail=reason_detail,
                            result_status=classification_status,
                            boxscore_players=boxscore_players,
                            related_matches=related_matches,
                        )
                    )
                if not classification_status:
                    reason, _ = classify_missing_final_player(
                        player=str(row.get("player") or "").strip(),
                        normalized_player=normalized_player,
                        boxscore_players=boxscore_players,
                        related_matches=related_event_matches_for_player(
                            normalized_player,
                            key,
                            player_game_keys,
                            workbook_games,
                            final_games,
                        ),
                        exact_match_count=roster_match_counts.get(
                            normalized_player, 0
                        ),
                    )
                    classification_status = result_status_for_missing_final_player(
                        reason
                    )
                if needs_home_runs or overwrite_filled:
                    row["actual_home_runs"] = ""
                if needs_status:
                    row["game_status"] = classification_status
                    row[RESULT_REASON_COLUMN] = reason
                    rows_filled += 1
                else:
                    rows_skipped += 1
                continue

            if needs_home_runs:
                row["actual_home_runs"] = str(home_runs[normalized_player])

            if needs_status:
                row["game_status"] = FINAL_STATUS
            row[RESULT_REASON_COLUMN] = ""
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


def default_diagnostic_report_dir(workbook_path: Path) -> Path:
    if workbook_path == DEFAULT_WORKBOOK:
        return DEFAULT_AUTOMATION_LOG_DIR
    return workbook_path.parent / "automation_logs"


def fill_summary_json(summary: FillSummary) -> dict[str, object]:
    return {
        "target_date": summary.target_date.isoformat(),
        "games_matched": summary.games_matched,
        "games_final": summary.games_final,
        "rows_filled": summary.rows_filled,
        "rows_skipped": summary.rows_skipped,
        "unmatched_games": summary.unmatched_games,
        "unmatched_players": summary.unmatched_players,
    }


def write_unmatched_player_json_report(
    report_dir: Path,
    *,
    workbook_path: Path,
    summary: FillSummary,
    diagnostics: list[UnmatchedPlayerDiagnostic],
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().astimezone()
    report_path = report_dir / (
        "live_hr_unmatched_players_"
        f"{summary.target_date.strftime('%Y%m%d')}_"
        f"{generated_at.strftime('%Y%m%d_%H%M%S')}.json"
    )
    payload = {
        "report_type": "live_hr_unmatched_player_diagnostics",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "target_date": summary.target_date.isoformat(),
        "workbook": str(workbook_path),
        "summary": fill_summary_json(summary),
        "void_candidate_count": sum(
            1
            for diagnostic in diagnostics
            if diagnostic.result_status == VOID_CANDIDATE_STATUS
        ),
        "manual_review_required_count": sum(
            1
            for diagnostic in diagnostics
            if diagnostic.result_status == MANUAL_REVIEW_STATUS
        ),
        "diagnostic_row_count": len(diagnostics),
        "unmatched_players": [diagnostic.as_json() for diagnostic in diagnostics],
    }
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report_path


def print_unmatched_player_diagnostics(
    diagnostics: list[UnmatchedPlayerDiagnostic],
    target_date: date,
) -> None:
    print()
    print("Unmatched player diagnostics")
    print(f"Target date: {target_date.isoformat()}")
    print(f"Diagnostic rows: {len(diagnostics)}")
    print(
        "Void candidates: "
        f"{sum(1 for diagnostic in diagnostics if diagnostic.result_status == VOID_CANDIDATE_STATUS)}"
    )
    print(
        "Manual review rows: "
        f"{sum(1 for diagnostic in diagnostics if diagnostic.result_status == MANUAL_REVIEW_STATUS)}"
    )

    if not diagnostics:
        print("No unmatched players found.")
        return

    for diagnostic in diagnostics:
        print()
        print(f"Event ID: {diagnostic.event_id}")
        print(f"Away team: {diagnostic.away_team}")
        print(f"Home team: {diagnostic.home_team}")
        print(f"Player: {diagnostic.player}")
        print(f"Normalized player: {diagnostic.normalized_player or '-'}")
        print(f"Workbook row: {diagnostic.row_number}")
        print(f"Game PK: {diagnostic.game_pk or '-'}")
        print(f"Reason: {diagnostic.reason}")
        print(f"Reason detail: {diagnostic.reason_detail}")
        if diagnostic.result_status:
            print(f"Classification: {diagnostic.result_status}")

        if diagnostic.related_event_matches:
            print("Related event matches:")
            for match in diagnostic.related_event_matches:
                print(
                    "  - "
                    f"{match.event_id}: {match.away_team} @ {match.home_team} "
                    f"(game_pk={match.game_pk or '-'})"
                )

        if diagnostic.possible_matches:
            print("Closest boxscore players:")
            for match in diagnostic.possible_matches:
                print(f"  - {match}")


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
    parser.add_argument(
        "--diagnostic",
        action="store_true",
        help=(
            "Print unresolved player match diagnostics and write a JSON report "
            "to automation_logs."
        ),
    )
    parser.add_argument(
        "--diagnostic-report-dir",
        type=Path,
        default=None,
        help=(
            "Directory for --diagnostic JSON reports. Default: an automation_logs "
            "directory next to the workbook."
        ),
    )
    args = parser.parse_args(argv)

    try:
        workbook_path = Path(args.workbook)
        target_date = parse_target_date(args.date)
        diagnostics: list[UnmatchedPlayerDiagnostic] | None = (
            [] if args.diagnostic else None
        )
        summary = fill_results_from_mlb_statsapi(
            workbook_path=workbook_path,
            target_date=target_date,
            overwrite_filled=args.overwrite_filled,
            dry_run=args.dry_run,
            unmatched_player_diagnostics=diagnostics,
            get_json=fetch_json,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print_summary(summary, dry_run=args.dry_run)
    if diagnostics is not None:
        print_unmatched_player_diagnostics(diagnostics, summary.target_date)
        report_dir = args.diagnostic_report_dir or default_diagnostic_report_dir(
            workbook_path
        )
        report_path = write_unmatched_player_json_report(
            report_dir,
            workbook_path=workbook_path,
            summary=summary,
            diagnostics=diagnostics,
        )
        print()
        print(f"Diagnostic JSON report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
