from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import courtvision_ai

from courtvision.lifecycle.clock import FixedClock
from courtvision.lifecycle.inspection import (
    list_observations_for_prediction,
    list_observations_for_run,
    show_availability_history_for_player_event,
    show_quote_history_for_prediction_key,
    show_schedule_history_for_event,
    verify_observation_segment,
)
from courtvision.lifecycle.models import EventEnvelope, EventType
from courtvision.lifecycle.observations import (
    ObservationValidationError,
    implied_probability_from_american,
    materialize_observation_events,
    normalize_availability_status,
    normalize_observation_numeric,
    normalize_schedule_status,
    prepare_market_quote_observation,
    prepare_observation_batch,
    prepare_player_availability_observation,
    prepare_schedule_observation,
)
from courtvision.lifecycle.publication import (
    begin_shadow_run,
    publish_shadow_after_board,
)
from courtvision.lifecycle.reconciliation import reconcile_board_with_events
from courtvision.lifecycle.writer import (
    LifecycleWriter,
    read_segment_events,
    verify_segment,
)
from courtvision.shadow_lifecycle import (
    lifecycle_observations_enabled,
    load_shadow_lifecycle_hooks,
)


NOW = datetime(2026, 7, 25, 17, 0, tzinfo=UTC)
DATE = "2026-07-25"
HEADER = (
    "game_id,player_id,player_name,team,opponent,market_type,selection,line,"
    "odds,vendor,line_source,model_projection,edge,confidence,quality_score,"
    "selection_score,qualification_reason,game_datetime,game_status,"
    "injury_status,odds_updated_at,kelly_eligible,recommended_stake\n"
)
ROW = (
    "100,246,LeBron James,LAL,BOS,player_points,OVER,24.5,-110,"
    "DraftKings,live_market,26.2,1.7,0.72,88,91,elite,"
    "2026-07-25T23:00:00Z,scheduled,available,"
    "2026-07-25T16:45:00Z,True,0.02\n"
)


@dataclass
class _Runtime:
    player_baselines_path: Path
    team_baselines_path: Path
    calibration_path: Path


def _context(tmp_path: Path, *, now: datetime = NOW):
    model = tmp_path / "outputs" / "model"
    model.mkdir(parents=True, exist_ok=True)
    (model / "player_baselines.csv").write_text(
        "player_id,value\n246,25\n", encoding="utf-8"
    )
    (model / "team_baselines.csv").write_text(
        "team,value\nLAL,110\n", encoding="utf-8"
    )
    (model / "calibration.json").write_text("{}", encoding="utf-8")
    context = begin_shadow_run(
        _Runtime(
            model / "player_baselines.csv",
            model / "team_baselines.csv",
            model / "calibration.json",
        ),
        repository_root=tmp_path,
        lifecycle_root=tmp_path / "data" / "lifecycle",
        prediction_date=DATE,
        verbose_outputs=False,
        force_output_overwrite=False,
        clock=FixedClock(now),
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


def _frames(
    *,
    schedule_status: str = "scheduled",
    scheduled_start: str = "2026-07-25T23:00:00Z",
    line: object = 24.5,
    odds: object = -110,
    vendor: str = "DraftKings",
    availability: str = "available",
    include_availability: bool = True,
):
    games_raw = pd.DataFrame(
        [
            {
                "id": 100,
                "date": scheduled_start,
                "datetime": scheduled_start,
                "status": schedule_status,
                "home_team": {
                    "id": 14,
                    "abbreviation": "LAL",
                    "name": "Lakers",
                },
                "visitor_team": {
                    "id": 2,
                    "abbreviation": "BOS",
                    "name": "Celtics",
                },
                "updated_at": "2026-07-25T16:40:00Z",
            }
        ]
    )
    games = pd.DataFrame(
        [
            {
                "game_id": 100,
                "home_team_abbr": "LAL",
                "visitor_team_abbr": "BOS",
                "status": schedule_status,
                "date": scheduled_start,
                "datetime": scheduled_start,
                "game_status_bucket": "",
            }
        ]
    )
    quote = {
        "game_id": 100,
        "player_id": 246,
        "player_name": "LeBron James",
        "team_abbr": "LAL",
        "raw_market_name": "points",
        "raw_prop_type": "points",
        "raw_market_type": "over_under",
        "market_type": "player_points",
        "selection": "over",
        "line": line,
        "odds": odds,
        "vendor": vendor,
        "bookmaker": vendor,
        "line_source": "line_value",
        "updated_at": "2026-07-25T16:45:00Z",
    }
    odds_provider_rows = pd.DataFrame([quote])
    canonical_odds = pd.DataFrame([quote])
    if include_availability:
        injury_raw = pd.DataFrame(
            [
                {
                    "game_id": 100,
                    "player_id": 246,
                    "player_name": "LeBron James",
                    "team_id": 14,
                    "team_abbr": "LAL",
                    "status": availability,
                    "description": "ankle",
                    "updated_at": "2026-07-25T16:30:00Z",
                }
            ]
        )
        injuries = injury_raw.copy()
    else:
        injury_raw = pd.DataFrame()
        injuries = pd.DataFrame()
    return (
        games_raw,
        games,
        odds_provider_rows,
        canonical_odds,
        injury_raw,
        injuries,
    )


def _batch(context, **frame_options):
    (
        games_raw,
        games,
        odds_provider_rows,
        odds,
        injuries_raw,
        injuries,
    ) = _frames(**frame_options)
    return prepare_observation_batch(
        prediction_run_id=context.prediction_run_id,
        prediction_date=DATE,
        clock=context.clock,
        games_raw=games_raw,
        games=games,
        odds_provider_rows=odds_provider_rows,
        odds=odds,
        injuries_raw=injuries_raw,
        injuries=injuries,
        schedule_provider_name="balldontlie",
        market_provider_name="balldontlie",
        availability_provider_name="balldontlie_sdk",
    )


def _event_payload(event: EventEnvelope) -> dict:
    return json.loads(event.payload_json)


def _events_by_type(result, event_type: EventType) -> list[EventEnvelope]:
    return [
        event
        for event in read_segment_events(result.segment_directory)
        if event.event_type == event_type.value
    ]


def test_schedule_serialization_and_timestamp_distinctions_are_deterministic() -> None:
    source = _frames()[0].iloc[0].to_dict()
    normalized = _frames()[1].iloc[0].to_dict()
    first = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row=source,
        normalized_row=normalized,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    second = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row=source,
        normalized_row=normalized,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert first.payload == second.payload
    assert first.evidence.data == second.evidence.data
    assert first.observation_identity == second.observation_identity
    assert first.payload["provider_reported_at_utc"] != first.payload["ingested_at_utc"]


def test_identical_schedule_rows_deduplicate_within_source_batch(tmp_path: Path) -> None:
    context = _context(tmp_path)
    frames = list(_frames())
    frames[0] = pd.concat([frames[0], frames[0]], ignore_index=True)
    frames[1] = pd.concat([frames[1], frames[1]], ignore_index=True)
    batch = prepare_observation_batch(
        prediction_run_id=context.prediction_run_id,
        prediction_date=DATE,
        clock=context.clock,
        games_raw=frames[0],
        games=frames[1],
        odds_provider_rows=pd.DataFrame(),
        odds=pd.DataFrame(),
        injuries_raw=pd.DataFrame(),
        injuries=pd.DataFrame(),
        schedule_provider_name="balldontlie",
        market_provider_name="balldontlie",
        availability_provider_name="balldontlie_sdk",
    )
    assert batch.schedule_count == 1


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("datetime", "2026-07-25T23:30:00Z"),
        ("status", "delayed"),
    ],
)
def test_schedule_material_change_creates_new_observation_identity(
    field: str,
    new_value: str,
) -> None:
    source = _frames()[0].iloc[0].to_dict()
    normalized = _frames()[1].iloc[0].to_dict()
    first = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row=source,
        normalized_row=normalized,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    changed_source = dict(source)
    changed_normalized = dict(normalized)
    changed_source[field] = new_value
    changed_normalized[field] = new_value
    if field == "datetime":
        changed_source["date"] = new_value
        changed_normalized["date"] = new_value
    second = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row=changed_source,
        normalized_row=changed_normalized,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert first.observation_identity != second.observation_identity


