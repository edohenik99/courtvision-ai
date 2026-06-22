"""Controlled local Retrosheet-style game and event CSV ingestion.

This prototype accepts only caller-supplied local files and produces immutable
historical/research rows plus Phase 3B provenance. It has no download path and
does not create features, training data, probabilities, or runtime inputs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Final, Mapping, Sequence

from courtvision.sports.mlb.data_manifest import (
    MLBDataDomain,
    MLBSourceFileRecord,
    MLBSourceManifest,
    MLBSourceType,
    compute_file_sha256,
    validate_source_manifest,
    write_manifest,
)


RETROSHEET_SOURCE_NAME: Final = "retrosheet"
RETROSHEET_SCHEMA_VERSION: Final = "1.0"
RETROSHEET_GAME_STATUSES: Final = frozenset(
    {"completed", "postponed", "suspended", "unknown"}
)
RETROSHEET_HOME_RUN_EVENT_TYPES: Final = frozenset(
    {"home_run", "homer", "hr"}
)
RETROSHEET_SOURCE_TYPES: Final = frozenset(
    {MLBSourceType.HISTORICAL.value, MLBSourceType.PUBLIC.value}
)

REQUIRED_RETROSHEET_GAME_COLUMNS: Final = frozenset(
    {
        "game_id",
        "game_date",
        "home_team",
        "away_team",
        "game_status",
        "source_type",
    }
)
REQUIRED_RETROSHEET_EVENT_COLUMNS: Final = frozenset(
    {
        "game_id",
        "game_date",
        "inning",
        "batting_team",
        "fielding_team",
        "batter_id",
        "batter_name",
        "pitcher_id",
        "pitcher_name",
        "event_type",
        "is_home_run",
        "source_type",
    }
)


class RetrosheetIngestionError(ValueError):
    """Raised when local input violates the narrow Retrosheet contract."""


@dataclass(frozen=True, slots=True)
class RetrosheetGameRow:
    """Stable historical MLB game/outcome row."""

    sport: str
    league: str
    source: str
    source_type: str
    game_id: str
    game_date: date
    home_team: str
    away_team: str
    game_number: int | None
    venue_name: str | None
    game_status: str
    home_score: int | None
    away_score: int | None
    as_of_date: date
    collected_at: datetime

    @property
    def is_completed(self) -> bool:
        """Return true only for an explicitly completed game."""

        return self.game_status == "completed"


@dataclass(frozen=True, slots=True)
class RetrosheetEventRow:
    """Stable historical MLB event row focused on home-run outcomes."""

    sport: str
    league: str
    source: str
    source_type: str
    game_id: str
    game_date: date
    inning: int
    batting_team: str
    fielding_team: str
    batter_id: str
    batter_name: str
    pitcher_id: str
    pitcher_name: str
    event_type: str
    event_text: str | None
    is_home_run: bool
    rbi: int | None
    as_of_date: date
    collected_at: datetime
    raw_row_hash: str


@dataclass(frozen=True, slots=True)
class RetrosheetIngestionResult:
    """Normalized rows, validated provenance, and explicit output paths."""

    games: tuple[RetrosheetGameRow, ...]
    events: tuple[RetrosheetEventRow, ...]
    manifest: MLBSourceManifest
    raw_game_output_path: Path | None = None
    raw_event_output_path: Path | None = None
    normalized_game_output_path: Path | None = None
    normalized_event_output_path: Path | None = None
    manifest_output_path: Path | None = None


def _optional_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _required_text(value: object, field_name: str, row_number: int) -> str:
    text = _optional_text(value)
    if text is None:
        raise RetrosheetIngestionError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return text


def _parse_date(value: date | str, field_name: str) -> date:
    if isinstance(value, datetime):
        raise RetrosheetIngestionError(
            f"{field_name} must be a date, not a datetime"
        )
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError) as exc:
        raise RetrosheetIngestionError(
            f"{field_name} must be an ISO date (YYYY-MM-DD): {value!r}"
        ) from exc


def _row_date(value: object, row_number: int) -> date:
    text = _required_text(value, "game_date", row_number)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RetrosheetIngestionError(
            f"row {row_number}: game_date must be an ISO date (YYYY-MM-DD): "
            f"{text!r}"
        ) from exc


def _optional_int(value: object, field_name: str, row_number: int) -> int | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise RetrosheetIngestionError(
            f"row {row_number}: {field_name} must be an integer or empty"
        ) from exc


def _required_int(value: object, field_name: str, row_number: int) -> int:
    parsed = _optional_int(value, field_name, row_number)
    if parsed is None:
        raise RetrosheetIngestionError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return parsed


def _source_type(value: object, row_number: int) -> str:
    source_type = _required_text(value, "source_type", row_number).lower()
    if source_type not in RETROSHEET_SOURCE_TYPES:
        supported = ", ".join(sorted(RETROSHEET_SOURCE_TYPES))
        raise RetrosheetIngestionError(
            f"row {row_number}: source_type must be one of: {supported}"
        )
    return source_type


def _game_status(value: object, row_number: int) -> str:
    status = _required_text(value, "game_status", row_number).lower()
    return status if status in RETROSHEET_GAME_STATUSES else "unknown"


def _event_type_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def _home_run_label(
    value: object, event_type: str, row_number: int
) -> bool:
    text = _optional_text(value)
    if text is None:
        return _event_type_key(event_type) in RETROSHEET_HOME_RUN_EVENT_TYPES
    normalized = text.lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise RetrosheetIngestionError(
        f"row {row_number}: is_home_run must be true, false, 1, 0, or empty"
    )


def _raw_row_hash(row: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_csv(
    path: Path,
    required_columns: frozenset[str],
    label: str,
) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise RetrosheetIngestionError(
                    f"Retrosheet {label} CSV is missing a header row"
                )
            missing = sorted(required_columns - set(reader.fieldnames))
            if missing:
                raise RetrosheetIngestionError(
                    f"Retrosheet {label} CSV is missing required columns: "
                    + ", ".join(missing)
                )
            rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise RetrosheetIngestionError(
                        f"row {row_number}: Retrosheet {label} CSV has extra values"
                    )
                rows.append(dict(row))
    except RetrosheetIngestionError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise RetrosheetIngestionError(
            f"could not read Retrosheet {label} CSV {path}: {exc}"
        ) from exc
    if not rows:
        raise RetrosheetIngestionError(
            f"Retrosheet {label} CSV contains no data rows"
        )
    return rows


def _normalize_game(
    raw_row: Mapping[str, object],
    *,
    row_number: int,
    as_of_date: date,
    collected_at: datetime,
) -> RetrosheetGameRow:
    return RetrosheetGameRow(
        sport="MLB",
        league="MLB",
        source=RETROSHEET_SOURCE_NAME,
        source_type=_source_type(raw_row.get("source_type"), row_number),
        game_id=_required_text(raw_row.get("game_id"), "game_id", row_number),
        game_date=_row_date(raw_row.get("game_date"), row_number),
        home_team=_required_text(
            raw_row.get("home_team"), "home_team", row_number
        ),
        away_team=_required_text(
            raw_row.get("away_team"), "away_team", row_number
        ),
        game_number=_optional_int(
            raw_row.get("game_number"), "game_number", row_number
        ),
        venue_name=_optional_text(raw_row.get("venue_name")),
        game_status=_game_status(raw_row.get("game_status"), row_number),
        home_score=_optional_int(
            raw_row.get("home_score"), "home_score", row_number
        ),
        away_score=_optional_int(
            raw_row.get("away_score"), "away_score", row_number
        ),
        as_of_date=as_of_date,
        collected_at=collected_at,
    )


def _normalize_event(
    raw_row: Mapping[str, object],
    *,
    row_number: int,
    as_of_date: date,
    collected_at: datetime,
) -> RetrosheetEventRow:
    event_type = _required_text(
        raw_row.get("event_type"), "event_type", row_number
    )
    return RetrosheetEventRow(
        sport="MLB",
        league="MLB",
        source=RETROSHEET_SOURCE_NAME,
        source_type=_source_type(raw_row.get("source_type"), row_number),
        game_id=_required_text(raw_row.get("game_id"), "game_id", row_number),
        game_date=_row_date(raw_row.get("game_date"), row_number),
        inning=_required_int(raw_row.get("inning"), "inning", row_number),
        batting_team=_required_text(
            raw_row.get("batting_team"), "batting_team", row_number
        ),
        fielding_team=_required_text(
            raw_row.get("fielding_team"), "fielding_team", row_number
        ),
        batter_id=_required_text(
            raw_row.get("batter_id"), "batter_id", row_number
        ),
        batter_name=_required_text(
            raw_row.get("batter_name"), "batter_name", row_number
        ),
        pitcher_id=_required_text(
            raw_row.get("pitcher_id"), "pitcher_id", row_number
        ),
        pitcher_name=_required_text(
            raw_row.get("pitcher_name"), "pitcher_name", row_number
        ),
        event_type=event_type,
        event_text=_optional_text(raw_row.get("event_text")),
        is_home_run=_home_run_label(
            raw_row.get("is_home_run"), event_type, row_number
        ),
        rbi=_optional_int(raw_row.get("rbi"), "rbi", row_number),
        as_of_date=as_of_date,
        collected_at=collected_at,
        raw_row_hash=_raw_row_hash(raw_row),
    )


def retrosheet_game_row_to_dict(row: RetrosheetGameRow) -> dict[str, object]:
    """Serialize a game row with stable names and ISO temporal values."""

    return {
        "as_of_date": row.as_of_date.isoformat(),
        "away_score": row.away_score,
        "away_team": row.away_team,
        "collected_at": row.collected_at.isoformat(),
        "game_date": row.game_date.isoformat(),
        "game_id": row.game_id,
        "game_number": row.game_number,
        "game_status": row.game_status,
        "home_score": row.home_score,
        "home_team": row.home_team,
        "league": row.league,
        "source": row.source,
        "source_type": row.source_type,
        "sport": row.sport,
        "venue_name": row.venue_name,
    }


def retrosheet_event_row_to_dict(row: RetrosheetEventRow) -> dict[str, object]:
    """Serialize an event row with stable names and ISO temporal values."""

    return {
        "as_of_date": row.as_of_date.isoformat(),
        "batter_id": row.batter_id,
        "batter_name": row.batter_name,
        "batting_team": row.batting_team,
        "collected_at": row.collected_at.isoformat(),
        "event_text": row.event_text,
        "event_type": row.event_type,
        "fielding_team": row.fielding_team,
        "game_date": row.game_date.isoformat(),
        "game_id": row.game_id,
        "inning": row.inning,
        "is_home_run": row.is_home_run,
        "league": row.league,
        "pitcher_id": row.pitcher_id,
        "pitcher_name": row.pitcher_name,
        "raw_row_hash": row.raw_row_hash,
        "rbi": row.rbi,
        "source": row.source,
        "source_type": row.source_type,
        "sport": row.sport,
    }


def _row_to_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def retrosheet_game_row_to_json(row: RetrosheetGameRow) -> str:
    """Return deterministic compact JSON for one normalized game row."""

    return _row_to_json(retrosheet_game_row_to_dict(row))


def retrosheet_event_row_to_json(row: RetrosheetEventRow) -> str:
    """Return deterministic compact JSON for one normalized event row."""

    return _row_to_json(retrosheet_event_row_to_dict(row))


def _input_root(paths: Sequence[Path]) -> Path:
    if len(paths) == 1:
        return paths[0]
    return Path(os.path.commonpath([str(path.parent) for path in paths]))


def _combined_checksum(checksums: Sequence[tuple[str, str]]) -> str:
    if len(checksums) == 1:
        return checksums[0][1]
    payload = "\n".join(f"{label}:{checksum}" for label, checksum in checksums)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _check_output_collisions(paths: Sequence[Path], overwrite: bool) -> None:
    if overwrite:
        return
    existing = [path for path in paths if path.exists()]
    if existing:
        raise FileExistsError(existing[0])


def ingest_local_retrosheet_csvs(
    *,
    games_csv: str | Path | None = None,
    events_csv: str | Path | None = None,
    as_of_date: date | str | None = None,
    collected_at: datetime | None = None,
    output_dir: str | Path | None = None,
    write_raw: bool = False,
    write_normalized: bool = False,
    write_manifest_file: bool = False,
    overwrite: bool = False,
) -> RetrosheetIngestionResult:
    """Parse one or both local fixture formats and optionally write outputs."""

    if games_csv is None and events_csv is None:
        raise RetrosheetIngestionError(
            "at least one of games_csv or events_csv is required"
        )
    game_path = (
        Path(games_csv).expanduser().resolve() if games_csv is not None else None
    )
    event_path = (
        Path(events_csv).expanduser().resolve() if events_csv is not None else None
    )
    for label, path in (("games", game_path), ("events", event_path)):
        if path is not None and not path.is_file():
            raise RetrosheetIngestionError(
                f"Retrosheet {label} input CSV does not exist: {path}"
            )

    if collected_at is None:
        collected_at = datetime.now(timezone.utc)
    if not isinstance(collected_at, datetime):
        raise RetrosheetIngestionError("collected_at must be a datetime")

    raw_games = (
        _read_csv(game_path, REQUIRED_RETROSHEET_GAME_COLUMNS, "games")
        if game_path is not None
        else []
    )
    raw_events = (
        _read_csv(event_path, REQUIRED_RETROSHEET_EVENT_COLUMNS, "events")
        if event_path is not None
        else []
    )
    parsed_dates = [
        _row_date(row.get("game_date"), row_number)
        for rows in (raw_games, raw_events)
        for row_number, row in enumerate(rows, start=2)
    ]
    start = min(parsed_dates)
    end = max(parsed_dates)
    effective_as_of_date = (
        _parse_date(as_of_date, "as_of_date") if as_of_date is not None else end
    )
    if effective_as_of_date < end:
        raise RetrosheetIngestionError(
            "as_of_date must not be before the latest game_date in the CSV inputs"
        )

    games = tuple(
        _normalize_game(
            row,
            row_number=row_number,
            as_of_date=effective_as_of_date,
            collected_at=collected_at,
        )
        for row_number, row in enumerate(raw_games, start=2)
    )
    events = tuple(
        _normalize_event(
            row,
            row_number=row_number,
            as_of_date=effective_as_of_date,
            collected_at=collected_at,
        )
        for row_number, row in enumerate(raw_events, start=2)
    )
    source_types = {row.source_type for row in (*games, *events)}
    if len(source_types) != 1:
        raise RetrosheetIngestionError(
            "all Retrosheet rows must use the same source_type for one manifest"
        )
    manifest_source_type = source_types.pop()

    any_write = write_raw or write_normalized or write_manifest_file
    if any_write and output_dir is None:
        raise RetrosheetIngestionError(
            "output_dir is required when any write is requested"
        )

    raw_game_output_path: Path | None = None
    raw_event_output_path: Path | None = None
    normalized_game_output_path: Path | None = None
    normalized_event_output_path: Path | None = None
    manifest_output_path: Path | None = None
    destinations: list[Path] = []
    output_root: Path | None = None
    stem = f"retrosheet_{start.isoformat()}_{end.isoformat()}"
    if output_dir is not None:
        output_root = Path(output_dir).expanduser().resolve()
        if write_raw and game_path is not None:
            raw_game_output_path = output_root / "raw" / f"{stem}_games.csv"
            destinations.append(raw_game_output_path)
        if write_raw and event_path is not None:
            raw_event_output_path = output_root / "raw" / f"{stem}_events.csv"
            destinations.append(raw_event_output_path)
        if write_normalized and games:
            normalized_game_output_path = (
                output_root / "normalized" / f"{stem}_games.jsonl"
            )
            destinations.append(normalized_game_output_path)
        if write_normalized and events:
            normalized_event_output_path = (
                output_root / "normalized" / f"{stem}_events.jsonl"
            )
            destinations.append(normalized_event_output_path)
        if write_manifest_file:
            manifest_output_path = (
                output_root / "manifests" / f"{stem}.manifest.json"
            )
            destinations.append(manifest_output_path)
        _check_output_collisions(destinations, overwrite)

    input_paths = [path for path in (game_path, event_path) if path is not None]
    file_specs: list[tuple[str, Path, int, Path | None]] = []
    if game_path is not None:
        file_specs.append(
            ("games", game_path, len(games), raw_game_output_path)
        )
    if event_path is not None:
        file_specs.append(
            ("events", event_path, len(events), raw_event_output_path)
        )
    checksums = [
        (label, compute_file_sha256(path)) for label, path, _, _ in file_specs
    ]
    manifest_raw_path = (
        output_root / "raw"
        if write_raw and output_root is not None
        else _input_root(input_paths)
    )
    manifest_normalized_path = (
        output_root / "normalized"
        if write_normalized and output_root is not None
        else None
    )
    manifest = MLBSourceManifest(
        source_name=RETROSHEET_SOURCE_NAME,
        source_type=manifest_source_type,
        data_domain=MLBDataDomain.RETROSHEET,
        collected_at=collected_at,
        raw_path=manifest_raw_path,
        schema_version=RETROSHEET_SCHEMA_VERSION,
        date_range_start=start,
        date_range_end=end,
        as_of_date=effective_as_of_date,
        normalized_path=manifest_normalized_path,
        checksum=_combined_checksum(checksums),
        row_count=len(games) + len(events),
        file_count=len(file_specs),
        generated_by="courtvision.sports.mlb.data.retrosheet_ingestion",
        notes=("Local Retrosheet-style historical ingestion prototype.",),
        warnings=(
            "Historical research use only.",
            "No training rows, joins, or rolling features were generated.",
        ),
        files=tuple(
            MLBSourceFileRecord(
                path=raw_output or path,
                checksum=checksum,
                row_count=row_count,
                byte_size=path.stat().st_size,
                content_type="text/csv",
            )
            for (label, path, row_count, raw_output), (_, checksum) in zip(
                file_specs, checksums, strict=True
            )
        ),
    )
    validate_source_manifest(manifest).raise_for_errors()

    if any_write:
        for destination in destinations:
            destination.parent.mkdir(parents=True, exist_ok=True)
        if raw_game_output_path is not None and game_path is not None:
            shutil.copyfile(game_path, raw_game_output_path)
        if raw_event_output_path is not None and event_path is not None:
            shutil.copyfile(event_path, raw_event_output_path)
        mode = "w" if overwrite else "x"
        if normalized_game_output_path is not None:
            with normalized_game_output_path.open(
                mode, encoding="utf-8", newline="\n"
            ) as handle:
                for row in games:
                    handle.write(f"{retrosheet_game_row_to_json(row)}\n")
        if normalized_event_output_path is not None:
            with normalized_event_output_path.open(
                mode, encoding="utf-8", newline="\n"
            ) as handle:
                for row in events:
                    handle.write(f"{retrosheet_event_row_to_json(row)}\n")
        if manifest_output_path is not None:
            write_manifest(manifest, manifest_output_path, overwrite=overwrite)

    return RetrosheetIngestionResult(
        games=games,
        events=events,
        manifest=manifest,
        raw_game_output_path=raw_game_output_path,
        raw_event_output_path=raw_event_output_path,
        normalized_game_output_path=normalized_game_output_path,
        normalized_event_output_path=normalized_event_output_path,
        manifest_output_path=manifest_output_path,
    )


__all__ = [
    "REQUIRED_RETROSHEET_EVENT_COLUMNS",
    "REQUIRED_RETROSHEET_GAME_COLUMNS",
    "RETROSHEET_GAME_STATUSES",
    "RETROSHEET_HOME_RUN_EVENT_TYPES",
    "RETROSHEET_SCHEMA_VERSION",
    "RETROSHEET_SOURCE_NAME",
    "RetrosheetEventRow",
    "RetrosheetGameRow",
    "RetrosheetIngestionError",
    "RetrosheetIngestionResult",
    "ingest_local_retrosheet_csvs",
    "retrosheet_event_row_to_dict",
    "retrosheet_event_row_to_json",
    "retrosheet_game_row_to_dict",
    "retrosheet_game_row_to_json",
]
