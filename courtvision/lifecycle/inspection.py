"""Read-only inspection utilities for immutable lifecycle observations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from courtvision.lifecycle.models import EventEnvelope, EventType
from courtvision.lifecycle.writer import (
    LifecycleIntegrityError,
    completed_segment_directories,
    read_segment_events,
    verify_segment,
)


OBSERVATION_EVENT_TYPES = frozenset(
    {
        EventType.SCHEDULE_OBSERVED.value,
        EventType.MARKET_QUOTE_OBSERVED.value,
        EventType.PLAYER_AVAILABILITY_OBSERVED.value,
    }
)


def list_observations_for_run(
    lifecycle_root: str | Path,
    prediction_run_id: str,
) -> tuple[EventEnvelope, ...]:
    return tuple(
        event
        for event in _verified_events(lifecycle_root)
        if event.prediction_run_id == str(prediction_run_id)
        and event.event_type in OBSERVATION_EVENT_TYPES
    )


def list_observations_for_prediction(
    lifecycle_root: str | Path,
    prediction_identifier: str,
) -> tuple[EventEnvelope, ...]:
    events = _verified_events(lifecycle_root)
    linked_ids: set[str] = set()
    for event in events:
        if event.event_type != EventType.PREDICTION_PUBLISHED.value:
            continue
        if str(prediction_identifier) not in {
            str(event.prediction_id or ""),
            str(event.prediction_key or ""),
        }:
            continue
        payload = _payload(event)
        links = payload.get("observation_links", {})
        if not isinstance(links, Mapping):
            continue
        for name in (
            "schedule_observation_event_id",
            "market_quote_observation_event_id",
        ):
            value = links.get(name)
            if value:
                linked_ids.add(str(value))
        values = links.get("availability_observation_event_ids", [])
        if isinstance(values, list):
            linked_ids.update(str(value) for value in values if value)
    return tuple(
        event
        for event in events
        if event.event_id in linked_ids
        and event.event_type in OBSERVATION_EVENT_TYPES
    )


def verify_observation_segment(
    lifecycle_root: str | Path,
    segment_directory: str | Path,
):
    return verify_segment(
        segment_directory,
        lifecycle_root=lifecycle_root,
    )


def show_schedule_history_for_event(
    lifecycle_root: str | Path,
    event_identifier: str,
) -> tuple[EventEnvelope, ...]:
    return _history(
        lifecycle_root,
        EventType.SCHEDULE_OBSERVED.value,
        lambda payload: str(event_identifier)
        in {
            str(payload.get("canonical_event_id") or ""),
            str(payload.get("provider_event_id") or ""),
        },
    )


def show_quote_history_for_prediction_key(
    lifecycle_root: str | Path,
    prediction_key: str,
) -> tuple[EventEnvelope, ...]:
    return tuple(
        event
        for event in _verified_events(lifecycle_root)
        if event.event_type == EventType.MARKET_QUOTE_OBSERVED.value
        and event.prediction_key == str(prediction_key)
    )


def show_availability_history_for_player_event(
    lifecycle_root: str | Path,
    participant_identifier: str,
    event_identifier: str | None = None,
) -> tuple[EventEnvelope, ...]:
    def matches(payload: Mapping[str, Any]) -> bool:
        if str(participant_identifier) not in {
            str(payload.get("canonical_participant_id") or ""),
            str(payload.get("provider_participant_id") or ""),
        }:
            return False
        if event_identifier is None:
            return True
        return str(event_identifier) in {
            str(payload.get("canonical_event_id") or ""),
            str(payload.get("provider_event_id") or ""),
        }

    return _history(
        lifecycle_root,
        EventType.PLAYER_AVAILABILITY_OBSERVED.value,
        matches,
    )


def _history(
    lifecycle_root: str | Path,
    event_type: str,
    predicate,
) -> tuple[EventEnvelope, ...]:
    events = [
        event
        for event in _verified_events(lifecycle_root)
        if event.event_type == event_type and predicate(_payload(event))
    ]
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.occurred_at_utc,
                event.recorded_at_utc,
                event.event_id,
            ),
        )
    )


def _verified_events(
    lifecycle_root: str | Path,
) -> tuple[EventEnvelope, ...]:
    root = Path(lifecycle_root)
    events: list[EventEnvelope] = []
    for segment in completed_segment_directories(root):
        verification = verify_segment(segment, lifecycle_root=root)
        if not verification.ok:
            raise LifecycleIntegrityError(
                "observation inspection refused an invalid segment: "
                + "; ".join(verification.violations)
            )
        events.extend(read_segment_events(segment))
    return tuple(events)


def _payload(event: EventEnvelope) -> Mapping[str, Any]:
    value = json.loads(event.payload_json)
    if not isinstance(value, Mapping):
        raise LifecycleIntegrityError(
            f"event payload is not an object: {event.event_id}"
        )
    return value


def _event_output(events: Iterable[EventEnvelope]) -> list[dict[str, Any]]:
    return [
        {
            "envelope": event.to_dict(),
            "payload": _payload(event),
        }
        for event in events
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect CourtVision immutable lifecycle observations."
    )
    parser.add_argument(
        "--lifecycle-root",
        default="data/lifecycle",
        help="Lifecycle storage root.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="List observations for one run.")
    run.add_argument("prediction_run_id")
    prediction = sub.add_parser(
        "prediction", help="List observations linked to one prediction."
    )
    prediction.add_argument("prediction_identifier")
    verify = sub.add_parser("verify", help="Verify one observation segment.")
    verify.add_argument("segment_directory")
    schedule = sub.add_parser(
        "schedule", help="Show schedule history for an event."
    )
    schedule.add_argument("event_identifier")
    quote = sub.add_parser(
        "quote", help="Show quote history for a prediction key."
    )
    quote.add_argument("prediction_key")
    availability = sub.add_parser(
        "availability", help="Show player availability history."
    )
    availability.add_argument("participant_identifier")
    availability.add_argument("--event-id", default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.lifecycle_root)
    if args.command == "run":
        output: Any = _event_output(
            list_observations_for_run(root, args.prediction_run_id)
        )
    elif args.command == "prediction":
        output = _event_output(
            list_observations_for_prediction(
                root, args.prediction_identifier
            )
        )
    elif args.command == "verify":
        result = verify_observation_segment(root, args.segment_directory)
        output = {
            "ok": result.ok,
            "segment_directory": str(result.segment_directory),
            "violations": list(result.violations),
            "event_count": result.event_count,
        }
    elif args.command == "schedule":
        output = _event_output(
            show_schedule_history_for_event(root, args.event_identifier)
        )
    elif args.command == "quote":
        output = _event_output(
            show_quote_history_for_prediction_key(root, args.prediction_key)
        )
    else:
        output = _event_output(
            show_availability_history_for_player_event(
                root,
                args.participant_identifier,
                args.event_id,
            )
        )
    print(json.dumps(output, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "OBSERVATION_EVENT_TYPES",
    "list_observations_for_prediction",
    "list_observations_for_run",
    "show_availability_history_for_player_event",
    "show_quote_history_for_prediction_key",
    "show_schedule_history_for_event",
    "verify_observation_segment",
]
