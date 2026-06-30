from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HISTORICAL_FEATURE_PACK_VERSION,
)
import courtvision.sports.mlb.training.hr_evaluation_contract as evaluation_contract
from courtvision.sports.mlb.training.hr_evaluation_contract import (
    ALLOWED_EVALUATION_METRIC_NAMES,
    EVALUATOR_CONTRACT_VERSION,
    MLBHREvaluationContractError,
    evaluate_frozen_mlb_hr_validation,
    plan_frozen_mlb_hr_research_evaluation,
    validate_mlb_hr_evaluation_label_opening,
)
from courtvision.sports.mlb.training.hr_prediction_artifact import (
    DEFAULT_MODEL_SPECIFICATION_PATH,
    FROZEN_PREDICTION_ARTIFACT_SCHEMA_VERSION,
    FROZEN_PREDICTION_ARTIFACT_TYPE,
    IMMUTABLE_WRITE_POLICY,
    MODEL_SPECIFICATION_ID,
    PROBABILITY_FIELDS,
    ROW_IDENTITY_KEYS,
)
from courtvision.sports.mlb.training.hr_preprocessing_artifact import (
    write_fitted_preprocessing_artifact,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    TEMPORAL_SPLIT_ARTIFACT_VERSION,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    build_label_custody_payload,
)
import scripts.mlb_dry_run_hr_evaluator as evaluator_cli


FIRST_DATE = date(2024, 4, 1)
GAME_DATES = tuple(FIRST_DATE + timedelta(days=index) for index in range(30))
WINDOW_DATES = {
    "train": GAME_DATES[:18],
    "validation": GAME_DATES[18:24],
    "test": GAME_DATES[24:],
}


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


