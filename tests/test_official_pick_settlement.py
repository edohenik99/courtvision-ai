from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import dataclasses
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
import json
from pathlib import Path
from threading import Barrier
from typing import Any, Callable, Mapping, NotRequired, TypedDict, Unpack
from uuid import uuid4

import pytest

import courtvision.official_picks.settlement as settlement_service
from courtvision.lifecycle.clock import FixedClock
from courtvision.lifecycle.models import EventType
from courtvision.lifecycle.writer import LifecycleWriterBusyError, read_segment_events
from courtvision.official_picks import (
    OfficialPickLedgerIntegrityError,
    OfficialPickSettlementConflictError,
    OfficialPickSettlementLedgerIntegrityError,
    OfficialPickSettlementReferenceError,
    OfficialPickSettlementTransitionError,
    correct_official_pick_settlement,
    promote_candidate_to_official_pick,
    read_official_picks,
    read_official_pick_settlement_corrections,
    read_official_pick_settlement_state,
    read_official_pick_settlements,
    review_official_pick_candidate,
    settle_official_pick,
)
from courtvision.official_picks.mlb_reconciliation import (
    MLBOfficialPickReconciliationValidationError,
    MLBOfficialPickReconciliationReason,
    create_mlb_official_pick_reconciliation_item,
)
from courtvision.official_picks.reporting import (
    build_official_pick_settlement_dataset,
)


PUBLISHED_AT = datetime(2026, 7, 26, 14, 0, tzinfo=UTC)
EVENT_START = datetime(2026, 7, 26, 23, 0, tzinfo=UTC)
SETTLED_AT = datetime(2026, 7, 27, 2, 0, tzinfo=UTC)
CORRECTED_AT = datetime(2026, 7, 27, 3, 0, tzinfo=UTC)
NBA_PICK_ID = "pick_0123456789abcdef0123456789abcdef"
MLB_PICK_ID = "pick_fedcba9876543210fedcba9876543210"
SETTLEMENT_ID = "settlement_0123456789abcdef0123456789abcdef"
FINAL_SETTLEMENT_ID = "settlement_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
CORRECTION_ID = (
    "settlement_correction_0123456789abcdef0123456789abcdef"
)


class _SettlementOptions(TypedDict):
    outcome: str
    result_source: str
    source_record_id: str
    settlement_run_id: str
    result_evidence: Mapping[str, Any]
    final_score: str | Mapping[str, Any] | None
    lifecycle_root: Path
    clock: FixedClock
    settlement_id_factory: Callable[[], str]
    transaction_id_factory: Callable[[], str]
    failure_hook: NotRequired[Callable[[str], None] | None]


class _SettlementOverrides(TypedDict, total=False):
    result_evidence: Mapping[str, Any]
    final_score: str | Mapping[str, Any] | None
    settlement_id_factory: Callable[[], str]
    failure_hook: Callable[[str], None] | None


def _candidate(*, sport: str = "basketball", **updates: object) -> dict[str, object]:
    if sport == "baseball":
        value: dict[str, object] = {
            "record_kind": "MODEL_CANDIDATE",
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
            "run_id": "mlb-run-001",
            "designation": "PAPER",
            "source_candidate_id": "mlb-candidate-001",
        }
    else:
        value = {
            "record_kind": "MODEL_CANDIDATE",
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
            "designation": "PAPER",
            "source_candidate_id": "nba-candidate-001",
        }
    value.update(updates)
    return value


def _promote(
    tmp_path: Path,
    *,
    pick_id: str = NBA_PICK_ID,
    sport: str = "basketball",
    transaction_id: str = "official-pick-promotion-test-001",
):
    candidate = _candidate(sport=sport)
    review = review_official_pick_candidate(
        candidate,
        operator_decision="APPROVED",
        operator_id="operator.settlement-test",
        decision_reason="settlement fixture approval",
        review_run_id=f"settlement-review-{sport}",
        lifecycle_root=tmp_path / "data" / "lifecycle",
        clock=FixedClock(PUBLISHED_AT),
    )
    return promote_candidate_to_official_pick(
        candidate,
        lifecycle_root=tmp_path / "data" / "lifecycle",
        review_id=review.review.review_id,
        clock=FixedClock(PUBLISHED_AT),
        pick_id_factory=lambda: pick_id,
        transaction_id_factory=lambda: transaction_id,
    )


