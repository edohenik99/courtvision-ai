"""Pure MLB boxscore evidence and 1+ hits resolution, never settlement policy.

Callers supply an already bound boxscore, explicit game status, observation time,
and provenance. Bare StatsAPI boxscores do not prove the provider-event mapping
or finality themselves. This module neither fetches nor persists anything and
never interprets a missing player/stat as a loss or a void.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from courtvision.core.candidates import EventIdentity, IdentityStatus, MarketCandidate
from courtvision.core.probability import ThresholdDirection
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name
from courtvision.sports.mlb.game_finality import classify_game_finality


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _mlb_id(value: object) -> str | None:
    """Accept explicit positive decimal IDs, never row keys or inferred IDs."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    text = str(value)
    if not text.isascii() or not text.isdecimal() or text.startswith("0"):
        return None
    return text


def _count(value: object) -> int | None:
    # JSON integer counts only: no bool, truncation, string coercion, or default 0.
    return value if type(value) is int and value >= 0 else None


def _mapping(value: object) -> Mapping:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError("boxscore objects must be mappings")
    return value


@dataclass(frozen=True, slots=True)
class BatterBattingResult:
    """Factual roster evidence; absent/invalid statistics remain unavailable."""

    mlb_player_id: str | None
    player_name: str | None
    side: str | None
    at_bats: int | None
    hits: int | None
    normalized_player_name: str = field(init=False)
    plate_appearances: int | None = None
    doubles: int | None = None
    triples: int | None = None
    home_runs: int | None = None
    walks: int | None = None
    strikeouts: int | None = None
    runs: int | None = None
    rbi: int | None = None
    has_batting_stats: bool = False

    def __post_init__(self) -> None:
        if self.mlb_player_id is not None and _mlb_id(self.mlb_player_id) != self.mlb_player_id:
            raise ValueError("mlb_player_id must be an explicit positive decimal string")
        name = None if self.player_name is None else _text(self.player_name, "player_name")
        normalized = normalize_mlb_player_name(name)
        if name is not None and not normalized:
            raise ValueError("player_name must have a normalized comparison key")
        if self.side not in {None, "home", "away"}:
            raise ValueError("side must be home, away, or unavailable")
        for key in ("at_bats", "hits", "plate_appearances", "doubles", "triples",
                    "home_runs", "walks", "strikeouts", "runs", "rbi"):
            value = getattr(self, key)
            if value is not None and _count(value) is None:
                raise ValueError(f"{key} must be a nonnegative integer or unavailable")
        if self.hits is not None and self.at_bats is not None and self.hits > self.at_bats:
            raise ValueError("hits cannot exceed at_bats")
        object.__setattr__(self, "player_name", name)
        object.__setattr__(self, "normalized_player_name", normalized)
        if type(self.has_batting_stats) is not bool:
            raise ValueError("has_batting_stats must be bool")

    @property
    def singles(self) -> int | None:
        components = (self.hits, self.doubles, self.triples, self.home_runs)
        if any(value is None for value in components):
            return None
        value = self.hits - self.doubles - self.triples - self.home_runs
        return value if value >= 0 else None


@dataclass(frozen=True, slots=True)
class BattingBoxscoreEvidence:
    """Caller-bound source evidence, including explicit status and provenance.

    Only the exact status ``final`` (case-insensitive) proves finality here.
    Unrecognized statuses remain non-final; inning, score and stat presence do
    not establish finality. Duplicate roster entries deliberately remain intact.
    """

    event_identity: EventIdentity
    game_status: str
    observed_at: datetime
    source_refs: tuple[str, ...]
    batters: tuple[BatterBattingResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.event_identity, EventIdentity):
            raise TypeError("event_identity must be EventIdentity")
        if not isinstance(self.observed_at, datetime) or self.observed_at.utcoffset() is None:
            raise ValueError("observed_at must be timezone-aware")
        if not isinstance(self.source_refs, tuple) or not self.source_refs:
            raise ValueError("source_refs must be a non-empty immutable tuple")
        for reference in self.source_refs:
            _text(reference, "source reference")
        if not isinstance(self.batters, tuple) or not all(
            isinstance(batter, BatterBattingResult) for batter in self.batters
        ):
            raise ValueError("batters must be an immutable tuple of BatterBattingResult")
        object.__setattr__(self, "game_status", _text(self.game_status, "game_status").casefold())

    @property
    def is_final(self) -> bool:
        return self.game_status == "final"


class BattingResolutionState(str, Enum):
    RESOLVED = "resolved"
    NOT_FINAL = "not_final"
    EVENT_MISMATCH = "event_mismatch"
    PLAYER_MISSING = "player_missing"
    IDENTITY_AMBIGUOUS = "identity_ambiguous"
    IDENTITY_UNRESOLVED = "identity_unresolved"
    STAT_MISSING = "stat_missing"


