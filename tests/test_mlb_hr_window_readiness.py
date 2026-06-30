from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest

from courtvision.sports.mlb.data.historical_backtest_readiness import (
    HistoricalBacktestReadinessVerdict,
)
from courtvision.sports.mlb.data.historical_feature_pack import (
    HISTORICAL_FEATURE_PACK_VERSION,
)
from courtvision.sports.mlb.training.hr_preprocessing_artifact import (
    write_fitted_preprocessing_artifact,
)
from courtvision.sports.mlb.training.hr_backtest_runner import (
    LABEL_ACCESS_SCOPE,
    MLBHRBacktestRunnerContractError,
    plan_sealed_mlb_hr_research_backtest,
)
from courtvision.sports.mlb.training.hr_preprocessing_plan import (
    TEMPORAL_SPLIT_ARTIFACT_VERSION,
)
from courtvision.sports.mlb.training.hr_window_readiness import (
    MLBHRWindowReadinessError,
    MLBHRWindowReadinessVerdict,
    validate_mlb_hr_window_readiness,
)
from courtvision.sports.mlb.training.hr_label_custody import (
    LABEL_CUSTODY_FILENAME,
    MLBHRLabelOpeningAuthorization,
    build_label_custody_payload,
)
import scripts.mlb_validate_hr_window_readiness as window_cli
import scripts.mlb_dry_run_hr_research_backtest as backtest_cli
import scripts.mlb_dry_run_hr_model_handoff as model_handoff_cli


