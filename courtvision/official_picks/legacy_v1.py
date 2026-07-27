"""Compatibility-only schema-v1 OfficialPick parser.

No production historical-v1 lifecycle segments are registered. The distinct
DTO in this module is available only to explicit migration tooling and is not
accepted by active publication, reconstruction, settlement, or reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from courtvision.lifecycle.canonical import (
    canonical_equal,
    freeze_json_value,
    parse_utc_datetime,
    thaw_json_value,
)
from courtvision.official_picks.contracts import (
    OfficialPickValidationError,
    PickRecordKind,
)


_LEGACY_V1_FIELDS = {
    "pick_id",
    "sport",
    "league",
    "event_id",
    "event_start_time",
    "prediction_date",
    "market_key",
    "selection",
    "line",
    "odds",
    "sportsbook",
    "player_id",
    "player_name",
    "team_id",
    "model_name",
    "model_version",
    "run_id",
    "published_at",
    "source_candidate_id",
    "source_observation_id",
    "status",
    "designation",
    "idempotency_key",
    "provenance",
    "schema_version",
    "record_kind",
}


@dataclass(frozen=True, slots=True)
class LegacyOfficialPickV1:
    """Frozen migration DTO that cannot masquerade as an active OfficialPick."""

    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", freeze_json_value(self.payload))

    @property
    def schema_version(self) -> int:
        return 1

    @property
    def record_kind(self) -> str:
        return PickRecordKind.OFFICIAL_PICK.value

    @property
    def pick_id(self) -> str:
        return str(self.payload["pick_id"])

    def to_dict(self) -> dict[str, Any]:
        value = thaw_json_value(self.payload)
        if not isinstance(value, dict):
            raise OfficialPickValidationError(
                "legacy official-pick payload is not an object"
            )
        return value


def parse_legacy_official_pick_v1(
    value: Mapping[str, Any],
) -> LegacyOfficialPickV1:
    """Parse v1 only for an explicit, audited migration/import procedure."""

    if not isinstance(value, Mapping):
        raise OfficialPickValidationError(
            "legacy official-pick row must be a mapping"
        )
    data = dict(value)
    if data.get("schema_version") != 1:
        raise OfficialPickValidationError(
            "legacy compatibility parser accepts only schema_version 1"
        )
    if set(data) != _LEGACY_V1_FIELDS:
        missing = sorted(_LEGACY_V1_FIELDS - set(data))
        unexpected = sorted(set(data) - _LEGACY_V1_FIELDS)
        raise OfficialPickValidationError(
            "legacy official-pick schema fields are not exact; "
            f"missing={missing}, unexpected={unexpected}"
        )
    if data["record_kind"] != PickRecordKind.OFFICIAL_PICK.value:
        raise OfficialPickValidationError("record_kind must be OFFICIAL_PICK")
    if re.fullmatch(r"pick_[0-9a-f]{32}", str(data["pick_id"])) is None:
        raise OfficialPickValidationError("legacy pick_id is malformed")
    if re.fullmatch(
        r"opidem_[0-9a-f]{64}", str(data["idempotency_key"])
    ) is None:
        raise OfficialPickValidationError(
            "legacy official-pick idempotency_key is malformed"
        )
    candidate_id = str(data.get("source_candidate_id") or "").strip()
    observation_id = str(data.get("source_observation_id") or "").strip()
    if bool(candidate_id) == bool(observation_id):
        raise OfficialPickValidationError(
            "legacy v1 requires exactly one source identity"
        )
    if not isinstance(data.get("provenance"), Mapping):
        raise OfficialPickValidationError(
            "legacy official-pick provenance must be a mapping"
        )
    event_start = parse_utc_datetime(data.get("event_start_time"))
    published_at = parse_utc_datetime(data.get("published_at"))
    if event_start is None or published_at is None:
        raise OfficialPickValidationError(
            "legacy official-pick timestamps are required"
        )
    if published_at > event_start:
        raise OfficialPickValidationError(
            "legacy published_at must not be after event_start_time"
        )
    parsed = LegacyOfficialPickV1(data)
    if not canonical_equal(parsed.to_dict(), value):
        raise OfficialPickValidationError(
            "legacy official-pick row is not in canonical round-trip form"
        )
    return parsed


__all__ = ["LegacyOfficialPickV1", "parse_legacy_official_pick_v1"]