def test_unknown_schedule_provider_timestamp_stays_null() -> None:
    source = _frames()[0].iloc[0].to_dict()
    source["updated_at"] = "2026-07-25 16:40:00"
    item = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row=source,
        normalized_row=_frames()[1].iloc[0].to_dict(),
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert item.payload["provider_reported_at_utc"] is None


def test_naive_ingestion_datetime_is_rejected() -> None:
    with pytest.raises(ObservationValidationError, match="timezone-aware"):
        prepare_schedule_observation(
            provider_name="balldontlie",
            operating_date=DATE,
            source_row={"id": 100},
            normalized_row={"game_id": 100},
            ingested_at_utc=datetime(2026, 7, 25, 17, 0),
            evidence_retention_level="SANITIZED_RAW",
        )


def test_unresolved_schedule_identity_and_doubleheader_are_not_invented() -> None:
    item = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row={"status": "scheduled"},
        normalized_row={"status": "scheduled"},
        ingested_at_utc=NOW,
        evidence_retention_level="NORMALIZED_ONLY",
    )
    assert item.payload["canonical_event_id"] is None
    assert item.payload["event_identity_resolution_status"] == "UNRESOLVED"
    assert item.payload["doubleheader_sequence"] is None


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("scheduled", "SCHEDULED"),
        ("delayed", "DELAYED"),
        ("1st Qtr", "IN_PROGRESS"),
        ("final", "FINAL"),
        ("postponed", "POSTPONED"),
        ("cancelled", "CANCELLED"),
        ("suspended", "SUSPENDED"),
        ("strange-provider-state", "UNKNOWN"),
    ],
)
def test_raw_and_normalized_schedule_status_are_separate(
    raw: str,
    normalized: str,
) -> None:
    assert normalize_schedule_status(raw) == normalized
    source = _frames()[0].iloc[0].to_dict()
    source["status"] = raw
    normalized_row = _frames()[1].iloc[0].to_dict()
    normalized_row["status"] = normalized
    observation = prepare_schedule_observation(
        provider_name="balldontlie",
        operating_date=DATE,
        source_row=source,
        normalized_row=normalized_row,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert observation.payload["game_status_raw"] == raw
    assert observation.payload["game_status_normalized"] == normalized


def test_identical_market_quotes_deduplicate_within_source_batch(tmp_path: Path) -> None:
    context = _context(tmp_path)
    frames = list(_frames())
    frames[2] = pd.concat([frames[2], frames[2]], ignore_index=True)
    frames[3] = pd.concat([frames[3], frames[3]], ignore_index=True)
    batch = prepare_observation_batch(
        prediction_run_id=context.prediction_run_id,
        prediction_date=DATE,
        clock=context.clock,
        games_raw=pd.DataFrame(),
        games=pd.DataFrame(),
        odds_provider_rows=frames[2],
        odds=frames[3],
        injuries_raw=pd.DataFrame(),
        injuries=pd.DataFrame(),
        schedule_provider_name="balldontlie",
        market_provider_name="balldontlie",
        availability_provider_name="balldontlie_sdk",
    )
    assert batch.market_count == 1


@pytest.mark.parametrize(
    ("field", "new_value"),
    [
        ("line", 25.5),
        ("odds", -105),
        ("vendor", "FanDuel"),
    ],
)
def test_market_material_change_creates_new_observation(
    field: str,
    new_value: object,
) -> None:
    quote = _frames()[2].iloc[0].to_dict()
    first = prepare_market_quote_observation(
        provider_name="balldontlie",
        source_row=quote,
        normalized_row=quote,
        ingested_at_utc=NOW,
        evidence_retention_level="NORMALIZED_ONLY",
    )
    changed = dict(quote)
    changed[field] = new_value
    if field == "vendor":
        changed["bookmaker"] = new_value
    second = prepare_market_quote_observation(
        provider_name="balldontlie",
        source_row=changed,
        normalized_row=changed,
        ingested_at_utc=NOW,
        evidence_retention_level="NORMALIZED_ONLY",
    )
    assert first.observation_identity != second.observation_identity


def test_missing_bookmaker_and_live_market_are_not_bookmaker_identity() -> None:
    quote = _frames()[2].iloc[0].to_dict()
    quote["vendor"] = ""
    quote["bookmaker"] = ""
    quote["line_source"] = "live_market"
    item = prepare_market_quote_observation(
        provider_name="balldontlie",
        source_row=quote,
        normalized_row=quote,
        ingested_at_utc=NOW,
        evidence_retention_level="NORMALIZED_ONLY",
    )
    assert item.payload["canonical_bookmaker_id"] is None
    assert item.payload["identity_resolution_status"] == "UNRESOLVED"
    assert item.payload["line_source"] == "live_market"


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("24.50", 24.5, 24.5),
        ("-0.0", 0, 0),
        ("-110.00", -110, -110),
    ],
)
def test_numeric_normalization_is_deterministic(
    left: object,
    right: object,
    expected: object,
) -> None:
    assert normalize_observation_numeric(left, field_name="value") == expected
    assert normalize_observation_numeric(right, field_name="value") == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_market_values_fail_closed(value: float) -> None:
    quote = _frames()[2].iloc[0].to_dict()
    quote["line"] = value
    with pytest.raises(ObservationValidationError, match="finite"):
        prepare_market_quote_observation(
            provider_name="balldontlie",
            source_row=quote,
            normalized_row=quote,
            ingested_at_utc=NOW,
            evidence_retention_level="NORMALIZED_ONLY",
        )


