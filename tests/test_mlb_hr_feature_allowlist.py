from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import courtvision.sports.mlb.data.historical_temporal_backtest as temporal
from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.training.hr_feature_allowlist import (
    MLBHRFeatureAvailability,
    MLBHRFeatureFieldClass,
    MLBHRFeaturePackRow,
    MLBHRResearchFeaturePack,
    validate_mlb_hr_feature_names,
    validate_mlb_hr_feature_pack,
)


GAME_DATE = date(2024, 6, 10)
ODDS_SNAPSHOT = datetime(2024, 6, 10, 22, 0, tzinfo=timezone.utc)
GAME_START = datetime(2024, 6, 10, 23, 0, tzinfo=timezone.utc)


def _feature_pack(
    *,
    feature_names: tuple[str, ...],
    availability: tuple[MLBHRFeatureAvailability, ...],
) -> MLBHRResearchFeaturePack:
    return MLBHRResearchFeaturePack(
        feature_names=feature_names,
        rows=(
            MLBHRFeaturePackRow(
                row_id="2024-06-10-game-1-player-1",
                game_date=GAME_DATE,
                odds_collected_at=ODDS_SNAPSHOT,
                event_start_time=GAME_START,
                feature_availability=availability,
            ),
        ),
    )


def test_valid_allowed_feature_set_passes() -> None:
    pack = _feature_pack(
        feature_names=(
            "hitter_recent_hr_rate",
            "weather_temperature",
            "american_odds",
        ),
        availability=(
            MLBHRFeatureAvailability(
                "hitter_recent_hr_rate",
                datetime(2024, 6, 10, 20, 0, tzinfo=timezone.utc),
                source_latest_game_date=date(2024, 6, 9),
            ),
            MLBHRFeatureAvailability(
                "weather_temperature",
                datetime(2024, 6, 10, 21, 30, tzinfo=timezone.utc),
            ),
            MLBHRFeatureAvailability("american_odds", ODDS_SNAPSHOT),
        ),
    )

    result = validate_mlb_hr_feature_pack(pack)

    assert result.is_valid
    assert dict(result.classifications) == {
        "hitter_recent_hr_rate": (
            MLBHRFeatureFieldClass.ALLOWED_HISTORICAL_ROLLING.value
        ),
        "weather_temperature": MLBHRFeatureFieldClass.ALLOWED_PREGAME.value,
        "american_odds": MLBHRFeatureFieldClass.ALLOWED_MARKET.value,
    }


def test_label_as_feature_fails() -> None:
    result = validate_mlb_hr_feature_names(("is_home_run",))

    assert not result.is_valid
    assert result.errors == (
        "label/outcome field cannot be used as a feature: is_home_run",
    )


def test_postgame_column_fails() -> None:
    result = validate_mlb_hr_feature_names(("launch_speed",))

    assert not result.is_valid
    assert result.errors == (
        "forbidden leakage field cannot be used as a feature: launch_speed",
    )


def test_final_score_column_fails() -> None:
    result = validate_mlb_hr_feature_names(("final_score",))

    assert not result.is_valid
    assert result.errors == (
        "forbidden leakage field cannot be used as a feature: final_score",
    )


def test_unknown_column_fails() -> None:
    result = validate_mlb_hr_feature_names(("hitter_magic_index",))

    assert not result.is_valid
    assert result.errors == (
        "unknown feature column is not explicitly allowlisted: hitter_magic_index",
    )


@pytest.mark.parametrize(
    ("available_at", "expected_error"),
    (
        (
            datetime(2024, 6, 10, 22, 1, tzinfo=timezone.utc),
            "timestamped after the odds snapshot",
        ),
        (
            GAME_START,
            "timestamped at or after game start",
        ),
    ),
)
def test_late_timestamp_fails(
    available_at: datetime,
    expected_error: str,
) -> None:
    pack = _feature_pack(
        feature_names=("weather_temperature",),
        availability=(
            MLBHRFeatureAvailability("weather_temperature", available_at),
        ),
    )

    result = validate_mlb_hr_feature_pack(pack)

    assert not result.is_valid
    assert any(expected_error in error for error in result.errors)


def test_same_day_outcome_cannot_feed_historical_rolling_feature() -> None:
    pack = _feature_pack(
        feature_names=("hitter_recent_hr_rate",),
        availability=(
            MLBHRFeatureAvailability(
                "hitter_recent_hr_rate",
                datetime(2024, 6, 10, 21, 0, tzinfo=timezone.utc),
                source_latest_game_date=GAME_DATE,
            ),
        ),
    )

    result = validate_mlb_hr_feature_pack(pack)

    assert not result.is_valid
    assert any("same-day or future outcomes" in error for error in result.errors)


def test_dry_run_temporal_backtest_refuses_leaked_feature_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = tmp_path / "leaked_pack"
    pack_dir.mkdir()
    monkeypatch.setattr(
        temporal,
        "preflight_historical_input_pack",
        lambda _pack_dir: SimpleNamespace(is_valid=True, errors=()),
    )
    monkeypatch.setattr(
        temporal,
        "audit_historical_backtest_readiness",
        lambda _pack_dir: SimpleNamespace(
            preflight_valid=True,
            verdict=(
                HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
            ),
            possible_leakage_columns=(),
        ),
    )
    monkeypatch.setattr(
        temporal,
        "_read_unique_game_dates",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("split dates must not be read after feature leakage")
        ),
    )
    leaked = _feature_pack(
        feature_names=("is_home_run",),
        availability=(MLBHRFeatureAvailability("is_home_run", ODDS_SNAPSHOT),),
    )

    result = temporal.dry_run_historical_research_backtest(
        pack_dir,
        feature_pack=leaked,
    )

    assert not result.split_planned
    assert result.feature_firewall_checked
    assert not result.feature_firewall_valid
    assert result.feature_firewall_errors == (
        "label/outcome field cannot be used as a feature: is_home_run",
    )
    assert result.refusal_reasons == (
        "feature leakage firewall rejected pack: label/outcome field cannot be used "
        "as a feature: is_home_run",
    )
