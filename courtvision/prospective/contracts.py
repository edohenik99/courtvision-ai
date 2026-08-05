"""Immutable contracts for prospective NBA paper-trial cohort identity.

This module is intentionally standard-library-only and has no dependency on
the prediction, lifecycle, OfficialPick, grading, or evaluation packages.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any


MODEL_BUILD_SCHEMA_VERSION = 1
PROSPECTIVE_COHORT_SCHEMA_VERSION = 1
COHORT_ID_DIGEST_LENGTH = 20


class ProspectiveContractError(ValueError):
    """Raised when a prospective paper-trial contract is invalid."""


class ProspectiveProvenanceError(ProspectiveContractError):
    """Raised when provenance cannot be captured or verified safely."""


class ProspectiveDirtyTreeError(ProspectiveProvenanceError):
    """Raised when a dirty working tree blocks cohort activation."""


class ProspectiveMissingArtifactError(ProspectiveProvenanceError):
    """Raised when a required model artifact is absent or not a file."""


class ProspectiveDigestMismatchError(ProspectiveProvenanceError):
    """Raised when claimed and recomputed evidence digests disagree."""


class ProspectiveSecretConfigurationError(ProspectiveContractError):
    """Raised when configuration contains a credential-like key."""


class ProspectiveUnverifiedModelError(ProspectiveContractError):
    """Raised when verified model-build evidence is missing or incomplete."""


def _canonical_json_value(value: Any, path: str = "$") -> Any:
    if isinstance(value, FrozenJSONMapping):
        return value.to_dict()
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ProspectiveContractError(
                f"{path}: non-finite configuration numbers are not supported"
            )
        return value
    if isinstance(value, Mapping):
        keys = tuple(value)
        if any(not isinstance(key, str) for key in keys):
            raise ProspectiveContractError(
                f"{path}: canonical mapping keys must be strings"
            )
        return {
            key: _canonical_json_value(value[key], f"{path}.{key}")
            for key in sorted(keys)
        }
    if isinstance(value, (list, tuple)):
        return [
            _canonical_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ProspectiveContractError(
        f"{path}: unsupported canonical JSON type {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON with sorted keys and no whitespace."""

    normalized = _canonical_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the lowercase SHA-256 of canonical JSON content."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenJSONMapping(value)
    if isinstance(value, list):
        return tuple(_freeze_json_value(item) for item in value)
    return value


class FrozenJSONMapping(  # pyright: ignore[reportGeneralTypeIssues]
    bytes,
    Mapping[str, Any],
):
    """Immutable canonical JSON mapping with detached serialization views.

    Canonical JSON bytes are the entire instance value, so neither direct nor
    ``object.__setattr__`` mutation can replace a hidden backing mapping.
    ``to_dict()`` returns a new JSON-compatible container for serialization.
    """

    __slots__ = ()

    def __new__(cls, values: Mapping[str, Any]) -> "FrozenJSONMapping":
        normalized = _canonical_json_value(values)
        if not isinstance(normalized, dict):
            raise ProspectiveContractError("frozen JSON value must be a mapping")
        return bytes.__new__(cls, canonical_json_bytes(normalized))

    def to_dict(self) -> dict[str, Any]:
        value = json.loads(bytes.decode(self, "utf-8"))
        if not isinstance(value, dict):
            raise ProspectiveContractError("frozen JSON mapping storage is invalid")
        return value

    def __getitem__(self, key: object) -> Any:
        if not isinstance(key, str):
            raise TypeError("FrozenJSONMapping keys must be strings")
        values = self.to_dict()
        if key not in values:
            raise KeyError(key)
        return _freeze_json_value(values[key])

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and key in self.to_dict()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        try:
            return canonical_json_bytes(self) == canonical_json_bytes(other)
        except ProspectiveContractError:
            return False

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # pyright: ignore[reportAssignmentType]

    def __repr__(self) -> str:
        return f"FrozenJSONMapping(keys={tuple(self)!r})"

    def __copy__(self) -> "FrozenJSONMapping":
        return type(self)(self.to_dict())

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        copied = deepcopy(self.to_dict(), memo)
        memo[id(self)] = copied
        return copied


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProspectiveContractError(f"{field_name} is required")
    return value.strip()


