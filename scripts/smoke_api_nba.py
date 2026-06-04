"""Read-only API-NBA smoke test for CourtVision Research Mode.

This script only probes API-NBA stats endpoints and writes compact diagnostics.
It does not import or call the betting pipeline, Kelly staking, Elite selection,
BallDontLie odds, or any production run entrypoint.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests

API_NBA_BASE_URL = "https://v2.nba.api-sports.io"
API_NBA_KEY_ENV = "API_NBA_KEY"
API_SPORTS_KEY_ENV = "API_SPORTS_KEY"
DEFAULT_OUTPUT_DIR = Path("outputs/runtime/diagnostics")

ENDPOINT_LABELS = (
    "games",
    "teams",
    "players",
    "players/statistics",
    "teams/statistics",
)

VERDICT_LABELS = (
    ("api_access_works", "API access works"),
    ("games_available_direct", "games_available_direct"),
    ("games_available_fallback", "games_available_fallback"),
    ("games_available_any", "games_available_any"),
    ("games_probe_attempt_count", "games_probe_attempt_count"),
    ("games_probe_note", "games_probe_note"),
    ("player_stats_available", "player stats available"),
    ("team_stats_available", "team stats available"),
    ("usable_for_research_mode", "usable for Research Mode"),
    ("usable_for_betting_mode", "usable for Betting Mode"),
)

PLAYOFF_UNRELIABLE_MESSAGE = (
    "API-NBA games endpoint appears usable for regular season but unreliable "
    "for playoff/finals schedule lookup."
)

LOG = logging.getLogger("api_nba_smoke")


def _clean_key(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _mask_key(value: str | None) -> str:
    cleaned = _clean_key(value)
    if not cleaned:
        return "<empty>"
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def resolve_api_key() -> tuple[str, str]:
    """Return the first configured API key and its source env var."""
    for env_var in (API_NBA_KEY_ENV, API_SPORTS_KEY_ENV):
        value = _clean_key(os.getenv(env_var))
        if value:
            return value, env_var
    return "", "<not found>"


def _build_session(api_key: str) -> requests.Session:
    session = requests.Session()
    session.headers.clear()
    session.headers.update({"x-apisports-key": api_key})
    return session


def _response_text_preview(response: requests.Response, limit: int = 180) -> str:
    try:
        text = response.text or ""
    except Exception:
        return "<unavailable>"
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] if text else "<empty>"


def _get(
    session: requests.Session,
    endpoint: str,
    params: dict[str, Any],
    timeout: int,
) -> tuple[int, dict[str, Any] | None, str | None]:
    """Run one GET request and return status, JSON object, and a short error."""
    url = f"{API_NBA_BASE_URL}/{endpoint.lstrip('/')}"
    try:
        response = session.get(url, params=params, timeout=timeout)
    except requests.exceptions.Timeout:
        return -1, None, "timeout"
    except requests.RequestException as exc:
        return -1, None, f"request_error: {exc}"
    except Exception as exc:
        return -1, None, f"unexpected_error: {exc}"

    status = int(getattr(response, "status_code", -1) or -1)
    try:
        parsed = response.json()
    except ValueError as exc:
        if status == 200:
            return status, None, f"json_parse_error: {exc}"
        return status, None, _response_text_preview(response)
    except Exception as exc:
        return status, None, f"json_parse_error: {exc}"

    if isinstance(parsed, dict):
        return status, parsed, None if status == 200 else _response_text_preview(response)

    return status, None, f"unexpected_json_type: {type(parsed).__name__}"


def _api_errors_present(body: dict[str, Any] | None) -> bool:
    if not isinstance(body, dict):
        return False
    errors = body.get("errors")
    if errors in (None, "", [], {}):
        return False
    return True


def _provider_status(
    http_status: int,
    body: dict[str, Any] | None = None,
    error: str | None = None,
) -> str:
    if http_status == 200:
        if error and "json_parse_error" in error:
            return "malformed_response"
        if body is None:
            return "empty_response"
        if _api_errors_present(body):
            return "provider_error"
        return "ok"

    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        408: "timeout",
        429: "rate_limited",
        500: "server_error",
        502: "bad_gateway",
        503: "unavailable",
        504: "gateway_timeout",
        -1: "connection_error",
    }
    return mapping.get(http_status, f"http_{http_status}")


def _response_items(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    response = body.get("response")
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        return [response]
    return []


def _first_item(body: dict[str, Any] | None) -> dict[str, Any]:
    items = _response_items(body)
    return items[0] if items else {}


def _object_count(body: dict[str, Any] | None) -> int:
    if not isinstance(body, dict):
        return 0
    results = body.get("results")
    if isinstance(results, int) and results >= 0:
        return results
    return len(_response_items(body))


def _sample_keys(body: dict[str, Any] | None, limit: int = 8) -> list[str]:
    return list(_first_item(body).keys())[:limit]


def _value_present(value: Any) -> bool:
    return value not in (None, "", [], {})


def _path_value(item: dict[str, Any], path: str) -> Any:
    current: Any = item
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _has_path(item: dict[str, Any], path: str) -> bool:
    return _value_present(_path_value(item, path))


def _has_any_path(item: dict[str, Any], *paths: str) -> bool:
    return any(_has_path(item, path) for path in paths)


def _game_date(item: dict[str, Any]) -> str:
    for path in ("date.start", "date", "game.date", "start"):
        value = _path_value(item, path) if "." in path else item.get(path)
        if _value_present(value):
            return str(value)[:10]
    return ""


def _sample_game_dates(body: dict[str, Any] | None, limit: int = 5) -> list[str]:
    dates: list[str] = []
    for item in _response_items(body):
        game_date = _game_date(item)
        if game_date:
            dates.append(game_date)
        if len(dates) >= limit:
            break
    return dates


def _sample_team_names(body: dict[str, Any] | None, limit: int = 5) -> list[str]:
    names: list[str] = []
    for item in _response_items(body):
        home = _path_value(item, "teams.home.name")
        visitors = _path_value(item, "teams.visitors.name")
        if _value_present(home) and _value_present(visitors):
            names.append(f"{home} vs {visitors}")
        elif _value_present(home):
            names.append(str(home))
        elif _value_present(visitors):
            names.append(str(visitors))
        if len(names) >= limit:
            break
    return names


def _matched_target_date_count(body: dict[str, Any] | None, target_date: str) -> int:
    return sum(1 for item in _response_items(body) if _game_date(item) == target_date)


def _body_with_games(body: dict[str, Any] | None, games: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    clone = dict(body)
    clone["response"] = list(games)
    clone["results"] = len(games)
    return clone


def _filter_games_body_by_date(body: dict[str, Any] | None, target_date: str) -> dict[str, Any] | None:
    games = [item for item in _response_items(body) if _game_date(item) == target_date]
    return _body_with_games(body, games)


def _games_probe_specs(target_date: str, season: str) -> list[dict[str, Any]]:
    return [
        {"attempt": "date_only", "params": {"date": target_date}, "direct": True},
        {"attempt": "date_season", "params": {"date": target_date, "season": season}, "direct": True},
        {"attempt": "date_league", "params": {"date": target_date, "league": "standard"}, "direct": True},
        {
            "attempt": "date_season_league",
            "params": {"date": target_date, "season": season, "league": "standard"},
            "direct": True,
        },
        {"attempt": "season_only_local_filter", "params": {"season": season}, "direct": False},
        {
            "attempt": "playoff_stage_2_probe",
            "params": {"date": target_date, "season": season, "league": "standard", "stage": 2},
            "direct": True,
            "playoff_style": True,
        },
        {
            "attempt": "playoff_stage_playoffs_probe",
            "params": {"date": target_date, "season": season, "league": "standard", "stage": "playoffs"},
            "direct": True,
            "playoff_style": True,
        },
    ]


def _games_attempt_available(attempt: dict[str, Any]) -> bool:
    if attempt.get("provider_status") != "ok":
        return False
    if attempt.get("direct"):
        return int(attempt.get("object_count") or 0) > 0
    return int(attempt.get("matched_target_date_count") or 0) > 0


def _games_probe_note(
    *,
    direct_available: bool,
    fallback_available: bool,
    any_accessible: bool,
    regular_season_control_available: bool | None = None,
) -> str:
    if direct_available:
        return "direct date games probe found target-date games"
    if fallback_available:
        return "direct date games probes were empty; season-only fallback matched target date locally"
    if regular_season_control_available is True:
        return PLAYOFF_UNRELIABLE_MESSAGE
    if any_accessible:
        return "games endpoint was accessible, but no probe matched target-date games"
    return "games endpoint was not accessible"


def _games_summary_from_attempts(
    attempts: list[dict[str, Any]],
    selected_body: dict[str, Any] | None,
    regular_season_control_available: bool | None = None,
) -> dict[str, Any]:
    direct_available = any(_games_attempt_available(item) for item in attempts if item.get("direct"))
    fallback_available = any(_games_attempt_available(item) for item in attempts if not item.get("direct"))
    any_accessible = any(item.get("provider_status") == "ok" for item in attempts)
    any_available = direct_available or fallback_available
    first_attempt = attempts[0] if attempts else {}
    item = _first_item(selected_body)

    diagnostic = {
        "endpoint": "games",
        "http_status": 200 if any_accessible else int(first_attempt.get("http_status") or -1),
        "provider_status": "ok" if any_accessible else str(first_attempt.get("provider_status") or "connection_error"),
        "object_count": _object_count(selected_body),
        "sample_keys": _sample_keys(selected_body),
        "has_player_id": False,
        "has_player_name": False,
        "has_team_id": _has_any_path(item, "teams.home.id", "teams.visitors.id"),
        "has_game_id": _has_path(item, "id"),
        "has_points": _has_any_path(item, "scores.home.points", "scores.visitors.points"),
        "has_rebounds": False,
        "has_assists": False,
        "games_available_direct": "yes" if direct_available else "no",
        "games_available_fallback": "yes" if fallback_available else "no",
        "games_available_any": "yes" if any_available else "no",
        "games_probe_attempt_count": len(attempts),
        "games_probe_note": _games_probe_note(
            direct_available=direct_available,
            fallback_available=fallback_available,
            any_accessible=any_accessible,
            regular_season_control_available=regular_season_control_available,
        ),
    }
    if regular_season_control_available is not None:
        diagnostic["regular_season_control_games_available"] = (
            "yes" if regular_season_control_available else "no"
        )
    return diagnostic


def _diagnostic_base(
    endpoint: str,
    http_status: int,
    body: dict[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    diagnostic = {
        "endpoint": endpoint,
        "http_status": http_status,
        "provider_status": _provider_status(http_status, body, error),
        "object_count": _object_count(body),
        "sample_keys": _sample_keys(body),
        "has_player_id": False,
        "has_player_name": False,
        "has_team_id": False,
        "has_game_id": False,
        "has_points": False,
        "has_rebounds": False,
        "has_assists": False,
    }
    if error:
        diagnostic["error"] = str(error)[:180]
    return diagnostic


def _extract_game_id(games_body: dict[str, Any] | None) -> int | None:
    item = _first_item(games_body)
    value = item.get("id")
    try:
        return int(value) if _value_present(value) else None
    except (TypeError, ValueError):
        return None


def _extract_team_id(
    games_body: dict[str, Any] | None,
    teams_body: dict[str, Any] | None,
) -> int | None:
    game = _first_item(games_body)
    for path in ("teams.home.id", "teams.visitors.id"):
        value = _path_value(game, path)
        if _value_present(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                pass

    for item in _response_items(teams_body):
        value = item.get("id")
        if _value_present(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _extract_player_id(players_body: dict[str, Any] | None) -> int | None:
    for item in _response_items(players_body):
        value = item.get("id")
        if _value_present(value):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return None


def _probe_games_attempts(
    session: requests.Session,
    target_date: str,
    season: str,
    timeout: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    attempts: list[dict[str, Any]] = []
    selected_body: dict[str, Any] | None = None

    for spec in _games_probe_specs(target_date, season):
        status, body, error = _get(session, "games", dict(spec["params"]), timeout)
        matched_body = (
            body
            if spec.get("direct")
            else _filter_games_body_by_date(body, target_date)
        )
        attempt = {
            "endpoint": "games",
            "attempt": spec["attempt"],
            "params": dict(spec["params"]),
            "http_status": status,
            "provider_status": _provider_status(status, body, error),
            "object_count": _object_count(body),
            "sample_keys": _sample_keys(body),
            "sample_game_dates": _sample_game_dates(body),
            "sample_team_names": _sample_team_names(body),
            "matched_target_date_count": _matched_target_date_count(body, target_date),
            "direct": bool(spec.get("direct")),
            "playoff_style": bool(spec.get("playoff_style", False)),
        }
        if error:
            attempt["error"] = str(error)[:180]
        attempts.append(attempt)

        if selected_body is None and _games_attempt_available(attempt):
            selected_body = matched_body

    return attempts, selected_body


def _probe_games(
    session: requests.Session,
    target_date: str,
    season: str,
    timeout: int,
    regular_season_control_available: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    attempts, selected_body = _probe_games_attempts(session, target_date, season, timeout)
    diagnostic = _games_summary_from_attempts(
        attempts,
        selected_body,
        regular_season_control_available=regular_season_control_available,
    )
    return diagnostic, selected_body, attempts


def _games_available_for_date(
    session: requests.Session,
    target_date: str,
    season: str,
    timeout: int,
) -> bool:
    attempts, _selected_body = _probe_games_attempts(session, target_date, season, timeout)
    return any(_games_attempt_available(item) for item in attempts)


def _probe_teams(
    session: requests.Session,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    status, body, error = _get(session, "teams", {"league": "standard"}, timeout)
    item = _first_item(body)
    diagnostic = _diagnostic_base("teams", status, body, error)
    diagnostic["has_team_id"] = _has_path(item, "id")
    return diagnostic, body


def _probe_players(
    session: requests.Session,
    season: str,
    timeout: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    status, body, error = _get(session, "players", {"season": season}, timeout)
    item = _first_item(body)
    diagnostic = _diagnostic_base("players", status, body, error)
    diagnostic.update(
        {
            "has_player_id": _has_path(item, "id"),
            "has_player_name": _has_any_path(item, "firstname", "lastname", "name"),
            "has_team_id": _has_any_path(
                item,
                "team.id",
                "teams.id",
                "leagues.standard.team.id",
                "leagues.standard.teams.id",
            ),
        }
    )
    return diagnostic, body


def _player_stats_params(
    games_body: dict[str, Any] | None,
    players_body: dict[str, Any] | None,
    teams_body: dict[str, Any] | None,
    season: str,
) -> dict[str, Any]:
    game_id = _extract_game_id(games_body)
    if game_id is not None:
        return {"game": game_id}

    player_id = _extract_player_id(players_body)
    if player_id is not None:
        return {"id": player_id, "season": season}

    team_id = _extract_team_id(games_body, teams_body)
    if team_id is not None:
        return {"team": team_id, "season": season}

    return {"season": season}


def _probe_player_stats(
    session: requests.Session,
    games_body: dict[str, Any] | None,
    players_body: dict[str, Any] | None,
    teams_body: dict[str, Any] | None,
    season: str,
    timeout: int,
) -> dict[str, Any]:
    params = _player_stats_params(games_body, players_body, teams_body, season)
    status, body, error = _get(session, "players/statistics", params, timeout)
    item = _first_item(body)
    diagnostic = _diagnostic_base("players/statistics", status, body, error)
    diagnostic.update(
        {
            "has_game_id": _has_path(item, "game.id"),
            "has_player_id": _has_path(item, "player.id"),
            "has_player_name": _has_any_path(item, "player.firstname", "player.lastname", "player.name"),
            "has_team_id": _has_path(item, "team.id"),
            "has_points": _has_path(item, "points"),
            "has_rebounds": _has_any_path(item, "totReb", "offReb", "defReb"),
            "has_assists": _has_path(item, "assists"),
        }
    )
    return diagnostic


def _probe_team_stats(
    session: requests.Session,
    games_body: dict[str, Any] | None,
    teams_body: dict[str, Any] | None,
    season: str,
    timeout: int,
) -> dict[str, Any]:
    team_id = _extract_team_id(games_body, teams_body) or 1
    status, body, error = _get(
        session,
        "teams/statistics",
        {"id": team_id, "season": season},
        timeout,
    )
    item = _first_item(body)
    diagnostic = _diagnostic_base("teams/statistics", status, body, error)
    diagnostic.update(
        {
            "has_team_id": bool(team_id),
            "has_points": _has_path(item, "points"),
            "has_rebounds": _has_any_path(item, "totReb", "offReb", "defReb"),
            "has_assists": _has_path(item, "assists"),
        }
    )
    return diagnostic


def _build_verdict(results: list[dict[str, Any]]) -> dict[str, str]:
    by_endpoint = {str(item.get("endpoint")): item for item in results}

    def ok(endpoint: str) -> bool:
        return by_endpoint.get(endpoint, {}).get("provider_status") == "ok"

    games = by_endpoint.get("games", {})
    api_ok = any(ok(endpoint) for endpoint in ENDPOINT_LABELS)
    games_any = str(games.get("games_available_any") or "no")
    player_stats_ok = (
        ok("players/statistics")
        and int(by_endpoint.get("players/statistics", {}).get("object_count") or 0) > 0
        and bool(by_endpoint.get("players/statistics", {}).get("has_points"))
    )
    team_stats_ok = (
        ok("teams/statistics")
        and int(by_endpoint.get("teams/statistics", {}).get("object_count") or 0) > 0
        and bool(by_endpoint.get("teams/statistics", {}).get("has_points"))
    )

    return {
        "api_access_works": "yes" if api_ok else "no",
        "games_available": games_any,
        "games_available_direct": str(games.get("games_available_direct") or "no"),
        "games_available_fallback": str(games.get("games_available_fallback") or "no"),
        "games_available_any": games_any,
        "games_probe_attempt_count": str(games.get("games_probe_attempt_count") or 0),
        "games_probe_note": str(games.get("games_probe_note") or ""),
        "player_stats_available": "yes" if player_stats_ok else "no",
        "team_stats_available": "yes" if team_stats_ok else "no",
        "usable_for_research_mode": "yes" if api_ok and (player_stats_ok or team_stats_ok) else "no",
        "usable_for_betting_mode": "no unless market lines/odds exist",
    }


def _missing_key_verdict() -> dict[str, str]:
    return {
        "api_access_works": "no",
        "games_available": "no",
        "games_available_direct": "no",
        "games_available_fallback": "no",
        "games_available_any": "no",
        "games_probe_attempt_count": "0",
        "games_probe_note": "API key missing",
        "player_stats_available": "no",
        "team_stats_available": "no",
        "usable_for_research_mode": "no",
        "usable_for_betting_mode": "no unless market lines/odds exist",
    }


def run_smoke_test(
    api_key: str,
    target_date: str,
    season: str,
    timeout: int = 30,
    regular_season_check_date: str | None = None,
) -> dict[str, Any]:
    session = _build_session(api_key)
    LOG.info("Probing API-NBA key=%s date=%s season=%s", _mask_key(api_key), target_date, season)

    regular_season_available: bool | None = None
    if regular_season_check_date:
        regular_season_available = _games_available_for_date(
            session,
            regular_season_check_date,
            season,
            timeout,
        )

    results: list[dict[str, Any]] = []

    games_diag, games_body, games_attempts = _probe_games(
        session,
        target_date,
        season,
        timeout,
        regular_season_control_available=regular_season_available,
    )
    results.append(games_diag)

    teams_diag, teams_body = _probe_teams(session, timeout)
    results.append(teams_diag)

    players_diag, players_body = _probe_players(session, season, timeout)
    results.append(players_diag)

    results.append(_probe_player_stats(session, games_body, players_body, teams_body, season, timeout))
    results.append(_probe_team_stats(session, games_body, teams_body, season, timeout))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "season": str(season),
        "base_url": API_NBA_BASE_URL,
        "key_preview": _mask_key(api_key),
        "games_probe_attempts": games_attempts,
        "endpoints": results,
        "verdict": _build_verdict(results),
    }
    if regular_season_check_date:
        payload["regular_season_check_date"] = regular_season_check_date
        payload["regular_season_games_available"] = "yes" if regular_season_available else "no"
    return payload


def _print_verdict(verdict: dict[str, str]) -> None:
    print("API-NBA smoke verdict")
    for key, label in VERDICT_LABELS:
        print(f"{label}: {verdict.get(key, 'no')}")
    if verdict.get("games_probe_note") == PLAYOFF_UNRELIABLE_MESSAGE:
        print(PLAYOFF_UNRELIABLE_MESSAGE)


def _default_season(today: date | None = None) -> str:
    current = today or date.today()
    return str(current.year - 1 if current.month < 10 else current.year)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test API-NBA stats endpoints.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD format.")
    parser.add_argument("--season", default=_default_season(), help="NBA season year, for example 2025.")
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Defaults to outputs/runtime/diagnostics/api_nba_smoke_YYYY-MM-DD.json.",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument(
        "--regular-season-check-date",
        default=None,
        help="Optional known regular-season date to compare against playoff/finals lookup behavior.",
    )
    return parser.parse_args(argv)


def _output_path(value: str | None, target_date: str) -> Path:
    if value:
        return Path(value)
    return DEFAULT_OUTPUT_DIR / f"api_nba_smoke_{target_date}.json"


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)

    api_key, key_source = resolve_api_key()
    if not api_key:
        print(f"API-NBA key={_mask_key(api_key)} source={key_source}")
        print(f"Set {API_NBA_KEY_ENV} or {API_SPORTS_KEY_ENV} before running the smoke test.")
        _print_verdict(_missing_key_verdict())
        return 1

    print(f"API-NBA key={_mask_key(api_key)} source={key_source}")
    payload = run_smoke_test(
        api_key=api_key,
        target_date=str(args.date),
        season=str(args.season),
        timeout=max(1, int(args.timeout)),
        regular_season_check_date=(
            str(args.regular_season_check_date) if args.regular_season_check_date else None
        ),
    )

    output_path = _output_path(args.output, str(args.date))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"diagnostic output: {output_path}")
    _print_verdict(payload["verdict"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
