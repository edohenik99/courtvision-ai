"""Market-neutral, explicitly final MLB facts from caller-supplied evidence.

No fetching, storage, market models or historical coverage claims live here.
IDs are MLBAM decimal strings; dates are caller-bound official game dates.
Source hashes cover normalized supplied JSON, not provider response bytes.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timezone
import hashlib
import json
import re
from typing import ClassVar

from courtvision.core.candidates import EventIdentity, IdentityStatus
from courtvision.sports.mlb.batting_results import (
    _count, _mapping, _mlb_id, extract_batting_results, validate_boxscore_binding,
)


FACT_SCHEMA_VERSION = "mlb_game_facts_v1"
BATTER_COUNTS = ("at_bats", "hits", "plate_appearances", "doubles", "triples",
                 "home_runs", "walks", "strikeouts", "runs", "rbi")
PITCHER_COUNTS = ("outs_recorded", "batters_faced", "strikeouts", "walks",
                  "hits_allowed", "home_runs_allowed", "pitches", "strikes")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or _mlb_id(value) != value:
        raise ValueError(f"{label} requires an explicit positive decimal string")
    return value


def _hash(value: object, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _refs(refs: object) -> tuple[str, ...]:
    if not isinstance(refs, tuple) or not refs:
        raise ValueError("source_refs must be a non-empty immutable tuple")
    if any(not isinstance(ref, str) or not ref.strip() or ref != ref.strip() for ref in refs):
        raise ValueError("source_refs must contain non-empty unpadded labels")
    # Labels are never opened. Canonical slash/order normalization is portable.
    return tuple(sorted({ref.replace("\\", "/") for ref in refs}))


def _counts(record: object, names: tuple[str, ...]) -> None:
    for name in names:
        value = getattr(record, name)
        if value is not None and _count(value) is None:
            raise ValueError(f"{name} must be a nonnegative integer or unavailable")


def innings_to_outs(value: str) -> int:
    """Baseball notation only: 6.1 is 19 outs, never decimal 6.1 innings."""
    if not isinstance(value, str) or re.fullmatch(r"(0|[1-9][0-9]*)\.[012]", value) is None:
        raise ValueError("innings_pitched must be baseball text such as 6.0, 6.1 or 6.2")
    whole, fraction = value.split(".")
    return int(whole) * 3 + int(fraction)


@dataclass(frozen=True, slots=True, kw_only=True)
class _FinalFact:
    mlbam_game_id: str
    game_date: date
    game_status: str
    observed_at: datetime
    source_refs: tuple[str, ...]
    source_hash: str
    fact_schema_version: str = field(default=FACT_SCHEMA_VERSION, init=False)
    research_only: bool = field(default=True, init=False)
    eligible_for_betting: bool = field(default=False, init=False)
    kelly_eligible: bool = field(default=False, init=False)
    eligible_for_official_pick: bool = field(default=False, init=False)
    approval_status: str = field(default="not_approved", init=False)
    role: ClassVar[str]

    def __post_init__(self) -> None:
        _id(self.mlbam_game_id, "mlbam_game_id")
        if type(self.game_date) is not date:
            raise ValueError("game_date must be an explicit official date")
        if not isinstance(self.game_status, str) or self.game_status.strip().casefold() != "final":
            raise ValueError("canonical facts require explicitly final evidence")
        observed = _aware(self.observed_at)
        if self.game_date > observed.date():
            raise ValueError("final observation cannot precede game_date")
        object.__setattr__(self, "game_status", "final")
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "source_refs", _refs(self.source_refs))
        _hash(self.source_hash, "source_hash")

    @property
    def season(self) -> int:
        return self.game_date.year

    @property
    def logical_identity(self) -> tuple[str, ...]:
        return (self.mlbam_game_id, self.role)

    @property
    def factual_record_hash(self) -> str:
        return _digest(fact_payload(self))


@dataclass(frozen=True, slots=True, kw_only=True)
class MLBGameFact(_FinalFact):
    home_team_id: str | None = None
    away_team_id: str | None = None
    home_team_name: str | None = None
    away_team_name: str | None = None
    final_home_runs: int | None = None
    final_away_runs: int | None = None
    role: ClassVar[str] = "GAME"

    def __post_init__(self) -> None:
        _FinalFact.__post_init__(self)
        for label in ("home_team_id", "away_team_id"):
            if getattr(self, label) is not None:
                _id(getattr(self, label), label)
        if self.home_team_id is not None and self.home_team_id == self.away_team_id:
            raise ValueError("home and away team identities must differ")
        for label in ("home_team_name", "away_team_name"):
            _optional_name(getattr(self, label))
        _counts(self, ("final_home_runs", "final_away_runs"))


def _optional_name(value: object) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip() or value != value.strip()):
        raise ValueError("descriptive names must be non-empty unpadded text when supplied")


@dataclass(frozen=True, slots=True, kw_only=True)
class _PlayerGameFact(_FinalFact):
    mlbam_player_id: str
    game_fact_hash: str
    player_name: str | None = None
    team_id: str | None = None
    opponent_id: str | None = None
    side: str | None = None

    def __post_init__(self) -> None:
        _FinalFact.__post_init__(self)
        _id(self.mlbam_player_id, "mlbam_player_id")
        _hash(self.game_fact_hash, "game_fact_hash")
        _optional_name(self.player_name)
        for label in ("team_id", "opponent_id"):
            if getattr(self, label) is not None:
                _id(getattr(self, label), label)
        if self.team_id is not None and self.team_id == self.opponent_id:
            raise ValueError("team and opponent identities must differ")
        if self.side not in {None, "home", "away"}:
            raise ValueError("side must be home, away or unavailable")

    @property
    def logical_identity(self) -> tuple[str, ...]:
        return (self.mlbam_game_id, self.mlbam_player_id, self.role)


@dataclass(frozen=True, slots=True, kw_only=True)
class MLBBatterGameFact(_PlayerGameFact):
    at_bats: int | None = None
    hits: int | None = None
    plate_appearances: int | None = None
    doubles: int | None = None
    triples: int | None = None
    home_runs: int | None = None
    walks: int | None = None
    strikeouts: int | None = None
    runs: int | None = None
    rbi: int | None = None
    role: ClassVar[str] = "BATTER"

    def __post_init__(self) -> None:
        _PlayerGameFact.__post_init__(self)
        _counts(self, BATTER_COUNTS)
        if self.hits is not None and self.at_bats is not None and self.hits > self.at_bats:
            raise ValueError("hits cannot exceed at_bats")
        if self.plate_appearances is not None and self.at_bats is not None and self.at_bats > self.plate_appearances:
            raise ValueError("at_bats cannot exceed plate_appearances")
        components = (self.doubles, self.triples, self.home_runs)
        if self.hits is not None and sum(x for x in components if x is not None) > self.hits:
            raise ValueError("extra-base hits exceed hits; impossible singles")

    @property
    def singles(self) -> int | None:
        if any(value is None for value in (self.hits, self.doubles, self.triples, self.home_runs)):
            return None
        return self.hits - self.doubles - self.triples - self.home_runs

    @property
    def total_bases(self) -> int | None:
        if self.singles is None:
            return None
        return self.singles + 2 * self.doubles + 3 * self.triples + 4 * self.home_runs


@dataclass(frozen=True, slots=True, kw_only=True)
class MLBPitcherGameFact(_PlayerGameFact):
    innings_pitched: str | None = None
    outs_recorded: int | None = None
    batters_faced: int | None = None
    strikeouts: int | None = None
    walks: int | None = None
    hits_allowed: int | None = None
    home_runs_allowed: int | None = None
    pitches: int | None = None
    strikes: int | None = None
    role: ClassVar[str] = "PITCHER"

    def __post_init__(self) -> None:
        _PlayerGameFact.__post_init__(self)
        _counts(self, PITCHER_COUNTS)
        if self.innings_pitched is not None:
            outs = innings_to_outs(self.innings_pitched)
            if self.outs_recorded is not None and self.outs_recorded != outs:
                raise ValueError("outs_recorded conflicts with innings_pitched")
            object.__setattr__(self, "outs_recorded", outs)
        for part, total in ((self.strikes, self.pitches), (self.home_runs_allowed, self.hits_allowed)):
            if part is not None and total is not None and part > total:
                raise ValueError("pitching component exceeds its total")


MLBFact = MLBGameFact | MLBBatterGameFact | MLBPitcherGameFact
_FACT_TYPES = {cls.role: cls for cls in (MLBGameFact, MLBBatterGameFact, MLBPitcherGameFact)}


def fact_payload(fact: MLBFact) -> dict[str, object]:
    """Portable normalized payload; no storage path participates in identity/hash."""
    if type(fact) not in _FACT_TYPES.values():
        raise TypeError("fact must be a supported canonical MLB fact")
    result = {f.name: getattr(fact, f.name) for f in fields(fact)}
    result.update(role=fact.role, game_date=fact.game_date.isoformat(),
                  observed_at=fact.observed_at.isoformat(), source_refs=list(fact.source_refs))
    return result


def fact_from_payload(payload: Mapping[str, object]) -> MLBFact:
    """Revalidate every field, including fixed safety flags and schema, on load."""
    raw = dict(payload)
    cls = _FACT_TYPES.get(raw.get("role"))
    if cls is None:
        raise ValueError("unsupported fact role")
    if set(raw) != {f.name for f in fields(cls)} | {"role"}:
        raise ValueError("fact fields differ from the canonical schema")
    values = {f.name: raw[f.name] for f in fields(cls) if f.init}
    values["game_date"] = date.fromisoformat(values["game_date"])
    values["observed_at"] = datetime.fromisoformat(values["observed_at"])
    if not isinstance(values["source_refs"], list):
        raise ValueError("serialized source_refs must be a JSON array")
    values["source_refs"] = tuple(values["source_refs"])
    fact = cls(**values)
    if canonical_json(fact_payload(fact)) != canonical_json(raw):
        raise ValueError("fact payload is noncanonical or changes fixed safety fields")
    return fact


def _event(game_id: str) -> EventIdentity:
    return EventIdentity(game_id, "caller_bound_final_game", IdentityStatus.RESOLVED, game_id)


def extract_game_fact(
    payload: Mapping[str, object], *, event_identity: EventIdentity, game_date: date,
    game_status: str, observed_at: datetime, source_refs: tuple[str, ...],
) -> MLBGameFact:
    """Read one supplied final schedule game; no schedule search/acquisition."""
    payload = _mapping(payload)
    status = validate_boxscore_binding(payload, event_identity, game_status)
    game_id = _id(event_identity.canonical_event_id, "canonical game identity")
    if "officialDate" in payload and payload["officialDate"] != game_date.isoformat():
        raise ValueError("officialDate conflicts with bound game_date")
    teams = _mapping(payload.get("teams"))
    values = {}
    for side in ("home", "away"):
        team = _mapping(teams.get(side))
        identity = _mapping(team.get("team"))
        team_id = identity.get("id")
        if team_id is not None and _mlb_id(team_id) is None:
            raise ValueError("invalid supplied team ID")
        values[f"{side}_team_id"] = _mlb_id(team_id)
        values[f"{side}_team_name"] = identity.get("name")
        values[f"final_{side}_runs"] = _count(team.get("score"))
    return MLBGameFact(mlbam_game_id=game_id, game_date=game_date, game_status=status,
                       observed_at=observed_at, source_refs=source_refs,
                       source_hash=_digest(payload), **values)


def extract_player_game_facts(
    payload: Mapping[str, object], *, game: MLBGameFact,
    observed_at: datetime, source_refs: tuple[str, ...],
) -> tuple[MLBBatterGameFact | MLBPitcherGameFact, ...]:
    """Reuse batting extraction; bind pitching rows to the same final game.

    Only nonempty role stat objects create player-game facts. A roster entry
    alone never creates a batting/pitching game. Missing counts stay None.
    Duplicate roster IDs fail closed, including identical duplicates; one
    uniquely identified player may legitimately produce both role records.
    """
    if type(game) is not MLBGameFact:
        raise TypeError("game must be MLBGameFact")
    # Revalidation also rejects tampered/deserialized non-final game objects.
    fact_from_payload(fact_payload(game))
    observed_at = _aware(observed_at)
    if observed_at < game.observed_at:
        raise ValueError("player facts cannot predate bound final game evidence")
    payload = _mapping(payload)
    if "officialDate" in payload and payload["officialDate"] != game.game_date.isoformat():
        raise ValueError("boxscore officialDate conflicts with bound game")
    batting = extract_batting_results(payload, event_identity=_event(game.mlbam_game_id),
        game_status=game.game_status, observed_at=observed_at, source_refs=source_refs)
    source_hash = _digest(payload)
    refs = _refs((*game.source_refs, *source_refs))
    teams = _mapping(payload.get("teams"))
    team_ids = {}
    for side in ("away", "home"):
        team = _mapping(teams.get(side))
        supplied = _mapping(team.get("team")).get("id")
        expected = getattr(game, f"{side}_team_id")
        if supplied is not None and (_mlb_id(supplied) is None or
                                     (expected is not None and _mlb_id(supplied) != expected)):
            raise ValueError("boxscore team identity conflicts with final game")
        team_ids[side] = expected if expected is not None else _mlb_id(supplied)
    if team_ids["home"] is not None and team_ids["home"] == team_ids["away"]:
        raise ValueError("home and away team identities must differ")
    results = []
    seen = set()
    # Traverse in the extractor's roster order; never re-extract batting stats.
    rows = [row for side in ("away", "home") for row in
            _mapping(_mapping(teams.get(side)).get("players")).values()]
    for batter, row in zip(batting.batters, rows, strict=True):
        pitching = _mapping(_mapping(_mapping(row).get("stats")).get("pitching"))
        player_id = batter.mlb_player_id
        if player_id is not None:
            if player_id in seen:
                raise ValueError("ambiguous duplicate roster player ID")
            seen.add(player_id)
        if not batter.has_batting_stats and not pitching:
            continue
        _id(player_id, "mlbam_player_id")
        side = batter.side
        values = dict(mlbam_game_id=game.mlbam_game_id, game_date=game.game_date,
            game_status=game.game_status, observed_at=observed_at, source_refs=refs,
            source_hash=source_hash, game_fact_hash=game.factual_record_hash,
            mlbam_player_id=player_id, player_name=batter.player_name, side=side,
            team_id=team_ids[side], opponent_id=team_ids["away" if side == "home" else "home"])
        if batter.has_batting_stats:
            results.append(MLBBatterGameFact(**values,
                **{name: getattr(batter, name) for name in BATTER_COUNTS}))
        if pitching:
            counts = {target: _count(pitching.get(source)) for target, source in (
                ("batters_faced", "battersFaced"), ("strikeouts", "strikeOuts"),
                ("walks", "baseOnBalls"), ("hits_allowed", "hits"),
                ("home_runs_allowed", "homeRuns"), ("pitches", "numberOfPitches"),
                ("strikes", "strikes"),
            )}
            results.append(MLBPitcherGameFact(**values,
                innings_pitched=pitching.get("inningsPitched"), **counts))
    return tuple(sorted(results, key=lambda fact: fact.logical_identity))
