"""Immutable, sport-agnostic normalized odds quote contract.

This module defines data boundaries and deterministic conversion helpers only.
It does not select providers, approve markets, size wagers, or route runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
import math
import re
from typing import Final


DEFAULT_MAX_QUOTE_AGE: Final = timedelta(minutes=5)
_VALID_MODES: Final = frozenset({"production", "research", "sample"})
_VALID_SOURCE_TYPES: Final = frozenset(
    {"live", "sample", "manual", "mock", "historical"}
)
_ALWAYS_INELIGIBLE_SOURCE_TYPES: Final = frozenset(
    {"sample", "mock", "historical"}
)
_MARKET_SEPARATOR_RE: Final = re.compile(r"[^a-z0-9]+")


class OddsFreshnessStatus(str, Enum):
    """Diagnostic states underlying the boolean freshness gate."""

    FRESH = "fresh"
    STALE = "stale"
    MISSING_TIMESTAMP = "missing_timestamp"
    FUTURE_TIMESTAMP = "future_timestamp"


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def validate_american_odds(american_odds: int | str) -> int:
    """Return a canonical integer American price or raise ``ValueError``.

    Valid American prices are integers at least +100 or at most -100. Boolean,
    decimal, empty, and zero values are rejected explicitly.
    """

    if isinstance(american_odds, bool):
        raise ValueError("American odds must be an integer, not a boolean")
    if isinstance(american_odds, str):
        value = american_odds.strip()
        if not re.fullmatch(r"[+-]?\d+", value):
            raise ValueError(f"Invalid American odds: {american_odds!r}")
        normalized = int(value)
    elif isinstance(american_odds, int):
        normalized = american_odds
    else:
        raise ValueError(f"Invalid American odds: {american_odds!r}")

    if normalized == 0:
        raise ValueError("American odds cannot be zero")
    if -100 < normalized < 100:
        raise ValueError(
            "American odds must be at least +100 or at most -100"
        )
    return normalized


def american_to_decimal(american_odds: int | str) -> float:
    """Convert a valid American price to decimal odds."""

    normalized = validate_american_odds(american_odds)
    if normalized > 0:
        return 1.0 + (normalized / 100.0)
    return 1.0 + (100.0 / abs(normalized))


def decimal_to_implied_probability(decimal_odds: float | int) -> float:
    """Convert decimal odds greater than 1.0 to implied probability."""

    if isinstance(decimal_odds, bool) or not isinstance(decimal_odds, (int, float)):
        raise ValueError(f"Invalid decimal odds: {decimal_odds!r}")
    normalized = float(decimal_odds)
    if not math.isfinite(normalized) or normalized <= 1.0:
        raise ValueError("Decimal odds must be finite and greater than 1.0")
    return 1.0 / normalized


def american_to_implied_probability(american_odds: int | str) -> float:
    """Convert a valid American price directly to implied probability."""

    return decimal_to_implied_probability(american_to_decimal(american_odds))


def normalize_market_type(market_type: str) -> str:
    """Normalize a provider-neutral market label to lowercase snake case."""

    value = _required_text(market_type, "market_type").casefold()
    normalized = _MARKET_SEPARATOR_RE.sub("_", value).strip("_")
    if not normalized:
        raise ValueError("market_type must contain letters or numbers")
    return normalized


def _validated_optional_line(line: float | int | None) -> float | None:
    if line is None:
        return None
    if isinstance(line, bool) or not isinstance(line, (int, float)):
        raise ValueError("line must be a finite number when provided")
    normalized = float(line)
    if not math.isfinite(normalized):
        raise ValueError("line must be a finite number when provided")
    return normalized


@dataclass(frozen=True, slots=True)
class OddsMarketIdentity:
    """Stable event and market identity, independent of provider payload shape."""

    sport: str
    league: str
    event_id: str
    event_date: date
    home_team: str
    away_team: str
    market_type: str

    def __post_init__(self) -> None:
        sport = _required_text(self.sport, "sport").upper()
        league = _required_text(self.league, "league").upper()
        event_id = _required_text(self.event_id, "event_id")
        home_team = _required_text(self.home_team, "home_team")
        away_team = _required_text(self.away_team, "away_team")
        if isinstance(self.event_date, datetime) or not isinstance(self.event_date, date):
            raise ValueError("event_date must be a date")
        if home_team.casefold() == away_team.casefold():
            raise ValueError("home_team and away_team must identify different teams")

        object.__setattr__(self, "sport", sport)
        object.__setattr__(self, "league", league)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "home_team", home_team)
        object.__setattr__(self, "away_team", away_team)
        object.__setattr__(self, "market_type", normalize_market_type(self.market_type))


@dataclass(frozen=True, slots=True)
class OddsSelection:
    """The priced selection within a normalized market."""

    selection_name: str
    selection_id: str | None = None
    line: float | None = None

    def __post_init__(self) -> None:
        selection_name = _required_text(self.selection_name, "selection_name")
        selection_id = self.selection_id
        if selection_id is not None:
            selection_id = _required_text(selection_id, "selection_id")
        object.__setattr__(self, "selection_name", selection_name)
        object.__setattr__(self, "selection_id", selection_id)
        object.__setattr__(self, "line", _validated_optional_line(self.line))


@dataclass(frozen=True, slots=True)
class OddsSourceMetadata:
    """Provider provenance and safety mode for a normalized quote."""

    sportsbook: str
    provider: str
    mode: str = "research"
    source_type: str = "manual"
    region: str | None = None
    raw_provider_market_id: str | None = None
    raw_event_id: str | None = None
    data_quality: str = "unknown"

    def __post_init__(self) -> None:
        sportsbook = _required_text(self.sportsbook, "sportsbook")
        provider = _required_text(self.provider, "provider")
        mode = _required_text(self.mode, "mode").casefold()
        source_type = _required_text(self.source_type, "source_type").casefold()
        if mode not in _VALID_MODES:
            raise ValueError(
                f"mode must be one of: {', '.join(sorted(_VALID_MODES))}"
            )
        if source_type not in _VALID_SOURCE_TYPES:
            raise ValueError(
                "source_type must be one of: "
                f"{', '.join(sorted(_VALID_SOURCE_TYPES))}"
            )

        region = self.region
        if region is not None:
            region = _required_text(region, "region").casefold()
        raw_market_id = self.raw_provider_market_id
        if raw_market_id is not None:
            raw_market_id = _required_text(
                raw_market_id, "raw_provider_market_id"
            )
        raw_event_id = self.raw_event_id
        if raw_event_id is not None:
            raw_event_id = _required_text(raw_event_id, "raw_event_id")

        object.__setattr__(self, "sportsbook", sportsbook)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "source_type", source_type)
        object.__setattr__(self, "region", region)
        object.__setattr__(self, "raw_provider_market_id", raw_market_id)
        object.__setattr__(self, "raw_event_id", raw_event_id)
        object.__setattr__(
            self, "data_quality", _required_text(self.data_quality, "data_quality")
        )

    @property
    def source(self) -> str:
        """Compatibility-friendly name for the quoted sportsbook/source."""

        return self.sportsbook


@dataclass(frozen=True, slots=True)
class NormalizedOddsQuote:
    """One validated price with stable identity, timestamps, and provenance."""

    market_identity: OddsMarketIdentity
    selection: OddsSelection
    source_metadata: OddsSourceMetadata
    american_odds: int | str
    decimal_odds: float | None = None
    implied_probability: float | None = None
    quote_timestamp: datetime | None = None
    collected_at: datetime | None = None
    event_start_time: datetime | None = None
    is_live: bool | None = None
    eligible_for_betting: bool = False
    kelly_eligible: bool = False
    approval_status: str = "not_approved"

    def __post_init__(self) -> None:
        if not isinstance(self.market_identity, OddsMarketIdentity):
            raise TypeError("market_identity must be an OddsMarketIdentity")
        if not isinstance(self.selection, OddsSelection):
            raise TypeError("selection must be an OddsSelection")
        if not isinstance(self.source_metadata, OddsSourceMetadata):
            raise TypeError("source_metadata must be an OddsSourceMetadata")

        american = validate_american_odds(self.american_odds)
        decimal = american_to_decimal(american)
        probability = decimal_to_implied_probability(decimal)
        if self.decimal_odds is not None:
            supplied_decimal = float(self.decimal_odds)
            if not math.isclose(supplied_decimal, decimal, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError("decimal_odds does not match american_odds")
        if self.implied_probability is not None:
            supplied_probability = float(self.implied_probability)
            if not math.isclose(
                supplied_probability, probability, rel_tol=1e-9, abs_tol=1e-12
            ):
                raise ValueError(
                    "implied_probability does not match american_odds"
                )

        for name in ("quote_timestamp", "collected_at", "event_start_time"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, datetime):
                raise ValueError(f"{name} must be a datetime when provided")
        if self.is_live is not None and not isinstance(self.is_live, bool):
            raise ValueError("is_live must be a boolean when provided")
        if not isinstance(self.eligible_for_betting, bool):
            raise ValueError("eligible_for_betting must be a boolean")
        if not isinstance(self.kelly_eligible, bool):
            raise ValueError("kelly_eligible must be a boolean")

        approval_status = _required_text(self.approval_status, "approval_status")
        restricted_provenance = (
            self.source_metadata.mode in {"research", "sample"}
            or self.source_metadata.source_type in _ALWAYS_INELIGIBLE_SOURCE_TYPES
        )
        if restricted_provenance and (
            self.eligible_for_betting
            or self.kelly_eligible
            or approval_status != "not_approved"
        ):
            raise ValueError(
                "Research, sample, mock, and historical quotes must remain "
                "ineligible and not approved"
            )
        if self.kelly_eligible and not self.eligible_for_betting:
            raise ValueError("kelly_eligible requires eligible_for_betting")

        object.__setattr__(self, "american_odds", american)
        object.__setattr__(self, "decimal_odds", decimal)
        object.__setattr__(self, "implied_probability", probability)
        object.__setattr__(self, "approval_status", approval_status)

    @property
    def sport(self) -> str:
        return self.market_identity.sport

    @property
    def league(self) -> str:
        return self.market_identity.league

    @property
    def event_id(self) -> str:
        return self.market_identity.event_id

    @property
    def event_date(self) -> date:
        return self.market_identity.event_date

    @property
    def home_team(self) -> str:
        return self.market_identity.home_team

    @property
    def away_team(self) -> str:
        return self.market_identity.away_team

    @property
    def market_type(self) -> str:
        return self.market_identity.market_type

    @property
    def selection_name(self) -> str:
        return self.selection.selection_name

    @property
    def selection_id(self) -> str | None:
        return self.selection.selection_id

    @property
    def line(self) -> float | None:
        return self.selection.line

    @property
    def sportsbook(self) -> str:
        return self.source_metadata.sportsbook

    @property
    def source(self) -> str:
        return self.source_metadata.source

    @property
    def provider(self) -> str:
        return self.source_metadata.provider

    @property
    def region(self) -> str | None:
        return self.source_metadata.region

    @property
    def mode(self) -> str:
        return self.source_metadata.mode

    @property
    def source_type(self) -> str:
        return self.source_metadata.source_type

    @property
    def raw_provider_market_id(self) -> str | None:
        return self.source_metadata.raw_provider_market_id

    @property
    def raw_event_id(self) -> str | None:
        return self.source_metadata.raw_event_id

    @property
    def data_quality(self) -> str:
        return self.source_metadata.data_quality


def quote_freshness_status(
    quote_or_timestamp: NormalizedOddsQuote | datetime | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_QUOTE_AGE,
) -> OddsFreshnessStatus:
    """Return a diagnostic freshness status using the provider quote time."""

    if not isinstance(max_age, timedelta) or max_age.total_seconds() < 0:
        raise ValueError("max_age must be a non-negative timedelta")
    timestamp = (
        quote_or_timestamp.quote_timestamp
        if isinstance(quote_or_timestamp, NormalizedOddsQuote)
        else quote_or_timestamp
    )
    if timestamp is None:
        return OddsFreshnessStatus.MISSING_TIMESTAMP
    if not isinstance(timestamp, datetime):
        raise ValueError("quote timestamp must be a datetime")

    current = now or datetime.now(timezone.utc)
    if not isinstance(current, datetime):
        raise ValueError("now must be a datetime")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    else:
        timestamp = timestamp.astimezone(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    age = current - timestamp
    if age < timedelta(0):
        return OddsFreshnessStatus.FUTURE_TIMESTAMP
    if age <= max_age:
        return OddsFreshnessStatus.FRESH
    return OddsFreshnessStatus.STALE


def quote_is_fresh(
    quote_or_timestamp: NormalizedOddsQuote | datetime | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_QUOTE_AGE,
) -> bool:
    """Return whether a quote is recent enough for the configured threshold.

    A missing provider timestamp is always stale, including when ``collected_at``
    is present. This keeps production freshness checks conservative.
    """

    return (
        quote_freshness_status(quote_or_timestamp, now=now, max_age=max_age)
        is OddsFreshnessStatus.FRESH
    )


__all__ = [
    "DEFAULT_MAX_QUOTE_AGE",
    "NormalizedOddsQuote",
    "OddsFreshnessStatus",
    "OddsMarketIdentity",
    "OddsSelection",
    "OddsSourceMetadata",
    "american_to_decimal",
    "american_to_implied_probability",
    "decimal_to_implied_probability",
    "normalize_market_type",
    "quote_freshness_status",
    "quote_is_fresh",
    "validate_american_odds",
]