def _lowercase_sha256(value: object, field_name: str) -> str:
    digest = _required_text(value, field_name)
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ProspectiveContractError(
            f"{field_name} must be 64 lowercase hexadecimal characters"
        )
    return digest


def _commit_sha(value: object) -> str:
    sha = _required_text(value, "commit_sha")
    if re.fullmatch(r"[0-9a-f]{40,64}", sha) is None:
        raise ProspectiveContractError(
            "commit_sha must be a lowercase Git commit object ID"
        )
    return sha


def _repository_relative_path(value: object) -> str:
    raw = _required_text(value, "repository_relative_path").replace("\\", "/")
    if raw.startswith("/") or raw.startswith("//") or re.match(r"^[A-Za-z]:", raw):
        raise ProspectiveContractError(
            "repository_relative_path must not be absolute"
        )
    if any(part == ".." for part in raw.split("/")):
        raise ProspectiveContractError(
            "repository_relative_path must not traverse outside the repository"
        )
    normalized = str(PurePosixPath(raw))
    if normalized in {"", "."}:
        raise ProspectiveContractError("repository_relative_path is required")
    return normalized


def _required_date(value: object, field_name: str) -> date:
    if value is None:
        raise ProspectiveUnverifiedModelError(f"{field_name} is required")
    if isinstance(value, datetime):
        raise ProspectiveContractError(f"{field_name} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError as exc:
            raise ProspectiveContractError(
                f"{field_name} must be an ISO-8601 date"
            ) from exc
    raise ProspectiveContractError(f"{field_name} must be a date")


def _required_utc_datetime(value: object, field_name: str) -> datetime:
    if value is None:
        raise ProspectiveUnverifiedModelError(f"{field_name} is required")
    parsed: datetime
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ProspectiveContractError(
                f"{field_name} must be an ISO-8601 UTC datetime"
            ) from exc
    else:
        raise ProspectiveContractError(f"{field_name} must be a datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProspectiveUnverifiedModelError(
            f"{field_name} must be timezone-aware UTC; file timestamps are not evidence"
        )
    if parsed.utcoffset() != timedelta(0):
        raise ProspectiveContractError(f"{field_name} must use a UTC offset")
    return parsed.astimezone(UTC)


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _sorted_unique_texts(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ProspectiveContractError(f"{field_name} must be a collection of strings")
    normalized = tuple(_required_text(item, field_name) for item in value)
    if not normalized:
        raise ProspectiveContractError(f"{field_name} must not be empty")
    return tuple(sorted(set(normalized)))


_SECRET_KEY_TERMS = {
    "apikey",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "token",
}


def _is_secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    pieces = tuple(piece for piece in normalized.split("_") if piece)
    compact = "".join(pieces)
    if any(piece in _SECRET_KEY_TERMS for piece in pieces):
        return True
    if compact in _SECRET_KEY_TERMS:
        return True
    if any(
        compact.endswith(term)
        for term in ("apikey", "credential", "credentials", "password", "secret", "token")
    ):
        return True
    return any(
        pair in {("api", "key"), ("access", "token"), ("auth", "token"),
                 ("client", "secret"), ("private", "key")}
        for pair in zip(pieces, pieces[1:])
    )


def _reject_secret_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key in value:
            if not isinstance(key, str):
                continue
            if _is_secret_key(key):
                raise ProspectiveSecretConfigurationError(
                    f"credential-like configuration key rejected at {path}.{key}; "
                    "secret values are never captured"
                )
            _reject_secret_keys(value[key], f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_secret_keys(item, f"{path}[{index}]")


@dataclass(frozen=True, slots=True)
class ModelArtifactEntryV1:
    logical_name: str
    repository_relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "logical_name", _required_text(self.logical_name, "logical_name"))
        object.__setattr__(
            self,
            "repository_relative_path",
            _repository_relative_path(self.repository_relative_path),
        )
        object.__setattr__(self, "sha256", _lowercase_sha256(self.sha256, "sha256"))
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ProspectiveContractError("size_bytes must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_name": self.logical_name,
            "repository_relative_path": self.repository_relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class TrainingProvenanceV1:
    training_start_date: date
    training_end_date: date
    training_completed_at_utc: datetime
    training_run_id: str
    training_data_digest: str
    model_build_tool_version: str

    def __post_init__(self) -> None:
        start = _required_date(self.training_start_date, "training_start_date")
        end = _required_date(self.training_end_date, "training_end_date")
        completed = _required_utc_datetime(
            self.training_completed_at_utc, "training_completed_at_utc"
        )
        if end < start:
            raise ProspectiveContractError(
                "training_end_date may not precede training_start_date"
            )
        if completed.date() < end:
            raise ProspectiveUnverifiedModelError(
                "training_completed_at_utc.date() may not precede "
                "training_end_date; timestamps are not inferred or repaired"
            )
        object.__setattr__(self, "training_start_date", start)
        object.__setattr__(self, "training_end_date", end)
        object.__setattr__(self, "training_completed_at_utc", completed)
        object.__setattr__(
            self, "training_run_id", _required_text(self.training_run_id, "training_run_id")
        )
        object.__setattr__(
            self,
            "training_data_digest",
            _lowercase_sha256(self.training_data_digest, "training_data_digest"),
        )
        object.__setattr__(
            self,
            "model_build_tool_version",
            _required_text(self.model_build_tool_version, "model_build_tool_version"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "training_start_date": self.training_start_date.isoformat(),
            "training_end_date": self.training_end_date.isoformat(),
            "training_completed_at_utc": _format_utc(self.training_completed_at_utc),
            "training_run_id": self.training_run_id,
            "training_data_digest": self.training_data_digest,
            "model_build_tool_version": self.model_build_tool_version,
        }


@dataclass(frozen=True, slots=True)
class GitProvenanceV1:
    commit_sha: str
    dirty: bool
    working_tree_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "commit_sha", _commit_sha(self.commit_sha))
        if type(self.dirty) is not bool:
            raise ProspectiveProvenanceError(
                "dirty must be a known boolean; unknown Git state blocks activation"
            )
        object.__setattr__(
            self,
            "working_tree_fingerprint",
            _lowercase_sha256(
                self.working_tree_fingerprint, "working_tree_fingerprint"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "commit_sha": self.commit_sha,
            "dirty": self.dirty,
            "working_tree_fingerprint": self.working_tree_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ConfigurationProvenanceV1:
    canonical_configuration: FrozenJSONMapping
    configuration_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_configuration, Mapping):
            raise ProspectiveContractError("canonical_configuration must be a mapping")
        _reject_secret_keys(self.canonical_configuration)
        frozen = FrozenJSONMapping(self.canonical_configuration)
        expected = canonical_sha256(frozen)
        claimed = _lowercase_sha256(
            self.configuration_digest, "configuration_digest"
        )
        if claimed != expected:
            raise ProspectiveDigestMismatchError(
                "configuration_digest does not match canonical_configuration"
            )
        object.__setattr__(self, "canonical_configuration", frozen)
        object.__setattr__(self, "configuration_digest", claimed)

    @classmethod
    def from_configuration(
        cls, configuration: Mapping[str, Any]
    ) -> "ConfigurationProvenanceV1":
        if not isinstance(configuration, Mapping):
            raise ProspectiveContractError("configuration must be a mapping")
        _reject_secret_keys(configuration)
        frozen = FrozenJSONMapping(configuration)
        return cls(frozen, canonical_sha256(frozen))

    def to_dict(self) -> dict[str, object]:
        return {
            "canonical_configuration": self.canonical_configuration.to_dict(),
            "configuration_digest": self.configuration_digest,
        }


def _normalized_artifacts(value: object) -> tuple[ModelArtifactEntryV1, ...]:
    if not isinstance(value, (list, tuple)):
        raise ProspectiveUnverifiedModelError("artifacts must be an explicit collection")
    artifacts = tuple(value)
    if not artifacts or any(not isinstance(item, ModelArtifactEntryV1) for item in artifacts):
        raise ProspectiveUnverifiedModelError(
            "artifacts must contain explicit ModelArtifactEntryV1 evidence"
        )
    names = tuple(item.logical_name for item in artifacts)
    paths = tuple(item.repository_relative_path for item in artifacts)
    if len(set(names)) != len(names):
        raise ProspectiveContractError("artifact logical_name values must be unique")
    if len(set(paths)) != len(paths):
        raise ProspectiveContractError("artifact repository paths must be unique")
    return tuple(
        sorted(
            artifacts,
            key=lambda item: (
                item.logical_name,
                item.repository_relative_path,
                item.sha256,
                item.size_bytes,
            ),
        )
    )


def _manifest_content(
    *,
    schema_version: int,
    model_id: str,
    model_version: str,
    sport: str,
    league: str,
    artifacts: tuple[ModelArtifactEntryV1, ...],
    training: TrainingProvenanceV1,
    feature_schema_version: str,
    feature_schema_digest: str,
    created_at_utc: datetime,
) -> dict[str, object]:
    return {
        "schema_version": schema_version,
        "model_id": model_id,
        "model_version": model_version,
        "sport": sport,
        "league": league,
        "artifacts": [artifact.to_dict() for artifact in artifacts],
        "training": training.to_dict(),
        "feature_schema_version": feature_schema_version,
        "feature_schema_digest": feature_schema_digest,
        "created_at_utc": _format_utc(created_at_utc),
    }


@dataclass(frozen=True, slots=True)
class ModelBuildManifestV1:
    schema_version: int
    model_id: str
    model_version: str
    sport: str
    league: str
    artifacts: tuple[ModelArtifactEntryV1, ...]
    training: TrainingProvenanceV1
    feature_schema_version: str
    feature_schema_digest: str
    created_at_utc: datetime
    manifest_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != MODEL_BUILD_SCHEMA_VERSION:
            raise ProspectiveContractError(
                f"schema_version must be {MODEL_BUILD_SCHEMA_VERSION}"
            )
        if not isinstance(self.training, TrainingProvenanceV1):
            raise ProspectiveUnverifiedModelError(
                "training must be explicit verified TrainingProvenanceV1"
            )
        model_id = _required_text(self.model_id, "model_id")
        model_version = _required_text(self.model_version, "model_version")
        sport = _required_text(self.sport, "sport")
        league = _required_text(self.league, "league")
        artifacts = _normalized_artifacts(self.artifacts)
        feature_version = _required_text(
            self.feature_schema_version, "feature_schema_version"
        )
        feature_digest = _lowercase_sha256(
            self.feature_schema_digest, "feature_schema_digest"
        )
        created_at = _required_utc_datetime(self.created_at_utc, "created_at_utc")
        if created_at < self.training.training_completed_at_utc:
            raise ProspectiveUnverifiedModelError(
                "created_at_utc may not precede "
                "training.training_completed_at_utc; timestamps are not "
                "inferred or repaired"
            )
        content = _manifest_content(
            schema_version=self.schema_version,
            model_id=model_id,
            model_version=model_version,
            sport=sport,
            league=league,
            artifacts=artifacts,
            training=self.training,
            feature_schema_version=feature_version,
            feature_schema_digest=feature_digest,
            created_at_utc=created_at,
        )
        expected = canonical_sha256(content)
        claimed = _lowercase_sha256(self.manifest_digest, "manifest_digest")
        if claimed != expected:
            raise ProspectiveDigestMismatchError(
                "manifest_digest does not match canonical model-build content"
            )
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "model_version", model_version)
        object.__setattr__(self, "sport", sport)
        object.__setattr__(self, "league", league)
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "feature_schema_version", feature_version)
        object.__setattr__(self, "feature_schema_digest", feature_digest)
        object.__setattr__(self, "created_at_utc", created_at)
        object.__setattr__(self, "manifest_digest", claimed)

    @classmethod
    def create(
        cls,
        *,
        model_id: str,
        model_version: str,
        sport: str,
        league: str,
        artifacts: tuple[ModelArtifactEntryV1, ...] | list[ModelArtifactEntryV1],
        training: TrainingProvenanceV1,
        feature_schema_version: str,
        feature_schema_digest: str,
        created_at_utc: datetime,
        schema_version: int = MODEL_BUILD_SCHEMA_VERSION,
    ) -> "ModelBuildManifestV1":
        normalized_artifacts = _normalized_artifacts(artifacts)
        if not isinstance(training, TrainingProvenanceV1):
            raise ProspectiveUnverifiedModelError(
                "training must be explicit verified TrainingProvenanceV1"
            )
        normalized_created = _required_utc_datetime(created_at_utc, "created_at_utc")
        content = _manifest_content(
            schema_version=schema_version,
            model_id=_required_text(model_id, "model_id"),
            model_version=_required_text(model_version, "model_version"),
            sport=_required_text(sport, "sport"),
            league=_required_text(league, "league"),
            artifacts=normalized_artifacts,
            training=training,
            feature_schema_version=_required_text(
                feature_schema_version, "feature_schema_version"
            ),
            feature_schema_digest=_lowercase_sha256(
                feature_schema_digest, "feature_schema_digest"
            ),
            created_at_utc=normalized_created,
        )
        return cls(
            schema_version=schema_version,
            model_id=model_id,
            model_version=model_version,
            sport=sport,
            league=league,
            artifacts=normalized_artifacts,
            training=training,
            feature_schema_version=feature_schema_version,
            feature_schema_digest=feature_schema_digest,
            created_at_utc=normalized_created,
            manifest_digest=canonical_sha256(content),
        )

    def content_without_digest(self) -> dict[str, object]:
        return _manifest_content(
            schema_version=self.schema_version,
            model_id=self.model_id,
            model_version=self.model_version,
            sport=self.sport,
            league=self.league,
            artifacts=self.artifacts,
            training=self.training,
            feature_schema_version=self.feature_schema_version,
            feature_schema_digest=self.feature_schema_digest,
            created_at_utc=self.created_at_utc,
        )

    def to_dict(self) -> dict[str, object]:
        payload = self.content_without_digest()
        payload["manifest_digest"] = self.manifest_digest
        return payload


@dataclass(frozen=True, slots=True)
class ProspectiveCohortSpecV1:
    schema_version: int
    sport: str
    league: str
    candidate_population: str
    source_lane: str
    model_build_manifest: ModelBuildManifestV1
    git_provenance: GitProvenanceV1
    configuration_provenance: ConfigurationProvenanceV1
    allowed_markets: tuple[str, ...]
    frozen_thresholds: FrozenJSONMapping
    required_sources: tuple[str, ...]
    sportsbook_policy: FrozenJSONMapping
    minimum_publication_lead_seconds: int
    prediction_window_start: date
    prediction_window_end: date
    prediction_timezone: str
    feature_schema_version: str

    def __post_init__(self) -> None:
        if self.schema_version != PROSPECTIVE_COHORT_SCHEMA_VERSION:
            raise ProspectiveContractError(
                f"schema_version must be {PROSPECTIVE_COHORT_SCHEMA_VERSION}"
            )
        if self.sport != "NBA":
            raise ProspectiveContractError("v1 sport must be NBA")
        if self.league != "NBA":
            raise ProspectiveContractError("v1 league must be NBA")
        if self.candidate_population != "MODEL_CANDIDATE":
            raise ProspectiveContractError(
                "v1 candidate_population must be MODEL_CANDIDATE; OfficialPick, "
                "legacy, shadow, rehearsal, and backfilled populations are forbidden"
            )
        if self.source_lane != "elite_board":
            raise ProspectiveContractError(
                "v1 source_lane must be elite_board; full-market and imported lanes are forbidden"
            )
        if not isinstance(self.model_build_manifest, ModelBuildManifestV1):
            raise ProspectiveUnverifiedModelError(
                "an explicit valid ModelBuildManifestV1 is required; legacy artifacts "
                "are not automatically verified"
            )
        if not isinstance(self.git_provenance, GitProvenanceV1):
            raise ProspectiveProvenanceError("GitProvenanceV1 is required")
        if not isinstance(self.configuration_provenance, ConfigurationProvenanceV1):
            raise ProspectiveProvenanceError("ConfigurationProvenanceV1 is required")
        if (
            self.model_build_manifest.sport != self.sport
            or self.model_build_manifest.league != self.league
        ):
            raise ProspectiveUnverifiedModelError(
                "model-build sport and league must match the cohort"
            )
        markets = _sorted_unique_texts(self.allowed_markets, "allowed_markets")
        sources = _sorted_unique_texts(self.required_sources, "required_sources")
        if not isinstance(self.frozen_thresholds, Mapping):
            raise ProspectiveContractError("frozen_thresholds must be a mapping")
        thresholds = FrozenJSONMapping(self.frozen_thresholds)
        if not thresholds:
            raise ProspectiveContractError("frozen_thresholds must not be empty")
        if not isinstance(self.sportsbook_policy, Mapping):
            raise ProspectiveContractError("sportsbook_policy must be a mapping")
        sportsbook_policy = FrozenJSONMapping(self.sportsbook_policy)
        if not sportsbook_policy:
            raise ProspectiveContractError("sportsbook_policy must not be empty")
        if (
            type(self.minimum_publication_lead_seconds) is not int
            or self.minimum_publication_lead_seconds < 0
        ):
            raise ProspectiveContractError(
                "minimum_publication_lead_seconds must be a non-negative integer"
            )
        window_start = _required_date(
            self.prediction_window_start, "prediction_window_start"
        )
        window_end = _required_date(self.prediction_window_end, "prediction_window_end")
        if window_end < window_start:
            raise ProspectiveContractError(
                "prediction_window_end may not precede prediction_window_start"
            )
        timezone_name = _required_text(self.prediction_timezone, "prediction_timezone")
        feature_version = _required_text(
            self.feature_schema_version, "feature_schema_version"
        )
        if feature_version != self.model_build_manifest.feature_schema_version:
            raise ProspectiveUnverifiedModelError(
                "cohort feature_schema_version must match "
                "model_build_manifest.feature_schema_version"
            )
        object.__setattr__(self, "allowed_markets", markets)
        object.__setattr__(self, "required_sources", sources)
        object.__setattr__(self, "frozen_thresholds", thresholds)
        object.__setattr__(self, "sportsbook_policy", sportsbook_policy)
        object.__setattr__(self, "prediction_window_start", window_start)
        object.__setattr__(self, "prediction_window_end", window_end)
        object.__setattr__(self, "prediction_timezone", timezone_name)
        object.__setattr__(self, "feature_schema_version", feature_version)

    def identity_payload(self) -> dict[str, object]:
        """Return every frozen material input and no mutable cohort status."""

        return {
            "schema_version": self.schema_version,
            "sport": self.sport,
            "league": self.league,
            "candidate_population": self.candidate_population,
            "source_lane": self.source_lane,
            "model_build_manifest": self.model_build_manifest.to_dict(),
            "git_provenance": self.git_provenance.to_dict(),
            "configuration_provenance": self.configuration_provenance.to_dict(),
            "allowed_markets": list(self.allowed_markets),
            "frozen_thresholds": self.frozen_thresholds.to_dict(),
            "required_sources": list(self.required_sources),
            "sportsbook_policy": self.sportsbook_policy.to_dict(),
            "minimum_publication_lead_seconds": self.minimum_publication_lead_seconds,
            "prediction_window_start": self.prediction_window_start.isoformat(),
            "prediction_window_end": self.prediction_window_end.isoformat(),
            "prediction_timezone": self.prediction_timezone,
            "feature_schema_version": self.feature_schema_version,
        }


@dataclass(frozen=True, slots=True)
class ProspectiveCohortIdentityV1:
    cohort_id: str
    cohort_digest: str
    canonical_identity_payload: FrozenJSONMapping

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_identity_payload, Mapping):
            raise ProspectiveContractError(
                "canonical_identity_payload must be a mapping"
            )
        payload = FrozenJSONMapping(self.canonical_identity_payload)
        digest = _lowercase_sha256(self.cohort_digest, "cohort_digest")
        expected_digest = canonical_sha256(payload)
        if digest != expected_digest:
            raise ProspectiveDigestMismatchError(
                "cohort_digest does not match canonical_identity_payload"
            )
        expected_id = f"prospective-nba-v1-{digest[:COHORT_ID_DIGEST_LENGTH]}"
        if self.cohort_id != expected_id:
            raise ProspectiveDigestMismatchError(
                "cohort_id does not match the deterministic cohort digest"
            )
        object.__setattr__(self, "canonical_identity_payload", payload)
        object.__setattr__(self, "cohort_digest", digest)

    @classmethod
    def from_spec(cls, spec: ProspectiveCohortSpecV1) -> "ProspectiveCohortIdentityV1":
        if not isinstance(spec, ProspectiveCohortSpecV1):
            raise ProspectiveContractError("ProspectiveCohortSpecV1 is required")
        if spec.git_provenance.dirty:
            raise ProspectiveDirtyTreeError(
                "dirty Git state blocks prospective cohort activation"
            )
        payload = FrozenJSONMapping(spec.identity_payload())
        digest = canonical_sha256(payload)
        return cls(
            cohort_id=f"prospective-nba-v1-{digest[:COHORT_ID_DIGEST_LENGTH]}",
            cohort_digest=digest,
            canonical_identity_payload=payload,
        )


def derive_prospective_cohort_identity(
    spec: ProspectiveCohortSpecV1,
) -> ProspectiveCohortIdentityV1:
    """Derive a deterministic identity from validated frozen inputs."""

    return ProspectiveCohortIdentityV1.from_spec(spec)


__all__ = [
    "COHORT_ID_DIGEST_LENGTH",
    "MODEL_BUILD_SCHEMA_VERSION",
    "PROSPECTIVE_COHORT_SCHEMA_VERSION",
    "ConfigurationProvenanceV1",
    "FrozenJSONMapping",
    "GitProvenanceV1",
    "ModelArtifactEntryV1",
    "ModelBuildManifestV1",
    "ProspectiveCohortIdentityV1",
    "ProspectiveCohortSpecV1",
    "ProspectiveContractError",
    "ProspectiveDigestMismatchError",
    "ProspectiveDirtyTreeError",
    "ProspectiveMissingArtifactError",
    "ProspectiveProvenanceError",
    "ProspectiveSecretConfigurationError",
    "ProspectiveUnverifiedModelError",
    "TrainingProvenanceV1",
    "canonical_json_bytes",
    "canonical_sha256",
    "derive_prospective_cohort_identity",
]
