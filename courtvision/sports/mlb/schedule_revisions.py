"""Shared MLB schedule observation/revision contract extracted from Statcast history.

Immutable identity and canonical selection retain the established policy:
capture time, finality rank, official date, scheduled start, state digest, then
response digest. Every occurrence is retained; names are descriptive. Conflicts
are reported and omitted from resolved games for callers to fail closed.
"""
from __future__ import annotations

from datetime import date
import json
from typing import Final, Mapping, Sequence, TypeAlias
from urllib.parse import parse_qs, urlparse

from courtvision.sports.mlb.data.prospective_context_acquisition import (
    EvidenceRequest, ProviderResponse, ProspectiveAcquisitionError, parse_utc, utc_text,
)
from courtvision.sports.mlb.game_facts import canonical_json as _canonical_json, _digest as _value_digest

ScheduleObservation: TypeAlias = dict[str, object]
ResolvedScheduleGame: TypeAlias = dict[str, object]
SCHEDULE_REVISION_POLICY_VERSION = "cv_mlb_schedule_revisions_v1"

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


def _mlbam_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text.isdigit() or len(text) < 6 or int(text) <= 0:
        raise ProspectiveAcquisitionError(f"{field_name} identity mismatch")
    return text


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


def is_final_schedule_state(state: Mapping[str, object]) -> bool:
    detailed = str(state.get("detailed_state") or "").strip().casefold()
    abstract = str(state.get("abstract_state") or "").strip().casefold()
    coded = str(state.get("coded_state") or "").strip().casefold()
    status_code = str(state.get("status_code") or "").strip().casefold()
    if detailed in _AMBIGUOUS_DETAILED_STATES:
        return False
    return abstract == "final" and (
        detailed in _FINAL_DETAILED_STATES or coded == "f" or status_code == "f"
    )


def schedule_state_rank(state: Mapping[str, object]) -> int:
    if is_final_schedule_state(state):
        return 4
    detailed = str(state.get("detailed_state") or "").strip().casefold()
    abstract = str(state.get("abstract_state") or "").strip().casefold()
    if detailed in {"in progress", "manager challenge", "warmup"} or abstract == "live":
        return 3
    if detailed in {"scheduled", "pre-game", "preview"} or abstract == "preview":
        return 2
    return 1


def schedule_state_payload(observation: Mapping[str, object]) -> dict[str, object]:
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


def schedule_state_from_game(date_bucket: str | None, game: Mapping[str, object]) -> ScheduleObservation:
    """The same normalized mutable state for reconciliation and payload binding."""
    status = _nested_mapping(game.get("status"), "historical game status")
    return {
        "date_bucket": date_bucket,
        "scheduled_start_utc": utc_text(parse_utc(game.get("gameDate", ""), "historical gameDate")),
        "official_date": str(game.get("officialDate") or "").strip(),
        "abstract_state": str(status.get("abstractGameState") or ""),
        "detailed_state": str(status.get("detailedState") or ""),
        "coded_state": str(status.get("codedGameState") or ""),
        "status_code": str(status.get("statusCode") or ""),
        "status_payload": dict(status),
    }


def selected_schedule_game_payload(
    candidates: Sequence[tuple[str | None, Mapping[str, object]]], game: ResolvedScheduleGame,
) -> dict[str, object]:
    """Bind the selected state back to exactly one distinct supplied game payload.

    Identical full payloads may repeat. Different facts for an otherwise equal
    selected state are ambiguous, so publication callers must fail closed.
    No score/content field is used to choose between conflicting observations.
    """
    selected = schedule_state_payload(game["selected_canonical_state"])
    matches = {
        _value_digest(raw): dict(raw) for bucket, raw in candidates
        if _mlbam_id(raw.get("gamePk"), "historical game") == game["game_id"]
        and schedule_state_from_game(bucket, raw) == selected
    }
    if len(matches) != 1:
        raise ProspectiveAcquisitionError("selected schedule state has ambiguous source content")
    return next(iter(matches.values()))


def resolve_schedule_responses(
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
                    **schedule_state_from_game(date_bucket, raw_game),
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
            _value_digest(schedule_state_payload(observation))
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
                schedule_state_rank(observation),
                str(observation.get("official_date") or ""),
                str(observation.get("scheduled_start_utc") or ""),
                _value_digest(schedule_state_payload(observation)),
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
            **schedule_state_payload(selected),
            "source_request_id": selected["source_request_id"],
            "source_response_digest": selected["source_response_digest"],
            "source_response_path": selected["source_response_path"],
            "captured_at_utc": selected["captured_at_utc"],
            "is_final": is_final_schedule_state(selected),
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
