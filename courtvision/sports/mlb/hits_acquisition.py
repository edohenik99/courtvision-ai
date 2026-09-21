"""Research-only acquisition adapters for MLB batter Hits feature evidence.

This module bridges CourtVision's existing immutable prospective acquisition
machinery to the typed Hits identity/feature contracts. It deliberately stops
before at-bat projection, probability generation, candidate publication, or
wagering. Player season requests are created only after a player has been
resolved to a unique MLBAM identity from the already-bound game feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlencode

from courtvision.core.candidates import IdentityStatus
from courtvision.sports.mlb.data.prospective_context_acquisition import (
    AcquisitionPolicy,
    AcquisitionResult,
    EvidenceProvider,
    EvidenceRequest,
    acquire_event_cluster,
    build_event_clusters,
    parse_utc,
)
from courtvision.sports.mlb.hits_features import (
    BatterLineupEvidence,
    BatterSeasonHittingEvidence,
    extract_batter_lineup_evidence,
    parse_batter_season_hitting_evidence,
)
from courtvision.sports.mlb.hits_identity import (
    MLBEventIdentityBinding,
    MLBPlayerIdentityBinding,
    resolve_mlb_batter_identity,
    validate_bound_game_feed,
)
from courtvision.sports.mlb.market_data import MLBPlayerPropSourceRecord


class HitsAcquisitionError(ValueError):
    """Raised when captured Hits evidence cannot be trusted or materialized."""


@dataclass(frozen=True, slots=True)
class CapturedHitsSource:
    request_id: str
    body: bytes
    first_observed_at_utc: datetime
    captured_at_utc: datetime
    evidence_cutoff: datetime
    source_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcquiredBatterHitsEvidence:
    """Pregame baseball evidence available before opportunity projection."""

    player_binding: MLBPlayerIdentityBinding
    season_evidence: BatterSeasonHittingEvidence
    lineup_evidence: BatterLineupEvidence
    evidence_cutoff: datetime

    def __post_init__(self) -> None:
        if self.player_binding.participant_identity.identity_status is not IdentityStatus.RESOLVED:
            raise HitsAcquisitionError("acquired feature evidence requires a resolved player")
        if self.player_binding.mlbam_player_id != self.season_evidence.mlbam_player_id:
            raise HitsAcquisitionError("season evidence player conflicts with identity")
        if self.player_binding.mlbam_player_id != self.lineup_evidence.mlbam_player_id:
            raise HitsAcquisitionError("lineup evidence player conflicts with identity")
        if self.player_binding.mlbam_game_id != self.lineup_evidence.mlbam_game_id:
            raise HitsAcquisitionError("lineup evidence game conflicts with identity")
        cutoff = parse_utc(self.evidence_cutoff, "evidence_cutoff")
        if max(
            self.season_evidence.observed_at,
            self.season_evidence.evidence_cutoff,
            self.lineup_evidence.observed_at,
            self.lineup_evidence.evidence_cutoff,
        ) > cutoff:
            raise HitsAcquisitionError("feature evidence is newer than evidence_cutoff")


def _resolved_event(event_binding: MLBEventIdentityBinding) -> None:
    if not isinstance(event_binding, MLBEventIdentityBinding):
        raise TypeError("event_binding must be MLBEventIdentityBinding")
    if (
        event_binding.identity_status is not IdentityStatus.RESOLVED
        or event_binding.scheduled_event is None
        or event_binding.mlbam_game_id is None
    ):
        raise HitsAcquisitionError("Hits acquisition requires a resolved canonical event")


def _resolved_player(player_binding: MLBPlayerIdentityBinding) -> str:
    if not isinstance(player_binding, MLBPlayerIdentityBinding):
        raise TypeError("player_binding must be MLBPlayerIdentityBinding")
    if (
        player_binding.participant_identity.identity_status is not IdentityStatus.RESOLVED
        or player_binding.mlbam_player_id is None
    ):
        raise HitsAcquisitionError("season acquisition requires a uniquely resolved player")
    return player_binding.mlbam_player_id


def _season_value(season: int) -> int:
    if type(season) is not int or season <= 0:
        raise HitsAcquisitionError("season must be a positive integer")
    return season


def hits_game_feed_request(event_binding: MLBEventIdentityBinding) -> EvidenceRequest:
    """Declare the authoritative game-feed request used for roster and lineup evidence."""
    _resolved_event(event_binding)
    game_id = event_binding.mlbam_game_id
    assert game_id is not None
    return EvidenceRequest(
        request_id=f"hits-game-feed-{game_id}",
        evidence_class="volatile_pregame",
        source_name="mlb_statsapi_hits_game_feed",
        provider="mlb_statsapi",
        url=f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live",
        event_id=game_id,
    )


def hits_season_hitting_request(
    player_binding: MLBPlayerIdentityBinding,
    *,
    season: int,
) -> EvidenceRequest:
    """Declare one season-hitting request only after canonical player resolution."""
    player_id = _resolved_player(player_binding)
    season = _season_value(season)
    official_start = player_binding.event_binding.official_commence_time
    if official_start is None or season != official_start.year:
        raise HitsAcquisitionError("season hitting evidence must match the bound game season")
    canonical_name = player_binding.participant_identity.canonical_participant_name
    if not isinstance(canonical_name, str) or not canonical_name.strip():
        raise HitsAcquisitionError(
            "season acquisition requires the resolved canonical player name"
        )
    hydrate = f"stats(group=[hitting],type=[season],season={season})"
    query = urlencode({"hydrate": hydrate})
    return EvidenceRequest(
        request_id=f"hits-season-{season}-{player_id}",
        evidence_class="volatile_pregame",
        source_name="mlb_statsapi_player_season_hitting",
        provider="mlb_statsapi",
        url=f"https://statsapi.mlb.com/api/v1/people/{player_id}?{query}",
        event_id=player_binding.mlbam_game_id,
        player_id=player_id,
        season=season,
        player_name=canonical_name,
    )


def hits_season_hitting_requests(
    player_bindings: Sequence[MLBPlayerIdentityBinding],
    *,
    season: int,
) -> tuple[EvidenceRequest, ...]:
    """Build deterministic unique requests for resolved players in one bound game."""
    season = _season_value(season)
    if not player_bindings:
        raise HitsAcquisitionError("at least one resolved player is required")
    game_id: str | None = None
    by_player: dict[str, MLBPlayerIdentityBinding] = {}
    for player in player_bindings:
        player_id = _resolved_player(player)
        if game_id is None:
            game_id = player.mlbam_game_id
        elif player.mlbam_game_id != game_id:
            raise HitsAcquisitionError("one Hits season capture cannot span games")
        previous = by_player.get(player_id)
        if previous is not None and previous != player:
            raise HitsAcquisitionError("conflicting bindings for one canonical player")
        by_player[player_id] = player
    return tuple(
        hits_season_hitting_request(by_player[player_id], season=season)
        for player_id in sorted(by_player, key=int)
    )


def _single_event_cluster(
    event_binding: MLBEventIdentityBinding,
    *,
    policy: AcquisitionPolicy,
):
    _resolved_event(event_binding)
    assert event_binding.scheduled_event is not None
    clusters = build_event_clusters((event_binding.scheduled_event,), policy=policy)
    if len(clusters) != 1:
        raise HitsAcquisitionError("resolved event did not produce exactly one acquisition cluster")
    return clusters[0]


def acquire_hits_game_feed(
    event_binding: MLBEventIdentityBinding,
    *,
    observed_at_utc: datetime | str,
    evidence_cutoff: datetime | str,
    provider: EvidenceProvider,
    acquisition_root: str | Path,
    git_commit: str,
    policy: AcquisitionPolicy = AcquisitionPolicy(),
) -> AcquisitionResult:
    """Capture one immutable pregame game feed; no player guessing or scoring occurs."""
    cluster = _single_event_cluster(event_binding, policy=policy)
    return acquire_event_cluster(
        cluster,
        requested_as_of_utc=evidence_cutoff,
        observed_at_utc=observed_at_utc,
        requests=(hits_game_feed_request(event_binding),),
        provider=provider,
        acquisition_root=acquisition_root,
        git_commit=git_commit,
    )


def acquire_hits_season_hitting(
    event_binding: MLBEventIdentityBinding,
    player_bindings: Sequence[MLBPlayerIdentityBinding],
    *,
    season: int,
    observed_at_utc: datetime | str,
    evidence_cutoff: datetime | str,
    provider: EvidenceProvider,
    acquisition_root: str | Path,
    git_commit: str,
    policy: AcquisitionPolicy = AcquisitionPolicy(),
) -> AcquisitionResult:
    """Capture season hitting evidence only for already-resolved MLBAM players."""
    _resolved_event(event_binding)
    for player in player_bindings:
        if player.event_binding != event_binding:
            raise HitsAcquisitionError("season player binding must retain the same event binding")
    cluster = _single_event_cluster(event_binding, policy=policy)
    return acquire_event_cluster(
        cluster,
        requested_as_of_utc=evidence_cutoff,
        observed_at_utc=observed_at_utc,
        requests=hits_season_hitting_requests(player_bindings, season=season),
        provider=provider,
        acquisition_root=acquisition_root,
        git_commit=git_commit,
    )


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _capture_manifest(capture: AcquisitionResult) -> Mapping[str, object]:
    if not isinstance(capture, AcquisitionResult):
        raise TypeError("capture must be AcquisitionResult")
    try:
        payload = json.loads(capture.manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HitsAcquisitionError("could not read Hits acquisition manifest") from exc
    if not isinstance(payload, dict):
        raise HitsAcquisitionError("Hits acquisition manifest must be an object")
    expected_digest = payload.get("manifest_digest")
    unsigned = dict(payload)
    unsigned.pop("manifest_digest", None)
    if not isinstance(expected_digest, str) or expected_digest != _canonical_digest(unsigned):
        raise HitsAcquisitionError("Hits acquisition manifest digest mismatch")
    if payload.get("capture_id") != capture.capture_id:
        raise HitsAcquisitionError("Hits acquisition manifest capture identity mismatch")
    if payload.get("research_only") is not True:
        raise HitsAcquisitionError("Hits acquisition must remain research-only")
    if payload.get("predictions_enabled") is not False or payload.get("wagering_enabled") is not False:
        raise HitsAcquisitionError("Hits acquisition activation boundary was violated")
    return payload


def captured_hits_source(
    capture: AcquisitionResult,
    *,
    request_id: str,
) -> CapturedHitsSource:
    """Load one completed raw response and independently verify its digest and path."""
    manifest = _capture_manifest(capture)
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise HitsAcquisitionError("Hits acquisition manifest sources must be an array")
    matches = [
        item for item in sources
        if isinstance(item, Mapping) and item.get("request_id") == request_id
    ]
    if len(matches) != 1:
        raise HitsAcquisitionError("Hits acquisition requires one unique source record")
    record = matches[0]
    if record.get("availability_status") != "completed":
        raise HitsAcquisitionError(
            f"Hits source is not completed: {record.get('availability_status')}"
        )
    body_rel = record.get("body_path")
    digest = record.get("sha256")
    if not isinstance(body_rel, str) or not isinstance(digest, str):
        raise HitsAcquisitionError("Hits source lacks immutable body metadata")
    root = capture.capture_dir.resolve()
    path = (root / body_rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HitsAcquisitionError("Hits source body path is invalid")
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise HitsAcquisitionError("could not read Hits source body") from exc
    if hashlib.sha256(body).hexdigest() != digest:
        raise HitsAcquisitionError("Hits source raw response digest mismatch")
    first_observed = parse_utc(record.get("first_observed_at_utc", ""), "first_observed_at_utc")
    captured = parse_utc(record.get("captured_at_utc", ""), "captured_at_utc")
    cutoff = parse_utc(record.get("requested_as_of_utc", ""), "requested_as_of_utc")
    if first_observed > captured or captured > cutoff:
        raise HitsAcquisitionError("Hits source clocks violate pregame acquisition ordering")
    source_refs = (
        f"capture:{capture.capture_id}:{request_id}:sha256:{digest}",
        f"manifest:{capture.capture_id}",
    )
    return CapturedHitsSource(
        request_id=request_id,
        body=body,
        first_observed_at_utc=first_observed,
        captured_at_utc=captured,
        evidence_cutoff=cutoff,
        source_refs=source_refs,
    )


def resolve_hits_player_from_capture(
    source: MLBPlayerPropSourceRecord,
    event_binding: MLBEventIdentityBinding,
    game_feed_capture: AcquisitionResult,
) -> MLBPlayerIdentityBinding:
    """Resolve one quoted batter from the immutable captured game feed."""
    feed = captured_hits_source(
        game_feed_capture,
        request_id=hits_game_feed_request(event_binding).request_id,
    )
    validate_bound_game_feed(feed.body, event_binding)
    return resolve_mlb_batter_identity(
        source,
        event_binding,
        feed.body,
        observed_at=feed.first_observed_at_utc,
        evidence_cutoff=feed.evidence_cutoff,
        source_refs=feed.source_refs,
    )


def materialize_acquired_hits_evidence(
    player_binding: MLBPlayerIdentityBinding,
    *,
    season: int,
    game_feed_capture: AcquisitionResult,
    season_capture: AcquisitionResult,
) -> AcquiredBatterHitsEvidence:
    """Create typed season/lineup evidence from preserved responses, without projection."""
    player_id = _resolved_player(player_binding)
    season = _season_value(season)
    feed = captured_hits_source(
        game_feed_capture,
        request_id=hits_game_feed_request(player_binding.event_binding).request_id,
    )
    season_source = captured_hits_source(
        season_capture,
        request_id=hits_season_hitting_request(player_binding, season=season).request_id,
    )
    lineup = extract_batter_lineup_evidence(
        feed.body,
        player_binding.event_binding,
        player_binding,
        observed_at=feed.first_observed_at_utc,
        evidence_cutoff=feed.evidence_cutoff,
        source_refs=feed.source_refs,
    )
    season_evidence = parse_batter_season_hitting_evidence(
        season_source.body,
        season=season,
        player_binding=player_binding,
        observed_at=season_source.first_observed_at_utc,
        evidence_cutoff=season_source.evidence_cutoff,
        source_refs=season_source.source_refs,
    )
    if season_evidence.mlbam_player_id != player_id:
        raise HitsAcquisitionError("season evidence changed canonical player identity")
    evidence_cutoff = max(feed.evidence_cutoff, season_source.evidence_cutoff)
    return AcquiredBatterHitsEvidence(
        player_binding=player_binding,
        season_evidence=season_evidence,
        lineup_evidence=lineup,
        evidence_cutoff=evidence_cutoff,
    )


__all__ = [
    "HitsAcquisitionError",
    "CapturedHitsSource",
    "AcquiredBatterHitsEvidence",
    "hits_game_feed_request",
    "hits_season_hitting_request",
    "hits_season_hitting_requests",
    "acquire_hits_game_feed",
    "acquire_hits_season_hitting",
    "captured_hits_source",
    "resolve_hits_player_from_capture",
    "materialize_acquired_hits_evidence",
]
