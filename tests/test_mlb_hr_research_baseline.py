from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import courtvision_ai
from courtvision.sports.mlb.training.hr_research_baseline import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION_V1,
    LEDGER_COLUMNS,
    MLBHRResearchBaselineError,
    MODEL_REQUIRED_INPUT_COLUMNS,
    ODDS_REQUIRED_COLUMNS,
    PREDICTION_ELIGIBLE_STATUS,
    RESEARCH_ONLY_LABEL,
    RESULTS_REQUIRED_COLUMNS,
    TRAINING_ELIGIBLE_STATUS,
    append_predictions_to_ledger,
    build_live_hr_research_features,
    build_validation_gate_report,
    chronological_split_rows,
    courtvision_operating_date,
    generate_daily_research_predictions,
    load_model_bundle,
    predict_model_probability,
    resolve_mlb_model_bundle,
    resolve_mlb_odds_csv,
    resolve_mlb_output_dir,
    settle_prediction_ledger,
    train_research_logistic_baseline,
    write_feature_artifacts,
)


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    path.write_text(encoded, encoding="utf-8")


def _odds_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "snapshot_time": "2026-07-01T15:00:00Z",
        "event_id": "event-1",
        "commence_time": "2026-07-01T23:00:00Z",
        "home_team": "Toronto Blue Jays",
        "away_team": "New York Yankees",
        "bookmaker_key": "draftkings",
        "bookmaker": "DraftKings",
        "market": "batter_home_runs_alternate",
        "player": "Jose Ramirez",
        "side": "Over",
        "price": 400,
        "point": 0.5,
    }
    row.update(overrides)
    return row


def _result_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "event_id": "event-1",
        "player": "Jose Ramirez",
        "actual_home_runs": 1,
        "game_status": "final",
        "result_reason": "",
    }
    row.update(overrides)
    return row


def _write_training_fixture(tmp_path: Path) -> tuple[Path, Path]:
    odds_rows = [
        _odds_row(event_id="event-1", player="Alpha Batter", price=450),
        _odds_row(
            event_id="event-1",
            player="Alpha Batter",
            bookmaker_key="fanduel",
            bookmaker="FanDuel",
            price=500,
        ),
        _odds_row(event_id="event-1", player="Beta Batter", price=300),
        _odds_row(
            event_id="event-2",
            commence_time="2026-07-02T23:00:00Z",
            snapshot_time="2026-07-02T15:00:00Z",
            player="Gamma Batter",
            price=350,
        ),
        _odds_row(
            event_id="event-2",
            commence_time="2026-07-02T23:00:00Z",
            snapshot_time="2026-07-02T15:00:00Z",
            player="Delta Batter",
            price=600,
        ),
        _odds_row(
            event_id="event-3",
            commence_time="2026-07-03T23:00:00Z",
            snapshot_time="2026-07-03T15:00:00Z",
            player="Epsilon Batter",
            price=250,
        ),
        _odds_row(
            event_id="event-3",
            commence_time="2026-07-03T23:00:00Z",
            snapshot_time="2026-07-03T15:00:00Z",
            player="Zeta Batter",
            price=700,
        ),
    ]
    result_rows = [
        _result_row(event_id="event-1", player="Alpha Batter", actual_home_runs=1),
        _result_row(event_id="event-1", player="Beta Batter", actual_home_runs=0),
        _result_row(event_id="event-2", player="Gamma Batter", actual_home_runs=1),
        _result_row(event_id="event-2", player="Delta Batter", actual_home_runs=0),
        _result_row(event_id="event-3", player="Epsilon Batter", actual_home_runs=1),
        _result_row(event_id="event-3", player="Zeta Batter", actual_home_runs=0),
    ]
    odds_path = tmp_path / "odds.csv"
    results_path = tmp_path / "results.csv"
    _write_csv(odds_path, ODDS_REQUIRED_COLUMNS, odds_rows)
    _write_csv(results_path, (*RESULTS_REQUIRED_COLUMNS, "result_reason"), result_rows)
    return odds_path, results_path


def _train_fixture_model(tmp_path: Path) -> tuple[Path, list[dict[str, str]]]:
    odds_path, results_path = _write_training_fixture(tmp_path)
    result = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-04T00:00:00Z",
    )
    artifact_paths = write_feature_artifacts(result, tmp_path / "features")
    training = train_research_logistic_baseline(
        feature_rows_path=artifact_paths["feature_rows_csv"],
        output_root=tmp_path / "models",
        model_version="test-v1",
        generated_at="2026-07-04T01:00:00Z",
    )
    return training.bundle_dir, _read_csv(Path(artifact_paths["feature_rows_csv"]))