@dataclass(frozen=True, slots=True)
class BatterHitsResultEvidence:
    """An inspectable factual resolution, with no wager/void/settlement policy."""

    state: BattingResolutionState
    candidate_id: str
    boxscore: BattingBoxscoreEvidence
    reason: str
    batter: BatterBattingResult | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", BattingResolutionState(self.state))
        _text(self.candidate_id, "candidate_id")
        _text(self.reason, "reason")
        if not isinstance(self.boxscore, BattingBoxscoreEvidence):
            raise TypeError("boxscore must be BattingBoxscoreEvidence")
        if self.batter is not None and self.batter not in self.boxscore.batters:
            raise ValueError("batter must come from the bound boxscore")
        if self.state is BattingResolutionState.RESOLVED and (
            not self.boxscore.is_final or self.batter is None or self.batter.hits is None
        ):
            raise ValueError("resolved evidence requires finality and an actual hits count")

    @property
    def actual_hits(self) -> int | None:
        return self.batter.hits if self.state is BattingResolutionState.RESOLVED else None

    @property
    def event_outcome(self) -> bool | None:
        return None if self.actual_hits is None else self.actual_hits >= 1


def validate_boxscore_binding(
    payload: Mapping[str, object], event_identity: EventIdentity, game_status: str,
) -> str:
    """Share the existing explicit game identity/finality checks with fact adapters."""
    payload = _mapping(payload)
    if not isinstance(event_identity, EventIdentity):
        raise TypeError("event_identity must be EventIdentity")
    status = _text(game_status, "game_status").casefold()
    if "gamePk" in payload and (
        _mlb_id(payload["gamePk"]) is None
        or _mlb_id(payload["gamePk"]) != event_identity.canonical_event_id
    ):
        raise ValueError("payload gamePk requires matching canonical event identity")
    embedded_status = payload.get("status")
    if embedded_status is not None:
        if isinstance(embedded_status, str):
            if (_text(embedded_status, "embedded status").casefold() == "final") != (status == "final"):
                raise ValueError("embedded status conflicts with supplied finality")
        else:
            embedded_status = _mapping(embedded_status)
            if not any(key in embedded_status for key in ("abstractGameState", "detailedState")):
                raise ValueError("embedded status requires an explicit supported text status")
            finality = classify_game_finality(embedded_status)
            # Legacy caller-bound boxscores may contain just one literal Final
            # text field, like the string form above. This checks consistency
            # with the caller's explicit status; it does not classify that
            # partial provider object as FINAL. Acquisition must separately
            # establish corroborated finality from schedule/feed evidence.
            partial_final_text = (
                set(embedded_status) in ({"abstractGameState"}, {"detailedState"})
                and isinstance(next(iter(embedded_status.values())), str)
                and next(iter(embedded_status.values())).strip().casefold() == "final"
            )
            if partial_final_text and status == "final":
                return status
            if (finality.canonical_state in {"AMBIGUOUS", "CONFLICT"}
                    or finality.is_final != (status == "final")):
                raise ValueError("embedded status code conflicts with supplied finality: "
                                 + finality.decision_reason)
    return status


def extract_batting_results(
    payload: Mapping[str, object], *, event_identity: EventIdentity,
    game_status: str, observed_at: datetime, source_refs: tuple[str, ...],
) -> BattingBoxscoreEvidence:
    """Extract supplied StatsAPI-like roster evidence, never infer event or finality.

    Missing/invalid counts remain unavailable. Additional counts do not change
    the existing hits resolution policy. Roster-only rows remain inspectable.
    """
    payload = _mapping(payload)
    status = validate_boxscore_binding(payload, event_identity, game_status)
    batters = []
    teams = _mapping(payload.get("teams"))
    for side in ("away", "home"):
        players = _mapping(_mapping(teams.get(side)).get("players"))
        for row in players.values():
            row = _mapping(row)
            person = _mapping(row.get("person"))
            raw_name = person.get("fullName")
            name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
            batting = _mapping(_mapping(row.get("stats")).get("batting"))
            at_bats, hits = _count(batting.get("atBats")), _count(batting.get("hits"))
            if at_bats is not None and hits is not None and hits > at_bats:
                hits = None
            extra = {target: _count(batting.get(source)) for target, source in (
                ("plate_appearances", "plateAppearances"), ("doubles", "doubles"),
                ("triples", "triples"), ("home_runs", "homeRuns"), ("walks", "baseOnBalls"),
                ("strikeouts", "strikeOuts"), ("runs", "runs"), ("rbi", "rbi"),
            )}
            batters.append(BatterBattingResult(
                _mlb_id(person.get("id")), name, side, at_bats, hits,
                **extra, has_batting_stats=bool(batting),
            ))
    return BattingBoxscoreEvidence(event_identity, status, observed_at, source_refs, tuple(batters))


