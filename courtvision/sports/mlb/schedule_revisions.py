"""Shared MLB schedule observation/revision contract extracted from Statcast history.

Immutable identity and canonical selection retain the established policy:
capture time, finality rank, official date, scheduled start, state digest, then
response digest. Every occurrence is retained; names are descriptive. Conflicts
are reported and omitted from resolved games for callers to fail closed.
"""
from __future__ import annotations

from datetime import date
import json
import re
from typing import Mapping, Sequence, TypeAlias
from urllib.parse import parse_qs, urlparse

from courtvision.sports.mlb.data.prospective_context_acquisition import (
    EvidenceRequest, ProviderResponse, ProspectiveAcquisitionError, parse_utc, utc_text,
)
from courtvision.sports.mlb.game_facts import canonical_json as _canonical_json, _digest as _value_digest
from courtvision.sports.mlb.game_finality import classify_game_finality
from courtvision.sports.mlb.data.crosswalk_validation import MLB_TEAM_ABBREVIATIONS

ScheduleObservation: TypeAlias = dict[str, object]
ResolvedScheduleGame: TypeAlias = dict[str, object]
SCHEDULE_REVISION_POLICY_VERSION = "cv_mlb_schedule_revisions_v1"
PARTICIPANT_RESOLUTION_POLICY = "postseason-two-club-placeholder-monotonic-v1"
_POSTSEASON_TYPES = frozenset({"F", "D", "L", "W"})
# IDs/names are corroborated by all 30 clubs in preserved LIVE-01 schedules.
# Only this explicit concrete-club crosswalk can resolve a candidate token.
_CONCRETE_CLUBS = {
    "LAA": ("108", "Los Angeles Angels"), "ARI": ("109", "Arizona Diamondbacks"),
    "BAL": ("110", "Baltimore Orioles"), "BOS": ("111", "Boston Red Sox"),
    "CHC": ("112", "Chicago Cubs"), "CIN": ("113", "Cincinnati Reds"),
    "CLE": ("114", "Cleveland Guardians"), "COL": ("115", "Colorado Rockies"),
    "DET": ("116", "Detroit Tigers"), "HOU": ("117", "Houston Astros"),
    "KC": ("118", "Kansas City Royals"), "LAD": ("119", "Los Angeles Dodgers"),
    "WSH": ("120", "Washington Nationals"), "NYM": ("121", "New York Mets"),
    "ATH": ("133", "Athletics"), "PIT": ("134", "Pittsburgh Pirates"),
    "SD": ("135", "San Diego Padres"), "SEA": ("136", "Seattle Mariners"),
    "SF": ("137", "San Francisco Giants"), "STL": ("138", "St. Louis Cardinals"),
    "TB": ("139", "Tampa Bay Rays"), "TEX": ("140", "Texas Rangers"),
    "TOR": ("141", "Toronto Blue Jays"), "MIN": ("142", "Minnesota Twins"),
    "PHI": ("143", "Philadelphia Phillies"), "ATL": ("144", "Atlanta Braves"),
    "CWS": ("145", "Chicago White Sox"), "MIA": ("146", "Miami Marlins"),
    "NYY": ("147", "New York Yankees"), "MIL": ("158", "Milwaukee Brewers"),
}
_CONCRETE_IDS = frozenset(team_id for team_id, _ in _CONCRETE_CLUBS.values())


def _placeholder_candidates(team: Mapping[str, object]) -> tuple[str, ...] | None:
    """Recognize only the observed provider object/schema and two-club syntax."""
    team_id, name = str(team.get("id", "")), team.get("name")
    if (set(team) != {"id", "name", "link"} or team_id in _CONCRETE_IDS
            or team.get("link") != f"/api/v1/teams/{team_id}"
            or not isinstance(name, str) or re.fullmatch(r"[A-Z]{2,3}/[A-Z]{2,3}", name) is None):
        return None
    tokens = name.split("/")
    if (len(set(tokens)) != 2 or not set(tokens) <= MLB_TEAM_ABBREVIATIONS
            or not set(tokens) <= _CONCRETE_CLUBS.keys()):
        return None
    return tuple(sorted(_CONCRETE_CLUBS[token][0] for token in tokens))