def test_feature_builder_is_deterministic_selects_best_book_and_tracks_exclusions(
    tmp_path: Path,
) -> None:
    odds_path = tmp_path / "odds.csv"
    results_path = tmp_path / "results.csv"
    odds_rows = [
        _odds_row(player="Jose Ramirez", price=400),
        _odds_row(
            player="Jose Ramirez",
            bookmaker_key="fanduel",
            bookmaker="FanDuel",
            price=450,
        ),
        _odds_row(event_id="event-2", player="Bench Batter", price=800),
        _odds_row(event_id="event-3", player="Missing Result", price=900),
    ]
    result_rows = [
        _result_row(player="Jose Ramirez", actual_home_runs=1),
        _result_row(
            event_id="event-2",
            player="Bench Batter",
            actual_home_runs="",
            game_status="void",
            result_reason="non_participant",
        ),
    ]
    _write_csv(odds_path, ODDS_REQUIRED_COLUMNS, odds_rows)
    _write_csv(results_path, (*RESULTS_REQUIRED_COLUMNS, "result_reason"), result_rows)

    first = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-02T00:00:00Z",
    )
    second = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-02T00:00:00Z",
    )

    assert first.rows == second.rows
    assert first.manifest["row_count"] == 3
    assert first.manifest["eligible_row_count"] == 1
    eligible = [row for row in first.rows if row["eligibility_status"] == TRAINING_ELIGIBLE_STATUS]
    assert eligible[0]["normalized_player_name"] == "jose ramirez"
    assert eligible[0]["sportsbook"] == "fanduel"
    assert eligible[0]["american_odds"] == "450"
    assert eligible[0]["bookmaker_count"] == "2"
    assert {row["exclusion_reason"] for row in first.exclusions} == {
        "void",
        "missing_result",
    }
    assert first.rows[0]["research_label"] == RESEARCH_ONLY_LABEL


def test_courtvision_operating_date_uses_toronto_timezone_and_rejects_bad_values(
    tmp_path: Path,
) -> None:
    assert (
        courtvision_operating_date(
            datetime.fromisoformat("2026-07-15T00:01:00+00:00")
        ).isoformat()
        == "2026-07-14"
    )
    assert (
        courtvision_operating_date(
            datetime.fromisoformat("2026-07-14T19:00:00+00:00")
        ).isoformat()
        == "2026-07-14"
    )
    assert (
        courtvision_operating_date(
            datetime.fromisoformat("2026-01-15T05:30:00+00:00")
        ).isoformat()
        == "2026-01-15"
    )
    assert (
        courtvision_operating_date(
            datetime.fromisoformat("2026-07-15T04:30:00+00:00")
        ).isoformat()
        == "2026-07-15"
    )
    assert (
        courtvision_operating_date(
            datetime.fromisoformat("2026-07-15T03:59:00+00:00")
        ).isoformat()
        == "2026-07-14"
    )
    with pytest.raises(MLBHRResearchBaselineError, match="timezone"):
        courtvision_operating_date(datetime(2026, 7, 15, 0, 1))

    odds_path = tmp_path / "bad_timestamp_odds.csv"
    results_path = tmp_path / "results.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [_odds_row(commence_time="not-a-timestamp")],
    )
    _write_csv(results_path, (*RESULTS_REQUIRED_COLUMNS, "result_reason"), [_result_row()])

    with pytest.raises(MLBHRResearchBaselineError, match="ISO datetime"):
        build_live_hr_research_features(
            odds_path=odds_path,
            results_path=results_path,
            generated_at="2026-07-02T00:00:00Z",
        )


def test_snapshot_cutoff_and_leakage_rows_are_excluded(tmp_path: Path) -> None:
    odds_path = tmp_path / "odds.csv"
    results_path = tmp_path / "results.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                snapshot_time="2026-07-01T23:30:00Z",
                commence_time="2026-07-01T23:00:00Z",
            )
        ],
    )
    _write_csv(
        results_path,
        (*RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row()],
    )

    result = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-02T00:00:00Z",
    )

    assert result.rows[0]["eligibility_status"] == "excluded"
    assert result.rows[0]["exclusion_reason"] == "snapshot_not_before_game_start"
    assert result.rows[0]["leakage_check_status"] == "failed"


