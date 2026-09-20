"""Pure normalization of supplied The Odds API MLB event-odds mappings.

No collection, credentials, persistence, or canonical identity resolution occurs.
The caller supplies capture time and immutable evidence references. A manual,
research-only quote accompanies each accepted bookmaker outcome.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from types import MappingProxyType

from courtvision.core.odds import american_to_decimal, validate_american_odds
from courtvision.sports.mlb.market_data import (
    MLBMarketDataBatch,
    MLBMarketDataDiagnostic,
    MLBMarketVariant,
    MLBPlayerPropSourceRecord,
    MLBPropSide,
)


# Deliberate allowlist: never infer support by stripping an _alternate suffix.
_MAIN_MARKETS = {
    "batter_hits": "batter_hits",
    "batter_total_bases": "batter_total_bases",
    "batter_rbis": "batter_rbis",
    "batter_runs_scored": "batter_runs",
    "batter_walks": "batter_walks",
    "batter_strikeouts": "batter_strikeouts",
    "batter_stolen_bases": "batter_stolen_bases",
    "pitcher_strikeouts": "pitcher_strikeouts",
    "pitcher_hits_allowed": "pitcher_hits_allowed",
    "pitcher_walks": "pitcher_walks_allowed",
    "pitcher_earned_runs": "pitcher_earned_runs",
    "pitcher_outs": "pitcher_outs_recorded",
}
# Verified against public provider documentation on 2026-09-20:
# https://the-odds-api.com/sports-odds-data/betting-markets.html
# No batter_stolen_bases_alternate is documented; it remains unsupported.
_ALTERNATE_MARKETS = {
    "batter_hits_alternate": "batter_hits",
    "batter_total_bases_alternate": "batter_total_bases",
    "batter_rbis_alternate": "batter_rbis",
    "batter_runs_scored_alternate": "batter_runs",
    "batter_walks_alternate": "batter_walks",
    "batter_strikeouts_alternate": "batter_strikeouts",
    "pitcher_strikeouts_alternate": "pitcher_strikeouts",
    "pitcher_hits_allowed_alternate": "pitcher_hits_allowed",
    "pitcher_walks_alternate": "pitcher_walks_allowed",
    "pitcher_earned_runs_alternate": "pitcher_earned_runs",
    "pitcher_outs_alternate": "pitcher_outs_recorded",
}
PROVIDER_MARKET_MAPPING = MappingProxyType({
    **{key: (value, MLBMarketVariant.MAIN) for key, value in _MAIN_MARKETS.items()},
    **{key: (value, MLBMarketVariant.ALTERNATE) for key, value in _ALTERNATE_MARKETS.items()},
})


class _InvalidRow(ValueError):
    def __init__(self, category: str, field: str) -> None:
        self.category = category
        self.field = field
        super().__init__(field)


def _text(row: Mapping, field: str) -> str:
    value = row.get(field)
    if value is None or value == "":
        raise _InvalidRow("MISSING_FIELD", field)
    if not isinstance(value, str) or not value.strip():
        raise _InvalidRow("INVALID", field)
    return value


def _timestamp(value: object, field: str) -> datetime:
    if value is None or value == "":
        raise _InvalidRow("MISSING_FIELD", field)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.utcoffset() is None:
            raise ValueError
        result = result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise _InvalidRow("BAD_TIMESTAMP", field) from None
    return result


def _line(value: object) -> float:
    if value is None:
        raise _InvalidRow("MISSING_FIELD", "point")
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError
        result = float(value)
        if not math.isfinite(result):
            raise ValueError
    except (ValueError, OverflowError):
        raise _InvalidRow("BAD_LINE", "point") from None
    return result


def _side(value: object) -> MLBPropSide:
    if value is None or value == "":
        raise _InvalidRow("MISSING_FIELD", "name")
    try:
        return MLBPropSide(value.strip().upper()) if isinstance(value, str) else MLBPropSide(value)
    except (ValueError, TypeError):
        raise _InvalidRow("UNKNOWN_SIDE", "name") from None


def _optional_id(row: Mapping, field: str = "id") -> str | int | None:
    value = row.get(field)
    if value is None or (field == 'id' and isinstance(value, int) and not isinstance(value, bool)):
        return value
    return _text(row, field)


def _context(event: Mapping, book: Mapping, market: Mapping, outcome: Mapping) -> tuple[tuple[str, str], ...]:
    """Keep only bounded scalar diagnostic fields, never whole provider objects."""
    fields = (
        ("event_id", event.get("id")), ("sport_key", event.get("sport_key")),
        ("bookmaker_key", book.get("key")), ("market_key", market.get("key")),
        ("market_updated_at", market.get("last_update")),
        ("participant", outcome.get("description")), ("side", outcome.get("name")),
        ("line", outcome.get("point")), ("price", outcome.get("price")),
        ("outcome_id", outcome.get("id")),
    )
    context = []
    for key, value in fields:
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            try:
                text = str(value)[:160]
            except (ValueError, OverflowError):
                text = "<unrepresentable scalar>"
            context.append((key, text))
    return tuple(context)


def _identity_keys(event: Mapping, book: Mapping, market: Mapping, outcome: Mapping) -> tuple[tuple, ...]:
    """Natural identity plus optional provider outcome-ID integrity constraint.

    Without an outcome ID, different participants/lines are distinct offers.
    With an explicit ID, contradictory participant/line/market claims at the
    same provider update cannot survive as separate offers. Neither key uses
    row order, hashes, collection time, nor a fabricated canonical identifier.
    """
    try:
        prefix = ("the_odds_api", _text(event, "id"), _text(book, "key"))
        updated = _timestamp(market.get("last_update"), "last_update").astimezone(timezone.utc)
    except _InvalidRow:
        return ()
    keys = []
    try:
        keys.append(("natural", *prefix, _text(market, "key"), _text(outcome, "description"),
                     _side(outcome.get("name")).value, _line(outcome.get("point")), updated))
    except _InvalidRow:
        pass
    if (isinstance(outcome.get("id"), str) and outcome["id"].strip()) or (isinstance(outcome.get("id"), int) and not isinstance(outcome["id"], bool)):
        keys.append(("outcome_id", *prefix, outcome["id"], updated))
    return tuple(keys)


def _record(event: Mapping, book: Mapping, market: Mapping, outcome: Mapping,
            collected_at: datetime, source_refs: tuple[str, ...]) -> MLBPlayerPropSourceRecord:
    sport_key = _text(event, "sport_key")
    if sport_key != "baseball_mlb":
        raise _InvalidRow("INVALID", "sport_key")
    raw_market = _text(market, "key")
    if raw_market not in PROVIDER_MARKET_MAPPING:
        raise _InvalidRow("UNSUPPORTED_MARKET", "key")
    canonical, variant = PROVIDER_MARKET_MAPPING[raw_market]
    start = _timestamp(event.get("commence_time"), "commence_time")
    updated = _timestamp(market.get("last_update"), "last_update")
    if updated.astimezone(timezone.utc) > collected_at.astimezone(timezone.utc):
        raise _InvalidRow("BAD_TIMESTAMP", "last_update")
    if collected_at.astimezone(timezone.utc) >= start.astimezone(timezone.utc):
        raise _InvalidRow("BAD_TIMESTAMP", "collected_at")
    line = _line(outcome.get("point"))
    side = _side(outcome.get("name"))
    if outcome.get("price") is None:
        raise _InvalidRow("MISSING_FIELD", "price")
    try:
        price = validate_american_odds(outcome["price"])
        decimal = american_to_decimal(price)
        if not math.isfinite(decimal) or decimal <= 1:
            raise ValueError
    except (ValueError, OverflowError):
        raise _InvalidRow("BAD_PRICE", "price") from None
    try:
        return MLBPlayerPropSourceRecord(
            provider="the_odds_api", provider_sport_key=sport_key,
            provider_sport_title=_optional_id(event, "sport_title"),
            provider_event_id=_text(event, "id"),
            home_team=_text(event, "home_team"), away_team=_text(event, "away_team"),
            commence_time=start, bookmaker_key=_text(book, "key"),
            bookmaker_name=_text(book, "title"), provider_market_key=raw_market,
            canonical_market_type=canonical, market_variant=variant,
            participant_name=_text(outcome, "description"), side=side, line=line,
            american_odds=price, market_updated_at=updated, collected_at=collected_at,
            source_refs=source_refs, provider_outcome_id=_optional_id(outcome),
            provider_market_id=_optional_id(market), provider_bookmaker_id=_optional_id(book),
        )
    except _InvalidRow:
        raise
    except (ValueError, TypeError):
        # Do not leak repr(payload) or arbitrary exception values into diagnostics.
        raise _InvalidRow("INVALID", "source_record") from None


@dataclass(frozen=True)
class _Claim:
    record: MLBPlayerPropSourceRecord | None
    keys: tuple[tuple, ...]
    context: tuple[tuple[str, str], ...]
    error: tuple[str, str] | None = None


def normalize_mlb_event_odds(
    payload: Mapping[str, object], *, collected_at: datetime, source_refs: tuple[str, ...],
) -> MLBMarketDataBatch:
    """Normalize one supplied event, keeping diagnostics and rejecting conflicts.

    Missing caller provenance is a contract error. Malformed provider rows are
    diagnostics; valid siblings survive. Exact normalized duplicate records
    deduplicate. All members of conflicting identity groups are excluded,
    including a valid price contradicted by an invalid price in the same batch.
    Timestamps retain the supplied instant in UTC (including DST-fold inputs).
    Ordering is independent of bookmaker, market, and outcome array positions.
    """
    collected_at = _timestamp(collected_at, "collected_at")
    if not isinstance(source_refs, tuple) or not source_refs or any(
        not isinstance(ref, str) or not ref.strip() or ref != ref.strip() for ref in source_refs
    ):
        raise ValueError("source_refs must be a non-empty immutable tuple of nonblank references")
    diagnostics = []
    claims = []

    def reject(category: str, field: str, context: tuple = ()) -> None:
        diagnostics.append(MLBMarketDataDiagnostic(category, field, context))

    def children(row: Mapping, field: str, context: tuple) -> tuple | list:
        value = row.get(field)
        if value is None:
            reject("MISSING_FIELD", field, context)
            return ()
        if not isinstance(value, (list, tuple)):
            reject("INVALID", field, context)
            return ()
        return value

    if not isinstance(payload, Mapping):
        return MLBMarketDataBatch(diagnostics=(MLBMarketDataDiagnostic("INVALID", "event", ()),))
    for book in children(payload, "bookmakers", _context(payload, {}, {}, {})):
        if not isinstance(book, Mapping):
            reject("INVALID", "bookmaker", _context(payload, {}, {}, {}))
            continue
        for market in children(book, "markets", _context(payload, book, {}, {})):
            if not isinstance(market, Mapping):
                reject("INVALID", "market", _context(payload, book, {}, {}))
                continue
            outcomes = children(market, "outcomes", _context(payload, book, market, {}))
            if not outcomes:
                try:
                    key = _text(market, "key")
                    if key not in PROVIDER_MARKET_MAPPING:
                        raise _InvalidRow("UNSUPPORTED_MARKET", "key")
                    _timestamp(market.get("last_update"), "last_update")
                except _InvalidRow as exc:
                    reject(exc.category, exc.field, _context(payload, book, market, {}))
            for outcome in outcomes:
                if not isinstance(outcome, Mapping):
                    reject("INVALID", "outcome", _context(payload, book, market, {}))
                    continue
                context = _context(payload, book, market, outcome)
                keys = _identity_keys(payload, book, market, outcome)
                try:
                    record = _record(payload, book, market, outcome, collected_at, source_refs)
                    claims.append(_Claim(record, keys, context))
                except _InvalidRow as exc:
                    reject(exc.category, exc.field, context)
                    claims.append(_Claim(None, keys, context, (exc.category, exc.field)))

    # Empty arrays cannot turn an invalid event into a silently accepted batch.
    if not claims:
        try:
            if _text(payload, "sport_key") != "baseball_mlb":
                raise _InvalidRow("INVALID", "sport_key")
            _text(payload, "id")
            home = _text(payload, "home_team")
            away = _text(payload, "away_team")
            if home.casefold() == away.casefold():
                raise _InvalidRow("INVALID", "away_team")
            if collected_at >= _timestamp(payload.get("commence_time"), "commence_time"):
                raise _InvalidRow("BAD_TIMESTAMP", "collected_at")
        except _InvalidRow as exc:
            reject(exc.category, exc.field, _context(payload, {}, {}, {}))

    groups: dict[tuple, list[int]] = {}
    for index, claim in enumerate(claims):
        for key in claim.keys:
            groups.setdefault(key, []).append(index)
    conflicts = set()
    for indexes in groups.values():
        contents = {claims[index].record or (claims[index].error, claims[index].context) for index in indexes}
        if len(contents) > 1:
            conflicts.update(indexes)
    # Propagate rejection across overlapping natural/provider-ID constraints.
    changed = True
    while changed:
        previous = len(conflicts)
        for indexes in groups.values():
            if conflicts.intersection(indexes):
                conflicts.update(indexes)
        changed = len(conflicts) != previous
    for index in sorted(conflicts):
        reject("CONFLICTING_DUPLICATE", "source_identity", claims[index].context)
    records = {claim.record for index, claim in enumerate(claims)
               if claim.record is not None and index not in conflicts}
    ordered = tuple(sorted(records, key=lambda record: (
        record.provider_event_id, record.bookmaker_key, record.provider_market_key,
        record.participant_name, record.side.value, record.line, record.market_updated_at,
    )))
    return MLBMarketDataBatch(
        records=ordered,
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.category, item.field, item.context))),
    )


__all__ = ["PROVIDER_MARKET_MAPPING", "normalize_mlb_event_odds"]
