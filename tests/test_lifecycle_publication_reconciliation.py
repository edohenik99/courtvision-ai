from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from courtvision.lifecycle.canonical import payload_sha256
from courtvision.lifecycle.clock import FixedClock
from courtvision.lifecycle.models import EventEnvelope, EventType
from courtvision.lifecycle.publication import (
    begin_shadow_run,
    publish_shadow_after_board,
)
from courtvision.lifecycle.reconciliation import reconcile_board_with_events
from courtvision.lifecycle.writer import (
    LifecycleWriter,
    completed_segment_directories,
    read_segment_events,
)


NOW = datetime(2026, 7, 25, 17, 0, tzinfo=UTC)
DATE = "2026-07-25"
HEADER = (
    "game_id,player_id,player_name,team,opponent,market_type,selection,line,"
    "odds,vendor,line_source,model_projection,model_probability,edge,"
    "confidence,quality_score,selection_score,qualification_reason,"
    "game_datetime,game_status,injury_status,odds_updated_at,"
    "kelly_eligible,recommended_stake\n"
)
ROW = (
    "100,246,LeBron James,LAL,BOS,player_points,OVER,24.5,-110,"
    "DraftKings,live_market,26.2,0.58,1.7,0.72,88,91,elite,"
    "2026-07-25T23:00:00Z,scheduled,available,"
    "2026-07-25T16:45:00Z,True,0.02\n"
)


@dataclass
class _Runtime:
    player_baselines_path: Path
    team_baselines_path: Path
    calibration_path: Path


def _context(tmp_path: Path, *, clock: FixedClock | None = None):
    model = tmp_path / "outputs" / "model"
    model.mkdir(parents=True, exist_ok=True)
    (model / "player_baselines.csv").write_text("player_id,value\n246,25\n", encoding="utf-8")
    (model / "team_baselines.csv").write_text("team,value\nLAL,110\n", encoding="utf-8")
    (model / "calibration.json").write_text("{}", encoding="utf-8")
    runtime = _Runtime(
        model / "player_baselines.csv",
        model / "team_baselines.csv",
        model / "calibration.json",
    )
    context = begin_shadow_run(
        runtime,
        repository_root=tmp_path,
        lifecycle_root=tmp_path / "data" / "lifecycle",
        prediction_date=DATE,
        verbose_outputs=False,
        force_output_overwrite=False,
        clock=clock or FixedClock(NOW),
        environ={"COURTVISION_LIFECYCLE_SHADOW": "1"},
    )
    assert context is not None
    return context


def _board(tmp_path: Path, *, row: str = ROW) -> Path:
    path = (
        tmp_path
        / "outputs"
        / "runtime"
        / "operator"
        / f"elite_board_{DATE}.csv"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + row, encoding="utf-8")
    return path


def _published_event(result) -> EventEnvelope:
    assert result.segment_directory is not None
    return next(
        event
        for event in read_segment_events(result.segment_directory)
        if event.event_type == EventType.PREDICTION_PUBLISHED.value
    )


