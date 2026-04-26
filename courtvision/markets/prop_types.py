"""Canonical BallDontLie prop-type mapping helpers.

This module maps raw provider ``prop_type`` values into CourtVision's
canonical ``market_type`` keys.
"""
from __future__ import annotations

import re
from typing import Any

# Canonical mappings requested for Phase 2A.
_RAW_TO_CANONICAL: dict[str, str] = {
    "points": "player_points",
    "rebounds": "player_rebounds",
    "assists": "player_assists",
    "points_rebounds": "player_points_rebounds",
    "points_assists": "player_points_assists",
    "rebounds_assists": "player_rebounds_assists",
    "points_rebounds_assists": "player_points_rebounds_assists",
}

_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    normalized = _NORMALIZE_RE.sub("_", text).strip("_")
    # Collapse duplicated underscores if input had mixed separators.
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def canonical_market_type_from_prop_type(raw_prop_type: Any) -> str | None:
    """Return canonical market_type for a raw BallDontLie ``prop_type``.

    Accepts variants like:
    - ``points rebounds``
    - ``points-rebounds``
    - ``player_points`` (already canonical)
    """
    key = _normalize_key(raw_prop_type)
    if not key:
        return None

    # Already canonical player_* key.
    if key.startswith("player_"):
        return key

    return _RAW_TO_CANONICAL.get(key)


__all__ = ["canonical_market_type_from_prop_type"]