def _participant_query_matches(
    query: Mapping[str, list[str]], identity: Mapping[str, object],
) -> bool:
    """Require an explicit MLB query whose scope agrees with this row."""
    if query.get("sportId") != ["1"]:
        return False
    if "gameTypes" in query:
        values = query["gameTypes"]
        if len(values) != 1:
            return False
        types = values[0].split(",")
        if (len(set(types)) != len(types)
                or not set(types) <= (_POSTSEASON_TYPES | {"R"})
                or identity["game_type"] not in types):
            return False
    if "leagueId" in query:
        values = query["leagueId"]
        if (len(values) != 1 or not values[0].isdigit() or int(values[0]) <= 0
                or identity["league_id"] != values[0]):
            return False
    return True

def _observation_ref(observation: ScheduleObservation) -> dict[str, object]:
    return {key: observation[key] for key in (
        "captured_at_utc", "source_request_id", "source_response_digest", "source_response_path")}


def _resolve_participant_slot(
    entries: Sequence[tuple[ScheduleObservation, dict[str, dict[str, object]], bool]], side: str,
) -> dict[str, object] | None:
    """Prove a chronological placeholder prefix followed by one concrete club.

    Equal capture times cannot establish a transition. Provider/name/link and
    candidate membership must agree; unknown forms remain ordinary conflicts.
    """
    ordered = sorted(entries, key=lambda entry: (
        parse_utc(entry[0]["captured_at_utc"], "schedule observation captured_at_utc"),
        _canonical_json(entry[0]), _canonical_json(entry[1])))
    first, teams, _ = ordered[0]
    placeholder = teams[side]
    candidates = _placeholder_candidates(placeholder)
    if candidates is None:
        return None
    resolved_team = None
    resolved_observation = None
    last_placeholder_time = None
    for observation, teams, provider_valid in ordered:
        identity = observation["identity"]
        if (not provider_valid or identity["game_type"] not in _POSTSEASON_TYPES
                or identity["sport_id"] != "1"):
            return None
        team = teams[side]
        captured = parse_utc(observation["captured_at_utc"], "schedule observation captured_at_utc")
        if _placeholder_candidates(team) is not None:
            if resolved_team is not None or team != placeholder:
                return None
            last_placeholder_time = captured
            continue
        team_id = str(team.get("id", ""))
        if (team_id not in candidates or team.get("link") != f"/api/v1/teams/{team_id}"
                or (team_id, team.get("name")) not in _CONCRETE_CLUBS.values()
                or set(team) != {"id", "name", "link"}
                or last_placeholder_time is None or captured <= last_placeholder_time):
            return None
        if resolved_team is not None and team != resolved_team:
            return None
        if resolved_team is None:
            resolved_team, resolved_observation = team, observation
    if resolved_team is None:
        return None
    return {"side": side, "placeholder_team": placeholder, "resolved_team": resolved_team,
            "first_placeholder_observation": _observation_ref(first),
            "concrete_resolution_observation": _observation_ref(resolved_observation)}


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
    status = state.get("status_payload")
    if status is None:
        status = {provider: state[key] for key, provider in (
            ("abstract_state", "abstractGameState"), ("detailed_state", "detailedState"),
            ("coded_state", "codedGameState"), ("status_code", "statusCode"),
        ) if state.get(key) not in (None, "")}
    return classify_game_finality(status).is_final


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
    A proven participant resolution also binds the concrete canonical slots.
    No score/content field is used to choose between conflicting observations.
    """
    selected = schedule_state_payload(game["selected_canonical_state"])
    matches = {
        _value_digest(raw): dict(raw) for bucket, raw in candidates
        if _mlbam_id(raw.get("gamePk"), "historical game") == game["game_id"]
        and schedule_state_from_game(bucket, raw) == selected
        and (not game.get("participant_resolution_count") or all(
            str(raw["teams"][side]["team"]["id"]) == game["identity"][side + "_team_id"]
            for side in ("away", "home")))
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
    participant_entries: dict[str, list[tuple[ScheduleObservation, dict[str, dict[str, object]], bool]]] = {}
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
        url = urlparse(request.url)
        query = parse_qs(url.query, keep_blank_values=True)
        provider_valid = (request.provider == "mlb_statsapi" and url.scheme == "https"
                          and url.netloc == "statsapi.mlb.com" and url.path == "/api/v1/schedule")
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
                participant_entries.setdefault(game_id, []).append(
                    (observation, {"away": dict(away_team), "home": dict(home_team)},
                     provider_valid and _participant_query_matches(query, observation["identity"])))

    resolved: dict[str, dict[str, object]] = {}
    identity_conflicts: list[dict[str, object]] = []
    revision_count = 0
    reconciled_game_count = 0
    duplicate_observation_count = 0
    participant_resolution_count = 0
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
        participant_resolutions = []
        for field in identity_fields:
            values = sorted(
                {
                    str(observation["identity"].get(field))
                    for observation in observations
                    if observation["identity"].get(field) not in {None, ""}
                }
            )
            if len(values) > 1:
                if field in {"away_team_id", "home_team_id"}:
                    resolution = _resolve_participant_slot(participant_entries[game_id], field.split("_")[0])
                    if resolution is not None:
                        participant_resolutions.append(resolution)
                        continue
                conflicts[field] = values
        # Placeholder names encode candidate identity, unlike descriptive club
        # names. A stable opaque ID cannot hide a changed candidate set/object.
        for side in ("away", "home"):
            entries = participant_entries[game_id]
            teams = [entry[1][side] for entry in entries]
            if (len({str(team["id"]) for team in teams}) == 1
                    and any(valid and observation["identity"]["game_type"] in _POSTSEASON_TYPES
                            and observation["identity"]["sport_id"] == "1"
                            and _placeholder_candidates(team[side]) is not None
                            for observation, team, valid in entries)
                    and len({_canonical_json(team) for team in teams}) != 1):
                conflicts[side + "_placeholder_identity"] = sorted(
                    {_canonical_json(team).decode() for team in teams})
        canonical_participants = {
            side: next((str(r["resolved_team"]["id"]) for r in participant_resolutions if r["side"] == side),
                       str(observations[0]["identity"][side + "_team_id"]))
            for side in ("away", "home")}
        if participant_resolutions and canonical_participants["away"] == canonical_participants["home"]:
            conflicts["participant_slots"] = [canonical_participants["away"]]
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
        for resolution in participant_resolutions:
            side = resolution["side"]
            canonical_identity[side + "_team_id"] = str(resolution["resolved_team"]["id"])
            canonical_identity[side + "_team_name"] = resolution["resolved_team"]["name"]
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
        if participant_resolutions:
            participant_resolutions.sort(key=lambda resolution: resolution["side"])
            resolved[game_id].update({
                "participant_resolution_policy": PARTICIPANT_RESOLUTION_POLICY,
                "participant_resolution_count": len(participant_resolutions),
                "participant_resolutions": participant_resolutions,
            })
            participant_resolution_count += len(participant_resolutions)
    return resolved, {
        "schedule_row_count": schedule_row_count,
        "unique_game_count": len(observations_by_game),
        "revision_count": revision_count,
        "reconciled_game_count": reconciled_game_count,
        "duplicate_observation_count": duplicate_observation_count,
        "identity_conflict_count": len(identity_conflicts),
        "identity_conflicts": identity_conflicts,
        # Keep ordinary/historical reconciliation hashes unchanged.
        **({"participant_resolution_count": participant_resolution_count}
           if participant_resolution_count else {}),
    }
