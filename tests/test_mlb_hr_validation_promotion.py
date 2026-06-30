from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from courtvision.sports.mlb.training.hr_evaluation_contract import (
    EVALUATOR_CONTRACT_VERSION,
    VALIDATION_EVALUATOR_VERSION,
)
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
from courtvision.sports.mlb.training.hr_validation_promotion import (
    DO_NOT_PROMOTE,
    PROMOTE_TO_TEST_REVIEW,
    VALIDATION_ACCEPTANCE_POLICY_VERSION,
    VALIDATION_PROMOTION_EVIDENCE_SCHEMA_VERSION,
    audit_mlb_hr_validation_promotion,
    pipeline_sha256,
    validation_result_sha256,
)
import scripts.mlb_audit_hr_validation_promotion as promotion_cli


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


def _build_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
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
        "model_specification_sha256": _sha256(DEFAULT_MODEL_SPECIFICATION_PATH),
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
    payload["artifact_sha256"] = _canonical_prediction_sha256(payload)
    prediction = tmp_path / "frozen_predictions.json"
    prediction.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return feature_pack, temporal_plan, fitted, prediction


def _interval(estimate: float, lower: float, upper: float) -> dict[str, object]:
    return {
        "estimate": estimate,
        "lower_bound": lower,
        "upper_bound": upper,
        "confidence_level": 0.95,
        "requested_replicates": 2_000,
        "successful_replicates": 2_000,
        "seed": 20260629,
        "status": "estimated",
    }


def _improvement(
    model_estimate: float,
    baseline_estimate: float,
    improvement_estimate: float,
    lower: float,
    upper: float,
) -> dict[str, object]:
    result = _interval(improvement_estimate, lower, upper)
    result["improvement_estimate"] = result.pop("estimate")
    result["model_estimate"] = model_estimate
    result["baseline_estimate"] = baseline_estimate
    return result


def _strong_results(
    paths: tuple[Path, Path, Path, Path],
) -> dict[str, object]:
    feature_pack, temporal_plan, fitted, prediction = paths
    artifact = load_frozen_prediction_artifact(
        prediction,
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
    )
    raw_metrics = {
        "log_loss": _interval(0.48, 0.44, 0.52),
        "brier_score": _interval(0.15, 0.13, 0.17),
        "roc_auc": _interval(0.60, 0.52, 0.68),
        "pr_auc": _interval(0.24, 0.18, 0.30),
        "calibration_error": _interval(0.06, 0.04, 0.08),
    }
    calibrated_metrics = {
        "log_loss": _interval(0.42, 0.38, 0.46),
        "brier_score": _interval(0.13, 0.11, 0.15),
        "roc_auc": _interval(0.60, 0.52, 0.68),
        "pr_auc": _interval(0.24, 0.18, 0.30),
        "calibration_error": _interval(0.03, 0.02, 0.04),
    }
    train_comparisons = {
        "log_loss": _improvement(0.42, 0.50, 0.08, 0.04, 0.12),
        "brier_score": _improvement(0.13, 0.15, 0.02, 0.01, 0.03),
        "roc_auc": _improvement(0.60, 0.50, 0.10, 0.04, 0.16),
        "pr_auc": _improvement(0.24, 0.16, 0.08, 0.02, 0.14),
        "calibration_error": _improvement(0.03, 0.05, 0.02, 0.005, 0.035),
    }
    market_comparisons = {
        "log_loss": _improvement(0.40, 0.43, 0.03, 0.005, 0.055),
        "brier_score": _improvement(0.12, 0.13, 0.01, 0.0, 0.02),
        "roc_auc": _improvement(0.62, 0.60, 0.02, -0.01, 0.05),
        "pr_auc": _improvement(0.26, 0.25, 0.01, -0.01, 0.03),
        "calibration_error": _improvement(0.025, 0.035, 0.01, 0.0, 0.02),
    }
    ablation_comparisons = {
        "log_loss": _improvement(0.42, 0.44, 0.02, 0.002, 0.038),
        "brier_score": _improvement(0.13, 0.14, 0.01, -0.002, 0.022),
        "roc_auc": _improvement(0.60, 0.59, 0.01, -0.02, 0.04),
        "pr_auc": _improvement(0.24, 0.23, 0.01, -0.02, 0.04),
        "calibration_error": _improvement(
            0.03, 0.035, 0.005, -0.005, 0.015
        ),
    }
    results: dict[str, object] = {
        "schema_version": VALIDATION_PROMOTION_EVIDENCE_SCHEMA_VERSION,
        "acceptance_policy_version": VALIDATION_ACCEPTANCE_POLICY_VERSION,
        "evaluator_contract_version": EVALUATOR_CONTRACT_VERSION,
        "validation_evaluator_version": VALIDATION_EVALUATOR_VERSION,
        "split_id": "validation",
        "research_only": True,
        "approval_status": "not_approved",
        "production_approved": False,
        "operational_use_enabled": False,
        "feature_pack_sha256": _sha256(feature_pack),
        "temporal_split_plan_sha256": _sha256(temporal_plan),
        "fitted_preprocessing_artifact_sha256": _sha256(fitted),
        "prediction_artifact_sha256": artifact.artifact_sha256,
        "prediction_file_sha256": _sha256(prediction),
        "model_specification_sha256": artifact.model_specification_sha256,
        "code_version_sha256": artifact.code_version_sha256,
        "pipeline_sha256": pipeline_sha256(artifact),
        "evaluation_attempt": {
            "attempt_id": "validation-attempt-001",
            "attempt_number": 1,
            "prediction_frozen_before_validation_labels": True,
            "metrics_predeclared": True,
            "baselines_predeclared": True,
            "all_required_results_reported": True,
            "rerun_after_validation_label_access": False,
            "prediction_regenerated_after_validation_labels": False,
            "post_label_model_or_metric_selection": False,
        },
        "label_access": {
            "validation_labels_opened": True,
            "validation_evaluation_only": True,
            "test_labels_opened": False,
            "test_labels_sealed": True,
            "test_metrics_computed": False,
        },
        "bootstrap": {
            "unit": "game_date_block",
            "method": "paired_percentile_bootstrap",
            "confidence_level": 0.95,
            "requested_replicates": 2_000,
            "minimum_successful_replicates": 1_900,
            "seed": 20260629,
            "deterministic": True,
        },
        "population": {
            "row_count": 2,
            "positive_count": 1,
            "negative_count": 1,
            "unique_game_date_count": 2,
            "market_covered_count": 1,
            "market_missing_count": 1,
        },
        "calibration": {
            "method": "platt_sigmoid",
            "fit_split": "train",
            "train_time_ordered_out_of_fold_scores": True,
            "frozen_before_validation_labels": True,
            "validation_refit_performed": False,
            "test_refit_performed": False,
            "selection_using_validation_labels": False,
            "reliability_bin_policy_frozen_before_validation_labels": True,
        },
        "metrics": {
            "raw_home_run_probability": raw_metrics,
            "calibrated_home_run_probability": calibrated_metrics,
        },
        "baseline_comparisons": {
            "train_prevalence_constant": {
                "population": "identical_full_evaluation_population",
                "row_count": 2,
                "source_sha256": "1" * 64,
                "metrics": train_comparisons,
            },
            "raw_implied_probability": {
                "population": "identical_predeclared_market_covered_paired_subset",
                "row_count": 1,
                "source_sha256": _sha256(feature_pack),
                "metrics": market_comparisons,
            },
            "no_market_logistic_ablation": {
                "population": "identical_full_evaluation_population",
                "row_count": 2,
                "source_sha256": "2" * 64,
                "metrics": ablation_comparisons,
            },
        },
        "validation_result_sha256": "pending",
    }
    results["validation_result_sha256"] = validation_result_sha256(results)
    return results


