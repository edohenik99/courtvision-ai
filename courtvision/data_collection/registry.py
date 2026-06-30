"""Closed registry of CourtVision sport collection adapters."""

from __future__ import annotations

from courtvision.data_collection.core import CollectionAdapter, UnsupportedSportCollectionError


class CollectionAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, CollectionAdapter] = {}

    def register(self, adapter: CollectionAdapter) -> None:
        sport = adapter.sport.strip().lower()
        if sport in self._adapters:
            raise ValueError(f"collection adapter already registered: {sport}")
        self._adapters[sport] = adapter

    def get(self, sport: str) -> CollectionAdapter:
        key = sport.strip().lower()
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise UnsupportedSportCollectionError(
                f"no approved collection adapter is registered for sport: {sport}"
            ) from exc

    def sports(self) -> tuple[str, ...]:
        return tuple(self._adapters)


def _build_registry() -> CollectionAdapterRegistry:
    from courtvision.sports.mlb.data_collection import MLBCollectionAdapter
    from courtvision.sports.nba.data_collection import NBACollectionAdapter
    from courtvision.sports.nfl.data_collection import NFLCollectionAdapter
    from courtvision.sports.nhl.data_collection import NHLCollectionAdapter
    from courtvision.sports.wnba.data_collection import WNBACollectionAdapter

    registry = CollectionAdapterRegistry()
    for adapter in (
        MLBCollectionAdapter(),
        NBACollectionAdapter(),
        NFLCollectionAdapter(),
        NHLCollectionAdapter(),
        WNBACollectionAdapter(),
    ):
        registry.register(adapter)
    return registry


COLLECTION_ADAPTER_REGISTRY = _build_registry()


def get_collection_adapter(sport: str) -> CollectionAdapter:
    return COLLECTION_ADAPTER_REGISTRY.get(sport)


__all__ = [
    "COLLECTION_ADAPTER_REGISTRY",
    "CollectionAdapterRegistry",
    "get_collection_adapter",
]