def test_training_writes_loadable_bundle_and_probability_bounds(tmp_path: Path) -> None:
    bundle_dir, feature_rows = _train_fixture_model(tmp_path)
    bundle = load_model_bundle(bundle_dir)

    probability = predict_model_probability(feature_rows[0], bundle)
    assert 0.0 <= probability <= 1.0

    missing_feature_row = dict(feature_rows[0])
    missing_feature_row["decimal_odds"] = ""
    missing_probability = predict_model_probability(missing_feature_row, bundle)
    assert 0.0 <= missing_probability <= 1.0
    assert bundle.metadata["research_label"] == RESEARCH_ONLY_LABEL
    assert (bundle_dir / "model_card.md").is_file()


def test_chronological_split_keeps_test_dates_after_train_dates() -> None:
    rows = [
        {"game_date": "2026-07-01", "hit_hr": "0"},
        {"game_date": "2026-07-01", "hit_hr": "1"},
        {"game_date": "2026-07-02", "hit_hr": "0"},
        {"game_date": "2026-07-03", "hit_hr": "1"},
        {"game_date": "2026-07-04", "hit_hr": "0"},
        {"game_date": "2026-07-05", "hit_hr": "1"},
    ]

    split = chronological_split_rows(rows)

    assert {row["hit_hr"] for row in split["train"]} == {"0", "1"}
    assert max(row["game_date"] for row in split["train"]) < min(
        row["game_date"] for row in split["test"]
    )


def test_prediction_dry_run_is_immutable_and_blocks_after_start_games(
    tmp_path: Path,
) -> None:
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "prediction_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="future-event",
                player="Future Batter",
                commence_time="2026-07-04T23:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            ),
            _odds_row(
                event_id="started-event",
                player="Started Batter",
                commence_time="2026-07-04T16:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            ),
        ],
    )
    output_dir = tmp_path / "predictions"

    first = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        output_dir=output_dir,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    second = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        output_dir=output_dir,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )

    assert not output_dir.exists()
    assert len(first.predictions) == 1
    assert first.predictions[0]["prediction_id"] == second.predictions[0]["prediction_id"]
    assert first.predictions[0]["eligibility_status"] == PREDICTION_ELIGIBLE_STATUS
    assert {row["exclusion_reason"] for row in first.exclusions} == {
        "game_already_started"
    }
    assert first.application_status == "PASS"
    assert first.lifecycle_status == "DISABLED"
    assert first.exclusion_reasons == {"game_already_started": 1}
    assert sum(first.exclusion_reasons.values()) == len(first.exclusions)
    assert first.input_diagnostics == {
        "input_row_count": 2,
        "requested_date_row_count": 2,
        "market_filtered_row_count": 2,
        "deduplicated_row_count": 2,
        "feature_validated_row_count": 2,
        "eligible_row_count": 1,
    }


def test_zero_prediction_statuses_and_date_diagnostics(
    tmp_path: Path,
) -> None:
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "prediction_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="started-event",
                player="Started Batter",
                commence_time="2026-07-04T16:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            )
        ],
    )

    zero_pick = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    no_data = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-05",
        prediction_timestamp="2026-07-05T17:00:00Z",
        dry_run=True,
    )

    assert zero_pick.application_status == "NO_ELIGIBLE_PREDICTIONS"
    assert zero_pick.lifecycle_status == "DISABLED"
    assert zero_pick.exclusion_reasons == {"game_already_started": 1}
    assert sum(zero_pick.exclusion_reasons.values()) == len(
        zero_pick.exclusions
    )
    assert zero_pick.input_diagnostics["requested_date_row_count"] == 1
    assert zero_pick.input_diagnostics["eligible_row_count"] == 0
    assert no_data.application_status == "NO_DATA"
    assert no_data.lifecycle_status == "DISABLED"
    assert no_data.input_diagnostics["input_row_count"] == 1
    assert no_data.input_diagnostics["requested_date_row_count"] == 0
    assert no_data.input_diagnostics["eligible_row_count"] == 0
    assert no_data.exclusion_reasons == {}


