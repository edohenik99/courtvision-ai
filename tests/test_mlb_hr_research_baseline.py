from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

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
