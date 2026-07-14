from __future__ import annotations

import csv
from pathlib import Path

import pytest

from courtvision.sports.mlb.training.hr_research_baseline import (
    CLOSING_LINE_COLUMNS,
    FEATURE_COLUMNS,
    IDENTITY_CACHE_COLUMNS,
    LEDGER_COLUMNS,
    MLBHRResearchBaselineError,
    ODDS_REQUIRED_COLUMNS,
    PREDICTION_ELIGIBLE_STATUS,
    PREDICTION_COLUMNS,
    RESULTS_REQUIRED_COLUMNS,
    append_identity_cache_records,
    append_predictions_to_ledger,
    build_advanced_feature_readiness_matrix,
    build_live_hr_research_features,
    build_prospective_trial_report,
    capture_closing_line_snapshots,
    generate_daily_research_predictions,
    resolve_player_identities,
    run_daily_research,
    settle_prediction_ledger,
    train_research_logistic_baseline,
    verify_prediction_artifacts,
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


def _write_training_fixture(tmp_path: Path) -> tuple[Path, Path]:
    odds_rows = [
        _odds_row(event_id="event-1", player="Alpha Batter", price=450),
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
    odds_path = tmp_path / "training_odds.csv"
    results_path = tmp_path / "training_results.csv"
    _write_csv(odds_path, ODDS_REQUIRED_COLUMNS, odds_rows)
    _write_csv(results_path, (*RESULTS_REQUIRED_COLUMNS, "result_reason"), result_rows)
    return odds_path, results_path


def _train_fixture_model(tmp_path: Path) -> Path:
    odds_path, results_path = _write_training_fixture(tmp_path)
    feature_result = build_live_hr_research_features(
        odds_path=odds_path,
        results_path=results_path,
        generated_at="2026-07-04T00:00:00Z",
    )
    artifacts = write_feature_artifacts(feature_result, tmp_path / "features")
    training = train_research_logistic_baseline(
        feature_rows_path=artifacts["feature_rows_csv"],
        output_root=tmp_path / "models",
        model_version="test-v1",
        generated_at="2026-07-04T01:00:00Z",
    )
    return training.bundle_dir


def _prediction_odds(path: Path, *, player: str = "Future Batter") -> Path:
    _write_csv(
        path,
        ODDS_REQUIRED_COLUMNS,
        [
            _odds_row(
                event_id="future-event",
                player=player,
                commence_time="2026-07-04T23:00:00Z",
                snapshot_time="2026-07-04T15:00:00Z",
                price=500,
            )
        ],
    )
    return path


def test_identity_resolution_resolves_appends_and_quarantines_conflicts(tmp_path: Path) -> None:
    feature_rows = [
        {"player_name": "Jose Ramirez", "normalized_player_name": "jose ramirez"},
        {"player_name": "Ambiguous Guy", "normalized_player_name": "ambiguous guy"},
        {"player_name": "Missing Player", "normalized_player_name": "missing player"},
    ]
    provider_rows = [
        {
            "mlb_player_id": "608070",
            "canonical_mlb_name": "Jose Ramirez",
            "normalized_player_name": "jose ramirez",
            "identity_source": "unit_provider",
        },
        {
            "mlb_player_id": "111111",
            "canonical_mlb_name": "Ambiguous Guy",
            "normalized_player_name": "ambiguous guy",
            "identity_source": "unit_provider",
        },
        {
            "mlb_player_id": "222222",
            "canonical_mlb_name": "Ambiguous Guy",
            "normalized_player_name": "ambiguous guy",
            "identity_source": "unit_provider",
        },
    ]

    result = resolve_player_identities(
        feature_rows=feature_rows,
        identity_provider_rows=provider_rows,
        resolved_at="2026-07-04T12:00:00Z",
    )

    statuses = {row["normalized_player_name"]: row["identity_status"] for row in result.records}
    assert statuses["jose ramirez"] == "resolved"
    assert statuses["ambiguous guy"] == "quarantined"
    assert statuses["missing player"] == "unresolved"
    cache_path = tmp_path / "identity_cache.csv"
    assert append_identity_cache_records(cache_path=cache_path, records=result.records) == 3
    assert append_identity_cache_records(cache_path=cache_path, records=result.records) == 0

    conflict_rows = [
        {
            "cache_schema_version": "mlb-hr-player-identity-cache-v1",
            "cache_record_id": "conflict-a",
            "sportsbook_player_name": "Conflict Player",
            "normalized_player_name": "conflict player",
            "mlb_player_id": "333333",
            "canonical_mlb_name": "Conflict Player",
            "identity_status": "resolved",
            "identity_method": "manual",
            "identity_source": "review",
            "resolved_at": "2026-07-04T12:00:00Z",
            "reviewed_at": "",
            "review_status": "not_reviewed",
            "mapping_version": "research-identity-v1",
            "conflict_reason": "",
        },
        {
            "cache_schema_version": "mlb-hr-player-identity-cache-v1",
            "cache_record_id": "conflict-b",
            "sportsbook_player_name": "Conflict Player",
            "normalized_player_name": "conflict player",
            "mlb_player_id": "444444",
            "canonical_mlb_name": "Conflict Player",
            "identity_status": "resolved",
            "identity_method": "manual",
            "identity_source": "review",
            "resolved_at": "2026-07-04T12:00:00Z",
            "reviewed_at": "",
            "review_status": "not_reviewed",
            "mapping_version": "research-identity-v1",
            "conflict_reason": "",
        },
    ]
    conflict_cache = tmp_path / "conflict_cache.csv"
    _write_csv(conflict_cache, IDENTITY_CACHE_COLUMNS, conflict_rows)

    conflict = resolve_player_identities(
        feature_rows=[{"player_name": "Conflict Player"}],
        identity_cache_csv=conflict_cache,
        resolved_at="2026-07-04T12:00:00Z",
    )

    assert conflict.records[0]["identity_status"] == "quarantined"
    assert conflict.records[0]["identity_method"] == "cache_conflict"


def test_verify_predictions_detects_artifact_mutation_and_ledger_gaps(tmp_path: Path) -> None:
    bundle_dir = _train_fixture_model(tmp_path)
    odds_path = _prediction_odds(tmp_path / "prediction_odds.csv")
    output_dir = tmp_path / "predictions"
    generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        output_dir=output_dir,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
    )

    assert verify_prediction_artifacts(predictions_root=output_dir).passed

    ledger_path = tmp_path / "ledger.csv"
    append_predictions_to_ledger(
        predictions_csv=output_dir / "predictions.csv",
        ledger_path=ledger_path,
    )
    ledger_rows = _read_csv(ledger_path)
    extra = dict(ledger_rows[0])
    extra["prediction_id"] = "missing-artifact-id"
    ledger_rows.append(extra)
    _write_csv(ledger_path, LEDGER_COLUMNS, ledger_rows)

    gap_check = verify_prediction_artifacts(
        predictions_root=output_dir,
        ledger_path=ledger_path,
    )

    assert not gap_check.passed
    assert any("lacks artifact" in error for error in gap_check.errors)

    rows = _read_csv(output_dir / "predictions.csv")
    rows.append(dict(rows[0]))
    _write_csv(output_dir / "predictions.csv", PREDICTION_COLUMNS, rows)

    mutation_check = verify_prediction_artifacts(predictions_root=output_dir)

    assert not mutation_check.passed
    assert any("duplicate prediction_id" in error for error in mutation_check.errors)


def test_closing_line_capture_prefers_same_book_and_marks_missing_prestart(
    tmp_path: Path,
) -> None:
    bundle_dir = _train_fixture_model(tmp_path)
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
                price=500,
            ),
            _odds_row(
                event_id="future-event",
                player="Future Batter",
                commence_time="2026-07-04T23:00:00Z",
                snapshot_time="2026-07-04T22:30:00Z",
                price=450,
            ),
            _odds_row(
                event_id="late-event",
                player="Late Batter",
                commence_time="2026-07-04T23:00:00Z",
                snapshot_time="2026-07-04T23:30:00Z",
                price=300,
            ),
        ],
    )
    prediction_run = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    late_prediction = dict(prediction_run.predictions[0])
    late_prediction.update(
        {
            "prediction_id": "late-prediction",
            "event_id": "late-event",
            "player_name": "Late Batter",
            "normalized_player_name": "late batter",
            "american_odds": "300",
            "implied_probability": "0.25",
        }
    )
    closing_path = tmp_path / "closing_lines.csv"

    result = capture_closing_line_snapshots(
        odds_path=odds_path,
        predictions=[*prediction_run.predictions, late_prediction],
        output_csv=closing_path,
        captured_at="2026-07-04T22:45:00Z",
    )
    rerun = capture_closing_line_snapshots(
        odds_path=odds_path,
        predictions=[*prediction_run.predictions, late_prediction],
        output_csv=closing_path,
        captured_at="2026-07-04T22:50:00Z",
    )

    rows = _read_csv(closing_path)
    assert result.report["status_counts"]["captured_same_book"] == 1
    assert result.report["status_counts"]["missing_prestart"] == 1
    assert rows[0]["closing_american_odds"] == "450"
    assert rows[1]["closing_status"] == "missing_prestart"
    assert rerun.report["skipped_existing_rows"] == 2