def _audit(
    paths: tuple[Path, Path, Path, Path], results: Mapping[str, object]
):
    feature_pack, temporal_plan, fitted, prediction = paths
    return audit_mlb_hr_validation_promotion(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
        prediction_artifact_path=prediction,
        validation_results=results,
    )


def _rehash(results: dict[str, object]) -> None:
    results["validation_result_sha256"] = validation_result_sha256(results)


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


def test_strong_validation_result_promotes_to_review(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)

    decision = _audit(paths, _strong_results(paths))

    assert decision.verdict == PROMOTE_TO_TEST_REVIEW
    assert decision.failures == ()
    assert decision.test_labels_sealed
    assert not decision.test_label_access_authorized
    assert not decision.test_evaluation_authorized
    assert not decision.production_approved
    assert not decision.writes_performed


def test_weak_validation_result_fails(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    results = _strong_results(paths)
    results["metrics"]["calibrated_home_run_probability"]["roc_auc"][
        "estimate"
    ] = 0.54
    _rehash(results)

    decision = _audit(paths, results)

    assert decision.verdict == DO_NOT_PROMOTE
    assert "calibrated ROC-AUC must be at least 0.55" in decision.failures


def test_missing_required_baseline_fails(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    results = _strong_results(paths)
    del results["baseline_comparisons"]["no_market_logistic_ablation"]
    _rehash(results)

    decision = _audit(paths, results)

    assert decision.verdict == DO_NOT_PROMOTE
    assert any("no_market_logistic_ablation" in item for item in decision.failures)


def test_changed_pipeline_hash_fails(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    results = _strong_results(paths)
    results["pipeline_sha256"] = "0" * 64
    _rehash(results)

    decision = _audit(paths, results)

    assert decision.verdict == DO_NOT_PROMOTE
    assert "pipeline_sha256 does not match the unchanged pipeline" in decision.failures


def test_test_label_access_fails(tmp_path: Path) -> None:
    paths = _build_inputs(tmp_path)
    results = _strong_results(paths)
    results["label_access"]["test_labels_opened"] = True
    results["label_access"]["test_labels_sealed"] = False
    results["label_access"]["test_metrics_computed"] = True
    _rehash(results)

    decision = _audit(paths, results)

    assert decision.verdict == DO_NOT_PROMOTE
    assert not decision.test_labels_sealed
    assert any("test_labels_opened" in item for item in decision.failures)


def test_cli_mutates_no_operational_folder_or_input(
    tmp_path: Path,
    capsys,
) -> None:
    paths = _build_inputs(tmp_path)
    results_path = tmp_path / "validation_results.json"
    results_path.write_text(
        json.dumps(_strong_results(paths), indent=2) + "\n", encoding="utf-8"
    )
    for relative in ("outputs", "test_outputs", "runtime", "history", "dashboard"):
        directory = tmp_path / relative
        directory.mkdir()
        (directory / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)

    assert promotion_cli.main(
        (
            "--feature-pack",
            str(paths[0]),
            "--temporal-split-plan",
            str(paths[1]),
            "--fitted-preprocessing-artifact",
            str(paths[2]),
            "--prediction-artifact",
            str(paths[3]),
            "--validation-results",
            str(results_path),
        )
    ) == 0
    output = capsys.readouterr().out

    assert output.rstrip().endswith(PROMOTE_TO_TEST_REVIEW)
    assert "writes_performed: false" in output
    assert "test_label_access_authorized: false" in output
    assert _snapshot(tmp_path) == before
