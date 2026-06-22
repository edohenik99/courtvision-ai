"""Controlled Baseball Savant / Statcast historical CSV ingestion.

This prototype is local-first and produces research-only event rows. Network
access and every filesystem output are default-deny and require explicit caller
opt-in. It does not create features, training data, probabilities, or runtime
inputs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Final, Mapping, Sequence
from urllib.parse import urlencode
import urllib.request

from courtvision.sports.mlb.data_manifest import (
    MLBDataDomain,
    MLBSourceFileRecord,
    MLBSourceManifest,
    MLBSourceType,
    compute_file_sha256,
    validate_source_manifest,
    write_manifest,
)


STATCAST_SOURCE_NAME: Final = "baseball_savant_statcast"
STATCAST_SCHEMA_VERSION: Final = "1.0"
STATCAST_QUERY_ENDPOINT: Final = "https://baseballsavant.mlb.com/statcast_search/csv"
DEFAULT_MAX_QUERY_DAYS: Final = 31

REQUIRED_STATCAST_COLUMNS: Final = frozenset(
    {
        "game_date",
        "player_name",
        "batter",
        "pitcher",
        "events",
        "description",
        "stand",
        "p_throws",
        "home_team",
        "away_team",
        "inning",
        "inning_topbot",
        "pitch_type",
        "launch_speed",
        "launch_angle",
        "hit_distance_sc",
        "bb_type",
    }
)
GAME_ID_COLUMNS: Final = ("game_pk", "game_id")


class StatcastIngestionError(ValueError):
    """Raised when Statcast input violates the narrow ingestion contract."""


@dataclass(frozen=True, slots=True)
class MLBStatcastEventRow:
    """Stable historical event row for MLB home-run research."""

    sport: str
    league: str
    source: str
    source_type: str
    game_date: date
    game_id: str
    player_id: int
    player_name: str
    pitcher_id: int
    event_type: str | None
    is_home_run: bool
    description: str | None
    batter_hand: str | None
    pitcher_hand: str | None
    home_team: str
    away_team: str
    inning: int | None
    inning_half: str | None
    pitch_type: str | None
    launch_speed: float | None
    launch_angle: float | None
    hit_distance: float | None
    batted_ball_type: str | None
    estimated_ba: float | None
    estimated_woba: float | None
    woba_value: float | None
    is_barrel: bool | None
    raw_row_hash: str
    as_of_date: date
    collected_at: datetime


@dataclass(frozen=True, slots=True)
class StatcastIngestionResult:
    """Rows, validated provenance, and any explicitly written destinations."""

    rows: tuple[MLBStatcastEventRow, ...]
    manifest: MLBSourceManifest
    raw_output_path: Path | None = None
    normalized_output_path: Path | None = None
    manifest_output_path: Path | None = None


def _parse_date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        raise StatcastIngestionError(f"{field_name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError) as exc:
        raise StatcastIngestionError(
            f"{field_name} must be an ISO date (YYYY-MM-DD): {value!r}"
        ) from exc


def _validate_date_range(
    start_date: date | str,
    end_date: date | str,
    *,
    max_days: int = DEFAULT_MAX_QUERY_DAYS,
    confirm_large_range: bool = False,
) -> tuple[date, date]:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise StatcastIngestionError("start_date must not be after end_date")
    if isinstance(max_days, bool) or not isinstance(max_days, int) or max_days < 1:
        raise StatcastIngestionError("max_days must be a positive integer")
    inclusive_days = (end - start).days + 1
    if inclusive_days > max_days and not confirm_large_range:
        raise StatcastIngestionError(
            f"date range is {inclusive_days} days; set confirm_large_range=True "
            f"to exceed the {max_days}-day guard"
        )
    return start, end


def build_statcast_query_params(
    start_date: date | str,
    end_date: date | str,
    *,
    max_days: int = DEFAULT_MAX_QUERY_DAYS,
    confirm_large_range: bool = False,
) -> dict[str, str]:
    """Build narrow Baseball Savant CSV parameters without making a request."""

    start, end = _validate_date_range(
        start_date,
        end_date,
        max_days=max_days,
        confirm_large_range=confirm_large_range,
    )
    return {
        "all": "true",
        "game_date_gt": start.isoformat(),
        "game_date_lt": end.isoformat(),
        "player_type": "batter",
        "type": "details",
    }


def build_statcast_query_url(
    start_date: date | str,
    end_date: date | str,
    *,
    max_days: int = DEFAULT_MAX_QUERY_DAYS,
    confirm_large_range: bool = False,
) -> str:
    """Return a guarded Baseball Savant CSV URL without network access."""

    params = build_statcast_query_params(
        start_date,
        end_date,
        max_days=max_days,
        confirm_large_range=confirm_large_range,
    )
    return f"{STATCAST_QUERY_ENDPOINT}?{urlencode(params)}"


def download_statcast_csv(
    start_date: date | str,
    end_date: date | str,
    output_path: str | Path,
    *,
    allow_network: bool = False,
    max_days: int = DEFAULT_MAX_QUERY_DAYS,
    confirm_large_range: bool = False,
    overwrite: bool = False,
    timeout_seconds: float = 60.0,
) -> Path:
    """Explicitly download a guarded date range to an existing directory."""

    url = build_statcast_query_url(
        start_date,
        end_date,
        max_days=max_days,
        confirm_large_range=confirm_large_range,
    )
    if not allow_network:
        raise PermissionError("Statcast network access requires allow_network=True")

    destination = Path(output_path).expanduser().resolve()
    if not destination.parent.is_dir():
        raise StatcastIngestionError("output parent directory must already exist")
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CourtVision-Statcast-Research/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = response.read()
    mode = "wb" if overwrite else "xb"
    with destination.open(mode) as handle:
        handle.write(payload)
    return destination


def _validate_columns(fieldnames: Sequence[str] | None) -> str:
    if not fieldnames:
        raise StatcastIngestionError("Statcast CSV is missing a header row")
    available = set(fieldnames)
    missing = sorted(REQUIRED_STATCAST_COLUMNS - available)
    if missing:
        raise StatcastIngestionError(
            "Statcast CSV is missing required columns: " + ", ".join(missing)
        )
    for game_id_column in GAME_ID_COLUMNS:
        if game_id_column in available:
            return game_id_column
    raise StatcastIngestionError(
        "Statcast CSV is missing required game id column: game_pk or game_id"
    )


def _optional_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _required_text(value: object, field_name: str, row_number: int) -> str:
    text = _optional_text(value)
    if text is None:
        raise StatcastIngestionError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return text


def _optional_float(value: object, field_name: str, row_number: int) -> float | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise StatcastIngestionError(
            f"row {row_number}: {field_name} must be numeric or empty"
        ) from exc


def _optional_int(value: object, field_name: str, row_number: int) -> int | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise StatcastIngestionError(
            f"row {row_number}: {field_name} must be an integer or empty"
        ) from exc


def _required_int(value: object, field_name: str, row_number: int) -> int:
    parsed = _optional_int(value, field_name, row_number)
    if parsed is None:
        raise StatcastIngestionError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return parsed


def _optional_bool(value: object, field_name: str, row_number: int) -> bool | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.lower()
    if normalized in {"1", "true"}:
        return True
    if normalized in {"0", "false"}:
        return False
    raise StatcastIngestionError(
        f"row {row_number}: {field_name} must be 0, 1, true, false, or empty"
    )


def _raw_row_hash(row: Mapping[str, object]) -> str:
    payload = json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_row(
    raw_row: Mapping[str, object],
    *,
    row_number: int,
    game_id_column: str,
    as_of_date: date,
    collected_at: datetime,
) -> MLBStatcastEventRow:
    game_date_text = _required_text(raw_row.get("game_date"), "game_date", row_number)
    try:
        game_date = date.fromisoformat(game_date_text)
    except ValueError as exc:
        raise StatcastIngestionError(
            f"row {row_number}: game_date must be an ISO date (YYYY-MM-DD): "
            f"{game_date_text!r}"
        ) from exc

    event_type = _optional_text(raw_row.get("events"))
    return MLBStatcastEventRow(
        sport="MLB",
        league="MLB",
        source=STATCAST_SOURCE_NAME,
        source_type=MLBSourceType.HISTORICAL.value,
        game_date=game_date,
        game_id=_required_text(raw_row.get(game_id_column), game_id_column, row_number),
        player_id=_required_int(raw_row.get("batter"), "batter", row_number),
        player_name=_required_text(raw_row.get("player_name"), "player_name", row_number),
        pitcher_id=_required_int(raw_row.get("pitcher"), "pitcher", row_number),
        event_type=event_type,
        is_home_run=event_type == "home_run",
        description=_optional_text(raw_row.get("description")),
        batter_hand=_optional_text(raw_row.get("stand")),
        pitcher_hand=_optional_text(raw_row.get("p_throws")),
        home_team=_required_text(raw_row.get("home_team"), "home_team", row_number),
        away_team=_required_text(raw_row.get("away_team"), "away_team", row_number),
        inning=_optional_int(raw_row.get("inning"), "inning", row_number),
        inning_half=_optional_text(raw_row.get("inning_topbot")),
        pitch_type=_optional_text(raw_row.get("pitch_type")),
        launch_speed=_optional_float(raw_row.get("launch_speed"), "launch_speed", row_number),
        launch_angle=_optional_float(raw_row.get("launch_angle"), "launch_angle", row_number),
        hit_distance=_optional_float(raw_row.get("hit_distance_sc"), "hit_distance_sc", row_number),
        batted_ball_type=_optional_text(raw_row.get("bb_type")),
        estimated_ba=_optional_float(
            raw_row.get("estimated_ba_using_speedangle"),
            "estimated_ba_using_speedangle",
            row_number,
        ),
        estimated_woba=_optional_float(
            raw_row.get("estimated_woba_using_speedangle"),
            "estimated_woba_using_speedangle",
            row_number,
        ),
        woba_value=_optional_float(raw_row.get("woba_value"), "woba_value", row_number),
        is_barrel=_optional_bool(raw_row.get("barrel"), "barrel", row_number),
        raw_row_hash=_raw_row_hash(raw_row),
        as_of_date=as_of_date,
        collected_at=collected_at,
    )


def statcast_row_to_dict(row: MLBStatcastEventRow) -> dict[str, object]:
    """Serialize a normalized row with stable names and ISO temporal values."""

    return {
        "as_of_date": row.as_of_date.isoformat(),
        "away_team": row.away_team,
        "batted_ball_type": row.batted_ball_type,
        "batter_hand": row.batter_hand,
        "collected_at": row.collected_at.isoformat(),
        "description": row.description,
        "estimated_ba": row.estimated_ba,
        "estimated_woba": row.estimated_woba,
        "event_type": row.event_type,
        "game_date": row.game_date.isoformat(),
        "game_id": row.game_id,
        "hit_distance": row.hit_distance,
        "home_team": row.home_team,
        "inning": row.inning,
        "inning_half": row.inning_half,
        "is_barrel": row.is_barrel,
        "is_home_run": row.is_home_run,
        "launch_angle": row.launch_angle,
        "launch_speed": row.launch_speed,
        "league": row.league,
        "pitch_type": row.pitch_type,
        "pitcher_hand": row.pitcher_hand,
        "pitcher_id": row.pitcher_id,
        "player_id": row.player_id,
        "player_name": row.player_name,
        "raw_row_hash": row.raw_row_hash,
        "source": row.source,
        "source_type": row.source_type,
        "sport": row.sport,
        "woba_value": row.woba_value,
    }


def statcast_row_to_json(row: MLBStatcastEventRow) -> str:
    """Return deterministic compact JSON for one normalized event row."""

    return json.dumps(
        statcast_row_to_dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_local_rows(path: Path) -> tuple[list[dict[str, str]], str]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            game_id_column = _validate_columns(reader.fieldnames)
            rows = [dict(row) for row in reader]
    except (OSError, UnicodeError, csv.Error) as exc:
        raise StatcastIngestionError(f"could not read Statcast CSV {path}: {exc}") from exc
    if not rows:
        raise StatcastIngestionError("Statcast CSV contains no data rows")
    return rows, game_id_column


def _output_paths(
    output_dir: Path,
    start: date,
    end: date,
) -> tuple[Path, Path, Path]:
    stem = f"statcast_{start.isoformat()}_{end.isoformat()}"
    return (
        output_dir / "raw" / f"{stem}.csv",
        output_dir / "normalized" / f"{stem}.jsonl",
        output_dir / "manifests" / f"{stem}.manifest.json",
    )


def _check_output_collisions(paths: Sequence[Path], overwrite: bool) -> None:
    if overwrite:
        return
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError(existing[0])


def ingest_local_statcast_csv(
    input_csv: str | Path,
    *,
    as_of_date: date | str | None = None,
    collected_at: datetime | None = None,
    output_dir: str | Path | None = None,
    write_raw: bool = False,
    write_normalized: bool = False,
    write_manifest_file: bool = False,
    overwrite: bool = False,
) -> StatcastIngestionResult:
    """Parse local Statcast CSV and optionally write explicitly requested files."""

    source_path = Path(input_csv).expanduser().resolve()
    if not source_path.is_file():
        raise StatcastIngestionError(f"Statcast input CSV does not exist: {source_path}")
    if collected_at is None:
        collected_at = datetime.now(timezone.utc)
    if not isinstance(collected_at, datetime):
        raise StatcastIngestionError("collected_at must be a datetime")

    raw_rows, game_id_column = _read_local_rows(source_path)
    parsed_dates: list[date] = []
    for row_number, raw_row in enumerate(raw_rows, start=2):
        raw_date = _required_text(raw_row.get("game_date"), "game_date", row_number)
        try:
            parsed_dates.append(date.fromisoformat(raw_date))
        except ValueError as exc:
            raise StatcastIngestionError(
                f"row {row_number}: game_date must be an ISO date (YYYY-MM-DD): {raw_date!r}"
            ) from exc
    start = min(parsed_dates)
    end = max(parsed_dates)
    effective_as_of_date = (
        _parse_date(as_of_date, "as_of_date") if as_of_date is not None else end
    )
    if effective_as_of_date < end:
        raise StatcastIngestionError(
            "as_of_date must not be before the latest game_date in the CSV"
        )

    rows = tuple(
        _normalize_row(
            raw_row,
            row_number=row_number,
            game_id_column=game_id_column,
            as_of_date=effective_as_of_date,
            collected_at=collected_at,
        )
        for row_number, raw_row in enumerate(raw_rows, start=2)
    )

    any_write = write_raw or write_normalized or write_manifest_file
    if any_write and output_dir is None:
        raise StatcastIngestionError("output_dir is required when any write is requested")

    raw_output_path: Path | None = None
    normalized_output_path: Path | None = None
    manifest_output_path: Path | None = None
    destinations: list[Path] = []
    if output_dir is not None:
        output_root = Path(output_dir).expanduser().resolve()
        raw_candidate, normalized_candidate, manifest_candidate = _output_paths(
            output_root, start, end
        )
        if write_raw:
            raw_output_path = raw_candidate
            destinations.append(raw_candidate)
        if write_normalized:
            normalized_output_path = normalized_candidate
            destinations.append(normalized_candidate)
        if write_manifest_file:
            manifest_output_path = manifest_candidate
            destinations.append(manifest_candidate)
        _check_output_collisions(destinations, overwrite)

    manifest_raw_path = raw_output_path or source_path
    manifest = MLBSourceManifest(
        source_name=STATCAST_SOURCE_NAME,
        source_type=MLBSourceType.HISTORICAL,
        data_domain=MLBDataDomain.STATCAST,
        collected_at=collected_at,
        raw_path=manifest_raw_path,
        schema_version=STATCAST_SCHEMA_VERSION,
        date_range_start=start,
        date_range_end=end,
        as_of_date=effective_as_of_date,
        normalized_path=normalized_output_path,
        checksum=compute_file_sha256(source_path),
        row_count=len(rows),
        file_count=1,
        generated_by="courtvision.sports.mlb.data.statcast_ingestion",
        notes=("Local historical CSV ingestion prototype.",),
        warnings=(
            "Historical research use only.",
            "No rolling or same-game feature generation was performed.",
        ),
        files=(
            MLBSourceFileRecord(
                path=manifest_raw_path,
                checksum=compute_file_sha256(source_path),
                row_count=len(rows),
                byte_size=source_path.stat().st_size,
                content_type="text/csv",
            ),
        ),
    )
    validate_source_manifest(manifest).raise_for_errors()

    if any_write:
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
        if raw_output_path is not None:
            shutil.copyfile(source_path, raw_output_path)
        if normalized_output_path is not None:
            mode = "w" if overwrite else "x"
            with normalized_output_path.open(mode, encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(f"{statcast_row_to_json(row)}\n")
        if manifest_output_path is not None:
            write_manifest(manifest, manifest_output_path, overwrite=overwrite)

    return StatcastIngestionResult(
        rows=rows,
        manifest=manifest,
        raw_output_path=raw_output_path,
        normalized_output_path=normalized_output_path,
        manifest_output_path=manifest_output_path,
    )


__all__ = [
    "DEFAULT_MAX_QUERY_DAYS",
    "GAME_ID_COLUMNS",
    "MLBStatcastEventRow",
    "REQUIRED_STATCAST_COLUMNS",
    "STATCAST_QUERY_ENDPOINT",
    "STATCAST_SCHEMA_VERSION",
    "STATCAST_SOURCE_NAME",
    "StatcastIngestionError",
    "StatcastIngestionResult",
    "build_statcast_query_params",
    "build_statcast_query_url",
    "download_statcast_csv",
    "ingest_local_statcast_csv",
    "statcast_row_to_dict",
    "statcast_row_to_json",
]
