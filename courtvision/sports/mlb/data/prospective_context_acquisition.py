"""Research-only prospective MLB context evidence acquisition.

The module owns timing, immutable raw-response publication, and provenance.  It
does not score candidates, train models, publish predictions, or call providers
while materializing feature rows.  Provider I/O is injected so automated tests
remain offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from typing import Final, Mapping, Protocol, Sequence
import urllib.request


ACQUISITION_SCHEMA_VERSION: Final = "mlb-hr-prospective-context-acquisition-v1"
RAW_RESPONSE_SCHEMA_VERSION: Final = "mlb-hr-context-raw-response-v1"
DAILY_HISTORY_SCHEMA_VERSION: Final = "mlb-hr-context-daily-history-v1"
CLUSTER_POLICY_VERSION: Final = "mlb-hr-event-cluster-v1"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_ACQUISITION_ROOT: Final = (
    PROJECT_ROOT
    / "data"
    / "research"
    / "mlb_hr_prospective_context_acquisition"
)

_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_MLBAM_ID: Final = re.compile(r"^[1-9]\d{5,9}$")
_SAFE_COMPONENT: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TERMINAL_CAPTURE_STATES: Final = frozenset(
    {"completed", "partial", "unavailable", "rejected"}
)


class ProspectiveAcquisitionError(ValueError):
    """Raised when evidence cannot be acquired without violating its contract."""


class ImmutableCaptureConflictError(ProspectiveAcquisitionError):
    """Raised when an existing immutable capture conflicts with its manifest."""


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    """Declared timing and deterministic clustering policy."""

    target_lead_minutes: int = 60
    minimum_lead_minutes: int = 45
    maximum_lead_minutes: int = 90
    cluster_window_minutes: int = 10

    def __post_init__(self) -> None:
        values = (
            self.target_lead_minutes,
            self.minimum_lead_minutes,
            self.maximum_lead_minutes,
            self.cluster_window_minutes,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ProspectiveAcquisitionError("timing policy values must be integers")
        if not (
            0 < self.minimum_lead_minutes
            <= self.target_lead_minutes
            <= self.maximum_lead_minutes
        ):
            raise ProspectiveAcquisitionError(
                "timing policy must satisfy 0 < minimum <= target <= maximum"
            )
        if self.cluster_window_minutes < 0:
            raise ProspectiveAcquisitionError("cluster_window_minutes cannot be negative")

    def as_dict(self) -> dict[str, object]:
        return {
            "policy_version": CLUSTER_POLICY_VERSION,
            "target_lead_minutes": self.target_lead_minutes,
            "minimum_lead_minutes": self.minimum_lead_minutes,
            "maximum_lead_minutes": self.maximum_lead_minutes,
            "cluster_window_minutes": self.cluster_window_minutes,
        }


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    event_id: str
    operating_date: date
    scheduled_start_utc: datetime
    away_team_id: str
    away_team: str
    home_team_id: str
    home_team: str
    venue_id: str
    venue_name: str
    status: str
    away_probable_pitcher_id: str | None = None
    home_probable_pitcher_id: str | None = None

    def identity_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "operating_date": self.operating_date.isoformat(),
            "scheduled_start_utc": utc_text(self.scheduled_start_utc),
            "away_team_id": self.away_team_id,
            "away_team": self.away_team,
            "home_team_id": self.home_team_id,
            "home_team": self.home_team,
            "venue_id": self.venue_id,
            "venue_name": self.venue_name,
            "status": self.status,
            "away_probable_pitcher_id": self.away_probable_pitcher_id,
            "home_probable_pitcher_id": self.home_probable_pitcher_id,
        }

    def cluster_identity_payload(self) -> dict[str, object]:
        """Stable event fields only; volatile status/pitcher changes cannot recluster."""
        return {
            "event_id": self.event_id,
            "operating_date": self.operating_date.isoformat(),
            "scheduled_start_utc": utc_text(self.scheduled_start_utc),
            "away_team_id": self.away_team_id,
            "away_team": self.away_team,
            "home_team_id": self.home_team_id,
            "home_team": self.home_team,
            "venue_id": self.venue_id,
            "venue_name": self.venue_name,
        }


@dataclass(frozen=True, slots=True)
class EventCluster:
    cluster_id: str
    operating_date: date
    events: tuple[ScheduledEvent, ...]
    window_opens_at_utc: datetime
    target_at_utc: datetime
    window_closes_at_utc: datetime

    @property
    def first_start_utc(self) -> datetime:
        return self.events[0].scheduled_start_utc

    @property
    def last_start_utc(self) -> datetime:
        return self.events[-1].scheduled_start_utc


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    request_id: str
    evidence_class: str
    source_name: str
    provider: str
    url: str
    event_id: str | None = None
    player_id: str | None = None
    headers: Mapping[str, str] = MappingProxyType({})

    def __post_init__(self) -> None:
        for field_name, value in (
            ("request_id", self.request_id),
            ("source_name", self.source_name),
            ("provider", self.provider),
        ):
            if not value or not _SAFE_COMPONENT.fullmatch(value):
                raise ProspectiveAcquisitionError(
                    f"{field_name} must be a safe non-empty token"
                )
        if self.evidence_class not in {"stable_history", "volatile_pregame"}:
            raise ProspectiveAcquisitionError("unsupported evidence_class")
        if not self.url.startswith(("https://", "mock://")):
            raise ProspectiveAcquisitionError("evidence request URL must use HTTPS")

    def identity_payload(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "evidence_class": self.evidence_class,
            "source_name": self.source_name,
            "provider": self.provider,
            "url": self.url,
            "event_id": self.event_id,
            "player_id": self.player_id,
            "headers": dict(sorted(self.headers.items())),
        }


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    body: bytes
    status_code: int
    headers: Mapping[str, str]
    first_observed_at_utc: datetime
    captured_at_utc: datetime
    provider_published_at_utc: datetime | None = None


class EvidenceProvider(Protocol):
    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        """Fetch exactly one declared request and return its raw response."""


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    capture_id: str
    capture_dir: Path
    manifest_path: Path
    capture_state: str
    no_op: bool
    provider_call_count: int


@dataclass(frozen=True, slots=True)
class DailyHistoryResult:
    history_snapshot_id: str
    snapshot_dir: Path
    manifest_path: Path
    snapshot_state: str
    no_op: bool
    provider_call_count: int

    def reference(self) -> dict[str, object]:
        return {
            "history_snapshot_id": self.history_snapshot_id,
            "manifest_path": str(self.manifest_path),
            "snapshot_state": self.snapshot_state,
        }


class HttpEvidenceProvider:
    """Small explicit HTTP provider used only by an authorized acquisition CLI."""

    def __init__(self, *, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ProspectiveAcquisitionError("timeout_seconds must be positive")
        self._timeout_seconds = timeout_seconds

    def fetch(self, request: EvidenceRequest) -> ProviderResponse:
        http_request = urllib.request.Request(
            request.url,
            headers={
                "User-Agent": "CourtVision-Prospective-Context-Research/1.0",
                **dict(request.headers),
            },
        )
        with urllib.request.urlopen(  # noqa: S310 - explicit HTTPS contract above
            http_request, timeout=self._timeout_seconds
        ) as response:
            body = response.read()
            captured = datetime.now(timezone.utc)
            headers = {str(key): str(value) for key, value in response.headers.items()}
            published = _http_header_datetime(headers.get("Last-Modified"))
            return ProviderResponse(
                body=body,
                status_code=int(response.status),
                headers=MappingProxyType(headers),
                # The response bytes do not exist at request start.  With no
                # provider publication clock, first observation is the instant
                # the complete response was captured.
                first_observed_at_utc=captured,
                captured_at_utc=captured,
                provider_published_at_utc=published,
            )


def parse_utc(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProspectiveAcquisitionError(
                f"{field_name} must be an ISO-8601 timestamp"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveAcquisitionError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def utc_text(value: datetime | str) -> str:
    return parse_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _http_header_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parse_utc(parsedate_to_datetime(value), "Last-Modified")
    except (TypeError, ValueError, ProspectiveAcquisitionError):
        return None


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
        indent=2 if pretty else None,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_value(value: object) -> str:
    return _sha256_bytes(_canonical_json(value))


def _write_raw_file(path: Path, payload: bytes) -> None:
    """Single injectable boundary for surfacing response persistence failures."""

    path.write_bytes(payload)


def _required_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProspectiveAcquisitionError(f"{field_name} must be an object")
    return value


def _required_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not _MLBAM_ID.fullmatch(text):
        raise ProspectiveAcquisitionError(f"{field_name} must be a canonical MLBAM id")
    return text


def _numeric_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text.isdigit() or int(text) <= 0:
        raise ProspectiveAcquisitionError(f"{field_name} must be a positive numeric id")
    return text


def _optional_id(value: object, field_name: str) -> str | None:
    text = "" if value is None else str(value).strip()
    return _required_id(text, field_name) if text else None


def parse_mlb_schedule(
    raw_json: bytes,
    *,
    operating_date: date,
) -> tuple[ScheduledEvent, ...]:
    """Parse a complete StatsAPI schedule without weakening event identity."""

    try:
        payload = json.loads(raw_json.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError("MLB schedule response is not valid JSON") from exc
    root = _required_mapping(payload, "MLB schedule")
    dates = root.get("dates")
    if not isinstance(dates, list):
        raise ProspectiveAcquisitionError("MLB schedule dates must be an array")
    events: list[ScheduledEvent] = []
    seen: set[str] = set()
    for date_payload in dates:
        date_record = _required_mapping(date_payload, "MLB schedule date")
        games = date_record.get("games")
        if not isinstance(games, list):
            raise ProspectiveAcquisitionError("MLB schedule games must be an array")
        for game_payload in games:
            game = _required_mapping(game_payload, "MLB schedule game")
            official_date = date.fromisoformat(str(game.get("officialDate")))
            if official_date != operating_date:
                continue
            event_id = _required_id(game.get("gamePk"), "schedule.gamePk")
            if event_id in seen:
                raise ProspectiveAcquisitionError(f"duplicate schedule event: {event_id}")
            seen.add(event_id)
            teams = _required_mapping(game.get("teams"), "schedule.teams")
            away = _required_mapping(teams.get("away"), "schedule.teams.away")
            home = _required_mapping(teams.get("home"), "schedule.teams.home")
            away_team = _required_mapping(away.get("team"), "schedule.away.team")
            home_team = _required_mapping(home.get("team"), "schedule.home.team")
            venue = _required_mapping(game.get("venue"), "schedule.venue")
            status = _required_mapping(game.get("status"), "schedule.status")
            events.append(
                ScheduledEvent(
                    event_id=event_id,
                    operating_date=official_date,
                    scheduled_start_utc=parse_utc(game.get("gameDate", ""), "gameDate"),
                    away_team_id=_numeric_id(away_team.get("id"), "away.team.id"),
                    away_team=str(away_team.get("name") or "").strip(),
                    home_team_id=_numeric_id(home_team.get("id"), "home.team.id"),
                    home_team=str(home_team.get("name") or "").strip(),
                    venue_id=_numeric_id(venue.get("id"), "venue.id"),
                    venue_name=str(venue.get("name") or "").strip(),
                    status=str(status.get("detailedState") or "unknown").strip(),
                    away_probable_pitcher_id=_optional_id(
                        _required_mapping(away.get("probablePitcher") or {}, "away.probable")
                        .get("id"),
                        "away.probablePitcher.id",
                    ),
                    home_probable_pitcher_id=_optional_id(
                        _required_mapping(home.get("probablePitcher") or {}, "home.probable")
                        .get("id"),
                        "home.probablePitcher.id",
                    ),
                )
            )
    if not events:
        raise ProspectiveAcquisitionError(
            f"MLB schedule contains no games for {operating_date.isoformat()}"
        )
    if any(not event.away_team or not event.home_team or not event.venue_name for event in events):
        raise ProspectiveAcquisitionError("MLB schedule identity fields cannot be blank")
    return tuple(sorted(events, key=lambda item: (item.scheduled_start_utc, item.event_id)))


def build_event_clusters(
    events: Sequence[ScheduledEvent],
    *,
    policy: AcquisitionPolicy = AcquisitionPolicy(),
) -> tuple[EventCluster, ...]:
    """Create deterministic clusters while preserving each event's timing window."""

    if not events:
        return ()
    ordered = sorted(events, key=lambda item: (item.scheduled_start_utc, item.event_id))
    operating_dates = {item.operating_date for item in ordered}
    if len(operating_dates) != 1:
        raise ProspectiveAcquisitionError("one cluster build cannot span operating dates")
    grouped: list[list[ScheduledEvent]] = []
    for event in ordered:
        if not grouped or (
            event.scheduled_start_utc - grouped[-1][-1].scheduled_start_utc
            > timedelta(minutes=policy.cluster_window_minutes)
        ):
            grouped.append([event])
        else:
            grouped[-1].append(event)
    clusters: list[EventCluster] = []
    for group in grouped:
        group_tuple = tuple(group)
        # Every event must be inside its own window at the shared cluster capture.
        opens = max(
            item.scheduled_start_utc - timedelta(minutes=policy.maximum_lead_minutes)
            for item in group_tuple
        )
        closes = min(
            item.scheduled_start_utc - timedelta(minutes=policy.minimum_lead_minutes)
            for item in group_tuple
        )
        if opens > closes:
            raise ProspectiveAcquisitionError(
                "cluster has no shared admissible window; reduce cluster_window_minutes"
            )
        targets = sorted(
            item.scheduled_start_utc - timedelta(minutes=policy.target_lead_minutes)
            for item in group_tuple
        )
        target = targets[len(targets) // 2]
        target = min(max(target, opens), closes)
        identity = {
            "policy": policy.as_dict(),
            "events": [item.cluster_identity_payload() for item in group_tuple],
            "window_opens_at_utc": utc_text(opens),
            "target_at_utc": utc_text(target),
            "window_closes_at_utc": utc_text(closes),
        }
        clusters.append(
            EventCluster(
                cluster_id="cl-" + _sha256_value(identity)[:12],
                operating_date=group_tuple[0].operating_date,
                events=group_tuple,
                window_opens_at_utc=opens,
                target_at_utc=target,
                window_closes_at_utc=closes,
            )
        )
    return tuple(clusters)


def classify_cluster_time(cluster: EventCluster, observed_at_utc: datetime | str) -> str:
    observed = parse_utc(observed_at_utc, "observed_at_utc")
    if observed >= cluster.first_start_utc:
        return "game_started"
    if observed < cluster.window_opens_at_utc:
        return "early"
    if observed > cluster.window_closes_at_utc:
        return "missed"
    return "admissible"


def validate_game_feed_identity(raw_json: bytes, event: ScheduledEvent) -> Mapping[str, object]:
    try:
        payload = json.loads(raw_json.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError("StatsAPI game feed is not valid JSON") from exc
    root = _required_mapping(payload, "StatsAPI game feed")
    if _required_id(root.get("gamePk"), "feed.gamePk") != event.event_id:
        raise ProspectiveAcquisitionError("conflicting game identity in StatsAPI feed")
    game_data = _required_mapping(root.get("gameData"), "feed.gameData")
    datetime_payload = _required_mapping(game_data.get("datetime"), "feed.datetime")
    if parse_utc(datetime_payload.get("dateTime", ""), "feed.datetime.dateTime") != event.scheduled_start_utc:
        raise ProspectiveAcquisitionError("conflicting scheduled start in StatsAPI feed")
    teams = _required_mapping(game_data.get("teams"), "feed.gameData.teams")
    for side, expected_id in (
        ("away", event.away_team_id),
        ("home", event.home_team_id),
    ):
        team = _required_mapping(teams.get(side), f"feed.teams.{side}")
        if _numeric_id(team.get("id"), f"feed.teams.{side}.id") != expected_id:
            raise ProspectiveAcquisitionError("conflicting team identity in StatsAPI feed")
    venue = _required_mapping(game_data.get("venue"), "feed.gameData.venue")
    if _numeric_id(venue.get("id"), "feed.venue.id") != event.venue_id:
        raise ProspectiveAcquisitionError("conflicting venue identity in StatsAPI feed")
    return root


def validate_probable_pitcher_identity(
    raw_json: bytes,
    event: ScheduledEvent,
) -> dict[str, str | None]:
    payload = validate_game_feed_identity(raw_json, event)
    game_data = _required_mapping(payload.get("gameData"), "feed.gameData")
    probable = _required_mapping(
        game_data.get("probablePitchers") or {}, "feed.gameData.probablePitchers"
    )
    result: dict[str, str | None] = {}
    for side, expected in (
        ("away", event.away_probable_pitcher_id),
        ("home", event.home_probable_pitcher_id),
    ):
        record = _required_mapping(probable.get(side) or {}, f"feed.probable.{side}")
        observed = _optional_id(record.get("id"), f"feed.probable.{side}.id")
        if expected is not None and observed is not None and expected != observed:
            raise ProspectiveAcquisitionError("conflicting probable pitcher identity")
        result[side] = observed or expected
    return result


def lineup_coverage(raw_json: bytes, event: ScheduledEvent) -> dict[str, int]:
    payload = validate_game_feed_identity(raw_json, event)
    live_data = _required_mapping(payload.get("liveData"), "feed.liveData")
    boxscore = _required_mapping(live_data.get("boxscore"), "feed.liveData.boxscore")
    teams = _required_mapping(boxscore.get("teams"), "feed.boxscore.teams")
    result: dict[str, int] = {}
    for side in ("away", "home"):
        team = _required_mapping(teams.get(side), f"feed.boxscore.{side}")
        order = team.get("battingOrder") or []
        if not isinstance(order, list):
            raise ProspectiveAcquisitionError("StatsAPI battingOrder must be an array")
        ids = [_required_id(value, f"feed.{side}.battingOrder") for value in order]
        if len(ids) != len(set(ids)) or len(ids) > 9:
            raise ProspectiveAcquisitionError("invalid StatsAPI batting order")
        result[side] = len(ids)
    return result


def validate_weather_observation(
    observation: Mapping[str, object],
    *,
    event: ScheduledEvent,
    requested_as_of_utc: datetime | str,
    tolerance: timedelta = timedelta(hours=1),
) -> None:
    if str(observation.get("event_id") or "") != event.event_id:
        raise ProspectiveAcquisitionError("weather event identity mismatch")
    if str(observation.get("venue_id") or "") != event.venue_id:
        raise ProspectiveAcquisitionError("weather venue identity mismatch")
    issued = parse_utc(observation.get("issued_at_utc", ""), "weather.issued_at_utc")
    valid_for = parse_utc(observation.get("valid_for_utc", ""), "weather.valid_for_utc")
    first_observed = parse_utc(
        observation.get("first_observed_at_utc", ""),
        "weather.first_observed_at_utc",
    )
    captured = parse_utc(observation.get("captured_at_utc", ""), "weather.captured_at_utc")
    cutoff = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    if not (issued <= first_observed <= captured <= cutoff):
        raise ProspectiveAcquisitionError("weather observation has inadmissible clocks")
    if abs(valid_for - event.scheduled_start_utc) > tolerance:
        raise ProspectiveAcquisitionError("weather forecast is valid for wrong time")
    for field_name in ("temperature_unit", "wind_speed_unit"):
        if not str(observation.get(field_name) or "").strip():
            raise ProspectiveAcquisitionError(f"weather {field_name} is required")


def normalize_nws_hourly_forecast(
    raw_json: bytes,
    *,
    event: ScheduledEvent,
    first_observed_at_utc: datetime | str,
    captured_at_utc: datetime | str,
    requested_as_of_utc: datetime | str,
) -> dict[str, object]:
    """Select the genuine NWS hourly period that covers scheduled first pitch."""

    try:
        payload = json.loads(raw_json.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError("NWS hourly forecast is not valid JSON") from exc
    root = _required_mapping(payload, "NWS hourly forecast")
    properties = _required_mapping(root.get("properties"), "NWS forecast.properties")
    generated = parse_utc(properties.get("generatedAt", ""), "NWS generatedAt")
    periods = properties.get("periods")
    if not isinstance(periods, list):
        raise ProspectiveAcquisitionError("NWS hourly periods must be an array")
    selected: Mapping[str, object] | None = None
    for value in periods:
        period = _required_mapping(value, "NWS hourly period")
        start = parse_utc(period.get("startTime", ""), "NWS period.startTime")
        end = parse_utc(period.get("endTime", ""), "NWS period.endTime")
        if start <= event.scheduled_start_utc < end:
            if selected is not None:
                raise ProspectiveAcquisitionError("ambiguous NWS hourly forecast period")
            selected = period
    if selected is None:
        raise ProspectiveAcquisitionError("NWS forecast is valid for wrong time")
    wind_text = str(selected.get("windSpeed") or "").strip().casefold()
    match = re.fullmatch(r"(?P<speed>\d+(?:\.\d+)?)\s*(?P<unit>mph|km/h)", wind_text)
    if match is None:
        raise ProspectiveAcquisitionError("NWS wind speed is not one exact numeric value")
    temperature_unit = str(selected.get("temperatureUnit") or "").strip().upper()
    if temperature_unit not in {"F", "C"}:
        raise ProspectiveAcquisitionError("NWS temperature unit is unsupported")
    humidity_payload = selected.get("relativeHumidity")
    humidity = (
        humidity_payload.get("value") if isinstance(humidity_payload, Mapping) else None
    )
    observation = {
        "event_id": event.event_id,
        "venue_id": event.venue_id,
        "venue_name": event.venue_name,
        "weather_type": "forecast",
        "weather_evidence_class": "provider_pregame_forecast",
        "issued_at_utc": utc_text(generated),
        "valid_for_utc": utc_text(selected.get("startTime", "")),
        "valid_until_utc": utc_text(selected.get("endTime", "")),
        "first_observed_at_utc": utc_text(first_observed_at_utc),
        "captured_at_utc": utc_text(captured_at_utc),
        "temperature": selected.get("temperature"),
        "temperature_unit": "fahrenheit" if temperature_unit == "F" else "celsius",
        "wind_speed": match.group("speed"),
        "wind_speed_unit": "mph" if match.group("unit") == "mph" else "kmh",
        "wind_direction": str(selected.get("windDirection") or "").strip(),
        "humidity": humidity,
        "source": "US National Weather Service hourly forecast",
        "source_record_id": str(selected.get("number") or ""),
        "source_version": "api.weather.gov-gridpoints-hourly",
    }
    validate_weather_observation(
        observation,
        event=event,
        requested_as_of_utc=requested_as_of_utc,
    )
    return observation


def validate_park_observation(
    observation: Mapping[str, object],
    *,
    event: ScheduledEvent,
    requested_as_of_utc: datetime | str,
) -> None:
    if str(observation.get("venue_id") or "") != event.venue_id:
        raise ProspectiveAcquisitionError("park venue identity mismatch")
    effective_from = date.fromisoformat(str(observation.get("effective_from_date") or ""))
    effective_to_text = str(observation.get("effective_to_date") or "").strip()
    effective_to = date.fromisoformat(effective_to_text) if effective_to_text else None
    if event.operating_date < effective_from or (
        effective_to is not None and event.operating_date > effective_to
    ):
        raise ProspectiveAcquisitionError("incorrect park effective date")
    available = parse_utc(
        observation.get("published_or_available_at_utc", ""),
        "park.published_or_available_at_utc",
    )
    captured = parse_utc(observation.get("captured_at_utc", ""), "park.captured_at_utc")
    cutoff = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    if not (available <= captured <= cutoff):
        raise ProspectiveAcquisitionError("park observation has inadmissible clocks")
    if not str(observation.get("version") or "").strip():
        raise ProspectiveAcquisitionError("park observation requires a source version")


def validate_historical_statcast_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    completed_game_ids: set[str],
    requested_as_of_utc: datetime | str,
    first_observed_at_utc: datetime | str,
    captured_at_utc: datetime | str,
) -> None:
    cutoff = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    first_observed = parse_utc(first_observed_at_utc, "first_observed_at_utc")
    captured = parse_utc(captured_at_utc, "captured_at_utc")
    if not (first_observed <= captured <= cutoff):
        raise ProspectiveAcquisitionError("historical Statcast captured after cutoff")
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, start=1):
        game_id = _required_id(row.get("game_pk") or row.get("game_id"), "statcast.game_id")
        if game_id not in completed_game_ids:
            raise ProspectiveAcquisitionError("future Statcast game leakage")
        game_date = date.fromisoformat(str(row.get("game_date") or ""))
        if game_date >= cutoff.date():
            raise ProspectiveAcquisitionError("future Statcast game leakage")
        at_bat = str(row.get("at_bat_number") or "").strip()
        pitch_number = str(row.get("pitch_number") or "").strip()
        key = (game_id, at_bat, pitch_number)
        if not at_bat or not pitch_number or key in seen:
            raise ProspectiveAcquisitionError(
                f"Statcast pitch identity is missing or duplicated at row {index}"
            )
        seen.add(key)


def completed_game_ids_from_schedule(
    raw_json: bytes,
    *,
    requested_as_of_utc: datetime | str,
) -> set[str]:
    """Return only games explicitly observed final before the requested cutoff."""

    cutoff = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    try:
        payload = json.loads(raw_json.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError("completed-game schedule is not valid JSON") from exc
    root = _required_mapping(payload, "completed-game schedule")
    dates = root.get("dates")
    if not isinstance(dates, list):
        raise ProspectiveAcquisitionError("completed-game schedule dates must be an array")
    completed: set[str] = set()
    for date_payload in dates:
        record = _required_mapping(date_payload, "completed-game schedule date")
        games = record.get("games")
        if not isinstance(games, list):
            raise ProspectiveAcquisitionError("completed-game schedule games must be an array")
        for game_payload in games:
            game = _required_mapping(game_payload, "completed-game schedule game")
            status = _required_mapping(game.get("status"), "completed-game status")
            if str(status.get("abstractGameState") or "").casefold() != "final":
                continue
            game_date = parse_utc(game.get("gameDate", ""), "completed-game gameDate")
            if game_date >= cutoff:
                raise ProspectiveAcquisitionError("future completed-game schedule leakage")
            completed.add(_required_id(game.get("gamePk"), "completed-game gamePk"))
    return completed


def _statcast_rows(raw_csv: bytes) -> tuple[dict[str, str], ...]:
    try:
        text = raw_csv.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ProspectiveAcquisitionError("Statcast history is not UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None:
        raise ProspectiveAcquisitionError("Statcast history lacks a CSV header")
    required = {"game_date", "at_bat_number", "pitch_number"}
    if not required.issubset(reader.fieldnames) or not {
        "game_pk",
        "game_id",
    }.intersection(reader.fieldnames):
        raise ProspectiveAcquisitionError("Statcast history lacks pitch identity columns")
    return tuple(dict(row) for row in reader)


def _capture_identity(
    cluster: EventCluster,
    requested_as_of: datetime,
    requests: Sequence[EvidenceRequest],
) -> dict[str, object]:
    return {
        "schema_version": ACQUISITION_SCHEMA_VERSION,
        "cluster_id": cluster.cluster_id,
        "operating_date": cluster.operating_date.isoformat(),
        "requested_as_of_utc": utc_text(requested_as_of),
        "events": [item.cluster_identity_payload() for item in cluster.events],
        "requests": [item.identity_payload() for item in requests],
    }


def _capture_destination(
    root: Path, cluster: EventCluster, capture_id: str
) -> Path:
    digest = capture_id.removeprefix("capture-")
    return root / cluster.operating_date.isoformat() / cluster.cluster_id / ("c-" + digest[:20])


def _validate_existing_capture(destination: Path, expected_capture_id: str) -> str:
    manifest_path = destination / "acquisition_manifest_v1.json"
    if not manifest_path.is_file():
        raise ImmutableCaptureConflictError("existing capture lacks its manifest")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ImmutableCaptureConflictError("existing capture manifest is invalid") from exc
    if payload.get("capture_id") != expected_capture_id:
        raise ImmutableCaptureConflictError("existing capture id conflicts with request")
    state = str(payload.get("capture_state") or "")
    if state not in _TERMINAL_CAPTURE_STATES:
        raise ImmutableCaptureConflictError("existing capture is not terminal")
    manifest_digest = str(payload.get("manifest_digest") or "")
    without_digest = dict(payload)
    without_digest.pop("manifest_digest", None)
    if not _SHA256.fullmatch(manifest_digest) or _sha256_value(without_digest) != manifest_digest:
        raise ImmutableCaptureConflictError("existing capture manifest digest mismatch")
    expected_files: set[str] = {"acquisition_manifest_v1.json"}
    for source in payload.get("sources") or []:
        if not isinstance(source, Mapping) or not source.get("body_path"):
            continue
        relative = str(source["body_path"])
        path = destination / relative
        if not path.is_file() or _sha256_bytes(path.read_bytes()) != source.get("sha256"):
            raise ImmutableCaptureConflictError("existing raw response digest mismatch")
        expected_files.add(relative.replace("/", "\\"))
        metadata = str(source.get("metadata_path") or "")
        if metadata:
            metadata_path = destination / metadata
            if not metadata_path.is_file():
                raise ImmutableCaptureConflictError("existing raw metadata is missing")
            expected_files.add(metadata.replace("/", "\\"))
    actual_files = {
        str(path.relative_to(destination)) for path in destination.rglob("*") if path.is_file()
    }
    if actual_files != expected_files:
        raise ImmutableCaptureConflictError("existing capture has unbound files")
    return state


def _source_record(
    temporary: Path,
    request: EvidenceRequest,
    response: ProviderResponse,
    *,
    requested_as_of: datetime,
    cluster: EventCluster,
    request_started_at_utc: datetime | None = None,
) -> dict[str, object]:
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
        state = "partial"
        note = "provider returned HTTP 206 partial content"
    elif response.status_code < 200 or response.status_code >= 300:
        state = "unavailable"
        note = f"provider returned HTTP {response.status_code}"
    elif not (availability <= first_observed <= captured <= requested_as_of):
        state = "rejected"
        note = "post-cutoff or inconsistent observation clocks"
    if request.evidence_class == "volatile_pregame" and request.event_id:
        event = next((item for item in cluster.events if item.event_id == request.event_id), None)
        if event is None:
            raise ProspectiveAcquisitionError("volatile request event is outside cluster")
        if captured >= event.scheduled_start_utc:
            state = "rejected"
            note = "response captured after game start"
        if not cluster.window_opens_at_utc <= first_observed <= cluster.window_closes_at_utc:
            state = "rejected"
            note = "volatile observation outside declared admissible window"
    digest = _sha256_bytes(response.body)
    raw_dir = temporary / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    body_path = raw_dir / f"{digest[:24]}.body"
    if body_path.exists() and body_path.read_bytes() != response.body:
        raise ImmutableCaptureConflictError("raw digest prefix collision")
    if not body_path.exists():
        _write_raw_file(body_path, response.body)
    metadata = {
        "schema_version": RAW_RESPONSE_SCHEMA_VERSION,
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
    metadata_path = raw_dir / f"{digest[:24]}.json"
    metadata_payload = _canonical_json(metadata, pretty=True)
    if metadata_path.exists() and metadata_path.read_bytes() != metadata_payload:
        raise ImmutableCaptureConflictError("raw metadata digest prefix collision")
    if not metadata_path.exists():
        _write_raw_file(metadata_path, metadata_payload)
    return {
        "request_id": request.request_id,
        "evidence_class": request.evidence_class,
        "source_name": request.source_name,
        "provider": request.provider,
        "event_id": request.event_id,
        "player_id": request.player_id,
        "endpoint_class": request.source_name,
        "request_started_at_utc": utc_text(request_started_at_utc or first_observed),
        "request_completed_at_utc": utc_text(captured),
        "request_result": "response_preserved",
        "availability_status": state,
        "availability_note": note,
        "provider_published_at_utc": metadata["provider_published_at_utc"],
        "first_observed_at_utc": metadata["first_observed_at_utc"],
        "captured_at_utc": metadata["captured_at_utc"],
        "requested_as_of_utc": metadata["requested_as_of_utc"],
        "sha256": digest,
        "byte_size": len(response.body),
        "body_path": body_path.relative_to(temporary).as_posix(),
        "metadata_path": metadata_path.relative_to(temporary).as_posix(),
        "raw_persistence_status": "completed",
    }


def _failed_request_record(
    request: EvidenceRequest,
    *,
    requested_as_of: datetime,
    started_at: datetime,
    completed_at: datetime,
    exc: Exception,
    response: ProviderResponse | None = None,
) -> dict[str, object]:
    persistence_failure = response is not None and isinstance(exc, OSError)
    validation_failure = response is not None and not persistence_failure
    return {
        "request_id": request.request_id,
        "evidence_class": request.evidence_class,
        "source_name": request.source_name,
        "provider": request.provider,
        "event_id": request.event_id,
        "player_id": request.player_id,
        "endpoint_class": request.source_name,
        "request_started_at_utc": utc_text(started_at),
        "request_completed_at_utc": utc_text(completed_at),
        "request_result": (
            "response_persistence_failed"
            if persistence_failure
            else "response_validation_failed"
            if validation_failure
            else "provider_error"
        ),
        "availability_status": "unavailable",
        "availability_note": (
            f"response-body persistence failure: {type(exc).__name__}"
            if persistence_failure
            else f"response validation failure: {type(exc).__name__}: {exc}"
            if validation_failure
            else f"provider failure: {type(exc).__name__}"
        ),
        "error_type": type(exc).__name__,
        "provider_published_at_utc": None,
        "first_observed_at_utc": (
            utc_text(response.first_observed_at_utc) if response is not None else None
        ),
        "captured_at_utc": (
            utc_text(response.captured_at_utc) if response is not None else None
        ),
        "requested_as_of_utc": utc_text(requested_as_of),
        "sha256": _sha256_bytes(response.body) if response is not None else None,
        "byte_size": len(response.body) if response is not None else None,
        "body_path": None,
        "metadata_path": None,
        "raw_persistence_status": (
            "failed"
            if persistence_failure
            else "not_persisted"
            if validation_failure
            else "not_available"
        ),
    }


def acquire_event_cluster(
    cluster: EventCluster,
    *,
    requested_as_of_utc: datetime | str,
    observed_at_utc: datetime | str,
    requests: Sequence[EvidenceRequest],
    provider: EvidenceProvider,
    acquisition_root: str | Path = DEFAULT_ACQUISITION_ROOT,
    git_commit: str,
    daily_history_reference: Mapping[str, object] | None = None,
) -> AcquisitionResult:
    """Acquire and atomically publish one immutable event-cluster capture."""

    observed = parse_utc(observed_at_utc, "observed_at_utc")
    requested_as_of = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    timing = classify_cluster_time(cluster, observed)
    if timing != "admissible":
        if timing == "game_started":
            raise ProspectiveAcquisitionError(
                "game already started; pregame evidence will not be backfilled"
            )
        if timing == "missed":
            raise ProspectiveAcquisitionError("missed acquisition window")
        raise ProspectiveAcquisitionError("acquisition window has not opened")
    if requested_as_of < observed:
        raise ProspectiveAcquisitionError("requested as_of cutoff precedes acquisition")
    request_ids = [item.request_id for item in requests]
    if len(request_ids) != len(set(request_ids)):
        raise ProspectiveAcquisitionError("duplicate evidence request id")
    if any(item.evidence_class != "volatile_pregame" for item in requests):
        raise ProspectiveAcquisitionError(
            "event-cluster capture accepts volatile pregame requests only"
        )
    identity = _capture_identity(cluster, requested_as_of, requests)
    capture_id = "capture-" + _sha256_value(identity)
    root = Path(acquisition_root).expanduser().resolve()
    destination = _capture_destination(root, cluster, capture_id)
    if destination.exists():
        state = _validate_existing_capture(destination, capture_id)
        return AcquisitionResult(
            capture_id=capture_id,
            capture_dir=destination,
            manifest_path=destination / "acquisition_manifest_v1.json",
            capture_state=state,
            no_op=True,
            provider_call_count=0,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".t-", dir=destination.parent)
    )
    published = False
    source_records: list[dict[str, object]] = []
    call_count = 0
    try:
        for request in requests:
            started_at = datetime.now(timezone.utc)
            response: ProviderResponse | None = None
            try:
                call_count += 1
                response = provider.fetch(request)
                record = _source_record(
                    temporary,
                    request,
                    response,
                    requested_as_of=requested_as_of,
                    cluster=cluster,
                    request_started_at_utc=started_at,
                )
            except Exception as exc:  # provider and persistence failures stay visible
                source_records.append(
                    _failed_request_record(
                        request,
                        requested_as_of=requested_as_of,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                        exc=exc,
                        response=response,
                    )
                )
                continue
            source_records.append(record)
            if (
                request.source_name == "mlb_statsapi_game_feed"
                and record["availability_status"] == "completed"
            ):
                event = next(item for item in cluster.events if item.event_id == request.event_id)
                try:
                    validate_probable_pitcher_identity(response.body, event)
                    record["lineup_slots_by_side"] = lineup_coverage(response.body, event)
                except ProspectiveAcquisitionError as exc:
                    record["availability_status"] = "rejected"
                    record["availability_note"] = str(exc)
            elif (
                request.source_name == "nws_hourly_forecast"
                and record["availability_status"] == "completed"
            ):
                event = next(item for item in cluster.events if item.event_id == request.event_id)
                try:
                    record["weather_observation"] = normalize_nws_hourly_forecast(
                        response.body,
                        event=event,
                        first_observed_at_utc=response.first_observed_at_utc,
                        captured_at_utc=response.captured_at_utc,
                        requested_as_of_utc=requested_as_of,
                    )
                except ProspectiveAcquisitionError as exc:
                    record["availability_status"] = "rejected"
                    record["availability_note"] = str(exc)
        states = {str(item["availability_status"]) for item in source_records}
        if states == {"completed"}:
            capture_state = "completed"
        elif "rejected" in states:
            capture_state = "rejected"
        elif states == {"unavailable"}:
            capture_state = "unavailable"
        else:
            capture_state = "partial"
        manifest: dict[str, object] = {
            **identity,
            "capture_id": capture_id,
            "capture_state": capture_state,
            "timing_status": timing,
            "observed_at_utc": utc_text(observed),
            "cluster_window": {
                "window_opens_at_utc": utc_text(cluster.window_opens_at_utc),
                "target_at_utc": utc_text(cluster.target_at_utc),
                "window_closes_at_utc": utc_text(cluster.window_closes_at_utc),
            },
            "daily_history_reference": dict(daily_history_reference or {}),
            "sources": source_records,
            "provider_call_count": call_count,
            "every_provider_request_accounted": len(source_records) == call_count,
            "git_commit": git_commit,
            "research_only": True,
            "model_training_enabled": False,
            "predictions_enabled": False,
            "operational_publication_enabled": False,
            "wagering_enabled": False,
        }
        manifest["manifest_digest"] = _sha256_value(manifest)
        (temporary / "acquisition_manifest_v1.json").write_bytes(
            _canonical_json(manifest, pretty=True)
        )
        if destination.exists():
            raise ImmutableCaptureConflictError("capture appeared during publication")
        temporary.replace(destination)
        published = True
        _validate_existing_capture(destination, capture_id)
        return AcquisitionResult(
            capture_id=capture_id,
            capture_dir=destination,
            manifest_path=destination / "acquisition_manifest_v1.json",
            capture_state=capture_state,
            no_op=False,
            provider_call_count=call_count,
        )
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)


def acquire_daily_history(
    events: Sequence[ScheduledEvent],
    *,
    requested_as_of_utc: datetime | str,
    requests: Sequence[EvidenceRequest],
    provider: EvidenceProvider,
    acquisition_root: str | Path = DEFAULT_ACQUISITION_ROOT,
    git_commit: str,
) -> DailyHistoryResult:
    """Acquire one immutable, reusable stable/history snapshot for a slate."""

    if not events:
        raise ProspectiveAcquisitionError("daily history requires scheduled events")
    ordered_events = tuple(
        sorted(events, key=lambda item: (item.scheduled_start_utc, item.event_id))
    )
    operating_dates = {item.operating_date for item in ordered_events}
    if len(operating_dates) != 1:
        raise ProspectiveAcquisitionError("daily history cannot span operating dates")
    if any(item.evidence_class != "stable_history" for item in requests):
        raise ProspectiveAcquisitionError(
            "daily history accepts stable_history requests only"
        )
    request_ids = [item.request_id for item in requests]
    if len(request_ids) != len(set(request_ids)):
        raise ProspectiveAcquisitionError("duplicate daily-history request id")
    requested_as_of = parse_utc(requested_as_of_utc, "requested_as_of_utc")
    operating_date = ordered_events[0].operating_date
    identity = {
        "schema_version": DAILY_HISTORY_SCHEMA_VERSION,
        "operating_date": operating_date.isoformat(),
        "events": [item.cluster_identity_payload() for item in ordered_events],
        "requests": [item.identity_payload() for item in requests],
    }
    history_snapshot_id = "history-" + _sha256_value(identity)
    root = Path(acquisition_root).expanduser().resolve()
    destination = (
        root
        / "daily_history"
        / operating_date.isoformat()
        / ("h-" + history_snapshot_id.removeprefix("history-")[:20])
    )
    if destination.exists():
        state = _validate_existing_capture(destination, history_snapshot_id)
        return DailyHistoryResult(
            history_snapshot_id=history_snapshot_id,
            snapshot_dir=destination,
            manifest_path=destination / "acquisition_manifest_v1.json",
            snapshot_state=state,
            no_op=True,
            provider_call_count=0,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".t-", dir=destination.parent))
    published = False
    source_records: list[dict[str, object]] = []
    responses: dict[str, ProviderResponse] = {}
    records_by_request: dict[str, dict[str, object]] = {}
    call_count = 0
    placeholder_cluster = build_event_clusters((ordered_events[0],))[0]
    try:
        for request in requests:
            started_at = datetime.now(timezone.utc)
            response: ProviderResponse | None = None
            try:
                call_count += 1
                response = provider.fetch(request)
            except Exception as exc:
                record = _failed_request_record(
                    request,
                    requested_as_of=requested_as_of,
                    started_at=started_at,
                    completed_at=datetime.now(timezone.utc),
                    exc=exc,
                )
            else:
                try:
                    record = _source_record(
                        temporary,
                        request,
                        response,
                        requested_as_of=requested_as_of,
                        cluster=placeholder_cluster,
                        request_started_at_utc=started_at,
                    )
                except Exception as exc:
                    record = _failed_request_record(
                        request,
                        requested_as_of=requested_as_of,
                        started_at=started_at,
                        completed_at=datetime.now(timezone.utc),
                        exc=exc,
                        response=response,
                    )
                else:
                    responses[request.request_id] = response
            source_records.append(record)
            records_by_request[request.request_id] = record

        schedule_requests = [
            item for item in requests if item.source_name == "mlb_schedule"
        ]
        if len(schedule_requests) != 1:
            raise ProspectiveAcquisitionError(
                "daily history requires exactly one full-slate MLB schedule request"
            )
        schedule_request = schedule_requests[0]
        schedule_response = responses.get(schedule_request.request_id)
        schedule_record = records_by_request[schedule_request.request_id]
        if schedule_response is not None and schedule_record["availability_status"] == "completed":
            try:
                parsed_events = parse_mlb_schedule(
                    schedule_response.body, operating_date=operating_date
                )
                if {
                    item.event_id for item in parsed_events
                } != {item.event_id for item in ordered_events}:
                    raise ProspectiveAcquisitionError(
                        "daily schedule event identity conflicts with requested slate"
                    )
            except ProspectiveAcquisitionError as exc:
                schedule_record["availability_status"] = "rejected"
                schedule_record["availability_note"] = str(exc)

        for request in requests:
            if request.source_name != "mlb_active_roster":
                continue
            response = responses.get(request.request_id)
            record = records_by_request[request.request_id]
            if response is None or record["availability_status"] != "completed":
                continue
            try:
                payload = json.loads(response.body.decode("utf-8-sig"))
                roster = _required_mapping(payload, "active roster").get("roster")
                if not isinstance(roster, list) or not roster:
                    raise ProspectiveAcquisitionError("active roster response is empty")
            except (UnicodeError, json.JSONDecodeError, ProspectiveAcquisitionError) as exc:
                record["availability_status"] = "rejected"
                record["availability_note"] = str(exc)

        completed_requests = [
            item
            for item in requests
            if item.source_name == "mlb_completed_game_schedule"
        ]
        statcast_requests = [
            item for item in requests if item.source_name == "statcast_pitch_history"
        ]
        if len(completed_requests) == 1 and len(statcast_requests) == 1:
            completed_request = completed_requests[0]
            statcast_request = statcast_requests[0]
            completed_response = responses.get(completed_request.request_id)
            statcast_response = responses.get(statcast_request.request_id)
            statcast_record = records_by_request[statcast_request.request_id]
            if (
                completed_response is not None
                and statcast_response is not None
                and records_by_request[completed_request.request_id][
                    "availability_status"
                ]
                == "completed"
                and statcast_record["availability_status"] == "completed"
            ):
                try:
                    completed_ids = completed_game_ids_from_schedule(
                        completed_response.body,
                        requested_as_of_utc=requested_as_of,
                    )
                    statcast_rows = _statcast_rows(statcast_response.body)
                    if not statcast_rows:
                        statcast_record["availability_status"] = "partial"
                        statcast_record["availability_note"] = (
                            "provider returned no historical Statcast rows"
                        )
                    else:
                        validate_historical_statcast_rows(
                            statcast_rows,
                            completed_game_ids=completed_ids,
                            requested_as_of_utc=requested_as_of,
                            first_observed_at_utc=statcast_response.first_observed_at_utc,
                            captured_at_utc=statcast_response.captured_at_utc,
                        )
                        statcast_record["row_count"] = len(statcast_rows)
                        statcast_record["completed_game_count"] = len(completed_ids)
                except ProspectiveAcquisitionError as exc:
                    statcast_record["availability_status"] = "rejected"
                    statcast_record["availability_note"] = str(exc)

        states = {str(item["availability_status"]) for item in source_records}
        if states == {"completed"}:
            snapshot_state = "completed"
        elif "rejected" in states:
            snapshot_state = "rejected"
        elif states == {"unavailable"}:
            snapshot_state = "unavailable"
        else:
            snapshot_state = "partial"
        manifest: dict[str, object] = {
            **identity,
            "capture_id": history_snapshot_id,
            "history_snapshot_id": history_snapshot_id,
            "capture_state": snapshot_state,
            "snapshot_state": snapshot_state,
            "requested_as_of_utc": utc_text(requested_as_of),
            "sources": source_records,
            "provider_call_count": call_count,
            "every_provider_request_accounted": len(source_records) == call_count,
            "git_commit": git_commit,
            "research_only": True,
            "model_training_enabled": False,
            "predictions_enabled": False,
            "operational_publication_enabled": False,
            "wagering_enabled": False,
        }
        manifest["manifest_digest"] = _sha256_value(manifest)
        (temporary / "acquisition_manifest_v1.json").write_bytes(
            _canonical_json(manifest, pretty=True)
        )
        if destination.exists():
            raise ImmutableCaptureConflictError(
                "daily history snapshot appeared during publication"
            )
        temporary.replace(destination)
        published = True
        _validate_existing_capture(destination, history_snapshot_id)
        return DailyHistoryResult(
            history_snapshot_id=history_snapshot_id,
            snapshot_dir=destination,
            manifest_path=destination / "acquisition_manifest_v1.json",
            snapshot_state=snapshot_state,
            no_op=False,
            provider_call_count=call_count,
        )
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)


