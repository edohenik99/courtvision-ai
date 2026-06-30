from courtvision.data_collection.core import (
    CollectionPlan,
    CollectionRequest,
    UnsupportedSportCollectionError,
)
from courtvision.data_collection.source_contracts import SourceContract


class NFLCollectionAdapter:
    sport = "nfl"
    required_sources = (
        "official schedule/results",
        "licensed play-by-play and rosters/injuries",
        "Meteostat/NOAA weather",
        "licensed historical odds archive",
    )

    def source_contracts(self) -> tuple[SourceContract, ...]:
        return ()

    def build_plan(self, request: CollectionRequest) -> CollectionPlan:
        raise UnsupportedSportCollectionError(
            "NFL data collection is a registry stub in v1; no sources were collected"
        )


__all__ = ["NFLCollectionAdapter"]
