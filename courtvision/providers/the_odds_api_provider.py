"""Research-safe The Odds API player-props provider.

This adapter intentionally stops at provider-neutral DataFrames. It does not
create MarketProp rows, call Kelly, produce Elite rows, or write operator
betting artifacts.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import requests


THE_ODDS_API_BASE_URL = "https://api.the-odds-api.com"
THE_ODDS_API_KEY_ENV = "THE_ODDS_API_KEY"
DEFAULT_SPORT = "basketball_nba"
DEFAULT_REGIONS = "us"
DEFAULT_ODDS_FORMAT = "american"
DEFAULT_TIMEZONE = "America/Toronto"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_RUNTIME_ROOT = Path("outputs/runtime")
PROVIDER_NAME = "the_odds_api"
PROVIDER_STATUS_OK = "ok"
PROVIDER_STATUS_MISSING_CREDENTIALS = "missing_credentials"
PROVIDER_STATUS_CACHED_FALLBACK = "cached_fallback"
_TORONTO_FALLBACK = object()

USAGE_HEADER_NAMES = (
    "x-requests-remaining",
    "x-requests-used",
    "x-requests-last",
)

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

NORMALIZED_COLUMNS = (
    "provider",
    "provider_event_id",
    "home_team",
    "away_team",
    "commence_time_utc",
    "commence_time_local",
    "game_date",
    "player_name",
    "market_type",
    "side",
    "line",
    "american_odds",
    "sportsbook",
    "updated_at",
    "source",
    "eligible_for_betting",
)


@dataclass(slots=True)
class _RequestResult:
    provider_status: str
    body: Any
    usage_headers: dict[str, str]
    error: str | None = None
    cache_status: str = "miss"
    cache_path: Path | None = None


@dataclass(slots=True)
class _EventsResult:
    all_events: list[dict[str, Any]]
    target_events: list[dict[str, Any]]
    request: _RequestResult
    event_debug_diagnostics: list[dict[str, Any]]
    warnings: list[str]


@dataclass(slots=True)
class _NormalizationResult:
    rows: list[dict[str, Any]]
    market_keys_seen: list[str]
    bookmaker_keys: set[str]
    warnings: list[str]


def get_events_for_date(
    target_date: str | date,
    sport: str = DEFAULT_SPORT,
    timezone: str = DEFAULT_TIMEZONE,
    *,
    api_key: str | None = None,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[dict[str, Any]]:
    """Fetch The Odds API events and keep only events matching the local date."""
    resolved_key = _resolve_api_key(api_key)
    if not resolved_key:
        return []

    session = _build_session()
    result = _fetch_events_for_date(
        target_date=_date_text(target_date),
        sport=str(sport),
        timezone_name=str(timezone),
        api_key=resolved_key,
        runtime_root=runtime_root,
        timeout=timeout,
        session=session,
    )
    return result.target_events


def get_player_props_for_event(
    event_id: str,
    markets: str | list[str] | tuple[str, ...],
    regions: str = DEFAULT_REGIONS,
    odds_format: str = DEFAULT_ODDS_FORMAT,
    *,
    sport: str = DEFAULT_SPORT,
    timezone: str = DEFAULT_TIMEZONE,
    api_key: str | None = None,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    event: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Fetch and normalize player props for one The Odds API event."""
    resolved_key = _resolve_api_key(api_key)
    if not resolved_key:
        return _empty_props_dataframe()

    session = _build_session()
    request = _fetch_event_odds(
        event_id=str(event_id),
        markets=_csv_items(markets),
        sport=str(sport),
        regions=str(regions),
        odds_format=str(odds_format),
        api_key=resolved_key,
        runtime_root=runtime_root,
        timeout=timeout,
        session=session,
    )
    if request.provider_status not in {PROVIDER_STATUS_OK, PROVIDER_STATUS_CACHED_FALLBACK}:
        return _empty_props_dataframe()
    if not isinstance(request.body, dict):
        return _empty_props_dataframe()

    normalized = _normalize_player_prop_rows(
        event=event or {},
        odds_body=request.body,
        requested_markets=set(_csv_items(markets)),
        timezone_name=str(timezone),
    )
    return _props_dataframe(normalized.rows)


