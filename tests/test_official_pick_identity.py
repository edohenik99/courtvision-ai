from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

import courtvision.official_picks.service as official_pick_service
from courtvision.lifecycle.clock import FixedClock
from courtvision.lifecycle.models import EventType
from courtvision.lifecycle.writer import read_segment_events
from courtvision.official_picks import (
    LiveOfficialPickBlockedError,
    OfficialPickConflictError,
    OfficialPickLedgerIntegrityError,
    OfficialPickPromotionRequest,
    OfficialPickValidationError,
    promote_candidate_to_official_pick,
    promote_observation_to_official_pick,
    read_official_pick,
    read_official_picks,
)
from courtvision.official_picks.reporting import (
    OfficialPickReportBoundaryError,
    OfficialPickSettlementReferenceError,
    adapt_legacy_unidentified,
    build_official_pick_report_dataset,
    candidate_performance_metadata,
    observation_performance_metadata,
    require_official_pick_roi_rows,
    validate_settlement_pick_reference,
)


NOW = datetime(2026, 7, 26, 14, 0, tzinfo=UTC)
EVENT_START = datetime(2026, 7, 26, 23, 0, tzinfo=UTC)
PICK_ID = "pick_0123456789abcdef0123456789abcdef"
TRANSACTION_ID = "official-pick-promotion-test-001"


def _nba_candidate(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "sport": "basketball",
        "league": "NBA",
        "event_id": "nba-game-001",
        "event_start_time": EVENT_START,
        "prediction_date": "2026-07-26",
        "market_key": "player_points",
        "selection": "OVER",
        "line": 24.5,
        "odds": -110,
        "sportsbook": "DraftKings",
        "player_id": "nba-player-23",
        "player_name": "Test Player",
        "team_id": "nba-team-lal",
        "model_name": "nba-props",
        "model_version": "2026.07",
        "run_id": "nba-run-001",
        "source_candidate_id": "nba-candidate-001",
        "provenance": {
            "git_commit_sha": "a" * 40,
            "input_manifest_hash": "b" * 64,
        },
    }
    value.update(updates)
    return value


def _mlb_observation(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "sport": "baseball",
        "league": "MLB",
        "event_id": "mlb-game-001",
        "event_start_time": EVENT_START,
        "prediction_date": "2026-07-26",
        "market_key": "player_home_runs",
        "selection": "YES",
        "line": 0.5,
        "odds": 350,
        "sportsbook": "FanDuel",
        "player_id": "mlbam-660271",
        "player_name": "Test Batter",
        "team_id": "mlb-team-laa",
        "model_name": "mlb-hr-research",
        "model_version": "baseline-v1",
        "run_id": "mlb-observation-run-001",
        "source_observation_id": "sportsbook-quote-001",
    }
    value.update(updates)
    return value


def _promote(
    tmp_path: Path,
    request: dict[str, object] | OfficialPickPromotionRequest | None = None,
    **kwargs: object,
):
    options: dict[str, object] = {
        "lifecycle_root": tmp_path / "data" / "lifecycle",
        "clock": FixedClock(NOW),
        "pick_id_factory": lambda: PICK_ID,
        "transaction_id_factory": lambda: TRANSACTION_ID,
    }
    options.update(kwargs)
    return promote_candidate_to_official_pick(
        request or _nba_candidate(),
        **options,
    )


