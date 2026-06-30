from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest

from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION,
    FROZEN_PREDICTION_ARTIFACT_TYPE,
    IMMUTABLE_WRITE_POLICY,
    MODEL_SPECIFICATION_ID,
    PROBABILITY_FIELDS,
    ROW_IDENTITY_KEYS,
    load_frozen_prediction_artifact,
)
from courtvision.sports.mlb.training.hr_test_evaluation_access import (
    APPROVE_TEST_LABEL_ACCESS_REVIEW,
    DENY_TEST_LABEL_ACCESS,
    VALIDATION_PROMOTION_AUDIT_RESULT_SCHEMA_VERSION,
    audit_mlb_hr_test_evaluation_access,
    validation_promotion_audit_result_sha256,
)
from courtvision.sports.mlb.training.hr_validation_promotion import (
    PROMOTE_TO_TEST_REVIEW,
    VALIDATION_ACCEPTANCE_POLICY_VERSION,
    audit_mlb_hr_validation_promotion,
    pipeline_sha256,
    validation_result_sha256,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    build_label_custody_payload,
)
import scripts.mlb_audit_hr_test_access as test_access_cli
from tests.test_mlb_hr_validation_promotion import _strong_results


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_prediction_sha256(payload: Mapping[str, object]) -> str:
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


def _prediction_payload(
    *,
    feature_pack: Path,
    split_plan: Path,
    preprocessing: Path,
    split_id: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    code_version = "reviewed-commit-0123456789abcdef"
    bounds = {
        "validation": ("2024-07-01", "2024-07-02"),
        "test": ("2024-07-03", "2024-07-04"),
    }[split_id]
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
        "temporal_split_plan_sha256": _sha256(split_plan),
        "fitted_preprocessing_artifact_sha256": _sha256(preprocessing),
        "model_specification_id": MODEL_SPECIFICATION_ID,
        "model_specification_sha256": _sha256(DEFAULT_MODEL_SPECIFICATION_PATH),
        "code_version": code_version,
        "code_version_sha256": hashlib.sha256(
            code_version.encode("utf-8")
        ).hexdigest(),
        "split_id": split_id,
        "window_id": f"{split_id}:{bounds[0]}:{bounds[1]}",
        "prediction_timestamp": "2024-06-30T18:00:00+00:00",
        "row_identity_keys": list(ROW_IDENTITY_KEYS),
        "probability_fields": list(PROBABILITY_FIELDS),
        "probability_minimum": 0.0,
        "probability_maximum": 1.0,
        "rows": rows,
        "artifact_sha256": "pending",
    }
    payload["artifact_sha256"] = _canonical_prediction_sha256(payload)
    return payload


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _receipt(
    *,
    feature_pack: Path,
    split_plan: Path,
    preprocessing: Path,
    validation_prediction: Path,
    validation_results: Path,
) -> dict[str, object]:
    decision = audit_mlb_hr_validation_promotion(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=split_plan,
        fitted_preprocessing_artifact_path=preprocessing,
        prediction_artifact_path=validation_prediction,
        validation_results=validation_results,
    )
    artifact = load_frozen_prediction_artifact(
        validation_prediction,
        feature_pack_path=feature_pack,
        temporal_split_plan_path=split_plan,
        fitted_preprocessing_artifact_path=preprocessing,
    )
    results = json.loads(validation_results.read_text(encoding="utf-8"))
    payload: dict[str, object] = {
        "schema_version": VALIDATION_PROMOTION_AUDIT_RESULT_SCHEMA_VERSION,
        "acceptance_policy_version": VALIDATION_ACCEPTANCE_POLICY_VERSION,
        "verdict": decision.verdict,
        "failures": list(decision.failures),
        "research_only": True,
        "approval_status": "not_approved",
        "immutable": True,
        "feature_pack_sha256": _sha256(feature_pack),
        "temporal_split_plan_sha256": _sha256(split_plan),
        "fitted_preprocessing_artifact_sha256": _sha256(preprocessing),
        "validation_prediction_file_sha256": _sha256(validation_prediction),
        "validation_prediction_artifact_sha256": artifact.artifact_sha256,
        "validation_result_file_sha256": _sha256(validation_results),
        "validation_result_sha256": results["validation_result_sha256"],
        "model_specification_sha256": artifact.model_specification_sha256,
        "code_version_sha256": artifact.code_version_sha256,
        "pipeline_sha256": pipeline_sha256(artifact),
        "test_labels_sealed": decision.test_labels_sealed,
        "test_labels_opened": False,
        "test_metrics_computed": False,
        "writes_performed": False,
        "test_label_access_authorized": False,
        "test_evaluation_authorized": False,
        "production_approved": False,
        "operational_use_enabled": False,
        "eligible_for_betting": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
        "audit_result_sha256": "pending",
    }
    payload["audit_result_sha256"] = (
        validation_promotion_audit_result_sha256(payload)
    )
    return payload


