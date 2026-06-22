from __future__ import annotations

from courtvision.sports.wnba import SPORT, WNBAProjectionModel


def test_wnba_module_loads_and_supports_required_props() -> None:
    assert SPORT.sport_name == "WNBA"
    assert set(SPORT.supported_prop_markets) == {
        "points",
        "rebounds",
        "assists",
        "pra",
        "threes",
        "steals",
        "blocks",
    }


def test_wnba_placeholder_projects_pra() -> None:
    history = [
        {"points": 20.0, "rebounds": 5.0, "assists": 4.0},
        {"points": 24.0, "rebounds": 7.0, "assists": 6.0},
    ]
    result = WNBAProjectionModel().project("pra", history)

    assert result.projection == 33.0
    assert result.is_placeholder is True
    assert result.sport == "WNBA"