def get_player_props_for_date(
    target_date: str | date,
    markets: str | list[str] | tuple[str, ...],
    max_events: int = 1,
    timezone: str = DEFAULT_TIMEZONE,
    *,
    sport: str = DEFAULT_SPORT,
    regions: str = DEFAULT_REGIONS,
    odds_format: str = DEFAULT_ODDS_FORMAT,
    api_key: str | None = None,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    write_diagnostics: bool = True,
) -> pd.DataFrame:
    """Fetch target-date player props and write isolated provider diagnostics."""
    target_date_text = _date_text(target_date)
    timezone_name = _normalize_timezone_name(timezone)
    requested_markets = _csv_items(markets)
    requested_market_set = set(requested_markets)
    warnings: list[str] = []
    usage_headers: dict[str, Any] = {"events": {}, "event_odds": []}
    resolved_key = _resolve_api_key(api_key)

    if not resolved_key:
        warnings.append(f"{THE_ODDS_API_KEY_ENV}_missing")
        df = _empty_props_dataframe()
        diagnostics = _diagnostics_payload(
            provider_status=PROVIDER_STATUS_MISSING_CREDENTIALS,
            target_date=target_date_text,
            timezone_name=timezone_name,
            sport=str(sport),
            regions=str(regions),
            odds_format=str(odds_format),
            events_available=0,
            target_date_events_count=0,
            probed_event_count=0,
            prop_row_count=0,
            bookmaker_count=0,
            requested_markets=requested_markets,
            market_keys_seen=[],
            usage_headers=usage_headers,
            eligible_for_betting_any_true=False,
            warnings=warnings,
        )
        if write_diagnostics:
            _write_diagnostics(diagnostics_path_for_date(target_date_text, runtime_root), diagnostics)
        return df

    session = _build_session()
    events_result = _fetch_events_for_date(
        target_date=target_date_text,
        sport=str(sport),
        timezone_name=timezone_name,
        api_key=resolved_key,
        runtime_root=runtime_root,
        timeout=timeout,
        session=session,
    )
    warnings.extend(events_result.warnings)
    usage_headers["events"] = events_result.request.usage_headers

    rows: list[dict[str, Any]] = []
    market_keys_seen: list[str] = []
    bookmaker_keys: set[str] = set()
    odds_provider_statuses: list[str] = []
    probed_event_count = 0

    for event_row in events_result.target_events[: max(0, int(max_events))]:
        event_id = _first_text(event_row.get("id"))
        if not event_id:
            warnings.append("target_event_missing_id")
            continue

        odds_request = _fetch_event_odds(
            event_id=event_id,
            markets=requested_markets,
            sport=str(sport),
            regions=str(regions),
            odds_format=str(odds_format),
            api_key=resolved_key,
            runtime_root=runtime_root,
            timeout=timeout,
            session=session,
        )
        probed_event_count += 1
        odds_provider_statuses.append(odds_request.provider_status)
        usage_headers["event_odds"].append(
            {"event_id": event_id, "headers": odds_request.usage_headers}
        )
        if odds_request.error:
            warnings.append(f"event_odds_error event_id={event_id}: {odds_request.error}")

        if odds_request.provider_status not in {PROVIDER_STATUS_OK, PROVIDER_STATUS_CACHED_FALLBACK}:
            continue
        if not isinstance(odds_request.body, dict):
            warnings.append(f"event_odds_body_not_object event_id={event_id}")
            continue

        normalized = _normalize_player_prop_rows(
            event=event_row,
            odds_body=odds_request.body,
            requested_markets=requested_market_set,
            timezone_name=timezone_name,
        )
        rows.extend(normalized.rows)
        market_keys_seen = _merge_ordered(market_keys_seen, normalized.market_keys_seen)
        bookmaker_keys.update(normalized.bookmaker_keys)
        warnings.extend(normalized.warnings)

    df = _props_dataframe(rows)
    missing_requested_markets = _missing_requested_markets(requested_markets, market_keys_seen)
    if missing_requested_markets:
        warnings.append(
            "missing_requested_markets: " + ", ".join(missing_requested_markets)
        )

    provider_status = events_result.request.provider_status
    if events_result.request.provider_status == PROVIDER_STATUS_OK and odds_provider_statuses:
        provider_status = (
            PROVIDER_STATUS_OK
            if PROVIDER_STATUS_OK in odds_provider_statuses
            else odds_provider_statuses[0]
        )

    diagnostics = _diagnostics_payload(
        provider_status=provider_status,
        target_date=target_date_text,
        timezone_name=timezone_name,
        sport=str(sport),
        regions=str(regions),
        odds_format=str(odds_format),
        events_available=len(events_result.all_events),
        target_date_events_count=len(events_result.target_events),
        probed_event_count=probed_event_count,
        prop_row_count=len(df.index),
        bookmaker_count=len(bookmaker_keys),
        requested_markets=requested_markets,
        market_keys_seen=market_keys_seen,
        usage_headers=usage_headers,
        eligible_for_betting_any_true=_eligible_any_true(df),
        warnings=warnings,
    )
    diagnostics["event_debug_diagnostics"] = events_result.event_debug_diagnostics
    if write_diagnostics:
        _write_diagnostics(diagnostics_path_for_date(target_date_text, runtime_root), diagnostics)
    return df


