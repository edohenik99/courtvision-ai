"""Fail-closed NBA collection registry stub.

Required future contracts: official schedule/results, licensed play-by-play and
player data, approved weather/travel context where applicable, and licensed
historical odds archives.
"""

from courtvision.sports.nba.data_collection.adapter import NBACollectionAdapter

__all__ = ["NBACollectionAdapter"]
