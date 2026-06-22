"""NBA module and compatibility exports."""

from courtvision.core.sport_registry import get_sport
from courtvision.sports.nba.projection import project_from_context, weighted_recent_average

SPORT = get_sport("NBA")

__all__ = ["CourtVisionPro", "SPORT", "project_from_context", "weighted_recent_average"]


def __getattr__(name: str):
    if name == "CourtVisionPro":
        from courtvision.sports.nba.runtime import CourtVisionPro

        return CourtVisionPro
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