FEATURE_NAMES = (
    "weather_temperature",
    "weather_wind_speed",
    "weather_wind_direction",
    "weather_wind_out_to_field",
    "weather_humidity",
    "roof_status",
    "park_factor_hr",
    "park_factor_lhb",
    "park_factor_rhb",
    "altitude",
    "sportsbook",
    "odds_provider",
    "hr_market_available",
    "american_odds",
    "decimal_odds",
    "implied_probability",
    "odds_collected_at",
    "odds_as_of",
    "odds_is_fresh_for_pregame",
)
FIRST_DATE = date(2024, 4, 1)
GAME_DATES = tuple(FIRST_DATE + timedelta(days=index) for index in range(150))
WINDOW_DATES = {
    "train": GAME_DATES[:90],
    "validation": GAME_DATES[90:120],
    "test": GAME_DATES[120:],
}
DEFAULT_SPECS = {
    "train": {"rows": 2_000, "games": 200, "players": 200, "positives": 100},
    "validation": {"rows": 1_000, "games": 100, "players": 100, "positives": 50},
    "test": {"rows": 1_000, "games": 100, "players": 100, "positives": 50},
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _feature_values(
    *,
    cutoff: str,
    odds_covered: bool,
) -> dict[str, object]:
    return {
        "weather_temperature": 72.0,
        "weather_wind_speed": 8.0,
        "weather_wind_direction": "out to center",
        "weather_wind_out_to_field": "center",
        "weather_humidity": 50.0,
        "roof_status": "open",
        "park_factor_hr": 1.05,
        "park_factor_lhb": 1.04,
        "park_factor_rhb": 1.06,
        "altitude": 100.0,
        "sportsbook": "Historical Book A" if odds_covered else None,
        "odds_provider": (
            "licensed_historical_export" if odds_covered else None
        ),
        "hr_market_available": odds_covered,
        "american_odds": 300 if odds_covered else None,
        "decimal_odds": 4.0 if odds_covered else None,
        "implied_probability": 0.25 if odds_covered else None,
        "odds_collected_at": cutoff if odds_covered else None,
        "odds_as_of": cutoff if odds_covered else None,
        "odds_is_fresh_for_pregame": odds_covered,
    }


def _rows_for_window(
    name: str,
    spec: Mapping[str, int],
    *,
    missing_odds: bool,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    window_dates = WINDOW_DATES[name]
    row_count = spec["rows"]
    game_count = spec["games"]
    player_count = spec["players"]
    positive_count = spec["positives"]
    for row_index in range(row_count):
        game_index = min(row_index * game_count // row_count, game_count - 1)
        date_index = game_index * (len(window_dates) - 1) // (game_count - 1)
        game_date = window_dates[date_index]
        date_text = game_date.isoformat()
        cutoff = f"{date_text}T20:00:00+00:00"
        start = f"{date_text}T23:00:00+00:00"
        values = _feature_values(cutoff=cutoff, odds_covered=not missing_odds)
        rows.append(
            {
                "row_id": f"{name}-row-{row_index:04d}",
                "game_id": f"{name}-game-{game_index:03d}",
                "game_date": date_text,
                "player_id": f"player-{row_index % player_count:03d}",
                "player_name": f"Player {row_index % player_count:03d}",
                "is_home_run": row_index < positive_count,
                "feature_cutoff_at": cutoff,
                "odds_collected_at": cutoff if not missing_odds else None,
                "event_start_time": start,
                "feature_values": values,
                "feature_availability": [
                    {
                        "feature_name": feature_name,
                        "available_at": f"{date_text}T18:00:00+00:00",
                        "source_latest_game_date": None,
                    }
                    for feature_name in FEATURE_NAMES
                ],
            }
        )
    return rows


def _build_inputs(
    tmp_path: Path,
    *,
    overrides: Mapping[str, Mapping[str, int]] | None = None,
    missing_odds_window: str | None = None,
) -> tuple[Path, Path, Path]:
    specs = {name: dict(values) for name, values in DEFAULT_SPECS.items()}
    for name, values in (overrides or {}).items():
        specs[name].update(values)
    rows = [
        row
        for name in ("train", "validation", "test")
        for row in _rows_for_window(
            name,
            specs[name],
            missing_odds=name == missing_odds_window,
        )
    ]
    labels = [
        {"row_id": row["row_id"], "is_home_run": row.pop("is_home_run")}
        for row in rows
    ]
    feature_pack = tmp_path / "feature_pack.json"
    feature_payload = {
        "schema_version": HISTORICAL_FEATURE_PACK_VERSION,
        "mode": "historical_research",
        "readiness_verdict": (
            HistoricalBacktestReadinessVerdict.READY_FOR_RESEARCH_BACKTEST.value
        ),
        "feature_names": list(FEATURE_NAMES),
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
    }
    feature_pack.write_text(
        json.dumps(feature_payload, separators=(",", ":")), encoding="utf-8"
    )
    (tmp_path / LABEL_CUSTODY_FILENAME).write_text(
        json.dumps(
            build_label_custody_payload(
                feature_payload=feature_payload,
                feature_pack_sha256=_sha256(feature_pack),
                labels=labels,
                created_at="2026-06-30T00:00:00+00:00",
            )
        ),
        encoding="utf-8",
    )

    temporal_plan = tmp_path / "temporal_plan.json"
    split_payload = {
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
        "train": {"game_dates": [value.isoformat() for value in WINDOW_DATES["train"]]},
        "validation": {
            "game_dates": [value.isoformat() for value in WINDOW_DATES["validation"]]
        },
        "test": {"game_dates": [value.isoformat() for value in WINDOW_DATES["test"]]},
    }
    temporal_plan.write_text(json.dumps(split_payload), encoding="utf-8")
    fitted = write_fitted_preprocessing_artifact(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        output_staging_dir=tmp_path / "fitted_staging",
    ).artifact_path
    return feature_pack, temporal_plan, fitted


def _validate(paths: tuple[Path, Path, Path]):
    feature_pack, temporal_plan, fitted = paths
    payload = json.loads(feature_pack.read_text(encoding="utf-8"))
    ids_by_split = {
        name: tuple(
            row["row_id"]
            for row in payload["rows"]
            if row["game_date"]
            in {value.isoformat() for value in WINDOW_DATES[name]}
        )
        for name in ("train", "validation", "test")
    }
    return validate_mlb_hr_window_readiness(
        feature_pack_path=feature_pack,
        label_custody_path=feature_pack.with_name(LABEL_CUSTODY_FILENAME),
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
        opening_authorizations=(
            MLBHRLabelOpeningAuthorization(
                split="train",
                reason="train_fitting",
                expected_row_ids=ids_by_split["train"],
            ),
            MLBHRLabelOpeningAuthorization(
                split="validation",
                reason="frozen_prediction_validation",
                expected_row_ids=ids_by_split["validation"],
                frozen_prediction_artifact_sha256="a" * 64,
            ),
            MLBHRLabelOpeningAuthorization(
                split="test",
                reason="approved_one_shot_test_handoff",
                expected_row_ids=ids_by_split["test"],
                frozen_prediction_artifact_sha256="b" * 64,
                approval_receipt_sha256="c" * 64,
            ),
        ),
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


def test_underpowered_train_split_fails_research_gate(tmp_path: Path) -> None:
    report = _validate(
        _build_inputs(tmp_path, overrides={"train": {"rows": 1_999}})
    )
    train = report.windows[0]

    assert train.verdict == (
        MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_REVIEW.value
    )
    assert "player_game_rows=1999 requires >= 2000" in train.research_failures
    assert report.verdict == (
        MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_REVIEW.value
    )


def test_validation_split_with_too_few_positives_fails(tmp_path: Path) -> None:
    report = _validate(
        _build_inputs(tmp_path, overrides={"validation": {"positives": 49}})
    )
    validation = report.windows[1]

    assert validation.verdict == (
        MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_REVIEW.value
    )
    assert "positive_labels=49 requires >= 50" in validation.research_failures


def test_test_split_missing_odds_coverage_fails(tmp_path: Path) -> None:
    report = _validate(_build_inputs(tmp_path, missing_odds_window="test"))
    test_window = report.windows[2]

    assert test_window.odds_coverage == 0.0
    assert test_window.odds_covered_rows == 0
    assert test_window.market_covered_rows == 0
    assert test_window.market_missing_rows == test_window.player_game_rows
    assert test_window.verdict == MLBHRWindowReadinessVerdict.WINDOW_NOT_READY.value
    assert "odds_coverage=0.00% requires >= 50.00%" in test_window.review_failures


def test_valid_windows_require_authorized_api_and_cli_stays_sealed(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _build_inputs(tmp_path)
    report = _validate(paths)

    assert all(
        window.verdict
        == MLBHRWindowReadinessVerdict.WINDOW_READY_FOR_RESEARCH_BACKTEST.value
        for window in report.windows
    )
    assert report.ready_for_research_backtest
    feature_pack, temporal_plan, fitted = paths
    assert window_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
        )
    ) == 2
    assert "requires explicit train, validation, and approved test" in (
        capsys.readouterr().err
    )


def test_artifact_hash_mismatch_fails(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)
    feature_payload = json.loads(feature_pack.read_text(encoding="utf-8"))
    feature_payload["created_at"] = "2026-06-28T12:00:00+00:00"
    feature_pack.write_text(json.dumps(feature_payload), encoding="utf-8")
    split_payload = json.loads(temporal_plan.read_text(encoding="utf-8"))
    split_payload["feature_pack_sha256"] = _sha256(feature_pack)
    temporal_plan.write_text(json.dumps(split_payload), encoding="utf-8")

    with pytest.raises(
        MLBHRWindowReadinessError,
        match="feature-pack SHA-256 does not match",
    ):
        _validate((feature_pack, temporal_plan, fitted))
    assert window_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
        )
    ) == 2
    assert "feature-pack SHA-256 does not match" in capsys.readouterr().err


def test_cli_does_not_mutate_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths = _build_inputs(tmp_path)
    for relative in ("outputs", "data/history", "runtime"):
        root = tmp_path / relative
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    feature_pack, temporal_plan, fitted = paths
    assert window_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
        )
    ) == 2
    capsys.readouterr()

    assert _snapshot(tmp_path) == before


