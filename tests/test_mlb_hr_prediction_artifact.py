from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pytest

from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION,
    FROZEN_PREDICTION_ARTIFACT_TYPE,
    IMMUTABLE_WRITE_POLICY,
    MLBHRFrozenPredictionArtifactError,
    MODEL_SPECIFICATION_ID,
    PROBABILITY_FIELDS,
    ROW_IDENTITY_KEYS,
    load_frozen_prediction_artifact,
)
import scripts.mlb_dry_run_hr_frozen_predictions as prediction_cli


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_payload_sha256(payload: Mapping[str, object]) -> str:
    content = dict(payload)
    content.pop("artifact_sha256", None)
    encoded = json.dumps(
        content,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    payload["artifact_sha256"] = _canonical_payload_sha256(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _build_inputs(
    tmp_path: Path,
    *,
    model_specification_path: Path | None = None,
) -> tuple[tuple[Path, Path, Path, Path], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    feature_pack = tmp_path / "feature_pack.json"
    feature_pack.write_text('{"sealed_labels":"not_opened"}\n', encoding="utf-8")
    temporal_plan = tmp_path / "temporal_plan.json"
    temporal_plan.write_text(
        json.dumps(
            {
                "validation": {
                    "game_dates": ["2024-07-01", "2024-07-02"]
                },
                "test": {"game_dates": ["2024-07-03", "2024-07-04"]},
            }
        ),
        encoding="utf-8",
    )
    fitted = tmp_path / "fitted_preprocessing.json"
    fitted.write_text('{"sealed":"train_only"}\n', encoding="utf-8")
    model_spec = model_specification_path or tmp_path / "model_spec.md"
    if model_specification_path is None:
        model_spec.write_text("# Frozen model specification\n", encoding="utf-8")

    code_version = "reviewed-commit-0123456789abcdef"
    payload: dict[str, object] = {
        "schema_version": FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION,
        "artifact_type": FROZEN_PREDICTION_ARTIFACT_TYPE,
        "mode": "historical_research",
        "research_only": True,
        "approval_status": "not_approved",
        "production_approved": False,
        "operational_use_enabled": False,
        "model_training_enabled": False,
        "prediction_generation_enabled": False,
        "evaluation_enabled": False,
        "live_fetching_enabled": False,
        "evaluation_data_sealed": True,
        "immutable": True,
        "write_policy": IMMUTABLE_WRITE_POLICY,
        "feature_pack_sha256": _sha256(feature_pack),
        "temporal_split_plan_sha256": _sha256(temporal_plan),
        "fitted_preprocessing_artifact_sha256": _sha256(fitted),
        "model_specification_id": MODEL_SPECIFICATION_ID,
        "model_specification_sha256": _sha256(model_spec),
        "code_version": code_version,
        "code_version_sha256": hashlib.sha256(
            code_version.encode("utf-8")
        ).hexdigest(),
        "split_id": "validation",
        "window_id": "validation:2024-07-01:2024-07-02",
        "prediction_timestamp": "2024-06-30T18:00:00+00:00",
        "row_identity_keys": list(ROW_IDENTITY_KEYS),
        "probability_fields": list(PROBABILITY_FIELDS),
        "probability_minimum": 0.0,
        "probability_maximum": 1.0,
        "rows": [
            {
                "row_id": "validation-row-001",
                "game_date": "2024-07-01",
                "game_id": "game-001",
                "player_id": "player-001",
                "raw_home_run_probability": 0.21,
                "calibrated_home_run_probability": 0.19,
            },
            {
                "row_id": "validation-row-002",
                "game_date": "2024-07-02",
                "game_id": "game-002",
                "player_id": "player-002",
                "raw_home_run_probability": 0.08,
                "calibrated_home_run_probability": 0.1,
            },
        ],
        "artifact_sha256": "pending",
    }
    prediction = tmp_path / "frozen_predictions.json"
    _write_payload(prediction, payload)
    return (feature_pack, temporal_plan, fitted, prediction), model_spec


def _load(
    paths: tuple[Path, Path, Path, Path],
    model_spec: Path,
):
    feature_pack, temporal_plan, fitted, prediction = paths
    return load_frozen_prediction_artifact(
        prediction,
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
        model_specification_path=model_spec,
    )


def _mutate_prediction(
    prediction: Path,
    mutation,
) -> None:
    payload = json.loads(prediction.read_text(encoding="utf-8"))
    mutation(payload)
    _write_payload(prediction, payload)


def _snapshot(root: Path) -> tuple[tuple[str, str, int, int], ...]:
    return tuple(
        sorted(
            (
                str(path.relative_to(root)),
                _sha256(path),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in root.rglob("*")
            if path.is_file()
        )
    )


def test_valid_prediction_artifact_passes(tmp_path: Path) -> None:
    paths, model_spec = _build_inputs(tmp_path)

    artifact = _load(paths, model_spec)

    assert artifact.schema_version == FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION
    assert artifact.split_id == "validation"
    assert artifact.window_id == "validation:2024-07-01:2024-07-02"
    assert len(artifact.rows) == 2
    assert artifact.evaluation_data_sealed
    assert not artifact.production_approved
    assert not artifact.evaluation_enabled


def test_label_column_fails(tmp_path: Path) -> None:
    paths, model_spec = _build_inputs(tmp_path)
    prediction = paths[3]
    _mutate_prediction(
        prediction,
        lambda payload: payload["rows"][0].update({"is_home_run": False}),
    )

    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match="prohibited label/outcome/final-score fields: is_home_run",
    ):
        _load(paths, model_spec)


@pytest.mark.parametrize(
    "field_name",
    ("ev", "kelly_fraction", "stake_units", "eligible_for_betting"),
)
def test_ev_kelly_staking_or_betting_column_fails(
    tmp_path: Path,
    field_name: str,
) -> None:
    paths, model_spec = _build_inputs(tmp_path)
    prediction = paths[3]
    _mutate_prediction(
        prediction,
        lambda payload: payload["rows"][0].update({field_name: 0}),
    )

    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match="prohibited EV/Kelly/staking/betting fields",
    ):
        _load(paths, model_spec)


def test_duplicate_row_identity_fails(tmp_path: Path) -> None:
    paths, model_spec = _build_inputs(tmp_path)
    prediction = paths[3]

    def duplicate(payload: dict[str, object]) -> None:
        payload["rows"].append(dict(payload["rows"][0]))

    _mutate_prediction(prediction, duplicate)

    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match="duplicates a row identity",
    ):
        _load(paths, model_spec)


@pytest.mark.parametrize("probability", (-0.01, 1.01))
def test_probability_outside_closed_unit_interval_fails(
    tmp_path: Path,
    probability: float,
) -> None:
    paths, model_spec = _build_inputs(tmp_path)
    prediction = paths[3]
    _mutate_prediction(
        prediction,
        lambda payload: payload["rows"][0].update(
            {"calibrated_home_run_probability": probability}
        ),
    )

    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match=r"numeric and within \[0, 1\]",
    ):
        _load(paths, model_spec)


