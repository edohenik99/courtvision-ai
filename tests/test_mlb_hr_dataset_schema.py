from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date, datetime, timezone
import json

import pytest

from courtvision.sports.mlb.training.hr_dataset_schema import (
    FORBIDDEN_DECISION_FIELD_NAMES,
    MLB_HR_DATASET_SCHEMA_VERSION,
    OUTCOME_LABEL_FIELD_NAMES,
    PREGAME_FEATURE_FIELD_NAMES,
    MLBHRBatterGameRow,
    MLBHRDatasetManifest,
    MLBHRDatasetMetadata,
    MLBHRDatasetSchemaError,
    assert_feature_as_of_before_game,
    assert_no_label_leakage,
    dataset_row_id,
    manifest_to_json,
    metadata_to_json,
    row_feature_dict,
    row_label_dict,
    row_to_dict,
    row_to_json,
    rows_to_csv_dicts,
    validate_batter_game_row,
    validate_dataset_manifest,
    validate_dataset_metadata,
)


GAME_START = datetime(2025, 7, 4, 23, 10, tzinfo=timezone.utc)
FEATURE_AS_OF = datetime(2025, 7, 4, 21, 0, tzinfo=timezone.utc)


def _row(**overrides: object) -> MLBHRBatterGameRow:
    game_id = str(overrides.get("game_id", "2025-07-04-TOR-NYY-1"))
    player_id = overrides.get("player_id", "batter-42")
    initial_row_id = (
        "invalid-identity-row"
        if not game_id or player_id in (None, "")
        else dataset_row_id(game_id, player_id)
    )
    values: dict[str, object] = {
        "row_id": initial_row_id,
        "game_id": game_id,
        "game_date": date(2025, 7, 4),
        "event_start_time": GAME_START,
        "season": 2025,
        "game_number": 1,
        "player_id": player_id,
        "player_name": "Example Batter",
        "team": "TOR",
        "opponent": "NYY",
        "home_team": "NYY",
        "away_team": "TOR",
        "is_home_team": False,
        "venue_name": "Example Park",
        "batting_order": 2,
        "lineup_status": "confirmed",
        "probable_pitcher_id": "pitcher-7",
        "probable_pitcher_name": "Example Pitcher",
        "probable_pitcher_team": "NYY",
        "probable_pitcher_status": "probable",
        "feature_as_of": FEATURE_AS_OF,
        "source_manifest_ids": ("statcast-2025", "retrosheet-2025"),
        "data_quality": "incomplete_optional_features",
        "leakage_check_status": "passed",
    }
    values.update(overrides)
    if (
        "game_id" in overrides
        and "row_id" not in overrides
        and values["game_id"] not in (None, "")
    ):
        values["row_id"] = dataset_row_id(values["game_id"], values["player_id"])
    if "player_id" in overrides and "row_id" not in overrides:
        candidate = values["player_id"]
        values["row_id"] = (
            "invalid-player-row" if candidate in (None, "") else dataset_row_id(values["game_id"], candidate)
        )
    return MLBHRBatterGameRow(**values)  # type: ignore[arg-type]


def _metadata(**overrides: object) -> MLBHRDatasetMetadata:
    values: dict[str, object] = {
        "dataset_id": "mlb-hr-2025-research-v1",
        "generated_at": datetime(2026, 6, 21, 14, 0, tzinfo=timezone.utc),
        "date_range_start": date(2025, 3, 27),
        "date_range_end": date(2025, 9, 28),
        "source_manifest_ids": ("statcast-2025", "retrosheet-2025"),
        "row_count": 1,
        "generated_by": "pytest-schema-contract",
        "mode": "historical",
    }
    values.update(overrides)
    return MLBHRDatasetMetadata(**values)  # type: ignore[arg-type]


def test_valid_row_is_immutable_and_validates_with_optional_feature_warnings() -> None:
    row = _row()

    with pytest.raises(FrozenInstanceError):
        row.game_id = "changed"  # type: ignore[misc]

    result = validate_batter_game_row(row)
    assert result.is_valid
    assert any("optional pregame features missing" in warning for warning in result.warnings)
    assert row.data_quality == "incomplete_optional_features"


@pytest.mark.parametrize(
    ("overrides", "expected_error"),
    [
        ({"game_id": ""}, "game_id is required"),
        ({"player_id": None}, "player_id is required"),
        ({"sport": "NBA"}, "sport must be 'MLB'"),
        ({"lineup_status": "maybe"}, "unsupported lineup_status"),
        (
            {"probable_pitcher_status": "starterish"},
            "unsupported probable_pitcher_status",
        ),
        ({"game_date": "07/04/2025"}, "ISO date format"),
    ],
)
def test_invalid_required_identity_or_status_fails_closed(
    overrides: dict[str, object], expected_error: str
) -> None:
    result = validate_batter_game_row(_row(**overrides))

    assert not result.is_valid
    assert any(expected_error in error for error in result.errors)


