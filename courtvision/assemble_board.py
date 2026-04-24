"""Defensive board assembly with edge validation tripwire."""

from __future__ import annotations

from typing import Any


def assemble_elite_board(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Defensive assembly of elite board with directional edge validation.
    
    This is a tripwire catch for any rows that slipped through the main filter.
    Should never catch anything if filter_elite_candidates() works correctly.
    
    Args:
        rows: List of candidate dictionaries that passed elite filtering
        
    Returns:
        Filtered list with edge-direction violations removed
    """
    elite_rows: list[dict[str, Any]] = []

    for row in rows:
        market = str(row.get("market", "")).lower()
        selection = str(row.get("selection", "")).lower()
        edge = float(row.get("edge_pct", row.get("edge", 0.0)) or 0.0)
        player = str(row.get("player_name", row.get("player", "unknown")))

        if market == "player_points":
            if selection == "over" and edge <= 0:
                print(f"[ELITE_DEFENSIVE_CATCH] {player}: over with edge {edge} - REJECTED")
                continue
            if selection == "under" and edge >= 0:
                print(f"[ELITE_DEFENSIVE_CATCH] {player}: under with edge {edge} - REJECTED")
                continue

        elite_rows.append(row)

    return elite_rows


__all__ = ["assemble_elite_board"]
