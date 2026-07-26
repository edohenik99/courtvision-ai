from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import socket
import time

import pandas as pd
import pytest

import courtvision_ai
from courtvision.lifecycle.writer import (
    completed_segment_directories,
    read_segment_events,
    verify_segment,
)
from courtvision.prediction import (
    CallbackPredictionPublisher,
    DisabledPredictionLifecycle,
    EnginePrediction,
    PredictionApplicationService,
    PredictionEngineRegistry,
    PredictionRequest,
    PredictionRunConflictError,
    ShadowPredictionLifecycle,
)
from courtvision.prediction.application import PredictionRunLock
from courtvision.prediction.publication import publish_dataframe, publish_text


class _FixtureEngine:
    sport = "nba"
    modes = frozenset({"production"})

    def __init__(self, outputs: dict[str, object]) -> None:
        self.outputs = outputs
        self.runtime = self

    def execute(self, request: PredictionRequest) -> EnginePrediction:
        copied = {
            key: value.copy(deep=True)
            if isinstance(value, pd.DataFrame)
            else value
            for key, value in self.outputs.items()
        }
        return EnginePrediction(
            outputs=copied,
            provider_provenance={"provider": "deterministic-fixture"},
            model_version="fixture-v1",
        )


def _operator_fixture(prediction_date: str) -> dict[str, object]:
    rows = pd.DataFrame(
        [
            {
                "prediction_date": prediction_date,
                "game_id": "100",
                "player_id": "246",
                "player_name": "Fixture Player A",
                "team": "LAL",
                "opponent": "BOS",
                "market_type": "player_points",
                "selection": "OVER",
                "sportsbook_line": 24.5,
                "line": 24.5,
                "odds": -110,
                "bookmaker": "DraftKings",
                "model_projection": 27.25,
                "edge": 2.75,
                "edge_pct": 0.112244897959,
                "confidence": 0.72,
                "quality_score": 91.0,
                "selection_score": 93.0,
                "qualification_reason": "fixture_pass",
                "elite_rejection_reason": "",
                "is_elite": True,
            },
            {
                "prediction_date": prediction_date,
                "game_id": "101",
                "player_id": "247",
                "player_name": "Fixture Player B",
                "team": "BOS",
                "opponent": "LAL",
                "market_type": "player_rebounds",
                "selection": "UNDER",
                "sportsbook_line": 8.5,
                "line": 8.5,
                "odds": -105,
                "bookmaker": "FanDuel",
                "model_projection": 6.75,
                "edge": -1.75,
                "edge_pct": -0.205882352941,
                "confidence": 0.68,
                "quality_score": 88.0,
                "selection_score": 89.0,
                "qualification_reason": "fixture_pass",
                "elite_rejection_reason": "",
                "is_elite": True,
            },
        ]
    )
    return {
        "selected_props": rows.copy(),
        "elite_props": rows.copy(),
        "qualified_pool_props": rows.copy(),
        "full_market_props": rows.copy(),
        "near_miss_props": pd.DataFrame(columns=rows.columns),
        "sgp_props": pd.DataFrame(),
        "summary": {
            "prediction_date": prediction_date,
            "selected_count": len(rows),
            "elite_count": len(rows),
            "pipeline_mode": "deterministic_fixture",
        },
        "grading_results": pd.DataFrame(),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_nba_application_preserves_outputs_artifacts_and_overwrite_guard(
    tmp_path: Path,
) -> None:
    prediction_date = "2026-07-25"
    outputs = _operator_fixture(prediction_date)
    old_root = tmp_path / "old"
    new_root = tmp_path / "new"
    old_paths = courtvision_ai._write_cli_outputs(
        out_dir=old_root,
        prediction_date=prediction_date,
        fit_metrics=None,
        prediction_outputs=outputs,
        verbose_outputs=False,
    )
    runtime = type(
        "Runtime",
        (),
        {"_predict_internal": lambda self, date: _operator_fixture(date)},
    )()
    result = courtvision_ai.run_nba_prediction_application(
        runtime,
        prediction_date=prediction_date,
        out_dir=new_root,
        entrypoint="test_prediction_application",
        hooks_loader=lambda: None,
    )

    assert result.status == "SUCCESS"
    assert result.lifecycle_status == "DISABLED"
    assert result.run_id
    assert result.provider_provenance["provider"] == "unknown"
    expected_keys = set(old_paths)
    assert expected_keys.issubset(result.artifact_paths)
    byte_stable_prediction_artifacts = {
        "player_predictions",
        "game_predictions",
        "player_edges",
        "game_edges",
        "elite_board",
        "full_market_board",
        "near_elite_review",
        "sgp_board",
        "player_points_elite_admission_csv",
    }
    for key in expected_keys:
        old_path = Path(old_paths[key])
        new_path = Path(result.artifact_paths[key])
        assert old_path.name == new_path.name
        assert old_path.exists() is new_path.exists()
        if old_path.exists() and key in byte_stable_prediction_artifacts:
            assert _sha256(old_path) == _sha256(new_path)

    for key in (
        "selected_props",
        "elite_props",
        "qualified_pool_props",
        "full_market_props",
    ):
        pd.testing.assert_frame_equal(
            outputs[key].reset_index(drop=True),
            result.outputs[key].reset_index(drop=True),
            check_exact=True,
        )
    assert list(result.outputs["elite_props"]["selection"]) == [
        "OVER",
        "UNDER",
    ]
    assert list(result.outputs["elite_props"]["confidence"]) == [0.72, 0.68]
    assert list(result.outputs["elite_props"]["edge"]) == [2.75, -1.75]

    elite_path = Path(result.artifact_paths["elite_board"])
    elite_hash = _sha256(elite_path)
    with pytest.raises(RuntimeError, match="ARTIFACT_OVERWRITE_GUARD"):
        courtvision_ai.run_nba_prediction_application(
            runtime,
            prediction_date=prediction_date,
            out_dir=new_root,
            entrypoint="test_prediction_application",
            hooks_loader=lambda: None,
        )
    assert _sha256(elite_path) == elite_hash


def test_publication_failure_rolls_back_every_staged_prediction_file(
    tmp_path: Path,
) -> None:
    prediction_date = "2026-07-25"
    engine = _FixtureEngine(_operator_fixture(prediction_date))

    def fail_after_two_writes(
        request: PredictionRequest,
        run_id: str,
        prediction: EnginePrediction,
    ) -> dict[str, Path]:
        publish_text(
            tmp_path / f"player_predictions_{prediction_date}.csv",
            "value\n1\n",
            prediction_date=prediction_date,
            caller="rollback-test",
            artifact_label="player_predictions",
        )
        publish_text(
            tmp_path / f"game_predictions_{prediction_date}.csv",
            "value\n2\n",
            prediction_date=prediction_date,
            caller="rollback-test",
            artifact_label="game_predictions",
        )
        raise RuntimeError("injected publication failure")

    service = PredictionApplicationService(
        registry=PredictionEngineRegistry([engine]),
        publisher=CallbackPredictionPublisher(fail_after_two_writes),
        lifecycle=DisabledPredictionLifecycle(),
    )
    with pytest.raises(RuntimeError, match="injected publication failure"):
        service.run(
            PredictionRequest(
                sport="nba",
                prediction_date=prediction_date,
                mode="production",
                out_dir=str(tmp_path),
            )
        )
    assert not (tmp_path / f"player_predictions_{prediction_date}.csv").exists()
    assert not (tmp_path / f"game_predictions_{prediction_date}.csv").exists()


def test_late_publication_failure_rolls_back_prediction_and_grading_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prediction_date = "2026-07-25"
    initial_outputs = _operator_fixture(prediction_date)
    initial_outputs["grading_results"] = pd.DataFrame(
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Fixture Player A",
                "market_type": "player_points",
                "selection": "over",
                "sportsbook_line": 24.5,
                "actual_value": 27,
                "result": "win",
                "graded_result": "win",
            }
        ]
    )
    paths = courtvision_ai._write_cli_outputs(
        out_dir=tmp_path,
        prediction_date=prediction_date,
        fit_metrics=None,
        prediction_outputs=initial_outputs,
    )
    restored_paths = {
        label: Path(paths[label])
        for label in ("player_predictions", "grading_results")
    }
    restored_bytes = {
        label: path.read_bytes() for label, path in restored_paths.items()
    }
    removed_paths = {
        label: Path(paths[label])
        for label in ("game_predictions", "grading_summary_json")
    }
    for path in removed_paths.values():
        path.unlink()

    failed_outputs = _operator_fixture(prediction_date)
    failed_outputs["grading_results"] = pd.DataFrame(
        [
            {
                "prediction_date": prediction_date,
                "player_name": "Fixture Player B",
                "market_type": "player_rebounds",
                "selection": "under",
                "sportsbook_line": 8.5,
                "actual_value": 10,
                "result": "loss",
                "graded_result": "loss",
            }
        ]
    )
    runtime = type(
        "Runtime",
        (),
        {"_predict_internal": lambda self, date: failed_outputs},
    )()
    original_write_text = courtvision_ai._write_prediction_text

    def fail_final_report_write(*args: object, **kwargs: object) -> None:
        if kwargs.get("artifact_label") == "top_plays_report":
            raise RuntimeError("injected late staged write failure")
        original_write_text(*args, **kwargs)

    monkeypatch.setattr(
        courtvision_ai,
        "_write_prediction_text",
        fail_final_report_write,
    )

    with pytest.raises(
        RuntimeError,
        match="injected late staged write failure",
    ):
        courtvision_ai.run_nba_prediction_application(
            runtime,
            prediction_date=prediction_date,
            out_dir=tmp_path,
            force_output_overwrite=True,
            entrypoint="grading-rollback-test",
            hooks_loader=lambda: None,
        )

    for label, path in restored_paths.items():
        assert path.read_bytes() == restored_bytes[label]
    for path in removed_paths.values():
        assert not path.exists()
    assert not list(tmp_path.rglob("*.stage"))
    assert not list(tmp_path.rglob("*.backup"))