def test_valid_gated_inputs_produce_backtest_execution_plan(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)

    plan = plan_sealed_mlb_hr_research_backtest(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
    )

    assert plan.status == "BACKTEST_EXECUTION_PLAN_ONLY"
    assert plan.label_access_scope == LABEL_ACCESS_SCOPE
    assert tuple(window.name for window in plan.windows) == (
        "train",
        "validation",
        "test",
    )
    assert all(window.current_action == "validate_only" for window in plan.windows)
    assert not plan.execution_authorized
    assert not plan.model_training_enabled
    assert not plan.predictions_enabled
    assert not plan.artifacts_written

    assert backtest_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
        )
    ) == 0
    output = capsys.readouterr().out
    assert "status: BACKTEST_EXECUTION_PLAN_ONLY" in output
    assert "gate.evaluation_label_access: PASSED" in output
    assert "window.validation.preprocessing_boundary: " in output
    assert "transform_only_no_refit_if_separately_approved" in output
    assert "metric.log_loss: planned_not_computed" in output
    assert "execution_authorized: false" in output
    assert "artifacts_written: false" in output


def test_sealed_backtest_runner_does_not_open_underpowered_window_labels(
    tmp_path: Path,
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(
        tmp_path,
        overrides={
            "validation": {
                "rows": 19,
                "games": 4,
                "players": 9,
                "positives": 1,
            }
        },
    )

    plan = plan_sealed_mlb_hr_research_backtest(
        feature_pack_path=feature_pack,
        temporal_split_plan_path=temporal_plan,
        fitted_preprocessing_artifact_path=fitted,
    )
    validation = next(window for window in plan.windows if window.name == "validation")
    assert validation.player_game_rows == 19
    assert validation.positive_labels is None
    assert validation.negative_labels is None


def test_sealed_backtest_runner_refuses_artifact_hash_mismatch(
    tmp_path: Path,
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)
    payload = json.loads(fitted.read_text(encoding="utf-8"))
    first_column = next(iter(payload["numeric_medians"]))
    payload["numeric_medians"][first_column] += 0.01
    fitted.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MLBHRBacktestRunnerContractError,
        match="artifact content SHA-256 does not match",
    ):
        plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
            fitted_preprocessing_artifact_path=fitted,
        )