def _build_case(tmp_path: Path) -> dict[str, Path]:
    feature_pack = tmp_path / "feature_pack.json"
    label_values = (True, False, False, True)
    feature_rows = [
        {
            "row_id": "validation-row-001",
            "game_date": "2024-07-01",
            "game_id": "game-001",
            "player_id": "player-001",
        },
        {
            "row_id": "validation-row-002",
            "game_date": "2024-07-02",
            "game_id": "game-002",
            "player_id": "player-002",
        },
        {
            "row_id": "test-row-001",
            "game_date": "2024-07-03",
            "game_id": "game-003",
            "player_id": "player-003",
        },
        {
            "row_id": "test-row-002",
            "game_date": "2024-07-04",
            "game_id": "game-004",
            "player_id": "player-004",
        },
    ]
    feature_payload = {
            "mode": "historical_research",
            "research_only": True,
            "approval_status": "not_approved",
            "model_training_enabled": False,
            "predictions_enabled": False,
            "backtesting_enabled": False,
            "eligible_for_betting": False,
            "ev_enabled": False,
            "kelly_eligible": False,
            "elite_enabled": False,
            "staking_enabled": False,
            "rows": feature_rows,
        }
    _write_json(feature_pack, feature_payload)
    label_custody = tmp_path / LABEL_CUSTODY_FILENAME
    _write_json(
        label_custody,
        build_label_custody_payload(
            feature_payload=feature_payload,
            feature_pack_sha256=_sha256(feature_pack),
            labels=tuple(
                {"row_id": row["row_id"], "is_home_run": label}
                for row, label in zip(feature_rows, label_values, strict=True)
            ),
            created_at="2026-06-30T00:00:00+00:00",
        ),
    )
    split_plan = tmp_path / "temporal_split_plan.json"
    _write_json(
        split_plan,
        {
            "mode": "historical_research",
            "approval_status": "not_approved",
            "model_training_enabled": False,
            "predictions_enabled": False,
            "eligible_for_betting": False,
            "validation": {"game_dates": ["2024-07-01", "2024-07-02"]},
            "test": {"game_dates": ["2024-07-03", "2024-07-04"]},
        },
    )
    preprocessing = tmp_path / "fitted_preprocessing.json"
    _write_json(
        preprocessing,
        {
            "mode": "historical_research",
            "research_only": True,
            "approval_status": "not_approved",
            "immutable": True,
            "model_training_enabled": False,
            "prediction_generation_enabled": False,
            "evaluation_enabled": False,
            "eligible_for_betting": False,
            "betting_enabled": False,
            "ev_enabled": False,
            "kelly_eligible": False,
            "elite_enabled": False,
            "staking_enabled": False,
        },
    )
    validation_rows = [
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
    ]
    test_rows = [
        {
            "row_id": "test-row-001",
            "game_date": "2024-07-03",
            "game_id": "game-003",
            "player_id": "player-003",
            "raw_home_run_probability": 0.12,
            "calibrated_home_run_probability": 0.11,
        },
        {
            "row_id": "test-row-002",
            "game_date": "2024-07-04",
            "game_id": "game-004",
            "player_id": "player-004",
            "raw_home_run_probability": 0.18,
            "calibrated_home_run_probability": 0.16,
        },
    ]
    validation_prediction = tmp_path / "validation_predictions.json"
    _write_json(
        validation_prediction,
        _prediction_payload(
            feature_pack=feature_pack,
            split_plan=split_plan,
            preprocessing=preprocessing,
            split_id="validation",
            rows=validation_rows,
        ),
    )
    test_prediction = tmp_path / "test_predictions.json"
    _write_json(
        test_prediction,
        _prediction_payload(
            feature_pack=feature_pack,
            split_plan=split_plan,
            preprocessing=preprocessing,
            split_id="test",
            rows=test_rows,
        ),
    )
    validation_results = tmp_path / "validation_results.json"
    _write_json(
        validation_results,
        _strong_results(
            (feature_pack, split_plan, preprocessing, validation_prediction)
        ),
    )
    promotion_result = tmp_path / "validation_promotion_audit_result.json"
    _write_json(
        promotion_result,
        _receipt(
            feature_pack=feature_pack,
            split_plan=split_plan,
            preprocessing=preprocessing,
            validation_prediction=validation_prediction,
            validation_results=validation_results,
        ),
    )
    return {
        "feature_pack": feature_pack,
        "label_custody": label_custody,
        "split_plan": split_plan,
        "preprocessing": preprocessing,
        "validation_prediction": validation_prediction,
        "validation_results": validation_results,
        "promotion_result": promotion_result,
        "test_prediction": test_prediction,
    }


def _audit(paths: Mapping[str, Path]):
    return audit_mlb_hr_test_evaluation_access(
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
        validation_prediction_artifact_path=paths["validation_prediction"],
        validation_results_path=paths["validation_results"],
        validation_promotion_audit_result_path=paths["promotion_result"],
        test_prediction_artifact_path=paths["test_prediction"],
    )


def _rehash_prediction(payload: dict[str, object]) -> None:
    payload["artifact_sha256"] = _canonical_prediction_sha256(payload)


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


