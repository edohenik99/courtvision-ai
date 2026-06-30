"""Fail-closed NFL collection registry stub.

Required future contracts: official schedule/results, licensed play-by-play,
rosters/injuries, NOAA/Meteostat weather, and licensed historical odds.
"""

from courtvision.sports.nfl.data_collection.adapter import NFLCollectionAdapter

__all__ = ["NFLCollectionAdapter"]