def test_prediction_defaults_select_latest_valid_local_inputs(
    tmp_path: Path,
) -> None:
    odds_path, results_path = _write_training_fixture(tmp_path / "training")
    features = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-04T00:00:00Z",
    )
    feature_paths = write_feature_artifacts(
        features, tmp_path / "feature_artifacts"
    )
    model_root = tmp_path / "models"
    older = train_research_logistic_baseline(
        feature_rows_path=feature_paths["feature_rows_csv"],
        output_root=model_root,
        model_version="older",
        generated_at="2026-07-04T01:00:00Z",
    )
    newer = train_research_logistic_baseline(
        feature_rows_path=feature_paths["feature_rows_csv"],
        output_root=model_root,
        model_version="newer",
        generated_at="2026-07-05T01:00:00Z",
    )
    (model_root / ".pytest_tmp-noise").mkdir()
    (model_root / "incomplete").mkdir()
    (model_root / "incomplete" / "metadata.json").write_text(
        "{}", encoding="utf-8"
    )
    (model_root / "unrelated").mkdir()

    repository_root = tmp_path / "repository"
    canonical_odds = (
        repository_root
        / "data"
        / "theoddsapi"
        / "live_hr_snapshots"
        / "live_hr_props_master.csv"
    )
    _write_csv(canonical_odds, ODDS_REQUIRED_COLUMNS, [_odds_row()])
    artifact_root = repository_root / "outputs" / "research" / "mlb_hr_baseline"

    assert resolve_mlb_model_bundle(model_root=model_root) == newer.bundle_dir
    assert (
        resolve_mlb_model_bundle(
            older.bundle_dir,
            model_root=tmp_path / "unused-model-root",
        )
        == older.bundle_dir
    )
    assert (
        resolve_mlb_odds_csv(repository_root=repository_root)
        == canonical_odds.resolve()
    )
    explicit_odds = tmp_path / "explicit_odds.csv"
    _write_csv(explicit_odds, ODDS_REQUIRED_COLUMNS, [_odds_row()])
    assert (
        resolve_mlb_odds_csv(
            explicit_odds,
            repository_root=tmp_path / "unused-repository",
        )
        == explicit_odds.resolve()
    )
    assert resolve_mlb_output_dir(
        None,
        target_date="2026-07-25",
        artifact_root=artifact_root,
    ) == (artifact_root / "daily_runs" / "2026-07-25").resolve()
    explicit_output = tmp_path / "explicit-output"
    assert resolve_mlb_output_dir(
        explicit_output,
        target_date="2026-07-25",
        artifact_root=tmp_path / "unused-artifacts",
    ) == explicit_output.resolve()


def test_lifecycle_locks_release_for_all_healthy_mlb_terminal_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COURTVISION_LIFECYCLE_SHADOW", "0")
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "prediction_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="timing-event",
                player="Timing Batter",
                commence_time="2026-07-04T16:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            )
        ],
    )
    cases = (
        ("pass", "2026-07-04", "2026-07-04T15:30:00Z", "PASS"),
        (
            "excluded",
            "2026-07-04",
            "2026-07-04T17:00:00Z",
            "NO_ELIGIBLE_PREDICTIONS",
        ),
        ("no-data", "2026-07-05", "2026-07-05T17:00:00Z", "NO_DATA"),
    )

    for label, target_date, timestamp, expected_status in cases:
        output_dir = tmp_path / label / target_date
        result = generate_daily_research_predictions(
            model_bundle_dir=bundle_dir,
            odds_path=odds_path,
            output_dir=output_dir,
            target_date=target_date,
            prediction_timestamp=timestamp,
        )
        assert result.application_status == expected_status
        assert result.lifecycle_status == "DISABLED"
        assert not (
            output_dir.parent
            / f".prediction_mlb_research_{target_date}.lock"
        ).exists()
        assert (output_dir / "exclusion_summary.json").is_file()


