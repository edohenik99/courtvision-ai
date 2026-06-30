from courtvision.data_collection.core import (
    CollectionPlan,
    CollectionRequest,
    UnsupportedSportCollectionError,
)
from courtvision.data_collection.source_contracts import SourceContract


class NHLCollectionAdapter:
    sport = "nhl"
    required_sources = (
        "official schedule/results",
        "licensed play-by-play, rosters, goalie and injury data",
        "licensed historical odds archive",
    )

    def source_contracts(self) -> tuple[SourceContract, ...]:
        return ()

    def build_plan(self, request: CollectionRequest) -> CollectionPlan:
        raise UnsupportedSportCollectionError(
            "NHL data collection is a registry stub in v1; no sources were collected"
        )


__all__ = ["NHLCollectionAdapter"]