def _write_prediction_payload(path: Path, payload: dict[str, object]) -> None:
    payload["artifact_sha256"] = _canonical_payload_sha256(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _build_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    rows: list[dict[str, object]] = []
    labels: list[dict[str, object]] = []
    for split_name in ("train", "validation", "test"):
        for index, game_date in enumerate(WINDOW_DATES[split_name]):
            date_text = game_date.isoformat()
            cutoff = f"{date_text}T20:00:00+00:00"
            row = {
                    "row_id": f"{split_name}-row-{index:02d}",
                    "game_id": f"{split_name}-game-{index:02d}",
                    "game_date": date_text,
                    "player_id": f"player-{split_name}-{index:02d}",
                    "player_name": f"Player {split_name} {index:02d}",
                    "odds_collected_at": cutoff,
                    "event_start_time": f"{date_text}T23:00:00+00:00",
                    "feature_values": {
                        "weather_temperature": 70.0 + index,
                        "hr_market_available": True,
                    },
                    "feature_availability": [
                        {
                            "feature_name": "weather_temperature",
                            "available_at": cutoff,
                            "source_latest_game_date": None,
                        },
                        {
                            "feature_name": "hr_market_available",
                            "available_at": cutoff,
                            "source_latest_game_date": None,
                        },
                    ],
                }
            rows.append(row)
            labels.append(
                {"row_id": row["row_id"], "is_home_run": index % 2 == 0}
            )

    feature_pack = tmp_path / "feature_pack.json"
    feature_pack.write_text(
        json.dumps(
            {
                "schema_version": HISTORICAL_FEATURE_PACK_VERSION,
                "mode": "historical_research",
                "readiness_verdict": (
                    HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
                ),
                "feature_names": ["weather_temperature", "hr_market_available"],
                "rows": rows,
                "feature_firewall_valid": True,
                "approval_status": "not_approved",
                "model_training_enabled": False,
                "backtesting_enabled": False,
                "predictions_enabled": False,
                "eligible_for_betting": False,
                "ev_enabled": False,
                "kelly_eligible": False,
                "elite_enabled": False,
                "staking_enabled": False,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    (tmp_path / LABEL_CUSTODY_FILENAME).write_text(
        json.dumps(
            build_label_custody_payload(
                feature_payload=json.loads(feature_pack.read_text(encoding="utf-8")),
                feature_pack_sha256=_sha256(feature_pack),
                labels=labels,
                created_at="2026-06-30T00:00:00+00:00",
            )
        ),
        encoding="utf-8",
    )

    temporal_plan = tmp_path / "temporal_plan.json"
    temporal_plan.write_text(
        json.dumps(
            {
                "schema_version": TEMPORAL_SPLIT_ARTIFACT_VERSION,
                "mode": "historical_research",
                "feature_pack_sha256": _sha256(feature_pack),
                "pack_dir": str(tmp_path / "source_pack"),
                "split_method": "whole_unique_game_dates_60_20_20",
                "readiness_verdict": (
                    HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
                ),
                "approval_status": "not_approved",
                "model_training_enabled": False,
                "backtesting_enabled": False,
                "predictions_enabled": False,
                "eligible_for_betting": False,
                "ev_enabled": False,
                "kelly_eligible": False,
                "elite_enabled": False,
                "staking_enabled": False,
                "train": {
                    "game_dates": [
                        value.isoformat() for value in WINDOW_DATES["train"]
                    ]
                },
                "validation": {
                    "game_dates": [
                        value.isoformat() for value in WINDOW_DATES["validation"]
                    ]
                },
                "test": {
                    "game_dates": [
                        value.isoformat() for value in WINDOW_DATES["test"]
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    fitted = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_staging",
    ).artifact_path

    code_version = "reviewed-evaluator-fixture-0123456789abcdef"
    validation_rows = [
        row for row in rows if row["game_date"] in {
            value.isoformat() for value in WINDOW_DATES["validation"]
        }
    ]
    prediction_payload: dict[str, object] = {
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
        "window_id": (
            f"validation:{WINDOW_DATES['validation'][0].isoformat()}:"
            f"{WINDOW_DATES['validation'][-1].isoformat()}"
        ),
        "prediction_timestamp": "2024-04-18T18:00:00+00:00",
        "row_identity_keys": list(ROW_IDENTITY_KEYS),
        "probability_fields": list(PROBABILITY_FIELDS),
        "probability_minimum": 0.0,
        "probability_maximum": 1.0,
        "rows": [
            {
                "row_id": row["row_id"],
                "game_date": row["game_date"],
                "game_id": row["game_id"],
                "player_id": row["player_id"],
                "raw_home_run_probability": 0.12 + index * 0.01,
                "calibrated_home_run_probability": 0.11 + index * 0.01,
            }
            for index, row in enumerate(validation_rows)
        ],
        "artifact_sha256": "pending",
    }
    prediction = tmp_path / "frozen_validation_predictions.json"
    _write_prediction_payload(prediction, prediction_payload)
    return feature_pack, temporal_plan, fitted, prediction


def _mutate_prediction(prediction: Path, mutation) -> None:
    payload = json.loads(prediction.read_text(encoding="utf-8"))
    mutation(payload)
    _write_prediction_payload(prediction, payload)


def _patch_runner(
    monkeypatch: pytest.MonkeyPatch,
    paths: tuple[Path, Path, Path, Path],
) -> None:
    feature_pack, temporal_plan, fitted, _ = paths

    def fake_runner(**kwargs):
        return SimpleNamespace(
            status="BACKTEST_EXECUTION_PLAN_ONLY",
            approval_status="not_approved",
            feature_pack_sha256=_sha256(feature_pack),
            label_custody_sha256=_sha256(
                feature_pack.with_name(LABEL_CUSTODY_FILENAME)
            ),
            temporal_split_plan_sha256=_sha256(temporal_plan),
            fitted_preprocessing_artifact_sha256=_sha256(fitted),
            feature_firewall_valid=True,
            temporal_split_valid=True,
            preprocessing_artifact_valid=True,
            window_population_valid=True,
            label_access_valid=True,
            research_only=True,
            execution_authorized=False,
            model_training_enabled=False,
            preprocessing_transform_enabled=False,
            predictions_enabled=False,
            metric_computation_enabled=False,
            live_fetching_enabled=False,
            backtesting_enabled=False,
            eligible_for_betting=False,
            ev_enabled=False,
            kelly_eligible=False,
            elite_enabled=False,
            staking_enabled=False,
            production_approved=False,
            artifacts_written=False,
        )

    monkeypatch.setattr(
        evaluation_contract,
        "plan_sealed_mlb_hr_research_backtest",
        fake_runner,
    )


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


def test_valid_artifacts_produce_evaluation_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    feature_pack, temporal_plan, fitted, prediction = paths

    plan = plan_frozen_mlb_hr_research_evaluation(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
        prediction_artifact_path=prediction,
    )

    assert plan.contract_version == EVALUATOR_CONTRACT_VERSION
    assert plan.status == "EVALUATION_PLAN_ONLY"
    assert plan.population_coverage.expected_rows == 6
    assert plan.population_coverage.matched_rows == 6
    assert tuple(metric.name for metric in plan.metric_definitions) == (
        ALLOWED_EVALUATION_METRIC_NAMES
    )
    assert plan.label_handoff_valid
    assert plan.labels_opened_after_prediction_validation
    assert not plan.label_values_exposed
    assert not plan.final_metrics_calculated
    assert not plan.metric_computation_enabled
    assert not plan.artifacts_written


def test_missing_predictions_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    _mutate_prediction(paths[3], lambda payload: payload["rows"].pop())

    with pytest.raises(
        MLBHREvaluationContractError,
        match="missing predictions=1",
    ):
        plan_frozen_mlb_hr_research_evaluation(
            feature_pack_path=paths[0],
            temporal_split_plan_path=paths[1],
            fitted_preprocessing_artifact_path=paths[2],
            prediction_artifact_path=paths[3],
        )


def test_extra_predictions_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)

    def add_extra(payload: dict[str, object]) -> None:
        payload["rows"].append(
            {
                "row_id": "validation-extra-row",
                "game_date": WINDOW_DATES["validation"][0].isoformat(),
                "game_id": "validation-extra-game",
                "player_id": "validation-extra-player",
                "raw_home_run_probability": 0.2,
                "calibrated_home_run_probability": 0.2,
            }
        )

    _mutate_prediction(paths[3], add_extra)
    with pytest.raises(
        MLBHREvaluationContractError,
        match="extra predictions=1",
    ):
        plan_frozen_mlb_hr_research_evaluation(
            feature_pack_path=paths[0],
            temporal_split_plan_path=paths[1],
            fitted_preprocessing_artifact_path=paths[2],
            prediction_artifact_path=paths[3],
        )


def test_label_opening_before_prediction_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[str] = []

    def forbidden_call(**kwargs):
        called.append("labels_opened")
        raise AssertionError("label readers must not be called")

    monkeypatch.setattr(
        evaluation_contract,
        "plan_sealed_mlb_hr_research_backtest",
        forbidden_call,
    )
    monkeypatch.setattr(
        evaluation_contract,
        "validate_mlb_hr_label_handoff",
        forbidden_call,
    )

    with pytest.raises(
        MLBHREvaluationContractError,
        match="labels cannot open before frozen prediction validation",
    ):
        validate_mlb_hr_evaluation_label_opening(
            frozen_prediction_artifact=None,
            population_coverage=None,
            feature_pack_path=tmp_path / "feature.json",
            temporal_split_plan_path=tmp_path / "split.json",
            fitted_preprocessing_artifact_path=tmp_path / "preprocessing.json",
        )
    assert called == []


def test_test_labels_remain_sealed_without_promotion_proof(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    feature_payload = json.loads(paths[0].read_text(encoding="utf-8"))
    test_rows = [
        row
        for row in feature_payload["rows"]
        if row["game_date"]
        in {value.isoformat() for value in WINDOW_DATES["test"]}
    ]

    def use_test_split(payload: dict[str, object]) -> None:
        payload["split_id"] = "test"
        payload["window_id"] = (
            f"test:{WINDOW_DATES['test'][0].isoformat()}:"
            f"{WINDOW_DATES['test'][-1].isoformat()}"
        )
        payload["rows"] = [
            {
                "row_id": row["row_id"],
                "game_date": row["game_date"],
                "game_id": row["game_id"],
                "player_id": row["player_id"],
                "raw_home_run_probability": 0.15,
                "calibrated_home_run_probability": 0.14,
            }
            for row in test_rows
        ]

    _mutate_prediction(paths[3], use_test_split)
    with pytest.raises(
        MLBHREvaluationContractError,
        match="test labels remain sealed pending validation-to-test promotion proof",
    ):
        plan_frozen_mlb_hr_research_evaluation(
            feature_pack_path=paths[0],
            temporal_split_plan_path=paths[1],
            fitted_preprocessing_artifact_path=paths[2],
            prediction_artifact_path=paths[3],
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "expected_value",
        "kelly_fraction",
        "eligible_for_betting",
        "elite_tier",
        "stake_units",
    ),
)
def test_betting_ev_or_kelly_fields_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    _mutate_prediction(
        paths[3],
        lambda payload: payload["rows"][0].update({field_name: 0}),
    )

    with pytest.raises(
        MLBHREvaluationContractError,
        match="prohibited EV/Kelly/staking/betting fields",
    ):
        plan_frozen_mlb_hr_research_evaluation(
            feature_pack_path=paths[0],
            temporal_split_plan_path=paths[1],
            fitted_preprocessing_artifact_path=paths[2],
            prediction_artifact_path=paths[3],
        )


def test_cli_writes_nothing_and_does_not_mutate_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    for relative in ("outputs", "test_outputs", "data/history", "runtime"):
        root = tmp_path / relative
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert evaluator_cli.main(
        (
            "--feature-pack",
            str(paths[0]),
            "--temporal-split-plan",
            str(paths[1]),
            "--fitted-preprocessing-artifact",
            str(paths[2]),
            "--prediction-artifact",
            str(paths[3]),
        )
    ) == 0
    output = capsys.readouterr().out

    assert "status: EVALUATION_PLAN_ONLY" in output
    assert "metric.log_loss: planned_not_computed" in output
    assert "population.missing_rows: 0" in output
    assert "metric_computation_enabled: false" in output
    assert "artifacts_written: false" in output
    assert _snapshot(tmp_path) == before


def test_validation_metrics_open_labels_only_after_frozen_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    events: list[str] = []
    selected_distribution_splits: list[tuple[str, ...]] = []

    real_prediction_loader = evaluation_contract.load_frozen_prediction_artifact
    real_coverage = evaluation_contract.validate_mlb_hr_prediction_population_coverage
    fake_runner = evaluation_contract.plan_sealed_mlb_hr_research_backtest
    real_handoff = evaluation_contract.validate_mlb_hr_label_handoff
    real_metric_inputs = evaluation_contract._validation_metric_inputs

    def tracked_prediction_loader(*args, **kwargs):
        events.append("prediction")
        return real_prediction_loader(*args, **kwargs)

    def tracked_coverage(**kwargs):
        events.append("coverage")
        return real_coverage(**kwargs)

    def tracked_runner(**kwargs):
        events.append("runner")
        return fake_runner(**kwargs)

    def tracked_handoff(**kwargs):
        events.append("handoff")
        selected_distribution_splits.append(tuple(kwargs["distribution_splits"]))
        return real_handoff(**kwargs)

    def tracked_metric_inputs(**kwargs):
        events.append("validation_labels")
        return real_metric_inputs(**kwargs)

    monkeypatch.setattr(
        evaluation_contract,
        "load_frozen_prediction_artifact",
        tracked_prediction_loader,
    )
    monkeypatch.setattr(
        evaluation_contract,
        "validate_mlb_hr_prediction_population_coverage",
        tracked_coverage,
    )
    monkeypatch.setattr(
        evaluation_contract,
        "plan_sealed_mlb_hr_research_backtest",
        tracked_runner,
    )
    monkeypatch.setattr(
        evaluation_contract,
        "validate_mlb_hr_label_handoff",
        tracked_handoff,
    )
    monkeypatch.setattr(
        evaluation_contract,
        "_validation_metric_inputs",
        tracked_metric_inputs,
    )

    result = evaluate_frozen_mlb_hr_validation(
        feature_pack_path=paths[0],
        temporal_split_plan_path=paths[1],
        fitted_preprocessing_artifact_path=paths[2],
        prediction_artifact_path=paths[3],
    )

    assert events == [
        "prediction",
        "coverage",
        "runner",
        "handoff",
        "validation_labels",
    ]
    assert selected_distribution_splits == [("validation",)]
    assert result.validation_labels_opened
    assert result.validation_metrics_calculated
    assert result.test_labels_sealed
    assert not result.test_labels_opened
    assert len(result.bootstrap.intervals) == 10
    assert not result.artifacts_written


def test_validation_cli_writes_nothing_and_preserves_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _build_inputs(tmp_path)
    _patch_runner(monkeypatch, paths)
    for relative in ("outputs", "test_outputs", "data/history", "runtime"):
        root = tmp_path / relative
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert evaluator_cli.main(
        (
            "--mode",
            "validation",
            "--feature-pack",
            str(paths[0]),
            "--temporal-split-plan",
            str(paths[1]),
            "--fitted-preprocessing-artifact",
            str(paths[2]),
            "--prediction-artifact",
            str(paths[3]),
        )
    ) == 0
    output = capsys.readouterr().out

    assert "status: VALIDATION_METRICS_COMPUTED_IN_MEMORY" in output
    assert "metric.raw_home_run_probability.log_loss:" in output
    assert "bootstrap.replicates: 2000" in output
    assert "test_labels_sealed: true" in output
    assert "model_training_enabled: false" in output
    assert "eligible_for_betting: false" in output
    assert "artifacts_written: false" in output
    assert _snapshot(tmp_path) == before