def _settle(
    tmp_path: Path,
    *,
    pick_id: str = NBA_PICK_ID,
    outcome: str = "WIN",
    clock_at: datetime = SETTLED_AT,
    settlement_id: str = SETTLEMENT_ID,
    transaction_id: str = "official-pick-settlement-test-001",
    **updates: Unpack[_SettlementOverrides],
):
    options: _SettlementOptions = {
        "outcome": outcome,
        "result_source": "fixture.boxscore",
        "source_record_id": "boxscore-game-001",
        "settlement_run_id": "settlement-run-001",
        "result_evidence": {"player_points": 27, "game_status": "FINAL"},
        "final_score": {"away": 101, "home": 108},
        "lifecycle_root": tmp_path / "data" / "lifecycle",
        "clock": FixedClock(clock_at),
        "settlement_id_factory": lambda: settlement_id,
        "transaction_id_factory": lambda: transaction_id,
    }
    if "result_evidence" in updates:
        options["result_evidence"] = updates["result_evidence"]
    if "final_score" in updates:
        options["final_score"] = updates["final_score"]
    if "settlement_id_factory" in updates:
        options["settlement_id_factory"] = updates["settlement_id_factory"]
    if "failure_hook" in updates:
        options["failure_hook"] = updates["failure_hook"]
    return settle_official_pick(pick_id, **options)


def _snapshot(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in path.rglob("*")
        if item.is_file()
    }


@pytest.mark.parametrize(
    "pick_id",
    [
        "",
        "nba-candidate-001",
        "sportsbook-observation-001",
        "legacy-unidentified-001",
        "pick_ffffffffffffffffffffffffffffffff",
    ],
)
def test_settlement_requires_a_committed_official_pick(
    tmp_path: Path, pick_id: str
) -> None:
    with pytest.raises(OfficialPickSettlementReferenceError, match="committed"):
        _settle(tmp_path, pick_id=pick_id)

    assert read_official_pick_settlements(
        tmp_path / "data" / "lifecycle"
    ) == ()


