"""Pure provider-to-StatsAPI event and batter bindings for pregame Hits evidence.

References identify caller-supplied evidence; no reference is dereferenced.
Quote clocks and prices are not baseball-evidence observation clocks or inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
import json
from zoneinfo import ZoneInfo

from courtvision.core.candidates import EventIdentity, IdentityStatus, ParticipantIdentity
from courtvision.sports.mlb.data.prospective_context_acquisition import (
    ScheduledEvent, validate_game_feed_identity,
)
from courtvision.sports.mlb.market_data import MLBPlayerPropSourceRecord
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


MATCH_TOLERANCE_SECONDS = 120
_OPERATING_TIMEZONE = ZoneInfo("America/Toronto")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be non-empty unpadded text")
    return value


def _mlbam_id(value: object, name: str) -> str:
    # Same explicit positive ASCII decimal identity rule as batting_results.
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{name} must be an explicit positive decimal MLBAM identifier")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or text.startswith("0"):
        raise ValueError(f"{name} must be an explicit positive decimal MLBAM identifier")
    return text


def _aware(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _provenance(observed_at: datetime, evidence_cutoff: datetime,
                source_refs: tuple[str, ...], start: datetime) -> None:
    for name, value in (("observed_at", observed_at), ("evidence_cutoff", evidence_cutoff)):
        _aware(value, name)
    if observed_at > evidence_cutoff or evidence_cutoff >= start:
        raise ValueError("identity evidence requires observed_at <= evidence_cutoff < game start")
    if not isinstance(source_refs, tuple) or not source_refs:
        raise ValueError("source_refs must be a non-empty immutable tuple")
    for ref in source_refs:
        _text(ref, "source reference")


def _schedule_event(event: ScheduledEvent) -> None:
    if not isinstance(event, ScheduledEvent):
        raise TypeError("schedule events must be ScheduledEvent")
    for name in ("event_id", "away_team_id", "home_team_id", "venue_id"):
        value = getattr(event, name)
        if not isinstance(value, str) or _mlbam_id(value, name) != value:
            raise ValueError(f"{name} must be a positive decimal string")
    for name in ("away_probable_pitcher_id", "home_probable_pitcher_id"):
        value = getattr(event, name)
        if value is not None and (not isinstance(value, str) or _mlbam_id(value, name) != value):
            raise ValueError(f"{name} must be a positive decimal string when supplied")
    if type(event.operating_date) is not date:
        raise ValueError("operating_date must be an explicit date")
    for name in ("away_team", "home_team", "venue_name", "status"):
        _text(getattr(event, name), name)
    _aware(event.scheduled_start_utc, "scheduled_start_utc")
    if event.away_team_id == event.home_team_id or event.away_team == event.home_team:
        raise ValueError("official home and away teams must differ")


def _event_matches(home: str, away: str, start: datetime, event: ScheduledEvent) -> bool:
    operating_date = start.astimezone(_OPERATING_TIMEZONE).date()
    return (
        home == event.home_team
        and away == event.away_team
        and operating_date == event.operating_date
        and operating_date == event.scheduled_start_utc.astimezone(_OPERATING_TIMEZONE).date()
        and abs((start - event.scheduled_start_utc).total_seconds()) <= MATCH_TOLERANCE_SECONDS
    )


@dataclass(frozen=True, slots=True)
class MLBEventIdentityBinding:
    """Provider reference plus, only on a unique match, authoritative schedule identity."""

    provider: str
    provider_event_id: str
    provider_commence_time: datetime
    provider_home_team: str
    provider_away_team: str
    identity_status: IdentityStatus
    identity_method: str
    scheduled_event: ScheduledEvent | None
    observed_at: datetime
    evidence_cutoff: datetime
    source_refs: tuple[str, ...]
    canonical_source: str = "mlb_statsapi"
    provider_sport_key: str = "baseball_mlb"

    def __post_init__(self) -> None:
        for name in ("provider", "provider_event_id", "provider_home_team",
                     "provider_away_team", "identity_method"):
            _text(getattr(self, name), name)
        _aware(self.provider_commence_time, "provider_commence_time")
        if self.provider_sport_key != "baseball_mlb" or self.canonical_source != "mlb_statsapi":
            raise ValueError("binding requires MLB and mlb_statsapi")
        if self.provider_home_team == self.provider_away_team:
            raise ValueError("provider home and away teams must differ")
        status = IdentityStatus(self.identity_status)
        if status not in (IdentityStatus.RESOLVED, IdentityStatus.UNRESOLVED, IdentityStatus.AMBIGUOUS):
            raise ValueError("unsupported event identity status")
        object.__setattr__(self, "identity_status", status)
        if (status is IdentityStatus.RESOLVED) != (self.scheduled_event is not None):
            raise ValueError("only resolved event bindings carry a ScheduledEvent")
        _provenance(self.observed_at, self.evidence_cutoff, self.source_refs,
                    self.provider_commence_time)
        if self.scheduled_event is not None:
            _schedule_event(self.scheduled_event)
            if not _event_matches(self.provider_home_team, self.provider_away_team,
                                  self.provider_commence_time, self.scheduled_event):
                raise ValueError("resolved event violates exact teams, date or 120-second bound")
            if self.evidence_cutoff >= self.scheduled_event.scheduled_start_utc:
                raise ValueError("event evidence cutoff must precede official game start")

    @property
    def mlbam_game_id(self) -> str | None:
        return None if self.scheduled_event is None else self.scheduled_event.event_id

    @property
    def official_commence_time(self) -> datetime | None:
        return None if self.scheduled_event is None else self.scheduled_event.scheduled_start_utc

    @property
    def official_home_team(self) -> str | None:
        return None if self.scheduled_event is None else self.scheduled_event.home_team

    @property
    def official_away_team(self) -> str | None:
        return None if self.scheduled_event is None else self.scheduled_event.away_team

    @property
    def schedule_start_drift_seconds(self) -> float | None:
        """Signed provider start minus the authoritative StatsAPI start."""
        if self.official_commence_time is None:
            return None
        return (self.provider_commence_time - self.official_commence_time).total_seconds()

    @property
    def event_identity(self) -> EventIdentity:
        return EventIdentity(self.provider_event_id, self.identity_method,
                             self.identity_status, self.mlbam_game_id)


def bind_mlb_event(
    source: MLBPlayerPropSourceRecord, schedule_events: Sequence[ScheduledEvent], *,
    observed_at: datetime, evidence_cutoff: datetime, source_refs: tuple[str, ...],
) -> MLBEventIdentityBinding:
    """Match exact ordered teams and Toronto date within 120 seconds, never by row order."""
    if not isinstance(source, MLBPlayerPropSourceRecord):
        raise TypeError("source must be MLBPlayerPropSourceRecord")
    _provenance(observed_at, evidence_cutoff, source_refs, source.commence_time)
    if source.provider_sport_key != "baseball_mlb":
        raise ValueError("event source must be baseball_mlb")
    matches = []
    seen: set[str] = set()
    for event in schedule_events:
        _schedule_event(event)
        if event.event_id in seen:
            raise ValueError("duplicate canonical schedule event id")
        seen.add(event.event_id)
        if _event_matches(source.home_team, source.away_team, source.commence_time, event):
            matches.append(event)
    status = (IdentityStatus.RESOLVED if len(matches) == 1 else
              IdentityStatus.AMBIGUOUS if matches else IdentityStatus.UNRESOLVED)
    if len(matches) == 1 and (
        source.market_updated_at >= matches[0].scheduled_start_utc
        or source.collected_at >= matches[0].scheduled_start_utc
    ):
        raise ValueError("source quote must precede official game start")
    return MLBEventIdentityBinding(
        provider=source.provider, provider_event_id=source.provider_event_id,
        provider_commence_time=source.commence_time, provider_home_team=source.home_team,
        provider_away_team=source.away_team, identity_status=status,
        identity_method="statsapi_exact_teams_toronto_date_start_within_120_seconds",
        scheduled_event=matches[0] if len(matches) == 1 else None,
        observed_at=observed_at, evidence_cutoff=evidence_cutoff,
        source_refs=tuple(dict.fromkeys((*source.source_refs, *source_refs))),
        provider_sport_key=source.provider_sport_key,
    )


def bind_mlb_events(
    sources: Sequence[MLBPlayerPropSourceRecord], schedule_events: Sequence[ScheduledEvent], *,
    observed_at: datetime, evidence_cutoff: datetime, source_refs: tuple[str, ...],
) -> tuple[MLBEventIdentityBinding, ...]:
    """Reject provider-reference conflicts and canonical game collisions in one batch.

    Repeated quotes for exactly the same provider event are allowed and retain
    their input order. Distinct provider game references cannot share gamePk.
    """
    bindings = tuple(bind_mlb_event(source, schedule_events, observed_at=observed_at,
                                   evidence_cutoff=evidence_cutoff, source_refs=source_refs)
                     for source in sources)
    provider_events: dict[tuple[str, str], MLBEventIdentityBinding] = {}
    canonical_events: dict[str, tuple[str, str]] = {}
    for binding in bindings:
        key = (binding.provider, binding.provider_event_id)
        previous = provider_events.get(key)
        if previous is not None and any(
            getattr(previous, name) != getattr(binding, name)
            for name in ("provider_commence_time", "provider_home_team", "provider_away_team",
                         "identity_status", "scheduled_event")
        ):
            raise ValueError("provider event conflict in binding batch")
        provider_events[key] = binding
        canonical_id = binding.mlbam_game_id
        if canonical_id is not None:
            if canonical_id in canonical_events and canonical_events[canonical_id] != key:
                raise ValueError("canonical game collision in binding batch")
            canonical_events[canonical_id] = key
    return bindings


def _mapping(value: object, name: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object keys are ambiguous evidence")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is invalid evidence: {value}")


def validate_bound_game_feed(
    game_feed: bytes | Mapping[str, object], event_binding: MLBEventIdentityBinding,
) -> Mapping[str, object]:
    """Validate supplied JSON against the retained authoritative ScheduledEvent."""
    if not isinstance(event_binding, MLBEventIdentityBinding):
        raise TypeError("event_binding must be MLBEventIdentityBinding")
    if event_binding.identity_status is not IdentityStatus.RESOLVED:
        raise ValueError("game feed requires a resolved event binding")
    if isinstance(game_feed, bytes):
        try:
            payload = json.loads(game_feed.decode("utf-8-sig"), object_pairs_hook=_unique_json_object,
                                 parse_constant=_reject_json_constant)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("game feed must contain valid supplied JSON") from exc
    else:
        payload = _mapping(game_feed, "game feed")
    # Serializing detaches caller mappings and rejects non-JSON/non-finite values.
    encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
    payload = _mapping(json.loads(encoded), "game feed")
    _mlbam_id(payload.get("gamePk"), "gamePk")
    data = _mapping(payload.get("gameData"), "gameData")
    teams = _mapping(data.get("teams"), "gameData.teams")
    for side in ("away", "home"):
        _mlbam_id(_mapping(teams.get(side), side).get("id"), f"{side}.id")
    _mlbam_id(_mapping(data.get("venue"), "venue").get("id"), "venue.id")
    assert event_binding.scheduled_event is not None
    return validate_game_feed_identity(encoded, event_binding.scheduled_event)


@dataclass(frozen=True, slots=True)
class MLBEventRosterPlayer:
    """One explicit player identity within a validated game feed."""

    mlbam_game_id: str
    mlbam_player_id: str
    player_name: str
    team_side: str | None
    team_id: str | None

    def __post_init__(self) -> None:
        for name in ("mlbam_game_id", "mlbam_player_id"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} must be a positive decimal string")
            _mlbam_id(getattr(self, name), name)
        _text(self.player_name, "player_name")
        if not normalize_mlb_player_name(self.player_name):
            raise ValueError("player_name requires a comparison key")
        if self.team_side not in (None, "home", "away"):
            raise ValueError("team_side must be home, away or unavailable")
        if (self.team_side is None) != (self.team_id is None):
            raise ValueError("team side and identity must be supplied together")
        if self.team_id is not None:
            if not isinstance(self.team_id, str):
                raise ValueError("team_id must be a positive decimal string")
            _mlbam_id(self.team_id, "team_id")


def event_roster_players(
    game_feed: bytes | Mapping[str, object], event_binding: MLBEventIdentityBinding,
) -> tuple[MLBEventRosterPlayer, ...]:
    """Read explicit IDs from gameData and/or boxscore rosters after feed binding.

    Boxscore roster membership and currentTeam, when supplied, must agree with
    the event. Repeated representations of an ID must have compatible names.
    """
    payload = validate_bound_game_feed(game_feed, event_binding)
    event = event_binding.scheduled_event
    assert event is not None
    sides = {event.home_team_id: "home", event.away_team_id: "away"}
    records: dict[str, MLBEventRosterPlayer] = {}

    def add(key: object, person: Mapping, side: str | None) -> None:
        player_id = _mlbam_id(person.get("id"), "player.id")
        if key != "ID" + player_id:
            raise ValueError("roster key conflicts with explicit MLBAM player id")
        name = _text(person.get("fullName"), "player.fullName")
        current_team = person.get("currentTeam")
        if current_team is not None:
            team_id = _mlbam_id(_mapping(current_team, "currentTeam").get("id"), "currentTeam.id")
            if team_id not in sides:
                raise ValueError("roster player is outside bound event teams")
            if side is not None and side != sides[team_id]:
                raise ValueError("conflicting roster player team")
            side = sides[team_id]
        team_id = None if side is None else (
            event.home_team_id if side == "home" else event.away_team_id
        )
        previous = records.get(player_id)
        if previous is not None:
            if normalize_mlb_player_name(previous.player_name) != normalize_mlb_player_name(name):
                raise ValueError("conflicting canonical names for one MLBAM player id")
            if previous.team_side is not None and side is not None and previous.team_side != side:
                raise ValueError("player appears on both bound event teams")
            # gameData supplies the canonical name; side rosters add membership.
            name = previous.player_name
            if side is None:
                side, team_id = previous.team_side, previous.team_id
        records[player_id] = MLBEventRosterPlayer(event.event_id, player_id, name, side, team_id)

    game_data = _mapping(payload.get("gameData"), "gameData")
    for key, row in _mapping(game_data.get("players", {}), "gameData.players").items():
        add(key, _mapping(row, "gameData player"), None)
    live = _mapping(payload.get("liveData", {}), "liveData")
    boxscore = _mapping(live.get("boxscore", {}), "boxscore")
    teams = _mapping(boxscore.get("teams", {}), "boxscore.teams")
    batting_sides: dict[str, str] = {}
    for side in ("away", "home"):
        team = _mapping(teams.get(side, {}), f"boxscore.{side}")
        if "team" in team:
            expected = event.home_team_id if side == "home" else event.away_team_id
            if _mlbam_id(_mapping(team["team"], "boxscore.team").get("id"), "boxscore.team.id") != expected:
                raise ValueError("boxscore roster team conflicts with bound event")
        if team.get("battingOrder") is not None:
            order = team["battingOrder"]
            if not isinstance(order, list) or len(order) > 9:
                raise ValueError("battingOrder must be an array of at most nine players")
            for raw_id in order:
                player_id = _mlbam_id(raw_id, "battingOrder player id")
                if player_id in batting_sides:
                    raise ValueError("duplicate player across supplied batting orders")
                batting_sides[player_id] = side
        for key, row in _mapping(team.get("players", {}), "boxscore.players").items():
            row = _mapping(row, "boxscore player")
            add(key, _mapping(row.get("person"), "boxscore player.person"), side)
    for player_id, side in batting_sides.items():
        player = records.get(player_id)
        if player is None:
            continue  # Order IDs alone never fabricate a named roster identity.
        if player.team_side is not None and player.team_side != side:
            raise ValueError("batting order conflicts with roster player team")
        team_id = event.home_team_id if side == "home" else event.away_team_id
        records[player_id] = MLBEventRosterPlayer(
            event.event_id, player_id, player.player_name, side, team_id,
        )
    return tuple(records[key] for key in sorted(records))


@dataclass(frozen=True, slots=True)
class MLBPlayerIdentityBinding:
    """Immutable roster provenance, preserving observed spelling and optional team."""

    participant_identity: ParticipantIdentity
    event_binding: MLBEventIdentityBinding
    roster_player: MLBEventRosterPlayer | None
    observed_at: datetime
    evidence_cutoff: datetime
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.participant_identity, ParticipantIdentity):
            raise TypeError("participant_identity must be ParticipantIdentity")
        if not isinstance(self.event_binding, MLBEventIdentityBinding):
            raise TypeError("event_binding must be MLBEventIdentityBinding")
        event = self.event_binding.scheduled_event
        if event is None:
            raise ValueError("player identity requires resolved event binding")
        _provenance(self.observed_at, self.evidence_cutoff, self.source_refs,
                    min(event.scheduled_start_utc, self.event_binding.provider_commence_time))
        identity = self.participant_identity
        if identity.identity_status not in (
            IdentityStatus.RESOLVED, IdentityStatus.NAME_ONLY_RESEARCH, IdentityStatus.AMBIGUOUS,
        ):
            raise ValueError("unsupported player identity status")
        if not normalize_mlb_player_name(identity.participant_name):
            raise ValueError("participant_name requires a comparison key")
        if identity.identity_status is IdentityStatus.RESOLVED:
            player = self.roster_player
            if not isinstance(player, MLBEventRosterPlayer):
                raise ValueError("resolved participant requires explicit roster identity")
            if player.mlbam_game_id != event.event_id:
                raise ValueError("player roster event conflicts with binding")
            if (identity.canonical_participant_id != player.mlbam_player_id
                    or identity.canonical_participant_name != player.player_name
                    or normalize_mlb_player_name(identity.participant_name)
                    != normalize_mlb_player_name(player.player_name)):
                raise ValueError("participant identity conflicts with roster evidence")
            if player.team_side is not None:
                expected = event.home_team_id if player.team_side == "home" else event.away_team_id
                if player.team_id != expected:
                    raise ValueError("roster player team conflicts with bound event")
        elif self.roster_player is not None:
            raise ValueError("unresolved participant cannot retain a chosen roster player")

    @property
    def mlbam_player_id(self) -> str | None:
        return self.participant_identity.canonical_participant_id

    @property
    def mlbam_game_id(self) -> str:
        assert self.event_binding.mlbam_game_id is not None
        return self.event_binding.mlbam_game_id

    @property
    def provider_event_id(self) -> str:
        return self.event_binding.provider_event_id

    @property
    def team_side(self) -> str | None:
        return None if self.roster_player is None else self.roster_player.team_side

    @property
    def team_id(self) -> str | None:
        return None if self.roster_player is None else self.roster_player.team_id


def resolve_mlb_batter_identity(
    source: MLBPlayerPropSourceRecord, event_binding: MLBEventIdentityBinding,
    game_feed: bytes | Mapping[str, object], *, observed_at: datetime,
    evidence_cutoff: datetime, source_refs: tuple[str, ...],
) -> MLBPlayerIdentityBinding:
    """Resolve one normalized name only within the strictly bound event roster."""
    if not isinstance(source, MLBPlayerPropSourceRecord):
        raise TypeError("source must be MLBPlayerPropSourceRecord")
    if not isinstance(event_binding, MLBEventIdentityBinding):
        raise TypeError("event_binding must be MLBEventIdentityBinding")
    _provenance(observed_at, evidence_cutoff, source_refs, source.commence_time)
    if (source.provider_sport_key != event_binding.provider_sport_key
            or source.provider != event_binding.provider
            or source.provider_event_id != event_binding.provider_event_id
            or source.home_team != event_binding.provider_home_team
            or source.away_team != event_binding.provider_away_team
            or source.commence_time != event_binding.provider_commence_time):
        raise ValueError("source record conflicts with provider event binding")
    if event_binding.official_commence_time is not None and (
        source.market_updated_at >= event_binding.official_commence_time
        or source.collected_at >= event_binding.official_commence_time
    ):
        raise ValueError("source quote must precede official game start")
    players = event_roster_players(game_feed, event_binding)
    name = normalize_mlb_player_name(source.participant_name)
    if not name:
        raise ValueError("observed participant name requires a comparison key")
    matches = tuple(player for player in players
                    if normalize_mlb_player_name(player.player_name) == name)
    selected = matches[0] if len(matches) == 1 else None
    status = (IdentityStatus.RESOLVED if selected else
              IdentityStatus.AMBIGUOUS if matches else IdentityStatus.NAME_ONLY_RESEARCH)
    identity = ParticipantIdentity(
        participant_name=source.participant_name,
        identity_method="unique_normalized_name_in_bound_statsapi_event_roster",
        identity_status=status,
        canonical_participant_id=None if selected is None else selected.mlbam_player_id,
        canonical_participant_name=None if selected is None else selected.player_name,
    )
    return MLBPlayerIdentityBinding(identity, event_binding, selected, observed_at,
                                    evidence_cutoff,
                                    tuple(dict.fromkeys((*source.source_refs, *source_refs))))


__all__ = [
    "MATCH_TOLERANCE_SECONDS", "MLBEventIdentityBinding", "MLBEventRosterPlayer",
    "MLBPlayerIdentityBinding", "bind_mlb_event", "bind_mlb_events",
    "event_roster_players", "resolve_mlb_batter_identity", "validate_bound_game_feed",
]
