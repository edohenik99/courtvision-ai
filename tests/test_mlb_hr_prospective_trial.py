from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import urllib.request

import pytest

from courtvision.sports.mlb.training import hr_prospective_trial as trial
from courtvision.sports.mlb.training import hr_research_baseline as baseline


NOW = datetime(2026, 8, 6, 17, 0, tzinfo=timezone.utc)
CLOSING_NOW = datetime(2026, 8, 6, 22, 45, tzinfo=timezone.utc)
SETTLEMENT_NOW = datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc)


def _clock(value: datetime = NOW):
    return lambda: value


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_csv(
    path: Path,
    columns: tuple[str, ...],
    rows: list[dict[str, object]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
            path.stat().st_mtime_ns,
        )
        for path in root.rglob("*")
        if path.is_file()
    }


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _model_payload(model_id: str, model_version: str) -> dict[str, object]:
    numeric = {
        name: {"mean": 0.0, "stdev": 1.0}
        for name in baseline.NUMERIC_MODEL_FEATURES
    }
    feature_order = ["intercept"]
    for name in baseline.NUMERIC_MODEL_FEATURES:
        feature_order.extend((f"{name}__z", f"{name}__missing"))
    return {
        "schema_version": baseline.MODEL_BUNDLE_SCHEMA_VERSION,
        "model_id": model_id,
        "model_version": model_version,
        "algorithm": "logistic_regression_gradient_descent",
        "parameters": {
            "iterations": 1200,
            "learning_rate": 0.08,
            "l2_penalty": 0.01,
            "class_weighting": "none",
            "calibration": "identity_logistic_probability",
        },
        "feature_schema_version": baseline.FEATURE_SCHEMA_VERSION,
        "required_input_columns": list(baseline.MODEL_REQUIRED_INPUT_COLUMNS),
        "numeric_features": list(baseline.NUMERIC_MODEL_FEATURES),
        "categorical_features": list(baseline.CATEGORICAL_MODEL_FEATURES),
        "preprocessing": {
            "numeric": numeric,
            "categorical_levels": {"sportsbook": []},
            "feature_order": feature_order,
        },
        "feature_order": feature_order,
        "weights": [0.0] * len(feature_order),
        "research_label": baseline.RESEARCH_ONLY_LABEL,
        "approval_status": "not_approved",
        "eligible_for_betting": False,
        "kelly_eligible": False,
    }


def _refresh_model_bundle(model_dir: Path) -> None:
    model_path = model_dir / "model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    metrics = json.loads((model_dir / "metrics.json").read_text(encoding="utf-8"))
    metadata_path = model_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["model_json_sha256"] = _sha(model_path)
    metadata["evaluation_metrics"] = metrics
    _write_json(metadata_path, metadata)
    manifest_path = model_dir / "bundle_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {}
    )
    manifest.update(
        {
            "model_id": model["model_id"],
            "metadata_json_sha256": _sha(metadata_path),
            "model_json_sha256": _sha(model_path),
            "metrics_json_sha256": _sha(model_dir / "metrics.json"),
            "research_label": baseline.RESEARCH_ONLY_LABEL,
            "approval_status": "not_approved",
        }
    )
    _write_json(manifest_path, manifest)


def _create_model_bundle(
    model_dir: Path,
    *,
    model_id: str = "mlb-hr-logreg-baseline-test",
    model_version: str = "test-v1",
) -> Path:
    model_dir.mkdir(parents=True)
    model = _model_payload(model_id, model_version)
    _write_json(model_dir / "model.json", model)
    metrics: dict[str, object] = {}
    _write_json(model_dir / "metrics.json", metrics)
    metadata = {
        "schema_version": baseline.MODEL_BUNDLE_SCHEMA_VERSION,
        "model_id": model_id,
        "model_version": model_version,
        "training_timestamp": "2026-08-01T00:00:00Z",
        "training_date_range": {"start": "2026-07-01", "end": "2026-07-31"},
        "feature_schema_version": baseline.FEATURE_SCHEMA_VERSION,
        "feature_names": model["feature_order"],
        "algorithm": model["algorithm"],
        "parameters": model["parameters"],
        "preprocessing_configuration": model["preprocessing"],
        "training_data_hash": "a" * 64,
        "training_data_path": "outputs/research/test/features.csv",
        "row_counts": {"train": 10, "validation": 2, "test": 2},
        "evaluation_metrics": metrics,
        "calibration_metrics": {},
        "exclusion_counts": {},
        "source_commit_sha": "b" * 40,
        "dependency_versions": {"python": "3.13"},
        "model_json_sha256": _sha(model_dir / "model.json"),
        "research_label": baseline.RESEARCH_ONLY_LABEL,
        "approval_status": "not_approved",
        "official_pick_status": "not_official_not_validated",
    }
    _write_json(model_dir / "metadata.json", metadata)
    (model_dir / "model_card.md").write_text(
        f"# Model\n\n{baseline.RESEARCH_ONLY_LABEL}\n\nModel ID: {model_id}\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_json(model_dir / "bundle_manifest.json", {})
    _refresh_model_bundle(model_dir)
    return model_dir


def _odds_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "snapshot_time": "2026-08-06T15:00:00Z",
        "event_id": "event-1",
        "commence_time": "2026-08-06T23:00:00Z",
        "home_team": "Toronto Blue Jays",
        "away_team": "New York Yankees",
        "bookmaker_key": "draftkings",
        "bookmaker": "DraftKings",
        "market": "batter_home_runs_alternate",
        "player": "Alpha Batter",
        "side": "Over",
        "price": 400,
        "point": 0.5,
    }
    row.update(overrides)
    return row


def _result_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "event-1",
        "player": "Alpha Batter",
        "actual_home_runs": 1,
        "game_status": "final",
        "result_reason": "",
    }
    row.update(overrides)
    return row


@pytest.fixture
def workspace(tmp_path: Path) -> dict[str, Path]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "CourtVision Tests")
    (repository / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-q", "-m", "initial")
    model_dir = _create_model_bundle(
        repository / "outputs" / "research" / "model" / "test-v1"
    )
    odds = repository / "outputs" / "inputs" / "odds.csv"
    _write_csv(odds, baseline.ODDS_REQUIRED_COLUMNS, [_odds_row()])
    results = repository / "outputs" / "inputs" / "results.csv"
    _write_csv(
        results,
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row()],
    )
    return {
        "repository": repository,
        "model": model_dir,
        "odds": odds,
        "results": results,
        "trial": repository / "outputs" / "prospective_trial",
    }


