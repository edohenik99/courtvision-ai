from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
)
from courtvision.sports.mlb.training.hr_one_shot_test_evaluator import (
    APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF,
    ONE_SHOT_TEST_EVALUATION_APPROVAL_SCOPE,
    ONE_SHOT_TEST_EVALUATION_PLAN_ONLY,
    ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION,
    RESULT_ARTIFACT_WRITE_POLICY,
    TEST_ACCESS_APPROVAL_RECEIPT_SCHEMA_VERSION,
    MLBHROneShotTestEvaluatorError,
    plan_one_shot_frozen_mlb_hr_test_evaluation,
    test_access_approval_receipt_sha256 as _approval_receipt_sha256,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    IMMUTABLE_WRITE_POLICY,
    load_frozen_prediction_artifact,
)
from courtvision.sports.mlb.training.hr_test_evaluation_access import (
    APPROVE_TEST_LABEL_ACCESS_REVIEW,
    FROZEN_TEST_EVALUATION_ACCESS_POLICY_VERSION,
)
from courtvision.sports.mlb.training.hr_validation_promotion import (
    pipeline_sha256,
)
import scripts.mlb_plan_hr_one_shot_test_evaluation as planner_cli
from tests.test_mlb_hr_test_evaluation_access import (
    _build_case,
    _rehash_prediction,
    _sha256,
    _snapshot,
    _write_json,
)


def _approval_receipt(paths: Mapping[str, Path]) -> dict[str, object]:
    artifact = load_frozen_prediction_artifact(
        paths["test_prediction"],
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
    )
    frozen_pipeline_hash = pipeline_sha256(artifact)
    payload: dict[str, object] = {
        "schema_version": TEST_ACCESS_APPROVAL_RECEIPT_SCHEMA_VERSION,
        "evaluator_policy_version": ONE_SHOT_TEST_EVALUATOR_POLICY_VERSION,
        "access_policy_version": FROZEN_TEST_EVALUATION_ACCESS_POLICY_VERSION,
        "access_audit_verdict": APPROVE_TEST_LABEL_ACCESS_REVIEW,
        "approval_scope": ONE_SHOT_TEST_EVALUATION_APPROVAL_SCOPE,
        "approval_id": "mlb-hr-test-approval-2026-06-29-001",
        "methodology_approver": "methodology-reviewer-01",
        "operator_approver": "research-operator-02",
        "approved_at": "2026-06-29T14:00:00-04:00",
        "test_access_approved": True,
        "label_handoff_approval": APPROVE_ONE_SHOT_TEST_LABEL_HANDOFF,
        "one_shot_attempt_number": 1,
        "one_shot_attempt_consumed": False,
        "feature_pack_sha256": _sha256(paths["feature_pack"]),
        "temporal_split_plan_sha256": _sha256(paths["split_plan"]),
        "fitted_preprocessing_artifact_sha256": _sha256(
            paths["preprocessing"]
        ),
        "test_prediction_file_sha256": _sha256(paths["test_prediction"]),
        "test_prediction_artifact_sha256": artifact.artifact_sha256,
        "accepted_validation_pipeline_sha256": frozen_pipeline_hash,
        "test_pipeline_sha256": frozen_pipeline_hash,
        "allowed_metrics": list(ALLOWED_EVALUATION_METRIC_NAMES),
        "test_labels_sealed": True,
        "test_labels_opened": False,
        "test_metrics_computed": False,
        "result_artifact_created": False,
        "no_rerun": True,
        "no_cherry_pick": True,
        "report_all_frozen_metrics": True,
        "result_artifact_write_policy": RESULT_ARTIFACT_WRITE_POLICY,
        "research_only": True,
        "approval_status": "not_approved",
        "immutable": True,
        "write_policy": IMMUTABLE_WRITE_POLICY,
        "production_approved": False,
        "operational_use_enabled": False,
        "eligible_for_betting": False,
        "betting_enabled": False,
        "ev_enabled": False,
        "kelly_eligible": False,
        "elite_enabled": False,
        "staking_enabled": False,
        "bankroll_enabled": False,
        "receipt_sha256": "pending",
    }
    payload["receipt_sha256"] = _approval_receipt_sha256(payload)
    return payload


def _build_approved_case(tmp_path: Path) -> dict[str, Path]:
    paths = _build_case(tmp_path)
    receipt = tmp_path / "test_access_approval_receipt.json"
    _write_json(receipt, _approval_receipt(paths))
    return {**paths, "approval_receipt": receipt}


def _plan(paths: Mapping[str, Path]):
    return plan_one_shot_frozen_mlb_hr_test_evaluation(
        feature_pack_path=paths["feature_pack"],
        temporal_split_plan_path=paths["split_plan"],
        fitted_preprocessing_artifact_path=paths["preprocessing"],
        test_prediction_artifact_path=paths["test_prediction"],
        test_access_approval_receipt_path=paths["approval_receipt"],
    )


def _rewrite_receipt(
    paths: Mapping[str, Path],
    mutation=None,
) -> None:
    payload = _approval_receipt(paths)
    if mutation is not None:
        mutation(payload)
    payload["receipt_sha256"] = _approval_receipt_sha256(payload)
    _write_json(paths["approval_receipt"], payload)