def _snapshot(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


def test_creation_assigns_one_immutable_pick_id(tmp_path: Path) -> None:
    result = _promote(tmp_path)

    assert result.publication_status == "PUBLISHED"
    assert result.pick.pick_id == PICK_ID
    assert result.pick.record_kind == "OFFICIAL_PICK"
    assert result.pick.designation == "PAPER"
    assert result.pick.status == "PUBLISHED"
    with pytest.raises(FrozenInstanceError):
        result.pick.pick_id = "pick_ffffffffffffffffffffffffffffffff"  # type: ignore[misc]


def test_rereading_published_pick_preserves_pick_id(tmp_path: Path) -> None:
    result = _promote(tmp_path)

    reread = read_official_pick(tmp_path / "data" / "lifecycle", PICK_ID)

    assert reread == result.pick
    assert reread is not None
    assert reread.pick_id == PICK_ID
    events = read_segment_events(result.ledger_segment_directory)
    assert [event.event_type for event in events] == [
        EventType.OFFICIAL_PICK_PUBLISHED.value
    ]


def test_publication_rereads_and_verifies_committed_pick(
    tmp_path: Path, monkeypatch
) -> None:
    original_commit = official_pick_service.LifecycleWriter.commit_segment

    def commit_then_tamper(self, *args, **kwargs):
        commit = original_commit(self, *args, **kwargs)
        events_path = commit.segment_directory / "events.jsonl"
        events_path.write_text(
            events_path.read_text(encoding="utf-8").replace(
                '"model_version":"2026.07"',
                '"model_version":"tampered-after-commit"',
            ),
            encoding="utf-8",
        )
        return commit

    monkeypatch.setattr(
        official_pick_service.LifecycleWriter,
        "commit_segment",
        commit_then_tamper,
    )

    with pytest.raises(
        OfficialPickLedgerIntegrityError,
        match="failed verification",
    ):
        _promote(tmp_path)


def test_duplicate_promotion_is_protected_no_op(tmp_path: Path) -> None:
    first = _promote(tmp_path)
    second = _promote(
        tmp_path,
        transaction_id_factory=lambda: "official-pick-promotion-test-002",
    )

    assert second.publication_status == "ALREADY_PUBLISHED"
    assert second.pick == first.pick
    assert second.event_id == first.event_id
    assert second.ledger_segment_directory == first.ledger_segment_directory
    assert len(read_official_picks(tmp_path / "data" / "lifecycle")) == 1


def test_concurrent_duplicate_promotion_commits_one_pick(tmp_path: Path) -> None:
    barrier = Barrier(2)

    def racing_pick_id() -> str:
        barrier.wait(timeout=5)
        return f"pick_{uuid4().hex}"

    def promote():
        return promote_candidate_to_official_pick(
            _nba_candidate(),
            lifecycle_root=tmp_path / "data" / "lifecycle",
            clock=FixedClock(NOW),
            pick_id_factory=racing_pick_id,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: promote(), range(2)))

    assert sorted(item.publication_status for item in results) == [
        "ALREADY_PUBLISHED",
        "PUBLISHED",
    ]
    assert results[0].pick == results[1].pick
    assert len(read_official_picks(tmp_path / "data" / "lifecycle")) == 1


def test_conflicting_duplicate_promotion_fails(tmp_path: Path) -> None:
    first = _promote(tmp_path)
    before = _snapshot(first.ledger_segment_directory)

    with pytest.raises(OfficialPickConflictError, match="IDEMPOTENCY_CONFLICT"):
        _promote(tmp_path, _nba_candidate(odds=-105))

    assert _snapshot(first.ledger_segment_directory) == before
    assert read_official_pick(tmp_path / "data" / "lifecycle", PICK_ID) == first.pick


def test_generated_pick_id_must_be_globally_unique(tmp_path: Path) -> None:
    first = _promote(tmp_path)
    different_source = _nba_candidate(source_candidate_id="nba-candidate-002")

    with pytest.raises(OfficialPickConflictError, match="already committed"):
        _promote(
            tmp_path,
            different_source,
            transaction_id_factory=lambda: "official-pick-promotion-test-002",
        )

    assert read_official_picks(tmp_path / "data" / "lifecycle") == (first.pick,)


