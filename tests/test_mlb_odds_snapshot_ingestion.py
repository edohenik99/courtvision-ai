from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
from pathlib import Path

import pytest

from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    HOME_RUN_MARKET_TYPE,
    OddsSnapshotIngestionError,
    american_to_decimal,
    american_to_implied_probability,
    ingest_local_odds_snapshot_csv,
)


FIXTURE = Path(__file__).parent / "fixtures" / "mlb" / "hr_odds_snapshot_sample.csv"


def test_local_odds_snapshot_fixture_parses_and_derives_market_references() -> None:
    result = ingest_local_odds_snapshot_csv(FIXTURE)

    assert len(result.rows) == 3
    assert result.rejected_row_count == 0
    assert result.manifest.row_count == 3
    assert result.manifest.data_domain == "odds"
    assert {row.market_type for row in result.rows} == {HOME_RUN_MARKET_TYPE}
    assert result.rows[0].american_odds == 275
    assert math.isclose(result.rows[0].decimal_odds, 3.75)
    assert math.isclose(result.rows[0].implied_probability, 1 / 3.75)
    with pytest.raises(FrozenInstanceError):
        result.rows[0].american_odds = 999  # type: ignore[misc]


@pytest.mark.parametrize(
    ("american_odds", "decimal_odds"),
    [(100, 2.0), (250, 3.5), (-100, 2.0), (-200, 1.5)],
)
def test_american_odds_validation_and_derivation(
    american_odds: int, decimal_odds: float
) -> None:
    assert math.isclose(american_to_decimal(american_odds), decimal_odds)
    assert math.isclose(
        american_to_implied_probability(american_odds), 1 / decimal_odds
    )


@pytest.mark.parametrize("value", [0, 99, -99])
def test_invalid_american_odds_are_rejected(value: int) -> None:
    with pytest.raises(OddsSnapshotIngestionError, match="at least"):
        american_to_decimal(value)


def test_invalid_local_row_is_rejected_with_visible_warning(tmp_path: Path) -> None:
    path = tmp_path / "odds.csv"
    path.write_text(
        "game_date,game_id,player_id,team,opponent,market_type,source_name,"
        "american_odds,as_of\n"
        "2025-04-01,g1,p1,TOR,BOS,home_run,Sample Source A,+250,"
        "2025-04-01T20:00:00Z\n"
        "2025-04-01,g1,p2,BOS,TOR,home_run,Sample Source B,50,"
        "2025-04-01T20:00:00Z\n",
        encoding="utf-8",
    )

    result = ingest_local_odds_snapshot_csv(path)

    assert len(result.rows) == 1
    assert result.rejected_row_count == 1
    assert any("american_odds" in warning for warning in result.warnings)


def test_all_invalid_local_rows_fail_clearly(tmp_path: Path) -> None:
    path = tmp_path / "odds.csv"
    path.write_text(
        "game_date,player_id,market_type,source_name,american_odds,as_of\n"
        "2025-04-01,p1,home_run,Sample Source A,not-a-price,"
        "2025-04-01T20:00:00Z\n",
        encoding="utf-8",
    )

    with pytest.raises(OddsSnapshotIngestionError, match="no valid rows"):
        ingest_local_odds_snapshot_csv(path)