def test_sealed_backtest_runner_refuses_leakage_feature(tmp_path: Path) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)
    payload = json.loads(feature_pack.read_text(encoding="utf-8"))
    payload["feature_names"].append("is_home_run")
    feature_pack.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        MLBHRBacktestRunnerContractError,
        match="label/outcome field cannot be used",
    ):
        plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
            fitted_preprocessing_artifact_path=fitted,
        )


def test_sealed_backtest_runner_refuses_missing_label_access(
    tmp_path: Path,
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)
    custody_path = feature_pack.with_name(LABEL_CUSTODY_FILENAME)
    custody_payload = json.loads(custody_path.read_text(encoding="utf-8"))
    custody_payload["rows"].pop(0)
    custody_path.write_text(json.dumps(custody_payload), encoding="utf-8")

    with pytest.raises(
        MLBHRBacktestRunnerContractError,
        match="missing label rows=1",
    ):
        plan_sealed_mlb_hr_research_backtest(
            feature_pack_path=feature_pack,
            temporal_split_plan_path=temporal_plan,
            fitted_preprocessing_artifact_path=fitted,
        )


def test_sealed_backtest_cli_does_not_mutate_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)
    for relative in ("outputs", "data/history", "runtime"):
        root = tmp_path / relative
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert backtest_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
        )
    ) == 0
    capsys.readouterr()

    assert _snapshot(tmp_path) == before


def test_model_handoff_cli_is_valid_and_does_not_mutate_operational_folders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    feature_pack, temporal_plan, fitted = _build_inputs(tmp_path)
    for relative in ("outputs", "data/history", "runtime"):
        root = tmp_path / relative
        root.mkdir(parents=True)
        (root / "sentinel.txt").write_text("preserve\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert model_handoff_cli.main(
        (
            "--feature-pack",
            str(feature_pack),
            "--temporal-split-plan",
            str(temporal_plan),
            "--fitted-preprocessing-artifact",
            str(fitted),
        )
    ) == 2
    assert "split distributions do not match" in capsys.readouterr().err
    assert _snapshot(tmp_path) == before
