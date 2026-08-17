"""Offline materialization of a prospective acquisition into a source pack."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Final, Mapping, Sequence
from collections.abc import Callable

from courtvision.sports.mlb.data.context_source_pack import (
    LINEUP_OUTPUT_COLUMNS,
    PROBABLE_PITCHER_OUTPUT_COLUMNS,
    SourcePackResult,
    SourceSnapshotResult,
    _load_snapshot,
    assemble_context_source_pack,
    collect_candidate_snapshot,
    collect_identity_snapshot,
    collect_normalized_source_snapshot,
    collect_statcast_snapshot,
    validate_context_source_pack,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import normalize_player_name
from courtvision.sports.mlb.data.prospective_context_acquisition import (
    ProspectiveAcquisitionError,
    ScheduledEvent,
    parse_mlb_schedule,
    parse_utc,
    utc_text,
    validate_game_feed_identity,
)
from courtvision.sports.mlb.data.prospective_statcast_history import (
    LoadedHistoricalStatcast,
    load_historical_statcast_snapshot,
)
from courtvision.sports.mlb.training.hr_context_features import (
    ContextFeatureBuildResult,
    build_context_features,
)


MATERIALIZATION_SCHEMA_VERSION: Final = "mlb-hr-context-materialization-v6"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_MATERIALIZATION_ROOT: Final = (
    PROJECT_ROOT
    / "data"
    / "research"
    / "mlb_hr_prospective_context_acquisition"
    / "materializations"
)
DEFAULT_FORWARD_SOURCE_ROOT: Final = (
    PROJECT_ROOT / "data" / "research" / "mlb_hr_prospective_source_packs"
)

_CROSSWALK_COLUMNS: Final = (
    "game_date",
    "retrosheet_game_id",
    "mlbam_game_id",
    "game_number",
    "retrosheet_batter_id",
    "mlbam_batter_id",
    "batter_name",
    "retrosheet_home_team_id",
    "home_team",
    "retrosheet_away_team_id",
    "away_team",
    "retrosheet_batting_team_id",
    "batting_team",
    "retrosheet_fielding_team_id",
    "fielding_team",
    "player_mapping_source",
    "game_mapping_source",
    "team_mapping_source",
    "verified_at",
    "mlbam_pitcher_id",
    "pitcher_name",
    "pitcher_team",
    "identity_mapping_version",
)
_SCHEDULE_COLUMNS: Final = (
    "event_id",
    "operating_date",
    "commence_time_utc",
    "home_team",
    "away_team",
    "venue_id",
    "venue_name",
    "source_record_id",
    "schedule_snapshot_id",
    "schedule_snapshot_complete",
    "source_published_or_available_at_utc",
    "captured_at_utc",
)
_ROSTER_COLUMNS: Final = (
    "event_id",
    "team",
    "player_id",
    "player_name",
    "batter_hand",
    "role",
    "eligibility_status",
    "source_record_id",
    "roster_snapshot_id",
    "team_roster_complete",
    "source_published_or_available_at_utc",
    "captured_at_utc",
)
_WEATHER_COLUMNS: Final = (
    "event_id",
    "venue_id",
    "venue_name",
    "weather_type",
    "weather_evidence_class",
    "issued_at_utc",
    "valid_for_utc",
    "measured_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
    "temperature",
    "temperature_unit",
    "wind_speed",
    "wind_speed_unit",
    "wind_direction",
    "humidity",
    "roof_status",
    "precipitation",
    "source",
    "source_record_id",
    "source_version",
)
_MLB_TO_RETROSHEET: Final[Mapping[str, str]] = MappingProxyType(
    {
        "ARI": "ARI", "ATL": "ATL", "ATH": "ATH", "BAL": "BAL",
        "BOS": "BOS", "CHC": "CHN", "CIN": "CIN", "CLE": "CLE",
        "COL": "COL", "CWS": "CHA", "DET": "DET", "HOU": "HOU",
        "KC": "KCA", "LAA": "LAA", "LAD": "LAN", "MIA": "MIA",
        "MIL": "MIL", "MIN": "MIN", "NYM": "NYN", "NYY": "NYA",
        "OAK": "OAK", "PHI": "PHI", "PIT": "PIT", "SD": "SDN",
        "SEA": "SEA", "SF": "SFN", "STL": "SLN", "TB": "TBA",
        "TEX": "TEX", "TOR": "TOR", "WSH": "WAS",
    }
)


@dataclass(frozen=True, slots=True)
class ProspectiveMaterializationResult:
    materialization_id: str
    materialization_dir: Path
    source_pack: SourcePackResult
    feature_dry_run: ContextFeatureBuildResult
    candidate_count: int
    probable_pitcher_count: int
    lineup_slot_count: int


def _canonical_json(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=None if pretty else (",", ":"),
        indent=2 if pretty else None,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + (b"\n" if pretty else b"")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_manifest(path: str | Path) -> tuple[Path, dict[str, object]]:
    source = Path(path).expanduser().resolve()
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError(f"could not read acquisition manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProspectiveAcquisitionError("acquisition manifest must be an object")
    digest = payload.get("manifest_digest")
    without_digest = dict(payload)
    without_digest.pop("manifest_digest", None)
    if digest != _sha256(_canonical_json(without_digest)):
        raise ProspectiveAcquisitionError("acquisition manifest digest mismatch")
    for record in payload.get("sources") or []:
        if not isinstance(record, Mapping) or not record.get("body_path"):
            continue
        raw_path = (source.parent / str(record["body_path"])).resolve()
        if not raw_path.is_relative_to(source.parent) or not raw_path.is_file():
            raise ProspectiveAcquisitionError("acquisition raw body path is invalid")
        if _sha256(raw_path.read_bytes()) != record.get("sha256"):
            raise ProspectiveAcquisitionError("acquisition raw body digest mismatch")
    return source, payload


def _source_record(
    manifest_path: Path,
    manifest: Mapping[str, object],
    *,
    request_id: str,
) -> tuple[Mapping[str, object], Path]:
    matches = [
        item
        for item in manifest.get("sources") or []
        if isinstance(item, Mapping) and item.get("request_id") == request_id
    ]
    if len(matches) != 1:
        raise ProspectiveAcquisitionError(f"missing unique acquisition source: {request_id}")
    record = matches[0]
    if record.get("availability_status") != "completed":
        raise ProspectiveAcquisitionError(f"acquisition source is not usable: {request_id}")
    body_path = (manifest_path.parent / str(record.get("body_path") or "")).resolve()
    return record, body_path


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


def _statsapi_json(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProspectiveAcquisitionError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ProspectiveAcquisitionError(f"{label} must be an object")
    return payload


def _collect_or_reuse_snapshot(
    source_name: str,
    research_root: str | Path,
    collector: Callable[[], SourceSnapshotResult],
) -> SourceSnapshotResult:
    """Reuse only the exact immutable snapshot whose computed ID collided."""

    try:
        return collector()
    except FileExistsError as exc:
        prefix = "immutable source snapshot already exists: "
        message = str(exc)
        if not message.startswith(prefix):
            raise
        destination = Path(message.removeprefix(prefix)).resolve()
        root = Path(research_root).expanduser().resolve()
        if not destination.is_relative_to(root):
            raise ProspectiveAcquisitionError(
                "existing source snapshot escaped the research root"
            ) from exc
        manifest, data_path = _load_snapshot(destination)
        if manifest.get("source_name") != source_name:
            raise ProspectiveAcquisitionError(
                "existing source snapshot has conflicting source identity"
            ) from exc
        return SourceSnapshotResult(
            source_name=source_name,
            snapshot_id=str(manifest["snapshot_id"]),
            snapshot_dir=destination,
            data_path=data_path,
            manifest_path=destination / "source_snapshot_manifest_v1.json",
            sha256=str(manifest["sha256"]),
            row_count=int(manifest["row_count"]),
        )


def _team_context(feed: Mapping[str, object]) -> dict[str, dict[str, object]]:
    game_data = feed.get("gameData")
    if not isinstance(game_data, Mapping) or not isinstance(game_data.get("teams"), Mapping):
        raise ProspectiveAcquisitionError("game feed lacks team identity")
    result: dict[str, dict[str, object]] = {}
    for side in ("away", "home"):
        team = game_data["teams"].get(side)  # type: ignore[index]
        if not isinstance(team, Mapping):
            raise ProspectiveAcquisitionError("game feed lacks team identity")
        abbreviation = str(team.get("abbreviation") or "").strip().upper()
        if abbreviation not in _MLB_TO_RETROSHEET:
            raise ProspectiveAcquisitionError(f"unsupported team abbreviation: {abbreviation}")
        result[side] = {
            "id": str(team.get("id") or ""),
            "abbreviation": abbreviation,
        }
    return result


def _feed_player(feed: Mapping[str, object], player_id: str) -> Mapping[str, object]:
    game_data = feed.get("gameData")
    players = game_data.get("players") if isinstance(game_data, Mapping) else None
    if not isinstance(players, Mapping):
        raise ProspectiveAcquisitionError("game feed lacks player identities")
    player = players.get("ID" + player_id)
    if not isinstance(player, Mapping):
        player = next(
            (
                item
                for item in players.values()
                if isinstance(item, Mapping) and str(item.get("id")) == player_id
            ),
            None,
        )
    if not isinstance(player, Mapping):
        raise ProspectiveAcquisitionError(f"game feed lacks player {player_id}")
    return player


def materialize_prospective_source_pack(
    *,
    history_manifest_path: str | Path,
    volatile_manifest_path: str | Path,
    weather_manifest_path: str | Path | None = None,
    statcast_history_manifest_path: str | Path | None = None,
    git_commit: str,
    materialization_root: str | Path = DEFAULT_MATERIALIZATION_ROOT,
    source_research_root: str | Path = DEFAULT_FORWARD_SOURCE_ROOT,
) -> ProspectiveMaterializationResult:
    """Verify captured bytes, build source snapshots, validate, and dry-run v2."""

    history_path, history = _load_manifest(history_manifest_path)
    volatile_path, volatile = _load_manifest(volatile_manifest_path)
    if volatile.get("capture_state") != "completed":
        raise ProspectiveAcquisitionError("volatile capture is not complete")
    event_ids = [str(item.get("event_id")) for item in volatile.get("events") or [] if isinstance(item, Mapping)]
    if not event_ids:
        raise ProspectiveAcquisitionError("volatile capture has no event identities")
    weather_path: Path | None = None
    weather: dict[str, object] | None = None
    if weather_manifest_path is not None:
        weather_path, weather = _load_manifest(weather_manifest_path)
        if weather.get("capture_state") != "completed":
            raise ProspectiveAcquisitionError("weather capture is not complete")
    operating_date = str(volatile.get("operating_date") or "")
    schedule_record, schedule_raw = _source_record(
        history_path,
        history,
        request_id=f"statsapi-schedule-{operating_date}",
    )
    scheduled_events = {
        item.event_id: item
        for item in parse_mlb_schedule(
            schedule_raw.read_bytes(), operating_date=datetime.fromisoformat(operating_date).date()
        )
        if item.event_id in set(event_ids)
    }
    if set(scheduled_events) != set(event_ids):
        raise ProspectiveAcquisitionError("selected events conflict with daily schedule")

    feed_records: dict[str, Mapping[str, object]] = {}
    feed_paths: dict[str, Path] = {}
    feeds: dict[str, Mapping[str, object]] = {}
    team_abbreviations: dict[str, str] = {}
    for event_id in event_ids:
        record, path = _source_record(
            volatile_path,
            volatile,
            request_id=f"statsapi-feed-{event_id}",
        )
        event = scheduled_events[event_id]
        feed = validate_game_feed_identity(path.read_bytes(), event)
        teams = _team_context(feed)
        team_abbreviations[event.away_team_id] = str(teams["away"]["abbreviation"])
        team_abbreviations[event.home_team_id] = str(teams["home"]["abbreviation"])
        feed_records[event_id] = record
        feed_paths[event_id] = path
        feeds[event_id] = feed

    cutoff = max(
        parse_utc(record.get("captured_at_utc", ""), "feed.captured_at_utc")
        for record in feed_records.values()
    )
    if weather is not None:
        weather_captures = [
            parse_utc(item.get("captured_at_utc", ""), "weather.captured_at_utc")
            for item in weather.get("sources") or []
            if isinstance(item, Mapping)
            and item.get("source_name") == "nws_hourly_forecast"
            and item.get("availability_status") == "completed"
        ]
        if len(weather_captures) != len(event_ids):
            raise ProspectiveAcquisitionError("weather capture lacks complete event coverage")
        cutoff = max(cutoff, *weather_captures)
    statcast_history: LoadedHistoricalStatcast | None = None
    if statcast_history_manifest_path is not None:
        preview_path = Path(statcast_history_manifest_path).expanduser().resolve()
        try:
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProspectiveAcquisitionError(
                "could not read historical Statcast manifest"
            ) from exc
        if not isinstance(preview, Mapping):
            raise ProspectiveAcquisitionError(
                "historical Statcast manifest must be an object"
            )
        statcast_captured = parse_utc(
            preview.get("captured_at_utc", ""), "historical Statcast captured_at_utc"
        )
        cutoff = max(cutoff, statcast_captured)
        statcast_history = load_historical_statcast_snapshot(
            preview_path,
            cutoff_utc=cutoff,
            target_game_ids=event_ids,
            eligible_hitter_ids=tuple(
                str(value) for value in preview.get("eligible_hitter_ids_requested") or []
            ),
            probable_pitcher_ids=tuple(
                str(value) for value in preview.get("probable_pitcher_ids_requested") or []
            ),
        )
    if any(cutoff >= event.scheduled_start_utc for event in scheduled_events.values()):
        raise ProspectiveAcquisitionError("materialization cutoff is not pregame")
    identity = {
        "schema_version": MATERIALIZATION_SCHEMA_VERSION,
        "history_snapshot_id": history.get("history_snapshot_id"),
        "volatile_capture_id": volatile.get("capture_id"),
        "weather_capture_id": weather.get("capture_id") if weather else None,
        "historical_statcast_snapshot_id": (
            statcast_history.manifest.get("snapshot_id") if statcast_history else None
        ),
        "historical_statcast_manifest_digest": (
            statcast_history.manifest.get("manifest_digest") if statcast_history else None
        ),
        "operating_date": operating_date,
        "cutoff_utc": utc_text(cutoff),
        "git_commit": git_commit,
    }
    materialization_id = "materialization-" + _sha256(_canonical_json(identity))
    root = Path(materialization_root).expanduser().resolve()
    destination = root / ("m-" + materialization_id.removeprefix("materialization-")[:20])
    if destination.exists():
        raise FileExistsError(f"immutable materialization already exists: {destination}")
    root.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".t-", dir=root))

    schedule_rows: list[dict[str, object]] = []
    roster_rows: list[dict[str, object]] = []
    crosswalk_rows: list[dict[str, object]] = []
    probable_rows: list[dict[str, object]] = []
    lineup_rows: list[dict[str, object]] = []
    weather_rows: list[dict[str, object]] = []
    raw_inputs: dict[str, Path] = {
        "daily_schedule": schedule_raw,
        "history_manifest": history_path,
        "volatile_manifest": volatile_path,
    }
    if weather_path is not None:
        raw_inputs["weather_manifest"] = weather_path
    history_id = str(history.get("history_snapshot_id"))
    schedule_available = schedule_record.get("provider_published_at_utc") or schedule_record.get(
        "captured_at_utc"
    )

    for event_id in event_ids:
        event = scheduled_events[event_id]
        feed = feeds[event_id]
        feed_record = feed_records[event_id]
        feed_path = feed_paths[event_id]
        raw_inputs[f"game_feed_{event_id}"] = feed_path
        teams = _team_context(feed)
        away_abbr = str(teams["away"]["abbreviation"])
        home_abbr = str(teams["home"]["abbreviation"])
        schedule_rows.append(
            {
                "event_id": event_id,
                "operating_date": operating_date,
                "commence_time_utc": utc_text(event.scheduled_start_utc),
                "home_team": home_abbr,
                "away_team": away_abbr,
                "venue_id": event.venue_id,
                "venue_name": event.venue_name,
                "source_record_id": f"statsapi-schedule-game-{event_id}",
                "schedule_snapshot_id": history_id,
                "schedule_snapshot_complete": "true",
                "source_published_or_available_at_utc": schedule_available,
                "captured_at_utc": schedule_record.get("captured_at_utc"),
            }
        )
        game_data = feed.get("gameData")
        if not isinstance(game_data, Mapping):
            raise ProspectiveAcquisitionError("feed lacks gameData")
        probable = game_data.get("probablePitchers")
        if not isinstance(probable, Mapping):
            raise ProspectiveAcquisitionError("feed lacks probable pitchers")
        probable_by_team: dict[str, tuple[str, str, str]] = {}
        for side, team_abbr in (("away", away_abbr), ("home", home_abbr)):
            pitcher = probable.get(side)
            if not isinstance(pitcher, Mapping):
                raise ProspectiveAcquisitionError("feed lacks both probable pitchers")
            pitcher_id = str(pitcher.get("id") or "")
            pitcher_name = str(pitcher.get("fullName") or "").strip()
            player = _feed_player(feed, pitcher_id)
            hand_payload = player.get("pitchHand")
            if not isinstance(hand_payload, Mapping):
                raise ProspectiveAcquisitionError("probable pitcher hand is unavailable")
            hand = str(hand_payload.get("code") or "").upper()
            probable_by_team[team_abbr] = (pitcher_id, pitcher_name, hand)
            probable_rows.append(
                {
                    "event_id": event_id,
                    "team": team_abbr,
                    "pitcher_id": pitcher_id,
                    "pitcher_name": pitcher_name,
                    "normalized_pitcher_name": normalize_player_name(pitcher_name) or "",
                    "pitcher_hand": hand,
                    "probable_pitcher_status": "probable",
                    "identity_status": "verified_mlbam",
                    "identity_mapping_version": "mlb-statsapi-forward-v1",
                    "provider_published_at_utc": "",
                    "first_observed_at_utc": feed_record.get("captured_at_utc"),
                    "captured_at_utc": feed_record.get("captured_at_utc"),
                    "source": "MLB StatsAPI gameData.probablePitchers",
                    "source_record_id": f"statsapi-feed-{event_id}-{side}-probable",
                    "source_version": "api-v1.1-game-feed",
                }
            )

        live_data = feed.get("liveData")
        boxscore = live_data.get("boxscore") if isinstance(live_data, Mapping) else None
        boxscore_teams = boxscore.get("teams") if isinstance(boxscore, Mapping) else None
        if not isinstance(boxscore_teams, Mapping):
            raise ProspectiveAcquisitionError("feed lacks boxscore batting orders")
        for side, team_abbr in (("away", away_abbr), ("home", home_abbr)):
            team_box = boxscore_teams.get(side)
            order = team_box.get("battingOrder") if isinstance(team_box, Mapping) else None
            if not isinstance(order, list) or len(order) != 9:
                raise ProspectiveAcquisitionError("feed lacks a complete both-team lineup")
            for slot, player_id_value in enumerate(order, start=1):
                player_id = str(player_id_value)
                lineup_rows.append(
                    {
                        "event_id": event_id,
                        "team": team_abbr,
                        "player_id": player_id,
                        "lineup_status": "confirmed",
                        "batting_order_position": slot,
                        "provider_published_at_utc": "",
                        "first_observed_at_utc": feed_record.get("captured_at_utc"),
                        "captured_at_utc": feed_record.get("captured_at_utc"),
                        "source": "MLB StatsAPI liveData.boxscore battingOrder",
                        "source_record_id": f"statsapi-feed-{event_id}-{team_abbr}-{slot}",
                        "expected_pa": "",
                        "expected_pa_source": "",
                        "expected_pa_version": "",
                    }
                )

        for side, team_id, team_abbr, opponent_abbr in (
            ("away", event.away_team_id, away_abbr, home_abbr),
            ("home", event.home_team_id, home_abbr, away_abbr),
        ):
            roster_record, roster_path = _source_record(
                history_path,
                history,
                request_id=f"statsapi-active-roster-{team_id}",
            )
            raw_inputs[f"active_roster_{team_id}"] = roster_path
            roster_payload = _statsapi_json(roster_path, "active roster")
            roster = roster_payload.get("roster")
            if not isinstance(roster, list) or not roster:
                raise ProspectiveAcquisitionError("active roster is empty")
            available = roster_record.get("provider_published_at_utc") or roster_record.get(
                "captured_at_utc"
            )
            opponent_pitcher = probable_by_team[opponent_abbr]
            for entry in roster:
                if not isinstance(entry, Mapping) or not isinstance(entry.get("person"), Mapping):
                    raise ProspectiveAcquisitionError("active roster row is malformed")
                person = entry["person"]
                player_id = str(person.get("id") or "")
                player_name = str(person.get("fullName") or "").strip()
                position = entry.get("position")
                position_type = (
                    str(position.get("type") or "") if isinstance(position, Mapping) else ""
                )
                is_hitter = position_type.casefold() != "pitcher"
                bat_side = person.get("batSide")
                hand = (
                    str(bat_side.get("code") or "").upper()
                    if isinstance(bat_side, Mapping)
                    else ""
                )
                if is_hitter and hand not in {"L", "R", "S"}:
                    raise ProspectiveAcquisitionError(
                        f"eligible hitter {player_id} lacks provider batting hand"
                    )
                roster_rows.append(
                    {
                        "event_id": event_id,
                        "team": team_abbr,
                        "player_id": player_id,
                        "player_name": player_name,
                        "batter_hand": hand,
                        "role": "hitter" if is_hitter else "pitcher",
                        "eligibility_status": "active_roster",
                        "source_record_id": f"statsapi-roster-{event_id}-{team_id}-{player_id}",
                        "roster_snapshot_id": history_id,
                        "team_roster_complete": "true",
                        "source_published_or_available_at_utc": available,
                        "captured_at_utc": roster_record.get("captured_at_utc"),
                    }
                )
                if not is_hitter:
                    continue
                home_retrosheet = _MLB_TO_RETROSHEET[home_abbr]
                away_retrosheet = _MLB_TO_RETROSHEET[away_abbr]
                batting_retrosheet = _MLB_TO_RETROSHEET[team_abbr]
                fielding_retrosheet = _MLB_TO_RETROSHEET[opponent_abbr]
                crosswalk_rows.append(
                    {
                        "game_date": operating_date,
                        "retrosheet_game_id": "",
                        "mlbam_game_id": event_id,
                        "game_number": 1,
                        "retrosheet_batter_id": "",
                        "mlbam_batter_id": player_id,
                        "batter_name": player_name,
                        "retrosheet_home_team_id": home_retrosheet,
                        "home_team": home_abbr,
                        "retrosheet_away_team_id": away_retrosheet,
                        "away_team": away_abbr,
                        "retrosheet_batting_team_id": batting_retrosheet,
                        "batting_team": team_abbr,
                        "retrosheet_fielding_team_id": fielding_retrosheet,
                        "fielding_team": opponent_abbr,
                        "player_mapping_source": "MLB StatsAPI active roster person.id",
                        "game_mapping_source": "MLB StatsAPI schedule gamePk",
                        "team_mapping_source": "CourtVision explicit Retrosheet team map v2",
                        "verified_at": roster_record.get("captured_at_utc"),
                        "mlbam_pitcher_id": opponent_pitcher[0],
                        "pitcher_name": opponent_pitcher[1],
                        "pitcher_team": opponent_abbr,
                        "identity_mapping_version": "mlb-statsapi-forward-v1",
                    }
                )

    if weather is not None and weather_path is not None:
        for event_id in event_ids:
            matches = [
                item
                for item in weather.get("sources") or []
                if isinstance(item, Mapping)
                and item.get("request_id") == f"nws-hourly-{event_id}"
            ]
            if len(matches) != 1 or matches[0].get("availability_status") != "completed":
                raise ProspectiveAcquisitionError(
                    f"weather capture lacks usable hourly forecast for {event_id}"
                )
            record = matches[0]
            observation = record.get("weather_observation")
            if not isinstance(observation, Mapping):
                raise ProspectiveAcquisitionError("weather source lacks normalized observation")
            weather_rows.append(
                {
                    **dict(observation),
                    "measured_at_utc": "",
                    "roof_status": "",
                    "precipitation": "",
                    "source_record_id": (
                        f"nws-hourly-{event_id}-period-"
                        + str(observation.get("source_record_id") or "unknown")
                    ),
                    "source_version": "api.weather.gov-gridpoints-hourly",
                }
            )
            hourly_path = (
                weather_path.parent / str(record.get("body_path") or "")
            ).resolve()
            raw_inputs[f"nws_hourly_{event_id}"] = hourly_path
            _, points_path = _source_record(
                weather_path,
                weather,
                request_id=f"nws-points-{event_id}",
            )
            raw_inputs[f"nws_points_{event_id}"] = points_path

    if statcast_history is not None:
        statcast_history = load_historical_statcast_snapshot(
            statcast_history.manifest_path,
            cutoff_utc=cutoff,
            target_game_ids=event_ids,
            eligible_hitter_ids=tuple(
                str(row["mlbam_batter_id"]) for row in crosswalk_rows
            ),
            probable_pitcher_ids=tuple(
                str(row["pitcher_id"]) for row in probable_rows
            ),
        )

    try:
        _write_csv(temporary / "schedule.csv", _SCHEDULE_COLUMNS, schedule_rows)
        _write_csv(temporary / "roster.csv", _ROSTER_COLUMNS, roster_rows)
        _write_csv(temporary / "identity_crosswalk.csv", _CROSSWALK_COLUMNS, crosswalk_rows)
        _write_csv(
            temporary / "probable_pitchers.csv",
            PROBABLE_PITCHER_OUTPUT_COLUMNS,
            probable_rows,
        )
        _write_csv(temporary / "lineups.csv", LINEUP_OUTPUT_COLUMNS, lineup_rows)
        if weather_rows:
            _write_csv(temporary / "weather.csv", _WEATHER_COLUMNS, weather_rows)
        files = []
        for path in sorted(temporary.iterdir()):
            if path.is_file():
                payload = path.read_bytes()
                files.append(
                    {
                        "filename": path.name,
                        "sha256": _sha256(payload),
                        "byte_size": len(payload),
                        "row_count": max(0, payload.count(b"\n") - 1),
                    }
                )
        manifest: dict[str, object] = {
            **identity,
            "materialization_id": materialization_id,
            "files": files,
            "source_states": {
                "candidates": "completed",
                "identity_crosswalk": "completed",
                "statcast": "completed" if statcast_history else "unavailable",
                "probable_pitchers": "completed",
                "lineups": "completed",
                "weather": "completed" if weather_rows else "unavailable",
                "park_factors": "unavailable",
                "market": "unavailable",
            },
            "unavailable_reasons": {
                **(
                    {}
                    if statcast_history
                    else {
                        "statcast": "guarded live run did not download historical Statcast"
                    }
                ),
                **(
                    {}
                    if weather_rows
                    else {
                        "weather": "persisted feed supplies no trustworthy forecast issuance/measurement clock"
                    }
                ),
                "park_factors": "no approved versioned effective-dated park source configured",
                "market": "market evidence is outside this context acquisition scope",
            },
            "source_clock_correction": (
                "HTTP acquisition v1 recorded request start in first_observed_at_utc; "
                "materialization v4 conservatively uses exact response captured_at_utc "
                "when provider_published_at_utc is absent"
            ),
            "historical_statcast_reference": (
                {
                    "snapshot_id": statcast_history.manifest.get("snapshot_id"),
                    "manifest_digest": statcast_history.manifest.get("manifest_digest"),
                    "manifest_path": str(statcast_history.manifest_path),
                    "captured_at_utc": statcast_history.manifest.get("captured_at_utc"),
                    "cutoff_eligible_at_utc": utc_text(cutoff),
                }
                if statcast_history
                else None
            ),
            "research_only": True,
        }
        manifest["manifest_digest"] = _sha256(_canonical_json(manifest))
        (temporary / "materialization_manifest_v1.json").write_bytes(
            _canonical_json(manifest, pretty=True)
        )
        temporary.replace(destination)
    except Exception:
        if temporary.exists():
            import shutil

            shutil.rmtree(temporary)
        raise

    cutoff_text = utc_text(cutoff)
    snapshots = {}
    candidates = _collect_or_reuse_snapshot(
        "candidates",
        source_research_root,
        lambda: collect_candidate_snapshot(
            destination / "schedule.csv",
            destination / "roster.csv",
            destination / "identity_crosswalk.csv",
            operating_date=operating_date,
            cutoff_utc=cutoff_text,
            collected_at_utc=cutoff_text,
            git_commit=git_commit,
            research_root=source_research_root,
            additional_raw_inputs=raw_inputs,
        ),
    )
    snapshots["candidates"] = candidates.snapshot_dir
    identity_snapshot = _collect_or_reuse_snapshot(
        "identity_crosswalk",
        source_research_root,
        lambda: collect_identity_snapshot(
            destination / "identity_crosswalk.csv",
            operating_date=operating_date,
            cutoff_utc=cutoff_text,
            collected_at_utc=cutoff_text,
            mapping_source="MLB StatsAPI persisted schedule/active-roster identity",
            mapping_version="mlb-statsapi-forward-v1",
            git_commit=git_commit,
            research_root=source_research_root,
            additional_raw_inputs=raw_inputs,
        ),
    )
    snapshots["identity_crosswalk"] = identity_snapshot.snapshot_dir
    probable_snapshot = _collect_or_reuse_snapshot(
        "probable_pitchers",
        source_research_root,
        lambda: collect_normalized_source_snapshot(
            "probable_pitchers",
            destination / "probable_pitchers.csv",
            operating_date=operating_date,
            cutoff_utc=cutoff_text,
            collected_at_utc=cutoff_text,
            provider="MLB StatsAPI persisted game feeds",
            collector_configuration={"provider_version": "api-v1.1-game-feed"},
            git_commit=git_commit,
            research_root=source_research_root,
            additional_raw_inputs={
                f"game_feed_{key}": value for key, value in feed_paths.items()
            },
        ),
    )
    snapshots["probable_pitchers"] = probable_snapshot.snapshot_dir
    lineup_snapshot = _collect_or_reuse_snapshot(
        "lineups",
        source_research_root,
        lambda: collect_normalized_source_snapshot(
            "lineups",
            destination / "lineups.csv",
            operating_date=operating_date,
            cutoff_utc=cutoff_text,
            collected_at_utc=cutoff_text,
            provider="MLB StatsAPI persisted game feeds",
            collector_configuration={"provider_version": "api-v1.1-game-feed"},
            git_commit=git_commit,
            research_root=source_research_root,
            additional_raw_inputs={
                f"game_feed_{key}": value for key, value in feed_paths.items()
            },
        ),
    )
    snapshots["lineups"] = lineup_snapshot.snapshot_dir
    if statcast_history is not None:
        statcast_snapshot = _collect_or_reuse_snapshot(
            "statcast",
            source_research_root,
            lambda: collect_statcast_snapshot(
                statcast_history.statcast_csv_path,
                statcast_history.game_clock_csv_path,
                operating_date=operating_date,
                cutoff_utc=cutoff_text,
                captured_at_utc=str(statcast_history.manifest["captured_at_utc"]),
                git_commit=git_commit,
                research_root=source_research_root,
                additional_raw_inputs={
                    "historical_statcast_manifest": statcast_history.manifest_path
                },
            ),
        )
        snapshots["statcast"] = statcast_snapshot.snapshot_dir
    if weather_rows:
        weather_snapshot = _collect_or_reuse_snapshot(
            "weather",
            source_research_root,
            lambda: collect_normalized_source_snapshot(
                "weather",
                destination / "weather.csv",
                operating_date=operating_date,
                cutoff_utc=cutoff_text,
                collected_at_utc=cutoff_text,
                provider="US National Weather Service persisted hourly forecasts",
                collector_configuration={
                    "provider_version": "api.weather.gov-gridpoints-hourly"
                },
                git_commit=git_commit,
                research_root=source_research_root,
                additional_raw_inputs={
                    key: value
                    for key, value in raw_inputs.items()
                    if key.startswith("nws_") or key == "weather_manifest"
                },
            ),
        )
        snapshots["weather"] = weather_snapshot.snapshot_dir
    unavailable = {
        "park_factors": "no approved versioned effective-dated park source configured",
        "market": "market evidence is outside this context acquisition scope",
    }
    if statcast_history is None:
        unavailable["statcast"] = "guarded live run did not download historical Statcast"
    if not weather_rows:
        unavailable["weather"] = (
            "no trustworthy pregame weather issuance/measurement clock was captured"
        )
    source_pack = assemble_context_source_pack(
        operating_date=operating_date,
        cutoff_utc=cutoff_text,
        assembled_at_utc=datetime.now(timezone.utc),
        snapshot_dirs=snapshots,
        unavailable_sources=unavailable,
        git_commit=git_commit,
        research_root=source_research_root,
    )
    validation = validate_context_source_pack(source_pack.pack_dir)
    if not validation.is_valid:
        raise ProspectiveAcquisitionError(
            "source pack validation failed: " + "; ".join(validation.errors)
        )
    feature_dry_run = build_context_features(
        operating_date=operating_date,
        as_of_utc=cutoff_text,
        source_root=source_pack.pack_dir,
        git_commit=git_commit,
        dry_run=True,
    )
    return ProspectiveMaterializationResult(
        materialization_id=materialization_id,
        materialization_dir=destination,
        source_pack=source_pack,
        feature_dry_run=feature_dry_run,
        candidate_count=len(crosswalk_rows),
        probable_pitcher_count=len(probable_rows),
        lineup_slot_count=len(lineup_rows),
    )


__all__ = [
    "DEFAULT_FORWARD_SOURCE_ROOT",
    "DEFAULT_MATERIALIZATION_ROOT",
    "MATERIALIZATION_SCHEMA_VERSION",
    "ProspectiveMaterializationResult",
    "materialize_prospective_source_pack",
]