def test_lifecycle_enabled_zero_pick_is_not_degraded_by_nba_observer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "prediction_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="started-event",
                player="Started Batter",
                commence_time="2026-07-04T16:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            )
        ],
    )
    context = SimpleNamespace(terminal=False)
    publication_kwargs: dict[str, object] = {}
    begin_count = 0

    def begin(*args: object, **kwargs: object) -> object:
        nonlocal begin_count
        begin_count += 1
        return context

    def publish(run: object, **kwargs: object) -> object:
        publication_kwargs.update(kwargs)
        context.terminal = True
        return SimpleNamespace(status="PASS")

    import courtvision.shadow_lifecycle as shadow_lifecycle

    monkeypatch.setattr(
        shadow_lifecycle,
        "load_shadow_lifecycle_hooks",
        lambda: SimpleNamespace(
            begin_shadow_run=begin,
            publish_shadow_after_board=publish,
            record_failed_shadow_run=lambda *args, **kwargs: None,
            observations_enabled=True,
            prepare_observation_batch=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("NBA observer must not run for MLB")
            ),
            observation_initialization_error=None,
        ),
    )
    output_dir = tmp_path / "lifecycle-enabled" / "2026-07-04"
    result = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        output_dir=output_dir,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        repository_root=tmp_path,
    )

    assert result.application_status == "NO_ELIGIBLE_PREDICTIONS"
    assert result.lifecycle_status == "PASS"
    assert begin_count == 1
    assert "observations_enabled" not in publication_kwargs
    prediction_manifest = json.loads(
        (output_dir / "manifest.json").read_text(encoding="utf-8")
    )
    application_manifest = json.loads(
        (output_dir / "application_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    exclusion_summary = json.loads(
        (output_dir / "exclusion_summary.json").read_text(encoding="utf-8")
    )
    assert prediction_manifest["status"] == "NO_ELIGIBLE_PREDICTIONS"
    assert prediction_manifest["exclusion_reasons"] == {
        "game_already_started": 1
    }
    assert application_manifest["status"] == "NO_ELIGIBLE_PREDICTIONS"
    assert application_manifest["lifecycle_status"] == "PASS"
    assert application_manifest["result_summary"]["exclusion_reasons"] == {
        "game_already_started": 1
    }
    assert sum(exclusion_summary["exclusion_reasons"].values()) == (
        exclusion_summary["excluded_row_count"]
    )
    assert not (
        output_dir.parent
        / ".prediction_mlb_research_2026-07-04.lock"
    ).exists()

    invalid_odds_path = tmp_path / "invalid_prediction_odds.csv"
    _write_csv(
        invalid_odds_path,
        ODDS_REQUIRED_COLUMNS,
        [_odds_row(point=1.5)],
    )
    invalid_output = tmp_path / "invalid" / "2026-07-04"
    with pytest.raises(
        MLBHRResearchBaselineError,
        match="not an Over 0.5 HR market",
    ):
        generate_daily_research_predictions(
            model_bundle_dir=bundle_dir,
            odds_path=invalid_odds_path,
            output_dir=invalid_output,
            target_date="2026-07-04",
            prediction_timestamp="2026-07-04T17:00:00Z",
            repository_root=tmp_path,
        )
    assert begin_count == 1
    assert not invalid_output.exists()


def test_cli_overrides_failure_status_and_protected_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("COURTVISION_LIFECYCLE_SHADOW", "0")
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "prediction_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="started-event",
                player="Started Batter",
                commence_time="2026-07-04T16:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            )
        ],
    )
    output_dir = tmp_path / "daily_runs" / "2026-07-04"
    args = [
        "predict",
        "--sport",
        "mlb",
        "--mode",
        "research",
        "--prediction-date",
        "2026-07-04",
        "--prediction-timestamp",
        "2026-07-04T17:00:00Z",
        "--model-dir",
        str(bundle_dir),
        "--odds-csv",
        str(odds_path),
        "--output-dir",
        str(output_dir),
    ]

    assert courtvision_ai.main(args) == 0
    first_output = capsys.readouterr().out
    assert '"status": "NO_ELIGIBLE_PREDICTIONS"' in first_output
    assert f'"resolved_model_dir": "{str(bundle_dir.resolve()).replace(chr(92), chr(92) * 2)}"' in first_output
    assert f'"resolved_odds_csv": "{str(odds_path.resolve()).replace(chr(92), chr(92) * 2)}"' in first_output
    assert f'"resolved_output_dir": "{str(output_dir.resolve()).replace(chr(92), chr(92) * 2)}"' in first_output
    assert (output_dir / "exclusion_summary.json").is_file()
    lock_path = (
        output_dir.parent
        / ".prediction_mlb_research_2026-07-04.lock"
    )
    assert not lock_path.exists()
    before_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_dir.iterdir()
        if path.is_file()
    }

    assert courtvision_ai.main(args) == 0
    second_output = capsys.readouterr().out
    assert '"status": "PROTECTED_NO_OP"' in second_output
    assert not lock_path.exists()
    assert before_hashes == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output_dir.iterdir()
        if path.is_file()
    }

    protected_with_missing_dependency = [
        *args[:],
    ]
    missing_index = (
        protected_with_missing_dependency.index("--odds-csv") + 1
    )
    protected_with_missing_dependency[missing_index] = str(
        tmp_path / "missing.csv"
    )
    assert courtvision_ai.main(protected_with_missing_dependency) == 0
    protected_output = capsys.readouterr().out
    assert '"status": "PROTECTED_NO_OP"' in protected_output

    failed_args = [*protected_with_missing_dependency]
    failure_output_index = failed_args.index("--output-dir") + 1
    failed_args[failure_output_index] = str(
        tmp_path / "failure" / "2026-07-04"
    )
    assert courtvision_ai.main(failed_args) == 1
    failure = capsys.readouterr()
    assert '"status": "FAILED"' in failure.err
    assert '"lifecycle_status": "NOT_STARTED"' in failure.err