def diagnostics_path_for_date(
    target_date: str | date,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> Path:
    return Path(runtime_root) / "diagnostics" / f"the_odds_api_provider_{_date_text(target_date)}.json"


def cache_root_for_runtime(runtime_root: str | Path = DEFAULT_RUNTIME_ROOT) -> Path:
    return Path(runtime_root) / "cache" / PROVIDER_NAME


def _fetch_events_for_date(
    *,
    target_date: str,
    sport: str,
    timezone_name: str,
    api_key: str,
    runtime_root: str | Path,
    timeout: int,
    session: requests.Session,
) -> _EventsResult:
    warnings: list[str] = []
    timezone_info = _resolve_timezone(timezone_name)
    params = {"apiKey": api_key, "dateFormat": "iso"}
    request = _get_json(
        session=session,
        path=f"/v4/sports/{sport}/events",
        params=params,
        timeout=timeout,
        cache_path=_cache_path(runtime_root, "events", sport, target_date),
    )
    if request.error:
        warnings.append(f"events_error: {request.error}")

    events = (
        _event_rows(request.body)
        if request.provider_status in {PROVIDER_STATUS_OK, PROVIDER_STATUS_CACHED_FALLBACK}
        else []
    )
    event_debug_diagnostics = _event_debug_diagnostics(events, target_date, timezone_info)
    target_events = [
        event
        for event, diagnostic in zip(events, event_debug_diagnostics, strict=True)
        if diagnostic["target_date_match_local"]
    ]
    return _EventsResult(
        all_events=events,
        target_events=target_events,
        request=request,
        event_debug_diagnostics=event_debug_diagnostics,
        warnings=warnings,
    )


def _fetch_event_odds(
    *,
    event_id: str,
    markets: list[str],
    sport: str,
    regions: str,
    odds_format: str,
    api_key: str,
    runtime_root: str | Path,
    timeout: int,
    session: requests.Session,
) -> _RequestResult:
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": ",".join(markets),
        "oddsFormat": odds_format,
        "dateFormat": "iso",
    }
    return _get_json(
        session=session,
        path=f"/v4/sports/{sport}/events/{event_id}/odds",
        params=params,
        timeout=timeout,
        cache_path=_cache_path(
            runtime_root,
            "event_odds",
            sport,
            event_id,
            regions,
            odds_format,
            "-".join(markets),
        ),
    )


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.clear()
    session.headers.update({"Accept": "application/json"})
    return session