def test_quote_timestamp_and_ingestion_timestamp_remain_distinct() -> None:
    quote = _frames()[2].iloc[0].to_dict()
    item = prepare_market_quote_observation(
        provider_name="balldontlie",
        source_row=quote,
        normalized_row=quote,
        ingested_at_utc=NOW,
        evidence_retention_level="NORMALIZED_ONLY",
    )
    assert item.payload["market_observed_at_utc"] == datetime(
        2026, 7, 25, 16, 45, tzinfo=UTC
    )
    assert item.payload["ingested_at_utc"] == NOW


def test_raw_provider_market_identity_and_american_pricing_are_preserved() -> None:
    quote = _frames()[2].iloc[0].to_dict()
    item = prepare_market_quote_observation(
        provider_name="balldontlie",
        source_row=quote,
        normalized_row=quote,
        ingested_at_utc=NOW,
        evidence_retention_level="NORMALIZED_ONLY",
    )
    assert item.payload["market_raw"] == "points"
    assert item.payload["market_normalized"] == "player_points"
    assert item.payload["provider_market_key"] == "points"
    assert item.payload["odds_format"] == "AMERICAN"
    assert item.payload["implied_probability"] == implied_probability_from_american(-110)


def test_identical_availability_observations_deduplicate(tmp_path: Path) -> None:
    context = _context(tmp_path)
    frames = list(_frames())
    frames[4] = pd.concat([frames[4], frames[4]], ignore_index=True)
    frames[5] = pd.concat([frames[5], frames[5]], ignore_index=True)
    batch = prepare_observation_batch(
        prediction_run_id=context.prediction_run_id,
        prediction_date=DATE,
        clock=context.clock,
        games_raw=pd.DataFrame(),
        games=pd.DataFrame(),
        odds_provider_rows=pd.DataFrame(),
        odds=pd.DataFrame(),
        injuries_raw=frames[4],
        injuries=frames[5],
        schedule_provider_name="balldontlie",
        market_provider_name="balldontlie",
        availability_provider_name="balldontlie_sdk",
    )
    assert batch.availability_count == 1


