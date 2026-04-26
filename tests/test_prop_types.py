from __future__ import annotations

import pytest

from courtvision.markets.prop_types import canonical_market_type_from_prop_type


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("points", "player_points"),
        ("rebounds", "player_rebounds"),
        ("assists", "player_assists"),
        ("points_rebounds", "player_points_rebounds"),
        ("points_assists", "player_points_assists"),
        ("rebounds_assists", "player_rebounds_assists"),
        ("points_rebounds_assists", "player_points_rebounds_assists"),
        ("points rebounds", "player_points_rebounds"),
        ("points-rebounds-assists", "player_points_rebounds_assists"),
        ("player_points", "player_points"),
    ],
)
def test_canonical_market_type_from_prop_type(raw: str, expected: str) -> None:
    assert canonical_market_type_from_prop_type(raw) == expected


def test_unknown_prop_type_returns_none() -> None:
    assert canonical_market_type_from_prop_type("double_double") is None