def test_prediction_timing_uses_exact_aware_timestamps_near_midnight(
    tmp_path: Path,
) -> None:
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "near_midnight_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="midnight-event",
                player="Midnight Batter",
                commence_time="2026-07-15T00:01:00Z",
                snapshot_time="2026-07-14T23:55:00Z",
            )
        ],
    )

    before_start = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-14",
        prediction_timestamp="2026-07-14T23:59:30Z",
        dry_run=True,
    )
    after_start = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-14",
        prediction_timestamp="2026-07-15T00:02:00Z",
        dry_run=True,
    )

    assert len(before_start.predictions) == 1
    assert before_start.predictions[0]["game_date"] == "2026-07-14"
    assert before_start.predictions[0]["game_date_utc"] == "2026-07-15"
    assert before_start.predictions[0]["game_date_operating"] == "2026-07-14"
    assert before_start.input_diagnostics["requested_date_row_count"] == 1
    assert len(after_start.predictions) == 0
    assert after_start.exclusions[0]["exclusion_reason"] == "game_already_started"


def test_ledger_settlement_appends_without_mutating_prediction_record(
    tmp_path: Path,
) -> None:
    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "prediction_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="settle-event",
                player="Settlement Batter",
                commence_time="2026-07-04T23:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
                price=500,
            )
        ],
    )
    prediction_run = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    ledger_path = tmp_path / "ledger.csv"

    append_result = append_predictions_to_ledger(
        predictions=prediction_run.predictions,
        ledger_path=ledger_path,
    )
    before = ledger_path.read_bytes()
    results_path = tmp_path / "settlement_results.csv"
    _write_csv(
        results_path,
        (*RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [
            _result_row(
                event_id="settle-event",
                player="Settlement Batter",
                actual_home_runs=1,
            )
        ],
    )
    settlement = settle_prediction_ledger(
        ledger_path=ledger_path,
        results_path=results_path,
        settlement_timestamp="2026-07-05T05:00:00Z",
    )

    assert append_result.appended_rows == 1
    assert settlement.appended_settlements == 1
    assert ledger_path.read_bytes().startswith(before)
    rows = _read_csv(ledger_path)
    assert [row["record_type"] for row in rows] == ["prediction", "settlement"]
    assert rows[0]["model_probability"] == prediction_run.predictions[0]["model_probability"]
    assert rows[1]["grade"] == "win"
    assert rows[1]["unit_profit_loss"] == "5"
    assert rows[1]["integrity_status"] == "prediction_before_game_start"


def test_one_class_training_and_corrupted_artifact_fail_closed(tmp_path: Path) -> None:
    odds_path, results_path = _write_training_fixture(tmp_path)
    result = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-04T00:00:00Z",
    )
    one_class_rows = []
    for row in result.rows:
        adjusted = dict(row)
        if adjusted["eligibility_status"] == TRAINING_ELIGIBLE_STATUS:
            adjusted["hit_hr"] = "0"
            adjusted["actual_home_runs"] = "0"
        one_class_rows.append(adjusted)
    one_class_path = tmp_path / "one_class_features.csv"
    _write_csv(one_class_path, FEATURE_COLUMNS, one_class_rows)

    with pytest.raises(MLBHRResearchBaselineError, match="both classes"):
        train_research_logistic_baseline(
            feature_rows_path=one_class_path,
            output_root=tmp_path / "one_class_models",
            generated_at="2026-07-04T01:00:00Z",
        )

    bundle_dir, _ = _train_fixture_model(tmp_path / "corrupt")
    model_path = bundle_dir / "model.json"
    model_payload = model_path.read_text(encoding="utf-8")
    model_path.write_text(model_payload.replace("test-v1", "tampered"), encoding="utf-8")

    with pytest.raises(MLBHRResearchBaselineError, match="integrity"):
        load_model_bundle(bundle_dir)


