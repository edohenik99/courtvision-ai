"""Controlled local MLB home-run odds snapshot ingestion.

This module reads caller-supplied CSV files only. It performs no network
access, downloads, scoring, model work, or production promotion. Normalized
prices are market references for historical research only.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Final, Mapping

from courtvision.sports.mlb.data_manifest import (
    MLBDataDomain,
    MLBSourceFileRecord,
    MLBSourceManifest,
    compute_file_sha256,
    validate_source_manifest,
)
from courtvision.sports.mlb.player_name_normalization import normalize_mlb_player_name


ODDS_SNAPSHOT_SCHEMA_VERSION: Final = "1.0"
ODDS_SNAPSHOT_MANIFEST_SOURCE: Final = "local_mlb_hr_odds_snapshot"
HOME_RUN_MARKET_TYPE: Final = "home_run"
REQUIRED_ODDS_COLUMNS: Final = frozenset(
    {"game_date", "market_type", "american_odds"}
)
_HOME_RUN_MARKET_ALIASES: Final = frozenset(
    {
        "home run",
        "home runs",
        "home_run",
        "home_runs",
        "homer",
        "hr",
        "player home run",
        "player_home_run",
        "player to hit a home run",
        "to hit a home run",
    }
)


class OddsSnapshotIngestionError(ValueError):
    """Raised when a local odds snapshot cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class MLBOddsSnapshotRow:
    """One normalized local market-reference snapshot."""

    game_date: date
    player_id: str | None
    player_name: str | None
    team: str | None
    opponent: str | None
    market_type: str
    sportsbook: str
    american_odds: int
    decimal_odds: float
    implied_probability: float
    odds_collected_at: datetime
    game_id: str | None = None
    event_start_time: datetime | None = None
    home_team: str | None = None
    away_team: str | None = None
    provider: str | None = None
    source_type: str = "historical"
    market_label: str | None = None
    selection_name: str | None = None
    raw_row_hash: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class MLBOddsSnapshotIngestionResult:
    """Parsed rows, immutable provenance, and recoverable row diagnostics."""

    rows: tuple[MLBOddsSnapshotRow, ...]
    manifest: MLBSourceManifest
    rejected_row_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _optional_text(value: object) -> str | None:
    text = "" if value is None else str(value).strip()
    return text or None


def _required_text(value: object, field_name: str, row_number: int) -> str:
    text = _optional_text(value)
    if text is None:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: {field_name} must not be empty"
        )
    return text


def normalize_market_type(value: object) -> str:
    """Normalize known HR labels while retaining other visible market labels."""

    text = "" if value is None else str(value).strip().casefold()
    text = re.sub(r"[\s_\-/]+", " ", text)
    text = re.sub(r"[^\w ]+", "", text).strip()
    if text in {alias.replace("_", " ") for alias in _HOME_RUN_MARKET_ALIASES}:
        return HOME_RUN_MARKET_TYPE
    return text.replace(" ", "_")


def normalize_player_name(value: object) -> str | None:
    """Return the canonical MLB player-name comparison key."""

    return normalize_mlb_player_name(value) or None


def american_to_decimal(american_odds: int) -> float:
    """Convert a valid American price to decimal odds."""

    if isinstance(american_odds, bool) or not isinstance(american_odds, int):
        raise OddsSnapshotIngestionError("american_odds must be an integer")
    if -100 < american_odds < 100:
        raise OddsSnapshotIngestionError(
            "american_odds must be at least +100 or at most -100"
        )
    if american_odds > 0:
        return 1.0 + american_odds / 100.0
    return 1.0 + 100.0 / abs(american_odds)


def american_to_implied_probability(american_odds: int) -> float:
    """Derive market-implied probability from a valid American price."""

    return 1.0 / american_to_decimal(american_odds)


def _parse_american_odds(value: object, row_number: int) -> int:
    text = _required_text(value, "american_odds", row_number)
    try:
        parsed_float = float(text)
    except ValueError as exc:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: american_odds must be an integer"
        ) from exc
    if not math.isfinite(parsed_float) or not parsed_float.is_integer():
        raise OddsSnapshotIngestionError(
            f"row {row_number}: american_odds must be an integer"
        )
    parsed = int(parsed_float)
    try:
        american_to_decimal(parsed)
    except OddsSnapshotIngestionError as exc:
        raise OddsSnapshotIngestionError(f"row {row_number}: {exc}") from exc
    return parsed


