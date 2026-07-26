"""Versioned CourtVision publication identity generation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping

from courtvision.lifecycle.canonical import deterministic_id


IDENTITY_SCHEMA_VERSION = 1

UNKNOWN_IDENTITY_SENTINELS = frozenset(
    {
        "-",
        "<na>",
        "missing",
        "n/a",
        "na",
        "nan",
        "none",
        "not applicable",
        "not_available",
        "null",
        "tbd",
        "unk",
        "unknown",
        "unresolved",
    }
)

_KNOWN_BOOKMAKERS = {
    "bet365": "bet365",
    "betmgm": "betmgm",
    "betrivers": "betrivers",
    "caesars": "caesars",
    "caesarssportsbook": "caesars",
    "draftkings": "draftkings",
    "espnbet": "espnbet",
    "fanduel": "fanduel",
    "fanatics": "fanatics",
    "pinnacle": "pinnacle",
    "pointsbet": "pointsbet",
}

_KNOWN_MARKETS = {
    "moneyline",
    "player_3pt_made",
    "player_assists",
    "player_blocks",
    "player_points",
    "player_points_assists",
    "player_points_rebounds",
    "player_points_rebounds_assists",
    "player_rebounds",
    "player_rebounds_assists",
    "player_steals",
    "player_home_runs",
    "team_total",
}

_MARKET_ALIASES = {
    "assists": "player_assists",
    "blocks": "player_blocks",
    "points": "player_points",
    "points_assists": "player_points_assists",
    "points_rebounds": "player_points_rebounds",
    "points_rebounds_assists": "player_points_rebounds_assists",
    "rebounds": "player_rebounds",
    "rebounds_assists": "player_rebounds_assists",
    "steals": "player_steals",
    "threes": "player_3pt_made",
    "three_pointers": "player_3pt_made",
    "batter_home_runs": "player_home_runs",
    "batter_home_runs_alternate": "player_home_runs",
    "home_runs": "player_home_runs",
    "player_hr": "player_home_runs",
}


class IdentityError(ValueError):
    """Raised when required deterministic identity inputs are invalid."""


@dataclass(frozen=True, slots=True)
class PublicationIdentity:
    identity_schema_version: int
    canonical_event_id: str | None
    canonical_participant_id: str | None
    canonical_market_id: str | None
    canonical_bookmaker_id: str | None
    selection: str | None
    canonical_line: str | None
    market_subject_key: str | None
    prediction_key: str | None
    prediction_id: str | None
    resolution_status: str
    unresolved_fields: tuple[str, ...]


def _clean_identity_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.casefold() in UNKNOWN_IDENTITY_SENTINELS:
        return None
    return text


def _clean_id(value: Any) -> str | None:
    text = _clean_identity_value(value)
    if text is None:
        return None
    try:
        numeric = Decimal(text)
    except InvalidOperation:
        return text
    if numeric.is_finite() and numeric == numeric.to_integral_value():
        return str(int(numeric))
    return text


def _canonical_domain_id(
    value: Any,
    *,
    sport: str,
    league: str,
    domain: str,
) -> str | None:
    clean = _clean_id(value)
    if clean is None:
        return None
    prefix = f"courtvision:{sport.lower()}:{league.lower()}:{domain}:"
    if clean.casefold().startswith("courtvision:"):
        if not clean.startswith(prefix):
            return None
        identifier = clean[len(prefix) :]
        if (
            _clean_identity_value(identifier) is None
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", identifier)
            is None
        ):
            return None
        return clean
    return f"{prefix}{clean}"


def canonical_event_id(value: Any, *, sport: str, league: str) -> str | None:
    return _canonical_domain_id(
        value,
        sport=sport,
        league=league,
        domain="event",
    )


def canonical_participant_id(value: Any, *, sport: str, league: str) -> str | None:
    return _canonical_domain_id(
        value,
        sport=sport,
        league=league,
        domain="participant",
    )


def canonical_team_id(value: Any, *, sport: str, league: str) -> str | None:
    return _canonical_domain_id(
        value,
        sport=sport,
        league=league,
        domain="team",
    )


def normalize_market_id(value: Any, *, sport: str, league: str) -> str | None:
    clean = _clean_identity_value(value)
    if clean is None:
        return None
    text = re.sub(r"[^a-z0-9]+", "_", clean.lower()).strip("_")
    text = _MARKET_ALIASES.get(text, text)
    if text not in _KNOWN_MARKETS:
        return None
    return f"courtvision:{sport.lower()}:{league.lower()}:market:{text}"


def normalize_bookmaker_id(value: Any) -> str | None:
    clean = _clean_identity_value(value)
    if clean is None:
        return None
    raw = clean.lower()
    if raw.startswith("courtvision:bookmaker:"):
        raw = raw.rsplit(":", 1)[-1]
    normalized = re.sub(r"[^a-z0-9]+", "", raw)
    bookmaker = _KNOWN_BOOKMAKERS.get(normalized)
    return f"courtvision:bookmaker:{bookmaker}" if bookmaker else None


def normalize_selection(value: Any) -> str | None:
    clean = _clean_identity_value(value)
    if clean is None:
        return None
    normalized = clean.upper()
    if normalized in {"OVER", "UNDER", "YES", "NO", "HOME", "AWAY"}:
        return normalized
    return None


def normalize_line(value: Any) -> str | None:
    text = _clean_identity_value(value)
    if text is None:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise IdentityError(f"line is not numeric: {value!r}") from exc
    if not number.is_finite():
        raise IdentityError("line must be finite")
    normalized = number.normalize()
    if normalized == Decimal("-0"):
        normalized = Decimal("0")
    result = format(normalized, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return result or "0"


def _identity_payload(
    *,
    sport: str,
    league: str,
    canonical_event: str,
    canonical_participant: str,
    canonical_market: str,
    selection: str,
    canonical_bookmaker: str,
    canonical_line: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "sport": sport.upper(),
        "league": league.upper(),
        "canonical_event_id": canonical_event,
        "canonical_participant_id": canonical_participant,
        "canonical_market_id": canonical_market,
        "selection": selection,
        "canonical_bookmaker_id": canonical_bookmaker,
    }
    if canonical_line is not None:
        payload["line"] = canonical_line
    return payload


def derive_publication_identity(
    *,
    sport: str,
    league: str,
    event_id: Any,
    participant_id: Any,
    market_id: Any,
    selection: Any,
    line: Any,
    bookmaker: Any,
    prediction_run_id: str,
) -> PublicationIdentity:
    event = canonical_event_id(event_id, sport=sport, league=league)
    participant = canonical_participant_id(participant_id, sport=sport, league=league)
    market = normalize_market_id(market_id, sport=sport, league=league)
    side = normalize_selection(selection)
    canonical_line = normalize_line(line)
    book = normalize_bookmaker_id(bookmaker)
    values = {
        "canonical_event_id": event,
        "canonical_participant_id": participant,
        "canonical_market_id": market,
        "selection": side,
        "line": canonical_line,
        "canonical_bookmaker_id": book,
    }
    unresolved = tuple(name for name, value in values.items() if value is None)
    if unresolved:
        return PublicationIdentity(
            identity_schema_version=IDENTITY_SCHEMA_VERSION,
            canonical_event_id=event,
            canonical_participant_id=participant,
            canonical_market_id=market,
            canonical_bookmaker_id=book,
            selection=side,
            canonical_line=canonical_line,
            market_subject_key=None,
            prediction_key=None,
            prediction_id=None,
            resolution_status="UNRESOLVED",
            unresolved_fields=unresolved,
        )

    assert event and participant and market and side and canonical_line is not None and book
    subject_payload = _identity_payload(
        sport=sport,
        league=league,
        canonical_event=event,
        canonical_participant=participant,
        canonical_market=market,
        selection=side,
        canonical_bookmaker=book,
    )
    prediction_payload = _identity_payload(
        sport=sport,
        league=league,
        canonical_event=event,
        canonical_participant=participant,
        canonical_market=market,
        selection=side,
        canonical_bookmaker=book,
        canonical_line=canonical_line,
    )
    market_subject_key = deterministic_id(
        "msk", "courtvision.market_subject.v1", subject_payload
    )
    prediction_key = deterministic_id(
        "pkey", "courtvision.prediction_key.v1", prediction_payload
    )
    prediction_id = deterministic_id(
        "pred",
        "courtvision.prediction_id.v1",
        {
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "prediction_key": prediction_key,
            "prediction_run_id": str(prediction_run_id),
        },
    )
    return PublicationIdentity(
        identity_schema_version=IDENTITY_SCHEMA_VERSION,
        canonical_event_id=event,
        canonical_participant_id=participant,
        canonical_market_id=market,
        canonical_bookmaker_id=book,
        selection=side,
        canonical_line=canonical_line,
        market_subject_key=market_subject_key,
        prediction_key=prediction_key,
        prediction_id=prediction_id,
        resolution_status="RESOLVED",
        unresolved_fields=(),
    )


def identity_inputs_from_board_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": (
            row.get("canonical_event_id")
            or row.get("game_id")
            or row.get("event_id")
        ),
        "participant_id": (
            row.get("canonical_player_id")
            or row.get("canonical_participant_id")
            or row.get("player_id")
            or row.get("entity_id")
            or row.get("normalized_player_name")
        ),
        "market_id": (
            row.get("canonical_market_id")
            or row.get("market_type")
            or row.get("market")
            or row.get("prop_type")
            or row.get("market_key")
        ),
        "selection": row.get("selection") or row.get("side"),
        "line": (
            row.get("sportsbook_line")
            or row.get("line")
            or row.get("point")
        ),
        "bookmaker": (
            row.get("canonical_bookmaker_id")
            or row.get("bookmaker")
            or row.get("sportsbook")
            or row.get("vendor")
        ),
    }


__all__ = [
    "IDENTITY_SCHEMA_VERSION",
    "UNKNOWN_IDENTITY_SENTINELS",
    "IdentityError",
    "PublicationIdentity",
    "canonical_event_id",
    "canonical_participant_id",
    "canonical_team_id",
    "derive_publication_identity",
    "identity_inputs_from_board_row",
    "normalize_bookmaker_id",
    "normalize_line",
    "normalize_market_id",
    "normalize_selection",
]
