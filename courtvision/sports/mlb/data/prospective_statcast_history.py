"""Immutable prospective acquisition of cutoff-safe historical Statcast evidence.

This module is research-only.  It captures bounded league-wide Savant date
chunks for an operating date, retains only rows relevant to the declared
hitter/probable-pitcher universe, and binds every included game to trustworthy
pre-cutoff completion evidence from MLB schedule observations or, when needed,
MLB play-by-play.  It does not train, score, or publish models.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Final, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from courtvision.sports.mlb.data.prospective_context_acquisition import (
    DEFAULT_ACQUISITION_ROOT,
    EvidenceProvider,
    EvidenceRequest,
    ImmutableCaptureConflictError,
    ProspectiveAcquisitionError,
    ProviderResponse,
    parse_utc,
    utc_text,
)


HISTORICAL_STATCAST_SCHEMA_VERSION: Final = (
    "mlb-hr-prospective-historical-statcast-v2"
)
STATCAST_CHUNK_MANIFEST_SCHEMA_VERSION: Final = "mlb-hr-statcast-chunk-manifest-v1"
REQUEST_ACCOUNTING_SCHEMA_VERSION: Final = "mlb-hr-provider-request-accounting-v1"
DEFAULT_INITIAL_CHUNK_DAYS: Final = 7
DEFAULT_SUSPICIOUS_CHUNK_ROW_COUNT: Final = 25_000
DEFAULT_HISTORICAL_STATCAST_ROOT: Final = (
    DEFAULT_ACQUISITION_ROOT / "historical_statcast"
)

_RAW_REQUIRED_COLUMNS: Final = frozenset(
    {
        "game_pk",
        "game_date",
        "at_bat_number",
        "pitch_number",
        "batter",
        "pitcher",
        "stand",
        "p_throws",
        "home_team",
        "away_team",
        "inning",
        "inning_topbot",
        "events",
        "description",
        "pitch_type",
        "release_speed",
        "launch_speed",
        "launch_angle",
        "bb_type",
    }
)
_STATCAST_KNOWN_GAME_TYPES: Final = frozenset(
    {"A", "C", "D", "E", "F", "I", "L", "P", "R", "S", "W"}
)
_STATCAST_ADMITTED_GAME_TYPES: Final = frozenset({"R"})
_HISTORICAL_GAME_UNIVERSE: Final = "regular_season"
_STATCAST_ADMISSION_SCHEMA_VERSION: Final = "mlb-hr-statcast-admission-v1"


_GAME_CLOCK_COLUMNS: Final = (
    "game_id",
    "game_completed_at_utc",
    "completion_evidence_type",
    "completion_witnessed_at_utc",
    "provider_published_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
    "provider_final_status",
    "completion_source_request_id",
)
_TERMINAL_STATES: Final = frozenset({"completed", "partial", "unavailable", "rejected"})
_FINAL_DETAILED_STATES: Final = frozenset(
    {"completed early", "final", "game over"}
)
_AMBIGUOUS_DETAILED_STATES: Final = frozenset(
    {
        "delayed",
        "in progress",
        "manager challenge",
        "postponed",
        "scheduled",
        "suspended",
        "warmup",
    }
)
_DATE_RANGE_REQUEST_ID: Final = re.compile(
    r"^baseball-savant-statcast-(\d{4}-\d{2}-\d{2})-(\d{4}-\d{2}-\d{2})$"
)


@dataclass(frozen=True, slots=True)
class HistoricalStatcastResult:
    snapshot_id: str
    snapshot_dir: Path
    manifest_path: Path
    snapshot_state: str
    manifest_digest: str
    no_op: bool
    provider_call_count: int
    game_count: int
    pitch_count: int
    plate_appearance_count: int
    reused_chunk_count: int
    recovered_chunk_count: int
    completion_witness_counts_by_source_type: Mapping[str, int]

    def reference(self) -> dict[str, object]:
        return {
            "snapshot_id": self.snapshot_id,
            "manifest_path": str(self.manifest_path),
            "manifest_digest": self.manifest_digest,
            "snapshot_state": self.snapshot_state,
        }


@dataclass(frozen=True, slots=True)
class LoadedHistoricalStatcast:
    manifest_path: Path
    manifest: Mapping[str, object]
    statcast_csv_path: Path
    game_clock_csv_path: Path
    raw_inputs: Mapping[str, Path]


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
        indent=2 if pretty else None,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _value_digest(value: object) -> str:
    return _sha256(_canonical_json(value))


def _mlbam_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text.isdigit() or len(text) < 6 or int(text) <= 0:
        raise ProspectiveAcquisitionError(f"{field_name} identity mismatch")
    return text


def _write_raw_file(path: Path, payload: bytes) -> None:
    """Single injectable write boundary for response-persistence failure tests."""

    path.write_bytes(payload)


def _write_csv(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, object]],
) -> None:
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def _request_execution(provider: EvidenceProvider, request: EvidenceRequest) -> str:
    resolver = getattr(provider, "request_execution", None)
    execution = resolver(request) if callable(resolver) else "provider_call"
    if execution not in {
        "provider_call",
        "reused_prior_response",
        "replayed_immutable_response",
    }:
        raise ProspectiveAcquisitionError(
            "provider returned invalid request execution provenance"
        )
    return str(execution)


def _request_accounting_times(
    provider: EvidenceProvider,
    request: EvidenceRequest,
    *,
    started: datetime,
    completed: datetime,
) -> tuple[datetime, datetime]:
    resolver = getattr(
        provider,
        "request_accounting_times",
        None,
    )

    if not callable(resolver):
        return started, completed

    values = resolver(request)

    if (
        not isinstance(values, tuple)
        or len(values) != 2
    ):
        raise ProspectiveAcquisitionError(
            "provider returned invalid request accounting clocks"
        )

    replay_started = parse_utc(
        values[0],
        "replayed request_started_at_utc",
    )

    replay_completed = parse_utc(
        values[1],
        "replayed request_completed_at_utc",
    )

    if replay_completed < replay_started:
        raise ProspectiveAcquisitionError(
            "replayed request accounting clocks are inverted"
        )

    return replay_started, replay_completed


def plan_statcast_date_chunks(
    *, start_date: date, end_date: date, initial_chunk_days: int
) -> tuple[tuple[date, date], ...]:
    """Return deterministic inclusive date chunks covering the requested range."""

    if (
        isinstance(initial_chunk_days, bool)
        or not isinstance(initial_chunk_days, int)
        or initial_chunk_days <= 0
    ):
        raise ProspectiveAcquisitionError("initial_chunk_days must be a positive integer")
    if end_date < start_date:
        raise ProspectiveAcquisitionError("Statcast chunk end date precedes start date")
    chunks: list[tuple[date, date]] = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(end_date, cursor + timedelta(days=initial_chunk_days - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return tuple(chunks)


def _statcast_request(start_date: date, end_date: date) -> EvidenceRequest:
    return EvidenceRequest(
        request_id=f"baseball-savant-statcast-{start_date}-{end_date}",
        evidence_class="stable_history",
        source_name="statcast_pitch_history",
        provider="baseball_savant",
        url=(
            "https://baseballsavant.mlb.com/statcast_search/csv?all=true"
            "&type=details&player_type=batter"
            f"&game_date_gt={start_date.isoformat()}"
            f"&game_date_lt={end_date.isoformat()}"
        ),
    )


def build_historical_statcast_requests(
    *,
    operating_date: date,
    season_start_date: date,
    initial_chunk_days: int = DEFAULT_INITIAL_CHUNK_DAYS,
) -> tuple[EvidenceRequest, ...]:
    if season_start_date >= operating_date:
        raise ProspectiveAcquisitionError("season_start_date must precede operating_date")
    end_date = operating_date - timedelta(days=1)
    schedule = EvidenceRequest(
            request_id=f"statsapi-completed-games-{season_start_date}-{end_date}",
            evidence_class="stable_history",
            source_name="mlb_completed_game_schedule",
            provider="mlb_statsapi",
            url=(
                "https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameTypes=R"
                f"&startDate={season_start_date.isoformat()}"
                f"&endDate={end_date.isoformat()}"
            ),
        )
    return (
        schedule,
        *(
            _statcast_request(chunk_start, chunk_end)
            for chunk_start, chunk_end in plan_statcast_date_chunks(
                start_date=season_start_date,
                end_date=end_date,
                initial_chunk_days=initial_chunk_days,
            )
        ),
    )


def _statcast_request_bounds(request: EvidenceRequest) -> tuple[date, date]:
    query = parse_qs(urlparse(request.url).query)
    raw_start = (query.get("game_date_gt") or [None])[0]
    raw_end = (query.get("game_date_lt") or [None])[0]
    if raw_start is None or raw_end is None:
        match = _DATE_RANGE_REQUEST_ID.fullmatch(request.request_id)
        if match is not None:
            raw_start, raw_end = match.groups()
    try:
        start_date = date.fromisoformat(str(raw_start))
        end_date = date.fromisoformat(str(raw_end))
    except (TypeError, ValueError) as exc:
        raise ProspectiveAcquisitionError(
            f"Statcast request {request.request_id} lacks deterministic date bounds"
        ) from exc
    if end_date < start_date:
        raise ProspectiveAcquisitionError(
            f"Statcast request {request.request_id} has inverted date bounds"
        )
    return start_date, end_date


def _request_record(
    temporary: Path,
    request: EvidenceRequest,
    provider: EvidenceProvider,
    *,
    requested_as_of: datetime,
) -> tuple[dict[str, object], ProviderResponse | None]:
    started = datetime.now(timezone.utc)
    try:
        response = provider.fetch(request)
    except Exception as exc:
        completed = datetime.now(timezone.utc)
        return (
            {
                **request.identity_payload(),
                "endpoint_class": request.source_name,
                "request_started_at_utc": utc_text(started),
                "request_completed_at_utc": utc_text(completed),
                "request_execution": _request_execution(provider, request),
                "request_result": "provider_error",
                "availability_status": "unavailable",
                "availability_note": f"provider failure: {type(exc).__name__}",
                "error_type": type(exc).__name__,
                "status_code": None,
                "provider_published_at_utc": None,
                "first_observed_at_utc": None,
                "captured_at_utc": None,
                "requested_as_of_utc": utc_text(requested_as_of),
                "sha256": None,
                "byte_size": None,
                "body_path": None,
                "metadata_path": None,
                "raw_persistence_status": "not_available",
            },
            None,
        )

    execution = _request_execution(provider, request)
    completed = datetime.now(timezone.utc)

    if execution == "replayed_immutable_response":
        started, completed = _request_accounting_times(
            provider,
            request,
            started=started,
            completed=completed,
        )

    first_observed = parse_utc(response.first_observed_at_utc, "first_observed_at_utc")
    captured = parse_utc(response.captured_at_utc, "captured_at_utc")
    published = (
        parse_utc(response.provider_published_at_utc, "provider_published_at_utc")
        if response.provider_published_at_utc is not None
        else None
    )
    availability = published or first_observed
    state = "completed"
    note: str | None = None
    if response.status_code == 206:
        state, note = "partial", "provider returned HTTP 206 partial content"
    elif response.status_code < 200 or response.status_code >= 300:
        state, note = "unavailable", f"provider returned HTTP {response.status_code}"
    elif not (availability <= first_observed <= captured <= requested_as_of):
        state, note = "rejected", "post-cutoff or inconsistent observation clocks"

    digest = _sha256(response.body)
    raw_dir = temporary / "raw"
    body_path = raw_dir / f"{digest[:24]}.body"
    request_digest = _value_digest(request.identity_payload())
    metadata_path = raw_dir / f"{digest[:16]}-{request_digest[:8]}.json"
    persistence_status = "completed"
    persistence_error: str | None = None
    try:
        raw_dir.mkdir(parents=True, exist_ok=True)
        if body_path.exists() and body_path.read_bytes() != response.body:
            raise ImmutableCaptureConflictError("raw digest prefix collision")
        if not body_path.exists():
            _write_raw_file(body_path, response.body)
        metadata = {
            "schema_version": REQUEST_ACCOUNTING_SCHEMA_VERSION,
            "request": request.identity_payload(),
            "status_code": response.status_code,
            "headers": dict(sorted(response.headers.items())),
            "provider_published_at_utc": utc_text(published) if published else None,
            "first_observed_at_utc": utc_text(first_observed),
            "captured_at_utc": utc_text(captured),
            "requested_as_of_utc": utc_text(requested_as_of),
            "availability_status": state,
            "availability_note": note,
            "sha256": digest,
            "byte_size": len(response.body),
        }
        metadata_payload = _canonical_json(metadata, pretty=True)
        if metadata_path.exists() and metadata_path.read_bytes() != metadata_payload:
            raise ImmutableCaptureConflictError("raw metadata digest prefix collision")
        if not metadata_path.exists():
            _write_raw_file(metadata_path, metadata_payload)
    except OSError as exc:
        persistence_status = "failed"
        persistence_error = type(exc).__name__
        state = "unavailable"
        note = f"response-body persistence failure: {type(exc).__name__}"

    return (
        {
            **request.identity_payload(),
            "endpoint_class": request.source_name,
            "request_started_at_utc": utc_text(started),
            "request_completed_at_utc": utc_text(completed),
            "request_execution": execution,
            "request_result": (
                "response_preserved"
                if persistence_status == "completed"
                else "response_persistence_failed"
            ),
            "availability_status": state,
            "availability_note": note,
            "error_type": persistence_error,
            "status_code": response.status_code,
            "provider_published_at_utc": utc_text(published) if published else None,
            "first_observed_at_utc": utc_text(first_observed),
            "captured_at_utc": utc_text(captured),
            "requested_as_of_utc": utc_text(requested_as_of),
            "sha256": digest,
            "byte_size": len(response.body),
            "body_path": (
                body_path.relative_to(temporary).as_posix()
                if body_path.is_file()
                else None
            ),
            "metadata_path": (
                metadata_path.relative_to(temporary).as_posix()
                if metadata_path.is_file()
                else None
            ),
            "metadata_sha256": (
                _sha256(metadata_path.read_bytes()) if metadata_path.is_file() else None
            ),
            "raw_persistence_status": persistence_status,
        },
        response,
    )


def _positive_provider_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ProspectiveAcquisitionError(f"{field_name} identity is missing")
    return text


def _nested_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProspectiveAcquisitionError(f"{field_name} is missing")
    return value


def _schedule_query_context(request: EvidenceRequest) -> dict[str, str | None]:
    query = parse_qs(urlparse(request.url).query)
    game_types = (query.get("gameTypes") or [None])[0]
    return {
        "requested_sport_id": (query.get("sportId") or [None])[0],
        "requested_league_id": (query.get("leagueId") or [None])[0],
        "requested_game_types": str(game_types) if game_types else None,
    }


def _is_final_schedule_state(state: Mapping[str, object]) -> bool:
    detailed = str(state.get("detailed_state") or "").strip().casefold()
    abstract = str(state.get("abstract_state") or "").strip().casefold()
    coded = str(state.get("coded_state") or "").strip().casefold()
    status_code = str(state.get("status_code") or "").strip().casefold()
    if detailed in _AMBIGUOUS_DETAILED_STATES:
        return False
    return abstract == "final" and (
        detailed in _FINAL_DETAILED_STATES or coded == "f" or status_code == "f"
    )


def _schedule_state_rank(state: Mapping[str, object]) -> int:
    if _is_final_schedule_state(state):
        return 4
    detailed = str(state.get("detailed_state") or "").strip().casefold()
    abstract = str(state.get("abstract_state") or "").strip().casefold()
    if detailed in {"in progress", "manager challenge", "warmup"} or abstract == "live":
        return 3
    if detailed in {"scheduled", "pre-game", "preview"} or abstract == "preview":
        return 2
    return 1


def _schedule_state_payload(observation: Mapping[str, object]) -> dict[str, object]:
    return {
        field: observation.get(field)
        for field in (
            "date_bucket",
            "scheduled_start_utc",
            "official_date",
            "abstract_state",
            "detailed_state",
            "coded_state",
            "status_code",
            "status_payload",
        )
    }


def _resolve_schedule_responses(
    sources: Sequence[
        tuple[EvidenceRequest, Mapping[str, object], ProviderResponse]
    ],
) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    """Resolve versioned schedule state while retaining every raw observation."""

    observations_by_game: dict[str, list[dict[str, object]]] = {}
    schedule_row_count = 0
    for request, record, response in sources:
        try:
            payload = json.loads(response.body.decode("utf-8-sig"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProspectiveAcquisitionError(
                "completed-game schedule is invalid JSON"
            ) from exc
        if not isinstance(payload, Mapping) or not isinstance(payload.get("dates"), list):
            raise ProspectiveAcquisitionError("completed-game schedule dates are missing")
        context = _schedule_query_context(request)
        source_digest = str(record.get("sha256") or "")
        captured_at = str(record.get("captured_at_utc") or "")
        if not source_digest or not captured_at:
            raise ProspectiveAcquisitionError(
                "schedule response lacks immutable digest or capture timestamp"
            )
        for day in payload["dates"]:
            if not isinstance(day, Mapping) or not isinstance(day.get("games"), list):
                raise ProspectiveAcquisitionError("completed-game schedule is malformed")
            date_bucket = str(day.get("date") or "") or None
            for raw_game in day["games"]:
                if not isinstance(raw_game, Mapping):
                    raise ProspectiveAcquisitionError(
                        "completed-game schedule game is malformed"
                    )
                schedule_row_count += 1
                game_id = _mlbam_id(raw_game.get("gamePk"), "historical game")
                status = _nested_mapping(
                    raw_game.get("status"), "historical game status"
                )
                teams = _nested_mapping(raw_game.get("teams"), "historical game teams")
                away = _nested_mapping(teams.get("away"), "historical away team")
                home = _nested_mapping(teams.get("home"), "historical home team")
                away_team = _nested_mapping(
                    away.get("team"), "historical away team identity"
                )
                home_team = _nested_mapping(
                    home.get("team"), "historical home team identity"
                )
                venue_value = raw_game.get("venue")
                venue = venue_value if isinstance(venue_value, Mapping) else {}
                sport_value = raw_game.get("sport")
                sport = sport_value if isinstance(sport_value, Mapping) else {}
                league_value = raw_game.get("league")
                league = league_value if isinstance(league_value, Mapping) else {}
                official_date = str(raw_game.get("officialDate") or "").strip()
                try:
                    date.fromisoformat(official_date)
                except ValueError as exc:
                    raise ProspectiveAcquisitionError(
                        f"historical game {game_id} officialDate is invalid"
                    ) from exc
                game_type = str(raw_game.get("gameType") or "").strip()
                season = str(raw_game.get("season") or "").strip()
                if not game_type or not season:
                    raise ProspectiveAcquisitionError(
                        f"historical game {game_id} sport/league context is missing"
                    )
                observation = {
                    "game_id": game_id,
                    "identity": {
                        "game_guid": str(raw_game.get("gameGuid") or "").strip()
                        or None,
                        "away_team_id": _positive_provider_id(
                            away_team.get("id"), "historical away team"
                        ),
                        "away_team_name": str(away_team.get("name") or "").strip()
                        or None,
                        "home_team_id": _positive_provider_id(
                            home_team.get("id"), "historical home team"
                        ),
                        "home_team_name": str(home_team.get("name") or "").strip()
                        or None,
                        "venue_id": (
                            _positive_provider_id(
                                venue.get("id"), "historical venue"
                            )
                            if venue.get("id") is not None
                            else None
                        ),
                        "venue_name": str(venue.get("name") or "").strip() or None,
                        "sport_id": (
                            str(sport.get("id") or "").strip()
                            or context["requested_sport_id"]
                        ),
                        "league_id": (
                            str(league.get("id") or "").strip()
                            or context["requested_league_id"]
                        ),
                        "requested_game_types": context["requested_game_types"],
                        "game_type": game_type,
                        "season": season,
                    },
                    "date_bucket": date_bucket,
                    "scheduled_start_utc": utc_text(
                        parse_utc(raw_game.get("gameDate", ""), "historical gameDate")
                    ),
                    "official_date": official_date,
                    "abstract_state": str(status.get("abstractGameState") or ""),
                    "detailed_state": str(status.get("detailedState") or ""),
                    "coded_state": str(status.get("codedGameState") or ""),
                    "status_code": str(status.get("statusCode") or ""),
                    "status_payload": dict(status),
                    "source_request_id": request.request_id,
                    "source_response_digest": source_digest,
                    "source_response_path": record.get("body_path"),
                    "captured_at_utc": captured_at,
                }
                observations_by_game.setdefault(game_id, []).append(observation)

    resolved: dict[str, dict[str, object]] = {}
    identity_conflicts: list[dict[str, object]] = []
    revision_count = 0
    reconciled_game_count = 0
    duplicate_observation_count = 0
    identity_fields = (
        "game_guid",
        "away_team_id",
        "home_team_id",
        "venue_id",
        "sport_id",
        "league_id",
        "requested_game_types",
        "game_type",
        "season",
    )
    for game_id in sorted(observations_by_game, key=int):
        observations = observations_by_game[game_id]
        conflicts: dict[str, list[str]] = {}
        for field in identity_fields:
            values = sorted(
                {
                    str(observation["identity"].get(field))
                    for observation in observations
                    if observation["identity"].get(field) not in {None, ""}
                }
            )
            if len(values) > 1:
                conflicts[field] = values
        if conflicts:
            identity_conflicts.append(
                {"game_id": game_id, "conflicting_fields": conflicts}
            )
            continue

        sorted_observations = sorted(
            observations, key=lambda value: _canonical_json(value)
        )
        state_digests = {
            _value_digest(_schedule_state_payload(observation))
            for observation in sorted_observations
        }
        distinct_state_count = len(state_digests)
        revision_count += max(0, distinct_state_count - 1)
        duplicate_observation_count += len(sorted_observations) - distinct_state_count
        if distinct_state_count > 1:
            reconciled_game_count += 1
        selected = max(
            sorted_observations,
            key=lambda observation: (
                parse_utc(
                    observation.get("captured_at_utc", ""),
                    "schedule observation captured_at_utc",
                ),
                _schedule_state_rank(observation),
                str(observation.get("official_date") or ""),
                str(observation.get("scheduled_start_utc") or ""),
                _value_digest(_schedule_state_payload(observation)),
                str(observation.get("source_response_digest") or ""),
            ),
        )
        canonical_identity: dict[str, object] = {"game_id": game_id}
        for field in identity_fields:
            values = sorted(
                {
                    str(observation["identity"].get(field))
                    for observation in sorted_observations
                    if observation["identity"].get(field) not in {None, ""}
                }
            )
            canonical_identity[field] = values[0] if values else None
        canonical_identity["away_team_names"] = sorted(
            {
                str(observation["identity"].get("away_team_name"))
                for observation in sorted_observations
                if observation["identity"].get("away_team_name")
            }
        )
        canonical_identity["home_team_names"] = sorted(
            {
                str(observation["identity"].get("home_team_name"))
                for observation in sorted_observations
                if observation["identity"].get("home_team_name")
            }
        )
        canonical_identity["venue_names"] = sorted(
            {
                str(observation["identity"].get("venue_name"))
                for observation in sorted_observations
                if observation["identity"].get("venue_name")
            }
        )
        selected_state = {
            **_schedule_state_payload(selected),
            "source_request_id": selected["source_request_id"],
            "source_response_digest": selected["source_response_digest"],
            "source_response_path": selected["source_response_path"],
            "captured_at_utc": selected["captured_at_utc"],
            "is_final": _is_final_schedule_state(selected),
        }
        resolved[game_id] = {
            "game_id": game_id,
            "identity": canonical_identity,
            "observed_states": sorted_observations,
            "observation_count": len(sorted_observations),
            "distinct_mutable_state_count": distinct_state_count,
            "revision_count": max(0, distinct_state_count - 1),
            "selected_canonical_state": selected_state,
            "selection_reason": (
                "status finality, then official date, scheduled start, state digest, "
                "and response digest"
            ),
        }
    return resolved, {
        "schedule_row_count": schedule_row_count,
        "unique_game_count": len(observations_by_game),
        "revision_count": revision_count,
        "reconciled_game_count": reconciled_game_count,
        "duplicate_observation_count": duplicate_observation_count,
        "identity_conflict_count": len(identity_conflicts),
        "identity_conflicts": identity_conflicts,
    }


def _parse_statcast(raw: bytes) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ProspectiveAcquisitionError("Statcast history is not UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ProspectiveAcquisitionError("Statcast history lacks a CSV header")
    columns = tuple(reader.fieldnames)
    required_columns = _RAW_REQUIRED_COLUMNS | {"game_type"}
    missing = sorted(required_columns - set(columns))
    if missing:
        raise ProspectiveAcquisitionError(
            "Statcast history lacks required columns: " + ", ".join(missing)
        )
    return columns, tuple(dict(row) for row in reader)


def _partition_statcast_game_universe(
    rows: Sequence[Mapping[str, str]],
    *,
    request_id: str,
) -> tuple[tuple[dict[str, str], ...], dict[str, object]]:
    admitted: list[dict[str, str]] = []
    excluded_game_ids: set[str] = set()
    excluded_game_types: set[str] = set()
    excluded_row_count = 0

    for index, raw_row in enumerate(rows, start=2):
        game_type = str(
            raw_row.get("game_type") or ""
        ).strip()

        if not game_type:
            raise ProspectiveAcquisitionError(
                f"Statcast chunk {request_id} row {index} "
                "has missing game_type"
            )

        if game_type not in _STATCAST_KNOWN_GAME_TYPES:
            raise ProspectiveAcquisitionError(
                f"Statcast chunk {request_id} row {index} "
                f"has invalid game_type {game_type!r}"
            )

        row = dict(raw_row)

        if game_type in _STATCAST_ADMITTED_GAME_TYPES:
            admitted.append(row)
            continue

        excluded_row_count += 1

        excluded_game_types.add(game_type)

        excluded_game_ids.add(
            _mlbam_id(
                raw_row.get("game_pk"),
                f"Statcast chunk {request_id} row {index} "
                "excluded non-regular game",
            )
        )

    return tuple(admitted), {
        "admitted_regular_season_row_count": len(admitted),
        "excluded_non_regular_row_count": excluded_row_count,
        "excluded_non_regular_game_count": len(
            excluded_game_ids
        ),
        "excluded_non_regular_game_ids": sorted(
            excluded_game_ids,
            key=int,
        ),
        "excluded_non_regular_game_types": sorted(
            excluded_game_types
        ),
    }


def _split_date_range(start_date: date, end_date: date) -> tuple[tuple[date, date], ...]:
    if start_date == end_date:
        return ()
    midpoint = start_date + timedelta(days=(end_date - start_date).days // 2)
    return ((start_date, midpoint), (midpoint + timedelta(days=1), end_date))


def _expected_completed_games(
    schedule: Mapping[str, Mapping[str, object]],
    *,
    start_date: date,
    end_date: date,
) -> set[str]:
    expected: set[str] = set()
    for game_id, resolution in schedule.items():
        state = resolution.get("selected_canonical_state")
        if not isinstance(state, Mapping) or not state.get("is_final"):
            continue
        try:
            official_date = date.fromisoformat(str(state.get("official_date") or ""))
        except ValueError as exc:
            raise ProspectiveAcquisitionError(
                f"historical game {game_id} canonical officialDate is invalid"
            ) from exc
        if start_date <= official_date <= end_date:
            expected.add(game_id)
    return expected


def _validate_chunk_rows(
    rows: Sequence[Mapping[str, str]],
    *,
    request_id: str,
    start_date: date,
    end_date: date,
) -> tuple[date | None, date | None, set[str]]:
    observed_dates: list[date] = []
    observed_games: set[str] = set()
    for index, row in enumerate(rows, start=2):
        raw_date = str(row.get("game_date") or "").strip()
        try:
            game_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ProspectiveAcquisitionError(
                f"Statcast chunk {request_id} row {index} has invalid game_date"
            ) from exc
        if not start_date <= game_date <= end_date:
            raise ProspectiveAcquisitionError(
                f"Statcast chunk {request_id} returned game_date {game_date} "
                f"outside {start_date}..{end_date}"
            )
        observed_dates.append(game_date)
        observed_games.add(
            _mlbam_id(row.get("game_pk"), f"Statcast chunk {request_id} row {index} game")
        )
    return (
        min(observed_dates) if observed_dates else None,
        max(observed_dates) if observed_dates else None,
        observed_games,
    )


def _coverage_gaps(
    *, start_date: date, end_date: date, ranges: Sequence[tuple[date, date]]
) -> tuple[list[dict[str, str]], int]:
    covered: set[date] = set()
    overlap_days = 0
    for chunk_start, chunk_end in sorted(ranges):
        cursor = chunk_start
        while cursor <= chunk_end:
            if cursor in covered:
                overlap_days += 1
            covered.add(cursor)
            cursor += timedelta(days=1)
    gaps: list[dict[str, str]] = []
    cursor = start_date
    while cursor <= end_date:
        if cursor in covered:
            cursor += timedelta(days=1)
            continue
        gap_start = cursor
        while cursor <= end_date and cursor not in covered:
            cursor += timedelta(days=1)
        gaps.append(
            {
                "start_date": gap_start.isoformat(),
                "end_date": (cursor - timedelta(days=1)).isoformat(),
            }
        )
    return gaps, overlap_days


def _pitch_sort_key(row: Mapping[str, str]) -> tuple[int, int, int, str]:
    return (
        int(str(row["game_pk"])),
        int(str(row["at_bat_number"])),
        int(str(row["pitch_number"])),
        str(row.get("sv_id") or ""),
    )


def _merge_statcast_chunks(
    chunks: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...], dict[str, object]]:
    columns: tuple[str, ...] = ()
    primary_seen: dict[tuple[str, ...], str] = {}
    ordinal_seen: dict[tuple[str, str, str], str] = {}
    rows_by_digest: dict[str, dict[str, str]] = {}
    raw_row_count = 0
    duplicate_row_count = 0
    for chunk in sorted(
        chunks,
        key=lambda item: (
            str(item["start_date"]),
            str(item["end_date"]),
            str(item["request_id"]),
        ),
    ):
        chunk_columns = tuple(str(value) for value in chunk["columns"])
        if not columns:
            columns = chunk_columns
        elif chunk_columns != columns:
            raise ProspectiveAcquisitionError(
                f"Statcast chunk {chunk['request_id']} has a conflicting CSV schema"
            )
        for row_index, raw_row in enumerate(chunk["rows"], start=2):
            raw_row_count += 1
            row = {column: str(raw_row.get(column) or "") for column in columns}
            game_id = _mlbam_id(
                row.get("game_pk"),
                f"Statcast chunk {chunk['request_id']} row {row_index} game",
            )
            at_bat = str(row.get("at_bat_number") or "").strip()
            pitch_number = str(row.get("pitch_number") or "").strip()
            if not at_bat.isdigit() or not pitch_number.isdigit():
                raise ProspectiveAcquisitionError(
                    f"missing pitch identity in Statcast chunk {chunk['request_id']} "
                    f"row {row_index}"
                )
            ordinal = (game_id, at_bat, pitch_number)
            stable_pitch_id = str(row.get("sv_id") or "").strip()
            primary = (
                ("sv_id", game_id, stable_pitch_id)
                if stable_pitch_id
                else ("ordinal", *ordinal)
            )
            row_digest = _value_digest(row)
            collisions = {
                digest
                for digest in (primary_seen.get(primary), ordinal_seen.get(ordinal))
                if digest is not None
            }
            if collisions and collisions != {row_digest}:
                raise ProspectiveAcquisitionError(
                    "conflicting duplicate pitch identity "
                    f"for game {game_id}, at-bat {at_bat}, pitch {pitch_number}"
                )
            primary_seen[primary] = row_digest
            ordinal_seen[ordinal] = row_digest
            if row_digest in rows_by_digest:
                duplicate_row_count += 1
                continue
            rows_by_digest[row_digest] = row
    merged = tuple(sorted(rows_by_digest.values(), key=_pitch_sort_key))
    game_dates = sorted(
        {date.fromisoformat(str(row["game_date"])) for row in merged}
    )
    return columns, merged, {
        "raw_row_count_before_dedupe": raw_row_count,
        "row_count_after_dedupe": len(merged),
        "duplicate_row_count": duplicate_row_count,
        "historical_game_count_represented": len(
            {str(row["game_pk"]) for row in merged}
        ),
        "observed_min_game_date": game_dates[0].isoformat() if game_dates else None,
        "observed_max_game_date": game_dates[-1].isoformat() if game_dates else None,
    }


def _write_chunk_manifests(
    temporary: Path, attempts: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    if not attempts:
        return []
    root = temporary / "chunks"
    root.mkdir(parents=True, exist_ok=True)
    references: list[dict[str, object]] = []
    for index, attempt in enumerate(
        sorted(
            attempts,
            key=lambda item: (
                str(item["start_date"]),
                str(item["end_date"]),
                str(item["request_id"]),
                str(item.get("request_started_at_utc") or ""),
            ),
        ),
        start=1,
    ):
        payload = {
            "schema_version": STATCAST_CHUNK_MANIFEST_SCHEMA_VERSION,
            **dict(attempt),
        }
        payload["manifest_digest"] = _value_digest(payload)
        filename = f"{index:04d}-{attempt['request_id']}.json"
        path = root / filename
        encoded = _canonical_json(payload, pretty=True)
        path.write_bytes(encoded)
        references.append(
            {
                "path": path.relative_to(temporary).as_posix(),
                "sha256": _sha256(encoded),
                "manifest_digest": payload["manifest_digest"],
                "request_id": attempt["request_id"],
                "start_date": attempt["start_date"],
                "end_date": attempt["end_date"],
                "chunk_status": attempt["chunk_status"],
            }
        )
    return references


def _completion_time(raw: bytes, *, expected_game_id: str) -> datetime:
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError("play-by-play response is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ProspectiveAcquisitionError("play-by-play response must be an object")
    returned_id = str(payload.get("gamePk") or "").strip()
    if returned_id and returned_id != expected_game_id:
        raise ProspectiveAcquisitionError("play-by-play game identity mismatch")
    plays = payload.get("allPlays")
    if not isinstance(plays, list) or not plays:
        raise ProspectiveAcquisitionError("final game lacks play-by-play completion evidence")
    completion: datetime | None = None
    for play in plays:
        about = play.get("about") if isinstance(play, Mapping) else None
        if not isinstance(about, Mapping):
            continue
        value = about.get("endTime")
        if value:
            parsed = parse_utc(value, "play-by-play endTime")
            completion = parsed if completion is None else max(completion, parsed)
    if completion is None:
        raise ProspectiveAcquisitionError("final game lacks a trustworthy completion clock")
    return completion


def _validate_snapshot_dir(path: Path) -> dict[str, object]:
    manifest_path = path / "historical_statcast_manifest_v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImmutableCaptureConflictError("historical Statcast manifest is invalid") from exc
    if not isinstance(manifest, dict):
        raise ImmutableCaptureConflictError("historical Statcast manifest must be an object")
    digest = manifest.get("manifest_digest")
    unsigned = dict(manifest)
    unsigned.pop("manifest_digest", None)
    if digest != _value_digest(unsigned):
        raise ImmutableCaptureConflictError("historical Statcast manifest digest mismatch")
    if manifest.get("snapshot_state") not in _TERMINAL_STATES:
        raise ImmutableCaptureConflictError("historical Statcast snapshot is not terminal")
    expected = {"historical_statcast_manifest_v1.json"}
    for record in manifest.get("provider_requests") or []:
        if not isinstance(record, Mapping):
            raise ImmutableCaptureConflictError("request accounting record is malformed")
        for field in ("body_path", "metadata_path"):
            relative = record.get(field)
            if not relative:
                continue
            target = (path / str(relative)).resolve()
            if not target.is_relative_to(path) or not target.is_file():
                raise ImmutableCaptureConflictError("bound raw response is missing")
            expected.add(str(relative).replace("/", "\\"))
            if field == "body_path" and _sha256(target.read_bytes()) != record.get("sha256"):
                raise ImmutableCaptureConflictError("bound raw response digest mismatch")
            if field == "metadata_path" and _sha256(target.read_bytes()) != record.get(
                "metadata_sha256"
            ):
                raise ImmutableCaptureConflictError("bound raw metadata digest mismatch")
    for record in manifest.get("statcast_chunk_manifests") or []:
        if not isinstance(record, Mapping):
            raise ImmutableCaptureConflictError("Statcast chunk manifest record is malformed")
        relative = str(record.get("path") or "")
        target = (path / relative).resolve()
        if not relative or not target.is_relative_to(path) or not target.is_file():
            raise ImmutableCaptureConflictError("bound Statcast chunk manifest is missing")
        raw = target.read_bytes()
        if _sha256(raw) != record.get("sha256"):
            raise ImmutableCaptureConflictError("Statcast chunk manifest digest mismatch")
        try:
            chunk_payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ImmutableCaptureConflictError(
                "Statcast chunk manifest is invalid"
            ) from exc
        chunk_digest = chunk_payload.get("manifest_digest")
        unsigned_chunk = dict(chunk_payload)
        unsigned_chunk.pop("manifest_digest", None)
        if (
            chunk_digest != _value_digest(unsigned_chunk)
            or chunk_digest != record.get("manifest_digest")
        ):
            raise ImmutableCaptureConflictError(
                "Statcast chunk content digest mismatch"
            )
        expected.add(relative.replace("/", "\\"))
    for record in manifest.get("derived_files") or []:
        if not isinstance(record, Mapping):
            raise ImmutableCaptureConflictError("derived-file record is malformed")
        relative = str(record.get("path") or "")
        target = (path / relative).resolve()
        if not relative or not target.is_relative_to(path) or not target.is_file():
            raise ImmutableCaptureConflictError("derived historical file is missing")
        if _sha256(target.read_bytes()) != record.get("sha256"):
            raise ImmutableCaptureConflictError("derived historical file digest mismatch")
        expected.add(relative.replace("/", "\\"))
    actual = {
        str(item.relative_to(path)) for item in path.rglob("*") if item.is_file()
    }
    if actual != expected:
        raise ImmutableCaptureConflictError("historical Statcast snapshot has unbound files")
    content_digest = str(manifest.get("content_digest") or "")
    content_payload = manifest.get("content_address_payload")
    if not isinstance(content_payload, Mapping) or _value_digest(content_payload) != content_digest:
        raise ImmutableCaptureConflictError("historical Statcast content digest mismatch")
    if manifest.get("snapshot_id") != "statcast-history-" + content_digest:
        raise ImmutableCaptureConflictError("historical Statcast content address mismatch")
    return manifest


def _request_from_payload(payload: Mapping[str, object]) -> EvidenceRequest:
    headers = payload.get("headers")
    if not isinstance(headers, Mapping):
        raise ImmutableCaptureConflictError("historical request headers are malformed")
    try:
        return EvidenceRequest(
            request_id=str(payload["request_id"]),
            evidence_class=str(payload["evidence_class"]),
            source_name=str(payload["source_name"]),
            provider=str(payload["provider"]),
            url=str(payload["url"]),
            event_id=(str(payload["event_id"]) if payload.get("event_id") else None),
            player_id=(str(payload["player_id"]) if payload.get("player_id") else None),
            headers={str(key): str(value) for key, value in headers.items()},
        )
    except (KeyError, TypeError, ProspectiveAcquisitionError) as exc:
        raise ImmutableCaptureConflictError("historical request identity is malformed") from exc


def _response_from_prior_record(
    prior_path: Path, record: Mapping[str, object]
) -> ProviderResponse:
    body_relative = str(record.get("body_path") or "")
    metadata_relative = str(record.get("metadata_path") or "")
    try:
        body_path = (prior_path / body_relative).resolve()
        metadata_path = (prior_path / metadata_relative).resolve()
        if (
            not body_relative
            or not metadata_relative
            or not body_path.is_relative_to(prior_path)
            or not metadata_path.is_relative_to(prior_path)
        ):
            raise OSError("response path escapes prior snapshot")
        body = body_path.read_bytes()
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImmutableCaptureConflictError(
            "prior response cannot be replayed immutably"
        ) from exc
    if not isinstance(metadata, Mapping):
        raise ImmutableCaptureConflictError("prior response metadata is malformed")
    if (
        _sha256(body) != record.get("sha256")
        or metadata.get("sha256") != record.get("sha256")
        or metadata.get("byte_size") != record.get("byte_size")
        or metadata.get("status_code") != record.get("status_code")
        or metadata.get("request")
        != {
            field: record.get(field)
            for field in (
                "request_id",
                "evidence_class",
                "source_name",
                "provider",
                "url",
                "event_id",
                "player_id",
                "headers",
            )
        }
    ):
        raise ImmutableCaptureConflictError("prior response metadata conflicts with manifest")
    headers = metadata.get("headers")
    if not isinstance(headers, Mapping):
        raise ImmutableCaptureConflictError("prior response headers are malformed")
    try:
        return ProviderResponse(
            body=body,
            status_code=int(record["status_code"]),
            headers={str(key): str(value) for key, value in headers.items()},
            provider_published_at_utc=(
                parse_utc(
                    record.get("provider_published_at_utc", ""),
                    "prior provider_published_at_utc",
                )
                if record.get("provider_published_at_utc")
                else None
            ),
            first_observed_at_utc=parse_utc(
                record.get("first_observed_at_utc", ""),
                "prior first_observed_at_utc",
            ),
            captured_at_utc=parse_utc(
                record.get("captured_at_utc", ""), "prior captured_at_utc"
            ),
        )
    except (KeyError, TypeError, ValueError, ProspectiveAcquisitionError) as exc:
        raise ImmutableCaptureConflictError("prior response clocks are malformed") from exc


class _RecoveryProvider:
    def __init__(
        self,
        provider: EvidenceProvider,
        *,
        reusable_responses: Mapping[str, ProviderResponse],
        retry_ranges: Sequence[tuple[date, date]],
    ) -> None:
        self._provider = provider
        self._reusable_responses = dict(reusable_responses)
        self._retry_ranges = tuple(retry_ranges)
        self._executions: dict[str, str] = {}

    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        reusable = self._reusable_responses.get(request.request_id)
        if reusable is not None:
            self._executions[request.request_id] = "reused_prior_response"
            return reusable
        allowed = request.source_name == "mlb_game_play_by_play"
        if request.source_name == "statcast_pitch_history":
            request_start, request_end = _statcast_request_bounds(request)
            allowed = any(
                retry_start <= request_start and request_end <= retry_end
                for retry_start, retry_end in self._retry_ranges
            )
        if not allowed:
            raise ProspectiveAcquisitionError(
                f"recovery attempted undeclared provider request: {request.request_id}"
            )
        self._executions[request.request_id] = "provider_call"
        return self._provider.fetch(request)

    def request_execution(self, request: EvidenceRequest) -> str:
        return self._executions.get(request.request_id, "provider_call")


class _ReplayProvider:
    """Serve only bytes preserved in one validated immutable snapshot."""

    def __init__(
        self,
        *,
        source_records: Mapping[str, Mapping[str, object]],
        responses: Mapping[str, ProviderResponse],
    ) -> None:
        self._source_records = {
            str(key): dict(value)
            for key, value in source_records.items()
        }
        self._responses = dict(responses)
        self._executions: dict[str, str] = {}

    def fetch(
        self,
        request: EvidenceRequest,
    ) -> ProviderResponse:

        record = self._source_records.get(
            request.request_id
        )

        response = self._responses.get(
            request.request_id
        )

        if record is None or response is None:
            raise ImmutableCaptureConflictError(
                "offline replay attempted request absent "
                f"from immutable source: {request.request_id}"
            )

        source_request = _request_from_payload(record)

        if (
            source_request.identity_payload()
            != request.identity_payload()
        ):
            raise ImmutableCaptureConflictError(
                "offline replay request identity conflicts "
                f"with immutable source: {request.request_id}"
            )

        self._executions[
            request.request_id
        ] = "replayed_immutable_response"

        return response

    def request_execution(
        self,
        request: EvidenceRequest,
    ) -> str:
        if request.request_id in self._source_records:
            return "replayed_immutable_response"

        return self._executions.get(
            request.request_id,
            "replayed_immutable_response",
        )

    def request_accounting_times(
        self,
        request: EvidenceRequest,
    ) -> tuple[datetime, datetime]:

        record = self._source_records.get(
            request.request_id
        )

        if record is None:
            raise ImmutableCaptureConflictError(
                "offline replay request accounting is absent "
                f"from immutable source: {request.request_id}"
            )

        return (
            parse_utc(
                record.get("request_started_at_utc", ""),
                "source replay request_started_at_utc",
            ),
            parse_utc(
                record.get("request_completed_at_utc", ""),
                "source replay request_completed_at_utc",
            ),
        )


def _prepare_replay(
    source_snapshot: str | Path,
    *,
    replay_git_commit: str,
) -> tuple[
    Path,
    dict[str, object],
    dict[str, object],
    tuple[EvidenceRequest, ...],
    _ReplayProvider,
    dict[str, object],
]:
    source_path = (
        Path(source_snapshot)
        .expanduser()
        .resolve()
    )

    source_manifest = _validate_snapshot_dir(
        source_path
    )

    source_state = str(
        source_manifest.get("snapshot_state") or ""
    )

    if source_state not in {
        "rejected",
        "completed",
    }:
        raise ProspectiveAcquisitionError(
            "offline replay requires an immutable terminal "
            "rejected or completed Statcast snapshot"
        )

    content = source_manifest.get(
        "content_address_payload"
    )

    if not isinstance(content, Mapping):
        raise ImmutableCaptureConflictError(
            "replay source content-address payload is malformed"
        )

    raw_identity = content.get(
        "request_identity"
    )

    if not isinstance(raw_identity, Mapping):
        raise ImmutableCaptureConflictError(
            "replay source request identity is malformed"
        )

    identity = dict(raw_identity)

    identity_digest = _value_digest(identity)

    if (
        source_manifest.get("request_identity_digest")
        != identity_digest
    ):
        raise ImmutableCaptureConflictError(
            "replay source request identity digest is invalid"
        )

    if (
        source_manifest.get("content_digest")
        != _value_digest(content)
    ):
        raise ImmutableCaptureConflictError(
            "replay source content digest is invalid"
        )

    raw_base_requests = identity.get(
        "base_requests"
    )

    if not isinstance(raw_base_requests, list):
        raise ImmutableCaptureConflictError(
            "replay source base request plan is malformed"
        )

    base_requests = tuple(
        _request_from_payload(item)
        for item in raw_base_requests
        if isinstance(item, Mapping)
    )

    if len(base_requests) != len(
        raw_base_requests
    ):
        raise ImmutableCaptureConflictError(
            "replay source base request plan is malformed"
        )

    raw_records = source_manifest.get(
        "provider_requests"
    )

    if not isinstance(raw_records, list):
        raise ImmutableCaptureConflictError(
            "replay source provider accounting is malformed"
        )

    records_by_request: dict[
        str,
        Mapping[str, object],
    ] = {}

    responses_by_request: dict[
        str,
        ProviderResponse,
    ] = {}

    execution_counts: dict[str, int] = {}

    for raw_record in raw_records:

        if not isinstance(raw_record, Mapping):
            raise ImmutableCaptureConflictError(
                "replay source provider record is malformed"
            )

        request_id = str(
            raw_record.get("request_id") or ""
        )

        if not request_id:
            raise ImmutableCaptureConflictError(
                "replay source provider record lacks request identity"
            )

        previous = records_by_request.setdefault(
            request_id,
            raw_record,
        )

        if previous != raw_record:
            raise ImmutableCaptureConflictError(
                "replay source contains conflicting provider "
                f"records for {request_id}"
            )

        if (
            raw_record.get("raw_persistence_status")
            != "completed"
            or not raw_record.get("body_path")
            or not raw_record.get("metadata_path")
        ):
            raise ProspectiveAcquisitionError(
                "offline replay source lacks complete preserved "
                f"response for {request_id}"
            )

        responses_by_request[
            request_id
        ] = _response_from_prior_record(
            source_path,
            raw_record,
        )

        execution = str(
            raw_record.get("request_execution")
            or "unknown"
        )

        execution_counts[execution] = (
            execution_counts.get(execution, 0)
            + 1
        )

    missing_base_requests = sorted(
        {
            request.request_id
            for request in base_requests
        }
        - set(records_by_request)
    )

    if missing_base_requests:
        raise ImmutableCaptureConflictError(
            "replay source lacks declared base requests: "
            + ", ".join(missing_base_requests)
        )

    manifest_path = (
        source_path
        / "historical_statcast_manifest_v1.json"
    )

    provenance: dict[str, object] = {
        "schema_version": (
            "mlb-hr-statcast-offline-replay-v1"
        ),
        "source_snapshot_id": source_manifest[
            "snapshot_id"
        ],
        "source_snapshot_state": source_state,
        "source_manifest_digest": source_manifest[
            "manifest_digest"
        ],
        "source_manifest_file_sha256": _sha256(
            manifest_path.read_bytes()
        ),
        "source_content_digest": source_manifest[
            "content_digest"
        ],
        "source_request_identity_digest": (
            source_manifest[
                "request_identity_digest"
            ]
        ),
        "source_provider_request_count": len(
            records_by_request
        ),
        "source_request_execution_counts": dict(
            sorted(execution_counts.items())
        ),
        "replay_network_access": False,
        "replay_underlying_provider_call_count": 0,
        "replay_git_commit": replay_git_commit,
        "replay_module_sha256": _sha256(
            Path(__file__).read_bytes()
        ),
        "statcast_admission_schema_version": (
            _STATCAST_ADMISSION_SCHEMA_VERSION
        ),
    }

    return (
        source_path,
        source_manifest,
        identity,
        base_requests,
        _ReplayProvider(
            source_records=records_by_request,
            responses=responses_by_request,
        ),
        provenance,
    )



def _prepare_recovery(
    prior_snapshot: str | Path,
    *,
    request_identity: Mapping[str, object],
    request_identity_digest: str,
    requested_as_of: datetime,
    provider: EvidenceProvider,
) -> tuple[
    Path,
    dict[str, object],
    _RecoveryProvider | None,
    dict[str, object] | None,
]:
    prior_path = Path(prior_snapshot).expanduser().resolve()
    prior_manifest = _validate_snapshot_dir(prior_path)
    state = str(prior_manifest.get("snapshot_state") or "")
    if state == "rejected":
        raise ProspectiveAcquisitionError(
            "prior rejected Statcast snapshot cannot be resumed"
        )
    if state not in {"partial", "completed"}:
        raise ProspectiveAcquisitionError(
            "only a prior partial or completed Statcast snapshot can be resumed"
        )

    content = prior_manifest.get("content_address_payload")
    prior_identity = (
        content.get("request_identity") if isinstance(content, Mapping) else None
    )
    if not isinstance(prior_identity, Mapping):
        raise ImmutableCaptureConflictError(
            "prior Statcast snapshot request identity is malformed"
        )

    prior_identity_dict = dict(prior_identity)
    current_identity_dict = dict(request_identity)

    if request_identity_digest != _value_digest(current_identity_dict):
        raise ImmutableCaptureConflictError(
            "recovery request identity digest is invalid"
        )

    if (
        prior_manifest.get("request_identity_digest")
        != _value_digest(prior_identity_dict)
    ):
        raise ImmutableCaptureConflictError(
            "prior Statcast snapshot request identity digest is invalid"
        )

    try:
        prior_requested_as_of = parse_utc(
            prior_identity_dict.pop("requested_as_of_utc"),
            "prior requested_as_of_utc",
        )
        current_requested_as_of = parse_utc(
            current_identity_dict.pop("requested_as_of_utc"),
            "recovery requested_as_of_utc",
        )
    except (KeyError, TypeError, ValueError, ProspectiveAcquisitionError) as exc:
        raise ImmutableCaptureConflictError(
            "prior or recovery request identity has invalid requested_as_of_utc"
        ) from exc

    if current_requested_as_of != requested_as_of:
        raise ImmutableCaptureConflictError(
            "recovery requested_as_of_utc conflicts with acquisition request"
        )

    if current_identity_dict != prior_identity_dict:
        raise ImmutableCaptureConflictError(
            "prior Statcast snapshot request identity conflicts with recovery"
        )

    if current_requested_as_of < prior_requested_as_of:
        raise ImmutableCaptureConflictError(
            "recovery requested_as_of_utc cannot precede the prior snapshot cutoff"
        )

    if state == "completed":
        return prior_path, prior_manifest, None, None
    raw_records = prior_manifest.get("provider_requests")
    if not isinstance(raw_records, list):
        raise ImmutableCaptureConflictError("prior provider accounting is malformed")
    records_by_request: dict[str, Mapping[str, object]] = {}
    responses_by_request: dict[str, ProviderResponse] = {}
    for raw_record in raw_records:
        if not isinstance(raw_record, Mapping):
            raise ImmutableCaptureConflictError("prior provider record is malformed")
        request_id = str(raw_record.get("request_id") or "")
        if not request_id:
            raise ImmutableCaptureConflictError("prior provider record lacks request identity")
        previous = records_by_request.setdefault(request_id, raw_record)
        if previous != raw_record:
            raise ImmutableCaptureConflictError(
                f"conflicting prior provider records for {request_id}"
            )
        if (
            raw_record.get("raw_persistence_status") == "completed"
            and raw_record.get("body_path")
            and raw_record.get("metadata_path")
        ):
            responses_by_request[request_id] = _response_from_prior_record(
                prior_path, raw_record
            )

    base_payloads = request_identity.get("base_requests")
    if not isinstance(base_payloads, list):
        raise ImmutableCaptureConflictError("prior base request plan is malformed")
    base_requests = tuple(
        _request_from_payload(item)
        for item in base_payloads
        if isinstance(item, Mapping)
    )
    if len(base_requests) != len(base_payloads):
        raise ImmutableCaptureConflictError("prior base request plan is malformed")
    schedule_requests = tuple(
        item for item in base_requests if item.source_name == "mlb_completed_game_schedule"
    )
    schedule_sources = []
    for request in schedule_requests:
        record = records_by_request.get(request.request_id)
        response = responses_by_request.get(request.request_id)
        if (
            record is None
            or response is None
            or record.get("availability_status") != "completed"
        ):
            raise ProspectiveAcquisitionError(
                "prior partial snapshot lacks reusable completed schedule evidence"
            )
        schedule_sources.append((request, record, response))
    resolved_schedule, resolved_summary = _resolve_schedule_responses(schedule_sources)
    if (
        [resolved_schedule[key] for key in sorted(resolved_schedule, key=int)]
        != prior_manifest.get("schedule_resolution")
        or resolved_summary != prior_manifest.get("schedule_summary")
    ):
        raise ImmutableCaptureConflictError(
            "prior schedule resolution conflicts with immutable raw observations"
        )

    raw_chunk_refs = prior_manifest.get("statcast_chunk_manifests")
    raw_accepted_refs = prior_manifest.get("accepted_statcast_chunk_manifests")
    raw_ranges = prior_manifest.get("statcast_requested_ranges")
    if not all(isinstance(value, list) for value in (raw_chunk_refs, raw_accepted_refs, raw_ranges)):
        raise ImmutableCaptureConflictError("prior Statcast leaf accounting is malformed")
    chunk_payloads: dict[str, Mapping[str, object]] = {}
    for raw_ref in raw_chunk_refs:
        if not isinstance(raw_ref, Mapping):
            raise ImmutableCaptureConflictError("prior Statcast leaf reference is malformed")
        relative = str(raw_ref.get("path") or "")
        try:
            payload = json.loads((prior_path / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ImmutableCaptureConflictError("prior Statcast leaf is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise ImmutableCaptureConflictError("prior Statcast leaf is malformed")
        request_id = str(payload.get("request_id") or "")
        previous = chunk_payloads.setdefault(request_id, payload)
        if previous != payload:
            raise ImmutableCaptureConflictError(
                f"conflicting prior Statcast leaf manifests for {request_id}"
            )

    accepted_refs: list[dict[str, object]] = []
    accepted_request_ids: set[str] = set()
    accepted_ranges: list[tuple[date, date]] = []
    for raw_ref in raw_accepted_refs:
        if not isinstance(raw_ref, Mapping):
            raise ImmutableCaptureConflictError("accepted Statcast leaf reference is malformed")
        request_id = str(raw_ref.get("request_id") or "")
        payload = chunk_payloads.get(request_id)
        record = records_by_request.get(request_id)
        response = responses_by_request.get(request_id)
        if (
            payload is None
            or record is None
            or response is None
            or payload.get("chunk_status") != "accepted"
            or raw_ref.get("chunk_status") != "accepted"
            or payload.get("manifest_digest") != raw_ref.get("manifest_digest")
            or payload.get("response_digest") != record.get("sha256")
            or payload.get("response_path") != record.get("body_path")
        ):
            raise ImmutableCaptureConflictError(
                f"prior accepted Statcast leaf is not fully bound: {request_id}"
            )
        leaf_start, leaf_end = _statcast_request_bounds(
            _request_from_payload(record)
        )
        if (
            payload.get("start_date") != leaf_start.isoformat()
            or payload.get("end_date") != leaf_end.isoformat()
        ):
            raise ImmutableCaptureConflictError(
                f"prior accepted Statcast leaf range conflicts: {request_id}"
            )
        columns, rows = _parse_statcast(response.body)
        _, _, observed_games = _validate_chunk_rows(
            rows,
            request_id=request_id,
            start_date=leaf_start,
            end_date=leaf_end,
        )
        expected_games = _expected_completed_games(
            resolved_schedule, start_date=leaf_start, end_date=leaf_end
        )
        if expected_games - observed_games:
            raise ImmutableCaptureConflictError(
                f"prior accepted Statcast leaf is incomplete: {request_id}"
            )
        if tuple(str(value) for value in columns) == ():
            raise ImmutableCaptureConflictError(
                f"prior accepted Statcast leaf lacks a CSV schema: {request_id}"
            )
        accepted_request_ids.add(request_id)
        accepted_ranges.append((leaf_start, leaf_end))
        accepted_refs.append(dict(raw_ref))

    accepted_summary_ids = {
        str(item.get("request_id") or "")
        for item in raw_ranges
        if isinstance(item, Mapping) and item.get("chunk_status") == "accepted"
    }
    if accepted_summary_ids != accepted_request_ids:
        raise ImmutableCaptureConflictError(
            "prior accepted Statcast leaf summary conflicts with leaf manifests"
        )
    retry_ranges: list[tuple[date, date]] = []
    retry_ids: set[str] = set()
    leaf_ranges: list[tuple[date, date]] = []
    for item in raw_ranges:
        if not isinstance(item, Mapping):
            raise ImmutableCaptureConflictError("prior Statcast range summary is malformed")
        status = str(item.get("chunk_status") or "")
        if status == "split":
            continue
        request_id = str(item.get("request_id") or "")
        try:
            leaf_start = date.fromisoformat(str(item.get("start_date") or ""))
            leaf_end = date.fromisoformat(str(item.get("end_date") or ""))
        except ValueError as exc:
            raise ImmutableCaptureConflictError("prior Statcast leaf range is invalid") from exc
        if leaf_end < leaf_start:
            raise ImmutableCaptureConflictError("prior Statcast leaf range is inverted")
        leaf_ranges.append((leaf_start, leaf_end))
        if status != "accepted":
            retry_ids.add(request_id)
            retry_ranges.append((leaf_start, leaf_end))
    season_start = date.fromisoformat(str(request_identity["season_start_date"]))
    season_end = date.fromisoformat(str(request_identity["season_end_date"]))
    gaps, _ = _coverage_gaps(
        start_date=season_start, end_date=season_end, ranges=leaf_ranges
    )
    for gap in gaps:
        gap_start = date.fromisoformat(str(gap["start_date"]))
        gap_end = date.fromisoformat(str(gap["end_date"]))
        request = _statcast_request(gap_start, gap_end)
        retry_ids.add(request.request_id)
        retry_ranges.append((gap_start, gap_end))
    if not retry_ranges:
        raise ImmutableCaptureConflictError(
            "prior partial snapshot has no failed or missing Statcast leaves"
        )

    reusable: dict[str, ProviderResponse] = {
        request.request_id: responses_by_request[request.request_id]
        for request in schedule_requests
    }
    for request_id, payload in chunk_payloads.items():
        if request_id in retry_ids:
            continue
        response = responses_by_request.get(request_id)
        if response is None:
            raise ImmutableCaptureConflictError(
                f"prior reusable Statcast response is missing: {request_id}"
            )
        reusable[request_id] = response

    prior_digest = str(prior_manifest.get("manifest_digest") or "")
    provenance = {
        "schema_version": "mlb-hr-statcast-recovery-v1",
        "prior_partial_snapshot_id": prior_manifest["snapshot_id"],
        "prior_partial_manifest_digest": prior_digest,
        "prior_partial_manifest_file_sha256": _sha256(
            (prior_path / "historical_statcast_manifest_v1.json").read_bytes()
        ),
        "reused_successful_leaf_count": len(accepted_refs),
        "reused_successful_leaf_manifests": sorted(
            accepted_refs,
            key=lambda item: (
                str(item.get("start_date") or ""),
                str(item.get("end_date") or ""),
                str(item.get("request_id") or ""),
            ),
        ),
        "failed_or_missing_leaf_ranges": [
            {
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "request_id": _statcast_request(start, end).request_id,
            }
            for start, end in sorted(set(retry_ranges))
        ],
        "requested_as_of_utc": utc_text(requested_as_of),
    }
    return (
        prior_path,
        prior_manifest,
        _RecoveryProvider(
            provider,
            reusable_responses=reusable,
            retry_ranges=retry_ranges,
        ),
        provenance,
    )


def _existing_request_snapshot(
    date_root: Path, request_identity_digest: str
) -> tuple[Path, dict[str, object]] | None:
    matches: list[tuple[Path, dict[str, object]]] = []
    if not date_root.exists():
        return None
    for child in sorted(date_root.iterdir()):
        if not child.is_dir() or not child.name.startswith("hs-"):
            continue
        manifest = _validate_snapshot_dir(child)
        if manifest.get("request_identity_digest") == request_identity_digest:
            matches.append((child, manifest))
    reusable = [item for item in matches if item[1].get("snapshot_state") == "completed"]
    if len(reusable) > 1:
        identities = {str(item[1].get("snapshot_id")) for item in reusable}
        if len(identities) > 1:
            raise ImmutableCaptureConflictError(
                "conflicting immutable Statcast snapshots for one request identity"
            )
    return reusable[0] if reusable else None



def _existing_replay_snapshot(
    date_root: Path,
    request_identity_digest: str,
    replay_provenance: Mapping[str, object],
) -> tuple[Path, dict[str, object]] | None:

    matches: list[
        tuple[Path, dict[str, object]]
    ] = []

    if not date_root.exists():
        return None

    expected_provenance = dict(
        replay_provenance
    )

    for child in sorted(
        date_root.iterdir()
    ):
        if (
            not child.is_dir()
            or not child.name.startswith("hs-")
        ):
            continue

        manifest = _validate_snapshot_dir(
            child
        )

        if (
            manifest.get("request_identity_digest")
            != request_identity_digest
        ):
            continue

        content = manifest.get(
            "content_address_payload"
        )

        if not isinstance(content, Mapping):
            continue

        if (
            content.get("replay_provenance")
            != expected_provenance
        ):
            continue

        matches.append(
            (child, manifest)
        )

    if len(matches) > 1:
        identities = {
            str(item[1].get("snapshot_id"))
            for item in matches
        }

        if len(identities) > 1:
            raise ImmutableCaptureConflictError(
                "conflicting immutable replay snapshots "
                "for one source/provenance identity"
            )

    return matches[0] if matches else None



def acquire_historical_statcast_snapshot(
    *,
    operating_date: date,
    season_start_date: date,
    requested_as_of_utc: datetime | str,
    target_game_ids: Sequence[str],
    eligible_hitter_ids: Sequence[str],
    probable_pitcher_ids: Sequence[str],
    provider: EvidenceProvider,
    acquisition_root: str | Path = DEFAULT_ACQUISITION_ROOT,
    git_commit: str,
    base_requests: Sequence[EvidenceRequest] | None = None,
    initial_chunk_days: int = DEFAULT_INITIAL_CHUNK_DAYS,
    suspicious_chunk_row_count: int = DEFAULT_SUSPICIOUS_CHUNK_ROW_COUNT,
    resume_from_snapshot: str | Path | None = None,
    allow_play_by_play_fallback: bool = True,
    _replay_provenance: Mapping[str, object] | None = None,
) -> HistoricalStatcastResult:
    """Capture one reusable, content-addressed daily Statcast history artifact."""

    requested_as_of = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    targets = tuple(
        sorted({_mlbam_id(value, "target game") for value in target_game_ids}, key=int)
    )
    hitters = tuple(
        sorted(
            {_mlbam_id(value, "eligible hitter") for value in eligible_hitter_ids},
            key=int,
        )
    )
    pitchers = tuple(
        sorted(
            {_mlbam_id(value, "probable pitcher") for value in probable_pitcher_ids},
            key=int,
        )
    )
    if not targets or not hitters or not pitchers:
        raise ProspectiveAcquisitionError(
            "historical Statcast requires target games, eligible hitters, and probable pitchers"
        )
    if (
        isinstance(suspicious_chunk_row_count, bool)
        or not isinstance(suspicious_chunk_row_count, int)
        or suspicious_chunk_row_count <= 0
    ):
        raise ProspectiveAcquisitionError(
            "suspicious_chunk_row_count must be a positive integer"
        )
    season_end_date = operating_date - timedelta(days=1)
    requests = tuple(
        base_requests
        or build_historical_statcast_requests(
            operating_date=operating_date,
            season_start_date=season_start_date,
            initial_chunk_days=initial_chunk_days,
        )
    )
    request_ids = [item.request_id for item in requests]
    if len(request_ids) != len(set(request_ids)):
        raise ProspectiveAcquisitionError("historical Statcast request IDs must be unique")
    schedule_requests = tuple(
        item for item in requests if item.source_name == "mlb_completed_game_schedule"
    )
    statcast_requests = tuple(
        item for item in requests if item.source_name == "statcast_pitch_history"
    )
    if (
        not schedule_requests
        or not statcast_requests
        or len(schedule_requests) + len(statcast_requests) != len(requests)
    ):
        raise ProspectiveAcquisitionError(
            "historical Statcast requires schedule and bounded Savant requests only"
        )
    initial_ranges = []
    for request in statcast_requests:
        chunk_start, chunk_end = _statcast_request_bounds(request)
        if chunk_start < season_start_date or chunk_end > season_end_date:
            raise ProspectiveAcquisitionError(
                f"Statcast request {request.request_id} escapes the season-to-date range"
            )
        initial_ranges.append((chunk_start, chunk_end))
    initial_gaps, _ = _coverage_gaps(
        start_date=season_start_date,
        end_date=season_end_date,
        ranges=initial_ranges,
    )
    if initial_gaps:
        raise ProspectiveAcquisitionError(
            "initial Statcast chunk plan has unexplained date gaps"
        )

    identity = {
        "schema_version": HISTORICAL_STATCAST_SCHEMA_VERSION,
        "operating_date": operating_date.isoformat(),
        "season_start_date": season_start_date.isoformat(),
        "season_end_date": season_end_date.isoformat(),
        "requested_as_of_utc": utc_text(requested_as_of),
        "target_game_ids": list(targets),
        "eligible_hitter_ids": list(hitters),
        "probable_pitcher_ids": list(pitchers),
        "initial_chunk_days": initial_chunk_days,
        "suspicious_chunk_row_count": suspicious_chunk_row_count,
        "base_requests": [item.identity_payload() for item in requests],
    }
    request_identity_digest = _value_digest(identity)
    recovery_provenance: dict[str, object] | None = None
    replay_provenance = (
        dict(_replay_provenance)
        if _replay_provenance is not None
        else None
    )

    if (
        replay_provenance is not None
        and resume_from_snapshot is not None
    ):
        raise ProspectiveAcquisitionError(
            "offline replay and recovery cannot be combined"
        )

    if replay_provenance is not None:
        replay_underlying_provider_call_count = (
            replay_provenance.get(
                "replay_underlying_provider_call_count"
            )
        )

        if (
            replay_provenance.get("schema_version")
            != "mlb-hr-statcast-offline-replay-v1"
            or replay_provenance.get(
                "replay_network_access"
            )
            is not False
            or isinstance(
                replay_underlying_provider_call_count,
                bool,
            )
            or not isinstance(
                replay_underlying_provider_call_count,
                int,
            )
            or replay_underlying_provider_call_count != 0
        ):
            raise ImmutableCaptureConflictError(
                "offline replay provenance is malformed"
            )

    if resume_from_snapshot is not None:
        prior_path, prior_manifest, recovery_provider, recovery_provenance = (
            _prepare_recovery(
                resume_from_snapshot,
                request_identity=identity,
                request_identity_digest=request_identity_digest,
                requested_as_of=requested_as_of,
                provider=provider,
            )
        )
        if recovery_provider is None:
            prior_coverage = prior_manifest.get("coverage")
            if not isinstance(prior_coverage, Mapping):
                raise ImmutableCaptureConflictError(
                    "historical Statcast coverage is missing"
                )
            witness_counts = prior_coverage.get(
                "completion_witness_counts_by_source_type"
            ) or {}
            if not isinstance(witness_counts, Mapping):
                raise ImmutableCaptureConflictError(
                    "historical completion-witness accounting is malformed"
                )
            return HistoricalStatcastResult(
                snapshot_id=str(prior_manifest["snapshot_id"]),
                snapshot_dir=prior_path,
                manifest_path=prior_path / "historical_statcast_manifest_v1.json",
                snapshot_state="completed",
                manifest_digest=str(prior_manifest["manifest_digest"]),
                no_op=True,
                provider_call_count=0,
                game_count=int(prior_coverage.get("game_count") or 0),
                pitch_count=int(prior_coverage.get("pitch_count") or 0),
                plate_appearance_count=int(
                    prior_coverage.get("plate_appearance_count") or 0
                ),
                reused_chunk_count=0,
                recovered_chunk_count=0,
                completion_witness_counts_by_source_type={
                    str(key): int(value) for key, value in witness_counts.items()
                },
            )
        provider = recovery_provider
    root = Path(acquisition_root).expanduser().resolve() / "historical_statcast"
    date_root = root / operating_date.isoformat()
    existing = (
        _existing_replay_snapshot(
            date_root,
            request_identity_digest,
            replay_provenance,
        )
        if replay_provenance is not None
        else _existing_request_snapshot(
            date_root,
            request_identity_digest,
        )
    )
    if existing is not None:
        path, manifest = existing
        coverage = manifest.get("coverage")
        if not isinstance(coverage, Mapping):
            raise ImmutableCaptureConflictError("historical Statcast coverage is missing")
        return HistoricalStatcastResult(
            snapshot_id=str(manifest["snapshot_id"]),
            snapshot_dir=path,
            manifest_path=path / "historical_statcast_manifest_v1.json",
            snapshot_state=str(manifest["snapshot_state"]),
            manifest_digest=str(manifest["manifest_digest"]),
            no_op=True,
            provider_call_count=0,
            game_count=int(coverage.get("game_count") or 0),
            pitch_count=int(coverage.get("pitch_count") or 0),
            plate_appearance_count=int(coverage.get("plate_appearance_count") or 0),
            reused_chunk_count=0,
            recovered_chunk_count=0,
            completion_witness_counts_by_source_type={
                str(key): int(value)
                for key, value in (
                    coverage.get("completion_witness_counts_by_source_type") or {}
                ).items()
            },
        )

    date_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".t-", dir=date_root))
    published = False
    records: list[dict[str, object]] = []
    integrity_errors: list[str] = []
    rejection_counts: dict[str, int] = {}
    derived_files: list[dict[str, object]] = []
    included_rows: list[dict[str, str]] = []
    included_clocks: list[dict[str, object]] = []
    completion_witness_counts: dict[str, int] = {}
    schedule: dict[str, dict[str, object]] = {}
    schedule_summary: dict[str, object] = {
        "schedule_row_count": 0,
        "unique_game_count": 0,
        "revision_count": 0,
        "reconciled_game_count": 0,
        "duplicate_observation_count": 0,
        "identity_conflict_count": 0,
        "identity_conflicts": [],
    }
    chunk_attempts: list[dict[str, object]] = []
    accepted_chunks: list[dict[str, object]] = []
    leaf_failure_count = 0
    try:
        schedule_sources: list[
            tuple[EvidenceRequest, Mapping[str, object], ProviderResponse]
        ] = []
        for request in schedule_requests:
            record, response = _request_record(
                temporary, request, provider, requested_as_of=requested_as_of
            )
            records.append(record)
            if (
                response is not None
                and record.get("availability_status") == "completed"
                and record.get("raw_persistence_status") == "completed"
            ):
                schedule_sources.append((request, record, response))
        schedule_ready = len(schedule_sources) == len(schedule_requests)
        if schedule_ready:
            try:
                schedule, schedule_summary = _resolve_schedule_responses(
                    schedule_sources
                )
            except ProspectiveAcquisitionError as exc:
                integrity_errors.append(str(exc))
            for conflict in schedule_summary.get("identity_conflicts") or []:
                fields = ", ".join(sorted(conflict["conflicting_fields"]))
                integrity_errors.append(
                    "conflicting schedule identity for historical game "
                    f"{conflict['game_id']}: {fields}"
                )

        def collect_chunk(request: EvidenceRequest) -> None:
            nonlocal leaf_failure_count
            chunk_start, chunk_end = _statcast_request_bounds(request)
            record, response = _request_record(
                temporary, request, provider, requested_as_of=requested_as_of
            )
            record["requested_start_date"] = chunk_start.isoformat()
            record["requested_end_date"] = chunk_end.isoformat()
            records.append(record)
            attempt: dict[str, object] = {
                "request_id": request.request_id,
                "requested_url": request.url,
                "start_date": chunk_start.isoformat(),
                "end_date": chunk_end.isoformat(),
                "request_started_at_utc": record.get("request_started_at_utc"),
                "request_completed_at_utc": record.get("request_completed_at_utc"),
                "response_status_code": record.get("status_code"),
                "response_row_count": None,
                "response_digest": record.get("sha256"),
                "response_path": record.get("body_path"),
                "response_metadata_path": record.get("metadata_path"),
                "observed_min_game_date": None,
                "observed_max_game_date": None,
                "observed_game_count": 0,
                "expected_completed_game_count": 0,
                "missing_expected_game_ids": [],
                "suspicious_reasons": [],
                "split_into": [],
                "chunk_status": "failed",
            }
            if (
                response is None
                or record.get("raw_persistence_status") != "completed"
                or record.get("availability_status")
                not in {"completed", "partial"}
            ):
                leaf_failure_count += 1
                attempt["chunk_status"] = str(
                    record.get("availability_status") or "unavailable"
                )
                chunk_attempts.append(attempt)
                return
            try:
                columns, raw_rows = _parse_statcast(response.body)
                admitted_rows, game_universe = _partition_statcast_game_universe(
                    raw_rows,
                    request_id=request.request_id,
                )
                observed_min, observed_max, observed_games = _validate_chunk_rows(
                    raw_rows,
                    request_id=request.request_id,
                    start_date=chunk_start,
                    end_date=chunk_end,
                )
                _, _, admitted_observed_games = _validate_chunk_rows(
                    admitted_rows,
                    request_id=request.request_id,
                    start_date=chunk_start,
                    end_date=chunk_end,
                )
            except ProspectiveAcquisitionError as exc:
                record["evidence_admissibility"] = "rejected"
                record["evidence_rejection_reason"] = str(exc)
                integrity_errors.append(str(exc))
                leaf_failure_count += 1
                attempt["chunk_status"] = "rejected"
                attempt["suspicious_reasons"] = [str(exc)]
                chunk_attempts.append(attempt)
                return
            expected_games = _expected_completed_games(
                schedule, start_date=chunk_start, end_date=chunk_end
            )
            missing_games = sorted(
                expected_games - admitted_observed_games,
                key=int,
            )
            suspicious_reasons: list[str] = []
            if record.get("availability_status") == "partial":
                suspicious_reasons.append("provider_partial_response")
            if len(raw_rows) >= suspicious_chunk_row_count:
                suspicious_reasons.append("suspicious_row_limit")
            if missing_games:
                suspicious_reasons.append("missing_completed_schedule_games")
            attempt.update(
                {
                    "response_row_count": len(raw_rows),
                    "observed_min_game_date": (
                        observed_min.isoformat() if observed_min else None
                    ),
                    "observed_max_game_date": (
                        observed_max.isoformat() if observed_max else None
                    ),
                    "observed_game_count": len(observed_games),
                    "admitted_regular_season_row_count": game_universe[
                        "admitted_regular_season_row_count"
                    ],
                    "admitted_regular_season_game_count": len(
                        admitted_observed_games
                    ),
                    "excluded_non_regular_row_count": game_universe[
                        "excluded_non_regular_row_count"
                    ],
                    "excluded_non_regular_game_count": game_universe[
                        "excluded_non_regular_game_count"
                    ],
                    "excluded_non_regular_game_ids": game_universe[
                        "excluded_non_regular_game_ids"
                    ],
                    "excluded_non_regular_game_types": game_universe[
                        "excluded_non_regular_game_types"
                    ],
                    "expected_completed_game_count": len(expected_games),
                    "missing_expected_game_ids": missing_games,
                    "suspicious_reasons": suspicious_reasons,
                }
            )
            children = _split_date_range(chunk_start, chunk_end)
            if suspicious_reasons and children:
                child_requests = tuple(
                    _statcast_request(child_start, child_end)
                    for child_start, child_end in children
                )
                record["evidence_admissibility"] = "superseded_by_split"
                record["evidence_rejection_reason"] = ", ".join(
                    suspicious_reasons
                )
                attempt["chunk_status"] = "split"
                attempt["split_into"] = [item.request_id for item in child_requests]
                chunk_attempts.append(attempt)
                for child in child_requests:
                    collect_chunk(child)
                return
            if suspicious_reasons:
                record["evidence_admissibility"] = "rejected"
                record["evidence_rejection_reason"] = ", ".join(
                    suspicious_reasons
                )
                leaf_failure_count += 1
                attempt["chunk_status"] = "partial"
                chunk_attempts.append(attempt)
                return
            rows = admitted_rows
            record["evidence_admissibility"] = "included"
            attempt["chunk_status"] = "accepted"
            chunk_attempts.append(attempt)
            accepted_chunks.append(
                {
                    "request_id": request.request_id,
                    "start_date": chunk_start,
                    "end_date": chunk_end,
                    "columns": columns,
                    "rows": rows,
                    "record": record,
                    "response": response,
                }
            )

        if schedule_ready and not integrity_errors:
            for request in sorted(
                statcast_requests,
                key=lambda value: (*_statcast_request_bounds(value), value.request_id),
            ):
                collect_chunk(request)
        else:
            leaf_failure_count = len(statcast_requests)
            for request in statcast_requests:
                chunk_start, chunk_end = _statcast_request_bounds(request)
                chunk_attempts.append(
                    {
                        "request_id": request.request_id,
                        "requested_url": request.url,
                        "start_date": chunk_start.isoformat(),
                        "end_date": chunk_end.isoformat(),
                        "request_started_at_utc": None,
                        "request_completed_at_utc": None,
                        "response_status_code": None,
                        "response_row_count": None,
                        "response_digest": None,
                        "response_path": None,
                        "response_metadata_path": None,
                        "observed_min_game_date": None,
                        "observed_max_game_date": None,
                        "observed_game_count": 0,
                        "expected_completed_game_count": 0,
                        "missing_expected_game_ids": [],
                        "suspicious_reasons": ["schedule_not_eligible"],
                        "split_into": [],
                        "chunk_status": "not_attempted",
                    }
                )

        accepted_attempts = [
            item
            for item in chunk_attempts
            if item.get("chunk_status") == "accepted"
        ]

        accepted_provider_row_count_total = sum(
            int(item.get("response_row_count") or 0)
            for item in accepted_attempts
        )

        admitted_regular_season_row_count_before_dedupe = sum(
            int(
                item.get(
                    "admitted_regular_season_row_count"
                )
                or 0
            )
            for item in accepted_attempts
        )

        excluded_non_regular_row_count = sum(
            int(
                item.get(
                    "excluded_non_regular_row_count"
                )
                or 0
            )
            for item in accepted_attempts
        )

        excluded_non_regular_game_ids = sorted(
            {
                str(game_id)
                for item in accepted_attempts
                for game_id in (
                    item.get(
                        "excluded_non_regular_game_ids"
                    )
                    or []
                )
            },
            key=int,
        )

        excluded_non_regular_game_types = sorted(
            {
                str(game_type)
                for item in accepted_attempts
                for game_type in (
                    item.get(
                        "excluded_non_regular_game_types"
                    )
                    or []
                )
            }
        )

        admission_partition_balance_ok = (
            accepted_provider_row_count_total
            ==
            admitted_regular_season_row_count_before_dedupe
            + excluded_non_regular_row_count
        )

        if not admission_partition_balance_ok:
            integrity_errors.append(
                "Statcast regular-season admission "
                "accounting does not balance"
            )

        admission_accounting: dict[str, object] = {
            "statcast_admission_schema_version": (
                _STATCAST_ADMISSION_SCHEMA_VERSION
            ),
            "historical_game_universe": (
                _HISTORICAL_GAME_UNIVERSE
            ),
            "known_statcast_game_types": sorted(
                _STATCAST_KNOWN_GAME_TYPES
            ),
            "admitted_game_types": sorted(
                _STATCAST_ADMITTED_GAME_TYPES
            ),
            "accepted_provider_row_count_total": (
                accepted_provider_row_count_total
            ),
            "admitted_regular_season_row_count_before_dedupe": (
                admitted_regular_season_row_count_before_dedupe
            ),
            "excluded_non_regular_row_count": (
                excluded_non_regular_row_count
            ),
            "excluded_non_regular_game_count": len(
                excluded_non_regular_game_ids
            ),
            "excluded_non_regular_game_ids": (
                excluded_non_regular_game_ids
            ),
            "excluded_non_regular_game_types": (
                excluded_non_regular_game_types
            ),
            "admission_partition_balance_ok": (
                admission_partition_balance_ok
            ),
        }

        accepted_ranges = [
            (item["start_date"], item["end_date"]) for item in accepted_chunks
        ]
        coverage_gaps, overlap_days = _coverage_gaps(
            start_date=season_start_date,
            end_date=season_end_date,
            ranges=accepted_ranges,
        )
        merged_columns: tuple[str, ...] = ()
        merged_rows: tuple[dict[str, str], ...] = ()
        merge_summary: dict[str, object] = {
            "raw_row_count_before_dedupe": 0,
            "row_count_after_dedupe": 0,
            "duplicate_row_count": 0,
            "historical_game_count_represented": 0,
            "observed_min_game_date": None,
            "observed_max_game_date": None,
        }
        chunks_complete = (
            schedule_ready
            and not integrity_errors
            and leaf_failure_count == 0
            and not coverage_gaps
        )
        if chunks_complete:
            try:
                merged_columns, merged_rows, merge_summary = _merge_statcast_chunks(
                    accepted_chunks
                )
                if (
                    int(
                        merge_summary.get(
                            "raw_row_count_before_dedupe"
                        )
                        or 0
                    )
                    != admitted_regular_season_row_count_before_dedupe
                ):
                    raise ProspectiveAcquisitionError(
                        "Statcast regular-season merge input "
                        "does not match admission accounting"
                    )
            except ProspectiveAcquisitionError as exc:
                integrity_errors.append(str(exc))

        accepted_responses = [item["response"] for item in accepted_chunks]
        accepted_first_observed = (
            max(
                parse_utc(item.first_observed_at_utc, "Statcast first observed")
                for item in accepted_responses
            )
            if accepted_responses
            else None
        )
        accepted_captured = (
            max(
                parse_utc(item.captured_at_utc, "Statcast captured")
                for item in accepted_responses
            )
            if accepted_responses
            else None
        )

        relevant_by_game: dict[str, list[dict[str, str]]] = {}
        if chunks_complete and not integrity_errors:
            try:
                for index, row in enumerate(merged_rows, start=2):
                    game_id = _mlbam_id(
                        row.get("game_pk"), f"Statcast row {index} game"
                    )
                    batter_id = _mlbam_id(
                        row.get("batter"), f"Statcast row {index} hitter"
                    )
                    pitcher_id = _mlbam_id(
                        row.get("pitcher"), f"Statcast row {index} pitcher"
                    )
                    if batter_id not in hitters and pitcher_id not in pitchers:
                        continue
                    if game_id in targets:
                        rejection_counts["target_game"] = (
                            rejection_counts.get("target_game", 0) + 1
                        )
                        continue
                    game = schedule.get(game_id)
                    if game is None:
                        raise ProspectiveAcquisitionError(
                            f"Statcast game identity conflicts with schedule: {game_id}"
                        )
                    state = game.get("selected_canonical_state")
                    if not isinstance(state, Mapping):
                        raise ProspectiveAcquisitionError(
                            f"historical game {game_id} lacks canonical schedule state"
                        )
                    scheduled_start = parse_utc(
                        state.get("scheduled_start_utc", ""),
                        "historical scheduled_start_utc",
                    )
                    if scheduled_start >= requested_as_of:
                        rejection_counts["future_game"] = (
                            rejection_counts.get("future_game", 0) + 1
                        )
                        continue
                    if not state.get("is_final"):
                        detailed = str(state.get("detailed_state") or "").casefold()
                        abstract = str(state.get("abstract_state") or "").casefold()
                        if detailed in {"in progress", "manager challenge", "warmup"} or abstract == "live":
                            rejection_counts["in_progress_game"] = (
                                rejection_counts.get("in_progress_game", 0) + 1
                            )
                            continue
                        if not allow_play_by_play_fallback:
                            rejection_counts["ambiguous_completion_state"] = (
                                rejection_counts.get("ambiguous_completion_state", 0) + 1
                            )
                            continue
                    relevant_by_game.setdefault(game_id, []).append(row)
            except ProspectiveAcquisitionError as exc:
                integrity_errors.append(str(exc))

        if (
            chunks_complete
            and not integrity_errors
            and accepted_captured is not None
            and accepted_first_observed is not None
        ):
            for game_id in sorted(relevant_by_game, key=int):
                game = schedule[game_id]
                state = game["selected_canonical_state"]
                if not isinstance(state, Mapping):
                    raise ProspectiveAcquisitionError(
                        f"historical game {game_id} lacks canonical schedule state"
                    )
                if state.get("is_final"):
                    witnessed = parse_utc(
                        state.get("captured_at_utc", ""),
                        "schedule completion witnessed_at_utc",
                    )
                    if witnessed > requested_as_of:
                        rejection_counts["completion_witness_after_cutoff"] = (
                            rejection_counts.get("completion_witness_after_cutoff", 0)
                            + len(relevant_by_game[game_id])
                        )
                        continue
                    included_rows.extend(relevant_by_game[game_id])
                    included_clocks.append(
                        {
                            "game_id": game_id,
                            "game_completed_at_utc": "",
                            "completion_evidence_type": "schedule_final_observation",
                            "completion_witnessed_at_utc": utc_text(witnessed),
                            "provider_published_at_utc": "",
                            "first_observed_at_utc": utc_text(accepted_first_observed),
                            "captured_at_utc": utc_text(accepted_captured),
                            "provider_final_status": str(
                                state.get("detailed_state") or "Final"
                            ),
                            "completion_source_request_id": state[
                                "source_request_id"
                            ],
                        }
                    )
                    completion_witness_counts["schedule_final_observation"] = (
                        completion_witness_counts.get(
                            "schedule_final_observation", 0
                        )
                        + 1
                    )
                    continue
                request = EvidenceRequest(
                    request_id=f"statsapi-playbyplay-{game_id}",
                    evidence_class="stable_history",
                    source_name="mlb_game_play_by_play",
                    provider="mlb_statsapi",
                    url=f"https://statsapi.mlb.com/api/v1/game/{game_id}/playByPlay",
                    event_id=game_id,
                )
                record, response = _request_record(
                    temporary, request, provider, requested_as_of=requested_as_of
                )
                records.append(record)
                if (
                    response is None
                    or record.get("availability_status") != "completed"
                    or record.get("raw_persistence_status") != "completed"
                ):
                    rejection_counts["completion_evidence_unavailable"] = (
                        rejection_counts.get("completion_evidence_unavailable", 0)
                        + len(relevant_by_game[game_id])
                    )
                    continue
                try:
                    completed = _completion_time(
                        response.body, expected_game_id=game_id
                    )
                except ProspectiveAcquisitionError as exc:
                    record["evidence_admissibility"] = "rejected"
                    record["evidence_rejection_reason"] = str(exc)
                    integrity_errors.append(str(exc))
                    continue
                if completed > requested_as_of:
                    record["evidence_admissibility"] = "rejected"
                    record["evidence_rejection_reason"] = (
                        "historical game completed after cutoff"
                    )
                    rejection_counts["completed_after_cutoff"] = (
                        rejection_counts.get("completed_after_cutoff", 0)
                        + len(relevant_by_game[game_id])
                    )
                    continue
                record["evidence_admissibility"] = "included"
                included_rows.extend(relevant_by_game[game_id])
                included_clocks.append(
                    {
                        "game_id": game_id,
                        "game_completed_at_utc": utc_text(completed),
                        "completion_evidence_type": "play_by_play_last_play_end",
                        "completion_witnessed_at_utc": str(record["captured_at_utc"]),
                        "provider_published_at_utc": "",
                        "first_observed_at_utc": utc_text(accepted_first_observed),
                        "captured_at_utc": utc_text(accepted_captured),
                        "provider_final_status": "Final",
                        "completion_source_request_id": request.request_id,
                    }
                )
                completion_witness_counts["play_by_play_last_play_end"] = (
                    completion_witness_counts.get(
                        "play_by_play_last_play_end", 0
                    )
                    + 1
                )

        if chunks_complete and not integrity_errors:
            statcast_path = temporary / "eligible_statcast.csv"
            clock_path = temporary / "game_clocks.csv"
            _write_csv(statcast_path, merged_columns, included_rows)
            _write_csv(clock_path, _GAME_CLOCK_COLUMNS, included_clocks)
            for item in (statcast_path, clock_path):
                payload = item.read_bytes()
                derived_files.append(
                    {
                        "path": item.name,
                        "sha256": _sha256(payload),
                        "byte_size": len(payload),
                        "row_count": max(0, payload.count(b"\n") - 1),
                    }
                )

        chunk_manifest_refs = _write_chunk_manifests(temporary, chunk_attempts)
        accepted_chunk_refs = [
            item
            for item in chunk_manifest_refs
            if item.get("chunk_status") == "accepted"
        ]
        called_states = {
            str(item.get("availability_status")) for item in records
        }
        if integrity_errors:
            snapshot_state = "rejected"
        elif not records or called_states == {"unavailable"}:
            snapshot_state = "unavailable"
        elif (
            not schedule_ready
            or leaf_failure_count
            or coverage_gaps
            or rejection_counts.get("completion_evidence_unavailable")
        ):
            snapshot_state = "partial"
        else:
            snapshot_state = "completed"

        included_hitter_ids = sorted(
            {
                str(row["batter"])
                for row in included_rows
                if str(row["batter"]) in hitters
            },
            key=int,
        )
        included_pitcher_ids = sorted(
            {
                str(row["pitcher"])
                for row in included_rows
                if str(row["pitcher"]) in pitchers
            },
            key=int,
        )
        plate_appearances = {
            (str(row["game_pk"]), str(row["at_bat_number"]))
            for row in included_rows
        }
        provider_statcast_records = [
            item
            for item in records
            if item.get("endpoint_class") == "statcast_pitch_history"
        ]
        provider_response_row_count_total = sum(
            int(item.get("response_row_count") or 0) for item in chunk_attempts
        )
        provider_call_count = sum(
            item.get("request_execution") == "provider_call" for item in records
        )
        reused_response_count = sum(
            item.get("request_execution") == "reused_prior_response"
            for item in records
        )
        replayed_response_count = sum(
            item.get("request_execution")
            == "replayed_immutable_response"
            for item in records
        )
        reused_chunk_count = sum(
            item["record"].get("request_execution") == "reused_prior_response"
            for item in accepted_chunks
        )
        replayed_chunk_count = sum(
            item["record"].get("request_execution")
            == "replayed_immutable_response"
            for item in accepted_chunks
        )
        recovered_chunk_count = (
            sum(
                item["record"].get("request_execution") == "provider_call"
                for item in accepted_chunks
            )
            if recovery_provenance is not None
            else 0
        )
        coverage = {
            **admission_accounting,
            "game_count": len(included_clocks),
            "pitch_count": len(included_rows),
            "plate_appearance_count": len(plate_appearances),
            "eligible_hitter_count_requested": len(hitters),
            "eligible_hitter_count_with_history": len(included_hitter_ids),
            "probable_pitcher_count_requested": len(pitchers),
            "probable_pitcher_count_with_history": len(included_pitcher_ids),
            **merge_summary,
            "provider_response_row_count_total": provider_response_row_count_total,
            "provider_statcast_request_count": len(provider_statcast_records),
            "provider_call_count": provider_call_count,
            "reused_prior_response_count": reused_response_count,
            "replayed_immutable_response_count": replayed_response_count,
            "accepted_statcast_chunk_count": len(accepted_chunks),
            "reused_statcast_chunk_count": reused_chunk_count,
            "replayed_statcast_chunk_count": replayed_chunk_count,
            "recovered_statcast_chunk_count": recovered_chunk_count,
            "split_statcast_chunk_count": sum(
                item.get("chunk_status") == "split" for item in chunk_attempts
            ),
            "failed_statcast_leaf_count": leaf_failure_count,
            "requested_date_coverage_gaps": coverage_gaps,
            "overlap_day_count": overlap_days,
            "schedule_row_count": schedule_summary["schedule_row_count"],
            "unique_schedule_game_count": schedule_summary["unique_game_count"],
            "schedule_revision_count": schedule_summary["revision_count"],
            "schedule_reconciled_game_count": schedule_summary[
                "reconciled_game_count"
            ],
            "schedule_identity_conflict_count": schedule_summary[
                "identity_conflict_count"
            ],
            "completion_witness_counts_by_source_type": dict(
                sorted(completion_witness_counts.items())
            ),
        }
        ordered_schedule = [schedule[key] for key in sorted(schedule, key=int)]
        raw_statcast_digests = [
            str(item["record"].get("sha256")) for item in accepted_chunks
        ]
        content = {
            **admission_accounting,
            "request_identity": identity,
            "request_identity_digest": request_identity_digest,
            "provider_requests": records,
            "statcast_chunk_manifests": chunk_manifest_refs,
            "accepted_statcast_chunk_manifests": accepted_chunk_refs,
            "schedule_resolution": ordered_schedule,
            "schedule_summary": schedule_summary,
            "derived_files": derived_files,
            "included_game_clocks": included_clocks,
            "included_hitter_ids": included_hitter_ids,
            "included_pitcher_ids": included_pitcher_ids,
            "eligible_hitter_ids_without_history": sorted(
                set(hitters) - set(included_hitter_ids), key=int
            ),
            "probable_pitcher_ids_without_history": sorted(
                set(pitchers) - set(included_pitcher_ids), key=int
            ),
            "evidence_rejection_counts": dict(sorted(rejection_counts.items())),
            "integrity_errors": integrity_errors,
            "coverage": coverage,
            "snapshot_state": snapshot_state,
        }
        if recovery_provenance is not None:
            content["recovery_provenance"] = recovery_provenance
        if replay_provenance is not None:
            content["replay_provenance"] = replay_provenance
        content_digest = _value_digest(content)
        snapshot_id = "statcast-history-" + content_digest
        manifest: dict[str, object] = {
            **admission_accounting,
            "schema_version": HISTORICAL_STATCAST_SCHEMA_VERSION,
            "snapshot_id": snapshot_id,
            "content_digest": content_digest,
            "content_address_payload": content,
            "request_identity_digest": request_identity_digest,
            "operating_date": operating_date.isoformat(),
            "season_start_date": season_start_date.isoformat(),
            "season_end_date": season_end_date.isoformat(),
            "requested_as_of_utc": utc_text(requested_as_of),
            "first_observed_at_utc": (
                utc_text(accepted_first_observed)
                if accepted_first_observed is not None
                else None
            ),
            "captured_at_utc": (
                utc_text(accepted_captured) if accepted_captured is not None else None
            ),
            "provider": "Baseball Savant Statcast plus MLB StatsAPI completion evidence",
            "query_parameters": {
                "season_start_date": season_start_date.isoformat(),
                "season_end_date": season_end_date.isoformat(),
                "player_type": "batter",
                "grain": "pitch",
                "initial_chunk_days": initial_chunk_days,
                "suspicious_chunk_row_count": suspicious_chunk_row_count,
            },
            "target_game_ids": list(targets),
            "eligible_hitter_ids_requested": list(hitters),
            "probable_pitcher_ids_requested": list(pitchers),
            "included_hitter_ids": included_hitter_ids,
            "included_pitcher_ids": included_pitcher_ids,
            "eligible_hitter_ids_without_history": sorted(
                set(hitters) - set(included_hitter_ids), key=int
            ),
            "probable_pitcher_ids_without_history": sorted(
                set(pitchers) - set(included_pitcher_ids), key=int
            ),
            "included_game_ids": [item["game_id"] for item in included_clocks],
            "included_game_completion_clocks": included_clocks,
            "schedule_resolution": ordered_schedule,
            "schedule_summary": schedule_summary,
            "raw_artifact_digest": (
                _value_digest(raw_statcast_digests) if raw_statcast_digests else None
            ),
            "raw_artifact_digests": raw_statcast_digests,
            "provider_requests": records,
            "provider_call_count": provider_call_count,
            "provider_observation_count": len(records),
            "reused_prior_response_count": reused_response_count,
            "every_provider_request_accounted": all(
                item.get("request_started_at_utc")
                and item.get("request_completed_at_utc")
                and item.get("endpoint_class")
                and item.get("request_result")
                for item in records
            ),
            "statcast_chunk_manifests": chunk_manifest_refs,
            "accepted_statcast_chunk_manifests": accepted_chunk_refs,
            "statcast_requested_ranges": [
                {
                    "request_id": item["request_id"],
                    "start_date": item["start_date"],
                    "end_date": item["end_date"],
                    "chunk_status": item["chunk_status"],
                }
                for item in sorted(
                    chunk_attempts,
                    key=lambda value: (
                        str(value["start_date"]),
                        str(value["end_date"]),
                        str(value["request_id"]),
                    ),
                )
            ],
            "derived_files": derived_files,
            "coverage": coverage,
            "evidence_rejection_counts": dict(sorted(rejection_counts.items())),
            "integrity_errors": integrity_errors,
            "collection_completeness": snapshot_state,
            "snapshot_state": snapshot_state,
            "git_commit": git_commit,
            "research_only": True,
            "model_training_enabled": False,
            "predictions_enabled": False,
            "operational_publication_enabled": False,
            "wagering_enabled": False,
        }
        if recovery_provenance is not None:
            manifest["recovery_provenance"] = recovery_provenance
        manifest["manifest_digest"] = _value_digest(manifest)
        destination = date_root / ("hs-" + content_digest[:20])
        manifest_path = temporary / "historical_statcast_manifest_v1.json"
        manifest_path.write_bytes(_canonical_json(manifest, pretty=True))
        if destination.exists():
            existing_manifest = _validate_snapshot_dir(destination)
            if existing_manifest.get("snapshot_id") != snapshot_id:
                raise ImmutableCaptureConflictError(
                    "conflicting immutable historical Statcast content address"
                )
            shutil.rmtree(temporary)
            return HistoricalStatcastResult(
                snapshot_id=snapshot_id,
                snapshot_dir=destination,
                manifest_path=destination / manifest_path.name,
                snapshot_state=snapshot_state,
                manifest_digest=str(manifest["manifest_digest"]),
                no_op=True,
                provider_call_count=provider_call_count,
                game_count=int(coverage["game_count"]),
                pitch_count=int(coverage["pitch_count"]),
                plate_appearance_count=int(coverage["plate_appearance_count"]),
                reused_chunk_count=reused_chunk_count,
                recovered_chunk_count=recovered_chunk_count,
                completion_witness_counts_by_source_type=dict(
                    sorted(completion_witness_counts.items())
                ),
            )
        temporary.replace(destination)
        published = True
        _validate_snapshot_dir(destination)
        return HistoricalStatcastResult(
            snapshot_id=snapshot_id,
            snapshot_dir=destination,
            manifest_path=destination / manifest_path.name,
            snapshot_state=snapshot_state,
            manifest_digest=str(manifest["manifest_digest"]),
            no_op=False,
            provider_call_count=provider_call_count,
            game_count=int(coverage["game_count"]),
            pitch_count=int(coverage["pitch_count"]),
            plate_appearance_count=int(coverage["plate_appearance_count"]),
            reused_chunk_count=reused_chunk_count,
            recovered_chunk_count=recovered_chunk_count,
            completion_witness_counts_by_source_type=dict(
                sorted(completion_witness_counts.items())
            ),
        )
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)



def replay_historical_statcast_snapshot(
    *,
    source_snapshot: str | Path,
    acquisition_root: str | Path = DEFAULT_ACQUISITION_ROOT,
    git_commit: str,
) -> HistoricalStatcastResult:
    """Re-evaluate one immutable terminal snapshot with zero network access."""

    if not str(git_commit).strip():
        raise ProspectiveAcquisitionError(
            "offline replay requires git_commit"
        )

    (
        _source_path,
        _source_manifest,
        identity,
        base_requests,
        provider,
        replay_provenance,
    ) = _prepare_replay(
        source_snapshot,
        replay_git_commit=str(git_commit),
    )

    try:
        operating_date = date.fromisoformat(
            str(identity["operating_date"])
        )
        season_start_date = date.fromisoformat(
            str(identity["season_start_date"])
        )
        requested_as_of = parse_utc(
            identity["requested_as_of_utc"],
            "replay requested_as_of_utc",
        )
        initial_chunk_days = int(
            identity["initial_chunk_days"]
        )
        suspicious_chunk_row_count = int(
            identity[
                "suspicious_chunk_row_count"
            ]
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        ProspectiveAcquisitionError,
    ) as exc:
        raise ImmutableCaptureConflictError(
            "replay source acquisition identity is malformed"
        ) from exc

    targets = identity.get(
        "target_game_ids"
    )
    hitters = identity.get(
        "eligible_hitter_ids"
    )
    pitchers = identity.get(
        "probable_pitcher_ids"
    )

    if not all(
        isinstance(value, list)
        for value in (
            targets,
            hitters,
            pitchers,
        )
    ):
        raise ImmutableCaptureConflictError(
            "replay source player/game identity is malformed"
        )

    return acquire_historical_statcast_snapshot(
        operating_date=operating_date,
        season_start_date=season_start_date,
        requested_as_of_utc=requested_as_of,
        target_game_ids=tuple(
            str(value)
            for value in targets
        ),
        eligible_hitter_ids=tuple(
            str(value)
            for value in hitters
        ),
        probable_pitcher_ids=tuple(
            str(value)
            for value in pitchers
        ),
        provider=provider,
        acquisition_root=acquisition_root,
        git_commit=str(git_commit),
        base_requests=base_requests,
        initial_chunk_days=initial_chunk_days,
        suspicious_chunk_row_count=(
            suspicious_chunk_row_count
        ),
        _replay_provenance=replay_provenance,
    )


def load_historical_statcast_snapshot(
    manifest_path: str | Path,
    *,
    cutoff_utc: datetime | str,
    target_game_ids: Sequence[str],
    eligible_hitter_ids: Sequence[str],
    probable_pitcher_ids: Sequence[str],
) -> LoadedHistoricalStatcast:
    source = Path(manifest_path).expanduser().resolve()
    manifest = _validate_snapshot_dir(source.parent)
    if source != source.parent / "historical_statcast_manifest_v1.json":
        raise ProspectiveAcquisitionError("historical Statcast manifest filename is invalid")
    if manifest.get("snapshot_state") != "completed":
        raise ProspectiveAcquisitionError("historical Statcast snapshot is not complete")
    cutoff = parse_utc(cutoff_utc, "cutoff_utc")
    first_observed = parse_utc(
        manifest.get("first_observed_at_utc", ""), "Statcast first_observed_at_utc"
    )
    captured = parse_utc(
        manifest.get("captured_at_utc", ""), "Statcast captured_at_utc"
    )
    requested = parse_utc(
        manifest.get("requested_as_of_utc", ""), "Statcast requested_as_of_utc"
    )
    if not (first_observed <= captured <= cutoff <= requested):
        raise ProspectiveAcquisitionError(
            "historical Statcast snapshot is not eligible for the feature cutoff"
        )
    targets = {str(value) for value in target_game_ids}
    included_games = {str(value) for value in manifest.get("included_game_ids") or []}
    if targets & included_games:
        raise ProspectiveAcquisitionError("historical Statcast includes a target game")
    requested_hitters = {
        str(value) for value in manifest.get("eligible_hitter_ids_requested") or []
    }
    requested_pitchers = {
        str(value) for value in manifest.get("probable_pitcher_ids_requested") or []
    }
    if not {str(value) for value in eligible_hitter_ids}.issubset(requested_hitters):
        raise ProspectiveAcquisitionError("historical Statcast hitter identity mismatch")
    if not {str(value) for value in probable_pitcher_ids}.issubset(requested_pitchers):
        raise ProspectiveAcquisitionError("historical Statcast pitcher identity mismatch")
    files = {
        str(item.get("path")): item
        for item in manifest.get("derived_files") or []
        if isinstance(item, Mapping)
    }
    if set(files) != {"eligible_statcast.csv", "game_clocks.csv"}:
        raise ProspectiveAcquisitionError("historical Statcast derived files are incomplete")
    raw_inputs: dict[str, Path] = {"historical_statcast_manifest": source}
    for record in manifest.get("provider_requests") or []:
        if not isinstance(record, Mapping) or not record.get("body_path"):
            continue
        raw_inputs[f"provider_{record['request_id']}"] = (
            source.parent / str(record["body_path"])
        ).resolve()
    return LoadedHistoricalStatcast(
        manifest_path=source,
        manifest=manifest,
        statcast_csv_path=source.parent / "eligible_statcast.csv",
        game_clock_csv_path=source.parent / "game_clocks.csv",
        raw_inputs=raw_inputs,
    )


def persist_request_accounting_manifest(
    *,
    operating_date: date,
    records: Sequence[Mapping[str, object]],
    outcome: str,
    reason: str,
    acquisition_root: str | Path = DEFAULT_ACQUISITION_ROOT,
    git_commit: str,
    evidence_manifest_paths: Sequence[str | Path] = (),
) -> Path:
    """Persist an immutable ledger for control/preflight and evidentiary calls."""

    normalized = [dict(item) for item in records]
    for index, record in enumerate(normalized, start=1):
        missing = [
            field
            for field in (
                "request_id",
                "endpoint_class",
                "started_at_utc",
                "completed_at_utc",
                "status",
            )
            if not record.get(field)
        ]
        if missing:
            raise ProspectiveAcquisitionError(
                f"request accounting row {index} lacks: {', '.join(missing)}"
            )
    evidence_manifests = []
    for value in evidence_manifest_paths:
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ProspectiveAcquisitionError(
                "request accounting evidence manifest is missing"
            )
        raw = path.read_bytes()
        evidence_manifests.append(
            {
                "path": str(path),
                "sha256": _sha256(raw),
                "byte_size": len(raw),
            }
        )
    payload: dict[str, object] = {
        "schema_version": REQUEST_ACCOUNTING_SCHEMA_VERSION,
        "operating_date": operating_date.isoformat(),
        "outcome": outcome,
        "reason": reason,
        "requests": normalized,
        "request_count": len(normalized),
        "evidence_manifests": evidence_manifests,
        "every_provider_request_accounted": True,
        "git_commit": git_commit,
        "research_only": True,
    }
    accounting_id = "request-accounting-" + _value_digest(payload)
    payload["accounting_id"] = accounting_id
    payload["manifest_digest"] = _value_digest(payload)
    root = (
        Path(acquisition_root).expanduser().resolve()
        / "request_accounting"
        / operating_date.isoformat()
    )
    destination = root / ("ra-" + accounting_id.removeprefix("request-accounting-")[:20])
    manifest_path = destination / "request_accounting_manifest_v1.json"
    encoded = _canonical_json(payload, pretty=True)
    if destination.exists():
        if not manifest_path.is_file() or manifest_path.read_bytes() != encoded:
            raise ImmutableCaptureConflictError("conflicting request accounting record")
        return manifest_path
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".t-", dir=root))
    try:
        (temporary / manifest_path.name).write_bytes(encoded)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return manifest_path


__all__ = [
    "DEFAULT_INITIAL_CHUNK_DAYS",
    "DEFAULT_HISTORICAL_STATCAST_ROOT",
    "DEFAULT_SUSPICIOUS_CHUNK_ROW_COUNT",
    "HISTORICAL_STATCAST_SCHEMA_VERSION",
    "HistoricalStatcastResult",
    "LoadedHistoricalStatcast",
    "REQUEST_ACCOUNTING_SCHEMA_VERSION",
    "acquire_historical_statcast_snapshot",
    "build_historical_statcast_requests",
    "load_historical_statcast_snapshot",
    "plan_statcast_date_chunks",
    "persist_request_accounting_manifest",
]
