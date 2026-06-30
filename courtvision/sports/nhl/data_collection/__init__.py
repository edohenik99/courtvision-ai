"""Fail-closed NHL collection registry stub.

Required future contracts: official schedule/results, licensed play-by-play and
rosters, goalie/injury status, and licensed historical odds archives.
"""

from courtvision.sports.nhl.data_collection.adapter import NHLCollectionAdapter

__all__ = ["NHLCollectionAdapter"]