def _activate(paths: dict[str, Path], **kwargs: object) -> trial.ControlActivationResult:
    return trial.activate_prospective_control(
        model_dir=paths["model"],
        trial_root=paths["trial"],
        repository_root=paths["repository"],
        clock=_clock(),
        **kwargs,
    )


def _run(
    paths: dict[str, Path],
    control: trial.ControlActivationResult,
    **kwargs: object,
) -> trial.ProspectivePaperRunResult:
    return trial.run_prospective_paper_day(
        target_date="2026-08-06",
        control_dir=control.control_dir,
        odds_csv=paths["odds"],
        trial_root=paths["trial"],
        repository_root=paths["repository"],
        clock=_clock(),
        **kwargs,
    )


def test_valid_bundle_activates_immutable_control(workspace: dict[str, Path]) -> None:
    result = _activate(workspace)
    manifest_path = result.control_dir / trial.CONTROL_MANIFEST_FILENAME

    assert result.control_id.startswith("mlb-hr-control-v1-")
    assert manifest_path.is_file()
    assert _sha(manifest_path) == result.control_manifest_digest


def test_control_identity_is_deterministic_and_replay_preserves_bytes_and_mtime(
    workspace: dict[str, Path],
) -> None:
    first = _activate(workspace)
    path = first.control_dir / trial.CONTROL_MANIFEST_FILENAME
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    second = trial.activate_prospective_control(
        model_dir=workspace["model"],
        trial_root=workspace["trial"],
        repository_root=workspace["repository"],
        clock=_clock(datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc)),
    )

    assert second.control_id == first.control_id
    assert second.replayed_existing_control is True
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_configuration_mapping_order_does_not_change_control_identity(
    workspace: dict[str, Path],
) -> None:
    first = _activate(workspace, activation_configuration={"b": 2, "a": 1})
    second = trial.activate_prospective_control(
        model_dir=workspace["model"],
        trial_root=workspace["trial"],
        repository_root=workspace["repository"],
        activation_configuration={"a": 1, "b": 2},
        clock=_clock(),
    )
    assert second.control_id == first.control_id


def test_model_file_change_changes_control_identity(workspace: dict[str, Path]) -> None:
    first = _activate(workspace)
    model_path = workspace["model"] / "model.json"
    payload = json.loads(model_path.read_text(encoding="utf-8"))
    payload["weights"][0] = 0.1
    _write_json(model_path, payload)
    _refresh_model_bundle(workspace["model"])
    second = _activate(workspace)
    assert second.control_id != first.control_id


def test_bundle_manifest_byte_change_changes_control_identity(
    workspace: dict[str, Path],
) -> None:
    first = _activate(workspace)
    path = workspace["model"] / "bundle_manifest.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["audit_note"] = "same model, changed complete manifest bytes"
    _write_json(path, payload)
    second = _activate(workspace)
    assert second.control_id != first.control_id


def test_git_commit_and_fingerprint_change_control_identity(
    workspace: dict[str, Path],
) -> None:
    first = _activate(workspace)
    tracked = workspace["repository"] / "tracked.txt"
    tracked.write_text("v2\n", encoding="utf-8")
    _git(workspace["repository"], "add", "tracked.txt")
    _git(workspace["repository"], "commit", "-q", "-m", "change")
    second = _activate(workspace)
    assert second.control_id != first.control_id


def test_dirty_git_fails_before_publication(workspace: dict[str, Path]) -> None:
    (workspace["repository"] / "tracked.txt").write_text("dirty\n", encoding="utf-8")
    blocked_root = workspace["repository"] / "outputs" / "blocked"
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="clean Git"):
        trial.activate_prospective_control(
            model_dir=workspace["model"],
            trial_root=blocked_root,
            repository_root=workspace["repository"],
            clock=_clock(),
        )
    assert not blocked_root.exists()


@pytest.mark.parametrize("filename", trial.REQUIRED_MODEL_FILES)
def test_missing_required_model_file_fails(
    workspace: dict[str, Path], filename: str
) -> None:
    (workspace["model"] / filename).unlink()
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="missing required file"):
        _activate(workspace)


def test_absolute_paths_and_mtimes_do_not_enter_control_identity(
    workspace: dict[str, Path],
) -> None:
    first = _activate(workspace)
    for filename in trial.REQUIRED_MODEL_FILES:
        os.utime(workspace["model"] / filename, None)
    second = _activate(workspace)
    manifest = json.loads(
        (first.control_dir / trial.CONTROL_MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert second.control_id == first.control_id
    assert str(workspace["repository"]) not in json.dumps(manifest["identity_material"])
    assert "mtime" not in json.dumps(manifest).casefold()


def test_conflicting_existing_control_fails_closed(workspace: dict[str, Path]) -> None:
    control = _activate(workspace)
    path = control.control_dir / trial.CONTROL_MANIFEST_FILENAME
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(trial.MLBHRProspectiveTrialConflictError):
        _activate(workspace)


def test_corrupt_model_file_fails_before_publication(workspace: dict[str, Path]) -> None:
    (workspace["model"] / "model.json").write_bytes(b"not-json\n")
    with pytest.raises(trial.MLBHRProspectiveTrialError):
        _activate(workspace)
    assert not workspace["trial"].exists()


def test_activation_has_no_latest_model_or_default_path_fallback() -> None:
    with pytest.raises(SystemExit) as caught:
        baseline.main(["activate-prospective-control"])
    assert caught.value.code == 2


def test_valid_control_generates_current_schema_and_exact_provenance(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    result = _run(workspace, control)
    rows = _read_csv(result.run_dir / "predictions.csv")  # type: ignore[operator]

    assert result.status == "completed"
    assert len(rows) == 1
    assert rows[0]["prediction_schema_version"] == trial.PREDICTION_SCHEMA_VERSION
    assert rows[0]["control_manifest_digest"] == control.control_manifest_digest
    assert rows[0]["model_bundle_manifest_digest"] == control.model_bundle_manifest_digest
    assert rows[0]["identity_status"] == "name_only_research"
    assert rows[0]["eligible_for_official_pick"] == "false"


def test_prediction_never_reads_results(workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    control = _activate(workspace)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("result reader invoked")

    monkeypatch.setattr(baseline, "_load_result_index", forbidden)
    assert _run(workspace, control, dry_run=True).prediction_count == 1


def test_prediction_timestamps_are_strictly_pregame_and_toronto_scoped(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    result = _run(workspace, control, dry_run=True)
    row = result.predictions[0]
    assert row["operating_date"] == "2026-08-06"
    assert row["operating_timezone"] == "America/Toronto"
    assert row["selected_snapshot_timestamp_utc"] <= row["prediction_timestamp_utc"]
    assert row["prediction_timestamp_utc"] < row["commence_time_utc"]


def test_complete_eligible_population_is_retained(workspace: dict[str, Path]) -> None:
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(player="Alpha Batter", price=120),
            _odds_row(player="Beta Batter", price=1200),
        ],
    )
    result = _run(workspace, _activate(workspace), dry_run=True)
    assert result.prediction_count == 2


def test_special_events_and_post_start_rows_are_explicitly_excluded(
    workspace: dict[str, Path],
) -> None:
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="all-star",
                home_team="National League",
                away_team="American League",
            ),
            _odds_row(event_id="started", commence_time="2026-08-06T16:00:00Z"),
        ],
    )
    result = _run(workspace, _activate(workspace), dry_run=True)
    reasons = {row["exclusion_reason"] for row in result.exclusions}
    assert result.prediction_count == 0
    assert baseline.SPECIAL_EVENT_EXCLUSION_REASON in reasons
    assert "game_already_started" in reasons