def test_feature_as_of_must_be_strictly_before_event_start() -> None:
    before = _row(feature_as_of="2025-07-04T21:00:00+00:00")
    after = replace(before, feature_as_of="2025-07-04T23:11:00+00:00")

    assert validate_batter_game_row(before).is_valid
    assert_feature_as_of_before_game(before)
    assert not validate_batter_game_row(after).is_valid
    with pytest.raises(MLBHRDatasetSchemaError, match="must be before"):
        assert_feature_as_of_before_game(after)


def test_row_id_helper_is_deterministic_and_input_sensitive() -> None:
    first = dataset_row_id("game-1", "player-1")

    assert first == dataset_row_id("game-1", "player-1")
    assert first != dataset_row_id("game-1", "player-2")
    assert len(first) == 64


def test_feature_and_label_namespaces_are_disjoint_and_serialize_separately() -> None:
    row = _row(hit_hr_today=True, home_run_count=1, label_available=True)
    feature_payload = row_feature_dict(row)
    label_payload = row_label_dict(row)

    assert set(PREGAME_FEATURE_FIELD_NAMES).isdisjoint(OUTCOME_LABEL_FIELD_NAMES)
    assert set(feature_payload).isdisjoint(OUTCOME_LABEL_FIELD_NAMES)
    assert set(label_payload) == set(OUTCOME_LABEL_FIELD_NAMES)
    assert label_payload["hit_hr_today"] is True
    assert_no_label_leakage(row)


def test_label_key_embedded_in_pitch_mix_feature_fails_leakage_check() -> None:
    row = _row(pitcher_pitch_mix_json='{"four_seam": 0.55, "hit_hr_today": true}')

    with pytest.raises(MLBHRDatasetSchemaError, match="outcome label"):
        assert_no_label_leakage(row)
    assert not validate_batter_game_row(row).is_valid


@pytest.mark.parametrize(
    "overrides",
    [
        {"eligible_for_betting": True},
        {"kelly_eligible": True},
        {"approval_status": "approved"},
    ],
)
def test_row_cannot_claim_wagering_or_production_approval(
    overrides: dict[str, object]
) -> None:
    row = _row(**overrides)

    assert not validate_batter_game_row(row).is_valid
    with pytest.raises(MLBHRDatasetSchemaError):
        row_to_dict(row)


def test_schema_contains_no_decision_or_sizing_fields() -> None:
    row_fields = {item.name for item in fields(MLBHRBatterGameRow)}

    assert row_fields.isdisjoint(FORBIDDEN_DECISION_FIELD_NAMES)
    assert "implied_probability" in row_fields
    assert "fair_probability" not in row_fields


def test_row_json_and_csv_serialization_are_deterministic() -> None:
    row = _row(
        pitcher_pitch_mix_json={"slider": 0.35, "four_seam": 0.5},
        warnings=("Research row only.",),
    )

    assert row_to_json(row) == row_to_json(row)
    assert json.loads(row_to_json(row)) == row_to_dict(row)
    first_csv = rows_to_csv_dicts((row,))
    second_csv = rows_to_csv_dicts((row,))
    assert first_csv == second_csv
    assert list(first_csv[0]) == [item.name for item in fields(MLBHRBatterGameRow)]
    assert first_csv[0]["pitcher_pitch_mix_json"] == '{"four_seam":0.5,"slider":0.35}'


def test_metadata_is_valid_and_serializes_deterministically() -> None:
    metadata = _metadata()

    assert metadata.schema_version == MLB_HR_DATASET_SCHEMA_VERSION
    assert validate_dataset_metadata(metadata).is_valid
    assert metadata_to_json(metadata) == metadata_to_json(metadata)
    assert json.loads(metadata_to_json(metadata))["approval_status"] == "not_approved"


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "production"},
        {"approval_status": "approved"},
        {"eligible_for_betting": True},
        {"kelly_eligible": True},
    ],
)
def test_metadata_cannot_claim_production_or_wagering_approval(
    overrides: dict[str, object]
) -> None:
    metadata = _metadata(**overrides)

    assert not validate_dataset_metadata(metadata).is_valid
    with pytest.raises(MLBHRDatasetSchemaError):
        metadata_to_json(metadata)


def test_manifest_exposes_canonical_feature_label_boundary() -> None:
    row = _row()
    metadata = _metadata()
    manifest = MLBHRDatasetManifest(metadata=metadata, row_ids=(row.row_id,))

    assert validate_dataset_manifest(manifest).is_valid
    payload = json.loads(manifest_to_json(manifest))
    assert payload["feature_field_names"] == list(PREGAME_FEATURE_FIELD_NAMES)
    assert payload["label_field_names"] == list(OUTCOME_LABEL_FIELD_NAMES)
    assert set(payload["feature_field_names"]).isdisjoint(payload["label_field_names"])
