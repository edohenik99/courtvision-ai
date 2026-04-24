from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Sequence

import pandas as pd

CORE_PARTIAL_PLAYER_MARKETS: tuple[str, ...] = (
    "player_points",
    "player_rebounds",
    "player_assists",
    "player_3pt_made",
)

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
    "3pt_made_alternate": "player_3pt_made",
    "3pt_made_alts": "player_3pt_made",
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

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_MARKET_KEY_RE = re.compile(r"[^a-z0-9]+")
_SUFFIX_TOKENS = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_market_alias(raw_market_name: Any) -> str | None:
    key = _normalize_market_key(raw_market_name)
    if not key:
        return None
    return _MARKET_ALIASES.get(key)


def canonical_player_name(player_name: Any) -> str:
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
    if game_odds.empty:
        return game_odds.head(0).copy()

    numeric_player_id = _to_float(player_id)
    if numeric_player_id is not None and "player_id" in game_odds.columns:
        by_player_id = game_odds[
            pd.to_numeric(game_odds["player_id"], errors="coerce") == numeric_player_id
        ].copy()
        if not by_player_id.empty:
            return by_player_id

    target_name = canonical_player_name(player_name)
    if not target_name:
        return game_odds.head(0).copy()

    working = game_odds.copy()
    working["_cv_name_key"] = working.get("player_name", pd.Series(index=working.index, dtype=object)).map(canonical_player_name)
    team_series = working.get("team", pd.Series(index=working.index, dtype=object)).fillna("").astype(str).str.upper()
    team_key = str(team_abbr or "").strip().upper()

    exact_mask = working["_cv_name_key"].eq(target_name)
    if team_key:
        exact_team = working[exact_mask & team_series.eq(team_key)].copy()
        if not exact_team.empty:
            return exact_team.drop(columns=["_cv_name_key"], errors="ignore")

    exact_any = working[exact_mask].copy()
    if not exact_any.empty:
        return exact_any.drop(columns=["_cv_name_key"], errors="ignore")

    target_last = target_name.split(" ")[-1]
    if target_last:
        last_mask = working["_cv_name_key"].str.split().str[-1].eq(target_last)
        if team_key:
            team_last = working[last_mask & team_series.eq(team_key)].copy()
            if not team_last.empty and team_last["_cv_name_key"].nunique() == 1:
                return team_last.drop(columns=["_cv_name_key"], errors="ignore")

        any_last = working[last_mask].copy()
        if not any_last.empty and any_last["_cv_name_key"].nunique() == 1:
            return any_last.drop(columns=["_cv_name_key"], errors="ignore")

    return game_odds.head(0).copy()


def partial_fill_markets(
    offered_market_types: Iterable[Any],
    live_supported_markets: Sequence[Any],
    allowed_markets: Sequence[str] = CORE_PARTIAL_PLAYER_MARKETS,
) -> list[str]:
    supported = {str(market).strip() for market in live_supported_markets if str(market).strip()}
    offered = {str(market).strip() for market in offered_market_types if str(market).strip()}
    allowed = {str(market).strip() for market in allowed_markets if str(market).strip()}
    return sorted((supported & allowed) - offered)


def _normalize_market_key(value: Any) -> str:
    text = _normalize_text(value).replace(" ", "_")
    if not text:
        return ""
    return _MARKET_KEY_RE.sub("_", text).strip("_")


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("&", " and ")
    text = _NON_ALNUM_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_float(value: Any) -> float | None:
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


__all__ = [
    "CORE_PARTIAL_PLAYER_MARKETS",
    "canonical_player_name",
    "filter_player_markets",
    "normalize_market_alias",
    "partial_fill_markets",
]