def test_tampered_official_pick_ledger_is_rejected_before_settlement(
    tmp_path: Path,
) -> None:
    promoted = _promote(tmp_path)
    events_path = promoted.ledger_segment_directory / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace(
            '"model_version":"2026.07"',
            '"model_version":"tampered"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(OfficialPickLedgerIntegrityError, match="failed verification"):
        _settle(tmp_path)


def test_successful_settlement_is_published_and_reread_immutably(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    result = _settle(tmp_path)

    assert result.publication_status == "PUBLISHED"
    assert result.settlement.settlement_id == SETTLEMENT_ID
    assert result.settlement.pick_id == NBA_PICK_ID
    assert result.settlement.settlement_status == "FINAL"
    assert result.settlement.outcome == "WIN"
    assert read_official_pick_settlements(
        tmp_path / "data" / "lifecycle"
    ) == (result.settlement,)
    with pytest.raises(FrozenInstanceError):
        result.settlement.outcome = "LOSS"  # type: ignore[misc]
    events = read_segment_events(result.ledger_segment_directory)
    assert [item.event_type for item in events] == [
        EventType.OFFICIAL_PICK_SETTLED.value
    ]


def test_identical_settlement_replay_returns_original_commit(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    first = _settle(tmp_path)
    second = _settle(
        tmp_path,
        settlement_id="settlement_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        transaction_id="official-pick-settlement-test-002",
    )

    assert second.publication_status == "ALREADY_PUBLISHED"
    assert second.settlement == first.settlement
    assert second.event_id == first.event_id
    assert second.ledger_segment_directory == first.ledger_segment_directory
    assert len(
        read_official_pick_settlements(tmp_path / "data" / "lifecycle")
    ) == 1


def test_conflicting_final_replay_fails_without_changing_ledger(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    first = _settle(tmp_path)
    before = _snapshot(tmp_path / "data" / "lifecycle")

    with pytest.raises(
        OfficialPickSettlementConflictError, match="IDEMPOTENCY_CONFLICT"
    ):
        _settle(tmp_path, outcome="LOSS")

    assert _snapshot(tmp_path / "data" / "lifecycle") == before
    assert read_official_pick_settlements(
        tmp_path / "data" / "lifecycle"
    ) == (first.settlement,)


def test_concurrent_initial_transitions_commit_only_one_state(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    barrier = Barrier(2)

    def racing_settlement_id() -> str:
        barrier.wait(timeout=5)
        return f"settlement_{uuid4().hex}"

    def publish(outcome: str):
        try:
            return _settle(
                tmp_path,
                outcome=outcome,
                final_score=(
                    None
                    if outcome == "UNRESOLVED"
                    else {"away": 101, "home": 108}
                ),
                result_evidence=(
                    {"reason": "game_not_final"}
                    if outcome == "UNRESOLVED"
                    else {"player_points": 27, "game_status": "FINAL"}
                ),
                settlement_id_factory=racing_settlement_id,
                transaction_id=f"official-pick-settlement-race-{outcome.lower()}",
            )
        except (
            OfficialPickSettlementConflictError,
            OfficialPickSettlementTransitionError,
            LifecycleWriterBusyError,
        ) as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(publish, ("UNRESOLVED", "WIN")))

    assert sum(hasattr(item, "settlement") for item in results) == 1
    assert sum(isinstance(item, Exception) for item in results) == 1
    assert len(
        read_official_pick_settlements(tmp_path / "data" / "lifecycle")
    ) == 1


def test_settlement_publication_rolls_back_completely_on_failure(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)

    def fail_at(stage: str) -> None:
        if stage == "after_data_files_written":
            raise RuntimeError("injected settlement publication failure")

    with pytest.raises(RuntimeError, match="injected settlement"):
        _settle(tmp_path, failure_hook=fail_at)

    root = tmp_path / "data" / "lifecycle"
    assert read_official_pick_settlements(root) == ()
    assert len(list(root.rglob("COMPLETE"))) == 2
    assert not (root / ".writer.lock").exists()


def test_committed_settlement_segment_cannot_be_overwritten(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    result = _settle(tmp_path)
    events_path = result.ledger_segment_directory / "events.jsonl"
    events_path.write_text(
        events_path.read_text(encoding="utf-8").replace(
            '"outcome":"WIN"',
            '"outcome":"LOSS"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        OfficialPickSettlementLedgerIntegrityError, match="failed verification"
    ):
        read_official_pick_settlements(tmp_path / "data" / "lifecycle")


def test_unresolved_transitions_to_final_through_a_new_event(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    unresolved = _settle(
        tmp_path,
        outcome="UNRESOLVED",
        final_score=None,
        result_evidence={"reason": "game_not_final"},
    )
    final = _settle(
        tmp_path,
        outcome="WIN",
        clock_at=datetime(2026, 7, 27, 4, 0, tzinfo=UTC),
        settlement_id=FINAL_SETTLEMENT_ID,
        transaction_id="official-pick-settlement-test-002",
    )

    assert unresolved.settlement.settlement_status == "UNRESOLVED"
    assert final.settlement.settlement_status == "FINAL"
    assert final.settlement.settlement_id != unresolved.settlement.settlement_id
    state = read_official_pick_settlement_state(
        tmp_path / "data" / "lifecycle", NBA_PICK_ID
    )
    assert state is not None
    assert state.unresolved_settlement == unresolved.settlement
    assert state.final_settlement == final.settlement
    assert state.effective_outcome == "WIN"


def test_final_settlement_cannot_be_replaced_by_unresolved(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    _settle(tmp_path)

    with pytest.raises(
        OfficialPickSettlementTransitionError, match="cannot be silently replaced"
    ):
        _settle(
            tmp_path,
            outcome="UNRESOLVED",
            final_score=None,
            result_evidence={"reason": "manual_review_required"},
        )


def test_explicit_correction_references_and_preserves_original_settlement(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    original = _settle(tmp_path)

    correction = correct_official_pick_settlement(
        original.settlement.settlement_id,
        pick_id=NBA_PICK_ID,
        correction_reason="source boxscore was corrected",
        corrected_outcome="LOSS",
        corrected_final_score={"away": 108, "home": 101},
        corrected_result_evidence={
            "player_points": 21,
            "game_status": "FINAL",
        },
        result_source="fixture.corrected_boxscore",
        source_record_id="boxscore-game-001-revision-2",
        correction_run_id="correction-run-001",
        lifecycle_root=tmp_path / "data" / "lifecycle",
        clock=FixedClock(CORRECTED_AT),
        correction_id_factory=lambda: CORRECTION_ID,
        transaction_id_factory=lambda: (
            "official-pick-settlement-correction-test-001"
        ),
    )

    assert correction.correction.original_settlement_id == SETTLEMENT_ID
    assert correction.correction.pick_id == NBA_PICK_ID
    assert correction.correction.corrected_outcome == "LOSS"
    assert read_official_pick_settlements(
        tmp_path / "data" / "lifecycle"
    ) == (original.settlement,)
    assert read_official_pick_settlement_corrections(
        tmp_path / "data" / "lifecycle"
    ) == (correction.correction,)
    correction_event = read_segment_events(
        correction.ledger_segment_directory
    )[0]
    original_event = read_segment_events(original.ledger_segment_directory)[0]
    assert (
        correction_event.event_type
        == EventType.OFFICIAL_PICK_SETTLEMENT_CORRECTION_RECORDED.value
    )
    assert correction_event.corrects_event_id == original_event.event_id
    state = read_official_pick_settlement_state(
        tmp_path / "data" / "lifecycle", NBA_PICK_ID
    )
    assert state is not None
    assert state.final_settlement == original.settlement
    assert state.correction == correction.correction
    assert state.effective_outcome == "LOSS"


def test_correction_replay_is_idempotent_and_conflict_fails(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    original = _settle(tmp_path)
    options = {
        "pick_id": NBA_PICK_ID,
        "correction_reason": "official stat correction",
        "corrected_outcome": "PUSH",
        "corrected_final_score": {"away": 104, "home": 104},
        "corrected_result_evidence": {"player_points": 24.5},
        "result_source": "fixture.corrected_boxscore",
        "source_record_id": "boxscore-game-001-revision-2",
        "correction_run_id": "correction-run-001",
        "lifecycle_root": tmp_path / "data" / "lifecycle",
        "clock": FixedClock(CORRECTED_AT),
        "correction_id_factory": lambda: CORRECTION_ID,
        "transaction_id_factory": lambda: (
            "official-pick-settlement-correction-test-001"
        ),
    }
    first = correct_official_pick_settlement(
        original.settlement.settlement_id, **options
    )
    second = correct_official_pick_settlement(
        original.settlement.settlement_id,
        **{
            **options,
            "correction_id_factory": lambda: (
                "settlement_correction_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
            ),
            "transaction_id_factory": lambda: (
                "official-pick-settlement-correction-test-002"
            ),
        },
    )

    assert second.correction == first.correction
    assert second.publication_status == "ALREADY_PUBLISHED"
    with pytest.raises(OfficialPickSettlementConflictError):
        correct_official_pick_settlement(
            original.settlement.settlement_id,
            **{**options, "corrected_outcome": "LOSS"},
        )


def test_official_settlement_report_joins_only_committed_pick_ids(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    _promote(
        tmp_path,
        pick_id=MLB_PICK_ID,
        sport="baseball",
        transaction_id="official-pick-promotion-test-002",
    )
    _settle(tmp_path)
    sources = (
        {
            "record_kind": "MARKET_OBSERVATION",
            "observation_id": "quote-001",
            "result": "won",
        },
        {
            "record_kind": "MODEL_CANDIDATE",
            "candidate_id": "candidate-001",
            "result": "won",
        },
        {"result": "legacy-won"},
        {
            "record_kind": "SETTLED_OFFICIAL_PICK",
            "pick_id": "pick_ffffffffffffffffffffffffffffffff",
            "outcome": "WIN",
        },
    )

    dataset = build_official_pick_settlement_dataset(
        lifecycle_root=tmp_path / "data" / "lifecycle",
        source_rows=sources,
    )

    assert [item.pick_id for item in dataset.settled_rows] == [
        NBA_PICK_ID
    ]
    assert [item.pick_id for item in dataset.unresolved_rows] == [
        MLB_PICK_ID
    ]
    assert dataset.excluded_observation_count == 1
    assert dataset.excluded_candidate_count == 1
    assert dataset.excluded_legacy_count == 1
    assert not hasattr(dataset, "bankroll_calculated")
    assert not hasattr(dataset, "kelly_calculated")
    dataset_dict = dataclasses.asdict(dataset)
    assert "bankroll_calculated" not in dataset_dict
    assert "kelly_calculated" not in dataset_dict
    rows = dataset.to_rows()
    prohibited_keys = {
        "bankroll",
        "kelly_fraction",
        "stake",
        "wager_amount",
        "expected_profit",
        "roi",
        "live_bet",
        "wagering_metadata",
        "execution_instructions",
    }
    assert all(
        not (set(row) & prohibited_keys)
        and all(
            value is None
            or isinstance(value, (str, int, float, bool))
            for value in row.values()
        )
        for row in rows
    )
    serialized = json.dumps(rows).lower()
    assert "pick_ffffffffffffffffffffffffffffffff" not in serialized


def test_mlb_reconciliation_requires_unresolved_committed_official_pick(
    tmp_path: Path,
) -> None:
    _promote(
        tmp_path,
        pick_id=MLB_PICK_ID,
        sport="baseball",
        transaction_id="official-pick-promotion-test-mlb",
    )
    item = create_mlb_official_pick_reconciliation_item(
        MLB_PICK_ID,
        reason="player_missing_from_boxscore",
        reconciliation_run_id="mlb-reconciliation-run-001",
        lifecycle_root=tmp_path / "data" / "lifecycle",
        clock=FixedClock(SETTLED_AT),
    )

    assert item.pick_id == MLB_PICK_ID
    assert item.reason == "player_missing_from_boxscore"
    with pytest.raises(OfficialPickSettlementReferenceError):
        create_mlb_official_pick_reconciliation_item(
            "sportsbook-observation-001",
            reason="game_not_final",
            reconciliation_run_id="mlb-reconciliation-run-001",
            lifecycle_root=tmp_path / "data" / "lifecycle",
        )
    with pytest.raises(OfficialPickSettlementReferenceError):
        create_mlb_official_pick_reconciliation_item(
            "pick_ffffffffffffffffffffffffffffffff",
            reason="game_not_final",
            reconciliation_run_id="mlb-reconciliation-run-001",
            lifecycle_root=tmp_path / "data" / "lifecycle",
        )


def test_mlb_reconciliation_reason_contract_is_frozen() -> None:
    assert {item.value for item in MLBOfficialPickReconciliationReason} == {
        "game_not_final",
        "player_missing_from_boxscore",
        "event_not_matched",
        "ambiguous_player_identity",
        "source_unavailable",
        "manual_review_required",
    }


def test_mlb_reconciliation_rejects_non_mlb_and_final_picks(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    with pytest.raises(
        MLBOfficialPickReconciliationValidationError, match="MLB official pick"
    ):
        create_mlb_official_pick_reconciliation_item(
            NBA_PICK_ID,
            reason="manual_review_required",
            reconciliation_run_id="mlb-reconciliation-run-001",
            lifecycle_root=tmp_path / "data" / "lifecycle",
        )

    root = tmp_path / "mlb"
    _promote(
        root,
        pick_id=MLB_PICK_ID,
        sport="baseball",
        transaction_id="official-pick-promotion-test-mlb",
    )
    _settle(root, pick_id=MLB_PICK_ID)
    with pytest.raises(
        OfficialPickSettlementTransitionError, match="cannot enter"
    ):
        create_mlb_official_pick_reconciliation_item(
            MLB_PICK_ID,
            reason="manual_review_required",
            reconciliation_run_id="mlb-reconciliation-run-001",
            lifecycle_root=root / "data" / "lifecycle",
        )


def test_settlement_foundation_does_not_activate_live_kelly_or_bankroll_output(
    tmp_path: Path,
) -> None:
    _promote(tmp_path)
    _settle(tmp_path)

    assert not (tmp_path / "outputs").exists()
    assert not any("kelly" in path.name.lower() for path in tmp_path.rglob("*"))
    assert not any(
        "bankroll" in path.name.lower() for path in tmp_path.rglob("*")
    )


def test_changed_json_schemas_are_valid_draft_2020_12() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_root = (
        Path(__file__).parents[1]
        / "courtvision"
        / "lifecycle"
        / "schemas"
    )
    for name in (
        "event_envelope_v1.json",
        "official_pick_settled_payload_v1.json",
        "official_pick_settlement_correction_payload_v1.json",
    ):
        schema = json.loads((schema_root / name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)


def test_published_payloads_match_frozen_json_schemas(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    _promote(tmp_path)
    settled = _settle(tmp_path)
    corrected = correct_official_pick_settlement(
        settled.settlement.settlement_id,
        pick_id=NBA_PICK_ID,
        correction_reason="validated official stat correction",
        corrected_outcome="LOSS",
        corrected_final_score={"away": 108, "home": 101},
        corrected_result_evidence={"player_points": 21},
        result_source="fixture.corrected_boxscore",
        source_record_id="boxscore-game-001-revision-2",
        correction_run_id="correction-run-schema-test",
        lifecycle_root=tmp_path / "data" / "lifecycle",
        clock=FixedClock(CORRECTED_AT),
        correction_id_factory=lambda: CORRECTION_ID,
        transaction_id_factory=lambda: (
            "official-pick-settlement-correction-schema-test"
        ),
    )
    schema_root = (
        Path(__file__).parents[1]
        / "courtvision"
        / "lifecycle"
        / "schemas"
    )
    cases = (
        (
            settled.ledger_segment_directory,
            "official_pick_settled_payload_v1.json",
        ),
        (
            corrected.ledger_segment_directory,
            "official_pick_settlement_correction_payload_v1.json",
        ),
    )
    for segment, schema_name in cases:
        event = read_segment_events(segment)[0]
        payload = json.loads(event.payload_json)
        schema = json.loads(
            (schema_root / schema_name).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_publication_rereads_and_verifies_committed_settlement(
    tmp_path: Path, monkeypatch
) -> None:
    _promote(tmp_path)
    original_commit = settlement_service.LifecycleWriter.commit_segment

    def commit_then_tamper(self, *args, **kwargs):
        commit = original_commit(self, *args, **kwargs)
        events_path = commit.segment_directory / "events.jsonl"
        events_path.write_text(
            events_path.read_text(encoding="utf-8").replace(
                '"outcome":"WIN"',
                '"outcome":"LOSS"',
            ),
            encoding="utf-8",
        )
        return commit

    monkeypatch.setattr(
        settlement_service.LifecycleWriter,
        "commit_segment",
        commit_then_tamper,
    )

    with pytest.raises(
        OfficialPickSettlementLedgerIntegrityError, match="failed verification"
    ):
        _settle(tmp_path)
