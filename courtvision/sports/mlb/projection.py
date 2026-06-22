"""MLB projection framework with neutral Statcast-style feature placeholders."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

from courtvision.core.projection_engine import ProjectionResult, weighted_recent_projection
from courtvision.core.sport_registry import get_sport


@dataclass(frozen=True, slots=True)
class MLBProjectionFeatures:
    handedness_matchup: float | None = None
    pitcher_matchup: float | None = None
    ballpark_factor: float | None = None
    weather_factor: float | None = None
    recent_form: float | None = None


class MLBProjectionModel:
    """Neutral baseline; feature fields are captured for research only."""

    name = "mlb_weighted_recent_placeholder_v1"
    supported_markets = get_sport("MLB").supported_prop_markets

    def project(
        self,
        market: str,
        values: Sequence[float],
        features: MLBProjectionFeatures | None = None,
    ) -> ProjectionResult:
        normalized_market = market.strip().lower()
        if normalized_market not in self.supported_markets:
            raise ValueError(f"Unsupported MLB market: {market}")
        feature_values = asdict(features or MLBProjectionFeatures())
        return ProjectionResult(
            sport="MLB",
            market=normalized_market,
            projection=weighted_recent_projection([float(value) for value in values]),
            model_name=self.name,
            data_quality=0.25 if values else 0.0,
            is_placeholder=True,
            context={"features": feature_values, "features_applied": False},
        )
