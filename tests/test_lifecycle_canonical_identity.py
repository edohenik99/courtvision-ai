from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

import pytest

from courtvision.lifecycle.canonical import (
    CanonicalizationError,
    canonical_json_v1,
    canonical_payload_bytes,
    payload_sha256,
)
from courtvision.lifecycle.clock import FixedClock, utc_now
from courtvision.lifecycle.identity import (
    UNKNOWN_IDENTITY_SENTINELS,
    derive_publication_identity,
)
from courtvision.lifecycle.models import EventEnvelope, EventType
from courtvision.lifecycle.provenance import capture_git_provenance
from courtvision.lifecycle.publication import begin_shadow_run, lifecycle_shadow_enabled


NOW = datetime(2026, 7, 25, 15, 30, 45, 123456, tzinfo=UTC)


def _identity(**overrides: object):
    values: dict[str, object] = {
        "sport": "basketball",
        "league": "NBA",
        "event_id": "100",
        "participant_id": "246",
        "market_id": "player_points",
        "selection": "over",
        "line": "24.50",
        "bookmaker": "DraftKings",
        "prediction_run_id": "run-a",
    }
    values.update(overrides)
    return derive_publication_identity(**values)


def test_utc_clock_produces_aware_timestamp() -> None:
    value = utc_now()
    assert value.tzinfo is not None
    assert value.utcoffset() == timedelta(0)


def test_canonical_json_is_deterministic_utf8_and_order_independent() -> None:
    left = {"z": None, "a": "é", "nested": {"b": 2, "a": 1}}
    right = {"nested": {"a": 1, "b": 2}, "a": "é", "z": None}
    assert canonical_json_v1(left) == canonical_json_v1(right)
    assert payload_sha256(left) == payload_sha256(right)
    assert canonical_payload_bytes(left).decode("utf-8") == canonical_json_v1(left)
    assert " " not in canonical_json_v1(left)


def test_canonical_json_datetime_is_utc_and_naive_is_rejected() -> None:
    local = datetime(2026, 7, 25, 11, 30, tzinfo=UTC)
    assert canonical_json_v1({"at": local}) == '{"at":"2026-07-25T11:30:00.000000Z"}'
    with pytest.raises(CanonicalizationError, match="naive"):
        canonical_json_v1({"at": datetime(2026, 7, 25, 11, 30)})


def test_canonical_json_rejects_nondeterministic_types() -> None:
    with pytest.raises(CanonicalizationError, match="unsupported"):
        canonical_json_v1({"values": {1, 2}})


def test_identity_v1_is_deterministic_and_field_order_independent() -> None:
    first = _identity()
    second = derive_publication_identity(
        prediction_run_id="run-a",
        bookmaker="draft kings",
        line=24.5,
        selection="OVER",
        market_id="points",
        participant_id=246,
        event_id=100,
        league="NBA",
        sport="basketball",
    )
    assert first == second
    assert first.identity_schema_version == 1
    assert first.resolution_status == "RESOLVED"


def test_changed_line_changes_prediction_key_not_market_subject() -> None:
    first = _identity(line="24.5")
    second = _identity(line="25.5")
    assert first.prediction_key != second.prediction_key
    assert first.market_subject_key == second.market_subject_key


def test_changed_bookmaker_changes_prediction_and_subject_keys() -> None:
    first = _identity(bookmaker="DraftKings")
    second = _identity(bookmaker="FanDuel")
    assert first.prediction_key != second.prediction_key
    assert first.market_subject_key != second.market_subject_key


def test_changed_run_changes_prediction_id_but_not_prediction_key() -> None:
    first = _identity(prediction_run_id="run-a")
    second = _identity(prediction_run_id="run-b")
    assert first.prediction_key == second.prediction_key
    assert first.market_subject_key == second.market_subject_key
    assert first.prediction_id != second.prediction_id


def test_changed_event_changes_all_relevant_ids() -> None:
    first = _identity(event_id=100)
    second = _identity(event_id=101)
    assert first.market_subject_key != second.market_subject_key
    assert first.prediction_key != second.prediction_key
    assert first.prediction_id != second.prediction_id


def test_unknown_bookmaker_is_explicitly_unresolved() -> None:
    identity = _identity(bookmaker="Mystery Bets")
    assert identity.resolution_status == "UNRESOLVED"
    assert "canonical_bookmaker_id" in identity.unresolved_fields
    assert identity.prediction_id is None


@pytest.mark.parametrize(
    "field",
    ["event_id", "participant_id"],
)
def test_unknown_required_identity_is_explicitly_unresolved(field: str) -> None:
    identity = _identity(**{field: "UNKNOWN"})
    unresolved_field = (
        "canonical_event_id"
        if field == "event_id"
        else "canonical_participant_id"
    )
    assert identity.resolution_status == "UNRESOLVED"
    assert unresolved_field in identity.unresolved_fields
    assert identity.market_subject_key is None
    assert identity.prediction_key is None
    assert identity.prediction_id is None


@pytest.mark.parametrize(
    "sentinel",
    [
        "UNKNOWN",
        "unk",
        "N/A",
        "NA",
        "NONE",
        "NULL",
        "NAN",
        "<NA>",
        "MISSING",
        "NOT_AVAILABLE",
        "NOT APPLICABLE",
        "UNRESOLVED",
        "TBD",
        "-",
        "  UnKnOwN  ",
    ],
)
def test_identity_sentinels_match_complete_cleaned_value_only(
    sentinel: str,
) -> None:
    assert sentinel.strip().casefold() in UNKNOWN_IDENTITY_SENTINELS
    identity = _identity(event_id=sentinel)
    assert identity.canonical_event_id is None
    assert identity.resolution_status == "UNRESOLVED"