def test_reviewed_identity_is_used_and_conflict_is_quarantined(
    workspace: dict[str, Path],
) -> None:
    cache = workspace["repository"] / "outputs" / "inputs" / "identity.csv"
    base = {
        "cache_schema_version": baseline.IDENTITY_CACHE_SCHEMA_VERSION,
        "cache_record_id": "one",
        "sportsbook_player_name": "Alpha Batter",
        "normalized_player_name": "alpha batter",
        "mlb_player_id": "123",
        "canonical_mlb_name": "Alpha Batter",
        "identity_status": "resolved",
        "identity_method": "manual",
        "identity_source": "review",
        "resolved_at": "2026-08-01T00:00:00Z",
        "reviewed_at": "2026-08-01T01:00:00Z",
        "review_status": "reviewed",
        "mapping_version": "mapping-v1",
        "conflict_reason": "",
    }
    _write_csv(cache, baseline.IDENTITY_CACHE_COLUMNS, [base])
    control = _activate(workspace)
    resolved = _run(workspace, control, dry_run=True, identity_cache_csv=cache)
    conflict = dict(base)
    conflict.update(cache_record_id="two", mlb_player_id="456")
    _write_csv(cache, baseline.IDENTITY_CACHE_COLUMNS, [base, conflict])
    quarantined = _run(workspace, control, dry_run=True, identity_cache_csv=cache)

    assert resolved.predictions[0]["player_id"] == "123"
    assert resolved.predictions[0]["identity_mapping_version"] == "mapping-v1"
    assert quarantined.prediction_count == 0
    assert quarantined.exclusions[0]["identity_status"] == "identity_conflict_quarantined"


def test_source_odds_mutation_during_read_fails(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    control = _activate(workspace)
    original = baseline.build_live_hr_research_features

    def mutate(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        with workspace["odds"].open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return result

    monkeypatch.setattr(baseline, "build_live_hr_research_features", mutate)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="changed"):
        _run(workspace, control, dry_run=True)


def test_code_change_after_activation_blocks_prediction(workspace: dict[str, Path]) -> None:
    control = _activate(workspace)
    (workspace["repository"] / "tracked.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="Git"):
        _run(workspace, control, dry_run=True)


def test_identical_prediction_replay_is_idempotent_and_atomic(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    first = _run(workspace, control)
    second = _run(workspace, control)
    stages = list((control.control_dir / "dates" / "2026-08-06").glob(".staging-*"))
    ledger_rows = _read_csv(control.control_dir / "prospective_ledger.csv")

    assert second.replayed_existing_run is True
    assert second.prediction_run_id == first.prediction_run_id
    assert len([row for row in ledger_rows if row["record_type"] == "prediction"]) == 1
    assert stages == []


def test_conflicting_prediction_ledger_replay_fails_closed(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    changed = dict(run.predictions[0])
    changed["model_probability"] = "0.9"
    changed["probability_edge"] = "0.7"
    with pytest.raises(trial.MLBHRProspectiveTrialConflictError):
        trial._append_prediction_ledger_rows(  # type: ignore[attr-defined]
            ledger_path=control.control_dir / "prospective_ledger.csv",
            predictions=(changed,),
            prediction_manifest_digest=run.prediction_manifest_digest,
            predictions_csv_sha256=_sha(run.run_dir / "predictions.csv"),  # type: ignore[operator]
        )


def test_failure_before_prediction_publication_leaves_no_run_or_stage(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    manifest_path = control.control_dir / trial.CONTROL_MANIFEST_FILENAME
    before = (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns)

    def fail(stage: str) -> None:
        if stage == "before_prediction_publication":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _run(workspace, control, failure_hook=fail)
    assert (manifest_path.read_bytes(), manifest_path.stat().st_mtime_ns) == before
    assert not any(
        path.is_file()
        for path in control.control_dir.rglob("*")
        if path != manifest_path
    )


def test_ledger_linkage_failure_does_not_publish_completed_run(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)

    def fail(stage: str) -> None:
        if stage == "before_prediction_ledger_append":
            raise RuntimeError("injected")

    with pytest.raises(RuntimeError, match="injected"):
        _run(workspace, control, failure_hook=fail)
    date_root = control.control_dir / "dates" / "2026-08-06"
    assert not date_root.exists() or not any(
        path.name.startswith("hrv1-") for path in date_root.iterdir()
    )
    assert not (control.control_dir / "prospective_ledger.csv").exists()


def test_zero_prediction_changed_source_can_create_later_run(
    workspace: dict[str, Path],
) -> None:
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [_odds_row(commence_time="2026-08-07T23:00:00Z")],
    )
    control = _activate(workspace)
    empty = _run(workspace, control)
    _write_csv(workspace["odds"], baseline.ODDS_REQUIRED_COLUMNS, [_odds_row()])
    valid = _run(workspace, control)
    assert empty.status == "completed_no_predictions"
    assert valid.status == "completed"
    assert empty.prediction_run_id != valid.prediction_run_id


def test_legacy_and_rehearsal_prediction_csv_cannot_enter_v1_closing(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    legacy = workspace["repository"] / "outputs" / "legacy" / "predictions.csv"
    _write_csv(legacy, baseline.PREDICTION_COLUMNS, [])
    with pytest.raises(trial.MLBHRProspectiveTrialError):
        trial.capture_prospective_closing(
            control_dir=control.control_dir,
            predictions_csv=legacy,
            odds_csv=workspace["odds"],
            trial_root=workspace["trial"],
            clock=_clock(CLOSING_NOW),
        )


def _published_run(
    workspace: dict[str, Path],
) -> tuple[trial.ControlActivationResult, trial.ProspectivePaperRunResult]:
    control = _activate(workspace)
    return control, _run(workspace, control)


def _capture_closing(
    workspace: dict[str, Path],
    control: trial.ControlActivationResult,
    run: trial.ProspectivePaperRunResult,
    *,
    clock_value: datetime = CLOSING_NOW,
) -> dict[str, object]:
    return trial.capture_prospective_closing(
        control_dir=control.control_dir,
        predictions_csv=run.run_dir / "predictions.csv",  # type: ignore[operator]
        odds_csv=workspace["odds"],
        trial_root=workspace["trial"],
        clock=_clock(clock_value),
    )


def test_closing_prefers_same_book_latest_prestart(workspace: dict[str, Path]) -> None:
    control, run = _published_run(workspace)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(snapshot_time="2026-08-06T22:00:00Z", price=350),
            _odds_row(
                snapshot_time="2026-08-06T22:30:00Z",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=325,
            ),
        ],
    )
    result = _capture_closing(workspace, control, run)
    row = _read_csv(control.control_dir / "closing_lines.csv")[0]
    assert result["same_book_count"] == 1
    assert row["closing_american_odds"] == "350"


def test_closing_consensus_fallback_uses_existing_rule(workspace: dict[str, Path]) -> None:
    control, run = _published_run(workspace)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                snapshot_time="2026-08-06T22:30:00Z",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=325,
            )
        ],
    )
    result = _capture_closing(workspace, control, run)
    assert result["consensus_count"] == 1


