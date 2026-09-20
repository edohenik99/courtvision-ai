"""Immutable, in-memory evidence for pregame MLB player-prop source quotes.

A provider reference is not a resolved MLB event or player identity. This
boundary preserves source semantics and emits the existing research-only odds
contract; it does not collect, persist, model, select, or activate markets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import math
from typing import Final
from zoneinfo import ZoneInfo

from courtvision.core.market_taxonomy import MarketFamily, resolve_market_taxonomy
from courtvision.core.odds import (
    NormalizedOddsQuote,
    OddsMarketIdentity,
    OddsSelection,
    OddsSourceMetadata,
    american_to_decimal,
    validate_american_odds,
)


# Resolve the existing operating timezone once, never during adapter execution.
_OPERATING_TIMEZONE: Final = ZoneInfo("America/Toronto")
_DIAGNOSTIC_CATEGORIES: Final = frozenset({
    "INVALID", "UNSUPPORTED_MARKET", "MISSING_FIELD", "BAD_TIMESTAMP",
    "BAD_PRICE", "BAD_LINE", "UNKNOWN_SIDE", "CONFLICTING_DUPLICATE",
})


class MLBMarketVariant(str, Enum):
    MAIN = "MAIN"
    ALTERNATE = "ALTERNATE"


class MLBPropSide(str, Enum):
    OVER = "OVER"
    UNDER = "UNDER"


def _source_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field_name} must not have surrounding whitespace")


def _aware(value: object, field_name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")


@dataclass(frozen=True, slots=True)
class MLBPlayerPropSourceRecord:
    """One observed bookmaker outcome, with explicit side and raw market key.

    Text is preserved exactly; padded source identifiers/text are rejected
    rather than silently rewritten. Optional provider IDs retain supplied strings
    or integers and remain unavailable when omitted. They never become canonical IDs.
    Numeric lines/prices and side/variant labels have deterministic canonical
    representations. References are supplied evidence labels, never opened.
    """

    provider: str
    provider_sport_key: str
    provider_event_id: str
    home_team: str
    away_team: str
    commence_time: datetime
    bookmaker_key: str
    bookmaker_name: str
    provider_market_key: str
    canonical_market_type: str
    market_variant: MLBMarketVariant
    participant_name: str
    side: MLBPropSide
    line: float
    american_odds: int | str
    market_updated_at: datetime
    collected_at: datetime
    source_refs: tuple[str, ...]
    provider_sport_title: str | None = None
    provider_outcome_id: str | int | None = None
    provider_market_id: str | int | None = None
    provider_bookmaker_id: str | int | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "provider", "provider_sport_key", "provider_event_id", "home_team",
            "away_team", "bookmaker_key", "bookmaker_name", "provider_market_key",
            "canonical_market_type", "participant_name",
        ):
            _source_text(getattr(self, field_name), field_name)
        if self.provider_sport_title is not None:
            _source_text(self.provider_sport_title, "provider_sport_title")
        for field_name in (
            "provider_outcome_id", "provider_market_id", "provider_bookmaker_id",
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(f"{field_name} must be a string or integer when supplied")
            if isinstance(value, str):
                _source_text(value, field_name)
        if self.home_team.casefold() == self.away_team.casefold():
            raise ValueError("home_team and away_team must identify different teams")
        taxonomy = resolve_market_taxonomy("MLB", self.canonical_market_type)
        if (
            taxonomy.market_type != self.canonical_market_type
            or taxonomy.market_family is not MarketFamily.PLAYER_PROP
        ):
            raise ValueError("canonical_market_type must be a canonical MLB player prop")
        for field_name, enum_type in (
            ("market_variant", MLBMarketVariant), ("side", MLBPropSide),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ValueError(f"{field_name} must be an explicit supported string")
            try:
                normalized = enum_type(value.strip().upper())
            except ValueError as exc:
                raise ValueError(f"{field_name} must be an explicit supported value") from exc
            object.__setattr__(self, field_name, normalized)
        if isinstance(self.line, bool) or not isinstance(self.line, (int, float)):
            raise ValueError("line must be a finite number")
        try:
            line = float(self.line)
        except OverflowError as exc:
            raise ValueError("line must be a finite number") from exc
        if not math.isfinite(line):
            raise ValueError("line must be a finite number")
        object.__setattr__(self, "line", line)
        american = validate_american_odds(self.american_odds)
        try:
            decimal = american_to_decimal(american)
            finite_price = math.isfinite(decimal) and decimal > 1.0
        except OverflowError:
            finite_price = False
        if not finite_price:
            raise ValueError("american_odds must have a finite decimal representation greater than 1")
        object.__setattr__(self, "american_odds", american)
        for field_name in ("commence_time", "market_updated_at", "collected_at"):
            _aware(getattr(self, field_name), field_name)
        try:
            updated = self.market_updated_at.astimezone(timezone.utc)
            collected = self.collected_at.astimezone(timezone.utc)
            start = self.commence_time.astimezone(timezone.utc)
            # A validated source record must also be convertible into its quote.
            self.commence_time.astimezone(_OPERATING_TIMEZONE).date()
        except OverflowError as exc:
            raise ValueError("timestamps must be representable in UTC and America/Toronto") from exc
        if updated > collected:
            raise ValueError("market_updated_at must be at or before collected_at")
        if collected >= start:
            raise ValueError("collected_at must be before commence_time")
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise ValueError("source_refs must be a non-empty immutable tuple")
        for reference in self.source_refs:
            _source_text(reference, "source reference")

    def to_normalized_quote(self) -> NormalizedOddsQuote:
        """Bind exact source facts to the existing research/manual odds contract.

        The provider event reference occupies event_id without claiming StatsAPI
        resolution. selection_id stays unavailable, even with a provider outcome
        ID: an outcome ID is not a resolved player identity. The side remains on
        this source record because the existing quote has no named-player side.
        """
        return NormalizedOddsQuote(
            market_identity=OddsMarketIdentity(
                sport="MLB",
                league="MLB",
                event_id=self.provider_event_id,
                event_date=self.commence_time.astimezone(_OPERATING_TIMEZONE).date(),
                home_team=self.home_team,
                away_team=self.away_team,
                market_type=self.canonical_market_type,
            ),
            selection=OddsSelection(
                selection_name=self.participant_name,
                selection_id=None,
                line=self.line,
            ),
            source_metadata=OddsSourceMetadata(
                sportsbook=self.bookmaker_name,
                provider=self.provider,
                mode="research",
                source_type="manual",
                raw_provider_market_id=self.provider_market_key,
                raw_event_id=self.provider_event_id,
            ),
            american_odds=self.american_odds,
            quote_timestamp=self.market_updated_at,
            collected_at=self.collected_at,
            event_start_time=self.commence_time,
            is_live=False,
            eligible_for_betting=False,
            kelly_eligible=False,
            approval_status="not_approved",
        )


@dataclass(frozen=True, slots=True)
class MLBMarketDataDiagnostic:
    """Rejected-source context, already sanitized by the provider adapter.

    Only immutable text pairs are accepted; nested provider payloads cannot be
    retained here. The adapter owns the allowlist and scalar-value sanitization.
    """

    category: str
    field: str
    context: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _source_text(self.category, "category")
        if self.category not in _DIAGNOSTIC_CATEGORIES:
            raise ValueError("category must be a defined source diagnostic")
        _source_text(self.field, "field")
        if not isinstance(self.context, tuple):
            raise ValueError("context must be an immutable tuple")
        keys: set[str] = set()
        for pair in self.context:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ValueError("context entries must be immutable text pairs")
            key, value = pair
            _source_text(key, "context key")
            if not isinstance(value, str):
                raise ValueError("context values must be strings")
            if key in keys:
                raise ValueError("context keys must be unique")
            keys.add(key)


@dataclass(frozen=True, slots=True)
class MLBMarketDataBatch:
    """Immutable valid records and diagnostics, with quotes bound to records.

    Quotes are derived from the records so callers cannot supply a conflicting
    second collection of normalized facts.
    """

    records: tuple[MLBPlayerPropSourceRecord, ...] = ()
    diagnostics: tuple[MLBMarketDataDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        for field_name, item_type in (
            ("records", MLBPlayerPropSourceRecord),
            ("diagnostics", MLBMarketDataDiagnostic),
        ):
            value = getattr(self, field_name)
            if not isinstance(value, tuple) or not all(
                isinstance(item, item_type) for item in value
            ):
                raise ValueError(f"{field_name} must be an immutable tuple of {item_type.__name__}")

    @property
    def quotes(self) -> tuple[NormalizedOddsQuote, ...]:
        return tuple(record.to_normalized_quote() for record in self.records)


__all__ = [
    "MLBMarketDataBatch", "MLBMarketDataDiagnostic", "MLBMarketVariant",
    "MLBPlayerPropSourceRecord", "MLBPropSide",
]
