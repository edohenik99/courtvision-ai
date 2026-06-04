"""Read-only The Odds API NBA player-props smoke test for CourtVision.

This script probes event-level NBA player props at low volume and writes a
compact diagnostics file. It is intentionally standalone and does not call the
betting runtime, Kelly sizing, Elite selection, or existing provider adapters.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests


THE_ODDS_API_BASE_URL = "https://api.the-odds-api.com"
THE_ODDS_API_KEY_ENV = "THE_ODDS_API_KEY"
DEFAULT_SPORT = "basketball_nba"
DEFAULT_REGIONS = "us"
DEFAULT_ODDS_FORMAT = "american"
DEFAULT_MARKETS = (
    "player_points",
    "player_rebounds",
    "player_assists",
)
DEFAULT_MARKETS_TEXT = ",".join(DEFAULT_MARKETS)
DEFAULT_OUTPUT_DIR = Path("outputs/runtime/diagnostics")
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_TIMEZONE = "America/Toronto"
SAMPLE_LIMIT = 10
_TORONTO_FALLBACK = object()

PLAYER_PROP_MARKETS = frozenset(
    {
        "player_points",
        "player_rebounds",
        "player_assists",
        "player_threes",
        "player_blocks",
        "player_steals",
        "player_turnovers",
        "player_points_rebounds",
        "player_points_assists",
        "player_rebounds_assists",
        "player_points_rebounds_assists",
    }
)

USAGE_HEADER_NAMES = (
    "x-requests-remaining",
    "x-requests-used",
    "x-requests-last",
)

NORMALIZED_ROW_FIELDS = (
    "provider",
    "provider_event_id",
    "home_team",
    "away_team",
    "commence_time",
    "player_name",
    "market_type",
    "side",
    "line",
    "american_odds",
    "sportsbook",
    "updated_at",
    "eligible_for_betting",
)

VERDICT_LABELS = (
    ("api_access_works", "API access works"),
    ("provider_status", "provider_status"),
    ("events_available", "events_available"),
    ("target_date_events_count", "target_date_events_count"),
    ("probed_event_count", "probed_event_count"),
    ("player_props_available", "player props available"),
    ("bookmaker_count", "bookmaker_count"),
)


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
    value = _clean_key(os.getenv(THE_ODDS_API_KEY_ENV))
    if value:
        return value, THE_ODDS_API_KEY_ENV
    return "", "<not found>"


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.clear()
    session.headers.update({"Accept": "application/json"})
    return session


def _response_text_preview(response: requests.Response, limit: int = 180) -> str:
    try:
        text = response.text or ""
    except Exception:
        return "<unavailable>"
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] if text else "<empty>"


def _usage_headers(response: requests.Response | Any) -> dict[str, str]:
    raw_headers = getattr(response, "headers", {}) or {}
    headers_by_lower = {str(key).lower(): str(value) for key, value in dict(raw_headers).items()}
    return {
        header: headers_by_lower[header]
        for header in USAGE_HEADER_NAMES
        if header in headers_by_lower
    }


def _get_json(
    session: requests.Session,
    path: str,
    params: dict[str, Any],
    timeout: int,
) -> tuple[int, Any, dict[str, str], str | None]:
    url = f"{THE_ODDS_API_BASE_URL}/{path.lstrip('/')}"
    try:
        response = session.get(url, params=params, timeout=timeout)
    except requests.exceptions.Timeout:
        return -1, None, {}, "timeout"
    except requests.RequestException as exc:
        return -1, None, {}, f"request_error: {exc}"
    except Exception as exc:
        return -1, None, {}, f"unexpected_error: {exc}"

    status = int(getattr(response, "status_code", -1) or -1)
    headers = _usage_headers(response)
    try:
        parsed = response.json()
    except ValueError as exc:
        if status == 200:
            return status, None, headers, f"json_parse_error: {exc}"
        return status, None, headers, _response_text_preview(response)
    except Exception as exc:
        return status, None, headers, f"json_parse_error: {exc}"

    return status, parsed, headers, None if status == 200 else _response_text_preview(response)


def _provider_status(http_status: int, body: Any = None, error: str | None = None) -> str:
    if http_status == 200:
        if error and "json_parse_error" in error:
            return "malformed_response"
        if body is None:
            return "empty_response"
        return "ok"

    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        408: "timeout",
        422: "unprocessable_entity",
        429: "rate_limited",
        500: "server_error",
        502: "bad_gateway",
        503: "unavailable",
        504: "gateway_timeout",
        -1: "connection_error",
    }
    return mapping.get(http_status, f"http_{http_status}")


def _csv_items(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_text(*values: Any) -> str:
    for value in values:
        if value not in (None, "", [], {}):
            return str(value).strip()
    return ""


def _event_rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [item for item in body if isinstance(item, dict)]
    if isinstance(body, dict):
        data = body.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def _normalize_timezone_name(value: str | None) -> str:
    return str(value or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE


def _resolve_timezone(timezone_name: str) -> Any:
    normalized = _normalize_timezone_name(timezone_name)
    if normalized in {"UTC", "Etc/UTC", "Z"}:
        return timezone.utc
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        if normalized == DEFAULT_TIMEZONE:
            return _TORONTO_FALLBACK
        raise ValueError(
            f"Time zone data for {normalized!r} is unavailable. "
            "Install tzdata or use UTC/America/Toronto."
        ) from exc


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first_day = date(year, month, 1)
    offset_days = (weekday - first_day.weekday()) % 7
    return first_day + timedelta(days=offset_days + (occurrence - 1) * 7)


def _toronto_dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
    dst_start_day = _nth_weekday(year, 3, 6, 2)
    dst_end_day = _nth_weekday(year, 11, 6, 1)
    dst_start_utc = datetime.combine(dst_start_day, time(7, 0), tzinfo=timezone.utc)
    dst_end_utc = datetime.combine(dst_end_day, time(6, 0), tzinfo=timezone.utc)
    return dst_start_utc, dst_end_utc


def _toronto_utc_offset_hours_for_utc(utc_dt: datetime) -> int:
    dst_start_utc, dst_end_utc = _toronto_dst_bounds_utc(utc_dt.year)
    return -4 if dst_start_utc <= utc_dt < dst_end_utc else -5


def _toronto_utc_offset_hours_for_local(local_dt: datetime) -> int:
    dst_start_day = _nth_weekday(local_dt.year, 3, 6, 2)
    dst_end_day = _nth_weekday(local_dt.year, 11, 6, 1)
    dst_start_local = datetime.combine(dst_start_day, time(2, 0))
    dst_end_local = datetime.combine(dst_end_day, time(2, 0))
    return -4 if dst_start_local <= local_dt < dst_end_local else -5


def _toronto_fixed_timezone(offset_hours: int) -> timezone:
    label = "EDT" if offset_hours == -4 else "EST"
    return timezone(timedelta(hours=offset_hours), label)


def _parse_commence_time_utc(value: Any) -> datetime | None:
    text = _first_text(value)
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _convert_utc_to_local(value: datetime, timezone_info: Any) -> datetime:
    utc_value = value.astimezone(timezone.utc)
    if timezone_info is _TORONTO_FALLBACK:
        offset_hours = _toronto_utc_offset_hours_for_utc(utc_value)
        return utc_value.astimezone(_toronto_fixed_timezone(offset_hours))
    return utc_value.astimezone(timezone_info)


def _local_midnight_to_utc(target_day: date, timezone_info: Any) -> datetime:
    local_midnight = datetime.combine(target_day, time.min)
    if timezone_info is _TORONTO_FALLBACK:
        offset_hours = _toronto_utc_offset_hours_for_local(local_midnight)
        local_midnight = local_midnight.replace(tzinfo=_toronto_fixed_timezone(offset_hours))
    else:
        local_midnight = local_midnight.replace(tzinfo=timezone_info)
    return local_midnight.astimezone(timezone.utc)


def _event_debug_diagnostic(
    event: dict[str, Any],
    target_date: str,
    timezone_info: Any,
) -> dict[str, Any]:
    commence_utc = _parse_commence_time_utc(event.get("commence_time"))
    diagnostic = {
        "event_id": _first_text(event.get("id")),
        "home_team": _first_text(event.get("home_team")),
        "away_team": _first_text(event.get("away_team")),
        "commence_time_utc": "",
        "commence_date_utc": "",
        "commence_time_local": "",
        "commence_date_local": "",
        "target_date_match_utc": False,
        "target_date_match_local": False,
    }
    if commence_utc is None:
        return diagnostic

    commence_local = _convert_utc_to_local(commence_utc, timezone_info)
    commence_date_utc = commence_utc.date().isoformat()
    commence_date_local = commence_local.date().isoformat()
    target_date_text = str(target_date)
    diagnostic.update(
        {
            "commence_time_utc": _format_utc(commence_utc),
            "commence_date_utc": commence_date_utc,
            "commence_time_local": commence_local.isoformat(),
            "commence_date_local": commence_date_local,
            "target_date_match_utc": commence_date_utc == target_date_text,
            "target_date_match_local": commence_date_local == target_date_text,
        }
    )
    return diagnostic


def _event_debug_diagnostics(
    events: list[dict[str, Any]],
    target_date: str,
    timezone_info: Any,
) -> list[dict[str, Any]]:
    return [_event_debug_diagnostic(event, target_date, timezone_info) for event in events]


def filter_events_by_date(
    events: list[dict[str, Any]],
    target_date: str,
    timezone_name: str = DEFAULT_TIMEZONE,
) -> list[dict[str, Any]]:
    timezone_info = _resolve_timezone(timezone_name)
    diagnostics = _event_debug_diagnostics(events, target_date, timezone_info)
    return [
        event
        for event, diagnostic in zip(events, diagnostics, strict=True)
        if diagnostic["target_date_match_local"]
    ]


def _build_commence_window_params(target_date: str, timezone_name: str = DEFAULT_TIMEZONE) -> dict[str, str]:
    timezone_info = _resolve_timezone(timezone_name)
    target_day = date.fromisoformat(str(target_date))
    start_utc = _local_midnight_to_utc(target_day, timezone_info)
    end_utc = _local_midnight_to_utc(target_day + timedelta(days=1), timezone_info)
    return {
        "commenceTimeFrom": _format_utc(start_utc),
        "commenceTimeTo": _format_utc(end_utc),
    }


def _side_from_outcome_name(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "over":
        return "over"
    if text == "under":
        return "under"
    return ""


def _event_identity(event: dict[str, Any], odds_body: dict[str, Any]) -> dict[str, str]:
    return {
        "provider_event_id": _first_text(odds_body.get("id"), event.get("id")),
        "home_team": _first_text(odds_body.get("home_team"), event.get("home_team")),
        "away_team": _first_text(odds_body.get("away_team"), event.get("away_team")),
        "commence_time": _first_text(odds_body.get("commence_time"), event.get("commence_time")),
    }


def _bookmaker_key(bookmaker: dict[str, Any]) -> str:
    return _first_text(bookmaker.get("key"), bookmaker.get("title"))


def _bookmaker_title(bookmaker: dict[str, Any]) -> str:
    return _first_text(bookmaker.get("title"), bookmaker.get("key"))


def _extract_market_rows(
    *,
    event: dict[str, Any],
    odds_body: dict[str, Any],
    requested_markets: set[str],
    sample_limit: int,
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]], bool]:
    normalized_rows: list[dict[str, Any]] = []
    market_keys_seen: list[str] = []
    sample_bookmakers: list[dict[str, str]] = []
    player_props_available = False
    seen_bookmakers: set[str] = set()

    identity = _event_identity(event, odds_body)
    bookmakers = odds_body.get("bookmakers") if isinstance(odds_body, dict) else None
    if not isinstance(bookmakers, list):
        return normalized_rows, market_keys_seen, sample_bookmakers, player_props_available

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue

        bookmaker_key = _bookmaker_key(bookmaker)
        bookmaker_title = _bookmaker_title(bookmaker)
        if bookmaker_key and bookmaker_key not in seen_bookmakers:
            sample_bookmakers.append({"key": bookmaker_key, "title": bookmaker_title})
            seen_bookmakers.add(bookmaker_key)

        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            continue

        for market in markets:
            if not isinstance(market, dict):
                continue
            market_key = _first_text(market.get("key"))
            if not market_key:
                continue
            if market_key not in market_keys_seen:
                market_keys_seen.append(market_key)
            outcomes = market.get("outcomes")
            has_outcomes = isinstance(outcomes, list) and bool(outcomes)
            if market_key in PLAYER_PROP_MARKETS and has_outcomes:
                player_props_available = True
            if requested_markets and market_key not in requested_markets:
                continue
            if market_key not in PLAYER_PROP_MARKETS or not has_outcomes:
                continue

            updated_at = _first_text(market.get("last_update"), bookmaker.get("last_update"))
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                side = _side_from_outcome_name(outcome.get("name"))
                if not side:
                    continue
                player_name = _first_text(
                    outcome.get("description"),
                    outcome.get("player_name"),
                    outcome.get("player"),
                )
                row = {
                    "provider": "the_odds_api",
                    "provider_event_id": identity["provider_event_id"],
                    "home_team": identity["home_team"],
                    "away_team": identity["away_team"],
                    "commence_time": identity["commence_time"],
                    "player_name": player_name,
                    "market_type": market_key,
                    "side": side,
                    "line": _safe_float(outcome.get("point")),
                    "american_odds": _safe_int(outcome.get("price")),
                    "sportsbook": bookmaker_title,
                    "updated_at": updated_at,
                    "eligible_for_betting": False,
                }
                if sample_limit <= 0:
                    continue
                normalized_rows.append({field: row.get(field) for field in NORMALIZED_ROW_FIELDS})
                if len(normalized_rows) >= sample_limit:
                    return normalized_rows, market_keys_seen, sample_bookmakers, player_props_available

    return normalized_rows, market_keys_seen, sample_bookmakers, player_props_available


def _merge_ordered(existing: list[Any], new_values: list[Any], *, limit: int | None = None) -> list[Any]:
    merged = list(existing)
    for value in new_values:
        if value not in merged:
            merged.append(value)
        if limit is not None and len(merged) >= limit:
            return merged[:limit]
    return merged


def _merge_bookmakers(existing: list[dict[str, str]], new_values: list[dict[str, str]], limit: int = 5) -> list[dict[str, str]]:
    merged = list(existing)
    seen = {_bookmaker_key(item) for item in merged}
    for item in new_values:
        key = _bookmaker_key(item)
        if key and key not in seen:
            merged.append(item)
            seen.add(key)
        if len(merged) >= limit:
            return merged[:limit]
    return merged


def _build_courtvision_verdict(
    *,
    api_access_works: bool,
    provider_status: str,
    target_date_events_count: int,
    probed_event_count: int,
    player_props_available: bool,
) -> dict[str, Any]:
    if not api_access_works:
        reason = f"The Odds API access failed with provider_status={provider_status}."
        looks_usable = False
    elif target_date_events_count <= 0:
        reason = "The Odds API is reachable, but no target-date NBA events were available to probe."
        looks_usable = False
    elif probed_event_count <= 0:
        reason = "Target-date events were found, but no event odds endpoint was probed."
        looks_usable = False
    elif player_props_available:
        reason = "The Odds API returned NBA player-prop markets for a target-date event."
        looks_usable = True
    else:
        reason = "The event odds endpoint worked, but requested player-prop markets were not seen."
        looks_usable = False

    return {
        "looks_usable_for_courtvision": looks_usable,
        "betting_mode_integrated": False,
        "reason": reason,
    }


def _missing_key_payload(
    *,
    target_date: str,
    timezone_name: str,
    sport: str,
    regions: str,
    odds_format: str,
    markets: str,
    max_events: int,
    use_commence_window: bool,
    commence_window_utc: dict[str, str],
) -> dict[str, Any]:
    provider_status = "missing_credentials"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "timezone": timezone_name,
        "sport": sport,
        "regions": regions,
        "odds_format": odds_format,
        "requested_markets": _csv_items(markets),
        "max_events": max_events,
        "use_commence_window": use_commence_window,
        "commence_window_utc": commence_window_utc,
        "api_access_works": False,
        "provider_status": provider_status,
        "events_available": 0,
        "target_date_events_count": 0,
        "probed_event_count": 0,
        "player_props_available": False,
        "bookmaker_count": 0,
        "market_keys_seen": [],
        "sample_bookmakers": [],
        "sample_normalized_rows": [],
        "event_debug_diagnostics": [],
        "usage_headers": {"events": {}, "event_odds": []},
        "courtvision_verdict": _build_courtvision_verdict(
            api_access_works=False,
            provider_status=provider_status,
            target_date_events_count=0,
            probed_event_count=0,
            player_props_available=False,
        ),
    }


def run_smoke_test(
    api_key: str,
    target_date: str,
    *,
    sport: str = DEFAULT_SPORT,
    regions: str = DEFAULT_REGIONS,
    odds_format: str = DEFAULT_ODDS_FORMAT,
    markets: str = DEFAULT_MARKETS_TEXT,
    max_events: int = 1,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    timezone_name: str = DEFAULT_TIMEZONE,
    use_commence_window: bool = False,
) -> dict[str, Any]:
    api_key = _clean_key(api_key)
    target_date = str(target_date)
    timezone_name = _normalize_timezone_name(timezone_name)
    timezone_info = _resolve_timezone(timezone_name)
    market_list = _csv_items(markets)
    requested_markets = set(market_list)
    max_events = max(0, int(max_events))
    commence_window_utc = (
        _build_commence_window_params(target_date, timezone_name)
        if use_commence_window
        else {}
    )

    if not api_key:
        return _missing_key_payload(
            target_date=target_date,
            timezone_name=timezone_name,
            sport=sport,
            regions=regions,
            odds_format=odds_format,
            markets=markets,
            max_events=max_events,
            use_commence_window=use_commence_window,
            commence_window_utc=commence_window_utc,
        )

    session = _build_session()
    events_params = {"apiKey": api_key, "dateFormat": "iso"}
    if use_commence_window:
        events_params.update(commence_window_utc)
    events_status, events_body, events_usage, events_error = _get_json(
        session,
        f"/v4/sports/{sport}/events",
        events_params,
        max(1, int(timeout)),
    )
    events_provider_status = _provider_status(events_status, events_body, events_error)
    events = _event_rows(events_body) if events_provider_status == "ok" else []
    event_debug_diagnostics = _event_debug_diagnostics(events, target_date, timezone_info)
    target_events = [
        event
        for event, diagnostic in zip(events, event_debug_diagnostics, strict=True)
        if diagnostic["target_date_match_local"]
    ]

    odds_provider_statuses: list[str] = []
    usage_event_odds: list[dict[str, Any]] = []
    bookmaker_keys: set[str] = set()
    market_keys_seen: list[str] = []
    sample_bookmakers: list[dict[str, str]] = []
    sample_normalized_rows: list[dict[str, Any]] = []
    player_props_available = False
    probed_event_count = 0

    for event in target_events[:max_events]:
        event_id = _first_text(event.get("id"))
        if not event_id:
            continue

        odds_params = {
            "apiKey": api_key,
            "regions": regions,
            "markets": ",".join(market_list),
            "oddsFormat": odds_format,
            "dateFormat": "iso",
        }
        odds_status, odds_body, odds_usage, odds_error = _get_json(
            session,
            f"/v4/sports/{sport}/events/{event_id}/odds",
            odds_params,
            max(1, int(timeout)),
        )
        probed_event_count += 1
        odds_provider_status = _provider_status(odds_status, odds_body, odds_error)
        odds_provider_statuses.append(odds_provider_status)
        usage_event_odds.append({"event_id": event_id, "headers": odds_usage})

        if odds_provider_status != "ok" or not isinstance(odds_body, dict):
            continue

        rows, markets_seen, bookmakers, has_player_props = _extract_market_rows(
            event=event,
            odds_body=odds_body,
            requested_markets=requested_markets,
            sample_limit=max(0, SAMPLE_LIMIT - len(sample_normalized_rows)),
        )
        player_props_available = player_props_available or has_player_props
        market_keys_seen = _merge_ordered(market_keys_seen, markets_seen)
        sample_bookmakers = _merge_bookmakers(sample_bookmakers, bookmakers)
        sample_normalized_rows.extend(rows)

        bookmakers_body = odds_body.get("bookmakers")
        if isinstance(bookmakers_body, list):
            for bookmaker in bookmakers_body:
                if isinstance(bookmaker, dict):
                    key = _bookmaker_key(bookmaker)
                    if key:
                        bookmaker_keys.add(key)

    provider_status = events_provider_status
    if events_provider_status == "ok" and odds_provider_statuses:
        provider_status = "ok" if "ok" in odds_provider_statuses else odds_provider_statuses[0]

    api_access_works = events_provider_status == "ok"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "timezone": timezone_name,
        "sport": sport,
        "regions": regions,
        "odds_format": odds_format,
        "requested_markets": market_list,
        "max_events": max_events,
        "use_commence_window": use_commence_window,
        "commence_window_utc": commence_window_utc,
        "api_access_works": api_access_works,
        "provider_status": provider_status,
        "events_available": len(events),
        "target_date_events_count": len(target_events),
        "probed_event_count": probed_event_count,
        "player_props_available": player_props_available,
        "bookmaker_count": len(bookmaker_keys),
        "market_keys_seen": market_keys_seen,
        "sample_bookmakers": sample_bookmakers,
        "sample_normalized_rows": sample_normalized_rows[:SAMPLE_LIMIT],
        "event_debug_diagnostics": event_debug_diagnostics,
        "usage_headers": {"events": events_usage, "event_odds": usage_event_odds},
        "courtvision_verdict": _build_courtvision_verdict(
            api_access_works=api_access_works,
            provider_status=provider_status,
            target_date_events_count=len(target_events),
            probed_event_count=probed_event_count,
            player_props_available=player_props_available,
        ),
    }
    if events_error:
        payload["events_error"] = str(events_error)[:180]
    return payload


def _bool_label(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _print_verdict(payload: dict[str, Any]) -> None:
    print("The Odds API NBA player-props smoke verdict")
    for key, label in VERDICT_LABELS:
        value = payload.get(key)
        if isinstance(value, bool):
            value = _bool_label(value)
        print(f"{label}: {value}")
    courtvision_verdict = payload.get("courtvision_verdict")
    if isinstance(courtvision_verdict, dict):
        print(f"CourtVision usable: {_bool_label(courtvision_verdict.get('looks_usable_for_courtvision'))}")
        print("Betting Mode integrated: no")
        print(f"reason: {courtvision_verdict.get('reason', '')}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test The Odds API NBA player props.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Target date in YYYY-MM-DD format.")
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help="IANA timezone used to compare event commence_time against the CourtVision sports date.",
    )
    parser.add_argument("--sport", default=DEFAULT_SPORT, help="The Odds API sport key.")
    parser.add_argument("--regions", default=DEFAULT_REGIONS, help="Comma-separated sportsbook regions.")
    parser.add_argument("--odds-format", default=DEFAULT_ODDS_FORMAT, help="Odds format, for example american.")
    parser.add_argument(
        "--markets",
        default=DEFAULT_MARKETS_TEXT,
        help="Comma-separated player-prop market keys to probe.",
    )
    parser.add_argument("--max-events", type=int, default=1, help="Maximum target-date events to probe.")
    parser.add_argument(
        "--use-commence-window",
        action="store_true",
        help="Send commenceTimeFrom/commenceTimeTo for the local target date converted to UTC.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Defaults to outputs/runtime/diagnostics/the_odds_api_smoke_YYYY-MM-DD.json.",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS, help="HTTP timeout in seconds.")
    return parser.parse_args(argv)


def _output_path(value: str | None, target_date: str) -> Path:
    if value:
        return Path(value)
    return DEFAULT_OUTPUT_DIR / f"the_odds_api_smoke_{target_date}.json"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    api_key, key_source = resolve_api_key()
    print(f"The Odds API key={_mask_key(api_key)} source={key_source}")
    if not api_key:
        print(f"Set {THE_ODDS_API_KEY_ENV} before running the smoke test.")

    payload = run_smoke_test(
        api_key=api_key,
        target_date=str(args.date),
        sport=str(args.sport),
        regions=str(args.regions),
        odds_format=str(args.odds_format),
        markets=str(args.markets),
        max_events=max(0, int(args.max_events)),
        timeout=max(1, int(args.timeout)),
        timezone_name=str(args.timezone),
        use_commence_window=bool(args.use_commence_window),
    )

    output_path = _output_path(args.output, str(args.date))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"diagnostic output: {output_path}")
    _print_verdict(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