def test_observations_never_enter_official_pick_report_rows() -> None:
    observation = {
        "record_kind": "MARKET_OBSERVATION",
        "observation_id": "quote-001",
        "result": "won",
    }

    dataset = build_official_pick_report_dataset((observation,))

    assert dataset.rows == ()
    assert dataset.excluded_observation_count == 1
    assert dataset.performance_label == "official-pick ROI"
    assert observation_performance_metadata() == {
        "report_scope": "MARKET_OBSERVATIONS_ONLY",
        "performance_label": "observation performance",
        "betting_roi_claim": "NOT_PERMITTED",
    }
    with pytest.raises(OfficialPickReportBoundaryError):
        require_official_pick_roi_rows((observation,))


def test_model_candidate_is_not_automatically_an_official_pick(
    tmp_path: Path,
) -> None:
    candidate = _nba_candidate(record_kind="MODEL_CANDIDATE")

    assert read_official_picks(tmp_path / "data" / "lifecycle") == ()
    dataset = build_official_pick_report_dataset((candidate,))

    assert dataset.rows == ()
    assert dataset.excluded_candidate_count == 1
    assert candidate_performance_metadata()["betting_roi_claim"] == "NOT_PERMITTED"


def test_mlb_sportsbook_rows_remain_observations_without_explicit_promotion(
    tmp_path: Path,
) -> None:
    observation = {
        **_mlb_observation(),
        "record_kind": "MARKET_OBSERVATION",
        "event_type": "MARKET_QUOTE_OBSERVED",
    }

    assert read_official_picks(tmp_path / "data" / "lifecycle") == ()
    dataset = build_official_pick_report_dataset((observation,))
    assert dataset.rows == ()
    assert dataset.excluded_observation_count == 1


def test_nba_candidate_requires_explicit_promotion(tmp_path: Path) -> None:
    candidate = _nba_candidate()
    assert read_official_picks(tmp_path / "data" / "lifecycle") == ()

    promoted = _promote(tmp_path, candidate)

    assert promoted.pick.source_candidate_id == "nba-candidate-001"
    assert len(read_official_picks(tmp_path / "data" / "lifecycle")) == 1


def test_mlb_observation_can_only_become_pick_through_explicit_service(
    tmp_path: Path,
) -> None:
    result = promote_observation_to_official_pick(
        _mlb_observation(),
        lifecycle_root=tmp_path / "data" / "lifecycle",
        designation="RESEARCH",
        clock=FixedClock(NOW),
        pick_id_factory=lambda: PICK_ID,
        transaction_id_factory=lambda: TRANSACTION_ID,
    )

    assert result.pick.source_observation_id == "sportsbook-quote-001"
    assert result.pick.source_candidate_id is None
    assert result.pick.designation == "RESEARCH"


def test_publication_rolls_back_completely_on_failure(tmp_path: Path) -> None:
    def fail_at(stage: str) -> None:
        if stage == "after_data_files_written":
            raise RuntimeError("injected publication failure")

    with pytest.raises(RuntimeError, match="injected publication failure"):
        _promote(tmp_path, failure_hook=fail_at)

    lifecycle_root = tmp_path / "data" / "lifecycle"
    assert read_official_picks(lifecycle_root) == ()
    assert list(lifecycle_root.rglob("COMPLETE")) == []
    assert not (lifecycle_root / ".writer.lock").exists()


def test_official_pick_paths_never_change_process_working_directory(
    tmp_path: Path, monkeypatch
) -> None:
    caller_directory = tmp_path / "caller"
    caller_directory.mkdir()
    monkeypatch.chdir(caller_directory)

    success = _promote(tmp_path / "success")
    assert success.publication_status == "PUBLISHED"
    assert Path.cwd() == caller_directory

    def fail_at(stage: str) -> None:
        if stage == "after_data_files_written":
            raise RuntimeError("injected publication failure")

    with pytest.raises(RuntimeError, match="injected publication failure"):
        _promote(tmp_path / "rollback", failure_hook=fail_at)
    assert Path.cwd() == caller_directory

    with pytest.raises(OfficialPickValidationError):
        _promote(
            tmp_path / "validation",
            _nba_candidate(event_id=""),
        )
    assert Path.cwd() == caller_directory

    events_path = success.ledger_segment_directory / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace(
            '"model_version":"2026.07"',
            '"model_version":"tampered"',
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        OfficialPickLedgerIntegrityError,
        match="failed verification",
    ):
        read_official_picks(tmp_path / "success" / "data" / "lifecycle")
    assert Path.cwd() == caller_directory


