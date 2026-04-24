"""Edge calculation functions for candidate scoring.

This module extracts edge percentage and bias factor logic from runtime_scoring.py.
"""

from __future__ import annotations

from typing import Any


def edge_pct_denominator(market_type: str, sportsbook_line: float) -> float:
    """Compute the denominator for edge percentage calculations.

    Different market types have different floor values to prevent
    inflated percentages on low lines.
    """
    floors = {
        "player_points": 14.0,
        "player_rebounds": 7.0,
        "player_assists": 6.0,
        "player_3pt_made": 2.5,
        "player_steals": 1.5,
        "player_blocks": 1.5,
        "team_total": 100.0,
    }
    floor = float(floors.get(str(market_type), 10.0))
    return max(abs(float(sportsbook_line or 0.0)), floor)


def favorite_bias_factor(market_type: str, sportsbook_line: float, odds: Any) -> float:
    """Compute bias factor based on favorite/longshot odds.

    Adjusts edge values based on whether the selection is a heavy favorite,
    moderate favorite, or longshot. Moneyline markets use odds directly,
    while player props use the line value.
    """
    if market_type == "moneyline":
        odds_value = to_float(odds)
        if odds_value is None:
            return 1.0
        if odds_value >= 400:
            return 0.72
        if odds_value >= 250:
            return 0.82
        if odds_value >= 150:
            return 0.90
        if odds_value <= -180:
            return 1.05
        return 1.0

    if sportsbook_line >= 20.0:
        return 1.05
    if sportsbook_line <= 12.0:
        return 0.90
    return 1.0


def edge_pct_value(market_type: str, adjusted_edge_abs: float, sportsbook_line: float) -> float:
    """Compute edge as a percentage of the line denominator.

    For moneyline, returns the raw edge percentage.
    For other markets, normalizes by the market-specific floor.
    """
    if market_type == "moneyline":
        return adjusted_edge_abs * 100.0
    denom = edge_pct_denominator(market_type, sportsbook_line)
    return (adjusted_edge_abs / denom) * 100.0 if denom > 0 else 0.0


def compute_edge(
    market_type: str,
    edge_abs: float,
    sportsbook_line: float,
    odds: Any,
) -> dict[str, float]:
    """Compute edge metrics for a candidate.

    Returns a dict with:
    - bias_factor: The favorite/longshot adjustment factor
    - adjusted_edge_abs: Edge after bias adjustment
    - edge_pct: Edge as percentage of normalized line
    """
    bias_factor = favorite_bias_factor(market_type, sportsbook_line, odds)
    adjusted_edge_abs = edge_abs * bias_factor
    edge_pct = edge_pct_value(market_type, adjusted_edge_abs, sportsbook_line)

    return {
        "bias_factor": round(float(bias_factor), 4),
        "adjusted_edge_abs": round(float(adjusted_edge_abs), 4),
        "edge_pct": round(float(edge_pct), 4),
    }


def to_float(value: Any) -> float | None:
    """Safely convert a value to float."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None
