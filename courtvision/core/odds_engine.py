"""Sport-neutral odds boundary without provider selection side effects."""

from __future__ import annotations

from dataclasses import dataclass

from courtvision.core.sport_registry import SPORT_REGISTRY


@dataclass(frozen=True, slots=True)
class OddsQuote:
    sport: str
    player: str
    market: str
    line: float
    over_odds: int | None = None
    under_odds: int | None = None
    provider: str = "not_configured"

    def __post_init__(self) -> None:
        config = SPORT_REGISTRY.get(self.sport)
        if not config.supports_market(self.market):
            raise ValueError(f"{self.market!r} is not supported for {config.sport_name}")
