"""
BallDontLie odds schema adapter.

Normalizes rows from BallDontLie `/nba/v2/odds/player_props` into CourtVision's
internal long-market schema.

Important BallDontLie v2 details:
- player_id is a flat integer; player objects are not guaranteed.
- line_value is commonly a string.
- market is nested and flattens to market.type, market.over_odds, market.under_odds,
  and market.odds after pandas.json_normalize.
- over/under rows contain two prices in one API row. CourtVision needs one row per
  actionable side, so this adapter expands those into separate over and under rows.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Optional

import pandas as pd
from courtvision.markets.prop_types import canonical_market_type_from_prop_type


REQUIRED_COLUMNS: tuple[str, ...] = (
    "player_id",
    "player_name",
    "raw_market_name",
    "raw_prop_type",
    "raw_market_type",
    "market_type",
    "selection",
    "line",
    "odds",
    "vendor",
    "game_id",
    "line_source",
    "unresolved_reason",
    # Legacy columns for backward compatibility with candidates.py
    "_normalized_name",
    "market",
    "_team_abbr",
    "bookmaker",
)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "<na>", "nat"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_int(value: Any) -> Optional[int]:
    parsed = _safe_float(value)
    if parsed is None:
        return None
    try:
        return int(parsed)
    except (ValueError, OverflowError):
        return None


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip().lower() in {"", "nan", "none", "null", "<na>", "nat"}


def _clean_text(value: Any) -> str | None:
    if _is_missing(value):
        return None
    text = str(value).strip()
    return text or None


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame({col: pd.Series(dtype="object") for col in REQUIRED_COLUMNS})


def _lookup_value(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row.index:
            value = row.get(name)
            if not _is_missing(value):
                return value
    return None


def _resolve_player_name(
    player_id: Any,
    player_lookup: Optional[dict[Any, dict[str, Any]]],
) -> Optional[str]:
    if not player_lookup or player_id is None:
        return None

    int_pid = _safe_int(player_id)
    str_pid = str(int_pid) if int_pid is not None else str(player_id)

    for key in (int_pid, str_pid, player_id):
        if key is None:
            continue
        identity = player_lookup.get(key)
        if not identity:
            continue
        for field in ("player_name", "full_name", "name"):
            name = _clean_text(identity.get(field))
            if name:
                return name
        first = _clean_text(identity.get("first_name")) or ""
        last = _clean_text(identity.get("last_name")) or ""
        combined = f"{first} {last}".strip()
        if combined:
            return combined
    return None


def _resolve_player_team_abbr(
    player_id: Any,
    player_lookup: Optional[dict[Any, dict[str, Any]]],
) -> Optional[str]:
    if not player_lookup or player_id is None:
        return None

    int_pid = _safe_int(player_id)
    str_pid = str(int_pid) if int_pid is not None else str(player_id)

    for key in (int_pid, str_pid, player_id):
        if key is None:
            continue
        identity = player_lookup.get(key)
        if not identity:
            continue
        team = _clean_text(
            identity.get("team_abbr")
            or identity.get("team")
            or identity.get("team_abbreviation")
        )
        if team:
            return team.upper()
    return None


def _resolve_player_name_from_row(
    row: pd.Series,
    player_id: Any,
    player_lookup: Optional[dict[Any, dict[str, Any]]],
) -> Optional[str]:
    explicit = _clean_text(_lookup_value(row, "player_name", "player.full_name", "player.name", "name"))
    if explicit:
        return explicit

    first = _clean_text(_lookup_value(row, "player.first_name", "first_name")) or ""
    last = _clean_text(_lookup_value(row, "player.last_name", "last_name")) or ""
    embedded = f"{first} {last}".strip()
    if embedded:
        return embedded

    return _resolve_player_name(player_id, player_lookup)


def _resolve_market_name(row: pd.Series) -> Any:
    explicit = _lookup_value(row, "raw_market_name", "market_name", "market")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    return _lookup_value(row, "prop_type", "market.type")


def _resolve_line(row: pd.Series) -> tuple[Optional[float], str]:
    for col in ("line_value", "line", "target_line"):
        value = _safe_float(row.get(col) if col in row.index else None)
        if value is not None:
            return value, col
    return None, "none"


def _resolve_market_type(
    raw_prop_type: Any,
    raw_market_name: Any,
    market_type_mapper: Optional[Callable[[Any], Optional[str]]],
) -> Optional[str]:
    value = raw_prop_type if not _is_missing(raw_prop_type) else raw_market_name

    mapped: Optional[str] = None
    if market_type_mapper is not None:
        mapped = market_type_mapper(value)
        if mapped is None and raw_market_name is not value:
            mapped = market_type_mapper(raw_market_name)

    # Fall back to canonical provider prop-type mapping when caller mapper
    # cannot resolve the value.
    if mapped is None:
        mapped = canonical_market_type_from_prop_type(value)
    if mapped is None and raw_market_name is not value:
        mapped = canonical_market_type_from_prop_type(raw_market_name)

    # Last resort: keep clean text so diagnostics can still surface unknown
    # provider values rather than dropping them silently.
    return _clean_text(mapped if mapped is not None else value)


def _unresolved_reason(player_name: Any, line: Any, market_type: Any, odds: Any) -> Optional[str]:
    if not _clean_text(player_name):
        return "missing_player_name"
    if _safe_float(line) is None:
        return "missing_line"
    if not _clean_text(market_type):
        return "missing_market_type"
    if _safe_float(odds) is None:
        return "missing_odds"
    return None


def _finalize(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return _empty_frame()

    out = pd.DataFrame(rows)
    out["_normalized_name"] = out["player_name"].apply(
        lambda x: str(x).lower().replace(" ", "_") if _clean_text(x) else None
    )
    out["market"] = out["market_type"]
    out["_team_abbr"] = out.get("team_abbr", pd.Series([None] * len(out), index=out.index))
    out["bookmaker"] = out["vendor"]
    for col in REQUIRED_COLUMNS:
        if col not in out.columns:
            out[col] = None
    return out[list(REQUIRED_COLUMNS)].reset_index(drop=True)


def normalize_bdl_player_props(
    raw_odds: pd.DataFrame,
    *,
    player_lookup: Optional[dict[Any, dict[str, Any]]] = None,
    market_type_mapper: Optional[Callable[[Any], Optional[str]]] = None,
) -> pd.DataFrame:
    """
    Normalize raw BallDontLie player-prop odds into one actionable row per side.

    The function does not silently drop unresolved rows. It preserves rows and sets
    `unresolved_reason` so pipeline diagnostics can explain why a market was not usable.
    """
    if raw_odds is None or not isinstance(raw_odds, pd.DataFrame) or raw_odds.empty:
        return _empty_frame()

    # Keep object columns normalized if caller passed raw list data instead of
    # pd.json_normalize output.
    src = pd.json_normalize(raw_odds.to_dict("records"))

    rows: list[dict[str, Any]] = []
    for _, row in src.iterrows():
        game_id = _safe_int(_lookup_value(row, "game_id", "game.id"))
        player_id = _safe_int(_lookup_value(row, "player_id", "player.id"))
        player_name = _resolve_player_name_from_row(row, player_id, player_lookup)
        team_abbr = (
            _clean_text(_lookup_value(row, "team_abbr", "team.abbreviation", "team.abbr"))
            or _resolve_player_team_abbr(player_id, player_lookup)
        )
        raw_prop_type = _lookup_value(row, "prop_type", "raw_prop_type")
        raw_market_name = _resolve_market_name(row)
        market_type = _resolve_market_type(raw_prop_type, raw_market_name, market_type_mapper)
        vendor = _clean_text(_lookup_value(row, "vendor", "bookmaker", "sportsbook")) or ""
        line, line_source = _resolve_line(row)
        raw_market_type = _clean_text(_lookup_value(row, "market.type")) or "over_under"
        market_shape = raw_market_type.lower()

        base = {
            "player_id": player_id,
            "player_name": player_name,
            "raw_market_name": raw_market_name,
            "raw_prop_type": raw_prop_type,
            "raw_market_type": raw_market_type,
            "market_type": market_type,
            "line": line,
            "vendor": vendor,
            "game_id": game_id,
            "line_source": line_source,
            "team_abbr": team_abbr,
        }

        if market_shape == "milestone":
            odds = _safe_float(_lookup_value(row, "market.odds", "odds"))
            item = {**base, "selection": "milestone", "odds": odds}
            item["unresolved_reason"] = _unresolved_reason(player_name, line, market_type, odds)
            rows.append(item)
            continue

        over_odds = _safe_float(_lookup_value(row, "market.over_odds", "over_odds"))
        under_odds = _safe_float(_lookup_value(row, "market.under_odds", "under_odds"))

        # CourtVision's edge validation is side-aware. Expand both sides when BDL
        # provides both prices; otherwise preserve whichever side is available.
        sides: list[tuple[str, Optional[float]]] = []
        if over_odds is not None:
            sides.append(("over", over_odds))
        if under_odds is not None:
            sides.append(("under", under_odds))

        if not sides:
            legacy_odds = _safe_float(_lookup_value(row, "odds"))
            legacy_side = (_clean_text(_lookup_value(row, "selection", "side", "bet_side")) or "").lower() or None
            sides.append((legacy_side or "unknown", legacy_odds))

        for selection, odds in sides:
            item = {**base, "selection": selection, "odds": odds}
            item["unresolved_reason"] = _unresolved_reason(player_name, line, market_type, odds)
            rows.append(item)

    return _finalize(rows)


def filter_valid_odds(normalized: pd.DataFrame) -> pd.DataFrame:
    """Return only rows where player_name, line, market_type, and odds are resolved."""
    if normalized is None or normalized.empty:
        return _empty_frame()
    working = normalized.copy()
    if "unresolved_reason" not in working.columns:
        working["unresolved_reason"] = None
    mask = working["unresolved_reason"].isna()
    return working[mask].reset_index(drop=True)
