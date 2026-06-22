"""WNBA placeholder projections based on the existing NBA recent-form blend."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from courtvision.core.projection_engine import ProjectionResult, weighted_recent_projection
from courtvision.core.sport_registry import get_sport


class WNBAProjectionModel:
    """Offline-safe baseline until WNBA data and odds providers are configured."""

    name = "wnba_weighted_recent_placeholder_v1"
    supported_markets = get_sport("WNBA").supported_prop_markets

    def project(self, market: str, game_history: Sequence[Mapping[str, float]]) -> ProjectionResult:
        normalized_market = market.strip().lower()
        if normalized_market not in self.supported_markets:
            raise ValueError(f"Unsupported WNBA market: {market}")

        if normalized_market == "pra":
            values = [
                float(row.get("points", 0.0))
                + float(row.get("rebounds", 0.0))
                + float(row.get("assists", 0.0))
                for row in game_history
            ]
        else:
            values = [float(row.get(normalized_market, 0.0)) for row in game_history]

        return ProjectionResult(
            sport="WNBA",
            market=normalized_market,
            projection=weighted_recent_projection(values),
            model_name=self.name,
            data_quality=0.35 if values else 0.0,
            is_placeholder=True,
            context={"games": len(values), "provider": "not_configured"},
        )