def test_closing_rejects_post_start_observation_and_marks_missing(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [_odds_row(snapshot_time="2026-08-06T23:30:00Z", price=300)],
    )
    result = _capture_closing(workspace, control, run)
    row = _read_csv(control.control_dir / "closing_lines.csv")[0]
    assert result["missing_count"] == 1
    assert row["closing_status"] == "missing_prestart"
    assert row["closing_snapshot_time_utc"] == ""


def test_closing_missing_without_matching_observation_is_explicit(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    _write_csv(workspace["odds"], baseline.ODDS_REQUIRED_COLUMNS, [])
    _capture_closing(workspace, control, run)
    row = _read_csv(control.control_dir / "closing_lines.csv")[0]
    assert row["closing_status"] == "missing"


def test_closing_identical_append_is_idempotent(workspace: dict[str, Path]) -> None:
    control, run = _published_run(workspace)
    first = _capture_closing(workspace, control, run)
    before = (control.control_dir / "closing_lines.csv").read_bytes()
    second = _capture_closing(
        workspace,
        control,
        run,
        clock_value=datetime(2026, 8, 6, 22, 50, tzinfo=timezone.utc),
    )
    assert first["closing_rows_appended"] == 1
    assert second["closing_rows_appended"] == 0
    assert (control.control_dir / "closing_lines.csv").read_bytes() == before


def test_conflicting_closing_evidence_fails(workspace: dict[str, Path]) -> None:
    control, run = _published_run(workspace)
    _capture_closing(workspace, control, run)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [_odds_row(price=300)],
    )
    with pytest.raises(trial.MLBHRProspectiveTrialConflictError):
        _capture_closing(workspace, control, run)


def test_closing_requires_exact_canonical_ledger_linkage(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    (control.control_dir / "prospective_ledger.csv").unlink()
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="ledger linkage"):
        _capture_closing(workspace, control, run)


def test_closing_source_mutation_fails(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    control, run = _published_run(workspace)
    original = baseline.capture_closing_line_snapshots

    def mutate(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        with workspace["odds"].open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return result

    monkeypatch.setattr(baseline, "capture_closing_line_snapshots", mutate)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="changed"):
        _capture_closing(workspace, control, run)


def _settle(
    workspace: dict[str, Path],
    control: trial.ControlActivationResult,
    *,
    clock_value: datetime = SETTLEMENT_NOW,
) -> dict[str, object]:
    return trial.settle_prospective_paper_day(
        control_dir=control.control_dir,
        results_csv=workspace["results"],
        trial_root=workspace["trial"],
        clock=_clock(clock_value),
    )


def test_final_strict_result_appends_one_settlement(workspace: dict[str, Path]) -> None:
    control, _ = _published_run(workspace)
    result = _settle(workspace, control)
    rows = _read_csv(control.control_dir / "prospective_ledger.csv")
    assert result["settlements_appended"] == 1
    assert rows[-1]["record_type"] == "settlement"
    assert rows[-1]["final_hr_outcome"] == "1"
    assert rows[-1]["grade"] == "win"


def test_settlement_uses_strict_event_and_normalized_player_join(
    workspace: dict[str, Path],
) -> None:
    control, _ = _published_run(workspace)
    _write_csv(
        workspace["results"],
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row(player="Alpha-Batter")],
    )
    assert _settle(workspace, control)["settlements_appended"] == 1


def test_settlement_preserves_prediction_ledger_bytes_as_prefix(
    workspace: dict[str, Path],
) -> None:
    control, _ = _published_run(workspace)
    ledger = control.control_dir / "prospective_ledger.csv"
    before = ledger.read_bytes()
    _settle(workspace, control)
    assert ledger.read_bytes().startswith(before)


def test_void_and_unresolved_do_not_become_wins_or_losses(
    workspace: dict[str, Path],
) -> None:
    control, _ = _published_run(workspace)
    _write_csv(
        workspace["results"],
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row(game_status="void", actual_home_runs="")],
    )
    void = _settle(workspace, control)
    settlement = _read_csv(control.control_dir / "prospective_ledger.csv")[-1]
    assert void["settlements_appended"] == 1
    assert settlement["grade"] == "void"
    assert settlement["unit_profit_loss"] == ""


@pytest.mark.parametrize("status", ["void_candidate", "manual_review_required", "pending"])
def test_nonfinal_result_status_remains_pending(
    workspace: dict[str, Path], status: str
) -> None:
    control, _ = _published_run(workspace)
    _write_csv(
        workspace["results"],
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row(game_status=status, actual_home_runs="")],
    )
    result = _settle(workspace, control)
    assert result["settlements_appended"] == 0
    assert result["pending_predictions"] == 1


def test_identical_settlement_is_idempotent(workspace: dict[str, Path]) -> None:
    control, _ = _published_run(workspace)
    first = _settle(workspace, control)
    second = _settle(
        workspace,
        control,
        clock_value=datetime(2026, 8, 7, 5, 30, tzinfo=timezone.utc),
    )
    assert first["settlements_appended"] == 1
    assert second["settlements_appended"] == 0
    assert second["skipped_existing_settlements"] == 1