def test_questionable_followed_by_out_creates_two_immutable_observations() -> None:
    row = _frames()[4].iloc[0].to_dict()
    questionable = {**row, "status": "QUESTIONABLE"}
    out = {**row, "status": "OUT"}
    first = prepare_player_availability_observation(
        provider_name="balldontlie_sdk",
        source_row=questionable,
        normalized_row=questionable,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    second = prepare_player_availability_observation(
        provider_name="balldontlie_sdk",
        source_row=out,
        normalized_row=out,
        ingested_at_utc=NOW + timedelta(minutes=80),
        evidence_retention_level="SANITIZED_RAW",
    )
    assert first.observation_identity != second.observation_identity
    assert first.payload["availability_status_normalized"] == "QUESTIONABLE"
    assert second.payload["availability_status_normalized"] == "OUT"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("active", "ACTIVE"),
        ("available", "AVAILABLE"),
        ("questionable", "QUESTIONABLE"),
        ("doubtful", "DOUBTFUL"),
        ("out", "OUT"),
        ("inactive", "INACTIVE"),
        ("starting", "STARTING"),
        ("not starting", "NOT_STARTING"),
        ("probable", "UNKNOWN"),
    ],
)
def test_raw_and_normalized_availability_are_separate(
    raw: str,
    normalized: str,
) -> None:
    assert normalize_availability_status(raw) == normalized
    source = _frames()[4].iloc[0].to_dict()
    source["status"] = raw
    normalized_row = _frames()[5].iloc[0].to_dict()
    normalized_row["status"] = normalized
    observation = prepare_player_availability_observation(
        provider_name="balldontlie_sdk",
        source_row=source,
        normalized_row=normalized_row,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert observation.payload["availability_status_raw"] == raw
    assert (
        observation.payload["availability_status_normalized"]
        == normalized
    )


@pytest.mark.parametrize("status", ["OUT", "NOT_STARTING"])
def test_availability_never_infers_settlement_void_or_dnp(status: str) -> None:
    row = {**_frames()[4].iloc[0].to_dict(), "status": status}
    item = prepare_player_availability_observation(
        provider_name="balldontlie_sdk",
        source_row=row,
        normalized_row=row,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert item.event_type == "PLAYER_AVAILABILITY_OBSERVED"
    assert "settlement" not in item.payload
    assert "void" not in item.payload
    assert item.payload["participation_status"] is None


def test_missing_availability_provider_timestamp_stays_null() -> None:
    row = _frames()[4].iloc[0].to_dict()
    row.pop("updated_at")
    item = prepare_player_availability_observation(
        provider_name="balldontlie_sdk",
        source_row=row,
        normalized_row=row,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert item.payload["provider_reported_at_utc"] is None


def test_conflicting_player_identity_fails_closed() -> None:
    row = _frames()[4].iloc[0].to_dict()
    row["player_identity_status"] = "CONFLICT"
    item = prepare_player_availability_observation(
        provider_name="balldontlie_sdk",
        source_row=row,
        normalized_row=row,
        ingested_at_utc=NOW,
        evidence_retention_level="SANITIZED_RAW",
    )
    assert item.payload["identity_resolution_status"] == "CONFLICT"
    assert item.payload["canonical_participant_id"] is None
    assert item.payload["canonical_team_id"] is None


def test_observation_enabled_publication_links_all_matching_evidence(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    before = board.read_bytes()
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=_batch(context),
    )
    assert result.status == "PASS"
    assert board.read_bytes() == before
    publication = _events_by_type(result, EventType.PREDICTION_PUBLISHED)[0]
    payload = _event_payload(publication)
    assert payload["payload_schema_version"] == 2
    links = payload["observation_links"]
    assert links["link_status"] == "COMPLETE"
    assert links["schedule_observation_event_id"]
    assert links["market_quote_observation_event_id"]
    assert len(links["availability_observation_event_ids"]) == 1


def test_zero_elite_board_still_commits_observations_without_fake_prediction(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path, row="")
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=_batch(context),
    )
    events = read_segment_events(result.segment_directory)
    assert not any(
        event.event_type == EventType.PREDICTION_PUBLISHED.value
        for event in events
    )
    assert sum(
        event.event_type in {
            EventType.SCHEDULE_OBSERVED.value,
            EventType.MARKET_QUOTE_OBSERVED.value,
            EventType.PLAYER_AVAILABILITY_OBSERVED.value,
        }
        for event in events
    ) == 3


def _changed_publication(
    original: EventEnvelope,
    payload: dict,
    *,
    observation_events: list[EventEnvelope],
) -> EventEnvelope:
    linked_ids = [
        payload["observation_links"].get("schedule_observation_event_id"),
        payload["observation_links"].get("market_quote_observation_event_id"),
        *payload["observation_links"].get(
            "availability_observation_event_ids", []
        ),
    ]
    linked = {
        event.event_id: event.event_hash
        for event in observation_events
        if event.event_id in linked_ids
    }
    source_hashes = dict(original.source_hashes)
    source_hashes["observation_event_hashes"] = linked
    return EventEnvelope.create(
        event_type=EventType.PREDICTION_PUBLISHED,
        payload=payload,
        payload_schema_version=2,
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
        idempotency_key="changed-publication",
        source_refs=original.source_refs,
        source_hashes=source_hashes,
    )


@pytest.mark.parametrize(
    ("link_name", "wrong_type", "expected_reason"),
    [
        (
            "schedule_observation_event_id",
            EventType.SCHEDULE_OBSERVED,
            "WRONG_SCHEDULE_EVENT_LINK",
        ),
        (
            "market_quote_observation_event_id",
            EventType.MARKET_QUOTE_OBSERVED,
            "WRONG_MARKET_PARTICIPANT_LINK",
        ),
        (
            "availability_observation_event_ids",
            EventType.PLAYER_AVAILABILITY_OBSERVED,
            "WRONG_AVAILABILITY_PARTICIPANT_LINK",
        ),
    ],
)
def test_wrong_observation_link_causes_reconciliation_fail(
    tmp_path: Path,
    link_name: str,
    wrong_type: EventType,
    expected_reason: str,
) -> None:
    board = _board(tmp_path)
    context = _context(tmp_path)
    batch = _batch(context)
    # Add a wrong identity observation of each type to the prepared batch.
    wrong_frames = list(_frames(line=30.5, odds=-105, availability="OUT"))
    wrong_frames[0].loc[0, "id"] = 200
    wrong_frames[1].loc[0, "game_id"] = 200
    wrong_frames[2].loc[0, "game_id"] = 200
    wrong_frames[2].loc[0, "player_id"] = 999
    wrong_frames[3] = wrong_frames[2].copy()
    wrong_frames[4].loc[0, "game_id"] = 200
    wrong_frames[4].loc[0, "player_id"] = 999
    wrong_frames[5] = wrong_frames[4].copy()
    wrong_batch = prepare_observation_batch(
        prediction_run_id=context.prediction_run_id,
        prediction_date=DATE,
        clock=context.clock,
        games_raw=wrong_frames[0],
        games=wrong_frames[1],
        odds_provider_rows=wrong_frames[2],
        odds=wrong_frames[3],
        injuries_raw=wrong_frames[4],
        injuries=wrong_frames[5],
        schedule_provider_name="balldontlie",
        market_provider_name="balldontlie",
        availability_provider_name="balldontlie_sdk",
    )
    from courtvision.lifecycle.observations import ObservationBatch

    combined = ObservationBatch(
        prediction_run_id=context.prediction_run_id,
        prepared_at_utc=NOW,
        observations=batch.observations + wrong_batch.observations,
        source_counts={},
    )
    result = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=combined,
    )
    all_events = list(read_segment_events(result.segment_directory))
    publication = next(
        event
        for event in all_events
        if event.event_type == EventType.PREDICTION_PUBLISHED.value
    )
    observation_events = [
        event for event in all_events if event.event_type in {
            EventType.SCHEDULE_OBSERVED.value,
            EventType.MARKET_QUOTE_OBSERVED.value,
            EventType.PLAYER_AVAILABILITY_OBSERVED.value,
        }
    ]
    wrong_event = next(
        event
        for event in observation_events
        if event.event_type == wrong_type.value
        and (
            _event_payload(event).get("provider_event_id") == 200
            or _event_payload(event).get("provider_participant_id") == 999
        )
    )
    payload = _event_payload(publication)
    if link_name == "availability_observation_event_ids":
        payload["observation_links"][link_name] = [wrong_event.event_id]
    else:
        payload["observation_links"][link_name] = wrong_event.event_id
    changed = _changed_publication(
        publication,
        payload,
        observation_events=observation_events,
    )
    report = reconcile_board_with_events(
        prediction_run_id=context.prediction_run_id,
        operating_date=DATE,
        board_path=board,
        board_path_reference=f"outputs/runtime/operator/elite_board_{DATE}.csv",
        events=tuple(observation_events + [changed]),
        clock=FixedClock(NOW),
    )
    assert report.status == "FAIL"
    assert any(
        finding.get("reason") == expected_reason
        for finding in report.mismatches
    )


@pytest.mark.parametrize(
    ("line", "odds", "provider_timestamp", "expected_reason"),
    [
        (25.5, -110, None, "MARKET_LINE_MISMATCH"),
        (24.5, -105, None, "MARKET_ODDS_MISMATCH"),
        (
            24.5,
            -110,
            "2026-07-25T16:44:00Z",
            "MARKET_PROVIDER_TIMESTAMP_MISMATCH",
        ),
    ],
)
def test_wrong_line_odds_or_provider_timestamp_observation_causes_fail(
    tmp_path: Path,
    line: float,
    odds: int,
    provider_timestamp: str | None,
    expected_reason: str,
) -> None:
    board = _board(tmp_path)
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=_batch(context),
    )
    events = list(read_segment_events(result.segment_directory))
    publication = next(
        event for event in events if event.event_type == "PREDICTION_PUBLISHED"
    )
    market = next(
        event for event in events if event.event_type == "MARKET_QUOTE_OBSERVED"
    )
    market_payload = _event_payload(market)
    market_payload["line"] = line
    market_payload["odds"] = odds
    if provider_timestamp is not None:
        market_payload["provider_reported_at_utc"] = provider_timestamp
        market_payload["market_observed_at_utc"] = provider_timestamp
    changed_market = EventEnvelope.create(
        event_type=EventType.MARKET_QUOTE_OBSERVED,
        payload=market_payload,
        payload_schema_version=1,
        prediction_run_id=market.prediction_run_id,
        prediction_key=market.prediction_key,
        market_subject_key=market.market_subject_key,
        event_sequence=98,
        occurred_at_utc=market.occurred_at_utc,
        recorded_at_utc=market.recorded_at_utc,
        provider_reported_at_utc=market.provider_reported_at_utc,
        operating_date=DATE,
        operating_timezone="America/Toronto",
        actor_type="SYSTEM",
        actor_id="test",
        correlation_id=market.prediction_run_id,
        idempotency_key="changed-market",
        source_refs=market.source_refs,
        source_hashes=market.source_hashes,
    )
    pub_payload = _event_payload(publication)
    pub_payload["observation_links"][
        "market_quote_observation_event_id"
    ] = changed_market.event_id
    observations = [
        event for event in events if event.event_type in {
            "SCHEDULE_OBSERVED",
            "PLAYER_AVAILABILITY_OBSERVED",
        }
    ] + [changed_market]
    changed_publication = _changed_publication(
        publication,
        pub_payload,
        observation_events=observations,
    )
    report = reconcile_board_with_events(
        prediction_run_id=context.prediction_run_id,
        operating_date=DATE,
        board_path=board,
        board_path_reference=f"outputs/runtime/operator/elite_board_{DATE}.csv",
        events=tuple(observations + [changed_publication]),
        clock=FixedClock(NOW),
    )
    assert report.status == "FAIL"
    assert any(
        finding.get("reason") == expected_reason
        for finding in report.mismatches
    )


