"""Market quality scoring and filtering.

This module extracts market normalization and quality evaluation
logic from runtime_markets.py and courtvision_ai.py.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import pandas as pd

# Core supported markets
CORE_PARTIAL_PLAYER_MARKETS: tuple[str, ...] = (
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_3pt_made",
)

# Market name aliases for normalization
_MARKET_ALIASES: dict[str, str] = {
    "player_points": "player_points",
    "player_points_alt": "player_points",
    "points": "player_points",
    "points_alt": "player_points",
    "points_scored": "player_points",
    "player_pts": "player_points",
    "pts": "player_points",
    "player_rebounds": "player_rebounds",
    "player_rebounds_alt": "player_rebounds",
    "rebounds": "player_rebounds",
    "rebounds_alt": "player_rebounds",
    "player_reb": "player_rebounds",
    "player_rebs": "player_rebounds",
    "reb": "player_rebounds",
    "rebs": "player_rebounds",
    "total_rebounds": "player_rebounds",
    "player_assists": "player_assists",
    "player_assists_alt": "player_assists",
    "assists": "player_assists",
    "assists_alt": "player_assists",
    "player_ast": "player_assists",
    "player_asts": "player_assists",
    "ast": "player_assists",
    "asts": "player_assists",
    "total_assists": "player_assists",
    "player_3pt_made": "player_3pt_made",
    "player_3pt_made_alt": "player_3pt_made",
    "player_threes": "player_3pt_made",
    "3pt_made": "player_3pt_made",
    "3pt_made_alt": "player_3pt_made",
    "3pm": "player_3pt_made",
    "fg3m": "player_3pt_made",
    "threes": "player_3pt_made",
    "made_threes": "player_3pt_made",
    "threes_made": "player_3pt_made",
    "three_pointers_made": "player_3pt_made",
    "three_point_field_goals_made": "player_3pt_made",
    "player_three_pointers_made": "player_3pt_made",
    "player_steals": "player_steals",
    "player_steals_alt": "player_steals",
    "steals": "player_steals",
    "steals_alt": "player_steals",
    "stl": "player_steals",
    "player_stl": "player_steals",
    "player_blocks": "player_blocks",
    "player_blocks_alt": "player_blocks",
    "blocks": "player_blocks",
    "blocks_alt": "player_blocks",
    "blk": "player_blocks",
    "player_blk": "player_blocks",
    "points_1q": "player_points",
    "points_2q": "player_points",
    "points_3q": "player_points",
    "points_4q": "player_points",
    "assists_1q": "player_assists",
    "assists_2q": "player_assists",
    "assists_3q": "player_assists",
    "assists_4q": "player_assists",
    "rebounds_1q": "player_rebounds",
    "rebounds_2q": "player_rebounds",
    "rebounds_3q": "player_rebounds",
    "rebounds_4q": "player_rebounds",
    "moneyline": "moneyline",
    "team_moneyline": "moneyline",
    "h2h": "moneyline",
    "team_total": "team_total",
    "team_total_over": "team_total",
    "team_total_under": "team_total",
    "team_totals": "team_total",
    "team_total_points": "team_total",
    "player_threes": "player_3pt_made",
}

# Regex patterns for text normalization
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MARKET_KEY_RE = re.compile(r"[^a-z0-9]+")
_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}


def _normalize_text(value: Any) -> str:
    """Normalize text to lowercase ASCII for matching."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = _NON_ALNUM_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_market_key(value: Any) -> str:
    """Normalize market name to canonical key."""
    text = _normalize_text(value).replace(" ", "_")
    if not text:
        return ""
    return _MARKET_KEY_RE.sub("_", text).strip("_")


def normalize_market_alias(raw_market_name: Any) -> str | None:
    """Normalize a raw market name to canonical alias.

    Returns None if the market name cannot be normalized.
    """
    key = _normalize_market_key(raw_market_name)
    if not key:
        return None
    return _MARKET_ALIASES.get(key)