def test_settlement_rerun_is_idempotent_and_conflicting_result_fails(
    tmp_path: Path,
) -> None:
    bundle_dir = _train_fixture_model(tmp_path)
    odds_path = _prediction_odds(tmp_path / "prediction_odds.csv", player="Settlement Batter")
    prediction_run = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    ledger_path = tmp_path / "ledger.csv"
    append_predictions_to_ledger(predictions=prediction_run.predictions, ledger_path=ledger_path)
    results_path = tmp_path / "results.csv"
    _write_csv(
        results_path,
        (*RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [
            _result_row(
                event_id="future-event",
                player="Settlement Batter",
                actual_home_runs=1,
            )
        ],
    )

    first = settle_prediction_ledger(
        ledger_path=ledger_path,
        results_path=results_path,
        settlement_timestamp="2026-07-05T05:00:00Z",
    )
    second = settle_prediction_ledger(
        ledger_path=ledger_path,
        results_path=results_path,
        settlement_timestamp="2026-07-05T05:30:00Z",
    )

    assert first.appended_settlements == 1
    assert second.skipped_existing_settlements == 1

    _write_csv(
        results_path,
        (*RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [
            _result_row(
                event_id="future-event",
                player="Settlement Batter",
                actual_home_runs=0,
            )
        ],
    )
    with pytest.raises(MLBHRResearchBaselineError, match="conflicting settlement"):
        settle_prediction_ledger(
            ledger_path=ledger_path,
            results_path=results_path,
            settlement_timestamp="2026-07-05T06:00:00Z",
        )


def test_daily_runner_dry_run_idempotency_force_and_no_games(tmp_path: Path) -> None:
    bundle_dir = _train_fixture_model(tmp_path)
    odds_path = _prediction_odds(tmp_path / "prediction_odds.csv")
    output_root = tmp_path / "daily_runs"
    ledger_path = tmp_path / "prospective_ledger.csv"

    dry = run_daily_research(
        target_date="2026-07-04",
        model_dir=bundle_dir,
        output_root=output_root,
        ledger_csv=ledger_path,
        odds_csv=odds_path,
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    first = run_daily_research(
        target_date="2026-07-04",
        model_dir=bundle_dir,
        output_root=output_root,
        ledger_csv=ledger_path,
        odds_csv=odds_path,
        prediction_timestamp="2026-07-04T17:00:00Z",
    )
    second = run_daily_research(
        target_date="2026-07-04",
        model_dir=bundle_dir,
        output_root=output_root,
        ledger_csv=ledger_path,
        odds_csv=odds_path,
        prediction_timestamp="2026-07-04T17:00:00Z",
    )
    forced = run_daily_research(
        target_date="2026-07-04",
        model_dir=bundle_dir,
        output_root=output_root,
        ledger_csv=ledger_path,
        odds_csv=odds_path,
        prediction_timestamp="2026-07-04T17:00:00Z",
        force=True,
    )
    empty = run_daily_research(
        target_date="2099-01-01",
        model_dir=bundle_dir,
        output_root=output_root,
        ledger_csv=ledger_path,
        odds_csv=odds_path,
        prediction_timestamp="2026-07-04T17:00:00Z",
    )

    assert dry.status == "dry_run"
    assert dry.output_dir is None
    assert first.status == "completed"
    assert second.status == "existing_completed_run"
    assert forced.status == "completed"
    assert forced.run_id != first.run_id
    assert empty.status == "completed_no_predictions"
    ledger_rows = _read_csv(ledger_path)
    assert sum(row["record_type"] == "prediction" for row in ledger_rows) == 2


def test_prospective_report_and_readiness_matrix_are_research_only(tmp_path: Path) -> None:
    bundle_dir = _train_fixture_model(tmp_path)
    odds_path = _prediction_odds(tmp_path / "prediction_odds.csv", player="Report Batter")
    prediction_run = generate_daily_research_predictions(
        model_bundle_dir=bundle_dir,
        odds_path=odds_path,
        target_date="2026-07-04",
        prediction_timestamp="2026-07-04T17:00:00Z",
        dry_run=True,
    )
    ledger_path = tmp_path / "ledger.csv"
    append_predictions_to_ledger(predictions=prediction_run.predictions, ledger_path=ledger_path)
    results_path = tmp_path / "results.csv"
    _write_csv(
        results_path,
        (*RESULTS_REQUIRED_COLUMNS, "result_reason"),
        [_result_row(event_id="future-event", player="Report Batter", actual_home_runs=1)],
    )
    settle_prediction_ledger(
        ledger_path=ledger_path,
        results_path=results_path,
        settlement_timestamp="2026-07-05T05:00:00Z",
    )
    closing_path = tmp_path / "closing_lines.csv"
    capture_closing_line_snapshots(
        odds_path=odds_path,
        predictions=prediction_run.predictions,
        output_csv=closing_path,
        captured_at="2026-07-04T22:00:00Z",
    )

    report = build_prospective_trial_report(
        ledger_path=ledger_path,
        closing_lines_csv=closing_path,
        generated_at="2026-07-06T00:00:00Z",
    )
    matrix = build_advanced_feature_readiness_matrix()

    assert report["official_betting_picks_ready"] is False
    assert report["counts"]["settled"] == 1
    assert report["metrics"]["model"]["status"] == "ok"
    assert report["research_pnl"]["staking_assumption"].startswith("flat 1u")
    assert matrix["approval_status"] == "not_approved"
    assert matrix["top_five_priorities"][0].startswith("deterministic player identity")
