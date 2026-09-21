"""Pure supplied baseball evidence and pregame assembly for the Hits baseline.

No acquisition or opportunity model lives here. References are opaque labels,
not paths to open. Observation times are caller-supplied provenance, not proof
that a source was historically available. Market prices never enter this API.
Downstream callers must retain the binding and generate probabilities/candidates
before both bound starts: the existing candidate adapter knows only the quote's
provider start time. This module validates feature evidence, not processing time.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import json
import math

from courtvision.core.candidates import IdentityStatus
from courtvision.sports.mlb.batter_hits import BatterHitsBaselineFeatures
from courtvision.sports.mlb.hits_identity import (
    MLBEventIdentityBinding,
    MLBPlayerIdentityBinding,
    event_roster_players,
    validate_bound_game_feed,
)
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be non-empty unpadded text")


def _id(value: object, label: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"{label} must be an explicit positive decimal MLBAM ID")
    text = str(value)
    if not text.isascii() or not text.isdecimal() or text.startswith("0"):
        raise ValueError(f"{label} must be an explicit positive decimal MLBAM ID")
    return text


def _name(value: object) -> str:
    _text(value, "player_name")
    result = normalize_mlb_player_name(value)
    if not result:
        raise ValueError("player_name must have a normalized comparison key")
    return result


def _aware(value: object, label: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")


def _refs(value: object) -> None:
    if not isinstance(value, tuple) or not value:
        raise ValueError("source_refs must be a non-empty immutable tuple")
    for reference in value:
        _text(reference, "source reference")


def _observation(observed_at: datetime, evidence_cutoff: datetime) -> None:
    _aware(observed_at, "observed_at")
    _aware(evidence_cutoff, "evidence_cutoff")
    if observed_at > evidence_cutoff:
        raise ValueError("observation must be at or before evidence_cutoff")


def _mapping(value: object, label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _bound_player(event: MLBEventIdentityBinding, player: MLBPlayerIdentityBinding) -> None:
    if not isinstance(event, MLBEventIdentityBinding) or not isinstance(player, MLBPlayerIdentityBinding):
        raise TypeError("event_binding and player_binding must be typed identity bindings")
    if event.identity_status is not IdentityStatus.RESOLVED:
        raise ValueError("feature evidence requires a resolved event")
    if player.participant_identity.identity_status is not IdentityStatus.RESOLVED:
        raise ValueError("feature evidence requires a resolved canonical player")
    if player.event_binding != event:
        raise ValueError("player evidence must retain the same provider/canonical event binding")


@dataclass(frozen=True, slots=True)
class BatterSeasonHittingEvidence:
    """Supplied season batting totals; unavailable counts cannot become zero."""

    season: int
    mlbam_player_id: str
    player_name: str
    hits: int
    at_bats: int
    observed_at: datetime
    evidence_cutoff: datetime
    source: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.season) is not int or self.season <= 0:
            raise ValueError("season must be a positive integer")
        if _id(self.mlbam_player_id, "mlbam_player_id") != self.mlbam_player_id:
            raise ValueError("mlbam_player_id must be a decimal string")
        _name(self.player_name)
        if type(self.hits) is not int or type(self.at_bats) is not int:
            raise ValueError("hits and at_bats must be integer counts")
        if not 0 <= self.hits <= self.at_bats or self.at_bats <= 0:
            raise ValueError("require 0 <= hits <= at_bats and at_bats > 0")
        _observation(self.observed_at, self.evidence_cutoff)
        if self.season > self.observed_at.year:
            raise ValueError("season cannot be after the observation year")
        _text(self.source, "source")
        _refs(self.source_refs)


@dataclass(frozen=True, slots=True)
class BatterAtBatProjectionEvidence:
    """An external opportunity projection, never an estimate inferred here.

    evidence_cutoff describes the projection's inputs; generated_at describes
    when the supplied projection existed. Both must precede feature assembly.
    There is intentionally no default for projected_at_bats.
    """

    mlbam_game_id: str
    mlbam_player_id: str
    player_name: str
    projected_at_bats: float
    projection_method: str
    model_version: str
    source: str
    generated_at: datetime
    evidence_cutoff: datetime
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for label in ("mlbam_game_id", "mlbam_player_id"):
            if _id(getattr(self, label), label) != getattr(self, label):
                raise ValueError(f"{label} must be a decimal string")
        _name(self.player_name)
        value = self.projected_at_bats
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("projected_at_bats must be finite and positive")
        try:
            valid = math.isfinite(value) and value > 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("projected_at_bats must be finite and positive")
        for label in ("projection_method", "model_version", "source"):
            _text(getattr(self, label), label)
        _aware(self.generated_at, "generated_at")
        _aware(self.evidence_cutoff, "evidence_cutoff")
        if self.evidence_cutoff > self.generated_at:
            raise ValueError("projection evidence_cutoff must be at or before generated_at")
        _refs(self.source_refs)


@dataclass(frozen=True, slots=True)
class BatterLineupEvidence:
    """Factual batting-order presence, without confirmation or DNP semantics."""

    mlbam_game_id: str
    mlbam_player_id: str
    player_name: str
    team_side: str | None
    batting_order_position: int | None
    lineup_status: str
    observed_at: datetime
    evidence_cutoff: datetime
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for label in ("mlbam_game_id", "mlbam_player_id"):
            if _id(getattr(self, label), label) != getattr(self, label):
                raise ValueError(f"{label} must be a decimal string")
        _name(self.player_name)
        if self.team_side not in {None, "home", "away"}:
            raise ValueError("team_side must be home, away, or unavailable")
        if self.lineup_status not in {"statsapi_batting_order_present", "not_listed", "unavailable"}:
            raise ValueError("unsupported factual lineup_status")
        position = self.batting_order_position
        if self.lineup_status == "statsapi_batting_order_present":
            if type(position) is not int or not 1 <= position <= 9 or self.team_side is None:
                raise ValueError("batting-order presence requires side and position 1..9")
        elif position is not None:
            raise ValueError("absent or unavailable lineup evidence cannot claim a position")
        _observation(self.observed_at, self.evidence_cutoff)
        _refs(self.source_refs)


def extract_batter_lineup_evidence(
    game_feed: bytes | Mapping,
    event_binding: MLBEventIdentityBinding,
    player_binding: MLBPlayerIdentityBinding,
    *,
    observed_at: datetime,
    evidence_cutoff: datetime,
    source_refs: tuple[str, ...],
) -> BatterLineupEvidence:
    """Read supplied battingOrder only after validating the game and roster."""
    _bound_player(event_binding, player_binding)
    _observation(observed_at, evidence_cutoff)
    _refs(source_refs)
    if evidence_cutoff >= min(event_binding.provider_commence_time, event_binding.official_commence_time):
        raise ValueError("lineup evidence must be pregame")
    payload = validate_bound_game_feed(game_feed, event_binding)
    roster = event_roster_players(game_feed, event_binding)
    matching = [entry for entry in roster if entry.mlbam_player_id == player_binding.mlbam_player_id]
    if len(matching) != 1 or _name(matching[0].player_name) != _name(
        player_binding.participant_identity.canonical_participant_name
    ):
        raise ValueError("lineup feed must contain the bound canonical player")
    roster_player = matching[0]
    side = roster_player.team_side
    if player_binding.team_side is not None and side != player_binding.team_side:
        raise ValueError("lineup feed conflicts with the bound player team")
    live = _mapping(payload.get("liveData", {}), "liveData")
    box = _mapping(live.get("boxscore", {}), "boxscore")
    teams = _mapping(box.get("teams", {}), "boxscore teams")
    orders: dict[str, tuple[str, ...] | None] = {}
    # event_roster_players already validates order IDs, uniqueness across both
    # sides, maximum length, and roster/team consistency. Interpret only presence
    # here; null and absent orders stay unavailable, while an empty list is known.
    for current_side in ("away", "home"):
        team = _mapping(teams.get(current_side, {}), "boxscore team")
        order = team.get("battingOrder")
        orders[current_side] = None if order is None else tuple(str(value) for value in order)
    present = [s for s, ids in orders.items() if ids is not None and player_binding.mlbam_player_id in ids]
    if present:
        found_side = present[0]  # Uniqueness proved across both batting orders above.
        if side is not None and side != found_side:
            raise ValueError("battingOrder conflicts with canonical player team")
        side = found_side
        position = orders[side].index(player_binding.mlbam_player_id) + 1
        status = "statsapi_batting_order_present"
    else:
        position = None
        relevant = (orders[side],) if side is not None else tuple(orders.values())
        status = "unavailable" if any(order is None for order in relevant) else "not_listed"
    return BatterLineupEvidence(
        event_binding.mlbam_game_id, player_binding.mlbam_player_id,
        roster_player.player_name, side, position, status,
        observed_at, evidence_cutoff, source_refs,
    )


def _json_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON value")


def parse_batter_season_hitting_evidence(
    payload: bytes | Mapping,
    *,
    season: int,
    player_binding: MLBPlayerIdentityBinding,
    observed_at: datetime,
    evidence_cutoff: datetime,
    source_refs: tuple[str, ...],
) -> BatterSeasonHittingEvidence:
    """Parse one explicitly identified StatsAPI-like people/season/hitting split.

    This intentionally supports only a single person, one season hitting block,
    and one aggregate split. Team-split aggregation and choosing among multiple
    season blocks require an upstream policy; this parser never guesses.
    A split may inherit identity from its enclosing person, but any embedded
    player identity must agree. No missing count receives a default.
    """
    if not isinstance(player_binding, MLBPlayerIdentityBinding):
        raise TypeError("player_binding must be MLBPlayerIdentityBinding")
    _bound_player(player_binding.event_binding, player_binding)
    if isinstance(payload, bytes):
        try:
            payload = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_json_object,
                                 parse_constant=_invalid_constant)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("season evidence must be valid JSON") from exc
    root = _mapping(payload, "season payload")
    people = root.get("people")
    if not isinstance(people, list) or len(people) != 1:
        raise ValueError("season payload requires exactly one person")
    person = _mapping(people[0], "person")
    identity = player_binding.participant_identity
    if _id(person.get("id"), "person.id") != player_binding.mlbam_player_id:
        raise ValueError("season evidence has the wrong player ID")
    player_name = person.get("fullName")
    if _name(player_name) != _name(identity.canonical_participant_name):
        raise ValueError("season evidence has a conflicting player name")
    blocks = person.get("stats")
    if not isinstance(blocks, list) or len(blocks) != 1:
        raise ValueError("season evidence requires exactly one batting-stat block")
    block = _mapping(blocks[0], "stats block")
    if (_mapping(block.get("type"), "stat type").get("displayName") != "season"
            or _mapping(block.get("group"), "stat group").get("displayName") != "hitting"):
        raise ValueError("only explicitly identified season hitting stats are supported")
    splits = block.get("splits")
    if not isinstance(splits, list) or len(splits) != 1:
        raise ValueError("season evidence requires exactly one unambiguous split")
    split = _mapping(splits[0], "season split")
    if split.get("season") != str(season):
        raise ValueError("season split does not match the supplied season")
    if "player" in split:
        player = _mapping(split["player"], "split player")
        if (_id(player.get("id"), "split player.id") != player_binding.mlbam_player_id
                or _name(player.get("fullName")) != _name(player_name)):
            raise ValueError("season split has conflicting player identity")
    counts = _mapping(split.get("stat"), "season stat")
    evidence = BatterSeasonHittingEvidence(
        season, player_binding.mlbam_player_id, player_name,
        counts.get("hits"), counts.get("atBats"), observed_at, evidence_cutoff,
        "mlb_statsapi", source_refs,
    )
    if evidence_cutoff >= min(player_binding.event_binding.provider_commence_time,
                              player_binding.event_binding.official_commence_time):
        raise ValueError("season evidence must be pregame")
    return evidence


def assemble_batter_hits_features(
    *,
    event_binding: MLBEventIdentityBinding,
    player_binding: MLBPlayerIdentityBinding,
    season_evidence: BatterSeasonHittingEvidence,
    lineup_evidence: BatterLineupEvidence,
    projection_evidence: BatterAtBatProjectionEvidence,
    evidence_cutoff: datetime,
) -> BatterHitsBaselineFeatures:
    """Join canonical baseball evidence while retaining the provider event ID.

    Lineup status is provenance only. No multiplier, opportunity default, price,
    market comparison, finality, or betting decision is produced here.
    """
    _bound_player(event_binding, player_binding)
    for value, expected in ((season_evidence, BatterSeasonHittingEvidence),
                            (lineup_evidence, BatterLineupEvidence),
                            (projection_evidence, BatterAtBatProjectionEvidence)):
        if not isinstance(value, expected):
            raise TypeError(f"feature input must be {expected.__name__}")
    _aware(evidence_cutoff, "evidence_cutoff")
    if evidence_cutoff >= min(event_binding.provider_commence_time, event_binding.official_commence_time):
        raise ValueError("feature evidence_cutoff must be before both game starts")
    identity = player_binding.participant_identity
    for component in (season_evidence, lineup_evidence, projection_evidence):
        if component.mlbam_player_id != player_binding.mlbam_player_id:
            raise ValueError("all canonical player IDs must agree")
        if _name(component.player_name) != _name(identity.canonical_participant_name):
            raise ValueError("all feature player names must be compatible")
    for component in (lineup_evidence, projection_evidence):
        if component.mlbam_game_id != event_binding.mlbam_game_id:
            raise ValueError("all canonical event IDs must agree")
    if (player_binding.team_side is not None
            and lineup_evidence.team_side != player_binding.team_side):
        raise ValueError("lineup team must match the bound player team")
    components = (event_binding, player_binding, season_evidence, lineup_evidence, projection_evidence)
    for component in components:
        available_at = (component.generated_at if isinstance(component, BatterAtBatProjectionEvidence)
                        else component.observed_at)
        if available_at > evidence_cutoff or component.evidence_cutoff > evidence_cutoff:
            raise ValueError("all evidence must be available by feature evidence_cutoff")
    refs: list[str] = []
    for component in components:
        refs.extend(component.source_refs)
    # Preserve the typed inputs' clocks and canonical identity alongside every
    # opaque source label, because the existing baseline has only provenance refs.
    metadata = {
        "game_id": event_binding.mlbam_game_id,
        "canonical_source": event_binding.canonical_source,
        "provider_commence_time": event_binding.provider_commence_time.isoformat(),
        "official_commence_time": event_binding.official_commence_time.isoformat(),
        "schedule_start_drift_seconds": event_binding.schedule_start_drift_seconds,
        "provider": event_binding.provider,
        "provider_event_id": event_binding.provider_event_id,
        "player_id": player_binding.mlbam_player_id,
        "canonical_player_name": identity.canonical_participant_name,
        "event_identity_method": event_binding.identity_method,
        "player_identity_method": identity.identity_method,
        "season": str(season_evidence.season),
        "season_source": season_evidence.source,
        "team_side": lineup_evidence.team_side,
        "batting_order_position": lineup_evidence.batting_order_position,
        "projection_method": projection_evidence.projection_method,
        "projection_model_version": projection_evidence.model_version,
        "projection_source": projection_evidence.source,
    }
    for label, component in zip(("event", "player", "season", "lineup", "projection"), components):
        available = component.generated_at if label == "projection" else component.observed_at
        metadata[f"{label}.available_at"] = available.isoformat()
        metadata[f"{label}.evidence_cutoff"] = component.evidence_cutoff.isoformat()
    refs.append("hits_feature_provenance=" + json.dumps(metadata, sort_keys=True, separators=(",", ":")))
    return BatterHitsBaselineFeatures(
        season_hits=season_evidence.hits,
        season_at_bats=season_evidence.at_bats,
        projected_at_bats=projection_evidence.projected_at_bats,
        lineup_status=lineup_evidence.lineup_status,
        evidence_cutoff=evidence_cutoff,
        source_refs=tuple(dict.fromkeys(refs)),
        batter_name=identity.participant_name,
        event_id=event_binding.provider_event_id,
    )


__all__ = [
    "BatterSeasonHittingEvidence", "BatterAtBatProjectionEvidence", "BatterLineupEvidence",
    "parse_batter_season_hitting_evidence", "extract_batter_lineup_evidence",
    "assemble_batter_hits_features",
]