def test_input_hash_mismatch_fails(tmp_path: Path) -> None:
    paths, model_spec = _build_inputs(tmp_path)
    prediction = paths[3]
    _mutate_prediction(
        prediction,
        lambda payload: payload.update({"feature_pack_sha256": "0" * 64}),
    )

    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match="input hash mismatch: feature_pack_sha256",
    ):
        _load(paths, model_spec)


def test_unsupported_schema_and_enabled_production_gate_fail(
    tmp_path: Path,
) -> None:
    paths, model_spec = _build_inputs(tmp_path)
    prediction = paths[3]
    _mutate_prediction(
        prediction,
        lambda payload: payload.update({"schema_version": "future-v2"}),
    )
    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match="schema_version is unsupported",
    ):
        _load(paths, model_spec)

    paths, model_spec = _build_inputs(tmp_path / "enabled")
    prediction = paths[3]
    _mutate_prediction(
        prediction,
        lambda payload: payload.update({"production_approved": True}),
    )
    with pytest.raises(
        MLBHRFrozenPredictionArtifactError,
        match="cannot enable production or execution gates",
    ):
        _load(paths, model_spec)


def test_dry_run_validates_predictions_before_handoff_and_mutates_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, _ = _build_inputs(
        tmp_path,
        model_specification_path=DEFAULT_MODEL_SPECIFICATION_PATH,
    )
    feature_pack, temporal_plan, fitted, prediction = paths
    for relative in ("outputs", "test_outputs", "data/history", "runtime"):
        root = tmp_path / relative
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    expected_hashes = (
        _sha256(feature_pack),
        _sha256(temporal_plan),
        _sha256(fitted),
    )
    calls: list[str] = []
    real_loader = prediction_cli.load_frozen_prediction_artifact

    def tracked_loader(*args, **kwargs):
        calls.append("prediction")
        return real_loader(*args, **kwargs)

    def fake_runner(**kwargs):
        calls.append("runner")
        return SimpleNamespace(
            status="BACKTEST_EXECUTION_PLAN_ONLY",
            feature_pack_sha256=expected_hashes[0],
            temporal_split_plan_sha256=expected_hashes[1],
            fitted_preprocessing_artifact_sha256=expected_hashes[2],
        )

    def fake_handoff(**kwargs):
        calls.append("handoff")
        return SimpleNamespace(
            status="LABEL_HANDOFF_PLAN_ONLY",
            feature_pack_sha256=expected_hashes[0],
            temporal_split_plan_sha256=expected_hashes[1],
            fitted_preprocessing_artifact_sha256=expected_hashes[2],
            phases=(
                SimpleNamespace(
                    name="validation_evaluation_after_predictions_frozen",
                    validation="evaluation_only",
                    predictions_frozen=True,
                ),
            ),
            label_values_exposed=False,
            artifacts_written=False,
        )

    monkeypatch.setattr(
        prediction_cli, "load_frozen_prediction_artifact", tracked_loader
    )
    monkeypatch.setattr(
        prediction_cli, "plan_sealed_mlb_hr_research_backtest", fake_runner
    )
    monkeypatch.setattr(
        prediction_cli, "validate_mlb_hr_label_handoff", fake_handoff
    )
    monkeypatch.chdir(tmp_path)

    assert prediction_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
            "--prediction-artifact",
            str(prediction),
        )
    ) == 0
    output = capsys.readouterr().out

    assert calls == ["prediction", "runner", "handoff"]
    assert "prediction_artifact_validated_before_label_handoff: PASSED" in output
    assert "evaluation_data_sealed_during_prediction_validation: true" in output
    assert "writes_performed: false" in output
    assert _snapshot(tmp_path) == before