def test_existing_official_pick_cannot_be_overwritten(tmp_path: Path) -> None:
    first = _promote(tmp_path)
    lifecycle_root = tmp_path / "data" / "lifecycle"
    before = _snapshot(lifecycle_root)

    with pytest.raises(OfficialPickConflictError):
        _promote(tmp_path, _nba_candidate(line=25.5))

    assert _snapshot(lifecycle_root) == before
    assert read_official_pick(lifecycle_root, PICK_ID) == first.pick


def test_overwritten_ledger_bytes_fail_integrity_validation(tmp_path: Path) -> None:
    result = _promote(tmp_path)
    events_path = result.ledger_segment_directory / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace(
            '"model_version":"2026.07"',
            '"model_version":"tampered"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        OfficialPickLedgerIntegrityError, match="failed verification"
    ):
        read_official_picks(tmp_path / "data" / "lifecycle")


def test_settlement_reference_requires_valid_committed_pick_id(
    tmp_path: Path,
) -> None:
    result = _promote(tmp_path)
    root = tmp_path / "data" / "lifecycle"

    assert validate_settlement_pick_reference(
        {"pick_id": PICK_ID, "result": "won"},
        lifecycle_root=root,
    ) == result.pick
    with pytest.raises(
        OfficialPickSettlementReferenceError, match="non-empty pick_id"
    ):
        validate_settlement_pick_reference({"result": "won"}, lifecycle_root=root)
    with pytest.raises(
        OfficialPickSettlementReferenceError, match="not present"
    ):
        validate_settlement_pick_reference(
            {"pick_id": "pick_ffffffffffffffffffffffffffffffff"},
            lifecycle_root=root,
        )


def test_live_and_bankroll_output_remain_blocked(tmp_path: Path) -> None:
    with pytest.raises(LiveOfficialPickBlockedError, match="blocked"):
        _promote(tmp_path, designation="LIVE")

    assert read_official_picks(tmp_path / "data" / "lifecycle") == ()
    assert not (tmp_path / "outputs").exists()
    assert not any("kelly" in path.name.lower() for path in tmp_path.rglob("*"))
    assert not any("bankroll" in path.name.lower() for path in tmp_path.rglob("*"))


@pytest.mark.parametrize(
    "updates",
    [
        {"event_id": ""},
        {"event_start_time": "not-a-time"},
        {"market_key": "mystery_market"},
        {"selection": ""},
        {"line": "nan"},
        {"odds": 0},
        {"sportsbook": "unknown book"},
        {"player_id": None},
        {"player_name": None},
        {"model_name": ""},
        {"model_version": ""},
        {"run_id": ""},
        {"source_candidate_id": None},
    ],
)
def test_schema_rejects_incomplete_identity_before_ledger(
    tmp_path: Path,
    updates: dict[str, object],
) -> None:
    with pytest.raises(OfficialPickValidationError):
        _promote(tmp_path, _nba_candidate(**updates))

    assert read_official_picks(tmp_path / "data" / "lifecycle") == ()


def test_legacy_adapter_labels_without_guessing_pick_id() -> None:
    adapted = adapt_legacy_unidentified(
        {"event_id": "historical-event", "result": "won"}
    )

    assert adapted["record_kind"] == "LEGACY_UNIDENTIFIED"
    assert adapted["official_pick_identity_status"] == "legacy_unidentified"
    assert "pick_id" not in adapted