def test_successful_publication_records_hashes_and_reconciles_pass(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    before = board.read_bytes()
    context = _context(tmp_path)
    result = publish_shadow_after_board(context, board_path=board)
    assert result.status == "PASS"
    assert result.commit_status == "COMMITTED"
    assert result.reconciliation.expected_row_count == 1
    assert result.reconciliation.matched_row_count == 1
    assert result.reconciliation.unresolved_identity_count == 0
    assert result.reconciliation_path is not None
    assert board.read_bytes() == before
    event = _published_event(result)
    payload = json.loads(event.payload_json)
    assert payload["canonical_board_row"]["recommended_stake"] == "0.02"
    assert payload["board_artifact_sha256"] == result.reconciliation.board_sha256
    assert event.idempotency_key == f"PREDICTION_PUBLISHED:{event.prediction_id}"


def test_unknown_source_timestamp_remains_null(tmp_path: Path) -> None:
    board = _board(
        tmp_path,
        row=ROW.replace("2026-07-25T16:45:00Z", "2026-07-25 16:45:00"),
    )
    result = publish_shadow_after_board(_context(tmp_path), board_path=board)
    event = _published_event(result)
    assert event.provider_reported_at_utc is None


def test_unresolved_identity_is_committed_and_reconciles_degraded(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path, row=ROW.replace("DraftKings", "Mystery Bets"))
    result = publish_shadow_after_board(_context(tmp_path), board_path=board)
    assert result.status == "DEGRADED"
    event = _published_event(result)
    assert event.prediction_id is None
    payload = json.loads(event.payload_json)
    assert payload["identity"]["resolution_status"] == "UNRESOLVED"
    assert "canonical_bookmaker_id" in payload["identity"]["unresolved_fields"]


@pytest.mark.parametrize(
    ("row", "unresolved_field"),
    [
        (
            ROW.replace("100,246", "UNKNOWN,246", 1),
            "canonical_event_id",
        ),
        (
            ROW.replace("100,246", "100,UNKNOWN", 1),
            "canonical_participant_id",
        ),
    ],
)
def test_unknown_required_identity_keeps_prediction_keys_null_and_degrades(
    tmp_path: Path,
    row: str,
    unresolved_field: str,
) -> None:
    board = _board(tmp_path, row=row)
    before = board.read_bytes()
    context = _context(tmp_path)
    result = publish_shadow_after_board(context, board_path=board)
    event = _published_event(result)
    payload = json.loads(event.payload_json)
    identity = payload["identity"]

    assert result.status == "DEGRADED"
    assert result.reconciliation.unresolved_identity_count == 1
    assert identity["resolution_status"] == "UNRESOLVED"
    assert unresolved_field in identity["unresolved_fields"]
    assert identity["market_subject_key"] is None
    assert identity["prediction_key"] is None
    assert identity["prediction_id"] is None
    assert event.market_subject_key is None
    assert event.prediction_key is None
    assert event.prediction_id is None
    assert event.idempotency_key == (
        "PREDICTION_PUBLISHED_UNRESOLVED:"
        f"{context.prediction_run_id}:0:"
        f"{payload_sha256(payload['canonical_board_row'])}"
    )
    assert board.read_bytes() == before


def test_empty_successful_board_commits_run_without_prediction_events(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path, row="")
    result = publish_shadow_after_board(_context(tmp_path), board_path=board)
    assert result.status == "PASS"
    events = read_segment_events(result.segment_directory)
    assert [event.event_type for event in events] == [
        "RUN_STARTED",
        "RUN_COMPLETED",
    ]
    assert result.reconciliation.expected_row_count == 0


def test_board_failure_cannot_create_valid_publication_events(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    missing = tmp_path / "outputs" / "runtime" / "operator" / f"elite_board_{DATE}.csv"
    result = publish_shadow_after_board(context, board_path=missing)
    assert result.status == "FAIL"
    assert result.segment_directory is None
    assert completed_segment_directories(context.lifecycle_root) == ()


def test_board_success_and_ledger_failure_returns_degraded_without_board_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board = _board(tmp_path)
    before = board.read_bytes()

    def fail_commit(*args, **kwargs):
        raise OSError("injected ledger failure")

    monkeypatch.setattr(LifecycleWriter, "commit_segment", fail_commit)
    result = publish_shadow_after_board(_context(tmp_path), board_path=board)
    assert result.status == "DEGRADED"
    assert result.segment_directory is None
    assert board.read_bytes() == before


def test_secret_bearing_board_fields_are_rejected_without_persisting_values(
    tmp_path: Path,
) -> None:
    secret = "credential-must-not-persist"
    board = _board(tmp_path)
    board.write_text(
        HEADER.rstrip("\n") + ",api_key\n" + ROW.rstrip("\n") + f",{secret}\n",
        encoding="utf-8",
    )
    context = _context(tmp_path)
    result = publish_shadow_after_board(context, board_path=board)
    assert result.status == "DEGRADED"
    assert result.segment_directory is None
    assert completed_segment_directories(context.lifecycle_root) == ()
    persisted = b"".join(
        path.read_bytes()
        for path in context.lifecycle_root.rglob("*")
        if path.is_file()
    )
    assert secret.encode("utf-8") not in persisted


def test_missing_event_is_reconciliation_fail(tmp_path: Path) -> None:
    board = _board(tmp_path)
    report = reconcile_board_with_events(
        prediction_run_id="run-a",
        operating_date=DATE,
        board_path=board,
        board_path_reference=f"outputs/runtime/operator/elite_board_{DATE}.csv",
        events=(),
        clock=FixedClock(NOW),
    )
    assert report.status == "FAIL"
    assert report.expected_row_count == 1
    assert report.committed_event_count == 0


def test_extra_event_is_reconciliation_fail(tmp_path: Path) -> None:
    board = _board(tmp_path)
    first = publish_shadow_after_board(_context(tmp_path), board_path=board)
    original = _published_event(first)
    payload = json.loads(original.payload_json)
    payload["board_row_index"] = 1
    extra = EventEnvelope.create(
        event_type=EventType.PREDICTION_PUBLISHED,
        payload=payload,
        payload_schema_version=1,
        prediction_run_id=original.prediction_run_id,
        prediction_id=original.prediction_id,
        prediction_key=original.prediction_key,
        market_subject_key=original.market_subject_key,
        event_sequence=99,
        occurred_at_utc=NOW,
        recorded_at_utc=NOW,
        operating_date=DATE,
        operating_timezone="America/Toronto",
        actor_type="SYSTEM",
        actor_id="test",
        correlation_id=original.prediction_run_id,
        idempotency_key="extra",
    )
    report = reconcile_board_with_events(
        prediction_run_id=original.prediction_run_id,
        operating_date=DATE,
        board_path=board,
        board_path_reference=f"outputs/runtime/operator/elite_board_{DATE}.csv",
        events=(original, extra),
        clock=FixedClock(NOW),
    )
    assert report.status == "FAIL"
    assert any(item["reason"] == "EXTRA_EVENT" for item in report.mismatches)


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("line", "25.5"),
        ("odds", "-105"),
        ("model_projection", "27.1"),
        ("model_probability", "0.61"),
    ],
)
def test_reconciliation_detects_prediction_value_mismatches(
    tmp_path: Path,
    field: str,
    new_value: str,
) -> None:
    board = _board(tmp_path)
    first = publish_shadow_after_board(_context(tmp_path), board_path=board)
    original = _published_event(first)
    payload = json.loads(original.payload_json)
    payload["canonical_board_row"][field] = new_value
    changed = EventEnvelope.create(
        event_type=EventType.PREDICTION_PUBLISHED,
        payload=payload,
        payload_schema_version=1,
        prediction_run_id=original.prediction_run_id,
        prediction_id=original.prediction_id,
        prediction_key=original.prediction_key,
        market_subject_key=original.market_subject_key,
        event_sequence=2,
        occurred_at_utc=NOW,
        recorded_at_utc=NOW,
        operating_date=DATE,
        operating_timezone="America/Toronto",
        actor_type="SYSTEM",
        actor_id="test",
        correlation_id=original.prediction_run_id,
        idempotency_key=f"changed:{field}",
    )
    report = reconcile_board_with_events(
        prediction_run_id=original.prediction_run_id,
        operating_date=DATE,
        board_path=board,
        board_path_reference=f"outputs/runtime/operator/elite_board_{DATE}.csv",
        events=(changed,),
        clock=FixedClock(NOW),
    )
    assert report.status == "FAIL"
    fields = report.mismatches[0]["fields"]
    assert any(item["field"] == field for item in fields)


def test_shadow_integration_does_not_touch_legacy_outputs_or_lock_logic(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    history = tmp_path / "data" / "history" / "prediction_history.csv"
    grading = tmp_path / "data" / "history" / "pick_history.csv"
    kelly = tmp_path / "outputs" / "runtime" / "operator" / f"kelly_{DATE}.csv"
    for path, content in (
        (history, b"history-sentinel\n"),
        (grading, b"grading-sentinel\n"),
        (kelly, b"kelly-sentinel\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    before = {path: path.read_bytes() for path in (board, history, grading, kelly)}
    result = publish_shadow_after_board(_context(tmp_path), board_path=board)
    assert result.status == "PASS"
    assert {path: path.read_bytes() for path in before} == before
    assert set(completed_segment_directories(tmp_path / "data" / "lifecycle"))