def _parse_date(value: object, row_number: int) -> date:
    text = _required_text(value, "game_date", row_number)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: game_date must be an ISO date (YYYY-MM-DD)"
        ) from exc


def _parse_datetime(
    value: object, field_name: str, row_number: int, *, required: bool
) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        if required:
            raise OddsSnapshotIngestionError(
                f"row {row_number}: {field_name} must not be empty"
            )
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: {field_name} must be an ISO datetime"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: {field_name} must include a UTC offset"
        )
    return parsed


def _raw_row_hash(row: Mapping[str, object]) -> str:
    payload = json.dumps(
        dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_row(
    raw_row: Mapping[str, object], *, row_number: int
) -> MLBOddsSnapshotRow:
    player_id = _optional_text(raw_row.get("player_id"))
    player_name = _optional_text(raw_row.get("player_name")) or _optional_text(
        raw_row.get("selection_name")
    )
    if player_id is None and player_name is None:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: player_id or player_name is required"
        )

    sportsbook = _optional_text(raw_row.get("sportsbook")) or _optional_text(
        raw_row.get("source_name")
    )
    if sportsbook is None:
        raise OddsSnapshotIngestionError(
            f"row {row_number}: sportsbook or source_name is required"
        )
    collected_value = raw_row.get("odds_collected_at") or raw_row.get("as_of")
    collected_at = _parse_datetime(
        collected_value, "odds_collected_at or as_of", row_number, required=True
    )
    assert collected_at is not None
    american_odds = _parse_american_odds(raw_row.get("american_odds"), row_number)
    decimal_odds = american_to_decimal(american_odds)
    warnings: list[str] = []
    supplied_decimal = _optional_text(raw_row.get("decimal_odds"))
    if supplied_decimal is not None:
        try:
            parsed_decimal = float(supplied_decimal)
        except ValueError as exc:
            raise OddsSnapshotIngestionError(
                f"row {row_number}: decimal_odds must be numeric or empty"
            ) from exc
        if not math.isfinite(parsed_decimal) or parsed_decimal <= 1.0:
            raise OddsSnapshotIngestionError(
                f"row {row_number}: decimal_odds must be finite and greater than 1"
            )
        if not math.isclose(parsed_decimal, decimal_odds, rel_tol=0.0, abs_tol=0.005):
            warnings.append(
                "supplied decimal_odds differed from the American-price derivation; "
                "the derived market reference was used"
            )
    if player_id is None:
        warnings.append("player_id is missing; only an unambiguous name fallback is safe")
    if _optional_text(raw_row.get("game_id")) is None:
        warnings.append("game_id is missing; only an unambiguous fallback is safe")

    return MLBOddsSnapshotRow(
        game_date=_parse_date(raw_row.get("game_date"), row_number),
        game_id=_optional_text(raw_row.get("game_id")),
        player_id=player_id,
        player_name=player_name,
        team=_optional_text(raw_row.get("team")),
        opponent=_optional_text(raw_row.get("opponent")),
        market_type=normalize_market_type(raw_row.get("market_type")),
        sportsbook=sportsbook,
        american_odds=american_odds,
        decimal_odds=decimal_odds,
        implied_probability=american_to_implied_probability(american_odds),
        odds_collected_at=collected_at,
        event_start_time=_parse_datetime(
            raw_row.get("event_start_time"),
            "event_start_time",
            row_number,
            required=False,
        ),
        home_team=_optional_text(raw_row.get("home_team")),
        away_team=_optional_text(raw_row.get("away_team")),
        provider=_optional_text(raw_row.get("provider")),
        source_type=_optional_text(raw_row.get("source_type")) or "historical",
        market_label=_optional_text(raw_row.get("market_label")),
        selection_name=_optional_text(raw_row.get("selection_name")),
        raw_row_hash=_raw_row_hash(raw_row),
        warnings=tuple(warnings),
    )