def _lock_metadata(
    *,
    run_id: str,
    pid: int,
    owner_token: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "role": "prediction",
        "owner_token": owner_token,
        "run_id": run_id,
        "pid": pid,
        "created_at_utc": "2026-07-25T12:00:00+00:00",
        "hostname": socket.gethostname(),
        "sport": "nba",
        "mode": "production",
        "prediction_date": "2026-07-25",
    }


def _prediction_lock(
    path: Path,
    *,
    run_id: str,
    stale_after_seconds: float = 60,
) -> PredictionRunLock:
    return PredictionRunLock(
        path,
        run_id=run_id,
        sport="nba",
        mode="production",
        prediction_date="2026-07-25",
        stale_after_seconds=stale_after_seconds,
    )


def test_active_prediction_lock_remains_protected_and_records_metadata(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "prediction.lock"
    with _prediction_lock(lock_path, run_id="active-run"):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["pid"] == os.getpid()
        assert metadata["sport"] == "nba"
        assert metadata["mode"] == "production"
        assert metadata["prediction_date"] == "2026-07-25"
        assert metadata["hostname"] == socket.gethostname()
        assert metadata["created_at_utc"]
        with pytest.raises(PredictionRunConflictError):
            with _prediction_lock(lock_path, run_id="contending-run"):
                pass


def test_dead_pid_prediction_lock_is_reclaimed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "prediction.lock"
    lock_path.write_text(
        json.dumps(
            _lock_metadata(
                run_id="dead-run",
                pid=999_999_999,
                owner_token="dead-owner",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        PredictionRunLock,
        "_process_is_alive",
        staticmethod(lambda pid: False),
    )

    with _prediction_lock(lock_path, run_id="replacement-run"):
        metadata = json.loads(lock_path.read_text(encoding="utf-8"))
        assert metadata["run_id"] == "replacement-run"

    assert not lock_path.exists()
    assert not lock_path.with_name(f"{lock_path.name}.reclaim").exists()


def test_old_corrupt_prediction_lock_is_reclaimed(tmp_path: Path) -> None:
    lock_path = tmp_path / "prediction.lock"
    lock_path.write_text("{corrupt", encoding="utf-8")
    old_time = time.time() - 120
    os.utime(lock_path, (old_time, old_time))

    with _prediction_lock(lock_path, run_id="replacement-run"):
        assert json.loads(lock_path.read_text(encoding="utf-8"))[
            "run_id"
        ] == "replacement-run"

    assert not lock_path.exists()


def test_recent_corrupt_prediction_lock_is_not_reclaimed(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "prediction.lock"
    lock_path.write_text("{corrupt", encoding="utf-8")

    with pytest.raises(PredictionRunConflictError):
        with _prediction_lock(lock_path, run_id="blocked-run"):
            pass

    assert lock_path.read_text(encoding="utf-8") == "{corrupt"


def test_prediction_lock_normal_and_exception_exits_remove_owned_lock(
    tmp_path: Path,
) -> None:
    normal_path = tmp_path / "normal.lock"
    with _prediction_lock(normal_path, run_id="normal-run"):
        assert normal_path.exists()
    assert not normal_path.exists()

    exception_path = tmp_path / "exception.lock"
    with pytest.raises(RuntimeError, match="injected"):
        with _prediction_lock(exception_path, run_id="exception-run"):
            assert exception_path.exists()
            raise RuntimeError("injected")
    assert not exception_path.exists()


def test_real_lifecycle_initialization_records_request_and_verifies_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COURTVISION_LIFECYCLE_SHADOW", "1")
    monkeypatch.delenv("COURTVISION_LIFECYCLE_OBSERVATIONS", raising=False)
    prediction_date = "2026-07-25"
    board = _operator_fixture(prediction_date)["elite_props"].iloc[[0]].copy()
    engine = _FixtureEngine(
        {
            "elite_props": board,
            "selected_props": board,
            "summary": {"selected_count": 1, "elite_count": 1},
        }
    )
    board_path = (
        tmp_path
        / "outputs"
        / "runtime"
        / "operator"
        / f"elite_board_{prediction_date}.csv"
    )

    def publish(
        request: PredictionRequest,
        run_id: str,
        prediction: EnginePrediction,
    ) -> dict[str, Path]:
        publish_dataframe(
            board_path,
            prediction.outputs["elite_props"],
            prediction_date=prediction_date,
            caller="lifecycle-positive-test",
            artifact_label="elite_board",
            protect_existing=True,
        )
        return {"elite_board": board_path}

    lifecycle_root = tmp_path / "lifecycle"
    service = PredictionApplicationService(
        registry=PredictionEngineRegistry([engine]),
        publisher=CallbackPredictionPublisher(
            publish,
            primary_artifact_label="elite_board",
        ),
        lifecycle=ShadowPredictionLifecycle(
            repository_root=Path(__file__).resolve().parents[1],
            lifecycle_root=lifecycle_root,
        ),
    )
    result = service.run(
        PredictionRequest(
            sport="nba",
            prediction_date=prediction_date,
            mode="production",
            run_id="nba-positive-lifecycle-run",
            out_dir=str(tmp_path / "outputs"),
            metadata={
                "entrypoint": "positive_lifecycle_test",
                "command": "positive lifecycle integration",
            },
        )
    )

    assert result.status == "SUCCESS"
    assert result.lifecycle_status == "PASS"
    segments = completed_segment_directories(lifecycle_root)
    assert len(segments) == 1
    verification = verify_segment(segments[0], lifecycle_root=lifecycle_root)
    assert verification.ok, verification.violations
    events = read_segment_events(segments[0])
    assert [event.event_type for event in events] == [
        "RUN_STARTED",
        "PREDICTION_PUBLISHED",
        "RUN_COMPLETED",
    ]
    assert all(event.actor_id == "prediction_application" for event in events)
    started_payload = json.loads(events[0].payload_json)
    assert started_payload["prediction_request"] == {
        "actor_id": "prediction_application",
        "command": "positive lifecycle integration",
        "entrypoint": "positive_lifecycle_test",
        "mode": "production",
        "prediction_date": prediction_date,
        "run_id": "nba-positive-lifecycle-run",
        "sport": "nba",
    }
    application_manifest = json.loads(
        Path(result.manifest_path).read_text(encoding="utf-8")
    )
    assert application_manifest["lifecycle_status"] == "PASS"
    assert application_manifest["run_id"] == result.run_id
    assert application_manifest["artifacts"]["elite_board"]["sha256"]
