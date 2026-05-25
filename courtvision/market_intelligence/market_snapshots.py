"""Market snapshot identity helpers.

This module is intentionally passive. It creates stable identifiers for a
player-market-side on a slate so entry, opening, and closing observations can
be joined later without changing prediction, Elite, or Kelly behavior.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any, Mapping


SNAPSHOT_KEY_FIELDS: tuple[str, ...] = (
    "prediction_date",
    "game_id",
    "player_id",
    "player_name",
    "team",
    "opponent",
    "market_type",
    "selection",
)


@dataclass(frozen=True, slots=True)
class MarketSnapshotIdentity:
    prediction_date: str
    game_id: str
    player_id: str
    player_name: str
    team: str
    opponent: str
    market_type: str
    selection: str

    def normalized_parts(self) -> tuple[str, ...]:
        return (
            _normalize_text(self.prediction_date),
            _normalize_text(self.game_id),
            _normalize_text(self.player_id),
            _normalize_text(self.player_name),
            _normalize_team(self.team),
            _normalize_team(self.opponent),
            _normalize_market_type(self.market_type),
            _normalize_text(self.selection).lower(),
        )


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if isinstance(value, float) and math.isnan(value):
            return True
    except TypeError:
        pass
    text = str(value).strip()
    return text == "" or text.lower() in {"nan", "none", "null", "<na>"}


def _normalize_text(value: Any) -> str:
    if _is_missing(value):
        return ""
    return " ".join(str(value).strip().split()).lower()


def _normalize_team(value: Any) -> str:
    if _is_missing(value):
        return ""
    return str(value).strip().upper()


def _normalize_market_type(value: Any) -> str:
    return _normalize_text(value).replace(" ", "_")


def _get_first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and not _is_missing(row.get(key)):
            return row.get(key)
    return ""


def market_snapshot_identity(
    row: Mapping[str, Any],
    *,
    prediction_date: str | None = None,
) -> MarketSnapshotIdentity:
    """Build normalized market identity fields from a board/snapshot row."""
    row_date = prediction_date if prediction_date is not None else _get_first(row, "prediction_date", "date")
    return MarketSnapshotIdentity(
        prediction_date=str(row_date or ""),
        game_id=str(_get_first(row, "game_id", "event_id")),
        player_id=str(_get_first(row, "player_id", "entity_id")),
        player_name=str(_get_first(row, "player_name", "entity_name", "name")),
        team=str(_get_first(row, "team", "team_abbr")),
        opponent=str(_get_first(row, "opponent", "opponent_abbr")),
        market_type=str(_get_first(row, "market_type", "market", "prop_type", "raw_prop_type")),
        selection=str(_get_first(row, "selection", "side")),
    )


def market_snapshot_key_from_identity(identity: MarketSnapshotIdentity) -> str:
    """Return a deterministic key for a player-market-side snapshot identity."""
    joined = "|".join(identity.normalized_parts())
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:20]
    return f"ms_{digest}"


def market_snapshot_key(
    row: Mapping[str, Any],
    *,
    prediction_date: str | None = None,
) -> str:
    """Return a deterministic market snapshot key for a row-like mapping."""
    existing = _get_first(row, "market_snapshot_key")
    if existing:
        return str(existing).strip()
    return market_snapshot_key_from_identity(
        market_snapshot_identity(row, prediction_date=prediction_date)
    )


def market_snapshot_key_payload(
    row: Mapping[str, Any],
    *,
    prediction_date: str | None = None,
) -> dict[str, str]:
    """Return the key plus normalized identity parts for diagnostics."""
    identity = market_snapshot_identity(row, prediction_date=prediction_date)
    parts = identity.normalized_parts()
    return {
        "market_snapshot_key": market_snapshot_key_from_identity(identity),
        **dict(zip(SNAPSHOT_KEY_FIELDS, parts, strict=True)),
    }


__all__ = [
    "MarketSnapshotIdentity",
    "SNAPSHOT_KEY_FIELDS",
    "market_snapshot_identity",
    "market_snapshot_key",
    "market_snapshot_key_from_identity",
    "market_snapshot_key_payload",
]