def test_valid_evidence_approves_test_label_access_review(tmp_path: Path) -> None:
    decision = _audit(_build_case(tmp_path))

    assert decision.verdict == APPROVE_TEST_LABEL_ACCESS_REVIEW
    assert decision.failures == ()
    assert decision.expected_test_rows == 2
    assert decision.predicted_test_rows == 2
    assert decision.matched_test_rows == 2
    assert decision.test_predictions_frozen
    assert decision.test_labels_sealed
    assert not decision.labels_accessed
    assert not decision.test_metrics_calculated
    assert not decision.test_label_access_authorized
    assert not decision.test_evaluation_authorized
    assert not decision.production_approved
    assert not decision.writes_performed


def test_failed_validation_promotion_denies(tmp_path: Path) -> None:
    paths = _build_case(tmp_path)
    results = json.loads(paths["validation_results"].read_text(encoding="utf-8"))
    results["metrics"]["calibrated_home_run_probability"]["roc_auc"][
        "estimate"
    ] = 0.54
    results["validation_result_sha256"] = validation_result_sha256(results)
    _write_json(paths["validation_results"], results)

    decision = _audit(paths)

    assert decision.verdict == DENY_TEST_LABEL_ACCESS
    assert any("did not return PROMOTE_TO_TEST_REVIEW" in item for item in decision.failures)


def test_changed_test_pipeline_hash_denies(tmp_path: Path) -> None:
    paths = _build_case(tmp_path)
    test_payload = json.loads(paths["test_prediction"].read_text(encoding="utf-8"))
    test_payload["code_version"] = "different-reviewed-commit"
    test_payload["code_version_sha256"] = hashlib.sha256(
        str(test_payload["code_version"]).encode("utf-8")
    ).hexdigest()
    _rehash_prediction(test_payload)
    _write_json(paths["test_prediction"], test_payload)

    decision = _audit(paths)

    assert decision.verdict == DENY_TEST_LABEL_ACCESS
    assert any("pipeline_sha256" in item for item in decision.failures)


def test_missing_test_prediction_artifact_denies(tmp_path: Path) -> None:
    paths = _build_case(tmp_path)
    paths["test_prediction"] = tmp_path / "missing_test_predictions.json"

    decision = _audit(paths)

    assert decision.verdict == DENY_TEST_LABEL_ACCESS
    assert any("must be an existing local file" in item for item in decision.failures)


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_missing_or_extra_test_rows_deny(tmp_path: Path, mutation: str) -> None:
    paths = _build_case(tmp_path)
    test_payload = json.loads(paths["test_prediction"].read_text(encoding="utf-8"))
    if mutation == "missing":
        test_payload["rows"].pop()
    else:
        test_payload["rows"].append(
            {
                "row_id": "extra-test-row",
                "game_date": "2024-07-04",
                "game_id": "extra-game",
                "player_id": "extra-player",
                "raw_home_run_probability": 0.1,
                "calibrated_home_run_probability": 0.1,
            }
        )
    _rehash_prediction(test_payload)
    _write_json(paths["test_prediction"], test_payload)

    decision = _audit(paths)

    assert decision.verdict == DENY_TEST_LABEL_ACCESS
    assert any(f"{mutation} rows" in item for item in decision.failures)


@pytest.mark.parametrize("gate", ("betting_enabled", "ev_enabled", "kelly_eligible"))
def test_enabled_betting_ev_or_kelly_gate_denies(
    tmp_path: Path, gate: str
) -> None:
    paths = _build_case(tmp_path)
    preprocessing = json.loads(
        paths["preprocessing"].read_text(encoding="utf-8")
    )
    preprocessing[gate] = True
    _write_json(paths["preprocessing"], preprocessing)

    decision = _audit(paths)

    assert decision.verdict == DENY_TEST_LABEL_ACCESS
    assert any(gate in item for item in decision.failures)


def test_cli_mutates_no_operational_folder_or_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _build_case(tmp_path)
    for relative in ("outputs", "test_outputs", "runtime", "history", "dashboard"):
        directory = tmp_path / relative
        directory.mkdir()
        (directory / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    assert test_access_cli.main(
        (
            "--feature-pack",
            str(paths["feature_pack"]),
            "--split-plan",
            str(paths["split_plan"]),
            "--preprocessing-artifact",
            str(paths["preprocessing"]),
            "--validation-prediction-artifact",
            str(paths["validation_prediction"]),
            "--validation-results",
            str(paths["validation_results"]),
            "--validation-promotion-audit-result",
            str(paths["promotion_result"]),
            "--test-prediction-artifact",
            str(paths["test_prediction"]),
        )
    ) == 0
    output = capsys.readouterr().out

    assert output.rstrip().endswith(APPROVE_TEST_LABEL_ACCESS_REVIEW)
    assert "labels_accessed: false" in output
    assert "test_metrics_calculated: false" in output
    assert "writes_performed: false" in output
    assert _snapshot(tmp_path) == before