def _get_json(
    *,
    session: requests.Session,
    path: str,
    params: dict[str, Any],
    timeout: int,
    cache_path: Path,
) -> _RequestResult:
    url = f"{THE_ODDS_API_BASE_URL}/{path.lstrip('/')}"
    try:
        response = session.get(url, params=params, timeout=max(1, int(timeout)))
    except requests.exceptions.Timeout:
        return _cache_fallback(cache_path, "timeout")
    except requests.RequestException as exc:
        return _cache_fallback(cache_path, f"request_error: {exc}")
    except Exception as exc:
        return _cache_fallback(cache_path, f"unexpected_error: {exc}")

    status = int(getattr(response, "status_code", -1) or -1)
    usage_headers = _usage_headers(response)
    try:
        body = response.json()
    except ValueError as exc:
        error = f"json_parse_error: {exc}" if status == 200 else _response_text_preview(response)
        if _can_use_cache_fallback(status):
            cached = _read_cache(cache_path)
            if cached is not None:
                return _RequestResult(
                    provider_status=PROVIDER_STATUS_CACHED_FALLBACK,
                    body=cached,
                    usage_headers=usage_headers,
                    error=error,
                    cache_status="hit_after_failure",
                    cache_path=cache_path,
                )
        return _RequestResult(
            provider_status=_provider_status(status, body=None, error=error),
            body=None,
            usage_headers=usage_headers,
            error=error,
            cache_path=cache_path,
        )
    except Exception as exc:
        error = f"json_parse_error: {exc}"
        return _RequestResult(
            provider_status=_provider_status(status, body=None, error=error),
            body=None,
            usage_headers=usage_headers,
            error=error,
            cache_path=cache_path,
        )

    if status == 200:
        _write_cache(
            cache_path,
            endpoint_path=path,
            params=params,
            body=body,
            usage_headers=usage_headers,
        )
        return _RequestResult(
            provider_status=PROVIDER_STATUS_OK,
            body=body,
            usage_headers=usage_headers,
            cache_status="written",
            cache_path=cache_path,
        )

    error = _response_text_preview(response)
    if _can_use_cache_fallback(status):
        cached = _read_cache(cache_path)
        if cached is not None:
            return _RequestResult(
                provider_status=PROVIDER_STATUS_CACHED_FALLBACK,
                body=cached,
                usage_headers=usage_headers,
                error=error,
                cache_status="hit_after_failure",
                cache_path=cache_path,
            )
    return _RequestResult(
        provider_status=_provider_status(status, body=body, error=error),
        body=body,
        usage_headers=usage_headers,
        error=error,
        cache_path=cache_path,
    )


