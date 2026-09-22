"""CourtVision-owned MLB batter at-bat opportunity projection.

Version 1 is deliberately conservative. It projects at-bats from the batter's
own observed season at-bats per game and only runs when the captured pregame
lineup explicitly contains that batter. Batting-order position is retained as
provenance but is not yet used as a calibrated multiplier.

No market price, sportsbook, edge, Kelly, OfficialPick, or wagering state is
accepted or produced here.
"""

from __future__ import annotations

from datetime import datetime
import json
import math

from courtvision.sports.mlb.batter_hits import BatterHitsBaselineFeatures
from courtvision.sports.mlb.hits_acquisition import AcquiredBatterHitsEvidence
from courtvision.sports.mlb.hits_features import (
    BatterAtBatProjectionEvidence,
    assemble_batter_hits_features,
)


AB_PROJECTION_MODEL_VERSION = "cv_ab_projection_v1"
AB_PROJECTION_METHOD = "season_at_bats_per_game_confirmed_lineup"
AB_PROJECTION_SOURCE = "courtvision"


class AtBatProjectionError(ValueError):
    """Raised when CourtVision lacks trustworthy inputs for an AB projection."""


def _aware(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise AtBatProjectionError(f"{label} must be a timezone-aware datetime")
    return value


def project_batter_at_bats(
    acquired: AcquiredBatterHitsEvidence,
    *,
    generated_at: datetime,
) -> BatterAtBatProjectionEvidence:
    """Project at-bats from observed season AB/game for a confirmed starter.

    v1 intentionally does not fabricate an opportunity value when lineup status
    or games-played history is unavailable. Batting-order slot is retained for
    later calibration work but does not alter the v1 arithmetic.
    """

    if not isinstance(acquired, AcquiredBatterHitsEvidence):
        raise TypeError("acquired must be AcquiredBatterHitsEvidence")

    lineup = acquired.lineup_evidence
    season = acquired.season_evidence
    player = acquired.player_binding
    event = player.event_binding

    if (
        lineup.lineup_status != "statsapi_batting_order_present"
        or lineup.batting_order_position is None
    ):
        raise AtBatProjectionError(
            "cv_ab_projection_v1 requires confirmed batting-order presence"
        )

    games_played = season.games_played
    if type(games_played) is not int or games_played <= 0:
        raise AtBatProjectionError(
            "cv_ab_projection_v1 requires positive season games_played evidence"
        )

    generated = _aware(generated_at, "generated_at")
    evidence_cutoff = _aware(acquired.evidence_cutoff, "evidence_cutoff")
    if generated < evidence_cutoff:
        raise AtBatProjectionError(
            "projection cannot be generated before its evidence cutoff"
        )

    official_start = event.official_commence_time
    if official_start is None:
        raise AtBatProjectionError("projection requires a resolved official game start")
    first_start = min(event.provider_commence_time, official_start)
    if generated >= first_start:
        raise AtBatProjectionError("projection must be generated before both game starts")

    projected = season.at_bats / games_played
    if not math.isfinite(projected) or projected <= 0:
        raise AtBatProjectionError("season AB/game must be finite and positive")

    identity = player.participant_identity
    canonical_name = identity.canonical_participant_name
    if not isinstance(canonical_name, str) or not canonical_name.strip():
        raise AtBatProjectionError("projection requires a canonical player name")
    game_id = player.mlbam_game_id
    player_id = player.mlbam_player_id
    if player_id is None:
        raise AtBatProjectionError("projection requires a canonical player ID")

    refs = list(season.source_refs)
    refs.extend(lineup.source_refs)
    metadata = {
        "model_version": AB_PROJECTION_MODEL_VERSION,
        "projection_method": AB_PROJECTION_METHOD,
        "season": season.season,
        "season_at_bats": season.at_bats,
        "season_games_played": games_played,
        "season_at_bats_per_game": projected,
        "batting_order_position": lineup.batting_order_position,
        "lineup_status": lineup.lineup_status,
        "game_id": game_id,
        "player_id": player_id,
        "evidence_cutoff": evidence_cutoff.isoformat(),
        "generated_at": generated.isoformat(),
    }
    refs.append(
        "cv_ab_projection_provenance="
        + json.dumps(metadata, sort_keys=True, separators=(",", ":"))
    )

    return BatterAtBatProjectionEvidence(
        mlbam_game_id=game_id,
        mlbam_player_id=player_id,
        player_name=canonical_name,
        projected_at_bats=projected,
        projection_method=AB_PROJECTION_METHOD,
        model_version=AB_PROJECTION_MODEL_VERSION,
        source=AB_PROJECTION_SOURCE,
        generated_at=generated,
        evidence_cutoff=evidence_cutoff,
        source_refs=tuple(dict.fromkeys(refs)),
    )


def assemble_acquired_batter_hits_features(
    acquired: AcquiredBatterHitsEvidence,
    *,
    generated_at: datetime,
) -> BatterHitsBaselineFeatures:
    """Project CourtVision AB opportunity and assemble the existing Hits features."""

    projection = project_batter_at_bats(acquired, generated_at=generated_at)
    return assemble_batter_hits_features(
        event_binding=acquired.player_binding.event_binding,
        player_binding=acquired.player_binding,
        season_evidence=acquired.season_evidence,
        lineup_evidence=acquired.lineup_evidence,
        projection_evidence=projection,
        evidence_cutoff=projection.generated_at,
    )


__all__ = [
    "AB_PROJECTION_METHOD",
    "AB_PROJECTION_MODEL_VERSION",
    "AB_PROJECTION_SOURCE",
    "AtBatProjectionError",
    "assemble_acquired_batter_hits_features",
    "project_batter_at_bats",
]