def build_mlb_game_feed_requests(cluster: EventCluster) -> tuple[EvidenceRequest, ...]:
    return tuple(
        EvidenceRequest(
            request_id=f"statsapi-feed-{event.event_id}",
            evidence_class="volatile_pregame",
            source_name="mlb_statsapi_game_feed",
            provider="mlb_statsapi",
            url=f"https://statsapi.mlb.com/api/v1.1/game/{event.event_id}/feed/live",
            event_id=event.event_id,
        )
        for event in cluster.events
    )


def build_daily_history_request_plan(
    events: Sequence[ScheduledEvent],
    *,
    operating_date: date,
    season_start_date: date,
) -> tuple[EvidenceRequest, ...]:
    """Build one shared full-slate plan; execution belongs to an explicit runner."""

    if season_start_date >= operating_date:
        raise ProspectiveAcquisitionError("season_start_date must precede operating_date")
    team_ids = sorted(
        {team_id for event in events for team_id in (event.away_team_id, event.home_team_id)},
        key=int,
    )
    requests: list[EvidenceRequest] = [
        EvidenceRequest(
            request_id=f"statsapi-schedule-{operating_date.isoformat()}",
            evidence_class="stable_history",
            source_name="mlb_schedule",
            provider="mlb_statsapi",
            url=(
                "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date="
                + operating_date.isoformat()
                + "&hydrate=probablePitcher,venue"
            ),
        )
    ]
    requests.extend(
        EvidenceRequest(
            request_id=f"statsapi-active-roster-{team_id}",
            evidence_class="stable_history",
            source_name="mlb_active_roster",
            provider="mlb_statsapi",
            url=(
                f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster"
                "?rosterType=active&hydrate=person"
            ),
        )
        for team_id in team_ids
    )
    requests.extend(
        (
            EvidenceRequest(
                request_id=f"statsapi-completed-games-{season_start_date}-{operating_date}",
                evidence_class="stable_history",
                source_name="mlb_completed_game_schedule",
                provider="mlb_statsapi",
                url=(
                    "https://statsapi.mlb.com/api/v1/schedule?sportId=1&gameTypes=R"
                    f"&startDate={season_start_date.isoformat()}"
                    f"&endDate={(operating_date - timedelta(days=1)).isoformat()}"
                ),
            ),
            EvidenceRequest(
                request_id=f"baseball-savant-statcast-{season_start_date}-{operating_date}",
                evidence_class="stable_history",
                source_name="statcast_pitch_history",
                provider="baseball_savant",
                url=(
                    "https://baseballsavant.mlb.com/statcast_search/csv?all=true"
                    "&type=details&player_type=batter"
                    f"&game_date_gt={season_start_date.isoformat()}"
                    f"&game_date_lt={(operating_date - timedelta(days=1)).isoformat()}"
                ),
            ),
        )
    )
    return tuple(requests)


__all__ = [
    "ACQUISITION_SCHEMA_VERSION",
    "AcquisitionPolicy",
    "AcquisitionResult",
    "DailyHistoryResult",
    "DEFAULT_ACQUISITION_ROOT",
    "EventCluster",
    "EvidenceProvider",
    "EvidenceRequest",
    "HttpEvidenceProvider",
    "ImmutableCaptureConflictError",
    "ProspectiveAcquisitionError",
    "ProviderResponse",
    "ScheduledEvent",
    "acquire_event_cluster",
    "acquire_daily_history",
    "build_daily_history_request_plan",
    "build_event_clusters",
    "build_mlb_game_feed_requests",
    "classify_cluster_time",
    "completed_game_ids_from_schedule",
    "lineup_coverage",
    "normalize_nws_hourly_forecast",
    "parse_mlb_schedule",
    "parse_utc",
    "utc_text",
    "validate_game_feed_identity",
    "validate_historical_statcast_rows",
    "validate_park_observation",
    "validate_probable_pitcher_identity",
    "validate_weather_observation",
]