def resolve_batter_hits_result(
    candidate: MarketCandidate, boxscore: BattingBoxscoreEvidence,
) -> BatterHitsResultEvidence:
    """Resolve only final MLB ``hits >= 1`` evidence for the exact bound event.

    Resolved player ID always takes precedence and never falls back to a name.
    Name-only research requires one exact normalized roster match. Duplicate
    IDs/names, conflicting identity and missing stats remain unresolved.
    """
    if not isinstance(candidate, MarketCandidate) or not isinstance(boxscore, BattingBoxscoreEvidence):
        raise TypeError("candidate and boxscore must use their immutable contracts")
    if (candidate.taxonomy.sport != "MLB" or candidate.taxonomy.market_type != "batter_hits"
            or candidate.probability.target_statistic != "hits"
            or candidate.probability.threshold != 1
            or candidate.probability.direction is not ThresholdDirection.AT_LEAST):
        raise ValueError("result resolver supports only MLB batter hits >= 1")

    def result(state: BattingResolutionState, reason: str, batter=None) -> BatterHitsResultEvidence:
        return BatterHitsResultEvidence(state, candidate.candidate_id, boxscore, reason, batter)

    event = candidate.event_identity
    evidence_event = boxscore.event_identity
    if (event.event_id != evidence_event.event_id
            or (event.canonical_event_id is not None
                and event.canonical_event_id != evidence_event.canonical_event_id)
            or event.identity_status not in {IdentityStatus.UNRESOLVED, IdentityStatus.RESOLVED}
            or evidence_event.identity_status not in {IdentityStatus.UNRESOLVED, IdentityStatus.RESOLVED}):
        return result(BattingResolutionState.EVENT_MISMATCH, "Event identity is mismatched or unqualified.")
    if not boxscore.is_final:
        return result(BattingResolutionState.NOT_FINAL, "Explicit final game status is required.")
    start = candidate.quote.event_start_time
    quote_time = candidate.quote.quote_timestamp
    if any(value is None or value.utcoffset() is None for value in (start, quote_time)):
        return result(BattingResolutionState.NOT_FINAL, "Aware quote and event start times are required.")
    if (boxscore.observed_at <= start
            or boxscore.observed_at < quote_time
            or boxscore.observed_at < candidate.probability.generated_at):
        return result(BattingResolutionState.NOT_FINAL, "Final evidence predates the bound event/candidate.")
    identity = candidate.participant_identity
    if identity.identity_status is IdentityStatus.RESOLVED:
        player_id = _mlb_id(identity.canonical_player_id)
        if player_id is None:
            return result(BattingResolutionState.IDENTITY_UNRESOLVED, "Canonical MLB player ID is invalid.")
        if normalize_mlb_player_name(identity.participant_name) != normalize_mlb_player_name(identity.canonical_player_name):
            return result(BattingResolutionState.IDENTITY_UNRESOLVED, "Observed and canonical player names conflict.")
        matches = [batter for batter in boxscore.batters if batter.mlb_player_id == player_id]
    elif identity.identity_status is IdentityStatus.NAME_ONLY_RESEARCH:
        name = normalize_mlb_player_name(identity.participant_name)
        if not name:
            return result(BattingResolutionState.IDENTITY_UNRESOLVED, "Normalized player name is missing.")
        matches = [batter for batter in boxscore.batters if batter.normalized_player_name == name]
    else:
        return result(BattingResolutionState.IDENTITY_UNRESOLVED, "Player identity is not qualified for lookup.")
    if not matches:
        return result(BattingResolutionState.PLAYER_MISSING, "Player is absent under the required identity key.")
    if len(matches) != 1:
        return result(BattingResolutionState.IDENTITY_AMBIGUOUS, "Multiple roster entries match the identity key.")
    batter = matches[0]  # Uniqueness was established; never a first-match fallback.
    if (identity.identity_status is IdentityStatus.RESOLVED
            and batter.normalized_player_name != normalize_mlb_player_name(identity.canonical_player_name)):
        return result(BattingResolutionState.IDENTITY_UNRESOLVED, "Canonical player ID and name conflict.")
    if batter.hits is None:
        return result(BattingResolutionState.STAT_MISSING, "Hits statistic is missing or invalid.", batter)
    return result(BattingResolutionState.RESOLVED, "Explicit final game and unique factual hits result.", batter)


__all__ = [
    "BatterBattingResult", "BattingBoxscoreEvidence", "BattingResolutionState",
    "BatterHitsResultEvidence", "extract_batting_results", "resolve_batter_hits_result",
    "validate_boxscore_binding",
]
