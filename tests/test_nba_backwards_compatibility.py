from __future__ import annotations

from courtvision.projection.base_model import (
    project_from_context as legacy_project_from_context,
    weighted_recent_average as legacy_weighted_recent_average,
)
from courtvision.sports.nba.projection import project_from_context, weighted_recent_average


def test_legacy_nba_projection_imports_resolve_to_sport_module() -> None:
    assert legacy_project_from_context is project_from_context
    assert legacy_weighted_recent_average is weighted_recent_average


def test_legacy_courtvision_pro_import_is_preserved() -> None:
    from courtvision import CourtVisionPro as legacy_class
    from courtvision.sports.nba import CourtVisionPro as sport_class

    assert legacy_class is sport_class