def test_conflicting_final_settlement_fails(workspace: dict[str, Path]) -> None:
    control, _ = _published_run(workspace)
    _settle(workspace, control)
    _write_csv(
        workspace["results"],
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row(actual_home_runs=0)],
    )
    with pytest.raises(trial.MLBHRProspectiveTrialConflictError):
        _settle(workspace, control)


def test_result_mutation_during_read_fails(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    control, _ = _published_run(workspace)
    original = baseline._load_result_index

    def mutate(*args: object, **kwargs: object):
        result = original(*args, **kwargs)
        with workspace["results"].open("a", encoding="utf-8") as handle:
            handle.write("\n")
        return result

    monkeypatch.setattr(baseline, "_load_result_index", mutate)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="changed"):
        _settle(workspace, control)


@pytest.mark.parametrize(
    "missing_field",
    [
        "original_american_odds",
        "original_decimal_odds",
        "original_implied_probability",
    ],
)
def test_unit_return_requires_complete_original_price(
    workspace: dict[str, Path], missing_field: str
) -> None:
    control, _ = _published_run(workspace)
    ledger = control.control_dir / "prospective_ledger.csv"
    prediction = _read_csv(ledger)[0]
    prediction[missing_field] = ""
    settlement = trial._settlement_row(  # type: ignore[attr-defined]
        prediction=prediction,
        result={key: str(value) for key, value in _result_row().items()},
        results_digest="a" * 64,
        settlement_timestamp=SETTLEMENT_NOW,
    )
    assert settlement is not None
    assert settlement["unit_profit_loss"] == ""
    assert settlement["integrity_status"] == "settled_missing_complete_original_price"


def test_prospective_schema_has_no_kelly_stake_bankroll_or_official_pick_path() -> None:
    joined = " ".join((*trial.PREDICTION_COLUMNS, *trial.LEDGER_COLUMNS)).casefold()
    assert "kelly" not in joined
    assert "stake" not in joined
    assert "bankroll" not in joined
    assert "official_pick_id" not in joined


def test_settlement_rejects_ledger_without_committed_prediction(
    workspace: dict[str, Path],
) -> None:
    control, _ = _published_run(workspace)
    ledger_path = control.control_dir / "prospective_ledger.csv"
    prediction = _read_csv(ledger_path)[0]
    orphan = dict(prediction)
    orphan["record_type"] = "settlement"
    orphan["settlement_status"] = "settled"
    orphan["strict_result_status"] = "final"
    orphan["final_hr_outcome"] = "1"
    orphan["grade"] = "win"
    _write_csv(ledger_path, trial.LEDGER_COLUMNS, [orphan])
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="not exact|no committed"):
        _settle(workspace, control)