def test_existing_v1_model_feature_schema_loads_when_feature_order_matches(
    tmp_path: Path,
) -> None:
    bundle_dir, _ = _train_fixture_model(tmp_path)
    loaded = load_model_bundle(bundle_dir)
    original_order = list(loaded.model["feature_order"])

    model_path = bundle_dir / "model.json"
    metadata_path = bundle_dir / "metadata.json"
    model_payload = json.loads(model_path.read_text(encoding="utf-8"))
    metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_payload["feature_schema_version"] = FEATURE_SCHEMA_VERSION_V1
    metadata_payload["feature_schema_version"] = FEATURE_SCHEMA_VERSION_V1
    _write_json(model_path, model_payload)
    metadata_payload["model_json_sha256"] = hashlib.sha256(
        model_path.read_bytes()
    ).hexdigest()
    _write_json(metadata_path, metadata_payload)

    legacy_loaded = load_model_bundle(bundle_dir)

    assert list(legacy_loaded.model["feature_order"]) == original_order
    assert tuple(legacy_loaded.model["required_input_columns"]) == MODEL_REQUIRED_INPUT_COLUMNS


def test_empty_date_feature_build_and_validation_gate_report(tmp_path: Path) -> None:
    odds_path, results_path = _write_training_fixture(tmp_path)
    result = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        target_date="2099-01-01",
        generated_at="2026-07-04T00:00:00Z",
    )
    paths = write_feature_artifacts(result, tmp_path / "empty_features")

    assert result.manifest["row_count"] == 0
    with pytest.raises(MLBHRResearchBaselineError, match="at least two"):
        train_research_logistic_baseline(
            feature_rows_path=paths["feature_rows_csv"],
            output_root=tmp_path / "models",
            generated_at="2026-07-04T01:00:00Z",
        )

    report = build_validation_gate_report(feature_rows_path=paths["feature_rows_csv"])
    assert report["official_betting_picks_ready"] is False
    assert report["gates"]["prediction_dates"]["passed"] is False


def test_ledger_schema_constant_matches_written_file(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.csv"
    _write_csv(ledger_path, LEDGER_COLUMNS, [])

    report = build_validation_gate_report(ledger_path=ledger_path)

    assert report["approval_status"] == "not_approved"


def test_canonical_application_matches_legacy_mlb_prediction_bytes(
    tmp_path: Path,
) -> None:
    from courtvision.sports.mlb.training.hr_research_baseline import (
        _file_sha256,
        _generate_daily_research_predictions_internal,
    )

    bundle_dir, _ = _train_fixture_model(tmp_path)
    odds_path = tmp_path / "parity_odds.csv"
    _write_csv(
        odds_path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="parity-event",
                player="Parity Batter",
                commence_time="2026-07-04T23:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
            )
        ],
    )
    timestamp = "2026-07-04T17:00:00Z"
    canonical_dry = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-04",
        prediction_timestamp=timestamp,
        dry_run=True,
    )
    legacy_dir = tmp_path / "legacy_predictions"
    canonical_dir = tmp_path / "canonical_predictions"
    legacy = _generate_daily_research_predictions_internal(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        output_dir=legacy_dir,
        target_date="2026-07-04",
        prediction_timestamp=timestamp,
        dry_run=False,
        prediction_run_id=canonical_dry.prediction_run_id,
    )
    canonical = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        output_dir=canonical_dir,
        target_date="2026-07-04",
        prediction_timestamp=timestamp,
        dry_run=False,
    )

    assert canonical.predictions == legacy.predictions
    assert canonical.exclusions == legacy.exclusions
    assert [
        row["model_probability"] for row in canonical.predictions
    ] == [row["model_probability"] for row in legacy.predictions]
    assert [
        row["probability_edge"] for row in canonical.predictions
    ] == [row["probability_edge"] for row in legacy.predictions]
    assert [
        row["feature_schema_version"] for row in canonical.predictions
    ] == [row["feature_schema_version"] for row in legacy.predictions]
    assert _file_sha256(legacy_dir / "predictions.csv") == _file_sha256(
        canonical_dir / "predictions.csv"
    )
    assert canonical.manifest["predictions_csv_sha256"] == _file_sha256(
        canonical_dir / "predictions.csv"
    )