def _normalize_player_prop_rows(
    *,
    event: dict[str, Any],
    odds_body: dict[str, Any],
    requested_markets: set[str],
    timezone_name: str,
) -> _NormalizationResult:
    rows: list[dict[str, Any]] = []
    market_keys_seen: list[str] = []
    bookmaker_keys: set[str] = set()
    warnings: list[str] = []
    identity = _event_identity(event, odds_body, timezone_name)
    bookmakers = odds_body.get("bookmakers")
    if not isinstance(bookmakers, list):
        return _NormalizationResult(rows, market_keys_seen, bookmaker_keys, warnings)

    for bookmaker in bookmakers:
        if not isinstance(bookmaker, dict):
            continue

        bookmaker_key = _bookmaker_key(bookmaker)
        bookmaker_title = _bookmaker_title(bookmaker)
        if bookmaker_key:
            bookmaker_keys.add(bookmaker_key)

        markets = bookmaker.get("markets")
        if not isinstance(markets, list):
            continue

        for market in markets:
            if not isinstance(market, dict):
                continue
            market_key = _first_text(market.get("key"))
            if not market_key:
                continue
            market_keys_seen = _merge_ordered(market_keys_seen, [market_key])

            outcomes = market.get("outcomes")
            if not isinstance(outcomes, list) or not outcomes:
                continue
            if requested_markets and market_key not in requested_markets:
                continue
            if market_key not in PLAYER_PROP_MARKETS:
                continue

            updated_at = _first_text(
                market.get("last_update"),
                bookmaker.get("last_update"),
                odds_body.get("last_update"),
            )
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                side = _side_from_outcome(outcome)
                if not side:
                    continue
                rows.append(
                    {
                        "provider": PROVIDER_NAME,
                        "provider_event_id": identity["provider_event_id"],
                        "home_team": identity["home_team"],
                        "away_team": identity["away_team"],
                        "commence_time_utc": identity["commence_time_utc"],
                        "commence_time_local": identity["commence_time_local"],
                        "game_date": identity["game_date"],
                        "player_name": _player_name_from_outcome(outcome),
                        "market_type": market_key,
                        "side": side,
                        "line": _safe_float(outcome.get("point")),
                        "american_odds": _safe_int(outcome.get("price")),
                        "sportsbook": bookmaker_title,
                        "updated_at": updated_at,
                        "source": "the_odds_api:event_odds",
                        "eligible_for_betting": False,
                    }
                )

    return _NormalizationResult(rows, market_keys_seen, bookmaker_keys, warnings)


def _event_identity(
    event: dict[str, Any],
    odds_body: dict[str, Any],
    timezone_name: str,
) -> dict[str, str]:
    commence_time_raw = _first_text(odds_body.get("commence_time"), event.get("commence_time"))
    commence_utc = _parse_commence_time_utc(commence_time_raw)
    commence_time_utc = commence_time_raw
    commence_time_local = ""
    game_date = ""
    if commence_utc is not None:
        commence_local = _convert_utc_to_local(commence_utc, _resolve_timezone(timezone_name))
        commence_time_utc = _format_utc(commence_utc)
        commence_time_local = commence_local.isoformat()
        game_date = commence_local.date().isoformat()

    return {
        "provider_event_id": _first_text(odds_body.get("id"), event.get("id")),
        "home_team": _first_text(odds_body.get("home_team"), event.get("home_team")),
        "away_team": _first_text(odds_body.get("away_team"), event.get("away_team")),
        "commence_time_utc": commence_time_utc,
        "commence_time_local": commence_time_local,
        "game_date": game_date,
    }


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
    diagnostic.update(
        {
            "commence_time_utc": _format_utc(commence_utc),
            "commence_date_utc": commence_date_utc,
            "commence_time_local": commence_local.isoformat(),
            "commence_date_local": commence_date_local,
            "target_date_match_utc": commence_date_utc == str(target_date),
            "target_date_match_local": commence_date_local == str(target_date),
        }
    )
    return diagnostic


def _event_debug_diagnostics(
    events: list[dict[str, Any]],
    target_date: str,
    timezone_info: Any,
) -> list[dict[str, Any]]:
    return [_event_debug_diagnostic(event, target_date, timezone_info) for event in events]


def _diagnostics_payload(
    *,
    provider_status: str,
    target_date: str,
    timezone_name: str,
    sport: str,
    regions: str,
    odds_format: str,
    events_available: int,
    target_date_events_count: int,
    probed_event_count: int,
    prop_row_count: int,
    bookmaker_count: int,
    requested_markets: list[str],
    market_keys_seen: list[str],
    usage_headers: dict[str, Any],
    eligible_for_betting_any_true: bool,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(dt_timezone.utc).isoformat(),
        "provider_status": provider_status,
        "target_date": target_date,
        "timezone": timezone_name,
        "sport": sport,
        "regions": regions,
        "odds_format": odds_format,
        "events_available": int(events_available),
        "target_date_events_count": int(target_date_events_count),
        "probed_event_count": int(probed_event_count),
        "prop_row_count": int(prop_row_count),
        "bookmaker_count": int(bookmaker_count),
        "requested_markets": requested_markets,
        "market_keys_seen": market_keys_seen,
        "missing_requested_markets": _missing_requested_markets(
            requested_markets,
            market_keys_seen,
        ),
        "usage_headers": usage_headers,
        "eligible_for_betting_any_true": bool(eligible_for_betting_any_true),
        "warnings": warnings,
    }


