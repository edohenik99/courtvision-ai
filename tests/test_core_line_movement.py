from __future__ import annotations

import pytest

from courtvision.core.line_movement import CLVSnapshot


def test_clv_snapshot_tracks_line_odds_and_beating_close() -> None:
    snapshot = CLVSnapshot(
        open_line=1.5,
        current_line=2.0,
        closing_line=2.5,
        open_odds=-110,
        current_odds=-125,
    )

    assert snapshot.odds_movement == -15
    assert snapshot.beat_closing_line("over") is True
    assert snapshot.beat_closing_line("under") is False


def test_clv_snapshot_validates_direction() -> None:
    with pytest.raises(ValueError, match="direction"):
        CLVSnapshot(1.5, 1.5, 1.5).beat_closing_line("sideways")
