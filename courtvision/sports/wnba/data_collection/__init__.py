"""Fail-closed WNBA collection registry stub.

Required future contracts: official schedule/results, licensed play-by-play and
player data, roster/injury status, and licensed historical odds archives.
"""

from courtvision.sports.wnba.data_collection.adapter import WNBACollectionAdapter

__all__ = ["WNBACollectionAdapter"]
