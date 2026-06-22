"""Backward-compatible NBA projection imports.

New code should import from :mod:`courtvision.sports.nba.projection`.
"""

from courtvision.sports.nba.projection import STAT_FIELDS, project_from_context, weighted_recent_average

__all__ = ["STAT_FIELDS", "project_from_context", "weighted_recent_average"]
