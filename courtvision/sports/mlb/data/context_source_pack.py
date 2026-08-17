"""Immutable source snapshots for the MLB HR point-in-time context store.

This module is deliberately upstream of ``hr_context_features``.  Collectors
persist provider evidence here; the feature materializer reads only an
assembled, immutable local pack and never calls a live provider.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from typing import Final, Iterable, Mapping, Sequence

from courtvision.sports.mlb.data.crosswalk_validation import (
    MLB_TEAM_ABBREVIATIONS,
    validate_mlb_hr_crosswalk_csv,
)
from courtvision.sports.mlb.player_name_normalization import (
    normalize_mlb_player_name,
)
from courtvision.sports.mlb.training import hr_context_features as context_features


SOURCE_PACK_SCHEMA_VERSION: Final = "mlb-hr-context-source-pack-v1"
SOURCE_SNAPSHOT_SCHEMA_VERSION: Final = "mlb-hr-context-source-snapshot-v1"
SOURCE_SNAPSHOT_MANIFEST_FILENAME: Final = "source_snapshot_manifest_v1.json"
SOURCE_PACK_MANIFEST_FILENAME: Final = "source_manifest_v1.json"
SOURCE_COLLECTOR_VERSION: Final = "1.1.0"
CANDIDATE_UNIVERSE_VERSION: Final = "mlb-hr-neutral-candidate-universe-v1"
CANDIDATE_UNIVERSE_GENERATOR: Final = "schedule-roster-enumerator-v1"
CANDIDATE_UNIVERSE_POLICY: Final = (
    "all eligible scheduled MLB hitters visible at cutoff; market-independent"
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[4]
DEFAULT_SOURCE_RESEARCH_ROOT: Final = (
    PROJECT_ROOT / "data" / "research" / "mlb_hr_context_sources"
)

SOURCE_FILES: Final[Mapping[str, str]] = context_features.SOURCE_FILES
SOURCE_NAMES: Final = tuple(SOURCE_FILES)
REQUIRED_SOURCES: Final = frozenset({"candidates", "identity_crosswalk"})
OPTIONAL_SOURCES: Final = frozenset(set(SOURCE_NAMES) - REQUIRED_SOURCES)

SOURCE_SCHEMA_VERSIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "candidates": CANDIDATE_UNIVERSE_VERSION,
        "identity_crosswalk": "mlb-hr-context-identity-v2",
        "statcast": "mlb-hr-context-statcast-pitch-v2",
        "probable_pitchers": "mlb-hr-context-probable-pitchers-v2",
        "lineups": "mlb-hr-context-lineups-v2",
        "weather": "mlb-hr-context-weather-v1",
        "park_factors": "mlb-hr-context-park-factors-v1",
        "market": "mlb-hr-context-market-v1",
    }
)

CANDIDATE_COLUMNS: Final = (
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
    "eligibility_basis",
)

SCHEDULE_COLUMNS: Final = frozenset(
    {
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
    }
)
ROSTER_COLUMNS: Final = frozenset(
    {
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
    }
)
ELIGIBLE_ROSTER_STATUSES: Final = frozenset(
    {"active_roster", "confirmed_lineup", "projected_eligible"}
)
HITTER_ROLES: Final = frozenset(
    {"batter", "catcher", "fielder", "hitter", "infielder", "outfielder"}
)
MARKET_CONTAMINATION_TOKENS: Final = (
    "american_odds",
    "sportsbook",
    "probability_edge",
    "market_rank",
    "selection_threshold",
    "baseline_prediction",
    "implied_probability",
)

STATCAST_OUTPUT_COLUMNS: Final = (
    "game_id",
    "game_date",
    "game_completed_at_utc",
    "completion_evidence_type",
    "completion_witnessed_at_utc",
    "provider_published_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
    "plate_appearance_id",
    "pitch_number",
    "pitch_id",
    "is_terminal_pa",
    "batter_id",
    "pitcher_id",
    "batter_hand",
    "pitcher_hand",
    "home_team",
    "away_team",
    "batter_team",
    "pitcher_team",
    "inning",
    "inning_half",
    "event_type",
    "description",
    "is_home_run",
    "pitch_type",
    "release_speed",
    "launch_speed",
    "launch_angle",
    "is_barrel",
    "estimated_woba",
    "estimated_slg",
    "batted_ball_type",
    "is_pull",
)

PROBABLE_PITCHER_OUTPUT_COLUMNS: Final = (
    "event_id",
    "team",
    "pitcher_id",
    "pitcher_name",
    "normalized_pitcher_name",
    "pitcher_hand",
    "probable_pitcher_status",
    "identity_status",
    "identity_mapping_version",
    "provider_published_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
    "source",
    "source_record_id",
    "source_version",
)

LINEUP_OUTPUT_COLUMNS: Final = (
    "event_id",
    "team",
    "player_id",
    "lineup_status",
    "batting_order_position",
    "provider_published_at_utc",
    "first_observed_at_utc",
    "captured_at_utc",
    "source",
    "source_record_id",
    "expected_pa",
    "expected_pa_source",
    "expected_pa_version",
)

SOURCE_LAYER_REQUIRED_COLUMNS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "probable_pitchers": frozenset(
            {"source", "source_record_id", "source_version"}
        ),
        "lineups": frozenset({"source", "source_record_id"}),
        "weather": frozenset(
            {
                "humidity",
                "temperature_unit",
                "wind_speed_unit",
                "source",
                "source_record_id",
                "source_version",
            }
        ),
        "park_factors": frozenset(
            {"factor_type", "factor_value", "source_record_id"}
        ),
        "market": frozenset(
            {"team", "source_snapshot_id", "source_record_id"}
        ),
    }
)

_MLBAM_ID: Final = re.compile(r"^[1-9]\d{5,9}$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
_SAFE_TOKEN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_TEAM_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {"CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF", "TBR": "TB", "WSN": "WSH", "AZ": "ARI"}
)


class ContextSourceError(ValueError):
    """Raised when source evidence cannot satisfy the immutable pack contract."""


@dataclass(frozen=True, slots=True)
class SourceSnapshotResult:
    source_name: str
    snapshot_id: str
    snapshot_dir: Path
    data_path: Path
    manifest_path: Path
    sha256: str
    row_count: int


@dataclass(frozen=True, slots=True)
class SourcePackResult:
    pack_id: str
    pack_dir: Path
    manifest_path: Path
    source_paths: Mapping[str, Path]


@dataclass(frozen=True, slots=True)
class SourcePackValidationResult:
    pack_dir: Path
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    feature_row_count: int | None = None

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ContextSourceError("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class CandidateUniverseResult:
    rows: tuple[Mapping[str, object], ...]
    source_digest: str
    configuration_digest: str
    candidate_universe_id: str


def _canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    options: dict[str, object] = {
        "ensure_ascii": True,
        "allow_nan": False,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + ("\n" if pretty else "")).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_value(value: object) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


def _utc(value: datetime | str, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ContextSourceError(
                f"{field_name} must be an ISO-8601 timezone-aware timestamp"
            ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextSourceError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _utc_text(value: datetime | str, field_name: str) -> str:
    return _utc(value, field_name).isoformat().replace("+00:00", "Z")


def _optional_utc(value: object, field_name: str) -> datetime | None:
    text = "" if value is None else str(value).strip()
    return _utc(text, field_name) if text else None


def _forward_observation_clocks(
    row: Mapping[str, object], label: str
) -> tuple[datetime | None, datetime, datetime, datetime]:
    provider_published = _optional_utc(
        row.get("provider_published_at_utc"),
        f"{label}.provider_published_at_utc",
    )
    first_observed = _utc(
        row.get("first_observed_at_utc", ""),
        f"{label}.first_observed_at_utc",
    )
    captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
    availability = provider_published or first_observed
    if not (availability <= first_observed <= captured):
        raise ContextSourceError(
            f"{label} clocks must satisfy trustworthy availability <= "
            "first_observed <= captured"
        )
    return provider_published, first_observed, captured, availability


def _date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        raise ContextSourceError(f"{field_name} must be a date")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ContextSourceError(f"{field_name} must be an ISO-8601 date") from exc


def _required_text(row: Mapping[str, object], field_name: str, label: str) -> str:
    text = "" if row.get(field_name) is None else str(row[field_name]).strip()
    if not text:
        raise ContextSourceError(f"{label}.{field_name} is required")
    return text


def _optional_text(row: Mapping[str, object], field_name: str) -> str | None:
    text = "" if row.get(field_name) is None else str(row[field_name]).strip()
    return text or None


def _mlbam_id(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not _MLBAM_ID.fullmatch(text):
        raise ContextSourceError(
            f"{field_name} must be a positive 6-10 digit canonical MLBAM id"
        )
    return text


def _team(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip().upper()
    text = _TEAM_ALIASES.get(text, text)
    if text not in MLB_TEAM_ABBREVIATIONS:
        raise ContextSourceError(f"{field_name} must be a canonical MLB team")
    return text


def _bool_text(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes"}:
        return True
    if text in {"0", "false", "no"}:
        return False
    raise ContextSourceError(f"{field_name} must be boolean")


def _int_text(value: object, field_name: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ContextSourceError(f"{field_name} must be an integer")
    try:
        parsed = int(str(value).strip())
    except ValueError as exc:
        raise ContextSourceError(f"{field_name} must be an integer") from exc
    if minimum is not None and parsed < minimum:
        raise ContextSourceError(f"{field_name} must be at least {minimum}")
    return parsed


def _float_or_blank(value: object, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    try:
        parsed = float(text)
    except ValueError as exc:
        raise ContextSourceError(f"{field_name} must be numeric or empty") from exc
    if not math.isfinite(parsed):
        raise ContextSourceError(f"{field_name} must be finite")
    return text


def _read_csv(path: str | Path, label: str) -> tuple[tuple[str, ...], list[dict[str, str]], bytes]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ContextSourceError(f"{label} CSV does not exist: {source}")
    try:
        raw = source.read_bytes()
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        headers = tuple(reader.fieldnames or ())
        rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ContextSourceError(f"could not read {label} CSV: {exc}") from exc
    if not headers:
        raise ContextSourceError(f"{label} CSV has no header")
    if len(headers) != len(set(headers)):
        raise ContextSourceError(f"{label} CSV has duplicate headers")
    if any(None in row for row in rows):
        raise ContextSourceError(f"{label} CSV contains extra values")
    return headers, rows, raw


def _csv_bytes(columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=tuple(columns),
        extrasaction="raise",
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return output.getvalue().encode("utf-8")


def _safe_name(value: str, field_name: str) -> str:
    if not _SAFE_TOKEN.fullmatch(value):
        raise ContextSourceError(f"{field_name} must be a safe path token")
    return value


def _validate_git_commit(value: str) -> str:
    commit = str(value).strip().casefold()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ContextSourceError("git_commit must be a 40-character hexadecimal SHA")
    return commit


def _research_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    normalized = root.as_posix().casefold()
    if "outputs/research/mlb_hr_prospective_trial" in normalized:
        raise ContextSourceError("source evidence cannot use the prospective-trial namespace")
    return root


def _file_record(path: Path, *, relative_to: Path) -> dict[str, object]:
    raw = path.read_bytes()
    row_count: int | None = None
    if path.suffix.casefold() == ".csv":
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        try:
            next(reader)
        except StopIteration:
            row_count = 0
        else:
            row_count = sum(1 for row in reader if row)
    return {
        "path": path.relative_to(relative_to).as_posix(),
        "sha256": _sha256_bytes(raw),
        "byte_size": len(raw),
        "row_count": row_count,
    }


def source_snapshot_id(
    *,
    source_name: str,
    schema_version: str,
    csv_sha256: str,
    collected_at_utc: datetime | str,
    request_scope: Mapping[str, object],
    collector_configuration: Mapping[str, object],
    git_commit: str,
    raw_input_digests: Mapping[str, str] | None = None,
) -> str:
    """Return the deterministic content/provenance identity for one snapshot."""

    if source_name not in SOURCE_FILES:
        raise ContextSourceError(f"unsupported source_name: {source_name!r}")
    if not _SHA256.fullmatch(str(csv_sha256).casefold()):
        raise ContextSourceError("csv_sha256 must be a 64-character SHA-256")
    payload = {
        "schema_version": SOURCE_SNAPSHOT_SCHEMA_VERSION,
        "source_name": source_name,
        "source_schema_version": schema_version,
        "csv_sha256": str(csv_sha256).casefold(),
        "collected_at_utc": _utc_text(collected_at_utc, "collected_at_utc"),
        "request_scope": dict(request_scope),
        "collector_version": SOURCE_COLLECTOR_VERSION,
        "collector_configuration": dict(collector_configuration),
        "git_commit": _validate_git_commit(git_commit),
        "raw_input_digests": dict(sorted((raw_input_digests or {}).items())),
    }
    # The parent directory already names the source.  Keeping the leaf to the
    # digest alone avoids Windows MAX_PATH failures in deeply nested worktrees.
    return _sha256_value(payload)


def persist_source_snapshot(
    *,
    source_name: str,
    csv_payload: bytes,
    row_count: int,
    operating_date: date | str,
    collected_at_utc: datetime | str,
    request_scope: Mapping[str, object],
    provider: str,
    collector_configuration: Mapping[str, object],
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    source_schema_version: str | None = None,
    source_clock_fields: Sequence[str] = (),
    raw_inputs: Mapping[str, str | Path] | None = None,
    availability_status: str = "available",
    availability_note: str | None = None,
) -> SourceSnapshotResult:
    """Persist one immutable, content-addressed source snapshot.

    Raw input files are copied byte-for-byte and bound in the snapshot manifest.
    Existing snapshot directories are never reused or overwritten.
    """

    if source_name not in SOURCE_FILES:
        raise ContextSourceError(f"unsupported source_name: {source_name!r}")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ContextSourceError("row_count must be a non-negative integer")
    try:
        reader = csv.DictReader(io.StringIO(csv_payload.decode("utf-8-sig"), newline=""))
        headers = tuple(reader.fieldnames or ())
        parsed_rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        raise ContextSourceError(f"source snapshot CSV is unreadable: {exc}") from exc
    if not headers or len(headers) != len(set(headers)):
        raise ContextSourceError("source snapshot CSV requires unique headers")
    if any(None in row for row in parsed_rows):
        raise ContextSourceError("source snapshot CSV contains extra values")
    required_columns = set(context_features.REQUIRED_SOURCE_COLUMNS[source_name])
    required_columns.update(SOURCE_LAYER_REQUIRED_COLUMNS.get(source_name, ()))
    _require_columns(headers, required_columns, f"{source_name} snapshot")
    if len(parsed_rows) != row_count:
        raise ContextSourceError("row_count does not match source snapshot CSV")
    parsed_date = _date(operating_date, "operating_date")
    collected_value = _utc(collected_at_utc, "collected_at_utc")
    collected = _utc_text(collected_value, "collected_at_utc")
    scope_cutoff = request_scope.get("cutoff_utc")
    if scope_cutoff is not None and collected_value > _utc(
        scope_cutoff, "request_scope.cutoff_utc"
    ):
        raise ContextSourceError("snapshot collected_at_utc cannot be after request cutoff")
    status = str(availability_status).strip().casefold()
    if status not in {"available", "partial"}:
        raise ContextSourceError(
            "source snapshot availability_status must be available or partial"
        )
    note = str(availability_note or "").strip() or None
    if status == "partial" and note is None:
        raise ContextSourceError("partial source snapshot requires availability_note")
    commit = _validate_git_commit(git_commit)
    schema_version = source_schema_version or SOURCE_SCHEMA_VERSIONS[source_name]
    bound_configuration = {
        **dict(collector_configuration),
        "availability_status": status,
        "availability_note": note,
    }
    root = _research_root(research_root)
    raw_input_paths: dict[str, Path] = {}
    raw_input_digests: dict[str, str] = {}
    for raw_name, raw_path_value in sorted((raw_inputs or {}).items()):
        safe_name = _safe_name(raw_name, "raw input name")
        raw_path = Path(raw_path_value).expanduser().resolve()
        if not raw_path.is_file():
            raise ContextSourceError(f"raw input does not exist: {raw_path}")
        raw_input_paths[safe_name] = raw_path
        raw_input_digests[safe_name] = _sha256_bytes(raw_path.read_bytes())

    data_sha256 = _sha256_bytes(csv_payload)
    snapshot_id = source_snapshot_id(
        source_name=source_name,
        schema_version=schema_version,
        csv_sha256=data_sha256,
        collected_at_utc=collected,
        request_scope=request_scope,
        collector_configuration=bound_configuration,
        git_commit=commit,
        raw_input_digests=raw_input_digests,
    )
    snapshot_parent = root / parsed_date.isoformat() / "_snapshots" / source_name
    destination = snapshot_parent / snapshot_id
    snapshot_parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"immutable source snapshot already exists: {destination}")

    temporary = Path(tempfile.mkdtemp(prefix=".context-source-", dir=snapshot_parent))
    published = False
    try:
        data_path = temporary / SOURCE_FILES[source_name]
        data_path.write_bytes(csv_payload)
        raw_records: list[dict[str, object]] = []
        if raw_input_paths:
            raw_dir = temporary / "raw"
            raw_dir.mkdir()
            for raw_name, raw_path in raw_input_paths.items():
                extension = raw_path.suffix.casefold()
                raw_destination = raw_dir / f"{raw_name}{extension}"
                with raw_path.open("rb") as source, raw_destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
                record = _file_record(raw_destination, relative_to=temporary)
                record["input_name"] = raw_name
                raw_records.append(record)

        manifest: dict[str, object] = {
            "schema_version": SOURCE_SNAPSHOT_SCHEMA_VERSION,
            "source_name": source_name,
            "source_schema_version": schema_version,
            "snapshot_id": snapshot_id,
            "provider": provider,
            "request_scope": dict(request_scope),
            "collected_at_utc": collected,
            "source_clock_fields": list(source_clock_fields),
            "availability_status": status,
            "availability_note": note,
            "row_count": row_count,
            "sha256": data_sha256,
            "byte_size": len(csv_payload),
            "filename": SOURCE_FILES[source_name],
            "collector_version": SOURCE_COLLECTOR_VERSION,
            "collector_configuration": bound_configuration,
            "git_commit": commit,
            "raw_inputs": raw_records,
            "research_only": True,
            "model_training_enabled": False,
            "predictions_enabled": False,
            "promotion_enabled": False,
            "eligible_for_betting": False,
            "kelly_eligible": False,
        }
        manifest["manifest_digest"] = _sha256_value(manifest)
        manifest_path = temporary / SOURCE_SNAPSHOT_MANIFEST_FILENAME
        manifest_path.write_bytes(_canonical_json_bytes(manifest, pretty=True))
        temporary.replace(destination)
        published = True
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)

    return SourceSnapshotResult(
        source_name=source_name,
        snapshot_id=snapshot_id,
        snapshot_dir=destination,
        data_path=destination / SOURCE_FILES[source_name],
        manifest_path=destination / SOURCE_SNAPSHOT_MANIFEST_FILENAME,
        sha256=data_sha256,
        row_count=row_count,
    )


def _manifest_without_digest(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if key != "manifest_digest"}


def _load_snapshot(snapshot_dir: str | Path) -> tuple[dict[str, object], Path]:
    root = Path(snapshot_dir).expanduser().resolve()
    manifest_path = root / SOURCE_SNAPSHOT_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ContextSourceError(f"source snapshot manifest is missing: {manifest_path}")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContextSourceError(f"could not read source snapshot manifest: {exc}") from exc
    if not isinstance(payload, dict):
        raise ContextSourceError("source snapshot manifest must be a JSON object")
    if payload.get("schema_version") != SOURCE_SNAPSHOT_SCHEMA_VERSION:
        raise ContextSourceError("unsupported source snapshot manifest schema")
    expected_manifest_digest = payload.get("manifest_digest")
    if expected_manifest_digest != _sha256_value(_manifest_without_digest(payload)):
        raise ContextSourceError("source snapshot manifest digest mismatch")
    source_name = str(payload.get("source_name") or "")
    if source_name not in SOURCE_FILES:
        raise ContextSourceError("source snapshot has unsupported source_name")
    if payload.get("source_schema_version") != SOURCE_SCHEMA_VERSIONS[source_name]:
        raise ContextSourceError("source snapshot has unsupported source schema version")
    availability_status = str(payload.get("availability_status") or "").casefold()
    if availability_status not in {"available", "partial"}:
        raise ContextSourceError("source snapshot has invalid availability_status")
    if availability_status == "partial" and not str(
        payload.get("availability_note") or ""
    ).strip():
        raise ContextSourceError("partial source snapshot lacks availability_note")
    snapshot_id = str(payload.get("snapshot_id") or "")
    if root.name != snapshot_id:
        raise ContextSourceError("source snapshot directory does not match snapshot_id")
    data_path = root / SOURCE_FILES[source_name]
    if not data_path.is_file():
        raise ContextSourceError(f"source snapshot data is missing: {data_path}")
    raw = data_path.read_bytes()
    if payload.get("sha256") != _sha256_bytes(raw):
        raise ContextSourceError("source snapshot data digest mismatch")
    if payload.get("byte_size") != len(raw):
        raise ContextSourceError("source snapshot data byte-size mismatch")
    raw_digests: dict[str, str] = {}
    for index, record in enumerate(payload.get("raw_inputs") or []):
        if not isinstance(record, Mapping):
            raise ContextSourceError(f"raw_inputs[{index}] must be an object")
        input_name = str(record.get("input_name") or "")
        _safe_name(input_name, f"raw_inputs[{index}].input_name")
        if input_name in raw_digests:
            raise ContextSourceError(f"duplicate raw input name: {input_name}")
        raw_path = (root / str(record.get("path") or "")).resolve()
        if not raw_path.is_relative_to(root) or not raw_path.is_file():
            raise ContextSourceError(f"raw_inputs[{index}] path is invalid")
        raw_bytes = raw_path.read_bytes()
        if record.get("sha256") != _sha256_bytes(raw_bytes):
            raise ContextSourceError(f"raw_inputs[{index}] digest mismatch")
        raw_digests[input_name] = str(record["sha256"])
        if record.get("byte_size") != len(raw_bytes):
            raise ContextSourceError(f"raw_inputs[{index}] byte-size mismatch")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        try:
            next(reader)
        except StopIteration:
            actual_row_count = 0
        else:
            actual_row_count = sum(1 for row in reader if row)
    except (UnicodeError, csv.Error) as exc:
        raise ContextSourceError(f"source snapshot CSV is unreadable: {exc}") from exc
    if payload.get("row_count") != actual_row_count:
        raise ContextSourceError("source snapshot row-count mismatch")
    scope = payload.get("request_scope")
    configuration = payload.get("collector_configuration")
    if not isinstance(scope, Mapping) or not isinstance(configuration, Mapping):
        raise ContextSourceError("source snapshot scope/configuration must be objects")
    expected_snapshot_id = source_snapshot_id(
        source_name=source_name,
        schema_version=str(payload["source_schema_version"]),
        csv_sha256=str(payload["sha256"]),
        collected_at_utc=str(payload["collected_at_utc"]),
        request_scope=scope,
        collector_configuration=configuration,
        git_commit=str(payload["git_commit"]),
        raw_input_digests=raw_digests,
    )
    if snapshot_id != expected_snapshot_id:
        raise ContextSourceError("source snapshot identity does not match bound evidence")
    return payload, data_path


def _require_columns(
    headers: Sequence[str], required: Iterable[str], label: str
) -> None:
    missing = sorted(set(required) - set(headers))
    if missing:
        raise ContextSourceError(f"{label} is missing required columns: {', '.join(missing)}")


def _candidate_source_digest(
    schedule_raw: bytes, roster_raw: bytes, identity_crosswalk_raw: bytes
) -> str:
    return _sha256_value(
        {
            "identity_crosswalk_sha256": _sha256_bytes(identity_crosswalk_raw),
            "roster_sha256": _sha256_bytes(roster_raw),
            "schedule_sha256": _sha256_bytes(schedule_raw),
        }
    )


def _candidate_configuration() -> dict[str, object]:
    return {
        "candidate_universe_version": CANDIDATE_UNIVERSE_VERSION,
        "candidate_universe_generator": CANDIDATE_UNIVERSE_GENERATOR,
        "candidate_universe_origin": "neutral_market_independent",
        "candidate_universe_policy": CANDIDATE_UNIVERSE_POLICY,
        "eligible_roster_statuses": sorted(ELIGIBLE_ROSTER_STATUSES),
        "hitter_roles": sorted(HITTER_ROLES),
    }


def build_neutral_candidate_universe(
    schedule_csv: str | Path,
    roster_csv: str | Path,
    identity_crosswalk_csv: str | Path,
    *,
    cutoff_utc: datetime | str,
) -> CandidateUniverseResult:
    """Build an all-eligible-hitter universe without accepting market inputs."""

    schedule_headers, schedule_rows, schedule_raw = _read_csv(schedule_csv, "schedule")
    roster_headers, roster_rows, roster_raw = _read_csv(roster_csv, "roster")
    _require_columns(schedule_headers, SCHEDULE_COLUMNS, "schedule CSV")
    _require_columns(roster_headers, ROSTER_COLUMNS, "roster CSV")
    contaminated = sorted(
        header
        for header in (*schedule_headers, *roster_headers)
        if any(token in header.casefold() for token in MARKET_CONTAMINATION_TOKENS)
    )
    if contaminated:
        raise ContextSourceError(
            "neutral candidate inputs contain market/model fields: " + ", ".join(contaminated)
        )
    crosswalk_path = Path(identity_crosswalk_csv).expanduser().resolve()
    crosswalk_validation = validate_mlb_hr_crosswalk_csv(crosswalk_path)
    if not crosswalk_validation.is_valid:
        raise ContextSourceError(
            "identity crosswalk validation failed: " + "; ".join(crosswalk_validation.errors)
        )
    crosswalk_headers, crosswalk_rows, crosswalk_raw = _read_csv(
        crosswalk_path, "identity crosswalk"
    )
    _require_columns(
        crosswalk_headers,
        context_features.REQUIRED_SOURCE_COLUMNS["identity_crosswalk"],
        "identity crosswalk CSV",
    )
    cutoff = _utc(cutoff_utc, "cutoff_utc")

    schedules: dict[str, dict[str, object]] = {}
    schedule_snapshot_ids: set[str] = set()
    schedule_record_ids: set[str] = set()
    for index, row in enumerate(schedule_rows, start=2):
        label = f"schedule row {index}"
        event_id = _mlbam_id(row.get("event_id"), f"{label}.event_id")
        if event_id in schedules:
            raise ContextSourceError(f"duplicate schedule event_id: {event_id}")
        operating_date = _date(row.get("operating_date", ""), f"{label}.operating_date")
        commence = _utc(row.get("commence_time_utc", ""), f"{label}.commence_time_utc")
        available = _utc(
            row.get("source_published_or_available_at_utc", ""),
            f"{label}.source_published_or_available_at_utc",
        )
        captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
        if not (available <= captured <= cutoff < commence):
            raise ContextSourceError(
                f"{label} clocks must satisfy available <= captured <= cutoff < commence"
            )
        home = _team(row.get("home_team"), f"{label}.home_team")
        away = _team(row.get("away_team"), f"{label}.away_team")
        if home == away:
            raise ContextSourceError(f"{label} home_team and away_team must differ")
        source_record_id = _required_text(row, "source_record_id", label)
        if source_record_id in schedule_record_ids:
            raise ContextSourceError(f"duplicate schedule source_record_id: {source_record_id}")
        schedule_record_ids.add(source_record_id)
        schedule_snapshot_ids.add(_required_text(row, "schedule_snapshot_id", label))
        if not _bool_text(
            row.get("schedule_snapshot_complete"),
            f"{label}.schedule_snapshot_complete",
        ):
            raise ContextSourceError(f"{label} schedule snapshot must be complete")
        schedules[event_id] = {
            "event_id": event_id,
            "operating_date": operating_date,
            "commence_time": commence,
            "home_team": home,
            "away_team": away,
            "venue_id": _optional_text(row, "venue_id") or "",
            "venue_name": _required_text(row, "venue_name", label),
            "available": available,
            "captured": captured,
        }
    if len(schedule_snapshot_ids) != 1:
        raise ContextSourceError("schedule rows must share one complete snapshot identity")

    roster_snapshot_ids: set[str] = set()
    roster_record_ids: set[str] = set()
    roster_team_coverage: set[tuple[str, str]] = set()
    for index, row in enumerate(roster_rows, start=2):
        label = f"roster row {index}"
        event_id = _mlbam_id(row.get("event_id"), f"{label}.event_id")
        schedule = schedules.get(event_id)
        if schedule is None:
            raise ContextSourceError(f"{label} has no scheduled event: {event_id}")
        team = _team(row.get("team"), f"{label}.team")
        _mlbam_id(row.get("player_id"), f"{label}.player_id")
        _required_text(row, "player_name", label)
        if team not in {schedule["home_team"], schedule["away_team"]}:
            raise ContextSourceError(f"{label} team does not match schedule")
        available = _utc(
            row.get("source_published_or_available_at_utc", ""),
            f"{label}.source_published_or_available_at_utc",
        )
        captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
        if not (available <= captured <= cutoff):
            raise ContextSourceError(
                f"{label} clocks must satisfy available <= captured <= cutoff"
            )
        source_record_id = _required_text(row, "source_record_id", label)
        if source_record_id in roster_record_ids:
            raise ContextSourceError(f"duplicate roster source_record_id: {source_record_id}")
        roster_record_ids.add(source_record_id)
        roster_snapshot_ids.add(_required_text(row, "roster_snapshot_id", label))
        if not _bool_text(
            row.get("team_roster_complete"), f"{label}.team_roster_complete"
        ):
            raise ContextSourceError(f"{label} team roster snapshot must be complete")
        roster_team_coverage.add((event_id, team))
    if len(roster_snapshot_ids) != 1:
        raise ContextSourceError("roster rows must share one complete snapshot identity")
    expected_team_coverage = {
        (event_id, str(schedule[team_field]))
        for event_id, schedule in schedules.items()
        for team_field in ("home_team", "away_team")
    }
    missing_team_coverage = sorted(expected_team_coverage - roster_team_coverage)
    if missing_team_coverage:
        raise ContextSourceError(
            "roster snapshot lacks complete event/team coverage: "
            + ", ".join(f"{event_id}/{team}" for event_id, team in missing_team_coverage)
        )

    crosswalk_by_key: dict[tuple[str, str], Mapping[str, str]] = {}
    for index, row in enumerate(crosswalk_rows, start=2):
        event_id = _mlbam_id(row.get("mlbam_game_id"), f"crosswalk row {index}.mlbam_game_id")
        player_id = _mlbam_id(
            row.get("mlbam_batter_id"), f"crosswalk row {index}.mlbam_batter_id"
        )
        key = (event_id, player_id)
        if key in crosswalk_by_key:
            raise ContextSourceError(f"duplicate identity crosswalk event/player key: {key}")
        crosswalk_by_key[key] = row

    prepared: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(roster_rows, start=2):
        label = f"roster row {index}"
        role = _required_text(row, "role", label).casefold()
        eligibility = _required_text(row, "eligibility_status", label).casefold()
        if role not in HITTER_ROLES or eligibility not in ELIGIBLE_ROSTER_STATUSES:
            continue
        event_id = _mlbam_id(row.get("event_id"), f"{label}.event_id")
        schedule = schedules.get(event_id)
        if schedule is None:
            raise ContextSourceError(f"{label} has no scheduled event: {event_id}")
        player_id = _mlbam_id(row.get("player_id"), f"{label}.player_id")
        key = (event_id, player_id)
        if key in seen:
            raise ContextSourceError(f"duplicate eligible roster event/player key: {key}")
        seen.add(key)
        team = _team(row.get("team"), f"{label}.team")
        home = str(schedule["home_team"])
        away = str(schedule["away_team"])
        if team not in {home, away}:
            raise ContextSourceError(f"{label} team does not match schedule")
        opponent = away if team == home else home
        available = _utc(
            row.get("source_published_or_available_at_utc", ""),
            f"{label}.source_published_or_available_at_utc",
        )
        captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
        if not (available <= captured <= cutoff):
            raise ContextSourceError(
                f"{label} clocks must satisfy available <= captured <= cutoff"
            )
        crosswalk = crosswalk_by_key.get(key)
        if crosswalk is None:
            raise ContextSourceError(f"{label} has no verified identity crosswalk mapping")
        crosswalk_team = _team(crosswalk.get("batting_team"), "crosswalk.batting_team")
        if crosswalk_team != team:
            raise ContextSourceError(f"{label} schedule/roster identity mismatch")
        player_name = _required_text(row, "player_name", label)
        normalized_name = normalize_mlb_player_name(player_name)
        crosswalk_name = normalize_mlb_player_name(
            _required_text(crosswalk, "batter_name", "identity crosswalk")
        )
        if not normalized_name or normalized_name != crosswalk_name:
            raise ContextSourceError(f"{label} roster/crosswalk player identity mismatch")
        hand = _required_text(row, "batter_hand", label).upper()
        if hand not in {"L", "R", "S"}:
            raise ContextSourceError(f"{label}.batter_hand must be L, R, or S")
        published = max(available, schedule["available"])
        captured_at = max(captured, schedule["captured"])
        verified_at = _utc(crosswalk.get("verified_at", ""), "crosswalk.verified_at")
        if verified_at > cutoff:
            raise ContextSourceError(f"{label} crosswalk was verified after cutoff")
        prepared.append(
            {
                "event_id": event_id,
                "operating_date": schedule["operating_date"].isoformat(),
                "commence_time_utc": _utc_text(schedule["commence_time"], "commence"),
                "home_team": home,
                "away_team": away,
                "venue_id": schedule["venue_id"],
                "venue_name": schedule["venue_name"],
                "team": team,
                "opponent": opponent,
                "player_id": player_id,
                "player_name": player_name,
                "normalized_player_name": normalized_name,
                "batter_hand": hand,
                "identity_status": "verified_mlbam",
                "identity_mapping_version": _required_text(
                    crosswalk, "identity_mapping_version", "identity crosswalk"
                ),
                "candidate_published_or_available_at_utc": _utc_text(published, "published"),
                "candidate_captured_at_utc": _utc_text(captured_at, "captured"),
                "eligibility_basis": eligibility,
            }
        )
    if not prepared:
        raise ContextSourceError("neutral candidate universe contains no eligible hitters")

    source_digest = _candidate_source_digest(schedule_raw, roster_raw, crosswalk_raw)
    configuration = _candidate_configuration()
    configuration_digest = _sha256_value(configuration)
    sorted_rows = sorted(
        prepared,
        key=lambda item: (item["commence_time_utc"], item["event_id"], item["player_id"]),
    )
    universe_id = "neutral-" + _sha256_value(
        {
            "cutoff_utc": _utc_text(cutoff, "cutoff"),
            "source_digest": source_digest,
            "configuration_digest": configuration_digest,
            "candidate_identities": [
                [row["event_id"], row["player_id"]] for row in sorted_rows
            ],
        }
    )
    for row in sorted_rows:
        row.update(
            {
                "candidate_universe_id": universe_id,
                "candidate_universe_version": CANDIDATE_UNIVERSE_VERSION,
                "candidate_universe_generator": CANDIDATE_UNIVERSE_GENERATOR,
                "candidate_universe_origin": "neutral_market_independent",
                "candidate_universe_policy": CANDIDATE_UNIVERSE_POLICY,
                "candidate_universe_source_digest": source_digest,
                "candidate_universe_configuration_digest": configuration_digest,
                "candidate_universe_cutoff_utc": _utc_text(cutoff, "cutoff"),
            }
        )
    return CandidateUniverseResult(
        rows=tuple(sorted_rows),
        source_digest=source_digest,
        configuration_digest=configuration_digest,
        candidate_universe_id=universe_id,
    )


def collect_candidate_snapshot(
    schedule_csv: str | Path,
    roster_csv: str | Path,
    identity_crosswalk_csv: str | Path,
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    collected_at_utc: datetime | str,
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    additional_raw_inputs: Mapping[str, str | Path] | None = None,
) -> SourceSnapshotResult:
    universe = build_neutral_candidate_universe(
        schedule_csv,
        roster_csv,
        identity_crosswalk_csv,
        cutoff_utc=cutoff_utc,
    )
    collected = _utc(collected_at_utc, "collected_at_utc")
    cutoff = _utc(cutoff_utc, "cutoff_utc")
    parsed_operating_date = _date(operating_date, "operating_date")
    if any(
        row["operating_date"] != parsed_operating_date.isoformat()
        for row in universe.rows
    ):
        raise ContextSourceError(
            "candidate universe operating date does not match snapshot request"
        )
    if collected > cutoff:
        raise ContextSourceError(
            "candidate snapshot collected_at_utc cannot be after request cutoff"
        )
    if any(
        collected < _utc(row["candidate_captured_at_utc"], "candidate_captured_at_utc")
        for row in universe.rows
    ):
        raise ContextSourceError(
            "candidate snapshot collected_at_utc cannot precede candidate capture"
        )
    event_identities = sorted(
        {
            (
                str(row["event_id"]),
                str(row["operating_date"]),
                str(row["commence_time_utc"]),
                str(row["home_team"]),
                str(row["away_team"]),
                str(row["venue_id"]),
                str(row["venue_name"]),
            )
            for row in universe.rows
        }
    )
    return persist_source_snapshot(
        source_name="candidates",
        csv_payload=_csv_bytes(CANDIDATE_COLUMNS, universe.rows),
        row_count=len(universe.rows),
        operating_date=operating_date,
        collected_at_utc=collected,
        request_scope={
            "operating_date": parsed_operating_date.isoformat(),
            "cutoff_utc": _utc_text(cutoff, "cutoff_utc"),
            "candidate_universe_id": universe.candidate_universe_id,
            "canonical_event_id_type": "mlb_statsapi_game_pk",
            "event_identities": [
                {
                    "event_id": event_id,
                    "operating_date": event_date,
                    "commence_time_utc": commence,
                    "home_team": home,
                    "away_team": away,
                    "venue_id": venue_id,
                    "venue_name": venue_name,
                }
                for (
                    event_id,
                    event_date,
                    commence,
                    home,
                    away,
                    venue_id,
                    venue_name,
                ) in event_identities
            ],
        },
        provider="MLB schedule and roster evidence supplied to CourtVision adapter",
        collector_configuration={
            "generator": CANDIDATE_UNIVERSE_GENERATOR,
            "policy": CANDIDATE_UNIVERSE_POLICY,
            "configuration_digest": universe.configuration_digest,
        },
        git_commit=git_commit,
        research_root=research_root,
        source_clock_fields=(
            "candidate_published_or_available_at_utc",
            "candidate_captured_at_utc",
            "candidate_universe_cutoff_utc",
        ),
        raw_inputs={
            **dict(additional_raw_inputs or {}),
            "schedule": schedule_csv,
            "roster": roster_csv,
            "identity_crosswalk": identity_crosswalk_csv,
        },
    )


def collect_identity_snapshot(
    identity_crosswalk_csv: str | Path,
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    collected_at_utc: datetime | str,
    mapping_source: str,
    mapping_version: str,
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    additional_raw_inputs: Mapping[str, str | Path] | None = None,
) -> SourceSnapshotResult:
    source = Path(identity_crosswalk_csv).expanduser().resolve()
    validation = validate_mlb_hr_crosswalk_csv(source)
    if not validation.is_valid:
        raise ContextSourceError(
            "identity crosswalk validation failed: " + "; ".join(validation.errors)
        )
    headers, rows, _ = _read_csv(source, "identity crosswalk")
    _require_columns(
        headers,
        context_features.REQUIRED_SOURCE_COLUMNS["identity_crosswalk"],
        "identity crosswalk CSV",
    )
    cutoff = _utc(cutoff_utc, "cutoff_utc")
    collected = _utc(collected_at_utc, "collected_at_utc")
    for index, row in enumerate(rows, start=2):
        verified = _utc(row.get("verified_at", ""), f"identity row {index}.verified_at")
        if verified > cutoff:
            raise ContextSourceError(f"identity row {index} was verified after cutoff")
        if verified > collected:
            raise ContextSourceError(
                f"identity row {index} verification is after snapshot collection"
            )
        if (
            _required_text(row, "identity_mapping_version", f"identity row {index}")
            != mapping_version
        ):
            raise ContextSourceError(f"identity row {index} mapping version mismatch")
    canonical = _csv_bytes(headers, sorted(rows, key=lambda row: (
        row.get("mlbam_game_id", ""), row.get("mlbam_batter_id", "")
    )))
    return persist_source_snapshot(
        source_name="identity_crosswalk",
        csv_payload=canonical,
        row_count=len(rows),
        operating_date=operating_date,
        collected_at_utc=collected_at_utc,
        request_scope={
            "operating_date": _date(operating_date, "operating_date").isoformat(),
            "cutoff_utc": _utc_text(cutoff, "cutoff_utc"),
        },
        provider=mapping_source,
        collector_configuration={"mapping_version": mapping_version},
        git_commit=git_commit,
        research_root=research_root,
        source_clock_fields=("verified_at",),
        raw_inputs={
            **dict(additional_raw_inputs or {}),
            "identity_crosswalk": source,
        },
    )


def _read_json_object(path: str | Path, label: str) -> tuple[Mapping[str, object], bytes]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ContextSourceError(f"{label} does not exist: {source}")
    raw = source.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContextSourceError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ContextSourceError(f"{label} must be a JSON object")
    return payload, raw


def _statsapi_game_feed_context(
    game_feed_json: str | Path,
) -> tuple[
    str,
    Mapping[str, str],
    Mapping[str, Mapping[str, object]],
    Mapping[str, tuple[str, ...]],
]:
    payload, _ = _read_json_object(game_feed_json, "MLB StatsAPI game feed")
    event_id = _mlbam_id(payload.get("gamePk"), "StatsAPI game feed.gamePk")
    game_data = payload.get("gameData")
    live_data = payload.get("liveData")
    if not isinstance(game_data, Mapping) or not isinstance(live_data, Mapping):
        raise ContextSourceError("StatsAPI game feed lacks gameData/liveData objects")
    teams_payload = game_data.get("teams")
    players_payload = game_data.get("players")
    boxscore = live_data.get("boxscore")
    if not isinstance(teams_payload, Mapping) or not isinstance(players_payload, Mapping):
        raise ContextSourceError("StatsAPI game feed lacks team/player identity objects")
    if not isinstance(boxscore, Mapping) or not isinstance(boxscore.get("teams"), Mapping):
        raise ContextSourceError("StatsAPI game feed lacks boxscore team objects")

    teams: dict[str, str] = {}
    batting_orders: dict[str, tuple[str, ...]] = {}
    boxscore_teams = boxscore["teams"]
    assert isinstance(boxscore_teams, Mapping)
    for side in ("away", "home"):
        team = teams_payload.get(side)
        boxscore_team = boxscore_teams.get(side)
        if not isinstance(team, Mapping) or not isinstance(boxscore_team, Mapping):
            raise ContextSourceError(f"StatsAPI game feed lacks {side} team evidence")
        abbreviation = _team(team.get("abbreviation"), f"StatsAPI {side}.abbreviation")
        teams[side] = abbreviation
        order = boxscore_team.get("battingOrder") or []
        if not isinstance(order, list):
            raise ContextSourceError(f"StatsAPI {side}.battingOrder must be an array")
        batting_orders[abbreviation] = tuple(
            _mlbam_id(value, f"StatsAPI {side}.battingOrder") for value in order
        )

    players: dict[str, Mapping[str, object]] = {}
    for key, value in players_payload.items():
        if not isinstance(value, Mapping):
            continue
        player_id = _mlbam_id(value.get("id"), f"StatsAPI player {key}.id")
        players[player_id] = value
    return (
        event_id,
        MappingProxyType(teams),
        MappingProxyType(players),
        MappingProxyType(batting_orders),
    )


def collect_statsapi_probable_pitcher_snapshot(
    observation_csv: str | Path,
    game_feed_json: str | Path,
    identity_crosswalk_csv: str | Path,
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    captured_at_utc: datetime | str,
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    additional_raw_inputs: Mapping[str, str | Path] | None = None,
) -> SourceSnapshotResult:
    """Normalize persisted StatsAPI probable-pitcher observations without fetching."""

    _, observed_rows, _ = _read_csv(observation_csv, "probable pitcher observation")
    if not observed_rows:
        raise ContextSourceError("probable pitcher observation contains no rows")
    event_id, teams_by_side, players, _ = _statsapi_game_feed_context(game_feed_json)
    crosswalk_path = Path(identity_crosswalk_csv).expanduser().resolve()
    validation = validate_mlb_hr_crosswalk_csv(crosswalk_path)
    if not validation.is_valid:
        raise ContextSourceError(
            "identity crosswalk validation failed: " + "; ".join(validation.errors)
        )
    _, crosswalk_rows, _ = _read_csv(crosswalk_path, "identity crosswalk")
    pitcher_roles: dict[str, tuple[str, str, str]] = {}
    for index, row in enumerate(crosswalk_rows, start=2):
        row_event = _mlbam_id(
            row.get("mlbam_game_id"), f"identity row {index}.mlbam_game_id"
        )
        if row_event != event_id:
            continue
        team = _team(row.get("pitcher_team"), f"identity row {index}.pitcher_team")
        role = (
            _mlbam_id(row.get("mlbam_pitcher_id"), f"identity row {index}.mlbam_pitcher_id"),
            normalize_mlb_player_name(
                _required_text(row, "pitcher_name", f"identity row {index}")
            ),
            _required_text(row, "identity_mapping_version", f"identity row {index}"),
        )
        previous = pitcher_roles.setdefault(team, role)
        if previous != role:
            raise ContextSourceError(
                f"conflicting probable-pitcher identity aliases for {event_id}/{team}"
            )

    prepared: list[dict[str, object]] = []
    seen_teams: set[str] = set()
    for index, row in enumerate(observed_rows, start=2):
        label = f"probable pitcher observation row {index}"
        row_event = _mlbam_id(row.get("event_id"), f"{label}.event_id")
        if row_event != event_id:
            raise ContextSourceError(f"{label} event_id conflicts with StatsAPI gamePk")
        team = _team(row.get("team"), f"{label}.team")
        if team not in set(teams_by_side.values()):
            raise ContextSourceError(f"{label} team conflicts with StatsAPI game identity")
        if team in seen_teams:
            raise ContextSourceError(f"duplicate probable pitcher observation for {team}")
        seen_teams.add(team)
        pitcher_id = _mlbam_id(row.get("pitcher_id"), f"{label}.pitcher_id")
        pitcher_name = _required_text(row, "pitcher_name", label)
        feed_player = players.get(pitcher_id)
        if feed_player is None:
            raise ContextSourceError(f"{label} pitcher is absent from persisted StatsAPI feed")
        feed_name = _required_text(feed_player, "fullName", "StatsAPI pitcher")
        if normalize_mlb_player_name(feed_name) != normalize_mlb_player_name(pitcher_name):
            raise ContextSourceError(f"{label} pitcher name conflicts with StatsAPI feed")
        pitch_hand = feed_player.get("pitchHand")
        if not isinstance(pitch_hand, Mapping):
            raise ContextSourceError(f"{label} StatsAPI pitcher hand is unavailable")
        hand = _required_text(pitch_hand, "code", "StatsAPI pitcher hand").upper()
        if hand not in {"L", "R"}:
            raise ContextSourceError(f"{label} StatsAPI pitcher hand must be L or R")
        crosswalk_role = pitcher_roles.get(team)
        if crosswalk_role is None:
            raise ContextSourceError(f"{label} has no verified pitcher identity role")
        if crosswalk_role[:2] != (pitcher_id, normalize_mlb_player_name(pitcher_name)):
            raise ContextSourceError(f"{label} conflicts with verified pitcher identity role")
        captured_text = _required_text(row, "captured_at_utc", label)
        provider_published = (
            _optional_text(row, "provider_published_at_utc")
            or _optional_text(row, "announced_or_published_at_utc")
            or ""
        )
        first_observed = (
            _optional_text(row, "first_observed_at_utc") or captured_text
        )
        status = (
            _optional_text(row, "probable_pitcher_status")
            or _required_text(row, "status", label)
        ).casefold()
        prepared.append(
            {
                "event_id": event_id,
                "team": team,
                "pitcher_id": pitcher_id,
                "pitcher_name": pitcher_name,
                "normalized_pitcher_name": normalize_mlb_player_name(pitcher_name),
                "pitcher_hand": hand,
                "probable_pitcher_status": status,
                "identity_status": "verified_mlbam",
                "identity_mapping_version": crosswalk_role[2],
                "provider_published_at_utc": provider_published,
                "first_observed_at_utc": first_observed,
                "captured_at_utc": captured_text,
                "source": _required_text(row, "source", label),
                "source_record_id": _required_text(row, "source_record_id", label),
                "source_version": _required_text(row, "source_version", label),
            }
        )
    if seen_teams != set(teams_by_side.values()):
        raise ContextSourceError("probable pitcher observations lack both-team coverage")

    cutoff = _utc(cutoff_utc, "cutoff_utc")
    _validate_point_in_time_rows(
        "probable_pitchers", PROBABLE_PITCHER_OUTPUT_COLUMNS, prepared, cutoff
    )
    snapshot_capture = _utc(captured_at_utc, "captured_at_utc")
    if any(
        snapshot_capture < _utc(row["captured_at_utc"], "probable.captured_at_utc")
        for row in prepared
    ):
        raise ContextSourceError("probable pitcher snapshot precedes row capture")
    return persist_source_snapshot(
        source_name="probable_pitchers",
        csv_payload=_csv_bytes(
            PROBABLE_PITCHER_OUTPUT_COLUMNS,
            sorted(prepared, key=lambda row: (row["event_id"], row["team"])),
        ),
        row_count=len(prepared),
        operating_date=operating_date,
        collected_at_utc=snapshot_capture,
        request_scope={
            "operating_date": _date(operating_date, "operating_date").isoformat(),
            "cutoff_utc": _utc_text(cutoff, "cutoff_utc"),
            "event_id": event_id,
        },
        provider="MLB StatsAPI persisted game feed",
        collector_configuration={"provider_adapter": "statsapi-probable-observation-v1"},
        git_commit=git_commit,
        research_root=research_root,
        source_clock_fields=(
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
        ),
        raw_inputs={
            **dict(additional_raw_inputs or {}),
            "probable_observation": observation_csv,
            "statsapi_game_feed": game_feed_json,
            "identity_crosswalk": identity_crosswalk_csv,
        },
    )


def collect_statsapi_lineup_snapshot(
    observation_csv: str | Path,
    game_feed_json: str | Path,
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    captured_at_utc: datetime | str,
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    additional_raw_inputs: Mapping[str, str | Path] | None = None,
) -> SourceSnapshotResult:
    """Normalize persisted StatsAPI lineup observations and retain partial coverage."""

    _, observed_rows, _ = _read_csv(observation_csv, "lineup observation")
    if not observed_rows:
        raise ContextSourceError("lineup observation contains no rows")
    event_id, teams_by_side, _, batting_orders = _statsapi_game_feed_context(
        game_feed_json
    )
    prepared: list[dict[str, object]] = []
    seen_slots: set[tuple[str, int]] = set()
    seen_players: set[tuple[str, str]] = set()
    observed_teams: set[str] = set()
    for index, row in enumerate(observed_rows, start=2):
        label = f"lineup observation row {index}"
        row_event = _mlbam_id(row.get("event_id"), f"{label}.event_id")
        if row_event != event_id:
            raise ContextSourceError(f"{label} event_id conflicts with StatsAPI gamePk")
        team = _team(row.get("team"), f"{label}.team")
        if team not in set(teams_by_side.values()):
            raise ContextSourceError(f"{label} team conflicts with StatsAPI game identity")
        player_id = _mlbam_id(row.get("player_id"), f"{label}.player_id")
        order = _int_text(
            row.get("batting_order_position"),
            f"{label}.batting_order_position",
            minimum=1,
        )
        if order > 9:
            raise ContextSourceError(f"{label}.batting_order_position must be 1 through 9")
        if (team, order) in seen_slots or (team, player_id) in seen_players:
            raise ContextSourceError(f"duplicate lineup identity for {team}/{player_id}")
        seen_slots.add((team, order))
        seen_players.add((team, player_id))
        observed_teams.add(team)
        feed_order = batting_orders.get(team, ())
        if len(feed_order) < order or feed_order[order - 1] != player_id:
            raise ContextSourceError(f"{label} conflicts with persisted StatsAPI batting order")
        status = _required_text(row, "lineup_status", label).casefold()
        if status == "confirmed_current_observation":
            status = "confirmed"
        captured_text = _required_text(row, "captured_at_utc", label)
        provider_published = (
            _optional_text(row, "provider_published_at_utc")
            or _optional_text(row, "announced_or_published_at_utc")
            or ""
        )
        prepared.append(
            {
                "event_id": event_id,
                "team": team,
                "player_id": player_id,
                "lineup_status": status,
                "batting_order_position": order,
                "provider_published_at_utc": provider_published,
                "first_observed_at_utc": (
                    _optional_text(row, "first_observed_at_utc") or captured_text
                ),
                "captured_at_utc": captured_text,
                "source": _required_text(row, "source", label),
                "source_record_id": _required_text(row, "source_record_id", label),
                # MLB StatsAPI batting order evidence does not publish an
                # expected-PA estimate. Keep that optional contract explicit.
                "expected_pa": "",
                "expected_pa_source": "",
                "expected_pa_version": "",
            }
        )

    cutoff = _utc(cutoff_utc, "cutoff_utc")
    _validate_point_in_time_rows("lineups", LINEUP_OUTPUT_COLUMNS, prepared, cutoff)
    snapshot_capture = _utc(captured_at_utc, "captured_at_utc")
    if any(
        snapshot_capture < _utc(row["captured_at_utc"], "lineup.captured_at_utc")
        for row in prepared
    ):
        raise ContextSourceError("lineup snapshot precedes row capture")
    missing_teams = sorted(set(teams_by_side.values()) - observed_teams)
    coverage_note = None
    availability_status = "available"
    if len(seen_slots) < 18:
        availability_status = "partial"
        coverage_note = (
            f"{len(seen_slots)} of 18 starting lineup slots observed; missing teams: "
            + (", ".join(missing_teams) if missing_teams else "partial slots")
        )
    return persist_source_snapshot(
        source_name="lineups",
        csv_payload=_csv_bytes(
            LINEUP_OUTPUT_COLUMNS,
            sorted(prepared, key=lambda row: (row["event_id"], row["team"], row["batting_order_position"])),
        ),
        row_count=len(prepared),
        operating_date=operating_date,
        collected_at_utc=snapshot_capture,
        request_scope={
            "operating_date": _date(operating_date, "operating_date").isoformat(),
            "cutoff_utc": _utc_text(cutoff, "cutoff_utc"),
            "event_id": event_id,
            "observed_starting_slots": len(seen_slots),
            "expected_starting_slots": 18,
        },
        provider="MLB StatsAPI persisted game feed",
        collector_configuration={"provider_adapter": "statsapi-lineup-observation-v1"},
        git_commit=git_commit,
        research_root=research_root,
        source_clock_fields=(
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
        ),
        raw_inputs={
            **dict(additional_raw_inputs or {}),
            "lineup_observation": observation_csv,
            "statsapi_game_feed": game_feed_json,
        },
        availability_status=availability_status,
        availability_note=coverage_note,
    )


def normalize_statcast_pitch_csv(
    statcast_csv: str | Path,
    game_clock_csv: str | Path,
    *,
    captured_at_utc: datetime | str | None = None,
    collected_at_utc: datetime | str | None = None,
) -> tuple[Mapping[str, object], ...]:
    """Normalize an unaggregated Savant export to the v2 pitch-grain contract.

    Savant exports do not establish a trustworthy game-completion/publication
    clock.  The caller must therefore supply a separately captured, immutable
    game-clock CSV rather than allowing this adapter to infer those times.
    """

    headers, raw_rows, _ = _read_csv(statcast_csv, "Statcast")
    required = {
        "game_pk",
        "game_date",
        "at_bat_number",
        "pitch_number",
        "batter",
        "pitcher",
        "stand",
        "p_throws",
        "home_team",
        "away_team",
        "inning",
        "inning_topbot",
        "events",
        "description",
        "pitch_type",
        "release_speed",
        "launch_speed",
        "launch_angle",
        "bb_type",
    }
    _require_columns(headers, required, "Statcast CSV")
    clock_headers, clock_rows, _ = _read_csv(game_clock_csv, "Statcast game clock")
    _require_columns(clock_headers, {"game_id"}, "Statcast game-clock CSV")
    capture_value = captured_at_utc if captured_at_utc is not None else collected_at_utc
    if capture_value is None:
        raise ContextSourceError("captured_at_utc is required")
    snapshot_capture = _utc(capture_value, "captured_at_utc")
    if captured_at_utc is not None and collected_at_utc is not None:
        legacy_capture = _utc(collected_at_utc, "collected_at_utc")
        if legacy_capture != snapshot_capture:
            raise ContextSourceError(
                "captured_at_utc conflicts with deprecated collected_at_utc"
            )
    clocks: dict[
        str,
        tuple[
            datetime | None,
            str,
            datetime | None,
            datetime | None,
            datetime,
            datetime,
        ],
    ] = {}

    for index, row in enumerate(clock_rows, start=2):
        game_id = _mlbam_id(
            row.get("game_id"),
            f"game clock row {index}.game_id",
        )

        completed_value = (
            row.get("game_completed_at_utc")
            or row.get("game_completed_derived_from_last_play_end_utc")
        )

        completed = _optional_utc(
            completed_value,
            f"game clock row {index}.game_completed_at_utc",
        )

        evidence_type = _optional_text(
            row,
            "completion_evidence_type",
        )

        # Backward compatibility for immutable source packs produced
        # before completion-evidence type was made explicit.
        if evidence_type is None:
            if completed is None:
                raise ContextSourceError(
                    f"game clock row {index} lacks completion evidence"
                )
            evidence_type = "legacy_exact_completion_clock"

        allowed_evidence_types = {
            "legacy_exact_completion_clock",
            "play_by_play_last_play_end",
            "schedule_final_observation",
        }

        if evidence_type not in allowed_evidence_types:
            raise ContextSourceError(
                f"game clock row {index}.completion_evidence_type is invalid"
            )

        witnessed = _optional_utc(
            row.get("completion_witnessed_at_utc"),
            f"game clock row {index}.completion_witnessed_at_utc",
        )

        provider_published = _optional_utc(
            row.get("provider_published_at_utc")
            or row.get("savant_publication_or_available_at_utc"),
            f"game clock row {index}.provider_published_at_utc",
        )

        first_observed = _utc(
            row.get("first_observed_at_utc")
            or row.get("savant_collected_at_utc")
            or "",
            f"game clock row {index}.first_observed_at_utc",
        )

        captured = _utc(
            row.get("captured_at_utc")
            or row.get("savant_collected_at_utc")
            or "",
            f"game clock row {index}.captured_at_utc",
        )

        availability = provider_published or first_observed

        if not (
            availability
            <= first_observed
            <= captured
            <= snapshot_capture
        ):
            raise ContextSourceError(
                f"game clock row {index} must satisfy trustworthy "
                "availability <= first_observed <= captured <= snapshot capture"
            )

        if evidence_type == "schedule_final_observation":
            if completed is not None:
                raise ContextSourceError(
                    f"game clock row {index} schedule-final evidence "
                    "must not claim an exact completion time"
                )
            if witnessed is None:
                raise ContextSourceError(
                    f"game clock row {index} schedule-final evidence "
                    "requires completion_witnessed_at_utc"
                )
            if witnessed > snapshot_capture:
                raise ContextSourceError(
                    f"game clock row {index} completion witness is after snapshot capture"
                )
        else:
            if completed is None:
                raise ContextSourceError(
                    f"game clock row {index} exact-completion evidence "
                    "requires game_completed_at_utc"
                )
            if completed > availability:
                raise ContextSourceError(
                    f"game clock row {index} exact completion occurs after "
                    "trustworthy Statcast availability"
                )
            if witnessed is not None:
                if witnessed < completed:
                    raise ContextSourceError(
                        f"game clock row {index} completion witness precedes "
                        "the exact completion time"
                    )
                if witnessed > snapshot_capture:
                    raise ContextSourceError(
                        f"game clock row {index} completion witness is after "
                        "snapshot capture"
                    )

        provider_final_status = _optional_text(
            row,
            "provider_final_status",
        )

        if (
            provider_final_status is not None
            and provider_final_status.casefold()
            not in {
                "final",
                "game over",
                "completed early",
            }
        ):
            raise ContextSourceError(
                f"game clock row {index} does not contain final-game "
                "completion evidence"
            )

        clock = (
            completed,
            evidence_type,
            witnessed,
            provider_published,
            first_observed,
            captured,
        )

        previous = clocks.setdefault(game_id, clock)

        if previous != clock:
            raise ContextSourceError(
                f"conflicting game-clock evidence for {game_id}"
            )

    normalized: list[dict[str, object]] = []
    pitch_keys: set[tuple[str, str, int]] = set()
    pa_identity: dict[tuple[str, str], tuple[object, ...]] = {}
    for index, row in enumerate(raw_rows, start=2):
        label = f"Statcast row {index}"
        game_id = _mlbam_id(row.get("game_pk"), f"{label}.game_pk")
        if game_id not in clocks:
            raise ContextSourceError(f"{label} has no verified game-completion clock")
        game_date = _date(row.get("game_date", ""), f"{label}.game_date")
        at_bat = _int_text(row.get("at_bat_number"), f"{label}.at_bat_number", minimum=1)
        pitch_number = _int_text(row.get("pitch_number"), f"{label}.pitch_number", minimum=1)
        pa_id = f"{game_id}:{at_bat}"
        pitch_key = (game_id, pa_id, pitch_number)
        if pitch_key in pitch_keys:
            raise ContextSourceError(f"duplicate Statcast pitch identity: {pitch_key}")
        pitch_keys.add(pitch_key)
        batter_id = _mlbam_id(row.get("batter"), f"{label}.batter")
        pitcher_id = _mlbam_id(row.get("pitcher"), f"{label}.pitcher")
        batter_hand = _required_text(row, "stand", label).upper()
        pitcher_hand = _required_text(row, "p_throws", label).upper()
        if batter_hand not in {"L", "R", "S"}:
            raise ContextSourceError(f"{label}.stand must be L, R, or S")
        if pitcher_hand not in {"L", "R"}:
            raise ContextSourceError(f"{label}.p_throws must be L or R")
        home = _team(row.get("home_team"), f"{label}.home_team")
        away = _team(row.get("away_team"), f"{label}.away_team")
        half = _required_text(row, "inning_topbot", label).casefold()
        if half in {"top", "topbot_top"}:
            batter_team, pitcher_team, normalized_half = away, home, "top"
        elif half in {"bot", "bottom", "topbot_bot"}:
            batter_team, pitcher_team, normalized_half = home, away, "bottom"
        else:
            raise ContextSourceError(f"{label}.inning_topbot must be Top or Bot")
        (
            completed,
            completion_evidence_type,
            completion_witnessed_at,
            provider_published,
            first_observed,
            captured,
        ) = clocks[game_id]
        completion_date_reference = (
            completed
            if completed is not None
            else completion_witnessed_at
        )
        if completion_date_reference is None:
            raise ContextSourceError(
                f"{label} has no usable game-completion evidence timestamp"
            )
        if completion_date_reference.date() < game_date:
            raise ContextSourceError(
                f"{label} completion clock cannot precede game_date"
            )
        event = (_optional_text(row, "events") or "").strip().casefold()
        terminal = bool(event)
        signature = (
                        game_date,
                        home,
                        away,
                        batter_team,
                        pitcher_team,
                    )
        previous = pa_identity.setdefault((game_id, pa_id), signature)
        if previous != signature:
            raise ContextSourceError(f"inconsistent Statcast PA identity: {(game_id, pa_id)}")
        raw_pitch_id = _optional_text(row, "sv_id") or _optional_text(row, "pitch_id")
        pitch_id = raw_pitch_id or f"{game_id}:{at_bat}:{pitch_number}"
        normalized.append(
            {
                "game_id": game_id,
                "game_date": game_date.isoformat(),
                "game_completed_at_utc": (
                    _utc_text(completed, "completed") if completed else ""
                ),
                "completion_evidence_type": completion_evidence_type,
                "completion_witnessed_at_utc": (
                    _utc_text(
                        completion_witnessed_at,
                        "completion_witnessed_at",
                    )
                    if completion_witnessed_at
                    else ""
                ),
                "provider_published_at_utc": (
                    _utc_text(provider_published, "provider_published")
                    if provider_published
                    else ""
                ),
                "first_observed_at_utc": _utc_text(
                    first_observed, "first_observed"
                ),
                "captured_at_utc": _utc_text(captured, "captured"),
                "plate_appearance_id": pa_id,
                "pitch_number": pitch_number,
                "pitch_id": pitch_id,
                "is_terminal_pa": str(terminal).lower(),
                "batter_id": batter_id,
                "pitcher_id": pitcher_id,
                "batter_hand": batter_hand,
                "pitcher_hand": pitcher_hand,
                "home_team": home,
                "away_team": away,
                "batter_team": batter_team,
                "pitcher_team": pitcher_team,
                "inning": _int_text(row.get("inning"), f"{label}.inning", minimum=1),
                "inning_half": normalized_half,
                "event_type": event,
                "description": _optional_text(row, "description") or "",
                "is_home_run": str(event == "home_run").lower(),
                "pitch_type": _optional_text(row, "pitch_type") or "",
                "release_speed": _float_or_blank(
                    row.get("release_speed"), f"{label}.release_speed"
                ),
                "launch_speed": _float_or_blank(row.get("launch_speed"), f"{label}.launch_speed"),
                "launch_angle": _float_or_blank(row.get("launch_angle"), f"{label}.launch_angle"),
                "is_barrel": (
                    ""
                    if _optional_text(row, "barrel") is None
                    else str(_bool_text(row.get("barrel"), f"{label}.barrel")).lower()
                ),
                "estimated_woba": _float_or_blank(
                    row.get("estimated_woba_using_speedangle"),
                    f"{label}.estimated_woba_using_speedangle",
                ),
                "estimated_slg": _float_or_blank(
                    row.get("estimated_slg_using_speedangle"),
                    f"{label}.estimated_slg_using_speedangle",
                ),
                "batted_ball_type": _optional_text(row, "bb_type") or "",
                # Spray direction is intentionally not inferred from provider geometry.
                "is_pull": "",
            }
        )

    by_pa: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for row in normalized:
        by_pa.setdefault((str(row["game_id"]), str(row["plate_appearance_id"])), []).append(row)
    for key, pitches in by_pa.items():
        terminal_rows = [row for row in pitches if row["is_terminal_pa"] == "true"]
        if len(terminal_rows) > 1:
            raise ContextSourceError(f"Statcast PA must have at most one terminal row: {key}")
        if terminal_rows:
            final_pitch = max(int(row["pitch_number"]) for row in pitches)
            if int(terminal_rows[0]["pitch_number"]) != final_pitch:
                raise ContextSourceError(f"terminal Statcast row is not the final pitch: {key}")
    return tuple(
        sorted(
            normalized,
            key=lambda row: (
                row["game_date"], row["game_id"], row["plate_appearance_id"], row["pitch_number"]
            ),
        )
    )


def collect_statcast_snapshot(
    statcast_csv: str | Path,
    game_clock_csv: str | Path,
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    captured_at_utc: datetime | str | None = None,
    collected_at_utc: datetime | str | None = None,
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    additional_raw_inputs: Mapping[str, str | Path] | None = None,
) -> SourceSnapshotResult:
    capture_value = captured_at_utc if captured_at_utc is not None else collected_at_utc
    if capture_value is None:
        raise ContextSourceError("captured_at_utc is required")
    rows = normalize_statcast_pitch_csv(
        statcast_csv,
        game_clock_csv,
        captured_at_utc=capture_value,
        collected_at_utc=collected_at_utc if captured_at_utc is not None else None,
    )
    cutoff = _utc(cutoff_utc, "cutoff_utc")
    if any(
        _utc(row["captured_at_utc"], "statcast.captured_at_utc") > cutoff
        for row in rows
    ):
        raise ContextSourceError("Statcast snapshot contains evidence collected after cutoff")
    return persist_source_snapshot(
        source_name="statcast",
        csv_payload=_csv_bytes(STATCAST_OUTPUT_COLUMNS, rows),
        row_count=len(rows),
        operating_date=operating_date,
        collected_at_utc=capture_value,
        request_scope={
            "operating_date": _date(operating_date, "operating_date").isoformat(),
            "cutoff_utc": _utc_text(cutoff, "cutoff_utc"),
            "grain": "one_row_per_pitch",
        },
        provider="Baseball Savant / Statcast through CourtVision adapter",
        collector_configuration={
            "provider_adapter": "courtvision-statcast-pitch-v1",
            "derived_aggregates": False,
            "spray_direction_inferred": False,
        },
        git_commit=git_commit,
        research_root=research_root,
        source_clock_fields=(
            "game_completed_at_utc",
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
        ),
        raw_inputs={
            **dict(additional_raw_inputs or {}),
            "statcast": statcast_csv,
            "game_clocks": game_clock_csv,
        },
    )


def _validate_point_in_time_rows(
    source_name: str,
    headers: Sequence[str],
    rows: Sequence[Mapping[str, str]],
    cutoff: datetime,
) -> None:
    _require_columns(
        headers,
        set(context_features.REQUIRED_SOURCE_COLUMNS[source_name])
        | set(SOURCE_LAYER_REQUIRED_COLUMNS[source_name]),
        f"{source_name} CSV",
    )
    source_record_ids: set[str] = set()
    for index, row in enumerate(rows, start=2):
        label = f"{source_name} row {index}"
        source_record_id = _required_text(row, "source_record_id", label)
        if source_record_id in source_record_ids:
            raise ContextSourceError(
                f"duplicate {source_name} source_record_id: {source_record_id}"
            )
        source_record_ids.add(source_record_id)
        if source_name == "probable_pitchers":
            _required_text(row, "source", label)
            _required_text(row, "source_version", label)
            _, _, captured, available = _forward_observation_clocks(row, label)
            _mlbam_id(row.get("event_id"), f"{label}.event_id")
            status = _required_text(row, "probable_pitcher_status", label).casefold()
            if status not in context_features.PROBABLE_PITCHER_STATUSES:
                raise ContextSourceError(
                    f"{label}.probable_pitcher_status is unsupported"
                )
            if status != "unknown":
                _mlbam_id(row.get("pitcher_id"), f"{label}.pitcher_id")
        elif source_name == "lineups":
            _required_text(row, "source", label)
            _, _, captured, available = _forward_observation_clocks(row, label)
            _mlbam_id(row.get("event_id"), f"{label}.event_id")
            _mlbam_id(row.get("player_id"), f"{label}.player_id")
            status = _required_text(row, "lineup_status", label).casefold()
            if status not in context_features.LINEUP_STATUSES:
                raise ContextSourceError(f"{label}.lineup_status is unsupported")
            order = _optional_text(row, "batting_order_position")
            if order is not None and not 1 <= _int_text(
                order, f"{label}.batting_order_position"
            ) <= 9:
                raise ContextSourceError(f"{label}.batting_order_position must be 1 through 9")
            if status in {"confirmed", "projected"} and order is None:
                raise ContextSourceError(
                    f"{label} confirmed/projected lineup requires batting_order_position"
                )
            if status in {"unknown", "not_starting"} and order is not None:
                raise ContextSourceError(
                    f"{label} unknown/not-starting lineup cannot carry batting_order_position"
                )
            expected_pa_text = _float_or_blank(
                row.get("expected_pa"), f"{label}.expected_pa"
            )
            expected_pa_source = _optional_text(row, "expected_pa_source")
            expected_pa_version = _optional_text(row, "expected_pa_version")
            if expected_pa_text:
                expected_pa = float(expected_pa_text)
                if not 0.0 < expected_pa <= 10.0:
                    raise ContextSourceError(
                        f"{label}.expected_pa must be greater than 0 and at most 10"
                    )
                if status not in {"confirmed", "projected"} or order is None:
                    raise ContextSourceError(
                        f"{label}.expected_pa requires an admissible batting-order row"
                    )
                if expected_pa_source is None or expected_pa_version is None:
                    raise ContextSourceError(
                        f"{label}.expected_pa requires source and version"
                    )
            elif expected_pa_source is not None or expected_pa_version is not None:
                raise ContextSourceError(
                    f"{label}.expected_pa source/version requires a value"
                )
        elif source_name == "weather":
            _required_text(row, "source", label)
            _required_text(row, "source_version", label)
            captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
            weather_type = _required_text(row, "weather_type", label).casefold()
            evidence_class = _required_text(row, "weather_evidence_class", label).casefold()
            if evidence_class in {"final", "final_game_weather", "postgame"}:
                raise ContextSourceError(f"{label} final observed weather is prohibited")
            if weather_type == "forecast":
                if evidence_class != "provider_pregame_forecast":
                    raise ContextSourceError(f"{label} is not genuine pregame forecast evidence")
                available = _utc(row.get("issued_at_utc", ""), f"{label}.issued_at_utc")
                valid_for = _utc(
                    row.get("valid_for_utc", ""), f"{label}.valid_for_utc"
                )
                if available > valid_for:
                    raise ContextSourceError(
                        f"{label} forecast issuance is after valid_for_utc"
                    )
                if _optional_text(row, "measured_at_utc") is not None:
                    raise ContextSourceError(f"{label} forecast cannot include measured_at_utc")
            elif weather_type == "pregame_observation":
                if evidence_class != "provider_pregame_observation":
                    raise ContextSourceError(
                        f"{label} is not genuine pregame observation evidence"
                    )
                available = _utc(
                    row.get("measured_at_utc", ""), f"{label}.measured_at_utc"
                )
            else:
                raise ContextSourceError(f"{label}.weather_type is unsupported")
            temperature_text = _float_or_blank(
                row.get("temperature"), f"{label}.temperature"
            )
            temperature_unit = _optional_text(row, "temperature_unit")
            wind_speed_text = _float_or_blank(
                row.get("wind_speed"), f"{label}.wind_speed"
            )
            if wind_speed_text and float(wind_speed_text) < 0:
                raise ContextSourceError(f"{label}.wind_speed cannot be negative")
            wind_speed_unit = _optional_text(row, "wind_speed_unit")
            if bool(temperature_text) != (temperature_unit is not None):
                raise ContextSourceError(
                    f"{label}.temperature and temperature_unit must be supplied together"
                )
            if bool(wind_speed_text) != (wind_speed_unit is not None):
                raise ContextSourceError(
                    f"{label}.wind_speed and wind_speed_unit must be supplied together"
                )
            humidity_text = _float_or_blank(row.get("humidity"), f"{label}.humidity")
            if humidity_text and not 0.0 <= float(humidity_text) <= 100.0:
                raise ContextSourceError(
                    f"{label}.humidity must be between 0 and 100"
                )
        elif source_name == "park_factors":
            available = _utc(
                row.get("published_or_available_at_utc", ""),
                f"{label}.published_or_available_at_utc",
            )
            captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
            effective_from = _date(
                row.get("effective_from_date", ""),
                f"{label}.effective_from_date",
            )
            effective_to_text = _optional_text(row, "effective_to_date")
            if effective_to_text and _date(
                effective_to_text, f"{label}.effective_to_date"
            ) < effective_from:
                raise ContextSourceError(f"{label} has invalid effective interval")
            _required_text(row, "venue_id", label)
            factor_type = _required_text(row, "factor_type", label).casefold()
            if factor_type not in {"home_run", "hr"}:
                raise ContextSourceError(f"{label}.factor_type must be home_run")
            try:
                factor_value = Decimal(_required_text(row, "factor_value", label))
                feature_value = Decimal(_required_text(row, "park_hr_factor", label))
            except InvalidOperation as exc:
                raise ContextSourceError(
                    f"{label} factor values must be finite decimals"
                ) from exc
            if not factor_value.is_finite() or not feature_value.is_finite():
                raise ContextSourceError(f"{label} factor values must be finite decimals")
            if factor_value != feature_value:
                raise ContextSourceError(
                    f"{label}.factor_value must equal park_hr_factor"
                )
        elif source_name == "market":
            available = _utc(row.get("quote_at_utc", ""), f"{label}.quote_at_utc")
            captured = _utc(row.get("captured_at_utc", ""), f"{label}.captured_at_utc")
            _mlbam_id(row.get("event_id"), f"{label}.event_id")
            _mlbam_id(row.get("player_id"), f"{label}.player_id")
            _team(row.get("team"), f"{label}.team")
            _required_text(row, "source_snapshot_id", label)
            if _required_text(row, "evidence_class", label).casefold() != "pregame_snapshot":
                raise ContextSourceError(f"{label} closing/non-pregame evidence is prohibited")
            odds = _int_text(row.get("american_odds"), f"{label}.american_odds")
            if odds == 0:
                raise ContextSourceError(f"{label}.american_odds cannot be zero")
        else:
            raise ContextSourceError(f"unsupported normalized source: {source_name}")
        if not (available <= captured <= cutoff):
            raise ContextSourceError(
                f"{label} clocks must satisfy source time <= captured <= cutoff"
            )


def collect_normalized_source_snapshot(
    source_name: str,
    input_csv: str | Path,
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    collected_at_utc: datetime | str,
    provider: str,
    collector_configuration: Mapping[str, object],
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    availability_status: str = "available",
    availability_note: str | None = None,
    additional_raw_inputs: Mapping[str, str | Path] | None = None,
) -> SourceSnapshotResult:
    """Persist one supplied point-in-time provider adapter output.

    Weather intentionally has no live implementation here: a real forecast
    adapter must first emit this documented contract.  Market input must
    already contain verified event and MLBAM player linkage; names are never
    used by this function as join keys.
    """

    if source_name not in {
        "probable_pitchers",
        "lineups",
        "weather",
        "park_factors",
        "market",
    }:
        raise ContextSourceError(f"source does not use normalized adapter boundary: {source_name}")
    headers, rows, _ = _read_csv(input_csv, source_name)
    cutoff = _utc(cutoff_utc, "cutoff_utc")
    _validate_point_in_time_rows(source_name, headers, rows, cutoff)
    collected = _utc(collected_at_utc, "collected_at_utc")
    captured_field = "captured_at_utc"
    if any(
        collected < _utc(row.get(captured_field, ""), f"{source_name}.{captured_field}")
        for row in rows
    ):
        raise ContextSourceError(
            f"{source_name} snapshot collection cannot precede row capture"
        )
    sorted_rows = sorted(rows, key=lambda row: _canonical_json_bytes(dict(row)))
    canonical = _csv_bytes(headers, sorted_rows)
    clock_fields = {
        "probable_pitchers": (
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
        ),
        "lineups": (
            "provider_published_at_utc",
            "first_observed_at_utc",
            "captured_at_utc",
        ),
        "weather": ("issued_at_utc", "measured_at_utc", "valid_for_utc", "captured_at_utc"),
        "park_factors": ("published_or_available_at_utc", "captured_at_utc"),
        "market": ("quote_at_utc", "captured_at_utc"),
    }[source_name]
    return persist_source_snapshot(
        source_name=source_name,
        csv_payload=canonical,
        row_count=len(rows),
        operating_date=operating_date,
        collected_at_utc=collected_at_utc,
        request_scope={
            "operating_date": _date(operating_date, "operating_date").isoformat(),
            "cutoff_utc": _utc_text(cutoff, "cutoff_utc"),
        },
        provider=provider,
        collector_configuration=collector_configuration,
        git_commit=git_commit,
        research_root=research_root,
        source_clock_fields=clock_fields,
        raw_inputs={**dict(additional_raw_inputs or {}), source_name: input_csv},
        availability_status=availability_status,
        availability_note=availability_note,
    )


def _source_capture_fields(source_name: str) -> tuple[tuple[str, str], ...]:
    if source_name == "candidates":
        return (("candidate_published_or_available_at_utc", "candidate_captured_at_utc"),)
    if source_name in {"probable_pitchers", "lineups"}:
        return ()
    if source_name == "statcast":
        return ()
    if source_name == "weather":
        return ()
    if source_name == "park_factors":
        return (("published_or_available_at_utc", "captured_at_utc"),)
    if source_name == "market":
        return (("quote_at_utc", "captured_at_utc"),)
    return ()


def _validate_pack_source_clocks(
    source_name: str,
    source_path: Path,
    *,
    cutoff: datetime,
    errors: list[str],
) -> None:
    try:
        headers, rows, _ = _read_csv(source_path, source_name)
        _require_columns(
            headers,
            context_features.REQUIRED_SOURCE_COLUMNS[source_name],
            f"{source_name} CSV",
        )
    except ContextSourceError as exc:
        errors.append(str(exc))
        return
    if source_name == "identity_crosswalk":
        validation = validate_mlb_hr_crosswalk_csv(source_path)
        errors.extend(validation.errors)
        for index, row in enumerate(rows, start=2):
            try:
                verified = _utc(row.get("verified_at", ""), f"identity row {index}.verified_at")
            except ContextSourceError as exc:
                errors.append(str(exc))
            else:
                if verified > cutoff:
                    errors.append(f"identity row {index} was verified after pack cutoff")
        return
    if source_name in SOURCE_LAYER_REQUIRED_COLUMNS:
        try:
            _validate_point_in_time_rows(source_name, headers, rows, cutoff)
        except ContextSourceError as exc:
            errors.append(str(exc))
        return
    if source_name == "statcast":
        for index, row in enumerate(rows, start=2):
            label = f"statcast row {index}"

            try:
                completed = _optional_utc(
                    row.get("game_completed_at_utc"),
                    f"{label}.game_completed_at_utc",
                )

                evidence_type = _optional_text(
                    row,
                    "completion_evidence_type",
                )

                if evidence_type is None:
                    if completed is None:
                        raise ContextSourceError(
                            f"{label} lacks completion evidence"
                        )
                    evidence_type = "legacy_exact_completion_clock"

                witnessed = _optional_utc(
                    row.get("completion_witnessed_at_utc"),
                    f"{label}.completion_witnessed_at_utc",
                )

                _, first_observed, captured, availability = (
                    _forward_observation_clocks(row, label)
                )

                if not (
                    availability
                    <= first_observed
                    <= captured
                    <= cutoff
                ):
                    raise ContextSourceError(
                        f"{label} clocks must satisfy trustworthy "
                        "availability <= first_observed <= captured <= pack cutoff"
                    )

                if evidence_type == "schedule_final_observation":
                    if completed is not None:
                        raise ContextSourceError(
                            f"{label} schedule-final evidence must not claim "
                            "an exact completion time"
                        )
                    if witnessed is None:
                        raise ContextSourceError(
                            f"{label} schedule-final evidence requires "
                            "completion_witnessed_at_utc"
                        )
                    if witnessed > cutoff:
                        raise ContextSourceError(
                            f"{label} completion witness is after pack cutoff"
                        )

                elif evidence_type in {
                    "legacy_exact_completion_clock",
                    "play_by_play_last_play_end",
                }:
                    if completed is None:
                        raise ContextSourceError(
                            f"{label} exact-completion evidence requires "
                            "game_completed_at_utc"
                        )
                    if completed > availability:
                        raise ContextSourceError(
                            f"{label} exact completion occurs after trustworthy "
                            "Statcast availability"
                        )
                    if witnessed is not None:
                        if witnessed < completed:
                            raise ContextSourceError(
                                f"{label} completion witness precedes exact completion"
                            )
                        if witnessed > cutoff:
                            raise ContextSourceError(
                                f"{label} completion witness is after pack cutoff"
                            )

                else:
                    raise ContextSourceError(
                        f"{label}.completion_evidence_type is invalid"
                    )

            except ContextSourceError as exc:
                errors.append(str(exc))
                continue

        return
    for index, row in enumerate(rows, start=2):
        for available_field, captured_field in _source_capture_fields(source_name):
            try:
                available = _utc(
                    row.get(available_field, ""),
                    f"{source_name} row {index}.{available_field}",
                )
                captured = _utc(
                    row.get(captured_field, ""),
                    f"{source_name} row {index}.{captured_field}",
                )
            except ContextSourceError as exc:
                errors.append(str(exc))
                continue
            if not (available <= captured <= cutoff):
                errors.append(
                    f"{source_name} row {index} clocks must satisfy source time "
                    "<= captured <= pack cutoff"
                )
        if source_name == "market":
            evidence_class = str(row.get("evidence_class") or "").strip().casefold()
            if evidence_class != "pregame_snapshot":
                errors.append(
                    f"market row {index} contains prohibited closing/non-pregame evidence"
                )
        if source_name == "candidates":
            try:
                candidate_cutoff = _utc(
                    row.get("candidate_universe_cutoff_utc", ""),
                    f"candidates row {index}.candidate_universe_cutoff_utc",
                )
            except ContextSourceError as exc:
                errors.append(str(exc))
            else:
                if candidate_cutoff != cutoff:
                    errors.append(f"candidates row {index} cutoff differs from pack cutoff")


def _validate_candidate_manifest_binding(
    entry: Mapping[str, object],
    raw: bytes,
    *,
    cutoff: datetime,
    errors: list[str],
) -> None:
    raw_inputs = entry.get("raw_input_digests")
    if not isinstance(raw_inputs, list):
        errors.append("candidate snapshot raw_input_digests must be a list")
        return
    digests: dict[str, str] = {}
    for index, record in enumerate(raw_inputs):
        if not isinstance(record, Mapping):
            errors.append(f"candidate raw_input_digests[{index}] must be an object")
            continue
        name = str(record.get("input_name") or "")
        digest = str(record.get("sha256") or "").casefold()
        if name in digests:
            errors.append(f"duplicate candidate raw input digest: {name}")
        elif not _SHA256.fullmatch(digest):
            errors.append(f"candidate raw input {name!r} has invalid SHA-256")
        else:
            digests[name] = digest
    required_inputs = {"schedule", "roster", "identity_crosswalk"}
    if not required_inputs.issubset(digests):
        errors.append(
            "candidate raw input digests must bind schedule, roster, and identity_crosswalk"
        )
        return
    expected_source_digest = _sha256_value(
        {
            "identity_crosswalk_sha256": digests["identity_crosswalk"],
            "roster_sha256": digests["roster"],
            "schedule_sha256": digests["schedule"],
        }
    )
    expected_configuration_digest = _sha256_value(_candidate_configuration())
    configuration = entry.get("collector_configuration")
    if not isinstance(configuration, Mapping):
        errors.append("candidate collector_configuration must be an object")
    else:
        if configuration.get("generator") != CANDIDATE_UNIVERSE_GENERATOR:
            errors.append("candidate generator version mismatch")
        if configuration.get("policy") != CANDIDATE_UNIVERSE_POLICY:
            errors.append("candidate universe policy mismatch")
        if configuration.get("configuration_digest") != expected_configuration_digest:
            errors.append("candidate collector configuration digest mismatch")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
        rows = list(reader)
    except (UnicodeError, csv.Error) as exc:
        errors.append(f"candidate CSV is unreadable: {exc}")
        return
    identities = sorted(
        [[str(row.get("event_id") or ""), str(row.get("player_id") or "")] for row in rows]
    )
    expected_universe_id = "neutral-" + _sha256_value(
        {
            "cutoff_utc": _utc_text(cutoff, "cutoff"),
            "source_digest": expected_source_digest,
            "configuration_digest": expected_configuration_digest,
            "candidate_identities": identities,
        }
    )
    event_identities = sorted(
        {
            (
                str(row.get("event_id") or ""),
                str(row.get("operating_date") or ""),
                str(row.get("commence_time_utc") or ""),
                str(row.get("home_team") or ""),
                str(row.get("away_team") or ""),
                str(row.get("venue_id") or ""),
                str(row.get("venue_name") or ""),
            )
            for row in rows
        }
    )
    expected_event_identities = [
        {
            "event_id": event_id,
            "operating_date": event_date,
            "commence_time_utc": commence,
            "home_team": home,
            "away_team": away,
            "venue_id": venue_id,
            "venue_name": venue_name,
        }
        for (
            event_id,
            event_date,
            commence,
            home,
            away,
            venue_id,
            venue_name,
        ) in event_identities
    ]
    request_scope = entry.get("request_scope")
    if not isinstance(request_scope, Mapping):
        errors.append("candidate request_scope must be an object")
    else:
        if request_scope.get("canonical_event_id_type") != "mlb_statsapi_game_pk":
            errors.append("candidate canonical event identity type mismatch")
        if request_scope.get("event_identities") != expected_event_identities:
            errors.append("candidate event identity binding mismatch")
    for index, row in enumerate(rows, start=2):
        if row.get("candidate_universe_source_digest") != expected_source_digest:
            errors.append(f"candidates row {index} source digest is not bound to raw inputs")
        if row.get("candidate_universe_configuration_digest") != expected_configuration_digest:
            errors.append(f"candidates row {index} configuration digest mismatch")
        if row.get("candidate_universe_id") != expected_universe_id:
            errors.append(f"candidates row {index} universe ID mismatch")


def assemble_context_source_pack(
    *,
    operating_date: date | str,
    cutoff_utc: datetime | str,
    assembled_at_utc: datetime | str,
    snapshot_dirs: Mapping[str, str | Path],
    unavailable_sources: Mapping[str, str] | None,
    git_commit: str,
    research_root: str | Path = DEFAULT_SOURCE_RESEARCH_ROOT,
    validate_feature_compatibility: bool = True,
) -> SourcePackResult:
    """Assemble immutable source snapshots into one v1 point-in-time pack."""

    parsed_date = _date(operating_date, "operating_date")
    cutoff = _utc(cutoff_utc, "cutoff_utc")
    assembled = _utc(assembled_at_utc, "assembled_at_utc")
    if assembled < cutoff:
        raise ContextSourceError("assembled_at_utc cannot be before cutoff_utc")
    commit = _validate_git_commit(git_commit)
    root = _research_root(research_root)
    supplied = dict(snapshot_dirs)
    unknown = sorted(set(supplied) - set(SOURCE_NAMES))
    if unknown:
        raise ContextSourceError("unsupported source snapshots: " + ", ".join(unknown))
    missing_required = sorted(REQUIRED_SOURCES - set(supplied))
    if missing_required:
        raise ContextSourceError(
            "missing required source snapshots: " + ", ".join(missing_required)
        )
    unavailable = dict(unavailable_sources or {})
    unknown_unavailable = sorted(set(unavailable) - set(OPTIONAL_SOURCES))
    if unknown_unavailable:
        raise ContextSourceError(
            "only optional sources may be unavailable: " + ", ".join(unknown_unavailable)
        )
    overlap = sorted(set(supplied) & set(unavailable))
    if overlap:
        raise ContextSourceError(
            "sources cannot be both supplied and unavailable: " + ", ".join(overlap)
        )
    unaccounted = sorted(set(OPTIONAL_SOURCES) - set(supplied) - set(unavailable))
    if unaccounted:
        raise ContextSourceError(
            "every missing optional source needs an explicit unavailable reason: "
            + ", ".join(unaccounted)
        )
    for source_name, reason in unavailable.items():
        if not isinstance(reason, str) or not reason.strip():
            raise ContextSourceError(f"unavailable reason is required for {source_name}")

    loaded: dict[str, tuple[dict[str, object], Path]] = {}
    source_entries: list[dict[str, object]] = []
    for source_name in SOURCE_NAMES:
        if source_name not in supplied:
            source_entries.append(
                {
                    "source_name": source_name,
                    "filename": SOURCE_FILES[source_name],
                    "available": False,
                    "unavailable_reason": unavailable[source_name].strip(),
                    "availability_status": "unavailable",
                    "availability_note": unavailable[source_name].strip(),
                    "snapshot_id": None,
                    "sha256": None,
                    "row_count": 0,
                    "byte_size": None,
                    "source_schema_version": SOURCE_SCHEMA_VERSIONS[source_name],
                }
            )
            continue
        snapshot_manifest, source_path = _load_snapshot(supplied[source_name])
        if snapshot_manifest.get("source_name") != source_name:
            raise ContextSourceError(
                f"snapshot source mismatch for {source_name}: "
                f"{snapshot_manifest.get('source_name')!r}"
            )
        scope = snapshot_manifest.get("request_scope")
        if not isinstance(scope, Mapping):
            raise ContextSourceError(f"{source_name} snapshot request_scope must be an object")
        if scope.get("operating_date") != parsed_date.isoformat():
            raise ContextSourceError(f"{source_name} snapshot operating date mismatch")
        try:
            source_cutoff = _utc(scope.get("cutoff_utc", ""), f"{source_name}.cutoff_utc")
        except ContextSourceError:
            raise
        if source_cutoff != cutoff:
            raise ContextSourceError(f"{source_name} snapshot cutoff mismatch")
        loaded[source_name] = (snapshot_manifest, source_path)
        source_entries.append(
            {
                "source_name": source_name,
                "filename": SOURCE_FILES[source_name],
                "available": True,
                "unavailable_reason": None,
                "availability_status": snapshot_manifest["availability_status"],
                "availability_note": snapshot_manifest["availability_note"],
                "snapshot_id": snapshot_manifest["snapshot_id"],
                "snapshot_manifest_digest": snapshot_manifest["manifest_digest"],
                "sha256": snapshot_manifest["sha256"],
                "row_count": snapshot_manifest["row_count"],
                "byte_size": snapshot_manifest["byte_size"],
                "source_schema_version": snapshot_manifest["source_schema_version"],
                "provider": snapshot_manifest["provider"],
                "request_scope": dict(scope),
                "collected_at_utc": snapshot_manifest["collected_at_utc"],
                "source_clock_fields": list(snapshot_manifest["source_clock_fields"]),
                "collector_version": snapshot_manifest["collector_version"],
                "collector_configuration": dict(
                    snapshot_manifest["collector_configuration"]  # type: ignore[arg-type]
                ),
                "raw_input_digests": [
                    {
                        key: record.get(key)
                        for key in ("input_name", "sha256", "byte_size", "row_count")
                    }
                    for record in (snapshot_manifest.get("raw_inputs") or [])
                    if isinstance(record, Mapping)
                ],
                "git_commit": snapshot_manifest["git_commit"],
            }
        )

    identity_payload = {
        "schema_version": SOURCE_PACK_SCHEMA_VERSION,
        "operating_date": parsed_date.isoformat(),
        "cutoff_utc": _utc_text(cutoff, "cutoff"),
        "assembled_at_utc": _utc_text(assembled, "assembled"),
        "git_commit": commit,
        "sources": source_entries,
    }
    pack_id = "pack-" + _sha256_value(identity_payload)
    parent = root / parsed_date.isoformat()
    destination = parent / pack_id
    parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"immutable source pack already exists: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=".context-pack-", dir=parent))
    published = False
    try:
        source_paths: dict[str, Path] = {}
        for source_name, (snapshot_manifest, source_path) in loaded.items():
            expected = str(snapshot_manifest["sha256"])
            if _sha256_bytes(source_path.read_bytes()) != expected:
                raise ContextSourceError(f"{source_name} snapshot changed before assembly")
            destination_file = temporary / SOURCE_FILES[source_name]
            with source_path.open("rb") as source, destination_file.open("xb") as target:
                shutil.copyfileobj(source, target)
            if _sha256_bytes(destination_file.read_bytes()) != expected:
                raise ContextSourceError(f"{source_name} copied digest mismatch")
            if _sha256_bytes(source_path.read_bytes()) != expected:
                raise ContextSourceError(f"{source_name} snapshot changed during assembly")
            source_paths[source_name] = destination_file

        manifest: dict[str, object] = {
            **identity_payload,
            "pack_id": pack_id,
            "feature_schema_version": context_features.FEATURE_SCHEMA_VERSION,
            "candidate_universe_origin_required": "neutral_market_independent",
            "source_files": source_entries,
            "research_only": True,
            "provider_network_access_performed": False,
            "model_training_enabled": False,
            "predictions_enabled": False,
            "promotion_enabled": False,
            "official_pick_or_lifecycle_modified": False,
            "eligible_for_betting": False,
            "kelly_eligible": False,
        }
        # Avoid retaining the duplicate identity-only key in the public manifest.
        manifest.pop("sources", None)
        manifest["manifest_digest"] = _sha256_value(manifest)
        manifest_path = temporary / SOURCE_PACK_MANIFEST_FILENAME
        manifest_path.write_bytes(_canonical_json_bytes(manifest, pretty=True))
        temporary.replace(destination)
        published = True
    finally:
        if not published and temporary.exists():
            shutil.rmtree(temporary)

    validation = validate_context_source_pack(
        destination,
        require_feature_compatibility=validate_feature_compatibility,
    )
    if not validation.is_valid:
        shutil.rmtree(destination)
        raise ContextSourceError(
            "assembled source pack failed validation: "
            + "; ".join(validation.errors)
        )
    return SourcePackResult(
        pack_id=pack_id,
        pack_dir=destination,
        manifest_path=destination / SOURCE_PACK_MANIFEST_FILENAME,
        source_paths=MappingProxyType(
            {
                source_name: destination / SOURCE_FILES[source_name]
                for source_name in loaded
            }
        ),
    )


def validate_context_source_pack(
    pack_dir: str | Path,
    *,
    require_feature_compatibility: bool = True,
) -> SourcePackValidationResult:
    """Read-only validation of manifest bindings, clocks, and v2 compatibility."""

    root = Path(pack_dir).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    manifest_path = root / SOURCE_PACK_MANIFEST_FILENAME
    if not manifest_path.is_file():
        return SourcePackValidationResult(
            root, False, (f"source pack manifest is missing: {manifest_path}",), ()
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return SourcePackValidationResult(
            root,
            False,
            (f"could not read source pack manifest: {exc}",),
            (),
        )
    if not isinstance(manifest, dict):
        return SourcePackValidationResult(
            root, False, ("source pack manifest must be an object",), ()
        )
    if manifest.get("schema_version") != SOURCE_PACK_SCHEMA_VERSION:
        errors.append("unsupported source pack schema version")
    if manifest.get("feature_schema_version") != context_features.FEATURE_SCHEMA_VERSION:
        errors.append("source pack feature schema version mismatch")
    if manifest.get("manifest_digest") != _sha256_value(_manifest_without_digest(manifest)):
        errors.append("source pack manifest digest mismatch")
    for field_name, expected in (
        ("research_only", True),
        ("provider_network_access_performed", False),
        ("model_training_enabled", False),
        ("predictions_enabled", False),
        ("promotion_enabled", False),
        ("official_pick_or_lifecycle_modified", False),
        ("eligible_for_betting", False),
        ("kelly_eligible", False),
    ):
        if manifest.get(field_name) is not expected:
            errors.append(f"source pack safety field {field_name} must be {expected}")
    try:
        operating_date = _date(manifest.get("operating_date", ""), "operating_date")
        cutoff = _utc(manifest.get("cutoff_utc", ""), "cutoff_utc")
        assembled = _utc(manifest.get("assembled_at_utc", ""), "assembled_at_utc")
        if assembled < cutoff:
            errors.append("assembled_at_utc cannot be before cutoff_utc")
        _validate_git_commit(str(manifest.get("git_commit") or ""))
    except ContextSourceError as exc:
        errors.append(str(exc))
        operating_date = date.min
        cutoff = datetime.min.replace(tzinfo=timezone.utc)
    entries = manifest.get("source_files")
    if not isinstance(entries, list) or len(entries) != len(SOURCE_NAMES):
        errors.append("source_files must account for every source exactly once")
        entries = []
    else:
        expected_pack_id = "pack-" + _sha256_value(
            {
                "schema_version": SOURCE_PACK_SCHEMA_VERSION,
                "operating_date": manifest.get("operating_date"),
                "cutoff_utc": manifest.get("cutoff_utc"),
                "assembled_at_utc": manifest.get("assembled_at_utc"),
                "git_commit": manifest.get("git_commit"),
                "sources": entries,
            }
        )
        if manifest.get("pack_id") != expected_pack_id:
            errors.append("source pack ID does not match its bound source identities")
        if root.name != manifest.get("pack_id"):
            errors.append("source pack directory does not match pack_id")
    seen: set[str] = set()
    available_names: set[str] = set()
    for index, entry in enumerate(entries):
        label = f"source_files[{index}]"
        if not isinstance(entry, Mapping):
            errors.append(f"{label} must be an object")
            continue
        source_name = str(entry.get("source_name") or "")
        if source_name not in SOURCE_FILES:
            errors.append(f"{label}.source_name is unsupported")
            continue
        if source_name in seen:
            errors.append(f"duplicate source manifest entry: {source_name}")
            continue
        seen.add(source_name)
        available = entry.get("available") is True
        source_path = root / SOURCE_FILES[source_name]
        if not available:
            if source_name in REQUIRED_SOURCES:
                errors.append(f"required source is unavailable: {source_name}")
            reason = entry.get("unavailable_reason")
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"missing optional source lacks reason: {source_name}")
            if source_path.exists():
                errors.append(f"unavailable source unexpectedly exists: {source_name}")
            if entry.get("availability_status") != "unavailable":
                errors.append(f"unavailable source has invalid status: {source_name}")
            if entry.get("availability_note") != reason:
                errors.append(f"unavailable source note/reason mismatch: {source_name}")
            continue
        available_names.add(source_name)
        availability_status = str(entry.get("availability_status") or "").casefold()
        if availability_status not in {"available", "partial"}:
            errors.append(f"available source has invalid status: {source_name}")
        if availability_status == "partial" and not str(
            entry.get("availability_note") or ""
        ).strip():
            errors.append(f"partial source lacks availability note: {source_name}")
        if not source_path.is_file():
            errors.append(f"available source file is missing: {source_path}")
            continue
        raw = source_path.read_bytes()
        if entry.get("sha256") != _sha256_bytes(raw):
            errors.append(f"source mutation/digest mismatch: {source_name}")
        if entry.get("byte_size") != len(raw):
            errors.append(f"source byte-size mismatch: {source_name}")
        try:
            reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""))
            try:
                next(reader)
            except StopIteration:
                actual_row_count = 0
            else:
                actual_row_count = sum(1 for row in reader if row)
        except (UnicodeError, csv.Error) as exc:
            errors.append(f"source CSV is unreadable for {source_name}: {exc}")
        else:
            if entry.get("row_count") != actual_row_count:
                errors.append(f"source row-count mismatch: {source_name}")
        if entry.get("source_schema_version") != SOURCE_SCHEMA_VERSIONS[source_name]:
            errors.append(f"source schema version mismatch: {source_name}")
        if source_name == "candidates":
            _validate_candidate_manifest_binding(entry, raw, cutoff=cutoff, errors=errors)
        try:
            snapshot_collected = _utc(
                entry.get("collected_at_utc", ""), f"{source_name}.collected_at_utc"
            )
        except ContextSourceError as exc:
            errors.append(str(exc))
        else:
            if snapshot_collected > cutoff:
                errors.append(f"{source_name} snapshot was collected after pack cutoff")
        scope = entry.get("request_scope")
        if not isinstance(scope, Mapping):
            errors.append(f"{source_name} request_scope must be an object")
        else:
            if scope.get("operating_date") != operating_date.isoformat():
                errors.append(f"{source_name} operating date differs from pack")
            try:
                source_cutoff = _utc(scope.get("cutoff_utc", ""), f"{source_name}.cutoff_utc")
            except ContextSourceError as exc:
                errors.append(str(exc))
            else:
                if source_cutoff != cutoff:
                    errors.append(f"{source_name} cutoff differs from pack")
        _validate_pack_source_clocks(source_name, source_path, cutoff=cutoff, errors=errors)
    missing_entries = sorted(set(SOURCE_NAMES) - seen)
    if missing_entries:
        errors.append("manifest omits sources: " + ", ".join(missing_entries))
    unknown_csv = sorted(
        path.name
        for path in root.glob("*.csv")
        if path.name not in {SOURCE_FILES[name] for name in available_names}
    )
    if unknown_csv:
        errors.append("unbound CSV files in source pack: " + ", ".join(unknown_csv))

    feature_row_count: int | None = None
    if require_feature_compatibility and not errors:
        try:
            build = context_features.build_context_features(
                operating_date=operating_date,
                as_of_utc=cutoff,
                source_root=root,
                git_commit=str(manifest["git_commit"]),
                dry_run=True,
            )
        except Exception as exc:
            errors.append(f"feature-store v2 compatibility failed: {exc}")
        else:
            feature_row_count = len(build.rows)
            if build.summary.get("provider_network_access_performed") is not False:
                errors.append("feature materializer reported provider network access")
            if build.summary.get("model_training_performed") is not False:
                errors.append("feature materializer reported model training")
    return SourcePackValidationResult(
        pack_dir=root,
        is_valid=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        feature_row_count=feature_row_count,
    )


__all__ = [
    "CANDIDATE_COLUMNS",
    "CANDIDATE_UNIVERSE_GENERATOR",
    "CANDIDATE_UNIVERSE_POLICY",
    "CANDIDATE_UNIVERSE_VERSION",
    "CandidateUniverseResult",
    "ContextSourceError",
    "DEFAULT_SOURCE_RESEARCH_ROOT",
    "OPTIONAL_SOURCES",
    "LINEUP_OUTPUT_COLUMNS",
    "PROBABLE_PITCHER_OUTPUT_COLUMNS",
    "REQUIRED_SOURCES",
    "SOURCE_COLLECTOR_VERSION",
    "SOURCE_FILES",
    "SOURCE_NAMES",
    "SOURCE_PACK_MANIFEST_FILENAME",
    "SOURCE_PACK_SCHEMA_VERSION",
    "SOURCE_SCHEMA_VERSIONS",
    "SOURCE_SNAPSHOT_MANIFEST_FILENAME",
    "SOURCE_SNAPSHOT_SCHEMA_VERSION",
    "STATCAST_OUTPUT_COLUMNS",
    "SourcePackResult",
    "SourcePackValidationResult",
    "SourceSnapshotResult",
    "assemble_context_source_pack",
    "build_neutral_candidate_universe",
    "collect_candidate_snapshot",
    "collect_identity_snapshot",
    "collect_normalized_source_snapshot",
    "collect_statsapi_lineup_snapshot",
    "collect_statsapi_probable_pitcher_snapshot",
    "collect_statcast_snapshot",
    "normalize_statcast_pitch_csv",
    "persist_source_snapshot",
    "source_snapshot_id",
    "validate_context_source_pack",
]