def test_valid_approval_creates_one_shot_plan(tmp_path: Path) -> None:
    plan = _plan(_build_approved_case(tmp_path))

    assert plan.status == ONE_SHOT_TEST_EVALUATION_PLAN_ONLY
    assert plan.split_id == "test"
    assert plan.test_access_approved
    assert plan.label_handoff_approved
    assert plan.test_labels_sealed
    assert not plan.labels_accessed
    assert not plan.test_metrics_calculated
    assert plan.population_coverage.expected_rows == 2
    assert plan.population_coverage.matched_rows == 2
    assert plan.policy.allowed_metrics == ALLOWED_EVALUATION_METRIC_NAMES
    assert plan.policy.maximum_attempts == 1
    assert not plan.policy.rerun_allowed
    assert not plan.policy.cherry_pick_allowed
    assert plan.policy.result_artifact_immutable
    assert plan.policy.result_artifact_write_policy == IMMUTABLE_WRITE_POLICY
    assert plan.policy.result_writer_implemented
    assert not plan.production_approved
    assert not plan.operational_use_enabled
    assert not plan.writes_performed


def test_missing_test_access_approval_receipt_fails(tmp_path: Path) -> None:
    paths = _build_approved_case(tmp_path)
    paths["approval_receipt"] = tmp_path / "missing_approval_receipt.json"

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match="test-access approval receipt must be an existing local file",
    ):
        _plan(paths)


def test_changed_pipeline_hash_fails(tmp_path: Path) -> None:
    paths = _build_approved_case(tmp_path)
    _rewrite_receipt(
        paths,
        lambda payload: payload.update(
            {"accepted_validation_pipeline_sha256": "0" * 64}
        ),
    )

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match="does not bind identical validation and test pipeline hashes",
    ):
        _plan(paths)


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("label_handoff_approval", "NOT_APPROVED"),
        ("allowed_metrics", [*ALLOWED_EVALUATION_METRIC_NAMES, "roi"]),
        ("test_labels_opened", True),
        ("no_rerun", False),
        ("no_cherry_pick", False),
        ("result_artifact_write_policy", "overwrite_allowed"),
    ),
)
def test_receipt_cannot_relax_frozen_one_shot_policy(
    tmp_path: Path,
    field_name: str,
    value: object,
) -> None:
    paths = _build_approved_case(tmp_path)
    _rewrite_receipt(
        paths,
        lambda payload: payload.update({field_name: value}),
    )

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match="invalid approval/policy fields",
    ):
        _plan(paths)


@pytest.mark.parametrize("mutation", ("missing", "extra"))
def test_missing_or_extra_test_rows_fail(
    tmp_path: Path,
    mutation: str,
) -> None:
    paths = _build_approved_case(tmp_path)
    prediction = json.loads(
        paths["test_prediction"].read_text(encoding="utf-8")
    )
    if mutation == "missing":
        prediction["rows"].pop()
    else:
        prediction["rows"].append(
            {
                "row_id": "extra-test-row",
                "game_date": "2024-07-04",
                "game_id": "extra-game",
                "player_id": "extra-player",
                "raw_home_run_probability": 0.1,
                "calibrated_home_run_probability": 0.1,
            }
        )
    _rehash_prediction(prediction)
    _write_json(paths["test_prediction"], prediction)
    _rewrite_receipt(paths)

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match=rf"{mutation} test predictions=1",
    ):
        _plan(paths)


@pytest.mark.parametrize(
    "gate",
    ("betting_enabled", "ev_enabled", "kelly_eligible"),
)
def test_enabled_betting_ev_or_kelly_gate_fails(
    tmp_path: Path,
    gate: str,
) -> None:
    paths = _build_approved_case(tmp_path)
    feature_pack = json.loads(paths["feature_pack"].read_text(encoding="utf-8"))
    feature_pack[gate] = True
    _write_json(paths["feature_pack"], feature_pack)
    custody = json.loads(paths["label_custody"].read_text(encoding="utf-8"))
    custody["feature_pack_sha256"] = _sha256(paths["feature_pack"])
    _write_json(paths["label_custody"], custody)

    prediction = json.loads(
        paths["test_prediction"].read_text(encoding="utf-8")
    )
    prediction["feature_pack_sha256"] = _sha256(paths["feature_pack"])
    _rehash_prediction(prediction)
    _write_json(paths["test_prediction"], prediction)
    _rewrite_receipt(paths)

    with pytest.raises(
        MLBHROneShotTestEvaluatorError,
        match=rf"feature pack\.{gate} must remain false",
    ):
        _plan(paths)


def test_cli_mutates_no_operational_folder_or_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _build_approved_case(tmp_path)
    for relative in (
        "outputs",
        "test_outputs",
        "runtime",
        "history",
        "dashboard",
        "bankroll",
    ):
        directory = tmp_path / relative
        directory.mkdir()
        (directory / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    assert planner_cli.main(
        (
            "--feature-pack",
            str(paths["feature_pack"]),
            "--split-plan",
            str(paths["split_plan"]),
            "--preprocessing-artifact",
            str(paths["preprocessing"]),
            "--test-prediction-artifact",
            str(paths["test_prediction"]),
            "--test-access-approval-receipt",
            str(paths["approval_receipt"]),
        )
    ) == 0
    output = capsys.readouterr().out

    assert f"status: {ONE_SHOT_TEST_EVALUATION_PLAN_ONLY}" in output
    assert "gate.test_access_approval_receipt: PASSED" in output
    assert "gate.explicit_label_handoff_approval: PASSED" in output
    assert "metric.log_loss: frozen_not_computed" in output
    assert "one_shot.rerun_allowed: false" in output
    assert "result_artifact.write_policy: create_once_atomic_no_overwrite" in output
    assert "test_labels_sealed: true" in output
    assert "test_metrics_calculated: false" in output
    assert "eligible_for_betting: false" in output
    assert "writes_performed: false" in output
    assert _snapshot(tmp_path) == before
