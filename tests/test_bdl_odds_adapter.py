"""Tests for the BallDontLie odds schema adapter."""
from __future__ import annotations

import pandas as pd
import pytest

from courtvision.data.bdl_odds_adapter import (
    REQUIRED_COLUMNS,
    filter_valid_odds,
    normalize_bdl_player_props,
)


def _flatten(rows):
    return pd.json_normalize(rows)


def _basic_lookup():
    return {
        246: {"player_id": 246, "player_name": "LeBron James", "team_abbr": "LAL"},
        99: {"player_id": 99, "player_name": "Jayson Tatum", "team_abbr": "BOS"},
    }


def test_empty_input_returns_required_columns():
    out = normalize_bdl_player_props(pd.DataFrame())
    assert list(out.columns) == list(REQUIRED_COLUMNS)
    assert out.empty


def test_none_input_returns_required_columns():
    out = normalize_bdl_player_props(None)  # type: ignore[arg-type]
    assert list(out.columns) == list(REQUIRED_COLUMNS)


def test_line_value_is_mapped_to_line():
    raw = _flatten([
        {
            "id": 1,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "25.5",
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    assert len(out) == 2
    row = out[out["selection"].eq("over")].iloc[0]
    assert row["line"] == 25.5
    assert row["line_source"] == "line_value"
    assert row["raw_prop_type"] == "points"
    assert row["raw_market_type"] == "over_under"
    assert row["raw_market_name"] == "points"
    assert row["player_name"] == "LeBron James"
    assert row["vendor"] == "draftkings"
    assert row["unresolved_reason"] is None
    assert row["selection"] == "over"
    assert row["odds"] == -110.0


def test_milestone_row_uses_market_odds():
    raw = _flatten([
        {
            "id": 2,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "30.0",
            "market": {"type": "milestone", "odds": 200},
        }
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    row = out.iloc[0]
    assert row["odds"] == 200.0
    assert row["selection"] == "milestone"
    assert row["raw_market_type"] == "milestone"


def test_missing_line_value_does_not_crash_and_marks_unresolved():
    raw = _flatten([
        {
            "id": 3,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            # no line_value
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    row = out.iloc[0]
    assert row["line"] is None
    assert row["unresolved_reason"] == "missing_line"


def test_missing_player_name_does_not_crash_and_marks_unresolved():
    raw = _flatten([
        {
            "id": 4,
            "game_id": 100,
            "player_id": 555,  # not in lookup
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "20.5",
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    row = out.iloc[0]
    assert row["player_name"] is None
    assert row["unresolved_reason"] == "missing_player_name"
    # line still preserved
    assert row["line"] == 20.5


def test_missing_raw_market_name_does_not_crash():
    raw = pd.DataFrame([
        {
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "line_value": "25.5",
            # no prop_type, no market
        }
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    row = out.iloc[0]
    assert row["unresolved_reason"] == "missing_market_type"


def test_filter_valid_odds_drops_unresolved_rows():
    raw = _flatten([
        {
            "id": 1,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "25.5",
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        },
        {
            "id": 2,
            "game_id": 100,
            "player_id": 555,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "22.5",
            "market": {"type": "over_under", "over_odds": -120, "under_odds": -110},
        },
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    valid = filter_valid_odds(out)
    assert len(valid) == 2
    assert set(valid["selection"]) == {"over", "under"}
    assert set(valid["player_name"]) == {"LeBron James"}


def test_filter_valid_odds_on_empty_returns_required_schema():
    out = filter_valid_odds(pd.DataFrame())
    assert list(out.columns) == list(REQUIRED_COLUMNS)


def test_market_type_mapper_canonicalizes_prop_type():
    def mapper(value):
        return {"points": "player_points", "rebounds": "player_rebounds"}.get(str(value))

    raw = _flatten([
        {
            "id": 1,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "25.5",
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }
    ])
    out = normalize_bdl_player_props(
        raw, player_lookup=_basic_lookup(), market_type_mapper=mapper
    )
    assert out.iloc[0]["market_type"] == "player_points"


def test_points_market_type_canonicalization_unchanged_without_mapper():
    raw = _flatten([
        {
            "id": 1,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "25.5",
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }
    ])

    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())

    assert set(out["market_type"]) == {"player_points"}
    assert set(out["raw_prop_type"]) == {"points"}


def test_required_columns_always_present():
    raw = _flatten([
        {
            "id": 1,
            "game_id": 100,
            "player_id": 246,
            "vendor": "draftkings",
            "prop_type": "points",
            "line_value": "25.5",
            "market": {"type": "over_under", "over_odds": -110, "under_odds": -110},
        }
    ])
    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())
    for col in REQUIRED_COLUMNS:
        assert col in out.columns


def test_over_under_market_expands_into_two_actionable_sides():
    raw = _flatten([
        {
            "id": 10,
            "game_id": 200,
            "player_id": 99,
            "vendor": "fanduel",
            "prop_type": "rebounds",
            "line_value": "8.5",
            "market": {"type": "over_under", "over_odds": 105, "under_odds": -125},
        }
    ])

    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())

    assert len(out) == 2
    by_side = out.set_index("selection")
    assert by_side.loc["over", "odds"] == 105.0
    assert by_side.loc["under", "odds"] == -125.0
    assert by_side.loc["over", "line"] == 8.5
    assert by_side.loc["under", "line"] == 8.5


def test_flat_player_id_uses_lookup_without_embedded_player_object():
    raw = _flatten([
        {
            "id": 11,
            "game_id": 200,
            "player_id": 99,
            "vendor": "fanduel",
            "prop_type": "assists",
            "line_value": "5.5",
            "market": {"type": "over_under", "over_odds": -115, "under_odds": -105},
        }
    ])

    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())

    assert set(out["player_name"]) == {"Jayson Tatum"}
    assert out["unresolved_reason"].isna().all()


def test_missing_odds_is_marked_unresolved():
    raw = _flatten([
        {
            "id": 12,
            "game_id": 200,
            "player_id": 99,
            "vendor": "fanduel",
            "prop_type": "points",
            "line_value": "24.5",
            "market": {"type": "over_under"},
        }
    ])

    out = normalize_bdl_player_props(raw, player_lookup=_basic_lookup())

    assert len(out) == 1
    assert out.iloc[0]["selection"] == "unknown"
    assert out.iloc[0]["unresolved_reason"] == "missing_odds"
