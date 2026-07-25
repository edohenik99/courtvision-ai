"""Deterministic actionable-board versus shadow-ledger reconciliation."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from courtvision.lifecycle.canonical import file_sha256
from courtvision.lifecycle.clock import Clock, SystemClock
from courtvision.lifecycle.identity import (
    canonical_event_id,
    canonical_participant_id,
    normalize_bookmaker_id,
    normalize_line,
    normalize_market_id,
    normalize_selection,
)
from courtvision.lifecycle.models import (
    EventEnvelope,
    EventType,
    ReconciliationReport,
    ReconciliationStatus,
)


def read_canonical_board_rows(board_path: str | Path) -> tuple[dict[str, str], ...]:
    path = Path(board_path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return ()
        return tuple(
            {str(key): "" if value is None else str(value) for key, value in row.items()}
            for row in reader
        )


def reconcile_board_with_events(
    *,
    prediction_run_id: str,
    operating_date: str,
    board_path: str | Path,
    board_path_reference: str,
    events: Sequence[EventEnvelope],
    expected_board_sha256: str | None = None,
    clock: Clock | None = None,
) -> ReconciliationReport:
    active_clock = clock or SystemClock()
    path = Path(board_path)
    if not path.is_file():
        return ReconciliationReport(
            prediction_run_id=prediction_run_id,
            operating_date=operating_date,
            status=ReconciliationStatus.FAIL.value,
            board_published=False,
            board_path=board_path_reference,
            board_sha256=None,
            expected_row_count=0,
            committed_event_count=0,
            matched_row_count=0,
            unresolved_identity_count=0,
            mismatches=(),
            errors=("canonical actionable board is missing",),
            verified_at_utc=active_clock.now(),
        )
    board_hash = file_sha256(path)
    rows = read_canonical_board_rows(path)
    publication_events = tuple(
        event
        for event in events
        if event.event_type == EventType.PREDICTION_PUBLISHED.value
    )
    mismatches: list[Mapping[str, Any]] = []
    errors: list[str] = []
    if expected_board_sha256 is not None and board_hash != expected_board_sha256:
        errors.append("board artifact SHA-256 changed after publication capture")
    if len(rows) != len(publication_events):
        errors.append(
            "publication count mismatch: "
            f"board={len(rows)} ledger={len(publication_events)}"
        )
    events_by_row: dict[int, EventEnvelope] = {}
    events_by_id = {event.event_id: event for event in events}
    for event in publication_events:
        try:
            payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            errors.append(f"event payload is invalid JSON: {event.event_id}")
            continue
        index = payload.get("board_row_index")
        if not isinstance(index, int) or index < 0:
            errors.append(f"event has invalid board_row_index: {event.event_id}")
            continue
        if index in events_by_row:
            errors.append(f"duplicate ledger board_row_index: {index}")
            continue
        events_by_row[index] = event
    matched = 0
    unresolved = 0
    observation_degraded = False
    for index, expected_row in enumerate(rows):
        event = events_by_row.get(index)
        if event is None:
            mismatches.append(
                {"board_row_index": index, "reason": "MISSING_EVENT"}
            )
            continue
        payload = json.loads(event.payload_json)
        actual_row = payload.get("canonical_board_row")
        if actual_row != expected_row:
            mismatches.append(
                {
                    "board_row_index": index,
                    "event_id": event.event_id,
                    "reason": "BOARD_ROW_MISMATCH",
                    "fields": _different_fields(expected_row, actual_row),
                }
            )
            continue
        if payload.get("board_artifact_sha256") != board_hash:
            mismatches.append(
                {
                    "board_row_index": index,
                    "event_id": event.event_id,
                    "reason": "BOARD_HASH_MISMATCH",
                }
            )
            continue
        identity = payload.get("identity")
        if not isinstance(identity, dict) or identity.get("resolution_status") != "RESOLVED":
            unresolved += 1
        elif (
            event.prediction_id != identity.get("prediction_id")
            or event.prediction_key != identity.get("prediction_key")
            or event.market_subject_key != identity.get("market_subject_key")
        ):
            mismatches.append(
                {
                    "board_row_index": index,
                    "event_id": event.event_id,
                    "reason": "PREDICTION_IDENTITY_MISMATCH",
                }
            )
            continue
        if int(payload.get("payload_schema_version", 1) or 1) >= 2:
            link_findings, link_degraded = _verify_observation_links(
                expected_row,
                event,
                payload,
                events_by_id,
            )
            if link_findings:
                mismatches.extend(
                    {
                        "board_row_index": index,
                        "event_id": event.event_id,
                        **finding,
                    }
                    for finding in link_findings
                )
                continue
            observation_degraded = observation_degraded or link_degraded
        matched += 1
    extra_indices = sorted(set(events_by_row).difference(range(len(rows))))
    for index in extra_indices:
        mismatches.append(
            {
                "board_row_index": index,
                "event_id": events_by_row[index].event_id,
                "reason": "EXTRA_EVENT",
            }
        )
    for event in events:
        if event.event_type != EventType.RUN_COMPLETED.value:
            continue
        try:
            completed_payload = json.loads(event.payload_json)
        except json.JSONDecodeError:
            continue
        observation_capture = completed_payload.get("observation_capture")
        if (
            isinstance(observation_capture, Mapping)
            and observation_capture.get("capture_errors")
        ):
            observation_degraded = True
    if errors or mismatches:
        status = ReconciliationStatus.FAIL.value
    elif unresolved or observation_degraded:
        status = ReconciliationStatus.DEGRADED.value
    else:
        status = ReconciliationStatus.PASS.value
    return ReconciliationReport(
        prediction_run_id=prediction_run_id,
        operating_date=operating_date,
        status=status,
        board_published=True,
        board_path=board_path_reference,
        board_sha256=board_hash,
        expected_row_count=len(rows),
        committed_event_count=len(publication_events),
        matched_row_count=matched,
        unresolved_identity_count=unresolved,
        mismatches=tuple(mismatches),
        errors=tuple(errors),
        verified_at_utc=active_clock.now(),
    )


def _verify_observation_links(
    board_row: Mapping[str, Any],
    publication_event: EventEnvelope,
    publication_payload: Mapping[str, Any],
    events_by_id: Mapping[str, EventEnvelope],
) -> tuple[list[dict[str, Any]], bool]:
    links = publication_payload.get("observation_links")
    if not isinstance(links, Mapping):
        return ([{"reason": "OBSERVATION_LINKS_MISSING"}], False)
    findings: list[dict[str, Any]] = []
    degraded = links.get("link_status") != "COMPLETE"
    linked_hashes = publication_event.source_hashes.get(
        "observation_event_hashes", {}
    )
    if not isinstance(linked_hashes, Mapping):
        linked_hashes = {}

    schedule = _linked_event(
        links.get("schedule_observation_event_id"),
        expected_type=EventType.SCHEDULE_OBSERVED.value,
        label="SCHEDULE",
        events_by_id=events_by_id,
        linked_hashes=linked_hashes,
        findings=findings,
    )
    market = _linked_event(
        links.get("market_quote_observation_event_id"),
        expected_type=EventType.MARKET_QUOTE_OBSERVED.value,
        label="MARKET",
        events_by_id=events_by_id,
        linked_hashes=linked_hashes,
        findings=findings,
    )
    availability_ids = links.get("availability_observation_event_ids", [])
    if not isinstance(availability_ids, list):
        findings.append({"reason": "AVAILABILITY_LINK_COLLECTION_INVALID"})
        availability_ids = []
    availability = [
        event
        for item in availability_ids
        if (
            event := _linked_event(
                item,
                expected_type=EventType.PLAYER_AVAILABILITY_OBSERVED.value,
                label="AVAILABILITY",
                events_by_id=events_by_id,
                linked_hashes=linked_hashes,
                findings=findings,
            )
        )
        is not None
    ]

    board_event = canonical_event_id(
        _first_value(board_row, "canonical_event_id", "game_id"),
        sport="basketball",
        league="NBA",
    )
    board_participant = canonical_participant_id(
        _first_value(
            board_row,
            "canonical_player_id",
            "canonical_participant_id",
            "player_id",
        ),
        sport="basketball",
        league="NBA",
    )
    if schedule is not None:
        schedule_payload = _event_payload(schedule, findings)
        if schedule_payload is not None:
            if (
                schedule_payload.get("event_identity_resolution_status")
                != "RESOLVED"
            ):
                degraded = True
            _verify_source_binding(schedule, schedule_payload, findings)
            if (
                board_event is not None
                and schedule_payload.get("canonical_event_id") != board_event
            ):
                findings.append({"reason": "WRONG_SCHEDULE_EVENT_LINK"})
            _compare_optional_timestamp(
                board_row,
                ("game_datetime", "game_date"),
                schedule_payload.get("scheduled_start_at_utc"),
                "SCHEDULE_START_MISMATCH",
                findings,
            )
            _compare_optional_timestamp(
                board_row,
                ("schedule_updated_at", "game_updated_at"),
                schedule_payload.get("provider_reported_at_utc"),
                "SCHEDULE_PROVIDER_TIMESTAMP_MISMATCH",
                findings,
            )
            raw_board_status = _first_value(board_row, "game_status")
            if raw_board_status is not None:
                expected_status = _normalize_schedule_status(raw_board_status)
                actual_status = schedule_payload.get("game_status_normalized")
                if (
                    expected_status != "UNKNOWN"
                    and actual_status is not None
                    and expected_status != actual_status
                ):
                    findings.append({"reason": "SCHEDULE_STATUS_MISMATCH"})
    elif links.get("schedule_observation_event_id") is not None:
        # A non-null broken link already produced a FAIL finding.
        pass

    if market is not None:
        market_payload = _event_payload(market, findings)
        if market_payload is not None:
            if market_payload.get("identity_resolution_status") != "RESOLVED":
                degraded = True
            _verify_source_binding(market, market_payload, findings)
            if (
                board_event is not None
                and market_payload.get("canonical_event_id") != board_event
            ):
                findings.append({"reason": "WRONG_MARKET_EVENT_LINK"})
            if (
                board_participant is not None
                and market_payload.get("canonical_participant_id")
                != board_participant
            ):
                findings.append({"reason": "WRONG_MARKET_PARTICIPANT_LINK"})
            board_market = normalize_market_id(
                _first_value(
                    board_row,
                    "canonical_market_id",
                    "market_type",
                    "market",
                    "prop_type",
                ),
                sport="basketball",
                league="NBA",
            )
            if (
                board_market is not None
                and market_payload.get("canonical_market_id") != board_market
            ):
                findings.append({"reason": "MARKET_ID_MISMATCH"})
            board_selection = normalize_selection(
                _first_value(board_row, "selection", "side")
            )
            if (
                board_selection is not None
                and market_payload.get("selection") != board_selection
            ):
                findings.append({"reason": "MARKET_SELECTION_MISMATCH"})
            if not _same_decimal(
                _first_value(board_row, "sportsbook_line", "line"),
                market_payload.get("line"),
            ):
                findings.append({"reason": "MARKET_LINE_MISMATCH"})
            if not _same_decimal(
                _first_value(board_row, "odds", "entry_odds"),
                market_payload.get("odds"),
            ):
                findings.append({"reason": "MARKET_ODDS_MISMATCH"})
            board_bookmaker = normalize_bookmaker_id(
                _first_value(
                    board_row,
                    "canonical_bookmaker_id",
                    "bookmaker",
                    "sportsbook",
                    "vendor",
                )
            )
            if (
                board_bookmaker is not None
                and market_payload.get("canonical_bookmaker_id")
                != board_bookmaker
            ):
                findings.append({"reason": "MARKET_BOOKMAKER_MISMATCH"})
            _compare_optional_timestamp(
                board_row,
                (
                    "odds_updated_at",
                    "market_observed_at_utc",
                    "market_updated_at",
                ),
                market_payload.get("provider_reported_at_utc")
                or market_payload.get("market_observed_at_utc"),
                "MARKET_PROVIDER_TIMESTAMP_MISMATCH",
                findings,
            )

    for item in availability:
        availability_payload = _event_payload(item, findings)
        if availability_payload is None:
            continue
        if (
            availability_payload.get("identity_resolution_status")
            != "RESOLVED"
        ):
            degraded = True
        _verify_source_binding(item, availability_payload, findings)
        if (
            board_participant is not None
            and availability_payload.get("canonical_participant_id")
            != board_participant
        ):
            findings.append({"reason": "WRONG_AVAILABILITY_PARTICIPANT_LINK"})
        availability_event = availability_payload.get("canonical_event_id")
        if (
            board_event is not None
            and availability_event is not None
            and availability_event != board_event
        ):
            findings.append({"reason": "WRONG_AVAILABILITY_EVENT_LINK"})
        board_availability = _normalize_availability_status(
            _first_value(board_row, "injury_status", "availability_status")
        )
        observed_availability = availability_payload.get(
            "availability_status_normalized"
        )
        if (
            board_availability != "UNKNOWN"
            and observed_availability not in (None, "UNKNOWN")
            and board_availability != observed_availability
        ):
            findings.append({"reason": "AVAILABILITY_STATUS_MISMATCH"})
        _compare_optional_timestamp(
            board_row,
            ("injury_updated_at", "availability_updated_at"),
            availability_payload.get("provider_reported_at_utc"),
            "AVAILABILITY_PROVIDER_TIMESTAMP_MISMATCH",
            findings,
        )
    return findings, degraded


def _linked_event(
    event_id: Any,
    *,
    expected_type: str,
    label: str,
    events_by_id: Mapping[str, EventEnvelope],
    linked_hashes: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> EventEnvelope | None:
    if event_id is None:
        return None
    event = events_by_id.get(str(event_id))
    if event is None:
        findings.append({"reason": f"{label}_OBSERVATION_LINK_NOT_FOUND"})
        return None
    if event.event_type != expected_type:
        findings.append({"reason": f"{label}_OBSERVATION_LINK_WRONG_TYPE"})
        return None
    expected_hash = linked_hashes.get(str(event_id))
    if expected_hash != event.event_hash:
        findings.append({"reason": f"{label}_OBSERVATION_HASH_MISMATCH"})
    return event


def _event_payload(
    event: EventEnvelope,
    findings: list[dict[str, Any]],
) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(event.payload_json)
    except json.JSONDecodeError:
        findings.append({"reason": "OBSERVATION_PAYLOAD_INVALID"})
        return None
    if not isinstance(payload, Mapping):
        findings.append({"reason": "OBSERVATION_PAYLOAD_INVALID"})
        return None
    return payload


def _verify_source_binding(
    event: EventEnvelope,
    payload: Mapping[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    source_ref = payload.get("source_payload_ref")
    source_payload_hash = payload.get("source_payload_sha256")
    if event.source_refs.get("source_payload") != source_ref:
        findings.append({"reason": "OBSERVATION_SOURCE_REFERENCE_MISMATCH"})
    if event.source_hashes.get("source_payload_sha256") != source_payload_hash:
        findings.append({"reason": "OBSERVATION_SOURCE_PAYLOAD_HASH_MISMATCH"})
    evidence_hash = event.source_hashes.get("source_evidence_sha256")
    if (
        not isinstance(source_ref, str)
        or not source_ref.startswith("evidence://sha256/")
        or source_ref.rsplit("/", 1)[-1] != evidence_hash
    ):
        findings.append({"reason": "OBSERVATION_EVIDENCE_HASH_MISMATCH"})
    if not _same_optional_timestamp(
        event.provider_reported_at_utc,
        payload.get("provider_reported_at_utc"),
    ):
        findings.append({"reason": "OBSERVATION_PROVIDER_TIMESTAMP_MISMATCH"})


def _compare_optional_timestamp(
    row: Mapping[str, Any],
    names: Sequence[str],
    observed: Any,
    reason: str,
    findings: list[dict[str, Any]],
) -> None:
    expected = _first_value(row, *names)
    if expected is None or observed is None:
        return
    try:
        from courtvision.lifecycle.canonical import parse_utc_datetime

        expected_time = parse_utc_datetime(expected)
        observed_time = parse_utc_datetime(observed)
    except (TypeError, ValueError):
        return
    if expected_time != observed_time:
        findings.append({"reason": reason})


def _same_decimal(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        return Decimal(str(left).strip()) == Decimal(str(right).strip())
    except (InvalidOperation, ValueError):
        return False


def _same_optional_timestamp(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    try:
        from courtvision.lifecycle.canonical import parse_utc_datetime

        return parse_utc_datetime(left) == parse_utc_datetime(right)
    except (TypeError, ValueError):
        return False


def _first_value(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "null", "<na>"}:
            return value
    return None


def _normalize_schedule_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    values = {
        "scheduled": "SCHEDULED",
        "not started": "SCHEDULED",
        "delayed": "DELAYED",
        "delay": "DELAYED",
        "in progress": "IN_PROGRESS",
        "in_progress": "IN_PROGRESS",
        "live": "IN_PROGRESS",
        "halftime": "IN_PROGRESS",
        "final": "FINAL",
        "final/ot": "FINAL",
        "postponed": "POSTPONED",
        "cancelled": "CANCELLED",
        "canceled": "CANCELLED",
        "suspended": "SUSPENDED",
    }
    return values.get(text, "UNKNOWN")


def _normalize_availability_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    return {
        "active": "ACTIVE",
        "available": "AVAILABLE",
        "questionable": "QUESTIONABLE",
        "doubtful": "DOUBTFUL",
        "out": "OUT",
        "inactive": "INACTIVE",
        "starting": "STARTING",
        "not starting": "NOT_STARTING",
        "not_starting": "NOT_STARTING",
    }.get(text, "UNKNOWN")


def degraded_reconciliation(
    *,
    prediction_run_id: str,
    operating_date: str,
    board_path: str | None,
    board_sha256: str | None,
    expected_row_count: int,
    error: str,
    status: str = ReconciliationStatus.DEGRADED.value,
    clock: Clock | None = None,
) -> ReconciliationReport:
    active_clock = clock or SystemClock()
    return ReconciliationReport(
        prediction_run_id=prediction_run_id,
        operating_date=operating_date,
        status=status,
        board_published=True,
        board_path=board_path,
        board_sha256=board_sha256,
        expected_row_count=expected_row_count,
        committed_event_count=0,
        matched_row_count=0,
        unresolved_identity_count=0,
        mismatches=(),
        errors=(str(error),),
        verified_at_utc=active_clock.now(),
    )


def _different_fields(
    expected: Mapping[str, Any], actual: Any
) -> list[dict[str, Any]]:
    if not isinstance(actual, Mapping):
        return [{"field": "*", "expected": "board row object", "actual": type(actual).__name__}]
    fields: list[dict[str, Any]] = []
    for key in sorted(set(expected).union(actual)):
        if expected.get(key) != actual.get(key):
            fields.append(
                {
                    "field": key,
                    "expected": expected.get(key),
                    "actual": actual.get(key),
                }
            )
    return fields


__all__ = [
    "degraded_reconciliation",
    "read_canonical_board_rows",
    "reconcile_board_with_events",
]