def test_missing_optional_availability_is_degraded_not_fabricated_pass(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=_batch(context, include_availability=False),
    )
    assert result.status == "DEGRADED"
    publication = _events_by_type(result, EventType.PREDICTION_PUBLISHED)[0]
    links = _event_payload(publication)["observation_links"]
    assert links["availability_observation_event_ids"] == []
    assert links["link_status"] == "DEGRADED"


def test_publication_payload_v1_remains_the_phase2_default(tmp_path: Path) -> None:
    result = publish_shadow_after_board(
        _context(tmp_path),
        board_path=_board(tmp_path),
    )
    publication = _events_by_type(result, EventType.PREDICTION_PUBLISHED)[0]
    payload = _event_payload(publication)
    assert publication.payload_schema_version == 1
    assert payload["payload_schema_version"] == 1
    assert "observation_links" not in payload


def test_phase3_schema_files_are_versioned_and_v1_schema_is_unchanged() -> None:
    schemas = Path("courtvision") / "lifecycle" / "schemas"
    v1 = json.loads(
        (schemas / "prediction_published_payload_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert v1["properties"]["payload_schema_version"]["const"] == 1
    assert "observation_links" not in v1["properties"]
    for name in (
        "schedule_observed_payload_v1.json",
        "market_quote_observed_payload_v1.json",
        "player_availability_observed_payload_v1.json",
    ):
        schema = json.loads((schemas / name).read_text(encoding="utf-8"))
        assert schema["properties"]["payload_schema_version"]["const"] == 1
        assert "source_payload_sha256" in schema["required"]
        assert "ingested_at_utc" in schema["required"]
    v2 = json.loads(
        (schemas / "prediction_published_payload_v2.json").read_text(
            encoding="utf-8"
        )
    )
    assert v2["properties"]["payload_schema_version"]["const"] == 2
    assert "observation_links" in v2["required"]


def test_observation_capture_failure_commits_degraded_v2_without_board_change(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    before = board.read_bytes()
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=None,
        observation_capture_error="fixture capture failed",
    )
    assert result.status == "DEGRADED"
    assert result.commit_status == "COMMITTED"
    assert board.read_bytes() == before
    publication = _events_by_type(result, EventType.PREDICTION_PUBLISHED)[0]
    assert _event_payload(publication)["observation_links"]["link_status"] == "DEGRADED"


def test_dual_feature_flag_semantics_and_import_boundary(monkeypatch) -> None:
    assert not lifecycle_observations_enabled({})
    assert lifecycle_observations_enabled(
        {"COURTVISION_LIFECYCLE_OBSERVATIONS": "1"}
    )
    assert (
        load_shadow_lifecycle_hooks(
            {
                "COURTVISION_LIFECYCLE_SHADOW": "0",
                "COURTVISION_LIFECYCLE_OBSERVATIONS": "1",
            }
        )
        is None
    )
    phase2 = load_shadow_lifecycle_hooks(
        {
            "COURTVISION_LIFECYCLE_SHADOW": "1",
            "COURTVISION_LIFECYCLE_OBSERVATIONS": "0",
        }
    )
    assert phase2 is not None
    assert not phase2.observations_enabled
    assert phase2.prepare_observation_batch is None
    phase3 = load_shadow_lifecycle_hooks(
        {
            "COURTVISION_LIFECYCLE_SHADOW": "1",
            "COURTVISION_LIFECYCLE_OBSERVATIONS": "1",
        }
    )
    assert phase3 is not None
    assert phase3.observations_enabled
    assert callable(phase3.prepare_observation_batch)


def test_observation_batch_uses_existing_writer_and_exact_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    context = _context(tmp_path)
    batch = _batch(context)
    first = publish_shadow_after_board(
        context,
        board_path=board,
        observations_enabled=True,
        observation_batch=batch,
    )
    assert first.commit_status == "COMMITTED"
    # Re-create the exact deterministic segment through the lower-level writer.
    segment_events = read_segment_events(first.segment_directory)
    from courtvision.lifecycle.evidence import PreparedEvidenceObject

    evidence = []
    manifest = json.loads(
        (first.segment_directory / "manifest.json").read_text(encoding="utf-8")
    )
    for digest in manifest["evidence_hashes"]:
        path = (
            context.lifecycle_root
            / "evidence"
            / "objects"
            / digest[:2]
            / f"{digest}.json"
        )
        evidence.append(PreparedEvidenceObject("retry", digest, path.read_bytes()))
    retry = LifecycleWriter(
        context.lifecycle_root, clock=context.clock
    ).commit_segment(
        context.run_manifest,
        segment_events,
        evidence_objects=tuple(evidence),
    )
    assert retry.status == "ALREADY_COMMITTED"


def test_tampered_observation_event_and_evidence_are_detected(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=_board(tmp_path),
        observations_enabled=True,
        observation_batch=_batch(context),
    )
    copied = tmp_path / "tampered-segment"
    import shutil

    shutil.copytree(result.segment_directory, copied)
    events_path = copied / "events.jsonl"
    data = events_path.read_bytes()
    events_path.write_bytes(data.replace(b"scheduled", b"scheduleD", 1))
    assert not verify_segment(
        copied, lifecycle_root=context.lifecycle_root
    ).ok

    market = _events_by_type(result, EventType.MARKET_QUOTE_OBSERVED)[0]
    digest = market.source_hashes["source_evidence_sha256"]
    evidence_path = (
        context.lifecycle_root
        / "evidence"
        / "objects"
        / digest[:2]
        / f"{digest}.json"
    )
    evidence_path.write_bytes(evidence_path.read_bytes() + b"\n")
    assert not verify_segment(
        result.segment_directory,
        lifecycle_root=context.lifecycle_root,
    ).ok


def test_source_evidence_sanitizes_all_required_secret_key_forms(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    frames = list(_frames())
    for name in (
        "authorization",
        "api_key",
        "apikey",
        "token",
        "access_token",
        "secret",
        "password",
        "cookie",
        "session",
    ):
        frames[0].loc[0, name] = f"value-{name}"
    batch = prepare_observation_batch(
        prediction_run_id=context.prediction_run_id,
        prediction_date=DATE,
        clock=context.clock,
        games_raw=frames[0],
        games=frames[1],
        odds_provider_rows=pd.DataFrame(),
        odds=pd.DataFrame(),
        injuries_raw=pd.DataFrame(),
        injuries=pd.DataFrame(),
        schedule_provider_name="balldontlie",
        market_provider_name="balldontlie",
        availability_provider_name="balldontlie_sdk",
    )
    evidence = batch.observations[0].evidence.data
    assert b"[REDACTED]" in evidence
    assert b"value-authorization" not in evidence
    assert b"value-session" not in evidence


def test_phase3_commit_does_not_rewrite_existing_phase2_segment(
    tmp_path: Path,
) -> None:
    board = _board(tmp_path)
    phase2_context = _context(tmp_path)
    phase2 = publish_shadow_after_board(phase2_context, board_path=board)
    before = {
        path.relative_to(phase2.segment_directory): path.read_bytes()
        for path in phase2.segment_directory.rglob("*")
        if path.is_file()
    }
    board.unlink()
    _board(tmp_path)
    phase3_context = _context(tmp_path, now=NOW + timedelta(seconds=1))
    publish_shadow_after_board(
        phase3_context,
        board_path=board,
        observations_enabled=True,
        observation_batch=_batch(phase3_context),
    )
    after = {
        path.relative_to(phase2.segment_directory): path.read_bytes()
        for path in phase2.segment_directory.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_inspection_apis_list_run_prediction_and_histories(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=_board(tmp_path),
        observations_enabled=True,
        observation_batch=_batch(context),
    )
    observations = list_observations_for_run(
        context.lifecycle_root, context.prediction_run_id
    )
    assert len(observations) == 3
    publication = _events_by_type(result, EventType.PREDICTION_PUBLISHED)[0]
    linked = list_observations_for_prediction(
        context.lifecycle_root, publication.prediction_id
    )
    assert {event.event_type for event in linked} == {
        "SCHEDULE_OBSERVED",
        "MARKET_QUOTE_OBSERVED",
        "PLAYER_AVAILABILITY_OBSERVED",
    }
    assert len(
        show_schedule_history_for_event(
            context.lifecycle_root, "courtvision:basketball:nba:event:100"
        )
    ) == 1
    assert len(
        show_quote_history_for_prediction_key(
            context.lifecycle_root, publication.prediction_key
        )
    ) == 1
    assert len(
        show_availability_history_for_player_event(
            context.lifecycle_root,
            "courtvision:basketball:nba:participant:246",
            "courtvision:basketball:nba:event:100",
        )
    ) == 1
    assert verify_observation_segment(
        context.lifecycle_root, result.segment_directory
    ).ok


def test_intraday_quotes_remain_separately_queryable(tmp_path: Path) -> None:
    context = _context(tmp_path)
    first = _batch(context)
    second = _batch(context, odds=-105)
    from courtvision.lifecycle.observations import ObservationBatch

    combined = ObservationBatch(
        prediction_run_id=context.prediction_run_id,
        prepared_at_utc=NOW,
        observations=first.observations + tuple(
            item
            for item in second.observations
            if item.event_type == "MARKET_QUOTE_OBSERVED"
        ),
        source_counts={},
    )
    result = publish_shadow_after_board(
        context,
        board_path=_board(tmp_path),
        observations_enabled=True,
        observation_batch=combined,
    )
    quotes = _events_by_type(result, EventType.MARKET_QUOTE_OBSERVED)
    assert len(quotes) == 2
    assert {normalize_observation_numeric(_event_payload(item)["odds"], field_name="odds") for item in quotes} == {-110, -105}


def test_observation_envelopes_carry_all_version_hash_time_and_run_fields(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    result = publish_shadow_after_board(
        context,
        board_path=_board(tmp_path),
        observations_enabled=True,
        observation_batch=_batch(context),
    )
    for event in read_segment_events(result.segment_directory):
        if event.event_type not in {
            "SCHEDULE_OBSERVED",
            "MARKET_QUOTE_OBSERVED",
            "PLAYER_AVAILABILITY_OBSERVED",
        }:
            continue
        assert event.event_schema_version == 1
        assert event.payload_schema_version == 1
        assert event.identity_schema_version == 1
        assert event.canonicalization_version == "canonical_json_v1"
        assert event.prediction_run_id == context.prediction_run_id
        assert event.correlation_id == context.prediction_run_id
        assert event.payload_sha256
        assert event.event_hash
        assert event.recorded_at_utc.tzinfo is not None
        assert event.source_refs["source_payload"].startswith(
            "evidence://sha256/"
        )


class _ParityLogger:
    def error(self, *args, **kwargs) -> None:
        pass

    def exception(self, *args, **kwargs) -> None:
        pass


def _offline_main(monkeypatch, runtime_type) -> None:
    monkeypatch.setattr(courtvision_ai, "_load_env_file", lambda: None)
    monkeypatch.setattr(
        courtvision_ai,
        "resolve_api_key",
        lambda **kwargs: (
            "test-key",
            {
                "env_var_name": "BALLDONTLIE_API_KEY",
                "source": "test",
                "masked_preview": "tes***",
            },
        ),
    )
    monkeypatch.setattr(
        courtvision_ai,
        "smoke_test_games_api",
        lambda *args, **kwargs: {
            "status_code": 200,
            "resolved_url": "fixture://games",
            "has_auth": True,
            "masked_key_preview": "tes***",
            "body_snippet": "fixture",
        },
    )
    monkeypatch.setattr(courtvision_ai, "CourtVisionAI", runtime_type)


def test_runtime_observation_plumbing_preserves_canonical_board_bytes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    board = tmp_path / "runtime" / "operator" / f"elite_board_{DATE}.csv"
    canonical_bytes = b"player_name,line,odds\nLeBron James,24.5,-110\n"
    state: dict[str, object] = {}

    class Runtime:
        def __init__(self, out_dir: str = "outputs") -> None:
            self.out_dir = Path(out_dir)
            self.logger = _ParityLogger()

        def predict(self, prediction_date: str):
            observer = getattr(self, "_shadow_lifecycle_observer")
            observer(
                prediction_date=prediction_date,
                games_raw=pd.DataFrame(),
                games=pd.DataFrame(),
                odds_provider_rows=pd.DataFrame(),
                odds=pd.DataFrame(),
                injuries_raw=pd.DataFrame(),
                injuries=pd.DataFrame(),
                schedule_provider_name="fixture",
                market_provider_name="fixture",
                availability_provider_name="fixture",
            )
            return {"summary": {}, "elite_props": pd.DataFrame()}

    _offline_main(monkeypatch, Runtime)
    context = SimpleNamespace(
        prediction_run_id="run-observation-parity",
        clock=FixedClock(NOW),
        terminal=False,
    )

    def write_outputs(**kwargs):
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(canonical_bytes)
        return {"elite_board": board}

    def publish(run, **kwargs):
        state.update(kwargs)
        run.terminal = True
        return SimpleNamespace(
            status="PASS",
            prediction_run_id=run.prediction_run_id,
            commit_status="COMMITTED",
            message="shadow publication reconciled",
        )

    monkeypatch.setattr(
        courtvision_ai,
        "load_shadow_lifecycle_hooks",
        lambda: SimpleNamespace(
            begin_shadow_run=lambda *args, **kwargs: context,
            publish_shadow_after_board=publish,
            record_failed_shadow_run=lambda *args, **kwargs: None,
            observations_enabled=True,
            prepare_observation_batch=lambda **kwargs: SimpleNamespace(
                prediction_run_id=context.prediction_run_id,
                observations=(),
                capture_errors=(),
            ),
            observation_initialization_error=None,
        ),
    )
    monkeypatch.setattr(courtvision_ai, "_write_cli_outputs", write_outputs)
    rc = courtvision_ai.main(
        [
            "--prediction-date",
            DATE,
            "--predict-only",
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    assert board.read_bytes() == canonical_bytes
    assert state["observations_enabled"] is True
    assert state["observation_batch"] is not None


def test_runtime_observation_capture_failure_preserves_canonical_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    board = tmp_path / "runtime" / "operator" / f"elite_board_{DATE}.csv"

    class Runtime:
        def __init__(self, out_dir: str = "outputs") -> None:
            self.out_dir = Path(out_dir)
            self.logger = _ParityLogger()

        def predict(self, prediction_date: str):
            getattr(self, "_shadow_lifecycle_observer")(
                prediction_date=prediction_date
            )
            return {"summary": {}, "elite_props": pd.DataFrame()}

    _offline_main(monkeypatch, Runtime)
    context = SimpleNamespace(
        prediction_run_id="run-observation-failure",
        clock=FixedClock(NOW),
        terminal=False,
    )
    publish_kwargs: dict[str, object] = {}

    def write_outputs(**kwargs):
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_text("player_name\n", encoding="utf-8")
        return {"elite_board": board}

    def publish(run, **kwargs):
        publish_kwargs.update(kwargs)
        run.terminal = True
        return SimpleNamespace(
            status="DEGRADED",
            prediction_run_id=run.prediction_run_id,
            commit_status="COMMITTED",
            message="observation capture degraded",
        )

    monkeypatch.setattr(
        courtvision_ai,
        "load_shadow_lifecycle_hooks",
        lambda: SimpleNamespace(
            begin_shadow_run=lambda *args, **kwargs: context,
            publish_shadow_after_board=publish,
            record_failed_shadow_run=lambda *args, **kwargs: None,
            observations_enabled=True,
            prepare_observation_batch=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("fixture capture failure")
            ),
            observation_initialization_error=None,
        ),
    )
    monkeypatch.setattr(courtvision_ai, "_write_cli_outputs", write_outputs)
    assert (
        courtvision_ai.main(
            [
                "--prediction-date",
                DATE,
                "--predict-only",
                "--out-dir",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert board.read_text(encoding="utf-8") == "player_name\n"
    assert publish_kwargs["observation_batch"] is None
    assert "fixture capture failure" in str(
        publish_kwargs["observation_capture_error"]
    )
