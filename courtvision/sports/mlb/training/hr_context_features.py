"""Point-in-time, research-only MLB HR context feature materialization.

This module reads persisted CSV snapshots only.  It never calls a provider,
loads outcome labels for the target event, trains a model, or writes to the
live prospective-trial namespace.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from types import MappingProxyType
from typing import Final, Iterable, Mapping, Sequence

from courtvision.sports.mlb.data.ballpark_factors import normalize_venue_name
from courtvision.sports.mlb.data.crosswalk_validation import (
    MLB_TEAM_ABBREVIATIONS,
    REQUIRED_CROSSWALK_COLUMNS,
    validate_mlb_hr_crosswalk_csv,
)
from courtvision.sports.mlb.data.odds_snapshot_ingestion import (
    american_to_decimal,
    american_to_implied_probability,
)
from courtvision.sports.mlb.data_manifest import compute_file_sha256
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name
from courtvision.sports.mlb.research_context import (
    LINEUP_STATUSES,
    PROBABLE_PITCHER_STATUSES,
)
from courtvision.sports.mlb.research_safety import mlb_research_safety_fields


FEATURE_SCHEMA_VERSION: Final = "mlb-hr-context-feature-v2"
MANIFEST_SCHEMA_VERSION: Final = "mlb-hr-context-feature-manifest-v2"
BUILD_SUMMARY_SCHEMA_VERSION: Final = "mlb-hr-context-feature-build-summary-v2"
FEATURES_FILENAME: Final = "features.csv"
MANIFEST_FILENAME: Final = "feature_manifest_v2.json"
BUILD_SUMMARY_FILENAME: Final = "build_summary_v2.json"
CONTEXT_FEATURE_RESEARCH_ROOT: Path = (
    Path(__file__).resolve().parents[4]
    / "outputs"
    / "research"
    / "mlb_hr_challengers"
    / "context_features"
)

_MLBAM_ID: Final = re.compile(r"^[1-9]\d{5,9}$")
CANDIDATE_UNIVERSE_ORIGINS: Final = frozenset(
    {
        "neutral_market_independent",
        "market_covered_subset",
        "market_selected",
        "manual_or_unknown",
    }
)
NEUTRAL_COMPARISON_ORIGINS: Final = frozenset(
    {"neutral_market_independent", "market_covered_subset"}
)
MATCHUP_HORIZON_POLICY: Final = "current_season_completed_visible_through_as_of"
MARKET_STALENESS_POLICY: Final = (
    "latest_known_quote_per_book_as_of_cutoff_with_per_book_timestamps"
)
WEATHER_VALID_FOR_TOLERANCE: Final = timedelta(hours=1)

SOURCE_FILES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "candidates": "candidates.csv",
        "identity_crosswalk": "identity_crosswalk.csv",
        "statcast": "statcast_events.csv",
        "probable_pitchers": "probable_pitchers.csv",
        "lineups": "lineups.csv",
        "weather": "weather.csv",
        "park_factors": "park_factors.csv",
        "market": "market_snapshots.csv",
    }
)

REQUIRED_SOURCE_COLUMNS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "candidates": frozenset(
            {
                "event_id",
                "operating_date",
                "commence_time_utc",
                "home_team",
                "away_team",
                "venue_id",
                "venue_name",
                "team",
                "opponent",
                "player_id",
                "player_name",
                "normalized_player_name",
                "batter_hand",
                "identity_status",
                "identity_mapping_version",
                "candidate_published_or_available_at_utc",
                "candidate_captured_at_utc",
                "candidate_universe_id",
                "candidate_universe_version",
                "candidate_universe_generator",
                "candidate_universe_origin",
                "candidate_universe_policy",
                "candidate_universe_source_digest",
                "candidate_universe_configuration_digest",
                "candidate_universe_cutoff_utc",
            }
        ),
        "identity_crosswalk": frozenset(
            set(REQUIRED_CROSSWALK_COLUMNS)
            | {
                "mlbam_pitcher_id",
                "pitcher_name",
                "pitcher_team",
                "identity_mapping_version",
            }
        ),
        "statcast": frozenset(
            {
                "game_id",
                "game_date",
                "game_completed_at_utc",
                "source_published_or_available_at_utc",
                "collected_at_utc",
                "plate_appearance_id",
                "pitch_number",
                "batter_id",
                "pitcher_id",
                "batter_hand",
                "pitcher_hand",
                "home_team",
                "away_team",
                "batter_team",
                "pitcher_team",
                "event_type",
                "is_home_run",
            }
        ),
        "probable_pitchers": frozenset(
            {
                "event_id",
                "team",
                "pitcher_id",
                "pitcher_name",
                "normalized_pitcher_name",
                "pitcher_hand",
                "probable_pitcher_status",
                "identity_status",
                "identity_mapping_version",
                "announced_or_published_at_utc",
                "captured_at_utc",
            }
        ),
        "lineups": frozenset(
            {
                "event_id",
                "team",
                "player_id",
                "lineup_status",
                "batting_order_position",
                "announced_or_published_at_utc",
                "captured_at_utc",
            }
        ),
        "weather": frozenset(
            {
                "event_id",
                "venue_id",
                "venue_name",
                "weather_type",
                "weather_evidence_class",
                "issued_at_utc",
                "valid_for_utc",
                "measured_at_utc",
                "captured_at_utc",
                "temperature",
                "wind_speed",
                "wind_direction",
                "roof_status",
            }
        ),
        "park_factors": frozenset(
            {
                "venue_id",
                "venue_name",
                "park_hr_factor",
                "park_factor_source",
                "park_factor_version",
                "effective_from_date",
                "effective_to_date",
                "published_or_available_at_utc",
                "captured_at_utc",
            }
        ),
        "market": frozenset(
            {
                "event_id",
                "player_id",
                "sportsbook",
                "american_odds",
                "evidence_class",
                "market_configuration_id",
                "quote_at_utc",
                "captured_at_utc",
            }
        ),
    }
)

HITTER_METRICS: Final = (
    "pa",
    "hr",
    "hr_per_pa",
    "barrel_rate",
    "hard_hit_rate",
    "average_exit_velocity",
    "max_exit_velocity",
    "sweet_spot_rate",
    "xwoba",
    "xslg",
    "strikeout_rate",
    "walk_rate",
    "fly_ball_rate",
    "pull_rate",
)
PITCHER_METRICS: Final = (
    "batters_faced",
    "hr_allowed",
    "hr_per_batter_faced",
    "barrel_rate_allowed",
    "hard_hit_rate_allowed",
    "average_exit_velocity_allowed",
    "xwoba_allowed",
    "xslg_allowed",
    "strikeout_rate",
    "walk_rate",
    "ground_ball_rate",
    "fly_ball_rate",
)
ROLLING_WINDOWS_DAYS: Final = (7, 14, 30)


def _window_feature_names(prefix: str, metrics: Sequence[str]) -> tuple[str, ...]:
    names = [f"{prefix}_season_{metric}" for metric in metrics]
    for days in ROLLING_WINDOWS_DAYS:
        names.extend(f"{prefix}_{days}d_{metric}" for metric in metrics)
    return tuple(names)


IDENTITY_AND_PROVENANCE_COLUMNS: Final = (
    "feature_schema_version",
    "feature_row_id",
    "research_only",
    "operating_date",
    "as_of_utc",
    "event_id",
    "commence_time_utc",
    "home_team",
    "away_team",
    "venue_id",
    "venue_name",
    "team",
    "opponent",
    "is_home",
    "player_id",
    "player_name",
    "normalized_player_name",
    "batter_hand",
    "identity_status",
    "identity_mapping_version",
    "identity_crosswalk_digest",
    "candidate_universe_id",
    "candidate_universe_version",
    "candidate_universe_generator",
    "candidate_universe_origin",
    "candidate_universe_policy",
    "candidate_universe_source_digest",
    "candidate_universe_configuration_digest",
    "candidate_universe_cutoff_utc",
    "candidate_published_or_available_at_utc",
    "candidate_captured_at_utc",
    "probable_pitcher_id",
    "probable_pitcher_name",
    "normalized_probable_pitcher_name",
    "pitcher_hand",
    "probable_pitcher_status",
    "probable_pitcher_identity_status",
    "probable_pitcher_identity_mapping_version",
    "probable_pitcher_announced_or_published_at_utc",
    "probable_pitcher_captured_at_utc",
    "lineup_status",
    "lineup_announced_or_published_at_utc",
    "lineup_captured_at_utc",
    "batting_order_position",
    "expected_pa",
    "hitter_stats_available",
    "pitcher_stats_available",
    "probable_pitcher_available",
    "lineup_available",
    "expected_pa_available",
    "park_factor_available",
    "weather_available",
    "market_available",
)

MATCHUP_COLUMNS: Final = (
    "platoon_matchup_category",
    "hitter_vs_pitcher_hand_pa",
    "hitter_vs_pitcher_hand_hr_per_pa",
    "pitcher_vs_batter_hand_batters_faced",
    "pitcher_vs_batter_hand_hr_per_batter_faced",
    "hitter_pitch_type_xwoba_json",
    "pitcher_pitch_mix_json",
    "pitcher_average_velocity_json",
    "bvp_pa_descriptive",
    "bvp_hr_descriptive",
)

CONTEXT_AND_MARKET_COLUMNS: Final = (
    "park_hr_factor",
    "park_factor_source",
    "park_factor_version",
    "park_factor_published_or_available_at_utc",
    "park_factor_captured_at_utc",
    "temperature",
    "wind_speed",
    "wind_direction",
    "roof_status",
    "weather_type",
    "weather_evidence_class",
    "weather_evidence_at_utc",
    "weather_valid_for_utc",
    "weather_captured_at_utc",
    "market_best_sportsbook",
    "market_best_american_odds",
    "market_best_decimal_odds",
    "market_best_implied_probability",
    "market_bookmaker_count",
    "market_implied_probability_dispersion",
    "market_hours_before_game",
    "market_best_observed_at_utc",
    "market_observed_at_utc",
    "market_configuration_ids_json",
    "market_quote_timestamps_json",
)

SOURCE_COLUMNS: Final = (
    "source_snapshot_ids_json",
    "source_digests_json",
    "source_max_captured_at_utc_json",
    "statcast_metric_availability_json",
    "source_identity_digest",
    "configuration_digest",
    "git_commit",
    "feature_manifest_reference",
)

FEATURE_COLUMNS: Final = (
    *IDENTITY_AND_PROVENANCE_COLUMNS,
    *_window_feature_names("hitter", HITTER_METRICS),
    *_window_feature_names("pitcher", PITCHER_METRICS),
    *MATCHUP_COLUMNS,
    *CONTEXT_AND_MARKET_COLUMNS,
    *SOURCE_COLUMNS,
)

_TERMINAL_STRIKEOUTS: Final = frozenset(
    {"strikeout", "strikeout_double_play"}
)
_TERMINAL_WALKS: Final = frozenset({"walk", "intent_walk", "intentional_walk"})
class ContextFeatureError(ValueError):
    """Raised when source evidence cannot support a safe point-in-time build."""


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    name: str
    path: Path
    sha256: str
    byte_size: int
    headers: tuple[str, ...]
    rows: tuple[Mapping[str, str], ...]

    @property
    def snapshot_id(self) -> str:
        return f"{self.path.name}:sha256:{self.sha256}"


@dataclass(frozen=True, slots=True)
class _Candidate:
    event_id: str
    operating_date: date
    commence_time: datetime
    home_team: str
    away_team: str
    venue_id: str | None
    venue_name: str
    team: str
    opponent: str
    player_id: str
    player_name: str
    normalized_player_name: str
    batter_hand: str
    identity_status: str
    identity_mapping_version: str
    identity_crosswalk_digest: str
    published_or_available_at: datetime
    captured_at: datetime
    candidate_universe_id: str
    candidate_universe_version: str
    candidate_universe_generator: str
    candidate_universe_origin: str
    candidate_universe_policy: str
    candidate_universe_source_digest: str
    candidate_universe_configuration_digest: str
    candidate_universe_cutoff: datetime
    crosswalk_pitcher_id: str
    normalized_crosswalk_pitcher_name: str


@dataclass(frozen=True, slots=True)
class _StatcastRow:
    game_id: str
    game_date: date
    completed_at: datetime
    published_or_available_at: datetime
    collected_at: datetime
    plate_appearance_id: str
    pitch_number: int
    batter_id: str
    pitcher_id: str
    batter_hand: str | None
    pitcher_hand: str | None
    event_type: str | None
    is_home_run: bool
    pitch_type: str | None
    release_speed: float | None
    launch_speed: float | None
    launch_angle: float | None
    is_barrel: bool | None
    estimated_woba: float | None
    estimated_slg: float | None
    batted_ball_type: str | None
    is_pull: bool | None
    home_team: str
    away_team: str
    batter_team: str
    pitcher_team: str

    @property
    def is_terminal(self) -> bool:
        return self.event_type is not None


@dataclass(frozen=True, slots=True)
class ContextFeatureBuildResult:
    rows: tuple[Mapping[str, object], ...]
    manifest: Mapping[str, object]
    summary: Mapping[str, object]
    output_root: Path | None
    features_path: Path | None
    manifest_path: Path | None
    summary_path: Path | None
    dry_run: bool


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_value(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_aware_datetime(value: object, field_name: str) -> datetime:
    text = "" if value is None else str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContextFeatureError(
            f"{field_name} must be an ISO-8601 timezone-aware timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextFeatureError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _parse_date(value: object, field_name: str) -> date:
    text = "" if value is None else str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ContextFeatureError(f"{field_name} must be an ISO-8601 date") from exc


def _required_text(row: Mapping[str, object], field_name: str, label: str) -> str:
    value = row.get(field_name)
    text = "" if value is None else str(value).strip()
    if not text:
        raise ContextFeatureError(f"{label}.{field_name} is required")
    return text


def _optional_text(row: Mapping[str, object], field_name: str) -> str | None:
    value = row.get(field_name)
    text = "" if value is None else str(value).strip()
    return text or None


def _optional_float(row: Mapping[str, object], field_name: str, label: str) -> float | None:
    text = _optional_text(row, field_name)
    if text is None:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise ContextFeatureError(f"{label}.{field_name} must be numeric") from exc
    if not math.isfinite(value):
        raise ContextFeatureError(f"{label}.{field_name} must be finite")
    return value


def _optional_int(row: Mapping[str, object], field_name: str, label: str) -> int | None:
    text = _optional_text(row, field_name)
    if text is None:
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise ContextFeatureError(f"{label}.{field_name} must be an integer") from exc
    return value


def _optional_bool(row: Mapping[str, object], field_name: str, label: str) -> bool | None:
    text = _optional_text(row, field_name)
    if text is None:
        return None
    normalized = text.casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ContextFeatureError(f"{label}.{field_name} must be boolean when present")


def _normalized_token(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (value or "").casefold()).strip("_")


def _canonical_mlbam_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not _MLBAM_ID.fullmatch(text):
        raise ContextFeatureError(
            f"{field_name} must be a positive 6-10 digit canonical MLBAM id"
        )
    return text


def _canonical_team(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip().upper()
    if text not in MLB_TEAM_ABBREVIATIONS:
        raise ContextFeatureError(f"{field_name} must be a canonical MLB team")
    return text


def _sha256_text(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ContextFeatureError(f"{field_name} must be a 64-character SHA-256")
    return text


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 10) if denominator else None


def _mean(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return round(sum(present) / len(present), 10) if present else None


def _read_snapshot(
    source_root: Path, source_name: str, *, required: bool
) -> SourceSnapshot | None:
    path = source_root / SOURCE_FILES[source_name]
    if not path.exists():
        if required:
            raise ContextFeatureError(f"required source snapshot does not exist: {path}")
        return None
    if not path.is_file():
        raise ContextFeatureError(f"source snapshot is not a file: {path}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        headers = tuple(reader.fieldnames or ())
        rows = tuple(dict(row) for row in reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContextFeatureError(f"could not read {source_name} snapshot: {exc}") from exc
    if not headers:
        raise ContextFeatureError(f"{source_name} snapshot has no CSV header")
    if len(headers) != len(set(headers)):
        raise ContextFeatureError(f"{source_name} snapshot has duplicate CSV headers")
    missing = sorted(REQUIRED_SOURCE_COLUMNS[source_name] - set(headers))
    if missing:
        raise ContextFeatureError(
            f"{source_name} snapshot is missing required columns: {', '.join(missing)}"
        )
    if any(None in row for row in rows):
        raise ContextFeatureError(f"{source_name} snapshot contains extra CSV values")
    return SourceSnapshot(
        name=source_name,
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        byte_size=len(raw),
        headers=headers,
        rows=rows,
    )


def _load_snapshots(source_root: str | Path) -> dict[str, SourceSnapshot | None]:
    root = Path(source_root).expanduser().resolve()
    if not root.is_dir():
        raise ContextFeatureError(f"source root is not a directory: {root}")
    snapshots = {
        name: _read_snapshot(
            root, name, required=name in {"candidates", "identity_crosswalk"}
        )
        for name in SOURCE_FILES
    }
    candidates = snapshots["candidates"]
    if candidates is None or not candidates.rows:
        raise ContextFeatureError("candidates snapshot must contain at least one row")
    crosswalk = snapshots["identity_crosswalk"]
    if crosswalk is None or not crosswalk.rows:
        raise ContextFeatureError("identity_crosswalk snapshot must contain at least one row")
    return snapshots


def _validated_crosswalk_rows(
    snapshot: SourceSnapshot,
) -> Mapping[tuple[str, str], Mapping[str, str]]:
    validation = validate_mlb_hr_crosswalk_csv(snapshot.path)
    if not validation.is_valid:
        raise ContextFeatureError(
            "identity crosswalk validation failed: " + "; ".join(validation.errors)
        )
    indexed: dict[tuple[str, str], Mapping[str, str]] = {}
    for index, row in enumerate(snapshot.rows, start=2):
        label = f"identity_crosswalk row {index}"
        game_id = _canonical_mlbam_id(row.get("mlbam_game_id"), f"{label}.mlbam_game_id")
        batter_id = _canonical_mlbam_id(
            row.get("mlbam_batter_id"), f"{label}.mlbam_batter_id"
        )
        pitcher_id = _canonical_mlbam_id(
            row.get("mlbam_pitcher_id"), f"{label}.mlbam_pitcher_id"
        )
        pitcher_name = _required_text(row, "pitcher_name", label)
        if normalize_mlb_player_name(pitcher_name) == "":
            raise ContextFeatureError(f"{label}.pitcher_name cannot normalize to empty")
        pitcher_team = _canonical_team(row.get("pitcher_team"), f"{label}.pitcher_team")
        fielding_team = _canonical_team(
            row.get("fielding_team"), f"{label}.fielding_team"
        )
        if pitcher_team != fielding_team:
            raise ContextFeatureError(f"{label} pitcher role/team does not match fielding team")
        _required_text(row, "identity_mapping_version", label)
        key = (game_id, batter_id)
        if key in indexed:
            raise ContextFeatureError(f"duplicate identity crosswalk key: {key}")
        indexed[key] = row
        if pitcher_id == batter_id:
            raise ContextFeatureError(
                f"{label} batter/opposing-pitcher role collision is impossible"
            )
    return MappingProxyType(indexed)


def _parse_candidates(
    snapshot: SourceSnapshot, crosswalk_snapshot: SourceSnapshot
) -> tuple[_Candidate, ...]:
    crosswalk = _validated_crosswalk_rows(crosswalk_snapshot)
    candidates: list[_Candidate] = []
    seen: set[tuple[str, str]] = set()
    universe_contracts: set[str] = set()
    for index, row in enumerate(snapshot.rows, start=2):
        label = f"candidates row {index}"
        event_id = _canonical_mlbam_id(row.get("event_id"), f"{label}.event_id")
        player_id = _canonical_mlbam_id(row.get("player_id"), f"{label}.player_id")
        key = (event_id, player_id)
        if key in seen:
            raise ContextFeatureError(
                f"duplicate candidate event/player identity: {event_id}/{player_id}"
            )
        seen.add(key)
        home_team = _canonical_team(row.get("home_team"), f"{label}.home_team")
        away_team = _canonical_team(row.get("away_team"), f"{label}.away_team")
        team = _canonical_team(row.get("team"), f"{label}.team")
        opponent = _canonical_team(row.get("opponent"), f"{label}.opponent")
        if home_team == away_team:
            raise ContextFeatureError(f"{label} home_team and away_team must differ")
        if {team, opponent} != {home_team, away_team} or team == opponent:
            raise ContextFeatureError(
                f"{label} team/opponent do not match the event home/away identity"
            )
        player_name = _required_text(row, "player_name", label)
        supplied_normalized = _required_text(row, "normalized_player_name", label)
        canonical_normalized = normalize_mlb_player_name(player_name)
        if supplied_normalized != canonical_normalized:
            raise ContextFeatureError(
                f"{label} normalized_player_name does not match player_name"
            )
        identity_status = _required_text(row, "identity_status", label).casefold()
        if identity_status != "verified_mlbam":
            raise ContextFeatureError(
                f"{label} identity_status must be verified_mlbam; got {identity_status!r}"
            )
        batter_hand = _required_text(row, "batter_hand", label).upper()
        if batter_hand not in {"L", "R", "S"}:
            raise ContextFeatureError(f"{label}.batter_hand must be L, R, or S")
        operating_date = _parse_date(row.get("operating_date"), f"{label}.operating_date")
        mapping_version = _required_text(row, "identity_mapping_version", label)
        crosswalk_row = crosswalk.get(key)
        if crosswalk_row is None:
            raise ContextFeatureError(f"{label} has no verified crosswalk mapping")
        if _parse_date(
            crosswalk_row.get("game_date"), "identity_crosswalk.game_date"
        ) != operating_date:
            raise ContextFeatureError(f"{label} crosswalk game_date mismatch")
        for field_name, expected in (
            ("home_team", home_team),
            ("away_team", away_team),
            ("batting_team", team),
            ("fielding_team", opponent),
        ):
            if _required_text(crosswalk_row, field_name, "identity_crosswalk") != expected:
                raise ContextFeatureError(f"{label} crosswalk {field_name} mismatch")
        if normalize_mlb_player_name(
            _required_text(crosswalk_row, "batter_name", "identity_crosswalk")
        ) != canonical_normalized:
            raise ContextFeatureError(f"{label} crosswalk batter name mismatch")
        if _required_text(
            crosswalk_row, "identity_mapping_version", "identity_crosswalk"
        ) != mapping_version:
            raise ContextFeatureError(f"{label} crosswalk mapping version mismatch")

        published = _parse_aware_datetime(
            row.get("candidate_published_or_available_at_utc"),
            f"{label}.candidate_published_or_available_at_utc",
        )
        captured = _parse_aware_datetime(
            row.get("candidate_captured_at_utc"), f"{label}.candidate_captured_at_utc"
        )
        cutoff = _parse_aware_datetime(
            row.get("candidate_universe_cutoff_utc"),
            f"{label}.candidate_universe_cutoff_utc",
        )
        verified_at = _parse_aware_datetime(
            crosswalk_row.get("verified_at"), "identity_crosswalk.verified_at"
        )
        if not (published <= captured <= cutoff):
            raise ContextFeatureError(
                f"{label} candidate clocks must satisfy available <= captured <= cutoff"
            )
        if verified_at > cutoff:
            raise ContextFeatureError(f"{label} crosswalk was verified after universe cutoff")
        origin = _required_text(row, "candidate_universe_origin", label).casefold()
        if origin not in CANDIDATE_UNIVERSE_ORIGINS:
            raise ContextFeatureError(f"{label} has unsupported candidate universe origin")
        if origin not in NEUTRAL_COMPARISON_ORIGINS:
            raise ContextFeatureError(
                f"{label} candidate universe origin is not allowed for neutral comparison: {origin}"
            )
        universe_payload = {
            "candidate_universe_id": _required_text(row, "candidate_universe_id", label),
            "candidate_universe_version": _required_text(
                row, "candidate_universe_version", label
            ),
            "candidate_universe_generator": _required_text(
                row, "candidate_universe_generator", label
            ),
            "candidate_universe_origin": origin,
            "candidate_universe_policy": _required_text(
                row, "candidate_universe_policy", label
            ),
            "candidate_universe_source_digest": _sha256_text(
                row.get("candidate_universe_source_digest"),
                f"{label}.candidate_universe_source_digest",
            ),
            "candidate_universe_configuration_digest": _sha256_text(
                row.get("candidate_universe_configuration_digest"),
                f"{label}.candidate_universe_configuration_digest",
            ),
            "candidate_universe_cutoff_utc": _utc_text(cutoff),
        }
        universe_contracts.add(_sha256_value(universe_payload))
        candidates.append(
            _Candidate(
                event_id=event_id,
                operating_date=operating_date,
                commence_time=_parse_aware_datetime(
                    row.get("commence_time_utc"), f"{label}.commence_time_utc"
                ),
                home_team=home_team,
                away_team=away_team,
                venue_id=_optional_text(row, "venue_id"),
                venue_name=_required_text(row, "venue_name", label),
                team=team,
                opponent=opponent,
                player_id=player_id,
                player_name=player_name,
                normalized_player_name=canonical_normalized,
                batter_hand=batter_hand,
                identity_status=identity_status,
                identity_mapping_version=mapping_version,
                identity_crosswalk_digest=crosswalk_snapshot.sha256,
                published_or_available_at=published,
                captured_at=captured,
                candidate_universe_id=str(universe_payload["candidate_universe_id"]),
                candidate_universe_version=str(
                    universe_payload["candidate_universe_version"]
                ),
                candidate_universe_generator=str(
                    universe_payload["candidate_universe_generator"]
                ),
                candidate_universe_origin=origin,
                candidate_universe_policy=str(universe_payload["candidate_universe_policy"]),
                candidate_universe_source_digest=str(
                    universe_payload["candidate_universe_source_digest"]
                ),
                candidate_universe_configuration_digest=str(
                    universe_payload["candidate_universe_configuration_digest"]
                ),
                candidate_universe_cutoff=cutoff,
                crosswalk_pitcher_id=_canonical_mlbam_id(
                    crosswalk_row.get("mlbam_pitcher_id"),
                    "identity_crosswalk.mlbam_pitcher_id",
                ),
                normalized_crosswalk_pitcher_name=normalize_mlb_player_name(
                    _required_text(crosswalk_row, "pitcher_name", "identity_crosswalk")
                ),
            )
        )
    if len(universe_contracts) != 1:
        raise ContextFeatureError("all candidates must share one candidate-universe contract")
    return tuple(
        sorted(candidates, key=lambda item: (item.commence_time, item.event_id, item.player_id))
    )


def _parse_statcast(snapshot: SourceSnapshot | None) -> tuple[_StatcastRow, ...]:
    if snapshot is None:
        return ()
    events: list[_StatcastRow] = []
    pitch_keys: set[tuple[str, str, int]] = set()
    pa_signatures: dict[tuple[str, str], tuple[object, ...]] = {}
    for index, row in enumerate(snapshot.rows, start=2):
        label = f"statcast row {index}"
        completed_at = _parse_aware_datetime(
            row.get("game_completed_at_utc"), f"{label}.game_completed_at_utc"
        )
        published_at = _parse_aware_datetime(
            row.get("source_published_or_available_at_utc"),
            f"{label}.source_published_or_available_at_utc",
        )
        collected_at = _parse_aware_datetime(
            row.get("collected_at_utc"), f"{label}.collected_at_utc"
        )
        if not (completed_at <= published_at <= collected_at):
            raise ContextFeatureError(
                f"{label} Statcast clocks must satisfy completed <= available <= collected"
            )
        event_type_text = _optional_text(row, "event_type")
        event_type = _normalized_token(event_type_text) or None
        supplied_home_run = _optional_bool(row, "is_home_run", label)
        derived_home_run = event_type == "home_run"
        if supplied_home_run is not None and supplied_home_run != derived_home_run:
            raise ContextFeatureError(f"{label} has conflicting home-run evidence")
        game_id = _canonical_mlbam_id(row.get("game_id"), f"{label}.game_id")
        pa_id = _required_text(row, "plate_appearance_id", label)
        pitch_number = _optional_int(row, "pitch_number", label)
        if pitch_number is None or pitch_number < 1:
            raise ContextFeatureError(f"{label}.pitch_number must be a positive integer")
        pitch_key = (game_id, pa_id, pitch_number)
        if pitch_key in pitch_keys:
            raise ContextFeatureError(f"duplicate Statcast pitch identity: {pitch_key}")
        pitch_keys.add(pitch_key)
        batter_id = _canonical_mlbam_id(row.get("batter_id"), f"{label}.batter_id")
        pitcher_id = _canonical_mlbam_id(row.get("pitcher_id"), f"{label}.pitcher_id")
        batter_hand = (_optional_text(row, "batter_hand") or "").upper() or None
        pitcher_hand = (_optional_text(row, "pitcher_hand") or "").upper() or None
        if batter_hand is not None and batter_hand not in {"L", "R", "S"}:
            raise ContextFeatureError(f"{label}.batter_hand must be L, R, S, or empty")
        if pitcher_hand is not None and pitcher_hand not in {"L", "R"}:
            raise ContextFeatureError(f"{label}.pitcher_hand must be L, R, or empty")
        home_team = _canonical_team(row.get("home_team"), f"{label}.home_team")
        away_team = _canonical_team(row.get("away_team"), f"{label}.away_team")
        batter_team = _canonical_team(row.get("batter_team"), f"{label}.batter_team")
        pitcher_team = _canonical_team(row.get("pitcher_team"), f"{label}.pitcher_team")
        if home_team == away_team or batter_team == pitcher_team:
            raise ContextFeatureError(f"{label} has impossible team roles")
        if {batter_team, pitcher_team} != {home_team, away_team}:
            raise ContextFeatureError(f"{label} batter/pitcher teams do not match game")
        game_date = _parse_date(row.get("game_date"), f"{label}.game_date")
        signature = (
            game_date,
            completed_at,
            published_at,
            collected_at,
            batter_id,
            pitcher_id,
            batter_hand,
            pitcher_hand,
            home_team,
            away_team,
            batter_team,
            pitcher_team,
        )
        pa_key = (game_id, pa_id)
        previous = pa_signatures.setdefault(pa_key, signature)
        if previous != signature:
            raise ContextFeatureError(f"inconsistent Statcast PA identity: {pa_key}")
        events.append(
            _StatcastRow(
                game_id=game_id,
                game_date=game_date,
                completed_at=completed_at,
                published_or_available_at=published_at,
                collected_at=collected_at,
                plate_appearance_id=pa_id,
                pitch_number=pitch_number,
                batter_id=batter_id,
                pitcher_id=pitcher_id,
                batter_hand=batter_hand,
                pitcher_hand=pitcher_hand,
                event_type=event_type,
                is_home_run=derived_home_run,
                pitch_type=_optional_text(row, "pitch_type"),
                release_speed=_optional_float(row, "release_speed", label),
                launch_speed=_optional_float(row, "launch_speed", label),
                launch_angle=_optional_float(row, "launch_angle", label),
                is_barrel=_optional_bool(row, "is_barrel", label),
                estimated_woba=_optional_float(row, "estimated_woba", label),
                estimated_slg=_optional_float(row, "estimated_slg", label),
                batted_ball_type=(
                    _normalized_token(_optional_text(row, "batted_ball_type")) or None
                ),
                is_pull=_optional_bool(row, "is_pull", label),
                home_team=home_team,
                away_team=away_team,
                batter_team=batter_team,
                pitcher_team=pitcher_team,
            )
        )
    by_pa: dict[tuple[str, str], list[_StatcastRow]] = {}
    for event in events:
        by_pa.setdefault((event.game_id, event.plate_appearance_id), []).append(event)
    for pa_key, pitches in by_pa.items():
        terminal = [pitch for pitch in pitches if pitch.is_terminal]
        if len(terminal) != 1:
            raise ContextFeatureError(
                f"Statcast PA must have exactly one terminal row: {pa_key}"
            )
        if terminal[0].pitch_number != max(pitch.pitch_number for pitch in pitches):
            raise ContextFeatureError(f"terminal Statcast row is not the final pitch: {pa_key}")
    return tuple(
        sorted(
            events,
            key=lambda item: (
                item.completed_at,
                item.game_id,
                item.plate_appearance_id,
                item.pitch_number,
            ),
        )
    )


def _eligible_history(
    events: Sequence[_StatcastRow], candidate: _Candidate, as_of: datetime
) -> tuple[_StatcastRow, ...]:
    return tuple(
        event
        for event in events
        if event.game_id != candidate.event_id
        and event.completed_at <= as_of
        and event.published_or_available_at <= as_of
        and event.collected_at <= as_of
        and event.completed_at < candidate.commence_time
    )


def _batted_rows(rows: Iterable[_StatcastRow]) -> tuple[_StatcastRow, ...]:
    return tuple(
        row
        for row in rows
        if row.launch_speed is not None
        or row.launch_angle is not None
        or row.batted_ball_type is not None
        or row.is_barrel is not None
    )


def _hitter_metrics(rows: Sequence[_StatcastRow]) -> dict[str, object]:
    terminal = tuple(row for row in rows if row.is_terminal)
    if not terminal:
        return {name: None for name in HITTER_METRICS}
    batted = _batted_rows(terminal)
    barrels = tuple(row.is_barrel for row in batted if row.is_barrel is not None)
    exit_speeds = tuple(row.launch_speed for row in batted if row.launch_speed is not None)
    launch_angles = tuple(row.launch_angle for row in batted if row.launch_angle is not None)
    fly_balls = tuple(
        row.batted_ball_type for row in batted if row.batted_ball_type is not None
    )
    pulls = tuple(row.is_pull for row in batted if row.is_pull is not None)
    pa = len(terminal)
    hr = sum(row.is_home_run is True for row in terminal)
    return {
        "pa": pa,
        "hr": hr,
        "hr_per_pa": _rate(hr, pa),
        "barrel_rate": _rate(sum(barrels), len(barrels)),
        "hard_hit_rate": _rate(
            sum(value >= 95.0 for value in exit_speeds), len(exit_speeds)
        ),
        "average_exit_velocity": _mean(exit_speeds),
        "max_exit_velocity": max(exit_speeds, default=None),
        "sweet_spot_rate": _rate(
            sum(8.0 <= value <= 32.0 for value in launch_angles), len(launch_angles)
        ),
        # The persisted Statcast fields are contact-quality estimates, not a
        # documented PA-level expected-value series.  V2 therefore leaves the
        # PA-level xwOBA/xSLG contract explicitly unavailable.
        "xwoba": None,
        "xslg": None,
        "strikeout_rate": _rate(
            sum(row.event_type in _TERMINAL_STRIKEOUTS for row in terminal), pa
        ),
        "walk_rate": _rate(sum(row.event_type in _TERMINAL_WALKS for row in terminal), pa),
        "fly_ball_rate": _rate(
            sum(value == "fly_ball" for value in fly_balls), len(fly_balls)
        ),
        "pull_rate": _rate(sum(pulls), len(pulls)),
    }


def _pitcher_metrics(rows: Sequence[_StatcastRow]) -> dict[str, object]:
    hitter = _hitter_metrics(rows)
    if hitter["pa"] is None:
        return {name: None for name in PITCHER_METRICS}
    batted = _batted_rows(row for row in rows if row.is_terminal)
    ground_types = tuple(
        row.batted_ball_type for row in batted if row.batted_ball_type is not None
    )
    return {
        "batters_faced": hitter["pa"],
        "hr_allowed": hitter["hr"],
        "hr_per_batter_faced": hitter["hr_per_pa"],
        "barrel_rate_allowed": hitter["barrel_rate"],
        "hard_hit_rate_allowed": hitter["hard_hit_rate"],
        "average_exit_velocity_allowed": hitter["average_exit_velocity"],
        "xwoba_allowed": hitter["xwoba"],
        "xslg_allowed": hitter["xslg"],
        "strikeout_rate": hitter["strikeout_rate"],
        "walk_rate": hitter["walk_rate"],
        "ground_ball_rate": _rate(
            sum(value == "ground_ball" for value in ground_types), len(ground_types)
        ),
        "fly_ball_rate": _rate(
            sum(value == "fly_ball" for value in ground_types), len(ground_types)
        ),
    }


def _windowed_features(
    *,
    prefix: str,
    metric_names: Sequence[str],
    history: Sequence[_StatcastRow],
    operating_date: date,
    as_of: datetime,
    metric_builder: object,
) -> dict[str, object]:
    build = metric_builder
    if not callable(build):
        raise TypeError("metric_builder must be callable")
    values: dict[str, object] = {}
    season_rows = tuple(row for row in history if row.game_date.year == operating_date.year)
    season_metrics = build(season_rows)
    for metric in metric_names:
        values[f"{prefix}_season_{metric}"] = season_metrics[metric]
    for days in ROLLING_WINDOWS_DAYS:
        cutoff = as_of - timedelta(days=days)
        window_rows = tuple(row for row in history if cutoff <= row.completed_at <= as_of)
        metrics = build(window_rows)
        for metric in metric_names:
            values[f"{prefix}_{days}d_{metric}"] = metrics[metric]
    return values


def _pitch_type_rate_json(rows: Sequence[_StatcastRow]) -> str | None:
    counts: dict[str, int] = {}
    for row in rows:
        if row.pitch_type:
            counts[row.pitch_type] = counts.get(row.pitch_type, 0) + 1
    total = sum(counts.values())
    if not total:
        return None
    return json.dumps(
        {key: round(value / total, 10) for key, value in sorted(counts.items())},
        sort_keys=True,
        separators=(",", ":"),
    )


def _pitch_velocity_json(rows: Sequence[_StatcastRow]) -> str | None:
    grouped: dict[str, list[float]] = {}
    for row in rows:
        if row.pitch_type and row.release_speed is not None:
            grouped.setdefault(row.pitch_type, []).append(row.release_speed)
    if not grouped:
        return None
    return json.dumps(
        {
            key: round(sum(values) / len(values), 10)
            for key, values in sorted(grouped.items())
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _hitter_pitch_type_xwoba_json(rows: Sequence[_StatcastRow]) -> str | None:
    del rows
    return None


def _latest_snapshot_row(
    snapshot: SourceSnapshot | None,
    *,
    predicate: object,
    as_of: datetime,
    label: str,
    available_field: str,
    captured_field: str,
) -> tuple[Mapping[str, str] | None, datetime | None, datetime | None]:
    if snapshot is None:
        return None, None, None
    matches: list[tuple[datetime, datetime, Mapping[str, str]]] = []
    for index, row in enumerate(snapshot.rows, start=2):
        if not callable(predicate) or not predicate(row):
            continue
        available = _parse_aware_datetime(
            row.get(available_field), f"{snapshot.name} row {index}.{available_field}"
        )
        captured = _parse_aware_datetime(
            row.get(captured_field), f"{snapshot.name} row {index}.{captured_field}"
        )
        if available > captured:
            raise ContextFeatureError(
                f"{snapshot.name} row {index} publication/announcement is after capture"
            )
        if captured <= as_of:
            matches.append((available, captured, row))
    if not matches:
        return None, None, None
    latest_key = max((item[0], item[1]) for item in matches)
    latest = [row for available, captured, row in matches if (available, captured) == latest_key]
    distinct = {_sha256_value(dict(sorted(row.items()))) for row in latest}
    if len(distinct) != 1:
        raise ContextFeatureError(f"ambiguous {label} at {latest_key[1].isoformat()}")
    return latest[0], latest_key[0], latest_key[1]


def _probable_pitcher_context(
    snapshot: SourceSnapshot | None, candidate: _Candidate, as_of: datetime
) -> tuple[dict[str, object], datetime | None]:
    if snapshot is not None:
        for index, row in enumerate(snapshot.rows, start=2):
            if _optional_text(row, "event_id") != candidate.event_id:
                continue
            team = _optional_text(row, "team")
            if team not in {candidate.home_team, candidate.away_team}:
                raise ContextFeatureError(
                    f"probable_pitchers row {index} team does not match event identity"
                )
    row, announced, captured = _latest_snapshot_row(
        snapshot,
        predicate=lambda item: _optional_text(item, "event_id") == candidate.event_id
        and _optional_text(item, "team") == candidate.opponent,
        as_of=as_of,
        label=f"probable pitcher for {candidate.event_id}/{candidate.opponent}",
        available_field="announced_or_published_at_utc",
        captured_field="captured_at_utc",
    )
    if row is None or announced is None or captured is None:
        return {
            "probable_pitcher_id": None,
            "probable_pitcher_name": None,
            "normalized_probable_pitcher_name": None,
            "pitcher_hand": None,
            "probable_pitcher_status": "unknown",
            "probable_pitcher_identity_status": "unavailable",
            "probable_pitcher_identity_mapping_version": None,
            "probable_pitcher_announced_or_published_at_utc": None,
            "probable_pitcher_captured_at_utc": None,
            "probable_pitcher_available": False,
        }, None
    status = _required_text(row, "probable_pitcher_status", "probable pitcher").casefold()
    if status not in PROBABLE_PITCHER_STATUSES:
        raise ContextFeatureError(f"unsupported probable_pitcher_status: {status}")
    if status == "unknown":
        return {
            "probable_pitcher_id": None,
            "probable_pitcher_name": None,
            "normalized_probable_pitcher_name": None,
            "pitcher_hand": None,
            "probable_pitcher_status": "unknown",
            "probable_pitcher_identity_status": "unavailable",
            "probable_pitcher_identity_mapping_version": None,
            "probable_pitcher_announced_or_published_at_utc": None,
            "probable_pitcher_captured_at_utc": None,
            "probable_pitcher_available": False,
        }, None
    hand = _required_text(row, "pitcher_hand", "probable pitcher").upper()
    if hand not in {"L", "R"}:
        raise ContextFeatureError("probable pitcher hand must be L or R")
    pitcher_name = _required_text(row, "pitcher_name", "probable pitcher")
    normalized_pitcher_name = _required_text(
        row, "normalized_pitcher_name", "probable pitcher"
    )
    if normalized_pitcher_name != normalize_mlb_player_name(pitcher_name):
        raise ContextFeatureError(
            "probable pitcher normalized_pitcher_name does not match pitcher_name"
        )
    identity_status = _required_text(
        row, "identity_status", "probable pitcher"
    ).casefold()
    if identity_status != "verified_mlbam":
        raise ContextFeatureError("probable pitcher identity_status must be verified_mlbam")
    pitcher_id = _canonical_mlbam_id(row.get("pitcher_id"), "probable pitcher.pitcher_id")
    if pitcher_id != candidate.crosswalk_pitcher_id:
        raise ContextFeatureError("probable pitcher does not match verified crosswalk role")
    if normalized_pitcher_name != candidate.normalized_crosswalk_pitcher_name:
        raise ContextFeatureError("probable pitcher name conflicts with verified crosswalk")
    mapping_version = _required_text(row, "identity_mapping_version", "probable pitcher")
    if mapping_version != candidate.identity_mapping_version:
        raise ContextFeatureError("probable pitcher crosswalk mapping version mismatch")
    return {
        "probable_pitcher_id": pitcher_id,
        "probable_pitcher_name": pitcher_name,
        "normalized_probable_pitcher_name": normalized_pitcher_name,
        "pitcher_hand": hand,
        "probable_pitcher_status": status,
        "probable_pitcher_identity_status": identity_status,
        "probable_pitcher_identity_mapping_version": mapping_version,
        "probable_pitcher_announced_or_published_at_utc": _utc_text(announced),
        "probable_pitcher_captured_at_utc": _utc_text(captured),
        "probable_pitcher_available": True,
    }, captured


def _lineup_context(
    snapshot: SourceSnapshot | None, candidate: _Candidate, as_of: datetime
) -> tuple[dict[str, object], datetime | None]:
    if snapshot is not None:
        for index, row in enumerate(snapshot.rows, start=2):
            if (
                _optional_text(row, "event_id") == candidate.event_id
                and _optional_text(row, "player_id") == candidate.player_id
                and _optional_text(row, "team") != candidate.team
            ):
                raise ContextFeatureError(
                    f"lineups row {index} player/event team linkage mismatch"
                )
    row, announced, captured = _latest_snapshot_row(
        snapshot,
        predicate=lambda item: _optional_text(item, "event_id") == candidate.event_id
        and _optional_text(item, "player_id") == candidate.player_id
        and _optional_text(item, "team") == candidate.team,
        as_of=as_of,
        label=f"lineup for {candidate.event_id}/{candidate.player_id}",
        available_field="announced_or_published_at_utc",
        captured_field="captured_at_utc",
    )
    if row is None or announced is None or captured is None:
        return {
            "lineup_status": "unknown",
            "lineup_announced_or_published_at_utc": None,
            "lineup_captured_at_utc": None,
            "batting_order_position": None,
            "lineup_available": False,
        }, None
    status = _required_text(row, "lineup_status", "lineup").casefold()
    if status not in LINEUP_STATUSES:
        raise ContextFeatureError(f"unsupported lineup_status: {status}")
    batting_order = _optional_int(row, "batting_order_position", "lineup")
    if batting_order is not None and batting_order not in range(1, 10):
        raise ContextFeatureError("lineup.batting_order_position must be 1 through 9")
    if status == "confirmed" and batting_order is None:
        raise ContextFeatureError("confirmed lineup requires batting_order_position")
    if status == "unknown":
        return {
            "lineup_status": "unknown",
            "lineup_announced_or_published_at_utc": None,
            "lineup_captured_at_utc": None,
            "batting_order_position": None,
            "lineup_available": False,
        }, None
    return {
        "lineup_status": status,
        "lineup_announced_or_published_at_utc": _utc_text(announced),
        "lineup_captured_at_utc": _utc_text(captured),
        "batting_order_position": batting_order,
        "lineup_available": status != "unknown",
    }, captured


def _weather_context(
    snapshot: SourceSnapshot | None, candidate: _Candidate, as_of: datetime
) -> tuple[dict[str, object], datetime | None]:
    missing = {
        "temperature": None,
        "wind_speed": None,
        "wind_direction": None,
        "roof_status": None,
        "weather_type": None,
        "weather_evidence_class": None,
        "weather_evidence_at_utc": None,
        "weather_valid_for_utc": None,
        "weather_captured_at_utc": None,
        "weather_available": False,
    }
    if snapshot is None:
        return missing, None
    eligible: list[tuple[datetime, datetime, Mapping[str, str], datetime | None]] = []
    for index, row in enumerate(snapshot.rows, start=2):
        if _optional_text(row, "event_id") != candidate.event_id:
            continue
        label = f"weather row {index}"
        row_venue_id = _optional_text(row, "venue_id")
        row_venue_name = _required_text(row, "venue_name", label)
        if candidate.venue_id and row_venue_id != candidate.venue_id:
            raise ContextFeatureError(f"{label} venue_id mismatch")
        if normalize_venue_name(row_venue_name) != normalize_venue_name(
            candidate.venue_name
        ):
            raise ContextFeatureError(f"{label} venue_name mismatch")
        weather_type = _required_text(row, "weather_type", label).casefold()
        evidence_class = _required_text(row, "weather_evidence_class", label).casefold()
        if evidence_class in {"final", "final_game_weather", "postgame"}:
            raise ContextFeatureError(f"{label} contains forbidden final/postgame weather")
        captured = _parse_aware_datetime(row.get("captured_at_utc"), f"{label}.captured_at_utc")
        if weather_type == "forecast":
            if evidence_class != "provider_pregame_forecast":
                raise ContextFeatureError(f"{label} forecast lacks pregame source provenance")
            evidence_at = _parse_aware_datetime(row.get("issued_at_utc"), f"{label}.issued_at_utc")
            valid_for = _parse_aware_datetime(row.get("valid_for_utc"), f"{label}.valid_for_utc")
            if _optional_text(row, "measured_at_utc") is not None:
                raise ContextFeatureError(f"{label} forecast cannot carry measured_at_utc")
            if abs(valid_for - candidate.commence_time) > WEATHER_VALID_FOR_TOLERANCE:
                raise ContextFeatureError(f"{label} valid_for_utc does not cover game start")
        elif weather_type == "pregame_observation":
            if evidence_class != "provider_pregame_observation":
                raise ContextFeatureError(f"{label} observation lacks pregame source provenance")
            evidence_at = _parse_aware_datetime(
                row.get("measured_at_utc"), f"{label}.measured_at_utc"
            )
            valid_for = None
            if _optional_text(row, "issued_at_utc") is not None or _optional_text(
                row, "valid_for_utc"
            ) is not None:
                raise ContextFeatureError(f"{label} observation cannot carry forecast clocks")
            if evidence_at >= candidate.commence_time:
                raise ContextFeatureError(f"{label} weather observation is not strictly pregame")
        else:
            raise ContextFeatureError(f"{label} is not supported pregame weather evidence")
        if evidence_at > captured:
            raise ContextFeatureError(f"{label} evidence timestamp is after capture")
        if captured <= as_of:
            eligible.append((evidence_at, captured, row, valid_for))
    if not eligible:
        return missing, None
    latest_key = max((item[0], item[1]) for item in eligible)
    latest = [item for item in eligible if (item[0], item[1]) == latest_key]
    if len({_sha256_value(dict(sorted(item[2].items()))) for item in latest}) != 1:
        raise ContextFeatureError(f"ambiguous weather at {latest_key[1].isoformat()}")
    evidence_at, captured, row, valid_for = latest[0]
    temperature = _optional_float(row, "temperature", "weather")
    wind_speed = _optional_float(row, "wind_speed", "weather")
    wind_direction = _optional_text(row, "wind_direction")
    roof_status = _optional_text(row, "roof_status")
    if all(value is None for value in (temperature, wind_speed, wind_direction, roof_status)):
        return missing, None
    return {
        "temperature": temperature,
        "wind_speed": wind_speed,
        "wind_direction": wind_direction,
        "roof_status": roof_status,
        "weather_type": _required_text(row, "weather_type", "weather").casefold(),
        "weather_evidence_class": _required_text(
            row, "weather_evidence_class", "weather"
        ).casefold(),
        "weather_evidence_at_utc": _utc_text(evidence_at),
        "weather_valid_for_utc": _utc_text(valid_for) if valid_for else None,
        "weather_captured_at_utc": _utc_text(captured),
        "weather_available": True,
    }, captured


def _park_context(
    snapshot: SourceSnapshot | None,
    candidate: _Candidate,
    operating_date: date,
    as_of: datetime,
) -> tuple[dict[str, object], datetime | None]:
    if snapshot is None:
        row = None
        observed = None
    else:
        eligible: list[tuple[datetime, Mapping[str, str]]] = []
        venue_key = normalize_venue_name(candidate.venue_name)
        for index, item in enumerate(snapshot.rows, start=2):
            item_name = _required_text(item, "venue_name", f"park_factors row {index}")
            item_id = _optional_text(item, "venue_id")
            same_name = normalize_venue_name(item_name) == venue_key
            same_id = not candidate.venue_id or item_id == candidate.venue_id
            if same_name and not same_id:
                raise ContextFeatureError(f"park_factors row {index} venue_id mismatch")
            if not (same_name and same_id):
                continue
            valid_from = _parse_date(
                item.get("effective_from_date"),
                f"park_factors row {index}.effective_from_date",
            )
            valid_to_text = _optional_text(item, "effective_to_date")
            valid_to = (
                _parse_date(valid_to_text, f"park_factors row {index}.effective_to_date")
                if valid_to_text
                else None
            )
            if valid_to is not None and valid_to < valid_from:
                raise ContextFeatureError(f"park_factors row {index} invalid date range")
            published_at = _parse_aware_datetime(
                item.get("published_or_available_at_utc"),
                f"park_factors row {index}.published_or_available_at_utc",
            )
            captured_at = _parse_aware_datetime(
                item.get("captured_at_utc"),
                f"park_factors row {index}.captured_at_utc",
            )
            if published_at > captured_at:
                raise ContextFeatureError(
                    f"park_factors row {index} publication is after capture"
                )
            if (
                captured_at <= as_of
                and valid_from <= operating_date
                and (valid_to is None or operating_date <= valid_to)
            ):
                eligible.append((captured_at, item))
        if eligible:
            observed = max(item[0] for item in eligible)
            latest = [item for item_at, item in eligible if item_at == observed]
            if len({_sha256_value(dict(sorted(item.items()))) for item in latest}) != 1:
                raise ContextFeatureError(f"ambiguous park factor at {observed.isoformat()}")
            row = latest[0]
        else:
            row = None
            observed = None
    if row is None or observed is None:
        return {
            "park_hr_factor": None,
            "park_factor_source": None,
            "park_factor_version": None,
            "park_factor_published_or_available_at_utc": None,
            "park_factor_captured_at_utc": None,
            "park_factor_available": False,
        }, None
    factor = _optional_float(row, "park_hr_factor", "park factor")
    if factor is None or factor <= 0:
        return {
            "park_hr_factor": None,
            "park_factor_source": None,
            "park_factor_version": None,
            "park_factor_published_or_available_at_utc": None,
            "park_factor_captured_at_utc": None,
            "park_factor_available": False,
        }, None
    published = _parse_aware_datetime(
        row.get("published_or_available_at_utc"),
        "park factor.published_or_available_at_utc",
    )
    return {
        "park_hr_factor": factor,
        "park_factor_source": _required_text(row, "park_factor_source", "park factor"),
        "park_factor_version": _required_text(row, "park_factor_version", "park factor"),
        "park_factor_published_or_available_at_utc": _utc_text(published),
        "park_factor_captured_at_utc": _utc_text(observed),
        "park_factor_available": True,
    }, observed


def _market_context(
    snapshot: SourceSnapshot | None, candidate: _Candidate, as_of: datetime
) -> tuple[dict[str, object], datetime | None]:
    missing = {
        "market_best_sportsbook": None,
        "market_best_american_odds": None,
        "market_best_decimal_odds": None,
        "market_best_implied_probability": None,
        "market_bookmaker_count": 0,
        "market_implied_probability_dispersion": None,
        "market_hours_before_game": None,
        "market_best_observed_at_utc": None,
        "market_observed_at_utc": None,
        "market_configuration_ids_json": None,
        "market_quote_timestamps_json": None,
        "market_available": False,
    }
    if snapshot is None:
        return missing, None
    by_book: dict[str, list[tuple[datetime, datetime, Mapping[str, str]]]] = {}
    for index, row in enumerate(snapshot.rows, start=2):
        if (
            _optional_text(row, "event_id") != candidate.event_id
            or _optional_text(row, "player_id") != candidate.player_id
        ):
            continue
        evidence_class = _required_text(
            row, "evidence_class", f"market row {index}"
        ).casefold()
        if evidence_class != "pregame_snapshot":
            raise ContextFeatureError(
                f"market row {index} is forbidden non-pregame evidence: {evidence_class!r}"
            )
        quote_at = _parse_aware_datetime(
            row.get("quote_at_utc"), f"market row {index}.quote_at_utc"
        )
        captured_at = _parse_aware_datetime(
            row.get("captured_at_utc"), f"market row {index}.captured_at_utc"
        )
        if quote_at > captured_at:
            raise ContextFeatureError(f"market row {index} quote time is after capture")
        if quote_at >= candidate.commence_time or captured_at >= candidate.commence_time:
            raise ContextFeatureError(f"market row {index} is at/after commence_time_utc")
        if captured_at <= as_of:
            sportsbook = _required_text(row, "sportsbook", f"market row {index}")
            by_book.setdefault(sportsbook, []).append((quote_at, captured_at, row))
    selected: list[tuple[datetime, datetime, Mapping[str, str]]] = []
    for sportsbook, matches in sorted(by_book.items()):
        latest_key = max((item[0], item[1]) for item in matches)
        latest = [
            row for quote_at, captured_at, row in matches
            if (quote_at, captured_at) == latest_key
        ]
        if len({_sha256_value(dict(sorted(row.items()))) for row in latest}) != 1:
            raise ContextFeatureError(
                f"ambiguous market snapshot for {sportsbook} at {latest_key[0].isoformat()}"
            )
        selected.append((latest_key[0], latest_key[1], latest[0]))
    if not selected:
        return missing, None
    quotes: list[tuple[int, str, datetime, datetime, Mapping[str, str]]] = []
    probabilities: list[float] = []
    configurations: set[str] = set()
    timestamp_lineage: dict[str, dict[str, str]] = {}
    for quote_at, captured_at, row in selected:
        label = f"market {row.get('sportsbook')}"
        american = _optional_int(row, "american_odds", label)
        if american is None or american == 0:
            raise ContextFeatureError(f"{label}.american_odds must be nonzero")
        probability = american_to_implied_probability(american)
        probabilities.append(probability)
        configurations.add(_required_text(row, "market_configuration_id", label))
        sportsbook = _required_text(row, "sportsbook", label)
        configuration = _required_text(row, "market_configuration_id", label)
        quotes.append((american, sportsbook, quote_at, captured_at, row))
        timestamp_lineage[sportsbook] = {
            "quote_at_utc": _utc_text(quote_at),
            "captured_at_utc": _utc_text(captured_at),
            "market_configuration_id": configuration,
        }
    best_american, best_book, best_quote_at, _, _ = max(
        quotes, key=lambda item: (item[0], item[1].casefold())
    )
    latest_observed = max(item[1] for item in selected)
    return {
        "market_best_sportsbook": best_book,
        "market_best_american_odds": best_american,
        "market_best_decimal_odds": round(american_to_decimal(best_american), 10),
        "market_best_implied_probability": round(
            american_to_implied_probability(best_american), 10
        ),
        "market_bookmaker_count": len(quotes),
        "market_implied_probability_dispersion": round(
            max(probabilities) - min(probabilities), 10
        ),
        "market_hours_before_game": round(
            (candidate.commence_time - best_quote_at).total_seconds() / 3600.0, 10
        ),
        "market_best_observed_at_utc": _utc_text(best_quote_at),
        "market_observed_at_utc": _utc_text(latest_observed),
        "market_configuration_ids_json": json.dumps(
            sorted(configurations), separators=(",", ":")
        ),
        "market_quote_timestamps_json": json.dumps(
            timestamp_lineage, sort_keys=True, separators=(",", ":")
        ),
        "market_available": True,
    }, latest_observed


def _configuration_payload() -> dict[str, object]:
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_columns": list(FEATURE_COLUMNS),
        "rolling_windows_days": list(ROLLING_WINDOWS_DAYS),
        "season_policy": "calendar_year_of_operating_date",
        "rolling_policy": "game_completed_at_utc in [as_of_utc-Nd, as_of_utc]",
        "matchup_horizon_policy": MATCHUP_HORIZON_POLICY,
        "market_staleness_policy": MARKET_STALENESS_POLICY,
        "weather_forecast_valid_for_tolerance_seconds": int(
            WEATHER_VALID_FOR_TOLERANCE.total_seconds()
        ),
        "temporal_policies": {
            "candidate": "available <= captured <= universe_cutoff == as_of < commence",
            "statcast": "completed <= available <= collected <= as_of; target excluded",
            "probable_pitcher": "announced_or_published <= captured <= as_of",
            "lineup": "announced_or_published <= captured <= as_of",
            "weather_forecast": "issued <= captured <= as_of; valid_for covers commence",
            "weather_observation": "measured <= captured <= as_of < commence",
            "park_factor": "published_or_available <= captured <= as_of; effective range covers game",
            "market": "quote <= captured <= as_of < commence",
        },
        "stat_aggregation_level": (
            "one_row_per_unique_pitch_with_exactly_one_terminal_row_per_plate_appearance"
        ),
        "hard_hit_threshold_mph": 95.0,
        "sweet_spot_launch_angle_degrees": [8.0, 32.0],
        "expected_pa_policy": "unavailable_no_imputation",
        "expected_stat_policy": "pa_level_xwoba_and_xslg_unavailable_in_v2",
        "missingness_policy": "null_or_empty_with_explicit_family_flags",
        "source_files": dict(SOURCE_FILES),
        "required_source_columns": {
            key: sorted(value) for key, value in REQUIRED_SOURCE_COLUMNS.items()
        },
    }


def _source_identity(
    snapshots: Mapping[str, SourceSnapshot | None]
) -> tuple[str, str, str]:
    digests = {
        name: snapshot.sha256
        for name, snapshot in snapshots.items()
        if snapshot is not None
    }
    snapshot_ids = {
        name: snapshot.snapshot_id
        for name, snapshot in snapshots.items()
        if snapshot is not None
    }
    digest_json = json.dumps(digests, sort_keys=True, separators=(",", ":"))
    ids_json = json.dumps(snapshot_ids, sort_keys=True, separators=(",", ":"))
    return ids_json, digest_json, _sha256_value(digests)


def _feature_row_id(
    *,
    candidate: _Candidate,
    as_of: datetime,
    configuration_digest: str,
    source_identity_digest: str,
) -> str:
    digest = _sha256_value(
        {
            "schema": FEATURE_SCHEMA_VERSION,
            "event_id": candidate.event_id,
            "player_id": candidate.player_id,
            "as_of_utc": _utc_text(as_of),
            "configuration_digest": configuration_digest,
            "source_identity_digest": source_identity_digest,
        }
    )
    return f"mlbhrctx_{digest}"


def _source_capability_gaps(
    snapshots: Mapping[str, SourceSnapshot | None]
) -> tuple[str, ...]:
    gaps: list[str] = ["expected_pa_model", "pa_level_xwoba", "pa_level_xslg"]
    if snapshots["statcast"] is None:
        gaps.extend(("hitter_statcast", "pitcher_statcast", "matchup_statcast"))
    else:
        headers = set(snapshots["statcast"].headers)  # type: ignore[union-attr]
        optional_capabilities = {
            "launch_speed": "exit_velocity_and_hard_hit",
            "launch_angle": "sweet_spot",
            "is_barrel": "barrel_rate",
            "batted_ball_type": "batted_ball_tendency",
            "is_pull": "pull_rate",
            "pitch_type": "pitch_mix_and_pitch_type_matchup",
            "release_speed": "pitch_velocity",
        }
        gaps.extend(
            capability
            for field, capability in optional_capabilities.items()
            if field not in headers
            or not any(
                str(row.get(field) or "").strip()
                for row in snapshots["statcast"].rows  # type: ignore[union-attr]
            )
        )
    gaps.extend(
        source
        for source in ("probable_pitchers", "lineups", "weather", "park_factors", "market")
        if snapshots[source] is None
    )
    return tuple(sorted(set(gaps)))


def _statcast_metric_availability(rows: Sequence[_StatcastRow]) -> str:
    fields = (
        "launch_speed",
        "launch_angle",
        "is_barrel",
        "batted_ball_type",
        "is_pull",
        "pitch_type",
        "release_speed",
    )
    availability = {
        field: any(getattr(row, field) is not None for row in rows)
        for field in fields
    }
    availability["pa_level_xwoba"] = False
    availability["pa_level_xslg"] = False
    return json.dumps(availability, sort_keys=True, separators=(",", ":"))


def _build_feature_row(
    *,
    candidate: _Candidate,
    operating_date: date,
    as_of: datetime,
    statcast: Sequence[_StatcastRow],
    snapshots: Mapping[str, SourceSnapshot | None],
    configuration_digest: str,
    source_snapshot_ids_json: str,
    source_digests_json: str,
    source_identity_digest: str,
    git_commit: str,
) -> dict[str, object]:
    if as_of >= candidate.commence_time:
        raise ContextFeatureError(
            f"as_of_utc must precede commence_time_utc for {candidate.event_id}"
        )
    if candidate.operating_date != operating_date:
        raise ContextFeatureError(
            f"candidate operating_date does not match requested operating_date for "
            f"{candidate.event_id}"
        )
    if candidate.candidate_universe_cutoff != as_of:
        raise ContextFeatureError(
            f"candidate_universe_cutoff_utc must equal as_of_utc for {candidate.event_id}"
        )
    if candidate.captured_at > as_of:
        raise ContextFeatureError(f"candidate was captured after as_of_utc for {candidate.event_id}")
    eligible = _eligible_history(statcast, candidate, as_of)
    hitter_history = tuple(row for row in eligible if row.batter_id == candidate.player_id)

    probable, probable_observed = _probable_pitcher_context(
        snapshots["probable_pitchers"], candidate, as_of
    )
    lineup, lineup_observed = _lineup_context(snapshots["lineups"], candidate, as_of)
    weather, weather_observed = _weather_context(
        snapshots["weather"], candidate, as_of
    )
    park, park_observed = _park_context(
        snapshots["park_factors"], candidate, operating_date, as_of
    )
    market, market_observed = _market_context(snapshots["market"], candidate, as_of)

    pitcher_id = probable["probable_pitcher_id"]
    pitcher_history = (
        tuple(row for row in eligible if row.pitcher_id == pitcher_id)
        if isinstance(pitcher_id, str)
        else ()
    )
    hitter_terminals = tuple(row for row in hitter_history if row.is_terminal)
    pitcher_terminals = tuple(row for row in pitcher_history if row.is_terminal)
    rolling_floor = as_of - timedelta(days=max(ROLLING_WINDOWS_DAYS))
    hitter_feature_terminals = tuple(
        row
        for row in hitter_terminals
        if row.game_date.year == operating_date.year or row.completed_at >= rolling_floor
    )
    pitcher_feature_terminals = tuple(
        row
        for row in pitcher_terminals
        if row.game_date.year == operating_date.year or row.completed_at >= rolling_floor
    )
    hitter_matchup_history = tuple(
        row for row in hitter_history if row.game_date.year == operating_date.year
    )
    pitcher_matchup_history = tuple(
        row for row in pitcher_history if row.game_date.year == operating_date.year
    )
    hitter_matchup_terminals = tuple(row for row in hitter_matchup_history if row.is_terminal)

    hitter_features = _windowed_features(
        prefix="hitter",
        metric_names=HITTER_METRICS,
        history=hitter_history,
        operating_date=operating_date,
        as_of=as_of,
        metric_builder=_hitter_metrics,
    )
    pitcher_features = _windowed_features(
        prefix="pitcher",
        metric_names=PITCHER_METRICS,
        history=pitcher_history,
        operating_date=operating_date,
        as_of=as_of,
        metric_builder=_pitcher_metrics,
    )
    pitcher_hand = probable["pitcher_hand"]
    hitter_split = tuple(
        row
        for row in hitter_matchup_history
        if row.pitcher_hand == pitcher_hand and row.is_terminal
    )
    pitcher_split = tuple(
        row
        for row in pitcher_matchup_history
        if row.batter_hand == candidate.batter_hand and row.is_terminal
    )
    bvp = tuple(
        row
        for row in hitter_matchup_history
        if isinstance(pitcher_id, str)
        and row.pitcher_id == pitcher_id
        and row.is_terminal
    )
    hitter_split_metrics = _hitter_metrics(hitter_split)
    pitcher_split_metrics = _pitcher_metrics(pitcher_split)
    matchup = {
        "platoon_matchup_category": (
            None
            if not isinstance(pitcher_hand, str)
            else (
                "same_side"
                if candidate.batter_hand != "S" and candidate.batter_hand == pitcher_hand
                else "opposite_or_switch"
            )
        ),
        "hitter_vs_pitcher_hand_pa": hitter_split_metrics["pa"],
        "hitter_vs_pitcher_hand_hr_per_pa": hitter_split_metrics["hr_per_pa"],
        "pitcher_vs_batter_hand_batters_faced": pitcher_split_metrics["batters_faced"],
        "pitcher_vs_batter_hand_hr_per_batter_faced": pitcher_split_metrics[
            "hr_per_batter_faced"
        ],
        "hitter_pitch_type_xwoba_json": _hitter_pitch_type_xwoba_json(
            hitter_matchup_history
        ),
        "pitcher_pitch_mix_json": _pitch_type_rate_json(pitcher_matchup_history),
        "pitcher_average_velocity_json": _pitch_velocity_json(pitcher_matchup_history),
        "bvp_pa_descriptive": (
            len(bvp) if isinstance(pitcher_id, str) and hitter_matchup_terminals else None
        ),
        "bvp_hr_descriptive": (
            sum(row.is_home_run is True for row in bvp)
            if isinstance(pitcher_id, str) and hitter_matchup_terminals
            else None
        ),
    }

    source_observed: dict[str, str] = {}
    source_observed["candidates"] = _utc_text(candidate.captured_at)
    if hitter_history or pitcher_history:
        source_observed["statcast"] = _utc_text(
            max(row.collected_at for row in (*hitter_history, *pitcher_history))
        )
    for name, observed in (
        ("probable_pitchers", probable_observed),
        ("lineups", lineup_observed),
        ("weather", weather_observed),
        ("park_factors", park_observed),
        ("market", market_observed),
    ):
        if observed is not None:
            source_observed[name] = _utc_text(observed)

    row: dict[str, object] = {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_row_id": _feature_row_id(
            candidate=candidate,
            as_of=as_of,
            configuration_digest=configuration_digest,
            source_identity_digest=source_identity_digest,
        ),
        "research_only": True,
        "operating_date": operating_date.isoformat(),
        "as_of_utc": _utc_text(as_of),
        "event_id": candidate.event_id,
        "commence_time_utc": _utc_text(candidate.commence_time),
        "home_team": candidate.home_team,
        "away_team": candidate.away_team,
        "venue_id": candidate.venue_id,
        "venue_name": candidate.venue_name,
        "team": candidate.team,
        "opponent": candidate.opponent,
        "is_home": candidate.team == candidate.home_team,
        "player_id": candidate.player_id,
        "player_name": candidate.player_name,
        "normalized_player_name": candidate.normalized_player_name,
        "batter_hand": candidate.batter_hand,
        "identity_status": candidate.identity_status,
        "identity_mapping_version": candidate.identity_mapping_version,
        "identity_crosswalk_digest": candidate.identity_crosswalk_digest,
        "candidate_universe_id": candidate.candidate_universe_id,
        "candidate_universe_version": candidate.candidate_universe_version,
        "candidate_universe_generator": candidate.candidate_universe_generator,
        "candidate_universe_origin": candidate.candidate_universe_origin,
        "candidate_universe_policy": candidate.candidate_universe_policy,
        "candidate_universe_source_digest": candidate.candidate_universe_source_digest,
        "candidate_universe_configuration_digest": (
            candidate.candidate_universe_configuration_digest
        ),
        "candidate_universe_cutoff_utc": _utc_text(candidate.candidate_universe_cutoff),
        "candidate_published_or_available_at_utc": _utc_text(
            candidate.published_or_available_at
        ),
        "candidate_captured_at_utc": _utc_text(candidate.captured_at),
        "probable_pitcher_id": probable["probable_pitcher_id"],
        "probable_pitcher_name": probable["probable_pitcher_name"],
        "normalized_probable_pitcher_name": probable[
            "normalized_probable_pitcher_name"
        ],
        "pitcher_hand": probable["pitcher_hand"],
        "probable_pitcher_status": probable["probable_pitcher_status"],
        "probable_pitcher_identity_status": probable[
            "probable_pitcher_identity_status"
        ],
        "probable_pitcher_identity_mapping_version": probable[
            "probable_pitcher_identity_mapping_version"
        ],
        "probable_pitcher_announced_or_published_at_utc": probable[
            "probable_pitcher_announced_or_published_at_utc"
        ],
        "probable_pitcher_captured_at_utc": probable[
            "probable_pitcher_captured_at_utc"
        ],
        "lineup_status": lineup["lineup_status"],
        "lineup_announced_or_published_at_utc": lineup[
            "lineup_announced_or_published_at_utc"
        ],
        "lineup_captured_at_utc": lineup["lineup_captured_at_utc"],
        "batting_order_position": lineup["batting_order_position"],
        "expected_pa": None,
        "hitter_stats_available": bool(hitter_feature_terminals),
        "pitcher_stats_available": bool(pitcher_feature_terminals),
        "probable_pitcher_available": probable["probable_pitcher_available"],
        "lineup_available": lineup["lineup_available"],
        "expected_pa_available": False,
        "park_factor_available": park["park_factor_available"],
        "weather_available": weather["weather_available"],
        "market_available": market["market_available"],
        **hitter_features,
        **pitcher_features,
        **matchup,
        "park_hr_factor": park["park_hr_factor"],
        "park_factor_source": park["park_factor_source"],
        "park_factor_version": park["park_factor_version"],
        "park_factor_published_or_available_at_utc": park[
            "park_factor_published_or_available_at_utc"
        ],
        "park_factor_captured_at_utc": park["park_factor_captured_at_utc"],
        "temperature": weather["temperature"],
        "wind_speed": weather["wind_speed"],
        "wind_direction": weather["wind_direction"],
        "roof_status": weather["roof_status"],
        "weather_type": weather["weather_type"],
        "weather_evidence_class": weather["weather_evidence_class"],
        "weather_evidence_at_utc": weather["weather_evidence_at_utc"],
        "weather_valid_for_utc": weather["weather_valid_for_utc"],
        "weather_captured_at_utc": weather["weather_captured_at_utc"],
        "market_best_sportsbook": market["market_best_sportsbook"],
        "market_best_american_odds": market["market_best_american_odds"],
        "market_best_decimal_odds": market["market_best_decimal_odds"],
        "market_best_implied_probability": market[
            "market_best_implied_probability"
        ],
        "market_bookmaker_count": market["market_bookmaker_count"],
        "market_implied_probability_dispersion": market[
            "market_implied_probability_dispersion"
        ],
        "market_hours_before_game": market["market_hours_before_game"],
        "market_best_observed_at_utc": market["market_best_observed_at_utc"],
        "market_observed_at_utc": market["market_observed_at_utc"],
        "market_configuration_ids_json": market[
            "market_configuration_ids_json"
        ],
        "market_quote_timestamps_json": market["market_quote_timestamps_json"],
        "source_snapshot_ids_json": source_snapshot_ids_json,
        "source_digests_json": source_digests_json,
        "source_max_captured_at_utc_json": json.dumps(
            source_observed, sort_keys=True, separators=(",", ":")
        ),
        "statcast_metric_availability_json": _statcast_metric_availability(
            (*hitter_history, *pitcher_history)
        ),
        "source_identity_digest": source_identity_digest,
        "configuration_digest": configuration_digest,
        "git_commit": git_commit,
        "feature_manifest_reference": MANIFEST_FILENAME,
    }
    if tuple(row) != FEATURE_COLUMNS:
        missing = sorted(set(FEATURE_COLUMNS) - set(row))
        extra = sorted(set(row) - set(FEATURE_COLUMNS))
        raise ContextFeatureError(
            f"internal feature schema order mismatch; missing={missing}, extra={extra}"
        )
    _validate_feature_values(row)
    return row


def _validate_feature_values(row: Mapping[str, object]) -> None:
    for key, value in row.items():
        if isinstance(value, float) and not math.isfinite(value):
            raise ContextFeatureError(f"{key} cannot contain NaN or Infinity")
    if row.get("research_only") is not True:
        raise ContextFeatureError("feature rows must remain research_only")
    as_of = _parse_aware_datetime(row.get("as_of_utc"), "row.as_of_utc")
    commence = _parse_aware_datetime(
        row.get("commence_time_utc"), "row.commence_time_utc"
    )
    if as_of >= commence:
        raise ContextFeatureError("row violates as_of_utc < commence_time_utc")
    for prefix, metrics, flag in (
        ("hitter", HITTER_METRICS, "hitter_stats_available"),
        ("pitcher", PITCHER_METRICS, "pitcher_stats_available"),
    ):
        names = _window_feature_names(prefix, metrics)
        if row.get(flag) is False and any(row.get(name) is not None for name in names):
            raise ContextFeatureError(f"{flag}=false requires null {prefix} features")
    for name in (
        "hitter_season_xwoba",
        "hitter_season_xslg",
        "hitter_7d_xwoba",
        "hitter_7d_xslg",
        "hitter_14d_xwoba",
        "hitter_14d_xslg",
        "hitter_30d_xwoba",
        "hitter_30d_xslg",
        "pitcher_season_xwoba_allowed",
        "pitcher_season_xslg_allowed",
        "pitcher_7d_xwoba_allowed",
        "pitcher_7d_xslg_allowed",
        "pitcher_14d_xwoba_allowed",
        "pitcher_14d_xslg_allowed",
        "pitcher_30d_xwoba_allowed",
        "pitcher_30d_xslg_allowed",
        "hitter_pitch_type_xwoba_json",
    ):
        if row.get(name) is not None:
            raise ContextFeatureError(f"{name} is unavailable under the v2 source contract")
    availability_contracts = (
        (
            "park_factor_available",
            ("park_hr_factor", "park_factor_source", "park_factor_version"),
        ),
        (
            "weather_available",
            ("weather_type", "weather_evidence_class", "weather_evidence_at_utc"),
        ),
        (
            "probable_pitcher_available",
            ("probable_pitcher_id", "pitcher_hand"),
        ),
    )
    for flag, required_values in availability_contracts:
        available = row.get(flag) is True
        present = all(row.get(name) is not None for name in required_values)
        if available != present:
            raise ContextFeatureError(f"{flag} does not agree with usable feature values")
    if row.get("lineup_available") is False and (
        row.get("batting_order_position") is not None
        or row.get("lineup_announced_or_published_at_utc") is not None
        or row.get("lineup_captured_at_utc") is not None
    ):
        raise ContextFeatureError("unavailable lineup must remain null")
    if row.get("expected_pa_available") is not False or row.get("expected_pa") is not None:
        raise ContextFeatureError("expected_pa must remain explicitly unavailable")
    market_fields = (
        "market_best_sportsbook",
        "market_best_american_odds",
        "market_best_decimal_odds",
        "market_best_implied_probability",
        "market_implied_probability_dispersion",
        "market_hours_before_game",
        "market_best_observed_at_utc",
        "market_observed_at_utc",
        "market_configuration_ids_json",
        "market_quote_timestamps_json",
    )
    if row.get("market_available") is False and (
        row.get("market_bookmaker_count") != 0
        or any(row.get(name) is not None for name in market_fields)
    ):
        raise ContextFeatureError("unavailable market must contain zero books and null values")


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContextFeatureError("CSV output cannot contain NaN or Infinity")
        return format(value, ".15g")
    if isinstance(value, (str, int)):
        return str(value)
    raise ContextFeatureError(f"unsupported CSV value type: {type(value).__name__}")


def _features_csv_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=FEATURE_COLUMNS,
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        if tuple(row) != FEATURE_COLUMNS:
            raise ContextFeatureError("feature row does not match exact column order")
        writer.writerow({name: _csv_cell(row[name]) for name in FEATURE_COLUMNS})
    return output.getvalue().encode("utf-8")


def _json_artifact_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _verify_source_immutability(
    snapshots: Mapping[str, SourceSnapshot | None]
) -> None:
    mutated: list[str] = []
    for name, snapshot in snapshots.items():
        if snapshot is None:
            continue
        try:
            current = compute_file_sha256(snapshot.path)
        except OSError as exc:
            raise ContextFeatureError(
                f"could not re-verify immutable source {snapshot.path.name}: {exc}"
            ) from exc
        if current != snapshot.sha256:
            mutated.append(name)
    if mutated:
        raise ContextFeatureError(
            "source evidence changed during materialization: " + ", ".join(mutated)
        )


def _source_artifact_manifest(
    snapshots: Mapping[str, SourceSnapshot | None]
) -> list[dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    for name in SOURCE_FILES:
        snapshot = snapshots[name]
        if snapshot is None:
            artifacts.append(
                {
                    "source_name": name,
                    "filename": SOURCE_FILES[name],
                    "available": False,
                    "sha256": None,
                    "byte_size": None,
                    "row_count": 0,
                    "columns": [],
                    "snapshot_id": None,
                }
            )
        else:
            artifacts.append(
                {
                    "source_name": name,
                    "filename": snapshot.path.name,
                    "available": True,
                    "sha256": snapshot.sha256,
                    "byte_size": snapshot.byte_size,
                    "row_count": len(snapshot.rows),
                    "columns": list(snapshot.headers),
                    "snapshot_id": snapshot.snapshot_id,
                }
            )
    return artifacts


def _statcast_source_coverage(snapshot: SourceSnapshot | None) -> Mapping[str, object]:
    if snapshot is None or not snapshot.rows:
        return MappingProxyType(
            {
                "available": False,
                "game_date_start": None,
                "game_date_end": None,
                "completed_at_start_utc": None,
                "completed_at_end_utc": None,
            }
        )
    game_dates = tuple(
        _parse_date(row.get("game_date"), "statcast.game_date") for row in snapshot.rows
    )
    completed = tuple(
        _parse_aware_datetime(
            row.get("game_completed_at_utc"), "statcast.game_completed_at_utc"
        )
        for row in snapshot.rows
    )
    return MappingProxyType(
        {
            "available": True,
            "game_date_start": min(game_dates).isoformat(),
            "game_date_end": max(game_dates).isoformat(),
            "completed_at_start_utc": _utc_text(min(completed)),
            "completed_at_end_utc": _utc_text(max(completed)),
        }
    )


def _build_manifest_and_summary(
    *,
    rows: Sequence[Mapping[str, object]],
    features_bytes: bytes,
    snapshots: Mapping[str, SourceSnapshot | None],
    configuration_digest: str,
    source_identity_digest: str,
    git_commit: str,
    operating_date: date,
    as_of: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    features_sha256 = hashlib.sha256(features_bytes).hexdigest()
    safety = mlb_research_safety_fields()
    universe = (
        {
            name: rows[0][name]
            for name in (
                "candidate_universe_id",
                "candidate_universe_version",
                "candidate_universe_generator",
                "candidate_universe_origin",
                "candidate_universe_policy",
                "candidate_universe_source_digest",
                "candidate_universe_configuration_digest",
                "candidate_universe_cutoff_utc",
            )
        }
        if rows
        else {}
    )
    manifest: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "research_only": True,
        "mode": safety["mode"],
        "approval_status": safety["betting_approval_status"],
        "eligible_for_betting": safety["eligible_for_betting"],
        "kelly_eligible": safety["kelly_eligible"],
        "model_training_enabled": False,
        "predictions_enabled": False,
        "promotion_enabled": False,
        "git_commit": git_commit,
        "configuration_digest": configuration_digest,
        "source_identity_digest": source_identity_digest,
        "source_artifacts": _source_artifact_manifest(snapshots),
        "source_coverage": {
            "statcast": dict(_statcast_source_coverage(snapshots["statcast"]))
        },
        "row_count": len(rows),
        "feature_row_ids": [row["feature_row_id"] for row in rows],
        "operating_date_range": [operating_date.isoformat(), operating_date.isoformat()],
        "as_of_utc_range": [_utc_text(as_of), _utc_text(as_of)],
        "feature_column_allowlist": list(FEATURE_COLUMNS),
        "identity_contract": {
            "event": "exact event_id plus exact home/away/team/opponent linkage",
            "player": (
                "canonical MLBAM id verified by digest-bound crosswalk; names are audit-only"
            ),
            "pitcher": (
                "canonical MLBAM id verified in the opposing-pitcher role; missing is unavailable"
            ),
            "crosswalk_digest": rows[0]["identity_crosswalk_digest"] if rows else None,
            "feature_row_id": (
                "sha256(schema,event,player,as_of,configuration,source identity)"
            ),
        },
        "candidate_universe_contract": universe,
        "temporal_contracts": dict(_configuration_payload()["temporal_policies"]),
        "matchup_horizon_policy": MATCHUP_HORIZON_POLICY,
        "market_staleness_policy": MARKET_STALENESS_POLICY,
        "missingness_contract": (
            "no imputation; unavailable scalar values serialize as null in memory "
            "and empty CSV cells with explicit family availability flags"
        ),
        "source_capability_gaps": list(_source_capability_gaps(snapshots)),
        "artifacts": {
            FEATURES_FILENAME: {
                "sha256": features_sha256,
                "columns": list(FEATURE_COLUMNS),
                "row_count": len(rows),
            },
            MANIFEST_FILENAME: {"self_digest_field": "manifest_digest"},
            BUILD_SUMMARY_FILENAME: {"schema_version": BUILD_SUMMARY_SCHEMA_VERSION},
        },
    }
    manifest["manifest_digest"] = _sha256_value(manifest)
    availability_fields = (
        "hitter_stats_available",
        "pitcher_stats_available",
        "probable_pitcher_available",
        "lineup_available",
        "park_factor_available",
        "weather_available",
        "market_available",
    )
    summary: dict[str, object] = {
        "schema_version": BUILD_SUMMARY_SCHEMA_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "research_only": True,
        "dry_run_capable": True,
        "row_count": len(rows),
        "operating_date": operating_date.isoformat(),
        "as_of_utc": _utc_text(as_of),
        "manifest_digest": manifest["manifest_digest"],
        "configuration_digest": configuration_digest,
        "source_identity_digest": source_identity_digest,
        "features_sha256": features_sha256,
        "availability_counts": {
            name: sum(row[name] is True for row in rows) for name in availability_fields
        },
        "source_capability_gaps": list(_source_capability_gaps(snapshots)),
        "artifacts": [FEATURES_FILENAME, MANIFEST_FILENAME, BUILD_SUMMARY_FILENAME],
        "model_training_performed": False,
        "provider_network_access_performed": False,
        "live_prospective_trial_modified": False,
        "official_pick_or_lifecycle_modified": False,
    }
    return manifest, summary


def _validate_output_root(
    output_root: str | Path, source_root: str | Path
) -> tuple[Path, Path]:
    output = Path(output_root).expanduser().resolve()
    source = Path(source_root).expanduser().resolve()
    allowed = CONTEXT_FEATURE_RESEARCH_ROOT.expanduser().resolve()
    if not allowed.is_dir():
        raise ContextFeatureError(
            f"dedicated context-feature research root does not exist: {allowed}"
        )
    if output == allowed or not output.is_relative_to(allowed):
        raise ContextFeatureError(
            f"output root must be a new descendant of dedicated research root: {allowed}"
        )
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ContextFeatureError("output root must be separate from immutable source root")
    if output.exists():
        raise ContextFeatureError(f"immutable output root already exists: {output}")
    if not output.parent.is_dir():
        raise ContextFeatureError(
            f"output parent directory does not exist: {output.parent}"
        )
    return output, output.parent


def _verify_persisted_artifacts(
    expected: Mapping[Path, bytes],
) -> None:
    for path, payload in expected.items():
        try:
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ContextFeatureError(f"could not verify persisted artifact {path.name}: {exc}") from exc
        expected_digest = hashlib.sha256(payload).hexdigest()
        if actual_digest != expected_digest:
            raise ContextFeatureError(f"persisted {path.name} digest mismatch")


def _write_artifacts_atomically(
    *,
    output_root: Path,
    parent: Path,
    features_bytes: bytes,
    manifest_bytes: bytes,
    summary_bytes: bytes,
) -> tuple[Path, Path, Path]:
    temporary = Path(tempfile.mkdtemp(prefix=".courtvision-mlb-hr-context-", dir=parent))
    published = False
    try:
        (temporary / FEATURES_FILENAME).write_bytes(features_bytes)
        (temporary / MANIFEST_FILENAME).write_bytes(manifest_bytes)
        (temporary / BUILD_SUMMARY_FILENAME).write_bytes(summary_bytes)
        temporary.replace(output_root)
        published = True
    except OSError as exc:
        raise ContextFeatureError(f"could not publish context feature artifacts: {exc}") from exc
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)
    features_path = output_root / FEATURES_FILENAME
    manifest_path = output_root / MANIFEST_FILENAME
    summary_path = output_root / BUILD_SUMMARY_FILENAME
    try:
        _verify_persisted_artifacts(
            {
                features_path: features_bytes,
                manifest_path: manifest_bytes,
                summary_path: summary_bytes,
            }
        )
    except ContextFeatureError:
        if output_root.exists():
            shutil.rmtree(output_root)
        raise
    return features_path, manifest_path, summary_path


def build_context_features(
    *,
    operating_date: date | str,
    as_of_utc: datetime | str,
    source_root: str | Path,
    output_root: str | Path | None = None,
    git_commit: str,
    dry_run: bool = False,
) -> ContextFeatureBuildResult:
    """Build deterministic point-in-time rows from immutable local snapshots."""

    parsed_date = (
        operating_date
        if isinstance(operating_date, date)
        else _parse_date(operating_date, "operating_date")
    )
    parsed_as_of = (
        as_of_utc.astimezone(timezone.utc)
        if isinstance(as_of_utc, datetime)
        and as_of_utc.tzinfo is not None
        and as_of_utc.utcoffset() is not None
        else _parse_aware_datetime(as_of_utc, "as_of_utc")
    )
    if isinstance(as_of_utc, datetime) and (
        as_of_utc.tzinfo is None or as_of_utc.utcoffset() is None
    ):
        raise ContextFeatureError("as_of_utc must be timezone-aware")
    commit = str(git_commit).strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ContextFeatureError("git_commit must be a 40-character hexadecimal SHA")

    snapshots = _load_snapshots(source_root)
    candidates_snapshot = snapshots["candidates"]
    if candidates_snapshot is None:
        raise ContextFeatureError("candidates snapshot is required")
    crosswalk_snapshot = snapshots["identity_crosswalk"]
    if crosswalk_snapshot is None:
        raise ContextFeatureError("identity_crosswalk snapshot is required")
    candidates = _parse_candidates(candidates_snapshot, crosswalk_snapshot)
    statcast = _parse_statcast(snapshots["statcast"])
    configuration_digest = _sha256_value(_configuration_payload())
    snapshot_ids_json, source_digests_json, source_identity_digest = _source_identity(
        snapshots
    )
    rows = tuple(
        _build_feature_row(
            candidate=candidate,
            operating_date=parsed_date,
            as_of=parsed_as_of,
            statcast=statcast,
            snapshots=snapshots,
            configuration_digest=configuration_digest,
            source_snapshot_ids_json=snapshot_ids_json,
            source_digests_json=source_digests_json,
            source_identity_digest=source_identity_digest,
            git_commit=commit,
        )
        for candidate in candidates
    )
    if len({row["feature_row_id"] for row in rows}) != len(rows):
        raise ContextFeatureError("feature_row_id collision detected")
    features_bytes = _features_csv_bytes(rows)
    manifest, summary = _build_manifest_and_summary(
        rows=rows,
        features_bytes=features_bytes,
        snapshots=snapshots,
        configuration_digest=configuration_digest,
        source_identity_digest=source_identity_digest,
        git_commit=commit,
        operating_date=parsed_date,
        as_of=parsed_as_of,
    )
    _verify_source_immutability(snapshots)

    if dry_run:
        return ContextFeatureBuildResult(
            rows=rows,
            manifest=manifest,
            summary=summary,
            output_root=None,
            features_path=None,
            manifest_path=None,
            summary_path=None,
            dry_run=True,
        )
    if output_root is None:
        raise ContextFeatureError("output_root is required unless dry_run is true")
    output, parent = _validate_output_root(output_root, source_root)
    features_path, manifest_path, summary_path = _write_artifacts_atomically(
        output_root=output,
        parent=parent,
        features_bytes=features_bytes,
        manifest_bytes=_json_artifact_bytes(manifest),
        summary_bytes=_json_artifact_bytes(summary),
    )
    try:
        _verify_source_immutability(snapshots)
    except ContextFeatureError:
        if output.exists():
            shutil.rmtree(output)
        raise
    return ContextFeatureBuildResult(
        rows=rows,
        manifest=manifest,
        summary=summary,
        output_root=output,
        features_path=features_path,
        manifest_path=manifest_path,
        summary_path=summary_path,
        dry_run=False,
    )


def _current_git_commit() -> str:
    project_root = Path(__file__).resolve().parents[4]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContextFeatureError(f"could not resolve git commit: {exc}") from exc
    return completed.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline point-in-time MLB HR context feature materializer"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser(
        "build-context-features", help="materialize deterministic research rows"
    )
    build.add_argument("--operating-date", required=True)
    build.add_argument("--as-of-utc", required=True)
    build.add_argument("--source-root", required=True)
    build.add_argument("--output-root")
    build.add_argument("--git-commit")
    build.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        result = build_context_features(
            operating_date=args.operating_date,
            as_of_utc=args.as_of_utc,
            source_root=args.source_root,
            output_root=args.output_root,
            git_commit=args.git_commit or _current_git_commit(),
            dry_run=args.dry_run,
        )
    except ContextFeatureError as exc:
        parser.error(str(exc))
    print(json.dumps(dict(result.summary), sort_keys=True, indent=2))
    return 0


__all__ = [
    "BUILD_SUMMARY_FILENAME",
    "BUILD_SUMMARY_SCHEMA_VERSION",
    "ContextFeatureBuildResult",
    "ContextFeatureError",
    "FEATURE_COLUMNS",
    "FEATURE_SCHEMA_VERSION",
    "FEATURES_FILENAME",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "REQUIRED_SOURCE_COLUMNS",
    "ROLLING_WINDOWS_DAYS",
    "SOURCE_FILES",
    "build_context_features",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