def test_grade_files_are_never_read_as_settlement_labels(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    control, _ = _published_run(workspace)
    grade_path = workspace["repository"] / "outputs" / "inputs" / "grade.csv"
    grade_path.write_text("prediction_id,grade\nforged,win\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def guarded_read_bytes(path: Path) -> bytes:
        if path == grade_path:
            raise AssertionError("grade file was read")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    assert _settle(workspace, control)["settlements_appended"] == 1


def test_visible_valid_trial_lock_reports_busy(workspace: dict[str, Path]) -> None:
    first = trial._TrialStoreLock(  # type: ignore[attr-defined]
        workspace["trial"], operation="first", control_id="control", clock=_clock()
    )
    first.acquire()
    try:
        second = trial._TrialStoreLock(  # type: ignore[attr-defined]
            workspace["trial"], operation="second", control_id="control", clock=_clock()
        )
        with pytest.raises(trial.MLBHRProspectiveTrialBusyError):
            second.acquire()
    finally:
        first.release()


def test_malformed_trial_lock_fails_closed_and_is_not_deleted(
    workspace: dict[str, Path],
) -> None:
    lock_path = workspace["trial"] / trial.TRIAL_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("{}\n", encoding="utf-8")
    lock = trial._TrialStoreLock(  # type: ignore[attr-defined]
        workspace["trial"], operation="test", control_id="control", clock=_clock()
    )
    with pytest.raises(trial.MLBHRProspectiveTrialLockError):
        lock.acquire()
    assert lock_path.exists()


def test_only_verified_lock_owner_removes_unchanged_lock(
    workspace: dict[str, Path],
) -> None:
    lock = trial._TrialStoreLock(  # type: ignore[attr-defined]
        workspace["trial"], operation="test", control_id="control", clock=_clock()
    )
    lock.acquire()
    lock.path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(trial.MLBHRProspectiveTrialLockError):
        lock.release()
    assert lock.path.exists()


def test_windows_permission_error_leaves_visible_lock_untouched(
    workspace: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = trial._TrialStoreLock(  # type: ignore[attr-defined]
        workspace["trial"], operation="owner", control_id="control", clock=_clock()
    )
    owner.acquire()
    before = owner.path.read_bytes()
    real_open = trial.os.open

    def denied(path: object, flags: int, mode: int = 0o777) -> int:
        if str(path) == str(owner.path):
            raise PermissionError("denied")
        return real_open(path, flags, mode)

    monkeypatch.setattr(trial.os, "open", denied)
    contender = trial._TrialStoreLock(  # type: ignore[attr-defined]
        workspace["trial"], operation="other", control_id="control", clock=_clock()
    )
    with pytest.raises(trial.MLBHRProspectiveTrialBusyError):
        contender.acquire()
    assert owner.path.read_bytes() == before
    monkeypatch.setattr(trial.os, "open", real_open)
    owner.release()


def test_concurrent_identical_predictions_publish_one_admissible_run(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    barrier = threading.Barrier(2)

    def attempt() -> object:
        barrier.wait()
        try:
            return _run(workspace, control)
        except trial.MLBHRProspectiveTrialBusyError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: attempt(), range(2)))
    assert any(isinstance(result, trial.ProspectivePaperRunResult) for result in results)
    run_dirs = [
        path
        for path in (control.control_dir / "dates" / "2026-08-06").iterdir()
        if path.is_dir() and path.name.startswith("hrv1-")
    ]
    assert len(run_dirs) == 1
    ledger_rows = _read_csv(control.control_dir / "prospective_ledger.csv")
    assert len([row for row in ledger_rows if row["record_type"] == "prediction"]) == 1


def test_zero_row_status_report_is_read_only_and_not_measurable(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    before = {
        path.relative_to(control.control_dir).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in control.control_dir.rglob("*")
        if path.is_file()
    }
    report = trial.report_prospective_status(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )
    after = {
        path.relative_to(control.control_dir).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in control.control_dir.rglob("*")
        if path.is_file()
    }
    assert report["metrics"]["status"] == "not_measurable"  # type: ignore[index]
    assert before == after


def test_settled_status_report_has_deterministic_counts_and_gates(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    _capture_closing(workspace, control, run)
    _settle(workspace, control)
    report = trial.report_prospective_status(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )
    counts = report["counts"]
    assert counts["committed_predictions"] == 1  # type: ignore[index]
    assert counts["settled_predictions"] == 1  # type: ignore[index]
    assert counts["positive_hr_outcomes"] == 1  # type: ignore[index]
    assert report["closing_line_coverage"]["captured"] == 1  # type: ignore[index]
    assert report["gate_progress"]["eligible_predictions"]["current_value"] == 1  # type: ignore[index]


def test_prospective_health_cli_is_registered_read_only_and_deterministic(
    workspace: dict[str, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _activate(workspace)
    before = _snapshot_tree(workspace["trial"])

    def forbidden_boundary(*args: object, **kwargs: object) -> object:
        raise AssertionError("health report reached a prohibited boundary")

    for name in (
        "_TrialStoreLock",
        "_transactional_append_csv",
        "_write_exclusive",
        "_safe_remove_owned_stage",
        "_append_prediction_ledger_rows",
        "activate_prospective_control",
        "run_prospective_paper_day",
        "capture_prospective_closing",
        "settle_prospective_paper_day",
    ):
        monkeypatch.setattr(trial, name, forbidden_boundary)
    for name in (
        "_write_csv_create_once",
        "_write_json_create_once",
        "_write_text_create_once",
        "write_feature_artifacts",
        "train_research_logistic_baseline",
        "generate_daily_research_predictions",
        "append_predictions_to_ledger",
        "settle_prediction_ledger",
        "capture_closing_line_snapshots",
        "run_daily_research",
        "build_live_hr_research_features",
        "load_model_bundle",
    ):
        monkeypatch.setattr(baseline, name, forbidden_boundary)
    monkeypatch.setattr(socket, "create_connection", forbidden_boundary)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden_boundary)
    argv = [
        "report-prospective-health",
        "--control-dir",
        str(control.control_dir),
        "--trial-root",
        str(workspace["trial"]),
    ]

    assert baseline.main(argv) == 0
    first = capsys.readouterr().out
    assert baseline.main(argv) == 0
    second = capsys.readouterr().out
    payload = json.loads(first)

    assert first == second
    assert payload["schema_version"] == trial.HEALTH_SCHEMA_VERSION
    assert payload["daily_evidence"] == []
    assert payload["performance"] == {
        "brier_score": None,
        "calibration_error": None,
        "flat_one_unit_profit_loss": None,
        "hit_rate": None,
        "log_loss": None,
        "settled_count": 0,
    }
    assert payload["control"]["research_only"] is True
    assert payload["control"]["approval_status"] == "not_approved"
    assert payload["control"]["eligible_for_betting"] is False
    assert payload["control"]["eligible_for_official_pick"] is False
    assert _snapshot_tree(workspace["trial"]) == before


def test_prospective_health_cumulative_counts_performance_and_gate_passthrough(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    _capture_closing(workspace, control, run)
    _settle(workspace, control)

    health = trial.report_prospective_health(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )
    status = trial.report_prospective_status(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )

    assert health["evidence"] == {
        "prospective_operating_dates": 1,
        "committed_predictions": 1,
        "settled_predictions": 1,
        "pending_predictions": 0,
        "void_predictions": 0,
        "unique_games": 1,
        "unique_players": 1,
        "positive_hr_outcomes": 1,
        "identity_status_coverage": {"name_only_research": 1},
        "closing_prediction_count": 1,
        "closing_captured": 1,
        "closing_explicit_missing": 0,
        "predictions_without_closing_record": 0,
        "closing_coverage_rate": 1.0,
    }
    performance = health["performance"]
    assert performance["settled_count"] == 1  # type: ignore[index]
    assert performance["hit_rate"] == 1.0  # type: ignore[index]
    assert performance["flat_one_unit_profit_loss"] == 4.0  # type: ignore[index]
    assert performance["brier_score"] is not None  # type: ignore[index]
    assert performance["calibration_error"] is not None  # type: ignore[index]
    assert performance["log_loss"] is not None  # type: ignore[index]
    assert health["gates"] == status["gate_progress"]


@pytest.mark.parametrize(
    ("bookmaker_key", "expected_status"),
    [
        ("draftkings", "captured_same_book"),
        ("", "captured_consensus"),
    ],
)
def test_prospective_health_accepts_writer_optional_bookmaker_metadata(
    workspace: dict[str, Path],
    bookmaker_key: str,
    expected_status: str,
) -> None:
    control, run = _published_run(workspace)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                bookmaker_key=bookmaker_key,
                bookmaker="",
                price=350,
            )
        ],
    )

    _capture_closing(workspace, control, run)
    closing = _read_csv(control.control_dir / "closing_lines.csv")[0]
    health = trial.report_prospective_health(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )
    daily = health["daily_evidence"][0]  # type: ignore[index]

    assert closing["closing_status"] == expected_status
    assert closing["closing_sportsbook_name"] == ""
    assert daily["closing_same_book"] == (  # type: ignore[index]
        1 if expected_status == "captured_same_book" else 0
    )
    assert daily["closing_consensus"] == (  # type: ignore[index]
        1 if expected_status == "captured_consensus" else 0
    )
    assert daily["usable_closing"] == 1  # type: ignore[index]


def test_prospective_health_daily_closing_and_settlement_accounting(
    workspace: dict[str, Path],
) -> None:
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(event_id="event-same", player="Same Batter"),
            _odds_row(event_id="event-consensus", player="Consensus Batter"),
            _odds_row(event_id="event-missing-1", player="Missing Batter One"),
            _odds_row(event_id="event-missing-2", player="Missing Batter Two"),
        ],
    )
    control = _activate(workspace)
    first_run = _run(workspace, control)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                event_id="event-same",
                player="Same Batter",
                price=350,
            ),
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                event_id="event-consensus",
                player="Consensus Batter",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=325,
            ),
        ],
    )
    _capture_closing(workspace, control, first_run)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [_odds_row(event_id="event-no-closing", player="No Closing Batter")],
    )
    _run(workspace, control)
    _write_csv(
        workspace["results"],
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [
            _result_row(event_id="event-same", player="Same Batter"),
            _result_row(
                event_id="event-consensus",
                player="Consensus Batter",
                actual_home_runs=0,
            ),
            _result_row(
                event_id="event-missing-1",
                player="Missing Batter One",
                actual_home_runs="",
                game_status="void",
            ),
            _result_row(
                event_id="event-missing-2",
                player="Missing Batter Two",
                actual_home_runs="",
                game_status="void",
            ),
        ],
    )
    _settle(workspace, control)

    health = trial.report_prospective_health(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )

    assert health["evidence"]["committed_predictions"] == 5  # type: ignore[index]
    assert health["evidence"]["settled_predictions"] == 2  # type: ignore[index]
    assert health["evidence"]["pending_predictions"] == 1  # type: ignore[index]
    assert health["evidence"]["void_predictions"] == 2  # type: ignore[index]
    assert health["evidence"]["closing_captured"] == 2  # type: ignore[index]
    assert health["evidence"]["closing_explicit_missing"] == 2  # type: ignore[index]
    assert health["evidence"]["predictions_without_closing_record"] == 1  # type: ignore[index]
    evidence = health["evidence"]
    assert evidence["committed_predictions"] == (  # type: ignore[index]
        evidence["settled_predictions"]  # type: ignore[index]
        + evidence["void_predictions"]  # type: ignore[index]
        + evidence["pending_predictions"]  # type: ignore[index]
    )
    assert health["daily_evidence"] == [
        {
            "operating_date": "2026-08-06",
            "predictions": 5,
            "settled": 2,
            "pending": 1,
            "void": 2,
            "positive_hr_outcomes": 1,
            "closing_same_book": 1,
            "closing_consensus": 1,
            "closing_explicit_missing": 2,
            "predictions_without_closing_record": 1,
            "usable_closing": 2,
            "closing_coverage_rate": 0.4,
        }
    ]
    daily = health["daily_evidence"][0]  # type: ignore[index]
    assert daily["usable_closing"] == (  # type: ignore[index]
        daily["closing_same_book"] + daily["closing_consensus"]  # type: ignore[index]
    )
    assert daily["predictions"] == (  # type: ignore[index]
        daily["usable_closing"]  # type: ignore[index]
        + daily["closing_explicit_missing"]  # type: ignore[index]
        + daily["predictions_without_closing_record"]  # type: ignore[index]
    )


