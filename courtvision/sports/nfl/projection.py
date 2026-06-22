"""NFL projection framework with neutral usage and matchup placeholders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from courtvision.core.projection_engine import ProjectionResult, weighted_recent_projection
from courtvision.core.sport_registry import get_sport


@dataclass(frozen=True, slots=True)
class NFLProjectionFeatures:
    defensive_matchup: float | None = None
    snap_share: float | None = None
    target_share: float | None = None
    usage_trend: float | None = None
    injury_status: str | None = None


class NFLProjectionModel:
    """Neutral baseline; feature fields are captured but not yet wager-active."""

    name = "nfl_weighted_recent_placeholder_v1"
    supported_markets = get_sport("NFL").supported_prop_markets

    def project(
        self,
        market: str,
        values: Sequence[float],
        features: NFLProjectionFeatures | None = None,
    ) -> ProjectionResult:
        normalized_market = market.strip().lower()
        if normalized_market not in self.supported_markets:
            raise ValueError(f"Unsupported NFL market: {market}")
        feature_values = asdict(features or NFLProjectionFeatures())
        return ProjectionResult(
            sport="NFL",
            market=normalized_market,
            projection=weighted_recent_projection([float(value) for value in values]),
            model_name=self.name,
            data_quality=0.25 if values else 0.0,
            is_placeholder=True,
            context={"features": feature_values, "features_applied": False},
        )