def _write_diagnostics(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception as exc:
        payload.setdefault("warnings", []).append(f"diagnostics_write_failed: {exc}")


def _cache_path(runtime_root: str | Path, prefix: str, *parts: Any) -> Path:
    slug = "__".join(_slug(part) for part in parts if _slug(part))
    if len(slug) > 140:
        slug = slug[:100]
    return cache_root_for_runtime(runtime_root) / f"{prefix}_{slug}.json"


def _write_cache(
    cache_path: Path,
    *,
    endpoint_path: str,
    params: dict[str, Any],
    body: Any,
    usage_headers: dict[str, str],
) -> None:
    sanitized_params = {
        key: value for key, value in params.items() if str(key).lower() != "apikey"
    }
    payload = {
        "cached_at": datetime.now(dt_timezone.utc).isoformat(),
        "provider": PROVIDER_NAME,
        "path": endpoint_path,
        "params": sanitized_params,
        "usage_headers": usage_headers,
        "body": body,
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        return


def _read_cache(path: Path) -> Any | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(payload, dict) and "body" in payload:
        return payload["body"]
    return None


def _cache_fallback(cache_path: Path, error: str) -> _RequestResult:
    cached = _read_cache(cache_path)
    if cached is not None:
        return _RequestResult(
            provider_status=PROVIDER_STATUS_CACHED_FALLBACK,
            body=cached,
            usage_headers={},
            error=error,
            cache_status="hit_after_failure",
            cache_path=cache_path,
        )
    return _RequestResult(
        provider_status="connection_error",
        body=None,
        usage_headers={},
        error=error,
        cache_path=cache_path,
    )


def _can_use_cache_fallback(status: int) -> bool:
    return status in {-1, 408, 429, 500, 502, 503, 504} or status >= 500


def _clean_key(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in {"'", '"'} and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _resolve_api_key(provided: str | None = None) -> str:
    return _clean_key(provided) or _clean_key(os.getenv(THE_ODDS_API_KEY_ENV))


def _usage_headers(response: requests.Response | Any) -> dict[str, str]:
    raw_headers = getattr(response, "headers", {}) or {}
    headers_by_lower = {str(key).lower(): str(value) for key, value in dict(raw_headers).items()}
    return {
        header: headers_by_lower[header]
        for header in USAGE_HEADER_NAMES
        if header in headers_by_lower
    }


def _provider_status(http_status: int, body: Any = None, error: str | None = None) -> str:
    if http_status == 200:
        if error and "json_parse_error" in error:
            return "malformed_response"
        if body is None:
            return "empty_response"
        return PROVIDER_STATUS_OK

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


def _response_text_preview(response: requests.Response | Any, limit: int = 180) -> str:
    try:
        text = response.text or ""
    except Exception:
        return "<unavailable>"
    text = text.replace("\r", " ").replace("\n", " ").strip()
    return text[:limit] if text else "<empty>"


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
        return dt_timezone.utc
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
    dst_start_utc = datetime.combine(dst_start_day, time(7, 0), tzinfo=dt_timezone.utc)
    dst_end_utc = datetime.combine(dst_end_day, time(6, 0), tzinfo=dt_timezone.utc)
    return dst_start_utc, dst_end_utc


def _toronto_utc_offset_hours_for_utc(utc_dt: datetime) -> int:
    dst_start_utc, dst_end_utc = _toronto_dst_bounds_utc(utc_dt.year)
    return -4 if dst_start_utc <= utc_dt < dst_end_utc else -5


def _toronto_fixed_timezone(offset_hours: int) -> dt_timezone:
    label = "EDT" if offset_hours == -4 else "EST"
    return dt_timezone(timedelta(hours=offset_hours), label)


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
        return parsed.replace(tzinfo=dt_timezone.utc)
    return parsed.astimezone(dt_timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(dt_timezone.utc).isoformat().replace("+00:00", "Z")


def _convert_utc_to_local(value: datetime, timezone_info: Any) -> datetime:
    utc_value = value.astimezone(dt_timezone.utc)
    if timezone_info is _TORONTO_FALLBACK:
        offset_hours = _toronto_utc_offset_hours_for_utc(utc_value)
        return utc_value.astimezone(_toronto_fixed_timezone(offset_hours))
    return utc_value.astimezone(timezone_info)


def _side_from_outcome(outcome: dict[str, Any]) -> str:
    for key in ("name", "label", "description"):
        side = _side_text(outcome.get(key))
        if side:
            return side
    return ""


def _side_text(value: Any) -> str:
    text = _clean_text(value).lower()
    if text == "over":
        return "over"
    if text == "under":
        return "under"
    return ""


def _player_name_from_outcome(outcome: dict[str, Any]) -> str:
    for key in ("description", "player_name", "player"):
        text = _clean_text(outcome.get(key))
        if text and not _side_text(text):
            return _strip_side_suffix(text)

    text = _clean_text(outcome.get("name"))
    if text and not _side_text(text):
        return _strip_side_suffix(text)
    return ""


def _strip_side_suffix(value: str) -> str:
    return re.sub(r"\s+(over|under)\s*$", "", value, flags=re.IGNORECASE).strip()


def _bookmaker_key(bookmaker: dict[str, Any]) -> str:
    return _first_text(bookmaker.get("key"), bookmaker.get("title"))


def _bookmaker_title(bookmaker: dict[str, Any]) -> str:
    return _first_text(bookmaker.get("title"), bookmaker.get("key"))


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


def _csv_items(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raw_items = str(value).split(",")
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _missing_requested_markets(
    requested_markets: list[str],
    market_keys_seen: list[str],
) -> list[str]:
    seen = set(market_keys_seen)
    return [market for market in requested_markets if market not in seen]


def _merge_ordered(existing: list[Any], new_values: list[Any]) -> list[Any]:
    merged = list(existing)
    for value in new_values:
        if value not in merged:
            merged.append(value)
    return merged


def _eligible_any_true(df: pd.DataFrame) -> bool:
    if df.empty or "eligible_for_betting" not in df.columns:
        return False
    return bool(df["eligible_for_betting"].fillna(False).astype(bool).any())


def _props_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_props_dataframe()
    return pd.DataFrame(rows, columns=NORMALIZED_COLUMNS)


def _empty_props_dataframe() -> pd.DataFrame:
    return pd.DataFrame(columns=NORMALIZED_COLUMNS)


def _date_text(value: str | date) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _first_text(*values: Any) -> str:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _slug(value: Any) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"[^a-z0-9_.-]+", "_", text)
    return text.strip("_.-")


__all__ = [
    "DEFAULT_ODDS_FORMAT",
    "DEFAULT_REGIONS",
    "DEFAULT_RUNTIME_ROOT",
    "DEFAULT_SPORT",
    "DEFAULT_TIMEZONE",
    "NORMALIZED_COLUMNS",
    "PLAYER_PROP_MARKETS",
    "PROVIDER_NAME",
    "THE_ODDS_API_KEY_ENV",
    "cache_root_for_runtime",
    "diagnostics_path_for_date",
    "get_events_for_date",
    "get_player_props_for_date",
    "get_player_props_for_event",
]