def test_prospective_health_groups_daily_evidence_in_date_order(
    workspace: dict[str, Path],
) -> None:
    control = _activate(workspace)
    _run(workspace, control)
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                snapshot_time="2026-08-07T15:00:00Z",
                event_id="event-2",
                commence_time="2026-08-07T23:00:00Z",
                player="Second Batter",
            )
        ],
    )
    trial.run_prospective_paper_day(
        target_date="2026-08-07",
        control_dir=control.control_dir,
        odds_csv=workspace["odds"],
        trial_root=workspace["trial"],
        repository_root=workspace["repository"],
        clock=_clock(datetime(2026, 8, 7, 17, 0, tzinfo=timezone.utc)),
    )

    health = trial.report_prospective_health(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )

    assert health["evidence"]["prospective_operating_dates"] == 2  # type: ignore[index]
    assert [
        row["operating_date"] for row in health["daily_evidence"]  # type: ignore[union-attr]
    ] == ["2026-08-06", "2026-08-07"]
    assert [
        row["predictions_without_closing_record"]
        for row in health["daily_evidence"]  # type: ignore[union-attr]
    ] == [1, 1]


def test_prospective_health_includes_validated_zero_prediction_date(
    workspace: dict[str, Path],
) -> None:
    _write_csv(
        workspace["odds"],
        baseline.ODDS_REQUIRED_COLUMNS,
        [_odds_row(commence_time="2026-08-07T23:00:00Z")],
    )
    control = _activate(workspace)
    run = _run(workspace, control)

    health = trial.report_prospective_health(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )

    assert run.status == "completed_no_predictions"
    assert health["evidence"]["prospective_operating_dates"] == 1  # type: ignore[index]
    assert health["evidence"]["committed_predictions"] == 0  # type: ignore[index]
    assert health["gates"]["prospective_prediction_dates"]["current_value"] == 0  # type: ignore[index]
    assert health["daily_evidence"] == [
        {
            "operating_date": "2026-08-06",
            "predictions": 0,
            "settled": 0,
            "pending": 0,
            "void": 0,
            "positive_hr_outcomes": 0,
            "closing_same_book": 0,
            "closing_consensus": 0,
            "closing_explicit_missing": 0,
            "predictions_without_closing_record": 0,
            "usable_closing": 0,
            "closing_coverage_rate": None,
        }
    ]


@pytest.mark.parametrize(
    "artifact_name",
    ["predictions.csv", trial.PREDICTION_MANIFEST_FILENAME],
)
def test_prospective_health_fails_if_run_artifact_changes_after_validation(
    workspace: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
) -> None:
    control, run = _published_run(workspace)
    artifact_path = run.run_dir / artifact_name  # type: ignore[operator]
    original_daily = trial._daily_health_evidence  # type: ignore[attr-defined]

    def mutate_after_validation(**kwargs: object) -> list[dict[str, object]]:
        result = original_daily(**kwargs)  # type: ignore[arg-type]
        artifact_path.write_bytes(artifact_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(trial, "_daily_health_evidence", mutate_after_validation)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="changed"):
        trial.report_prospective_health(
            control_dir=control.control_dir,
            trial_root=workspace["trial"],
        )


def test_prospective_health_fails_if_control_changes_after_validation(
    workspace: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control = _activate(workspace)
    manifest_path = control.control_dir / trial.CONTROL_MANIFEST_FILENAME
    original_daily = trial._daily_health_evidence  # type: ignore[attr-defined]

    def mutate_after_validation(**kwargs: object) -> list[dict[str, object]]:
        result = original_daily(**kwargs)  # type: ignore[arg-type]
        manifest_path.write_bytes(manifest_path.read_bytes() + b"\n")
        return result

    monkeypatch.setattr(trial, "_daily_health_evidence", mutate_after_validation)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="changed"):
        trial.report_prospective_health(
            control_dir=control.control_dir,
            trial_root=workspace["trial"],
        )


