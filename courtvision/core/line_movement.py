"""Shared line movement types and a provider-neutral CLV capture record."""

from __future__ import annotations

from dataclasses import dataclass

from courtvision.market_intelligence.line_movement import (
    LineMovementAnalysis,
    LineMovementAnalyzer,
    LineMovementType,
    LineSnapshot,
)


@dataclass(frozen=True, slots=True)
class CLVSnapshot:
    """Minimal open/current/close tracking before a provider is connected."""

    open_line: float
    current_line: float
    closing_line: float | None = None
    open_odds: int | None = None
    current_odds: int | None = None
    closing_odds: int | None = None

    @property
    def odds_movement(self) -> int | None:
        if self.open_odds is None or self.current_odds is None:
            return None
        return self.current_odds - self.open_odds

    def beat_closing_line(self, direction: str) -> bool | None:
        if self.closing_line is None:
            return None
        normalized = direction.strip().lower()
        if normalized == "over":
            return self.open_line < self.closing_line
        if normalized == "under":
            return self.open_line > self.closing_line
        raise ValueError("direction must be 'over' or 'under'")


__all__ = [
    "CLVSnapshot",
    "LineMovementAnalysis",
    "LineMovementAnalyzer",
    "LineMovementType",
    "LineSnapshot",
]
