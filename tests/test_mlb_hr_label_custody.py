from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    MLBHRLabelCustodyError,
    MLBHRLabelOpeningAuthorization,
    build_label_custody_payload,
    feature_row_identity_sha256,
    open_mlb_hr_label_custody_split,
    validate_mlb_hr_label_custody,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    rows = [
        {
            "row_id": "train-row",
            "game_date": "2024-07-01",
            "game_id": "game-1",
            "player_id": "player-1",
            "feature_values": {"weather_temperature": 70.0},
        },
        {
            "row_id": "validation-row",
            "game_date": "2024-07-02",
            "game_id": "game-2",
            "player_id": "player-2",
            "feature_values": {"weather_temperature": 71.0},
        },
        {
            "row_id": "test-row",
            "game_date": "2024-07-03",
            "game_id": "game-3",
            "player_id": "player-3",
            "feature_values": {"weather_temperature": 72.0},
        },
    ]
    feature_payload = {
        "mode": "historical_research",
        "feature_names": ["weather_temperature"],
        "rows": rows,
        "approval_status": "not_approved",
    }
    feature_pack = tmp_path / "feature_pack.json"
    _write(feature_pack, feature_payload)
    custody = tmp_path / LABEL_CUSTODY_FILENAME
    _write(
        custody,
        build_label_custody_payload(
            feature_payload=feature_payload,
            feature_pack_sha256=_sha256(feature_pack),
            labels=(
                {"row_id": "train-row", "is_home_run": False},
                {"row_id": "validation-row", "is_home_run": True},
                {"row_id": "test-row", "is_home_run": False},
            ),
            created_at="2026-06-30T00:00:00+00:00",
        ),
    )
    split_plan = tmp_path / "split_plan.json"
    _write(
        split_plan,
        {
            "train": {"game_dates": ["2024-07-01"]},
            "validation": {"game_dates": ["2024-07-02"]},
            "test": {"game_dates": ["2024-07-03"]},
        },
    )
    return feature_pack, custody, split_plan


def test_model_visible_feature_pack_has_no_labels(tmp_path: Path) -> None:
    feature_pack, _, _ = _inputs(tmp_path)
    payload = json.loads(feature_pack.read_text(encoding="utf-8"))

    assert "is_home_run" not in feature_pack.read_text(encoding="utf-8")
    assert all("is_home_run" not in row for row in payload["rows"])


def test_labels_verify_by_feature_row_identity_hash(tmp_path: Path) -> None:
    feature_pack, custody, _ = _inputs(tmp_path)
    feature_payload = json.loads(feature_pack.read_text(encoding="utf-8"))
    custody_payload = json.loads(custody.read_text(encoding="utf-8"))

    binding = validate_mlb_hr_label_custody(
        feature_pack_path=feature_pack,
        label_custody_path=custody,
    )

    assert binding.row_identity_sha256 == feature_row_identity_sha256(feature_payload)
    assert custody_payload["feature_pack_row_identity_sha256"] == (
        binding.row_identity_sha256
    )


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_missing_or_extra_label_rows_fail(tmp_path: Path, mutation: str) -> None:
    feature_pack, custody, _ = _inputs(tmp_path)
    payload = json.loads(custody.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload["rows"].pop()
    else:
        payload["rows"].append({"row_id": "extra-row", "is_home_run": False})
    _write(custody, payload)

    with pytest.raises(MLBHRLabelCustodyError, match="label rows"):
        validate_mlb_hr_label_custody(
            feature_pack_path=feature_pack,
            label_custody_path=custody,
        )


def test_validation_labels_require_frozen_prediction_authority(tmp_path: Path) -> None:
    feature_pack, custody, split_plan = _inputs(tmp_path)
    with pytest.raises(MLBHRLabelCustodyError, match="validated opening"):
        open_mlb_hr_label_custody_split(
            feature_pack_path=feature_pack,
            label_custody_path=custody,
            temporal_split_plan_path=split_plan,
            authorization=None,
        )

    opened = open_mlb_hr_label_custody_split(
        feature_pack_path=feature_pack,
        label_custody_path=custody,
        temporal_split_plan_path=split_plan,
        authorization=MLBHRLabelOpeningAuthorization(
            split="validation",
            reason="frozen_prediction_validation",
            expected_row_ids=("validation-row",),
            frozen_prediction_artifact_sha256="a" * 64,
        ),
    )
    assert [(row.row_id, row.is_home_run) for row in opened] == [
        ("validation-row", True)
    ]


def test_test_labels_require_explicit_approval_authority(tmp_path: Path) -> None:
    feature_pack, custody, split_plan = _inputs(tmp_path)
    with pytest.raises(MLBHRLabelCustodyError, match="required opening authority"):
        MLBHRLabelOpeningAuthorization(
            split="test",
            reason="approved_one_shot_test_handoff",
            expected_row_ids=("test-row",),
            frozen_prediction_artifact_sha256="b" * 64,
        )

    opened = open_mlb_hr_label_custody_split(
        feature_pack_path=feature_pack,
        label_custody_path=custody,
        temporal_split_plan_path=split_plan,
        authorization=MLBHRLabelOpeningAuthorization(
            split="test",
            reason="approved_one_shot_test_handoff",
            expected_row_ids=("test-row",),
            frozen_prediction_artifact_sha256="b" * 64,
            approval_receipt_sha256="c" * 64,
        ),
    )
    assert [row.row_id for row in opened] == ["test-row"]


def test_custody_validation_and_opening_mutate_no_operational_folder(
    tmp_path: Path,
) -> None:
    feature_pack, custody, split_plan = _inputs(tmp_path)
    operational = [tmp_path / name for name in ("outputs", "test_outputs", "runtime")]
    for folder in operational:
        folder.mkdir()
        (folder / "sentinel.txt").write_text("preserve", encoding="utf-8")
    before = {
        path: tuple((item.name, item.read_bytes()) for item in path.iterdir())
        for path in operational
    }

    validate_mlb_hr_label_custody(
        feature_pack_path=feature_pack,
        label_custody_path=custody,
    )
    open_mlb_hr_label_custody_split(
        feature_pack_path=feature_pack,
        label_custody_path=custody,
        temporal_split_plan_path=split_plan,
        authorization=MLBHRLabelOpeningAuthorization(
            split="validation",
            reason="frozen_prediction_validation",
            expected_row_ids=("validation-row",),
            frozen_prediction_artifact_sha256="d" * 64,
        ),
    )

    assert {
        path: tuple((item.name, item.read_bytes()) for item in path.iterdir())
        for path in operational
    } == before