@pytest.mark.parametrize(
    ("field", "value", "expected_suffix"),
    [
        ("event_id", "event-none-7", ":event:event-none-7"),
        ("participant_id", "player-NA-42", ":participant:player-NA-42"),
        ("event_id", "unknown-event-9", ":event:unknown-event-9"),
    ],
)
def test_legitimate_identity_ids_containing_sentinel_substrings_resolve(
    field: str,
    value: str,
    expected_suffix: str,
) -> None:
    identity = _identity(**{field: value})
    canonical = (
        identity.canonical_event_id
        if field == "event_id"
        else identity.canonical_participant_id
    )
    assert identity.resolution_status == "RESOLVED"
    assert canonical is not None and canonical.endswith(expected_suffix)


@pytest.mark.parametrize(
    ("field", "canonical"),
    [
        ("event_id", "courtvision:basketball:nba:event:100"),
        (
            "participant_id",
            "courtvision:basketball:nba:participant:246",
        ),
    ],
)
def test_valid_already_namespaced_identity_is_preserved(
    field: str,
    canonical: str,
) -> None:
    identity = _identity(**{field: canonical})
    actual = (
        identity.canonical_event_id
        if field == "event_id"
        else identity.canonical_participant_id
    )
    assert identity.resolution_status == "RESOLVED"
    assert actual == canonical


@pytest.mark.parametrize(
    ("field", "canonical", "unresolved_field"),
    [
        (
            "event_id",
            "courtvision:basketball:nba:participant:100",
            "canonical_event_id",
        ),
        (
            "participant_id",
            "courtvision:basketball:nba:event:246",
            "canonical_participant_id",
        ),
    ],
)
def test_wrong_domain_canonical_namespace_fails_closed(
    field: str,
    canonical: str,
    unresolved_field: str,
) -> None:
    identity = _identity(**{field: canonical})
    assert identity.resolution_status == "UNRESOLVED"
    assert unresolved_field in identity.unresolved_fields


@pytest.mark.parametrize(
    "malformed",
    [
        "courtvision:event:100",
        "courtvision:basketball:nba:event:",
        "courtvision:basketball:nba:event:UNKNOWN",
        "courtvision:basketball:nba:event:bad value",
        "COURTVISION:basketball:nba:event:100",
    ],
)
def test_malformed_canonical_namespace_fails_closed(malformed: str) -> None:
    identity = _identity(event_id=malformed)
    assert identity.canonical_event_id is None
    assert identity.resolution_status == "UNRESOLVED"


def test_event_envelope_rejects_naive_lifecycle_timestamps() -> None:
    with pytest.raises(CanonicalizationError, match="naive"):
        EventEnvelope.create(
            event_type=EventType.RUN_STARTED,
            payload={"status": "STARTED"},
            payload_schema_version=1,
            prediction_run_id="run-a",
            event_sequence=1,
            occurred_at_utc=datetime(2026, 7, 25),
            recorded_at_utc=NOW,
            operating_date="2026-07-25",
            operating_timezone="America/Toronto",
            actor_type="SYSTEM",
            actor_id="test",
            correlation_id="run-a",
            idempotency_key="RUN_STARTED:run-a",
        )


@dataclass
class _DummyRuntime:
    player_baselines_path: Path
    team_baselines_path: Path
    calibration_path: Path


def test_two_actual_run_initializations_produce_different_run_ids(
    tmp_path: Path,
) -> None:
    runtime = _DummyRuntime(
        tmp_path / "player.csv",
        tmp_path / "team.csv",
        tmp_path / "calibration.json",
    )
    kwargs = {
        "repository_root": tmp_path,
        "prediction_date": "2026-07-25",
        "verbose_outputs": False,
        "force_output_overwrite": False,
        "clock": FixedClock(NOW),
        "environ": {"COURTVISION_LIFECYCLE_SHADOW": "1"},
    }
    first = begin_shadow_run(runtime, **kwargs)
    second = begin_shadow_run(runtime, **kwargs)
    assert first is not None and second is not None
    assert first.prediction_run_id != second.prediction_run_id


def test_feature_flag_defaults_off_and_accepts_explicit_true() -> None:
    assert lifecycle_shadow_enabled({}) is False
    assert lifecycle_shadow_enabled({"COURTVISION_LIFECYCLE_SHADOW": "1"}) is True
    assert lifecycle_shadow_enabled({"COURTVISION_LIFECYCLE_SHADOW": "true"}) is True


def test_git_provenance_distinguishes_clean_and_dirty(tmp_path: Path) -> None:
    def clean_runner(
        command: list[str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        if command[1] == "rev-parse":
            return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    def dirty_runner(
        command: list[str], cwd: Path
    ) -> subprocess.CompletedProcess[str]:
        if command[1] == "rev-parse":
            return subprocess.CompletedProcess(command, 0, "a" * 40 + "\n", "")
        if command[1] == "status":
            return subprocess.CompletedProcess(command, 0, " M tracked.py\n", "")
        return subprocess.CompletedProcess(command, 0, "diff --git a/tracked.py b/tracked.py\n", "")

    clean = capture_git_provenance(tmp_path, command_runner=clean_runner)
    dirty = capture_git_provenance(tmp_path, command_runner=dirty_runner)
    assert clean["git_dirty"] is False
    assert dirty["git_dirty"] is True
    assert clean["working_tree_hash"] != dirty["working_tree_hash"]