@pytest.mark.parametrize("operation", ["add_run", "remove_run", "rename_date"])
def test_prospective_health_fails_if_membership_changes_after_validation(
    workspace: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    control, run = _published_run(workspace)
    run_dir = run.run_dir
    assert run_dir is not None
    date_dir = run_dir.parent
    original_daily = trial._daily_health_evidence  # type: ignore[attr-defined]

    def mutate_after_validation(**kwargs: object) -> list[dict[str, object]]:
        result = original_daily(**kwargs)  # type: ignore[arg-type]
        if operation == "add_run":
            (date_dir / "added-run").mkdir()
        elif operation == "remove_run":
            run_dir.rename(workspace["repository"] / "removed-run")
        else:
            date_dir.rename(date_dir.with_name("2026-08-07"))
        return result

    monkeypatch.setattr(trial, "_daily_health_evidence", mutate_after_validation)
    with pytest.raises(trial.MLBHRProspectiveTrialError):
        trial.report_prospective_health(
            control_dir=control.control_dir,
            trial_root=workspace["trial"],
        )


@pytest.mark.parametrize("mutation", ["malformed", "conflicting"])
def test_prospective_health_fails_closed_on_invalid_closing_evidence(
    workspace: dict[str, Path], mutation: str
) -> None:
    control, run = _published_run(workspace)
    _capture_closing(workspace, control, run)
    closing_path = control.control_dir / "closing_lines.csv"
    rows = _read_csv(closing_path)
    if mutation == "malformed":
        rows[0]["integrity_status"] = "unverified"
    else:
        conflicting = dict(rows[0])
        conflicting["closing_record_id"] = "conflicting-record"
        rows.append(conflicting)
    _write_csv(closing_path, trial.CLOSING_COLUMNS, rows)

    with pytest.raises(trial.MLBHRProspectiveTrialError):
        trial.report_prospective_health(
            control_dir=control.control_dir,
            trial_root=workspace["trial"],
        )


def test_prospective_health_fails_closed_on_artifact_integrity_finding(
    workspace: dict[str, Path],
) -> None:
    control, run = _published_run(workspace)
    with (run.run_dir / "predictions.csv").open(  # type: ignore[operator]
        "a", encoding="utf-8"
    ) as handle:
        handle.write("\n")

    with pytest.raises(trial.MLBHRProspectiveTrialError, match="integrity"):
        trial.report_prospective_health(
            control_dir=control.control_dir,
            trial_root=workspace["trial"],
        )


def test_report_never_counts_historical_rehearsal_lifecycle_or_grade_rows(
    workspace: dict[str, Path],
) -> None:
    control, _ = _published_run(workspace)
    report = trial.report_prospective_status(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )
    separation = report["evidence_separation"]
    assert separation["historical_training_rows_imported"] == 0  # type: ignore[index]
    assert separation["rehearsal_rows_imported"] == 0  # type: ignore[index]
    assert separation["lifecycle_diagnostic_rows_imported"] == 0  # type: ignore[index]
    assert separation["grade_derivative_rows_imported"] == 0  # type: ignore[index]


def test_report_surfaces_prediction_artifact_mutation(workspace: dict[str, Path]) -> None:
    control, run = _published_run(workspace)
    predictions_path = run.run_dir / "predictions.csv"  # type: ignore[operator]
    with predictions_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
    report = trial.report_prospective_status(
        control_dir=control.control_dir,
        trial_root=workspace["trial"],
    )
    assert report["artifact_integrity"]["status"] == "findings"  # type: ignore[index]
    assert report["gate_progress"]["artifact_mutation_findings"]["status"] == "fail"  # type: ignore[index]


def test_model_versions_and_controls_remain_separate(workspace: dict[str, Path]) -> None:
    first = _activate(workspace)
    second_model = _create_model_bundle(
        workspace["repository"] / "outputs" / "research" / "model" / "test-v2",
        model_id="mlb-hr-logreg-baseline-test-v2",
        model_version="test-v2",
    )
    second = trial.activate_prospective_control(
        model_dir=second_model,
        trial_root=workspace["trial"],
        repository_root=workspace["repository"],
        clock=_clock(),
    )
    assert first.control_id != second.control_id
    assert first.control_dir.parent == second.control_dir.parent
    assert trial.report_prospective_status(
        control_dir=first.control_dir, trial_root=workspace["trial"]
    )["control"]["model_version"] == "test-v1"  # type: ignore[index]
    assert trial.report_prospective_status(
        control_dir=second.control_dir, trial_root=workspace["trial"]
    )["control"]["model_version"] == "test-v2"  # type: ignore[index]


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("sport", "NBA"),
        ("market", "other"),
        ("research_only", False),
        ("approval_status", "approved"),
        ("eligible_for_betting", True),
        ("eligible_for_official_pick", True),
    ],
)
def test_control_research_boundary_fields_fail_closed(
    workspace: dict[str, Path], field_name: str, invalid: object
) -> None:
    control = _activate(workspace)
    manifest = json.loads(
        (control.control_dir / trial.CONTROL_MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    manifest[field_name] = invalid
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="research-only"):
        trial._validate_control_payload(manifest)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "field_name",
    [
        "control_manifest_digest",
        "model_bundle_manifest_digest",
        "feature_schema_version",
        "prediction_git_commit",
        "prediction_tree_fingerprint",
        "prediction_configuration_digest",
        "event_id",
        "commence_time_utc",
        "prediction_timestamp_utc",
        "selected_snapshot_timestamp_utc",
    ],
)
def test_missing_required_prediction_provenance_is_rejected(
    workspace: dict[str, Path], field_name: str
) -> None:
    control, run = _published_run(workspace)
    run_dir = run.run_dir
    predictions_path = run_dir / "predictions.csv"  # type: ignore[operator]
    rows = _read_csv(predictions_path)
    rows[0][field_name] = ""
    _write_csv(predictions_path, trial.PREDICTION_COLUMNS, rows)
    manifest_path = run_dir / trial.PREDICTION_MANIFEST_FILENAME  # type: ignore[operator]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictions_csv_sha256"] = _sha(predictions_path)
    _write_json(manifest_path, manifest)
    summary_path = run_dir / trial.RUN_SUMMARY_FILENAME  # type: ignore[operator]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["prediction_manifest_digest"] = _sha(manifest_path)
    _write_json(summary_path, summary)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="missing"):
        trial._validate_prediction_artifact(  # type: ignore[attr-defined]
            predictions_csv=predictions_path,
            control_manifest=json.loads(
                (control.control_dir / trial.CONTROL_MANIFEST_FILENAME).read_text(
                    encoding="utf-8"
                )
            ),
            control_manifest_digest=control.control_manifest_digest,
            control_dir=control.control_dir,
        )


def test_cli_help_preserves_legacy_commands_and_adds_explicit_opt_in(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        baseline.main(["--help"])
    output = capsys.readouterr().out
    assert caught.value.code == 0
    for command in (
        "audit-data",
        "build-features",
        "train",
        "predict",
        "append-ledger",
        "settle-ledger",
        "report-gates",
        "resolve-identities",
        "run-daily-research",
        "verify-predictions",
        "capture-closing-lines",
        "report-trial",
        "feature-readiness",
        "activate-prospective-control",
        "run-prospective-paper-day",
        "capture-prospective-closing",
        "settle-prospective-paper-day",
        "report-prospective-status",
        "report-prospective-health",
    ):
        assert command in output
