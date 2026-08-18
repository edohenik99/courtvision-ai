from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Callable

import pytest

from courtvision.sports.mlb.training import hr_prospective_market_movement as movement
from courtvision.sports.mlb.training import hr_prospective_trial as trial
from courtvision.sports.mlb.training import hr_research_baseline as baseline


NOW = datetime(2026, 8, 6, 17, 0, tzinfo=timezone.utc)
CLOSING_NOW = datetime(2026, 8, 6, 22, 45, tzinfo=timezone.utc)
SETTLEMENT_NOW = datetime(2026, 8, 7, 5, 0, tzinfo=timezone.utc)


def _clock(value: datetime) -> Callable[[], datetime]:
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
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _model_payload() -> dict[str, object]:
    numeric = {
        name: {"mean": 0.0, "stdev": 1.0}
        for name in baseline.NUMERIC_MODEL_FEATURES
    }
    feature_order = ["intercept"]
    for name in baseline.NUMERIC_MODEL_FEATURES:
        feature_order.extend((f"{name}__z", f"{name}__missing"))
    return {
        "schema_version": baseline.MODEL_BUNDLE_SCHEMA_VERSION,
        "model_id": "mlb-hr-logreg-movement-test",
        "model_version": "movement-test-v1",
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


def _create_model_bundle(model_dir: Path) -> Path:
    model_dir.mkdir(parents=True)
    model = _model_payload()
    metrics: dict[str, object] = {}
    _write_json(model_dir / "model.json", model)
    _write_json(model_dir / "metrics.json", metrics)
    metadata = {
        "schema_version": baseline.MODEL_BUNDLE_SCHEMA_VERSION,
        "model_id": model["model_id"],
        "model_version": model["model_version"],
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
        f"# Model\n\n{baseline.RESEARCH_ONLY_LABEL}\n\n"
        f"Model ID: {model['model_id']}\n",
        encoding="utf-8",
        newline="\n",
    )
    _write_json(
        model_dir / "bundle_manifest.json",
        {
            "model_id": model["model_id"],
            "metadata_json_sha256": _sha(model_dir / "metadata.json"),
            "model_json_sha256": _sha(model_dir / "model.json"),
            "metrics_json_sha256": _sha(model_dir / "metrics.json"),
            "research_label": baseline.RESEARCH_ONLY_LABEL,
            "approval_status": "not_approved",
        },
    )
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


def _workspace(
    tmp_path: Path,
    *,
    initial_rows: list[dict[str, object]] | None = None,
    publish: bool = True,
) -> dict[str, object]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.email", "tests@example.invalid")
    _git(repository, "config", "user.name", "CourtVision Tests")
    (repository / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-q", "-m", "initial")
    model = _create_model_bundle(
        repository / "outputs" / "research" / "model" / "movement-test-v1"
    )
    odds = repository / "outputs" / "inputs" / "odds.csv"
    _write_csv(
        odds,
        baseline.ODDS_REQUIRED_COLUMNS,
        initial_rows if initial_rows is not None else [_odds_row()],
    )
    results = repository / "outputs" / "inputs" / "results.csv"
    _write_csv(
        results,
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row()],
    )
    trial_root = repository / "outputs" / "prospective_trial"
    control = trial.activate_prospective_control(
        model_dir=model,
        trial_root=trial_root,
        repository_root=repository,
        clock=_clock(NOW),
    )
    run = None
    if publish:
        run = trial.run_prospective_paper_day(
            target_date="2026-08-06",
            control_dir=control.control_dir,
            odds_csv=odds,
            trial_root=trial_root,
            repository_root=repository,
            clock=_clock(NOW),
        )
    return {
        "repository": repository,
        "model": model,
        "odds": odds,
        "results": results,
        "trial_root": trial_root,
        "control": control,
        "run": run,
    }


def _capture(
    workspace: dict[str, object],
    rows: list[dict[str, object]],
    *,
    captured_at: datetime = CLOSING_NOW,
    run: trial.ProspectivePaperRunResult | None = None,
) -> None:
    odds = workspace["odds"]
    assert isinstance(odds, Path)
    _write_csv(odds, baseline.ODDS_REQUIRED_COLUMNS, rows)
    selected_run = run or workspace["run"]
    assert isinstance(selected_run, trial.ProspectivePaperRunResult)
    assert selected_run.run_dir is not None
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    trial.capture_prospective_closing(
        control_dir=control.control_dir,
        predictions_csv=selected_run.run_dir / "predictions.csv",
        odds_csv=odds,
        trial_root=workspace["trial_root"],
        clock=_clock(captured_at),
    )


def _report(workspace: dict[str, object]) -> dict[str, object]:
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    return movement.report_prospective_market_movement(
        control_dir=control.control_dir,
        trial_root=workspace["trial_root"],
    )


def _closing_path(workspace: dict[str, object]) -> Path:
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    return control.control_dir / "closing_lines.csv"


def _rewrite_closing(
    workspace: dict[str, object], rows: list[dict[str, object]]
) -> None:
    _write_csv(_closing_path(workspace), trial.CLOSING_COLUMNS, rows)


def _refresh_closing_id(row: dict[str, object]) -> None:
    row["closing_record_id"] = "mlb-hr-closing-v2-" + trial._canonical_sha256(
        {
            "prediction_id": row["prediction_id"],
            "closing_status": row["closing_status"],
            "closing_method": row["closing_method"],
            "closing_snapshot_time": row["closing_snapshot_time_utc"],
            "closing_sportsbook": row["closing_sportsbook"],
            "closing_american_odds": row["closing_american_odds"],
        }
    )[:24]


def _comparable_workspace(
    tmp_path: Path,
    *,
    original_price: int = 400,
    closing_price: int = 300,
) -> dict[str, object]:
    workspace = _workspace(
        tmp_path, initial_rows=[_odds_row(price=original_price)]
    )
    _capture(
        workspace,
        [
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                price=closing_price,
            )
        ],
    )
    return workspace