def ingest_local_odds_snapshot_csv(
    input_csv: str | Path,
) -> MLBOddsSnapshotIngestionResult:
    """Parse one local odds CSV without writing files or using the network."""

    source_path = Path(input_csv).expanduser().resolve()
    if not source_path.is_file():
        raise OddsSnapshotIngestionError(
            f"Odds snapshot input CSV does not exist: {source_path}"
        )
    try:
        with source_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise OddsSnapshotIngestionError(
                    "Odds snapshot CSV is missing a header row"
                )
            headers = set(reader.fieldnames)
            missing = sorted(REQUIRED_ODDS_COLUMNS - headers)
            if missing:
                raise OddsSnapshotIngestionError(
                    "Odds snapshot CSV is missing required columns: "
                    + ", ".join(missing)
                )
            if not ({"player_id", "player_name", "selection_name"} & headers):
                raise OddsSnapshotIngestionError(
                    "Odds snapshot CSV requires player_id, player_name, or selection_name"
                )
            if not ({"sportsbook", "source_name"} & headers):
                raise OddsSnapshotIngestionError(
                    "Odds snapshot CSV requires sportsbook or source_name"
                )
            if not ({"odds_collected_at", "as_of"} & headers):
                raise OddsSnapshotIngestionError(
                    "Odds snapshot CSV requires odds_collected_at or as_of"
                )
            raw_rows = []
            for row_number, row in enumerate(reader, start=2):
                if None in row:
                    raise OddsSnapshotIngestionError(
                        f"row {row_number}: Odds snapshot CSV has extra values"
                    )
                raw_rows.append((row_number, dict(row)))
    except OddsSnapshotIngestionError:
        raise
    except (OSError, UnicodeError, csv.Error) as exc:
        raise OddsSnapshotIngestionError(
            f"could not read Odds snapshot CSV {source_path}: {exc}"
        ) from exc
    if not raw_rows:
        raise OddsSnapshotIngestionError("Odds snapshot CSV contains no data rows")

    rows: list[MLBOddsSnapshotRow] = []
    warnings: list[str] = []
    rejected = 0
    for row_number, raw_row in raw_rows:
        try:
            rows.append(_normalize_row(raw_row, row_number=row_number))
        except OddsSnapshotIngestionError as exc:
            rejected += 1
            warnings.append(f"{exc}; row rejected")
    if not rows:
        raise OddsSnapshotIngestionError(
            "Odds snapshot CSV contains no valid rows; " + "; ".join(warnings)
        )

    start = min(row.game_date for row in rows)
    end = max(row.game_date for row in rows)
    checksum = compute_file_sha256(source_path)
    manifest = MLBSourceManifest(
        source_name=ODDS_SNAPSHOT_MANIFEST_SOURCE,
        source_type="historical",
        data_domain=MLBDataDomain.ODDS,
        collected_at=max(row.odds_collected_at for row in rows),
        raw_path=source_path,
        schema_version=ODDS_SNAPSHOT_SCHEMA_VERSION,
        date_range_start=start,
        date_range_end=end,
        checksum=checksum,
        row_count=len(rows),
        file_count=1,
        generated_by="courtvision.sports.mlb.data.odds_snapshot_ingestion",
        notes=("Local MLB HR odds snapshot ingestion trial.",),
        warnings=(
            "Historical research only; market reference only.",
            *warnings,
        ),
        files=(
            MLBSourceFileRecord(
                path=source_path,
                checksum=checksum,
                row_count=len(rows),
                byte_size=source_path.stat().st_size,
                content_type="text/csv",
                warnings=tuple(warnings),
            ),
        ),
    )
    validate_source_manifest(manifest).raise_for_errors()
    return MLBOddsSnapshotIngestionResult(
        rows=tuple(rows),
        manifest=manifest,
        rejected_row_count=rejected,
        warnings=tuple(warnings),
    )


__all__ = [
    "HOME_RUN_MARKET_TYPE",
    "MLBOddsSnapshotIngestionResult",
    "MLBOddsSnapshotRow",
    "ODDS_SNAPSHOT_SCHEMA_VERSION",
    "OddsSnapshotIngestionError",
    "american_to_decimal",
    "american_to_implied_probability",
    "ingest_local_odds_snapshot_csv",
    "normalize_market_type",
    "normalize_player_name",
]
