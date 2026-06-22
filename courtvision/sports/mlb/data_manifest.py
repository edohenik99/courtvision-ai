"""Local MLB storage layout and immutable source-manifest contracts.

This module is storage/schema scaffolding only.  It performs no acquisition,
provider selection, normalization, feature generation, or runtime promotion.
Manifest serialization fails closed so provenance cannot imply an approved
production or wagering use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from typing import Final, Sequence


MLB_MANIFEST_SCHEMA_VERSION: Final = "1.0"
MLB_SPORT: Final = "MLB"
MLB_LEAGUE: Final = "MLB"
NOT_APPROVED: Final = "not_approved"


class MLBSourceType(StrEnum):
    """Supported provenance classes for local MLB data."""

    PUBLIC = "public"
    FREE = "free"
    PAID = "paid"
    MANUAL = "manual"
    SAMPLE = "sample"
    MOCK = "mock"
    HISTORICAL = "historical"
    STATIC = "static"


class MLBDataDomain(StrEnum):
    """Supported raw and derived MLB storage domains."""

    STATCAST = "statcast"
    RETROSHEET = "retrosheet"
    LAHMAN = "lahman"
    WEATHER = "weather"
    ODDS = "odds"
    LINEUPS = "lineups"
    PROBABLE_PITCHERS = "probable_pitchers"
    BALLPARK = "ballpark"
    RESEARCH = "research"
    TRAINING = "training"


SUPPORTED_MLB_SOURCE_TYPES: Final = frozenset(item.value for item in MLBSourceType)
SUPPORTED_MLB_DATA_DOMAINS: Final = frozenset(item.value for item in MLBDataDomain)
RAW_MLB_DATA_DOMAINS: Final = frozenset(
    {
        MLBDataDomain.STATCAST.value,
        MLBDataDomain.RETROSHEET.value,
        MLBDataDomain.LAHMAN.value,
        MLBDataDomain.WEATHER.value,
        MLBDataDomain.ODDS.value,
        MLBDataDomain.LINEUPS.value,
        MLBDataDomain.PROBABLE_PITCHERS.value,
        MLBDataDomain.BALLPARK.value,
    }
)


class MLBManifestValidationError(ValueError):
    """Raised when an MLB source manifest fails the safety contract."""


def _tuple_of_text(values: Sequence[str] | str, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    elif isinstance(values, bytes):
        raise TypeError(f"{field_name} must be a sequence of strings")
    normalized = tuple(values)
    if any(not isinstance(value, str) for value in normalized):
        raise TypeError(f"{field_name} must contain only strings")
    return normalized


def _enum_text(value: object) -> object:
    return value.value if isinstance(value, StrEnum) else value


def _path_text(value: str | Path | None) -> str | None:
    return str(value) if value is not None else None


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class MLBSourceFileRecord:
    """Provenance for one immutable file referenced by a source manifest."""

    path: str | Path
    checksum: str | None = None
    row_count: int | None = None
    byte_size: int | None = None
    content_type: str | None = None
    source_version: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )


@dataclass(frozen=True, slots=True)
class MLBDataPartition:
    """A dated or seasonal raw/derived partition without materializing data."""

    partition_key: str
    data_domain: MLBDataDomain | str
    season: int | None = None
    date_range_start: date | None = None
    date_range_end: date | None = None
    raw_path: str | Path | None = None
    normalized_path: str | Path | None = None
    schema_version: str | None = None


@dataclass(frozen=True, slots=True)
class MLBStorageLayout:
    """Expected local directories rooted at a CourtVision checkout."""

    project_root: Path
    raw_statcast: Path
    raw_retrosheet: Path
    raw_lahman: Path
    raw_weather: Path
    raw_odds: Path
    raw_lineups: Path
    raw_probable_pitchers: Path
    raw_ballpark: Path
    normalized: Path
    research_hr: Path
    training_hr: Path
    manifests: Path

    @property
    def directories(self) -> tuple[Path, ...]:
        """Return every managed MLB directory in stable order."""

        return (
            self.raw_statcast,
            self.raw_retrosheet,
            self.raw_lahman,
            self.raw_weather,
            self.raw_odds,
            self.raw_lineups,
            self.raw_probable_pitchers,
            self.raw_ballpark,
            self.normalized,
            self.research_hr,
            self.training_hr,
            self.manifests,
        )


@dataclass(frozen=True, slots=True)
class MLBSourceManifest:
    """Immutable MLB source provenance and leakage-audit metadata."""

    source_name: str
    source_type: MLBSourceType | str
    data_domain: MLBDataDomain | str
    collected_at: datetime | None
    raw_path: str | Path | None
    schema_version: str
    sport: str = MLB_SPORT
    league: str = MLB_LEAGUE
    season: int | None = None
    date_range_start: date | None = None
    date_range_end: date | None = None
    as_of_date: date | None = None
    provider_name: str | None = None
    normalized_path: str | Path | None = None
    source_version: str | None = None
    checksum: str | None = None
    row_count: int | None = None
    file_count: int | None = None
    generated_by: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    files: tuple[MLBSourceFileRecord, ...] = field(default_factory=tuple)
    partitions: tuple[MLBDataPartition, ...] = field(default_factory=tuple)
    approval_status: str = NOT_APPROVED
    eligible_for_betting: bool = False
    kelly_eligible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "notes", _tuple_of_text(self.notes, "notes"))
        object.__setattr__(
            self, "warnings", _tuple_of_text(self.warnings, "warnings")
        )
        files = tuple(self.files)
        if any(not isinstance(item, MLBSourceFileRecord) for item in files):
            raise TypeError("files must contain only MLBSourceFileRecord values")
        object.__setattr__(self, "files", files)
        partitions = tuple(self.partitions)
        if any(not isinstance(item, MLBDataPartition) for item in partitions):
            raise TypeError("partitions must contain only MLBDataPartition values")
        object.__setattr__(self, "partitions", partitions)


@dataclass(frozen=True, slots=True)
class MLBManifestValidationResult:
    """Deterministic validation result for manifest diagnostics and tests."""

    is_valid: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    def raise_for_errors(self) -> None:
        if not self.is_valid:
            raise MLBManifestValidationError("; ".join(self.errors))


def get_mlb_storage_layout(root: str | Path | None = None) -> MLBStorageLayout:
    """Return the expected MLB layout without creating any directory."""

    project_root = (
        Path(root).expanduser().resolve()
        if root is not None
        else Path(__file__).resolve().parents[3]
    )
    raw = project_root / "data" / "raw" / "mlb"
    return MLBStorageLayout(
        project_root=project_root,
        raw_statcast=raw / "statcast",
        raw_retrosheet=raw / "retrosheet",
        raw_lahman=raw / "lahman",
        raw_weather=raw / "weather",
        raw_odds=raw / "odds",
        raw_lineups=raw / "lineups",
        raw_probable_pitchers=raw / "probable_pitchers",
        raw_ballpark=raw / "ballpark",
        normalized=project_root / "data" / "normalized" / "mlb",
        research_hr=project_root / "data" / "research" / "mlb" / "hr",
        training_hr=project_root / "data" / "training" / "mlb" / "hr",
        manifests=project_root / "data" / "manifests" / "mlb",
    )


def ensure_mlb_storage_dirs(
    root: str | Path | None = None, dry_run: bool = True
) -> tuple[Path, ...]:
    """Plan MLB directories, creating them only when ``dry_run`` is false."""

    directories = get_mlb_storage_layout(root).directories
    if not dry_run:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    return directories


_SAFE_PATH_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _safe_path_token(value: object, field_name: str) -> str:
    if isinstance(value, (date, datetime)):
        value = value.isoformat()
    text = str(value).strip()
    if not text or ".." in text or not _SAFE_PATH_TOKEN.fullmatch(text):
        raise ValueError(f"{field_name} must be a safe non-empty path token")
    return text


def manifest_path_for(
    source_name: str,
    domain: MLBDataDomain | str,
    season_or_date: int | str | date,
    *,
    root: str | Path | None = None,
) -> Path:
    """Return a deterministic path for a source/domain/partition manifest."""

    domain_value = _enum_text(domain)
    if domain_value not in SUPPORTED_MLB_DATA_DOMAINS:
        raise ValueError(f"unsupported MLB data domain: {domain_value!r}")
    source_token = _safe_path_token(source_name, "source_name")
    partition_token = _safe_path_token(season_or_date, "season_or_date")
    filename = f"{domain_value}-{source_token}-{partition_token}.manifest.json"
    return get_mlb_storage_layout(root).manifests / filename


def _missing_text(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _valid_date(value: object) -> bool:
    return isinstance(value, date) and not isinstance(value, datetime)


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None


def _unsafe_approval_claim(manifest: MLBSourceManifest) -> bool:
    text_values = (
        manifest.source_name,
        manifest.provider_name or "",
        manifest.generated_by or "",
        *manifest.notes,
        *manifest.warnings,
    )
    text = " ".join(text_values).lower()
    approval_terms = ("approved", "approval", "eligible", "ready", "authorized", "cleared")
    restricted_terms = ("production", "betting", "wager", "kelly", "bankroll")
    return any(term in text for term in approval_terms) and any(
        term in text for term in restricted_terms
    )


def validate_source_manifest(manifest: MLBSourceManifest) -> MLBManifestValidationResult:
    """Validate provenance and safety metadata; all ambiguity fails closed."""

    if not isinstance(manifest, MLBSourceManifest):
        return MLBManifestValidationResult(False, ("manifest has invalid type",))

    errors: list[str] = []
    source_type = _enum_text(manifest.source_type)
    data_domain = _enum_text(manifest.data_domain)

    if _missing_text(manifest.source_name):
        errors.append("source_name is required")
    if _missing_text(source_type):
        errors.append("source_type is required")
    elif source_type not in SUPPORTED_MLB_SOURCE_TYPES:
        errors.append(f"unsupported source_type: {source_type!r}")
    if _missing_text(data_domain):
        errors.append("data_domain is required")
    elif data_domain not in SUPPORTED_MLB_DATA_DOMAINS:
        errors.append(f"unsupported data_domain: {data_domain!r}")
    if manifest.sport != MLB_SPORT:
        errors.append("sport must be 'MLB'")
    if manifest.league != MLB_LEAGUE:
        errors.append("league must be 'MLB'")
    if _missing_text(manifest.schema_version):
        errors.append("schema_version is required")
    if not isinstance(manifest.collected_at, datetime):
        errors.append("collected_at must be a datetime")
    if manifest.as_of_date is not None and not _valid_date(manifest.as_of_date):
        errors.append("as_of_date must be a date")
    if manifest.date_range_start is not None and not _valid_date(
        manifest.date_range_start
    ):
        errors.append("date_range_start must be a date")
    if manifest.date_range_end is not None and not _valid_date(manifest.date_range_end):
        errors.append("date_range_end must be a date")
    if (
        _valid_date(manifest.date_range_start)
        and _valid_date(manifest.date_range_end)
        and manifest.date_range_start > manifest.date_range_end
    ):
        errors.append("date_range_start must not be after date_range_end")
    if manifest.season is not None and (
        isinstance(manifest.season, bool)
        or not isinstance(manifest.season, int)
        or manifest.season < 1876
    ):
        errors.append("season must be a valid MLB season")

    raw_path_required = (
        data_domain in RAW_MLB_DATA_DOMAINS
        or source_type == MLBSourceType.HISTORICAL.value
    )
    if raw_path_required and (
        manifest.raw_path is None or not str(manifest.raw_path).strip()
    ):
        errors.append("raw_path is required for raw or historical data")

    for field_name in ("row_count", "file_count"):
        value = getattr(manifest, field_name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            errors.append(f"{field_name} must be a non-negative integer")
    if manifest.checksum is not None and not _valid_sha256(manifest.checksum):
        errors.append("checksum must be a 64-character SHA-256 digest")

    for index, record in enumerate(manifest.files):
        prefix = f"files[{index}]"
        if not str(record.path).strip():
            errors.append(f"{prefix}.path is required")
        if record.checksum is not None and not _valid_sha256(record.checksum):
            errors.append(f"{prefix}.checksum must be a 64-character SHA-256 digest")
        for field_name in ("row_count", "byte_size"):
            value = getattr(record, field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                errors.append(f"{prefix}.{field_name} must be a non-negative integer")

    for index, partition in enumerate(manifest.partitions):
        prefix = f"partitions[{index}]"
        partition_domain = _enum_text(partition.data_domain)
        if _missing_text(partition.partition_key):
            errors.append(f"{prefix}.partition_key is required")
        if partition_domain not in SUPPORTED_MLB_DATA_DOMAINS:
            errors.append(f"{prefix}.data_domain is unsupported")
        if (
            _valid_date(partition.date_range_start)
            and _valid_date(partition.date_range_end)
            and partition.date_range_start > partition.date_range_end
        ):
            errors.append(f"{prefix} has an invalid date range")

    if manifest.approval_status != NOT_APPROVED:
        errors.append("approval_status must be 'not_approved'")
    if manifest.eligible_for_betting is not False:
        errors.append("eligible_for_betting must be false")
    if manifest.kelly_eligible is not False:
        errors.append("kelly_eligible must be false")
    if _unsafe_approval_claim(manifest):
        errors.append("manifest text cannot claim production or wagering approval")

    return MLBManifestValidationResult(not errors, tuple(errors))


def _file_record_to_dict(record: MLBSourceFileRecord) -> dict[str, object]:
    return {
        "path": _path_text(record.path),
        "checksum": record.checksum,
        "row_count": record.row_count,
        "byte_size": record.byte_size,
        "content_type": record.content_type,
        "source_version": record.source_version,
        "warnings": list(record.warnings),
    }


def _partition_to_dict(partition: MLBDataPartition) -> dict[str, object]:
    return {
        "partition_key": partition.partition_key,
        "data_domain": _enum_text(partition.data_domain),
        "season": partition.season,
        "date_range_start": _iso(partition.date_range_start),
        "date_range_end": _iso(partition.date_range_end),
        "raw_path": _path_text(partition.raw_path),
        "normalized_path": _path_text(partition.normalized_path),
        "schema_version": partition.schema_version,
    }


def manifest_to_dict(manifest: MLBSourceManifest) -> dict[str, object]:
    """Return the stable manifest schema after default-deny validation."""

    validate_source_manifest(manifest).raise_for_errors()
    return {
        "source_name": manifest.source_name,
        "source_type": _enum_text(manifest.source_type),
        "sport": manifest.sport,
        "league": manifest.league,
        "data_domain": _enum_text(manifest.data_domain),
        "season": manifest.season,
        "date_range_start": _iso(manifest.date_range_start),
        "date_range_end": _iso(manifest.date_range_end),
        "collected_at": _iso(manifest.collected_at),
        "as_of_date": _iso(manifest.as_of_date),
        "provider_name": manifest.provider_name,
        "raw_path": _path_text(manifest.raw_path),
        "normalized_path": _path_text(manifest.normalized_path),
        "schema_version": manifest.schema_version,
        "source_version": manifest.source_version,
        "checksum": manifest.checksum,
        "row_count": manifest.row_count,
        "file_count": manifest.file_count,
        "generated_by": manifest.generated_by,
        "notes": list(manifest.notes),
        "warnings": list(manifest.warnings),
        "files": [_file_record_to_dict(record) for record in manifest.files],
        "partitions": [
            _partition_to_dict(partition) for partition in manifest.partitions
        ],
        "approval_status": manifest.approval_status,
        "eligible_for_betting": manifest.eligible_for_betting,
        "kelly_eligible": manifest.kelly_eligible,
    }


def manifest_to_json(
    manifest: MLBSourceManifest, *, indent: int | None = 2
) -> str:
    """Return deterministic, UTF-8-safe JSON for a validated manifest."""

    return json.dumps(
        manifest_to_dict(manifest),
        ensure_ascii=False,
        indent=indent,
        sort_keys=True,
    )


def write_manifest(
    manifest: MLBSourceManifest,
    path: str | Path,
    overwrite: bool = False,
) -> Path:
    """Write a validated manifest without creating parent directories."""

    destination = Path(path)
    payload = manifest_to_json(manifest)
    mode = "w" if overwrite else "x"
    with destination.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(f"{payload}\n")
    return destination


def compute_file_sha256(path: str | Path) -> str:
    """Return the lowercase SHA-256 digest of the exact file bytes."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "MLB_MANIFEST_SCHEMA_VERSION",
    "MLBDataDomain",
    "MLBDataPartition",
    "MLBManifestValidationError",
    "MLBManifestValidationResult",
    "MLBSourceFileRecord",
    "MLBSourceManifest",
    "MLBSourceType",
    "MLBStorageLayout",
    "RAW_MLB_DATA_DOMAINS",
    "SUPPORTED_MLB_DATA_DOMAINS",
    "SUPPORTED_MLB_SOURCE_TYPES",
    "compute_file_sha256",
    "ensure_mlb_storage_dirs",
    "get_mlb_storage_layout",
    "manifest_path_for",
    "manifest_to_dict",
    "manifest_to_json",
    "validate_source_manifest",
    "write_manifest",
]