def _tree_state(root: Path) -> list[tuple[str, str, int, int]]:
    return [
        (
            path.relative_to(root).as_posix(),
            _sha(path),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def test_cli_registration_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        baseline.main([movement.REPORT_COMMAND, "--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    assert "--control-dir" in output
    assert "--trial-root" in output


def test_empty_valid_evidence(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, publish=False)
    report = _report(workspace)
    assert report["schema_version"] == movement.REPORT_SCHEMA_VERSION
    assert report["evidence"] == {
        "committed_predictions": 0,
        "comparable_same_book": 0,
        "comparable_consensus": 0,
        "non_comparable_temporal": {
            "total": 0,
            "same_book": 0,
            "consensus": 0,
        },
        "explicit_missing": {"total": 0, "missing": 0, "missing_prestart": 0},
        "predictions_without_closing_record": 0,
        "accounting_invariant_holds": True,
    }


def test_json_is_deterministic_and_finite(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    first = _report(workspace)
    second = _report(workspace)
    assert first == second
    assert json.dumps(first, sort_keys=True, allow_nan=False) == json.dumps(
        second, sort_keys=True, allow_nan=False
    )
    assert "generated_at" not in json.dumps(first)


def test_report_is_strictly_read_only(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    trial_root = workspace["trial_root"]
    assert isinstance(trial_root, Path)
    before = _tree_state(trial_root)
    _report(workspace)
    assert _tree_state(trial_root) == before


@pytest.mark.parametrize(
    ("original_price", "closing_price", "direction"),
    [
        (400, 300, "shortened"),
        (400, 400, "unchanged"),
        (400, 500, "lengthened"),
        (200, 150, "shortened"),
        (-110, -130, "shortened"),
        (110, -110, "shortened"),
    ],
)
def test_same_book_direction_and_american_price_domains(
    tmp_path: Path,
    original_price: int,
    closing_price: int,
    direction: str,
) -> None:
    report = _report(
        _comparable_workspace(
            tmp_path,
            original_price=original_price,
            closing_price=closing_price,
        )
    )
    summary = report["movement"]["same_book"]  # type: ignore[index]
    assert summary["count"] == 1  # type: ignore[index]
    assert summary[f"{direction}_count"] == 1  # type: ignore[index]
    if (original_price, closing_price) == (400, 300):
        assert summary["shortened_rate"] == "1"  # type: ignore[index]
        assert summary["mean_implied_probability_delta"] == "0.05"  # type: ignore[index]
        assert summary["median_implied_probability_delta"] == "0.05"  # type: ignore[index]
        assert summary["mean_decimal_odds_delta"] == "-1"  # type: ignore[index]
        assert summary["median_decimal_odds_delta"] == "-1"  # type: ignore[index]


def test_decimal_and_implied_consistency_fails_closed(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["closing_decimal_odds"] = "3.9"
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="inconsistent"):
        _report(workspace)


def test_consensus_uses_consensus_implied_probability(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _capture(
        workspace,
        [
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=300,
            ),
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                bookmaker_key="betmgm",
                bookmaker="BetMGM",
                price=500,
            ),
        ],
    )
    report = _report(workspace)
    summary = report["movement"]["consensus"]  # type: ignore[index]
    assert summary["shortened_count"] == 1  # type: ignore[index]
    assert summary["lengthened_count"] == 0  # type: ignore[index]


def test_consensus_does_not_fabricate_american_or_decimal_movement(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    _capture(
        workspace,
        [
            _odds_row(
                snapshot_time="2026-08-06T22:00:00Z",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=300,
            )
        ],
    )
    summary = _report(workspace)["movement"]["consensus"]  # type: ignore[index]
    assert not any("american" in key or "decimal" in key for key in summary)


def test_explicit_missing_is_accounted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _capture(workspace, [])
    evidence = _report(workspace)["evidence"]
    assert evidence["explicit_missing"] == {  # type: ignore[index]
        "total": 1,
        "missing": 1,
        "missing_prestart": 0,
    }


def test_missing_prestart_is_accounted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    _capture(
        workspace,
        [_odds_row(snapshot_time="2026-08-06T23:30:00Z")],
    )
    evidence = _report(workspace)["evidence"]
    assert evidence["explicit_missing"] == {  # type: ignore[index]
        "total": 1,
        "missing": 0,
        "missing_prestart": 1,
    }


def test_prediction_without_closing_record_is_accounted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    evidence = _report(workspace)["evidence"]
    assert evidence["predictions_without_closing_record"] == 1  # type: ignore[index]


def test_mixed_state_accounting_invariant(tmp_path: Path) -> None:
    initial = [
        _odds_row(player=f"Batter {index}") for index in range(1, 7)
    ]
    workspace = _workspace(tmp_path, initial_rows=initial)
    _capture(
        workspace,
        [
            _odds_row(
                player="Batter 1",
                snapshot_time="2026-08-06T22:00:00Z",
                price=300,
            ),
            _odds_row(
                player="Batter 2",
                snapshot_time="2026-08-06T22:00:00Z",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=300,
            ),
            _odds_row(player="Batter 3", price=350),
            _odds_row(
                player="Batter 4",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=350,
            ),
        ],
    )
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    ledger = _read_csv(control.control_dir / "prospective_ledger.csv")
    id_for_six = next(
        row["prediction_id"]
        for row in ledger
        if row["record_type"] == "prediction" and row["player_name"] == "Batter 6"
    )
    closing = [
        row
        for row in _read_csv(_closing_path(workspace))
        if row["prediction_id"] != id_for_six
    ]
    _rewrite_closing(workspace, closing)
    evidence = _report(workspace)["evidence"]
    assert evidence == {
        "committed_predictions": 6,
        "comparable_same_book": 1,
        "comparable_consensus": 1,
        "non_comparable_temporal": {
            "total": 2,
            "same_book": 1,
            "consensus": 1,
        },
        "explicit_missing": {"total": 1, "missing": 1, "missing_prestart": 0},
        "predictions_without_closing_record": 1,
        "accounting_invariant_holds": True,
    }


@pytest.mark.parametrize("snapshot", ["2026-08-06T14:59:00Z", "2026-08-06T15:00:00Z"])
def test_writer_valid_earlier_or_equal_observation_is_non_comparable(
    tmp_path: Path, snapshot: str
) -> None:
    workspace = _workspace(tmp_path)
    _capture(workspace, [_odds_row(snapshot_time=snapshot, price=350)])
    evidence = _report(workspace)["evidence"]
    assert evidence["comparable_same_book"] == 0  # type: ignore[index]
    assert evidence["non_comparable_temporal"] == {  # type: ignore[index]
        "total": 1,
        "same_book": 1,
        "consensus": 0,
    }


def test_closing_snapshot_at_commence_fails_closed(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["closing_snapshot_time_utc"] = "2026-08-06T23:00:00Z"
    rows[0]["captured_at_utc"] = "2026-08-06T23:00:00Z"
    _refresh_closing_id(rows[0])
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="pregame"):
        _report(workspace)


def test_closing_snapshot_after_capture_fails_closed(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["closing_snapshot_time_utc"] = "2026-08-06T22:50:00Z"
    _refresh_closing_id(rows[0])
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="captured_at"):
        _report(workspace)


def test_duplicate_closing_prediction_ids_fail(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    _rewrite_closing(workspace, [rows[0], dict(rows[0])])
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="duplicate"):
        _report(workspace)


def test_orphan_closing_record_fails(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["prediction_id"] = "mlb-hr-pred-v1-orphan"
    _refresh_closing_id(rows[0])
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="orphan"):
        _report(workspace)


@pytest.mark.parametrize(
    ("field", "value"),
    [("control_id", "wrong-control"), ("control_manifest_digest", "f" * 64)],
)
def test_wrong_control_or_digest_fails(
    tmp_path: Path, field: str, value: str
) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0][field] = value
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="control or run"):
        _report(workspace)


def test_wrong_prediction_run_linkage_fails(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["prediction_run_id"] = "wrong-run"
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="control or run"):
        _report(workspace)


def test_wrong_original_price_linkage_fails(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["original_american_odds"] = "450"
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="original-price"):
        _report(workspace)


def test_malformed_status_method_pair_fails(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["closing_method"] = "consensus_latest_prestart"
    _refresh_closing_id(rows[0])
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="status and method"):
        _report(workspace)


def test_invalid_deterministic_closing_record_id_fails(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["closing_record_id"] = "mlb-hr-closing-v2-invalid"
    _rewrite_closing(workspace, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="closing_record_id"):
        _report(workspace)


def test_artifact_ledger_linkage_failure_fails(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    ledger_path = control.control_dir / "prospective_ledger.csv"
    rows: list[dict[str, object]] = list(_read_csv(ledger_path))
    rows[0]["predictions_csv_sha256"] = "0" * 64
    _write_csv(ledger_path, trial.LEDGER_COLUMNS, rows)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="ledger linkage"):
        _report(workspace)


def test_immutable_prediction_artifact_mutation_fails(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    run = workspace["run"]
    assert isinstance(run, trial.ProspectivePaperRunResult)
    assert run.run_dir is not None
    with (run.run_dir / "predictions.csv").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="digest"):
        _report(workspace)


def test_toctou_closing_mutation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _comparable_workspace(tmp_path)
    original = movement._capture_evidence_snapshot
    calls = 0

    def mutate_after_snapshot(control_dir: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        snapshot = original(control_dir)
        if calls == 1:
            with (control_dir / "closing_lines.csv").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write("\n")
        return snapshot

    monkeypatch.setattr(movement, "_capture_evidence_snapshot", mutate_after_snapshot)
    with pytest.raises(trial.MLBHRProspectiveTrialError, match="changed"):
        _report(workspace)


def test_per_date_grouping_is_sorted_and_deterministic(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    odds = workspace["odds"]
    assert isinstance(odds, Path)
    second_original = _odds_row(
        snapshot_time="2026-08-07T15:00:00Z",
        event_id="event-2",
        commence_time="2026-08-07T23:00:00Z",
        player="Beta Batter",
    )
    _write_csv(odds, baseline.ODDS_REQUIRED_COLUMNS, [second_original])
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    second_run = trial.run_prospective_paper_day(
        target_date="2026-08-07",
        control_dir=control.control_dir,
        odds_csv=odds,
        trial_root=workspace["trial_root"],
        repository_root=workspace["repository"],
        clock=_clock(datetime(2026, 8, 7, 17, 0, tzinfo=timezone.utc)),
    )
    _capture(
        workspace,
        [
            _odds_row(
                snapshot_time="2026-08-07T22:00:00Z",
                event_id="event-2",
                commence_time="2026-08-07T23:00:00Z",
                player="Beta Batter",
                price=300,
            )
        ],
        captured_at=datetime(2026, 8, 7, 22, 45, tzinfo=timezone.utc),
        run=second_run,
    )
    report = _report(workspace)
    dates = report["by_operating_date"]
    assert [row["operating_date"] for row in dates] == [  # type: ignore[index]
        "2026-08-06",
        "2026-08-07",
    ]
    assert report == _report(workspace)


def test_per_sportsbook_grouping_is_sorted_and_same_book_only(
    tmp_path: Path,
) -> None:
    workspace = _workspace(
        tmp_path,
        initial_rows=[
            _odds_row(player="Alpha Batter"),
            _odds_row(
                player="Beta Batter",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
            ),
        ],
    )
    _capture(
        workspace,
        [
            _odds_row(
                player="Alpha Batter",
                snapshot_time="2026-08-06T22:00:00Z",
                price=300,
            ),
            _odds_row(
                player="Beta Batter",
                snapshot_time="2026-08-06T22:00:00Z",
                bookmaker_key="fanduel",
                bookmaker="FanDuel",
                price=300,
            ),
        ],
    )
    rows = _report(workspace)["by_sportsbook"]
    assert [row["sportsbook"] for row in rows] == [  # type: ignore[index]
        "draftkings",
        "fanduel",
    ]
    assert all(row["movement"]["count"] == 1 for row in rows)  # type: ignore[index]


def test_outcome_and_settlement_evidence_cannot_change_output(tmp_path: Path) -> None:
    workspace = _comparable_workspace(tmp_path)
    before = _report(workspace)
    control = workspace["control"]
    assert isinstance(control, trial.ControlActivationResult)
    trial.settle_prospective_paper_day(
        control_dir=control.control_dir,
        results_csv=workspace["results"],
        trial_root=workspace["trial_root"],
        clock=_clock(SETTLEMENT_NOW),
    )
    results = workspace["results"]
    assert isinstance(results, Path)
    _write_csv(
        results,
        (*baseline.RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row(actual_home_runs=0)],
    )
    ledger_path = control.control_dir / "prospective_ledger.csv"
    ledger: list[dict[str, object]] = list(_read_csv(ledger_path))
    settlement = next(row for row in ledger if row["record_type"] == "settlement")
    settlement["grade"] = "changed-outcome"
    settlement["unit_profit_loss"] = "999"
    settlement["results_sha256"] = "f" * 64
    _write_csv(ledger_path, trial.LEDGER_COLUMNS, ledger)
    assert _report(workspace) == before


def test_writer_provider_model_and_outcome_boundaries_are_not_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _comparable_workspace(tmp_path)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("prohibited boundary reached")

    monkeypatch.setattr(trial, "report_prospective_status", forbidden)
    monkeypatch.setattr(trial, "capture_prospective_closing", forbidden)
    monkeypatch.setattr(trial, "settle_prospective_paper_day", forbidden)
    monkeypatch.setattr(trial, "run_prospective_paper_day", forbidden)
    monkeypatch.setattr(baseline, "capture_closing_line_snapshots", forbidden)
    monkeypatch.setattr(baseline, "load_model_bundle", forbidden)
    assert _report(workspace)["integrity"]["status"] == "valid"  # type: ignore[index]


def test_blank_optional_bookmaker_display_metadata_is_accepted(
    tmp_path: Path,
) -> None:
    workspace = _comparable_workspace(tmp_path)
    rows: list[dict[str, object]] = list(_read_csv(_closing_path(workspace)))
    rows[0]["closing_sportsbook_name"] = ""
    _rewrite_closing(workspace, rows)
    assert _report(workspace)["evidence"]["comparable_same_book"] == 1  # type: ignore[index]


def test_legacy_prospective_commands_remain_registered(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        baseline.main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    for command in sorted(trial.PROSPECTIVE_COMMANDS):
        assert command in output
    assert movement.REPORT_COMMAND in output