def canonical_player_name(player_name: Any) -> str:
    """Normalize player name for matching.

    Removes suffixes like Jr., Sr., II, III, etc.
    """
    text = _normalize_text(player_name)
    if not text:
        return ""

    tokens = [token for token in text.split(" ") if token]
    while tokens and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def filter_player_markets(
    game_odds: pd.DataFrame,
    player_name: str,
    team_abbr: str,
    player_id: Any = None,
) -> pd.DataFrame:
    """Filter game odds to find markets for a specific player.

    Tries multiple matching strategies:
    1. Exact player_id match
    2. Canonical name + team match
    3. Last name + team match (if unique)

    Returns empty DataFrame if no matches found.
    """
    if game_odds.empty:
        return game_odds.head(0).copy()

    # Try player_id match first
    numeric_player_id = to_float(player_id)
    if numeric_player_id is not None and "player_id" in game_odds.columns:
        by_player_id = game_odds[
            pd.to_numeric(game_odds["player_id"], errors="coerce") == numeric_player_id
        ].copy()
        if not by_player_id.empty:
            return by_player_id

    # Canonical name matching
    target_name = canonical_player_name(player_name)
    if not target_name:
        return game_odds.head(0).copy()

    working = game_odds.copy()
    working["_cv_name_key"] = working.get(
        "player_name", pd.Series(index=working.index, dtype=object)
    ).map(canonical_player_name)

    team_series = (
        working.get("team", pd.Series(index=working.index, dtype=object))
        .fillna("").astype(str).str.upper()
    )
    team_key = str(team_abbr or "").strip().upper()

    # Exact name + team match
    exact_mask = working["_cv_name_key"].eq(target_name)
    if team_key:
        exact_team = working[exact_mask & team_series.eq(team_key)].copy()
        if not exact_team.empty:
            return exact_team.drop(columns=["_cv_name_key"], errors="ignore")

    # Exact name any team
    exact_any = working[exact_mask].copy()
    if not exact_any.empty:
        return exact_any.drop(columns=["_cv_name_key"], errors="ignore")

    # Last name + team match (only if unique)
    target_last = target_name.split(" ")[-1]
    if target_last:
        last_mask = working["_cv_name_key"].str.split().str[-1].eq(target_last)
        if team_key:
            team_last = working[last_mask & team_series.eq(team_key)].copy()
            if not team_last.empty and team_last["_cv_name_key"].nunique() == 1:
                return team_last.drop(columns=["_cv_name_key"], errors="ignore")

        # Last name any team (only if unique)
        any_last = working[last_mask].copy()
        if not any_last.empty and any_last["_cv_name_key"].nunique() == 1:
            return any_last.drop(columns=["_cv_name_key"], errors="ignore")

    return game_odds.head(0).copy()


def to_float(value: Any) -> float | None:
    """Safely convert value to float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class MarketQualityConfig:
    """Configuration for market quality evaluation."""

    # Minimum thresholds for market acceptance
    min_edge: float = 0.5
    min_confidence: float = 0.35
    min_quality_score: float = 70.0

    # Quality bands
    quality_elite_threshold: float = 90.0
    quality_high_threshold: float = 80.0
    quality_mid_threshold: float = 70.0

    # Market type weights
    player_points_weight: float = 1.0
    player_rebounds_weight: float = 0.95
    player_assists_weight: float = 0.95
    player_3pt_weight: float = 0.90
    player_steals_weight: float = 0.85
    player_blocks_weight: float = 0.85
    moneyline_weight: float = 1.0
    team_total_weight: float = 0.95


class MarketQualityScorer:
    """Scorer for evaluating market quality."""

    def __init__(self, config: MarketQualityConfig | None = None) -> None:
        self.config = config or MarketQualityConfig()

    def market_type_weight(self, market_type: str) -> float:
        """Get quality weight for a market type."""
        weights = {
            "player_points": self.config.player_points_weight,
            "player_rebounds": self.config.player_rebounds_weight,
            "player_assists": self.config.player_assists_weight,
            "player_3pt_made": self.config.player_3pt_weight,
            "player_steals": self.config.player_steals_weight,
            "player_blocks": self.config.player_blocks_weight,
            "moneyline": self.config.moneyline_weight,
            "team_total": self.config.team_total_weight,
        }
        return weights.get(str(market_type), 1.0)

    def quality_band(self, quality_score: float) -> str:
        """Classify quality score into band."""
        if quality_score >= self.config.quality_elite_threshold:
            return "elite"
        if quality_score >= self.config.quality_high_threshold:
            return "high"
        if quality_score >= self.config.quality_mid_threshold:
            return "mid"
        return "low"

    def passes_minimum_thresholds(
        self,
        edge: float,
        confidence: float,
        quality_score: float,
    ) -> bool:
        """Check if candidate passes minimum thresholds."""
        return (
            edge >= self.config.min_edge
            and confidence >= self.config.min_confidence
            and quality_score >= self.config.min_quality_score
        )


def partial_fill_markets(
    offered_market_types: Iterable[Any],
    live_supported_markets: Sequence[Any],
    allowed_markets: Sequence[str] = CORE_PARTIAL_PLAYER_MARKETS,
) -> list[str]:
    """Determine which markets to fill partially.

    Returns markets that are:
    1. Supported by live data
    2. Allowed for partial fill
    3. Not already offered
    """
    supported = {str(market).strip() for market in live_supported_markets if str(market).strip()}
    offered = {str(market).strip() for market in offered_market_types if str(market).strip()}
    allowed = {str(market).strip() for market in allowed_markets if str(market).strip()}
    return sorted((supported & allowed) - offered)
