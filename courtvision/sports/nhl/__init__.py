"""Reserved NHL module for the next expansion phase."""

from courtvision.core.sport_registry import get_sport

SPORT = get_sport("NHL")

__all__ = ["SPORT"]
